"""The append-only record of every operator gesture, and the derived name it reads.

**Migration `0014`, and the rule it inherits from `0008`.** `situation_alarm` stays the *current*
membership and this release mutates it; that is safe precisely because every gesture captures the
membership at the instant it happened. *"A moment not captured is not captured late — it is
captured never."* The snapshot lives in `situation_event_member` in the same shape
`feedback_member` has used since `0008`: ordered, positional, server-authoritative.

**Nothing here decides anything.** Which gestures assert something about a grouping, what
confidence does to a training row, and what a bag's provenance is worth are all the
pre-registration's questions and are answered in `engine/dataset/gestures.py` and in
`engine/model/training.py`. This module is SQL.

**No method here takes ``store.lock``.** That is this package's contract for callers
(`tests/test_store_concurrency.py` walks the MRO, so this module is covered by construction).
"""

from __future__ import annotations

from typing import Any

from netcorenoc.crosscutting.shaping import derive_situation_name
from netcorenoc.store.base import StoreBase

__all__ = ["SituationEventMixin"]

#: The gestures that assert something about a **grouping**, and therefore the only ones that may
#: produce a link-training row. `manual_clear` and `self_clear` are deliberately absent:
#: `PREREGISTRATION-0.16.0.md` §1 extends `incumbent_linked`'s prohibition to *any signal that is
#: not an assertion about a grouping*, and a zombie clear is a fact about an alarm's lifecycle.
#:
#: Here rather than in the caller so the prohibition is one literal a guard can read, rather than a
#: condition written out at each of four call sites.
ASSERTING_KINDS: frozenset[str] = frozenset({"verdict", "move", "merge", "operator_split"})


class SituationEventMixin(StoreBase):
    # -- the derived name -----------------------------------------------------------------------

    async def refresh_derived_name(self, situation_id: int) -> str | None:
        """Recompute `situation.derived_name` from the current membership. Returns the new value.

        **Called by the statement group that changed the membership, and by nothing else**
        (DECISIONS #257). That is what makes "never stale" structural rather than promised: there
        is no path that changes `situation_alarm` without passing through a caller of this, and
        `tests/test_store.py::test_the_derived_name_agrees_with_the_membership_after_every_mutation`
        drives all six of them and compares.

        One aggregate query, and it reads **device addresses** rather than labels — an operator's
        device label is free text, and folding it into a name the server computed would make an
        operator's own words indistinguishable from a projection. `DISTINCT` because the name's
        four forms turn on how many *devices* are involved, not how many rows.

        Returns `None` — and writes nothing — on a schema without the column, which is what lets
        this run inside `add_alarm_to_situation` against the schema-13 database
        `tests/test_upgrade.py` builds.
        """
        if not self._has_lifecycle:
            return None
        cur = await self.conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT d.ip) FROM situation_alarm sa "
            "JOIN alarm a ON a.id = sa.alarm_id JOIN device d ON d.id = a.device_id "
            "WHERE sa.situation_id = ?",
            (situation_id,),
        )
        counts = await cur.fetchone()
        assert counts is not None
        members = int(counts[0])
        # Only ever three addresses are needed: the four forms turn on "one device", "exactly two
        # devices", and "more than two", and the widest form names the lowest address and counts
        # the rest. `LIMIT 3` is what keeps this bounded on a 500-device storm rather than reading
        # every member's address to throw all but one away.
        cur = await self.conn.execute(
            "SELECT DISTINCT d.ip FROM situation_alarm sa "
            "JOIN alarm a ON a.id = sa.alarm_id JOIN device d ON d.id = a.device_id "
            "WHERE sa.situation_id = ? ORDER BY d.ip LIMIT 3",
            (situation_id,),
        )
        addresses = [str(row[0]) for row in await cur.fetchall()]
        name = derive_situation_name(addresses, members, device_count=int(counts[1]))
        await self.conn.execute(
            "UPDATE situation SET derived_name = ? WHERE id = ?", (name, situation_id)
        )
        return name

    async def set_operator_name(self, situation_id: int, name: str | None, ts: float) -> bool:
        """An operator's own name for a situation. **The only writer of `operator_name`.**

        `None` clears it and the derived name shows through again — a rename is a label and an
        operator may withdraw one. Returns False when the situation does not exist, which the
        caller turns into the same 404 an out-of-scope one takes.

        No model reaches this method, in this release or through any path it adds
        (`tests/test_store.py::test_no_server_derivation_ever_reaches_operator_name`). A model
        writing *"fibre cut"* above a grouping the operator is about to judge contaminates that
        judgement, which is the `incumbent_linked` mistake in a new register.
        """
        cur = await self.conn.execute(
            "UPDATE situation SET operator_name = ?, updated_at = ? WHERE id = ? RETURNING id",
            (name, ts, situation_id),
        )
        return await cur.fetchone() is not None

    # -- the snapshot, and how the bag was held together -----------------------------------------

    async def situation_member_ids(self, situation_id: int) -> list[int]:
        """The situation's members as an **ordered** list of alarm ids.

        `ORDER BY alarm_id` — stable, unique, and a property of the data rather than of the query
        planner, which is the same reason `labelled_pairs` orders by `(f.id, p.id)`. The bag is
        ordered because the position is part of the record and cannot be recomputed, so the order
        has to be one a later reader can reproduce.
        """
        cur = await self.conn.execute(
            "SELECT alarm_id FROM situation_alarm WHERE situation_id=? ORDER BY alarm_id",
            (situation_id,),
        )
        return [int(row[0]) for row in await cur.fetchall()]

    async def bag_links(self, situation_id: int) -> tuple[list[tuple[int, int]], list[float]]:
        """The links **inside** the current membership, as edges and scores.

        Filtered to pairs whose two endpoints are both current members, because after an operator
        move they need not be: a `link` records what the correlator computed and this release does
        not delete one, so a situation can hold a link to an alarm that has left. Its arithmetic is
        still true and still worth keeping; it is simply not a statement about *this* bag any more.

        The same filter the detail view applies, so the provenance recorded beside a gesture and the
        soundness summary the operator was reading are computed over one set of links rather than
        two that can disagree.
        """
        cur = await self.conn.execute(
            "SELECT l.alarm_a, l.alarm_b, l.score FROM link l "
            "WHERE l.situation_id=? "
            "  AND EXISTS (SELECT 1 FROM situation_alarm m "
            "               WHERE m.situation_id=l.situation_id AND m.alarm_id=l.alarm_a) "
            "  AND EXISTS (SELECT 1 FROM situation_alarm m "
            "               WHERE m.situation_id=l.situation_id AND m.alarm_id=l.alarm_b) "
            "ORDER BY l.id",
            (situation_id,),
        )
        rows = await cur.fetchall()
        return [(int(r[0]), int(r[1])) for r in rows], [float(r[2]) for r in rows]

    async def situation_threshold(self, situation_id: int) -> float | None:
        """The threshold **this situation's** links had to clear, from the configuration that
        formed it rather than from the active one (F84's rule, DECISIONS #247).

        `None` when the situation predates scorer provenance or its configuration row is gone —
        never a default, because a margin computed against a threshold the server guessed would be
        worse than one it says it does not have.
        """
        cur = await self.conn.execute(
            "SELECT c.threshold FROM situation s JOIN scorer_config c ON c.id = s.scorer_config_id "
            "WHERE s.id = ?",
            (situation_id,),
        )
        row = await cur.fetchone()
        return float(row[0]) if row is not None else None

    # -- the event log --------------------------------------------------------------------------

    async def add_situation_event(self, **fields: Any) -> int:
        """One gesture, appended. Returns the event id.

        `**fields` for the reason `add_observation` and `open_capture_run` take them: the caller in
        `engine/dataset/gestures.py` is the one place that knows which columns a *kind* of gesture
        carries, and enumerating nine optional parameters here would put that knowledge in two
        places. The `CHECK` constraints in `0014` are what refuse a malformed row.
        """
        columns = ", ".join(fields)
        marks = ", ".join("?" * len(fields))
        cur = await self.conn.execute(
            f"INSERT INTO situation_event ({columns}) VALUES ({marks}) RETURNING id",  # nosec B608
            tuple(fields.values()),
        )
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    async def add_event_members(self, event_id: int, source: str, alarm_ids: list[int]) -> None:
        """The ordered snapshot, server-authoritative. `source` is `server` or `peer`.

        An **empty** ``alarm_ids`` writes nothing and that is correct rather than a case to guard
        against: a gesture on a situation whose members have all been moved away genuinely has an
        empty bag, and recording zero rows is how that population becomes countable — the reasoning
        `add_feedback_members` gives for the label bag, one table over.
        """
        if not alarm_ids:
            return
        await self.conn.executemany(
            "INSERT INTO situation_event_member (event_id, source, position, alarm_id) "
            "VALUES (?, ?, ?, ?)",
            [(event_id, source, index, alarm) for index, alarm in enumerate(alarm_ids)],
        )

    async def event_members(self, event_id: int, source: str) -> list[int]:
        """One snapshot, in its recorded order. The evidence half of an event."""
        cur = await self.conn.execute(
            "SELECT alarm_id FROM situation_event_member WHERE event_id=? AND source=? "
            "ORDER BY position",
            (event_id, source),
        )
        return [int(row[0]) for row in await cur.fetchall()]

    async def situation_events(self, situation_id: int) -> list[dict[str, Any]]:
        """One situation's history, oldest first. **The four columns the console renders.**

        Both directions: a `move` is recorded on the situation the alarm **left**, and the
        destination's history would otherwise say nothing about an alarm that arrived by an
        operator's hand rather than by the correlator's.

        `SELECT *` deliberately not: this feeds `GET /api/situations/{sid}`, which a **scoped**
        editor may read, and the row carries `alarm_id`, `peer_situation_id` and two membership
        digests. Serving those would let a scoped reader learn about members and situations the
        redaction elsewhere is careful never to name — the whole of `project_situation_detail`'s
        discipline, undone by a history panel. What an operator needs to read the card is *what
        happened, when, who did it, and how sure they were*, and that is what this returns.

        The full row is `situation_event` itself and is read by the census and the dataset reports,
        which are `admin`-only and emit aggregates.
        """
        cur = await self.conn.execute(
            "SELECT kind, at, actor, confidence FROM situation_event "
            "WHERE situation_id=? OR peer_situation_id=? ORDER BY at, id",
            (situation_id, situation_id),
        )
        return [dict(row) for row in await cur.fetchall()]

    async def gesture_positive_pairs(self) -> list[dict[str, Any]]:
        """The **positive** pair assertions the label surface has no shape for. One query.

        Two gestures reach here, and the plan's §2 says exactly what each asserts:

        * **`move`** — the moved alarm against every member of the situation it **joined**. The
          negative half (against the members it left) is a `split` on the source situation with the
          alarm marked, written through the ordinary label path, so it arrives via
          `labelled_pairs`. This is the other half, and a build that recorded only the negative
          would have thrown away the release's own product.
        * **`merge`** — every **cross** pair between the two bags. A `confirm` on the merged
          situation would have asserted every pair *inside each original bag* positive too, which
          the operator did not say, so a merge writes no label at all and its assertion lives here.

        `situation_event_member` is joined twice, `server` against `peer`, and the pair is matched
        in **both orders** because `dataset_pair` records `(alarm_a, alarm_b)` as the window alarm
        and the newly activated one — an ordering that is a fact about capture, not about the
        assertion. For a `move` the server side is narrowed to the moved alarm; for a `merge` it is
        the whole bag, which is what makes one query serve both.

        Filters, and each is the plan's rather than a convenience:

        * `produces_training_rows = 1` — the stored form of §1's prohibition, so a `manual_clear`
          or a `self_clear` cannot reach a scorer even if a future kind is added carelessly;
        * `lifecycle = 'dataset'` — a pair still in the sink was never promoted, and asserting
          about features nothing promoted would be asserting about rows the corpus does not hold.

        `ORDER BY e.id, p.id` — both stable and unique, so the training row order is a property of
        the data rather than of the query planner, exactly as `labelled_pairs` orders.
        """
        cur = await self.conn.execute(
            "SELECT e.id AS event_id, e.kind, e.confidence, e.situation_id, "
            "       e.at AS label_at, e.acquisition_channel, "
            "       p.id AS pair_id, p.delta_t_s, p.class_affinity, p.entity_affinity, "
            "       p.incumbent_linked, p.evaluated_at "
            "FROM situation_event e "
            "JOIN situation_event_member sm ON sm.event_id = e.id AND sm.source = 'server' "
            "JOIN situation_event_member pm ON pm.event_id = e.id AND pm.source = 'peer' "
            "JOIN dataset_pair p ON p.lifecycle = 'dataset' AND ("
            "     (p.alarm_a = sm.alarm_id AND p.alarm_b = pm.alarm_id) "
            "  OR (p.alarm_a = pm.alarm_id AND p.alarm_b = sm.alarm_id)) "
            "WHERE e.produces_training_rows = 1 "
            "  AND e.kind IN ('move', 'merge') "
            "  AND (e.kind = 'merge' OR sm.alarm_id = e.alarm_id) "
            "ORDER BY e.id, p.id"
        )
        return [dict(row) for row in await cur.fetchall()]

    async def event_counts_by_channel(self) -> dict[str, int]:
        """Gestures per `acquisition_channel`. **Reported separately and never averaged.**

        DECISIONS #126, and `PREREGISTRATION-0.16.0.md` §2 restates it for the three channels this
        release adds: a merge selects for a different population from the one an operator browses,
        and blending them destroys the bias characterisation retroactively — including for rows
        already written.
        """
        cur = await self.conn.execute(
            "SELECT COALESCE(acquisition_channel, '(none)'), COUNT(*) FROM situation_event "
            "GROUP BY 1 ORDER BY 1"
        )
        return {str(row[0]): int(row[1]) for row in await cur.fetchall()}
