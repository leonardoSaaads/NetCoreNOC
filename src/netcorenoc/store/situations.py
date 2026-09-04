"""Situations: open, join, merge, close, and the link rows that justify a grouping.

**v0.16.0 — the state machine (migration `0014`, DECISIONS #253, #254, #259).** `status` is
`new | open | resolved`, and a second column records *why* a situation left:

    status      new | open | resolved
    resolution  operator | self_cleared | idle | merged | manual_clear | unattributed

`status` stays small because it is what the three console tabs render. `resolution` carries the
fact, and the fact is what an ISP manager auditing two months later actually needs: *"the network
fixed it"*, *"an operator judged it"* and *"nobody looked at it for an hour"* were one value before
this release and no column distinguished them.

**Every method here that names a v0.16.0 column consults `self._has_lifecycle` first**, so the
identical store code runs against a schema that predates its own columns — the same discipline
`create_situation` has followed for `scorer_config_id` since `0005` and `merge_situations` for
`merged_into` since `0008`, and the property `tests/test_upgrade.py` relies on to prove that a
migration changes behaviour and the code does not.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from netcorenoc.store.situation_events import SituationEventMixin

#: The two live states. A situation that has not left is `new` (nobody has looked at it) or `open`
#: (an operator is working it), and **every reader that used to ask for `open` asks for both** —
#: the idle sweep, the engine's state reload, the scope resolution and the `open_situations` stat.
#: Counting `open` alone would have reported zero on a working appliance the moment this release
#: shipped, because the correlator creates `new` (DECISIONS #254).
#:
#: Written as a SQL fragment rather than assembled per call site: one literal, no placeholders to
#: get wrong, and `tests/test_store.py::test_every_live_situation_query_uses_the_one_fragment`
#: reads this module to assert nothing spells it out a second time.
LIVE = "status IN ('new','open')"


class SituationMixin(SituationEventMixin):
    """Inherits the event mixin for **one** method: `refresh_derived_name`.

    The third sibling-inheritance edge in this package, on DECISIONS #88's terms. `add_alarm_to_
    situation` and `merge_situations` change membership, and the derived name is a projection of
    membership that is refreshed *inside* those writes rather than by a caller who might forget —
    which is what makes staleness structurally impossible (#257). A stub here instead would resolve
    and do nothing, which is the silent no-op that decision rejects.
    """

    async def create_situation(self, ts: float, scorer_config_id: int | None = None) -> int:
        """Open a situation, recording which scorer configuration formed it (v0.6.0 provenance).

        The engine passes the active `config_id`; it is written here, on the store side under the
        batch lock the engine already holds — never on the datagram path.

        ``None`` means "no configuration is in effect" — the fail-safe state where the engine is
        running on the coded defaults. The column is then left out of the statement entirely
        rather than written as NULL, so this call still succeeds against a schema that predates
        `0005_scorer_config.sql`. That is not hypothetical tidiness: it is what lets the same
        engine code run before and after the migration, which is how the upgrade test proves the
        migration changes no grouping.

        **v0.16.0: the correlator creates `new`** (DECISIONS #254). `new` means *"nobody has
        looked at this"* and `open` means *"an operator is working it"*; the first operator gesture
        that names a situation promotes it. There is no timer and no member count, because every
        such criterion is a threshold nothing in this repository has measured, and a threshold
        chosen to look reasonable is the placeholder rule (#219) wearing a number.

        `status` is written **explicitly** rather than by moving the column default: changing a
        default is a table rebuild in SQLite, and rebuilding `situation` would move a table three
        others reference by foreign key in order to state a value one INSERT already states. The
        `_has_lifecycle` branch is what keeps this call working against a schema-13 database.
        """
        status = "new" if self._has_lifecycle else "open"
        if scorer_config_id is None:
            cur = await self.conn.execute(
                "INSERT INTO situation (status, created_at, updated_at) VALUES (?, ?, ?) "
                "RETURNING id",
                (status, ts, ts),
            )
        else:
            cur = await self.conn.execute(
                "INSERT INTO situation (status, created_at, updated_at, scorer_config_id) "
                "VALUES (?, ?, ?, ?) RETURNING id",
                (status, ts, ts, scorer_config_id),
            )
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    async def promote_situation(self, situation_id: int, ts: float) -> None:
        """`new` -> `open`: an operator has touched this situation (v0.16.0, DECISIONS #254).

        Idempotent and one-directional. `WHERE status='new'` means a second gesture is a no-op and
        a gesture on a **resolved** situation does not resurrect it — reopening is a decision
        nobody has made, and doing it as a side effect of a rename would be making it silently.
        """
        if not self._has_lifecycle:
            return
        await self.conn.execute(
            "UPDATE situation SET status='open', updated_at=? WHERE id=? AND status='new'",
            (ts, situation_id),
        )

    async def add_alarm_to_situation(self, situation_id: int, alarm_id: int) -> None:
        """Add one member, and **refresh the derived name in the same statement group**.

        v0.16.0, DECISIONS #257. The name is a projection of membership, so the only way it can be
        stale is if a path changes membership without recomputing it — and the way to make that
        impossible rather than merely forbidden is to put the recomputation *inside* the write. The
        engine's `_assign_situation` calls this and nothing else when an alarm joins, so the ingest
        path is covered without a line of it changing.

        The cost is two bounded reads and one UPDATE per **membership change** — not per trap, and
        not per activation: an alarm already in its situation takes the `INSERT OR IGNORE` and never
        reaches this. `RETURNING` is what makes that distinction, rather than refreshing
        unconditionally and paying for a name nothing moved.
        """
        cur = await self.conn.execute(
            "INSERT OR IGNORE INTO situation_alarm (situation_id, alarm_id) VALUES (?, ?) "
            "RETURNING alarm_id",
            (situation_id, alarm_id),
        )
        if await cur.fetchone() is not None:
            await self.refresh_derived_name(situation_id)

    async def merge_situations(self, dst: int, src: int, ts: float) -> None:
        await self.conn.execute(
            "INSERT OR IGNORE INTO situation_alarm (situation_id, alarm_id) "
            "SELECT ?, alarm_id FROM situation_alarm WHERE situation_id=?",
            (dst, src),
        )
        await self.conn.execute("DELETE FROM situation_alarm WHERE situation_id=?", (src,))
        await self.conn.execute("UPDATE link SET situation_id=? WHERE situation_id=?", (dst, src))
        # Both sides: the destination gained members and the source lost all of them, so both
        # names moved (DECISIONS #257). The source's becomes "(no members)", which is the honest
        # heading for a situation that has been absorbed.
        await self.refresh_derived_name(dst)
        await self.refresh_derived_name(src)
        # v0.8.0: `merged_into` records **where the members went**. Until this release the merge
        # marked the source `merged` and said nothing about the destination, so the merge chain was
        # unrecoverable: a reader holding a labelled situation id could not follow it forward to the
        # situation that absorbed it. Phase 0 proved the consequence — a label's referent is
        # destroyed entirely, and no query recovers the bag.
        #
        # One extra column on an UPDATE that already ran: no new statement, no new index, nothing
        # added to the batch path's cost.
        #
        # The fallback is the same discipline `create_situation` follows for `scorer_config_id`
        # (0005): **this call must still succeed against a schema that predates its own column.**
        # That is not defensive tidiness — it is what lets the identical engine code run before and
        # after the migration, which is how `tests/test_upgrade.py` proves that `0008` changes
        # behaviour and the code does not. Merges are rare (four across the whole eval corpus), so
        # the cost of learning this once is a single caught error per process on an old schema.
        #
        # v0.16.0: the *status* half of this statement moves with the state machine. `merged` was
        # a `status` value and is now a `resolution` — the one historical value that is knowable
        # exactly, because this statement is what wrote it (DECISIONS #253). The schema probe
        # decides which form to emit, so the pre-0014 branch stays byte-identical.
        left = (
            "status='resolved', resolution='merged'" if self._has_lifecycle else "status='merged'"
        )
        if self._has_merged_into is not False:
            try:
                await self.conn.execute(
                    f"UPDATE situation SET {left}, closed_at=?, updated_at=?, "  # nosec B608
                    "merged_into=? WHERE id=?",
                    (ts, ts, dst, src),
                )
                self._has_merged_into = True
                await self.touch_situation(dst, ts)
                return
            except sqlite3.OperationalError:
                self._has_merged_into = False  # pre-0008 schema; record it and use the old form
        await self.conn.execute(
            f"UPDATE situation SET {left}, closed_at=?, updated_at=? WHERE id=?",  # nosec B608
            (ts, ts, src),
        )
        await self.touch_situation(dst, ts)

    async def touch_situation(self, situation_id: int, ts: float) -> None:
        await self.conn.execute("UPDATE situation SET updated_at=? WHERE id=?", (ts, situation_id))

    async def close_situation(self, situation_id: int, ts: float) -> None:
        """The **appliance's own** close, resolving why it happened (v0.16.0, DECISIONS #259).

        Two callers, one statement, and they mean opposite things to a model: the engine closes a
        situation when every member alarm has cleared — *the network fixed itself* — and the idle
        sweep closes one that nobody touched for `IDLE_CLOSE_S`. Before this release both wrote
        `closed` and nothing distinguished them.

        **The reason is derived here rather than passed in**, and that is what lets
        `engine/operate/engine.py` stay byte-identical through this release: `_close_situation`
        calls this with `(sid, ts)` on both paths and the information that tells them apart —
        whether any member is still active — is in the database at the instant of the call, which
        is the only instant at which it is still true. Passing it would mean the ingest path had to
        say what it already demonstrates.

        **A situation with no members resolves as `idle`, never `self_cleared`.** `all_cleared`
        answers True for an empty bag, and *"nothing was active"* is not *"the alarms cleared"* —
        that is the invariant Appendix B warns about, an expression that cannot come out false for
        one of its inputs.
        """
        if not self._has_lifecycle:
            await self.conn.execute(
                "UPDATE situation SET status='closed', closed_at=?, updated_at=? "
                "WHERE id=? AND status='open'",
                (ts, ts, situation_id),
            )
            return
        await self.conn.execute(
            "UPDATE situation SET status='resolved', resolution=?, closed_at=?, updated_at=? "
            f"WHERE id=? AND {LIVE}",  # nosec B608 - `LIVE` is a module literal
            (await self._close_reason(situation_id), ts, ts, situation_id),
        )

    async def _close_reason(self, situation_id: int) -> str:
        """`self_cleared` when the bag had members and none is still active, else `idle`."""
        cur = await self.conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(a.status='active'), 0) FROM situation_alarm sa "
            "JOIN alarm a ON a.id=sa.alarm_id WHERE sa.situation_id=?",
            (situation_id,),
        )
        row = await cur.fetchone()
        assert row is not None
        members, active = int(row[0]), int(row[1])
        return "self_cleared" if members and not active else "idle"

    async def set_root(self, situation_id: int, alarm_id: int) -> None:
        await self.conn.execute(
            "UPDATE situation SET root_alarm_id=? WHERE id=?", (alarm_id, situation_id)
        )

    async def manual_close_situation(self, situation_id: int, ts: float) -> bool:
        """Operator ack: resolve a live situation. Returns False if it was not live.

        `resolution='operator'` — the value that used to be indistinguishable from the idle
        sweep's. A `new` situation closes exactly as an `open` one does: an operator who reads a
        card and closes it has looked at it, whether or not they touched it first.
        """
        if not self._has_lifecycle:
            cur = await self.conn.execute(
                "UPDATE situation SET status='closed', closed_at=?, updated_at=? "
                "WHERE id=? AND status='open' RETURNING id",
                (ts, ts, situation_id),
            )
            return await cur.fetchone() is not None
        cur = await self.conn.execute(
            "UPDATE situation SET status='resolved', resolution='operator', closed_at=?, "
            f"updated_at=? WHERE id=? AND {LIVE} RETURNING id",  # nosec B608 - module literal
            (ts, ts, situation_id),
        )
        return await cur.fetchone() is not None

    async def add_link(
        self,
        situation_id: int,
        alarm_a: int,
        alarm_b: int,
        score: float,
        term_t: float,
        term_a: float,
        term_e: float,
        ts: float,
    ) -> None:
        await self.conn.execute(
            "INSERT INTO link (situation_id, alarm_a, alarm_b, score, term_t, term_a, term_e, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (situation_id, alarm_a, alarm_b, score, term_t, term_a, term_e, ts),
        )

    async def idle_open_situations(self, cutoff: float) -> list[int]:
        """Live situations nobody has touched since `cutoff`. **`new` counts as live.**

        A situation the correlator opened and nobody triaged is exactly what the sweep is for, so
        widening this to both live states preserves the sweep's meaning rather than changing it
        (DECISIONS #254).
        """
        cur = await self.conn.execute(
            f"SELECT id FROM situation WHERE {LIVE} AND updated_at < ?",  # nosec B608
            (cutoff,),
        )
        return [int(r[0]) for r in await cur.fetchall()]

    async def all_cleared(self, situation_id: int) -> bool:
        cur = await self.conn.execute(
            "SELECT COUNT(*) FROM situation_alarm sa JOIN alarm a ON a.id=sa.alarm_id "
            "WHERE sa.situation_id=? AND a.status='active'",
            (situation_id,),
        )
        row = await cur.fetchone()
        assert row is not None
        return int(row[0]) == 0

    async def open_situation_members(self) -> list[dict[str, Any]]:
        """Members of all **live** situations, for rebuilding engine state at startup.

        `new` as well as `open`: a restart must not forget the membership of every situation an
        operator has not triaged yet, which is most of them.
        """
        cur = await self.conn.execute(
            "SELECT sa.situation_id, a.id AS alarm_id, a.class_id, a.device_id, a.first_seen "
            "FROM situation s JOIN situation_alarm sa ON sa.situation_id=s.id "
            f"JOIN alarm a ON a.id=sa.alarm_id WHERE s.{LIVE}"  # nosec B608 - module literal
        )
        return [dict(r) for r in await cur.fetchall()]

    async def situation_members(self, situation_id: int) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT a.id, a.device_id, a.class_id, a.first_seen FROM situation_alarm sa "
            "JOIN alarm a ON a.id=sa.alarm_id WHERE sa.situation_id=?",
            (situation_id,),
        )
        return [dict(r) for r in await cur.fetchall()]
