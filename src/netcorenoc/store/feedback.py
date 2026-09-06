"""The label row and everything hanging off it, and the device/class label table.

Both original halves carry a v0.7.1 finding: F36 (feedback is at most once per
``(situation, verdict, bag)``, so its influence on learned state is bounded) and F37 (a label may
only name a target that exists, and a missing target answers the same 404 an out-of-scope one does,
so the fix cannot re-introduce the existence oracle F34 closes).

**v0.16.1 widened F36's key by one column and did not weaken it.** ``bag_key`` — a digest over the
member *set* at the instant of the label — is what makes the second correction of one situation a
second assertion rather than a lost one (F89), and what keeps N identical posts a single one.
``PREREGISTRATION-0.16.1.md`` §2 is the argument and migration ``0015`` is the schema; this module
holds the only expression that computes the key.

**v0.9.1 (DECISIONS #128): the label row's children moved here from `dataset.py`** — the bag, the
annotations, the situation's open time, and this release's exclusion set. They were only ever over
there because v0.8.0 added them in the same release as the capture sink, and the two are **different
paths, not two halves of one**: capture runs on the ingest path, once per activation, under the
batch lock, while everything here runs once per *operator verdict* — thousands of times rarer, on
the HTTP write path. That is the same seam `labels.py` was split from `capture.py` on, one layer up.

**No method here takes ``store.lock``.** That is this package's contract for callers
(`tests/test_store_concurrency.py` walks the MRO, so this module is covered by construction).
"""

from __future__ import annotations

import hashlib
from typing import Any

from netcorenoc.store.base import StoreBase
from netcorenoc.store.types import FeedbackResult

#: The `bag_key` of a row written before `0015` — *"this row's bag identity was never computed"*.
#: A real value with a stated meaning rather than NULL, so the unique index needs no `COALESCE` and
#: `ON CONFLICT` can name three plain columns. It is `excluded_reconciled_source`'s three-state
#: precedent in one fewer state: no digest can ever equal it, so a legacy row never claims to be
#: the same bag as anything, and among legacy rows themselves the old two-column bound is preserved
#: exactly — at most one per `(situation_id, verdict)`, which is what the old index guaranteed.
UNKEYED_BAG = ""


def bag_key(alarm_ids: list[int]) -> str:
    """A bag's **identity**: a digest over its member ids as a SET.

    `PREREGISTRATION-0.16.1.md` §2. Order is part of the *record* — `member_digest` keeps it, over
    the ordered bag, because the order is what the operator saw. Order is not part of the
    *identity*: the same alarms in a different order are the same grouping, and an operator who
    asserts about them twice has asserted once. **Sorted and deduplicated**, so a correlator that
    re-orders a bag cannot manufacture a second assertion out of one.

    Deliberately **not** `labels.member_digest`. Two digests over one bag that disagreed by
    accident would be a silent inconsistency; two that disagree *by construction*, over different
    quantities, with different names, are two facts. The store may not import from the engine
    (`tests/test_layers.py`), and that constraint agrees with the design here rather than fighting
    it: the identity is the store's, the observation is the engine's.
    """
    return hashlib.sha256(",".join(str(a) for a in sorted(set(alarm_ids))).encode()).hexdigest()


def _check_reconciled(fields: dict[str, Any]) -> None:
    """`0 <= excluded_reconciled <= member_count`, and the blind count within it (v0.9.2).

    A **precondition**, not a validation of user input: nothing a client sends reaches here
    unreconciled, so a violation is a bug in `labels._assertion` or in a caller that invented a
    number. Stated as `ValueError` rather than `assert` because the process is the same either way
    and an assertion would vanish under `-O`.

    Only checks what the call actually carries. `record_label` writes all of these in **one**
    `UPDATE`, so `member_count` is present whenever `excluded_reconciled` is — and a call that
    carried a reconciled count without a bag size would be exactly the malformed write this refuses.
    """
    reconciled = fields.get("excluded_reconciled")
    if reconciled is not None:
        n = fields.get("member_count")
        if n is None or not 0 <= reconciled <= n:
            raise ValueError(
                f"excluded_reconciled={reconciled!r} is not within [0, member_count={n!r}]"
            )
    blind = fields.get("excluded_reconciled_out_of_scope")
    if blind is not None and (reconciled is None or not 0 <= blind <= reconciled):
        raise ValueError(
            f"excluded_reconciled_out_of_scope={blind!r} is not within "
            f"[0, excluded_reconciled={reconciled!r}]"
        )


class FeedbackMixin(StoreBase):
    async def add_feedback(
        self,
        situation_id: int,
        verdict: str,
        ts: float,
        *,
        principal_ref: str | None = None,
        role: str | None = None,
    ) -> FeedbackResult:
        """Record one verdict, **at most once per (situation, verdict, bag)** (F36, then F89).

        v0.7.0 inserted unconditionally, so N identical posts wrote N rows and drove N learning
        effects — each of which advanced the global forgetting epoch. The `UNIQUE` index added by
        migration `0007` makes the repeat a no-op at the storage layer, and the returned
        :class:`FeedbackResult` is what lets `Engine.apply_feedback` apply the learning effect
        **only** on a genuine insert.

        **v0.16.1 (F89): the key gains the bag** (`PREREGISTRATION-0.16.1.md` §2, migration
        `0015`). `UNIQUE (situation_id, verdict)` meant the *second* `move` out of one situation
        recorded its event and no second label — and the second move asserts about a **different
        bag**, because the first one changed it. So the key is what a bag actually is: the
        situation, the verdict, and the member **set** at the instant of the label.

        **F36's measured defect stays fixed exactly where F36 measured it.** N identical posts have
        one `bag_key` and still insert once. What inserts a second row is a post about a bag that
        has changed, which is a different assertion rather than a repeat of one. The bound this
        trades away is named in the amendment and not discovered here: the cap on one situation's
        influence moves from *two applications* to *one per verdict per distinct membership*. Still
        bounded, still monotone in operator acts, no longer a constant.

        The key is derived from `situation_alarm` — **the persisted membership, which is this
        layer's own** — rather than from the caller's bag, so `engine.apply_feedback` is
        byte-identical and its call site did not have to learn about identity. That the two can
        never disagree is not assumed: `tests/test_bag_identity.py` asserts, over the real write
        path, that every row's `bag_key` is the set digest of the `feedback_member(source='server')`
        snapshot the same verdict recorded.

        `principal_ref` / `role` attribute the row. They are nullable because rows written before
        `0007` have no author and inventing one would be worse than admitting none.
        """
        cur = await self.conn.execute("SELECT 1 FROM situation WHERE id=?", (situation_id,))
        if await cur.fetchone() is None:
            return FeedbackResult(exists=False, inserted=False)
        if not self._has_bag_key:
            # A database that has not been migrated past `0014` still carries `0007`'s two-column
            # index, and this statement is byte-identical to what v0.16.0 issued. The probe is at
            # `open()` rather than here for the reason `_has_lifecycle`'s is (DECISIONS #250): this
            # runs on every verdict and every gesture, and a caught `OperationalError` per call
            # would infer the schema from a failure instead of asking once.
            cur = await self.conn.execute(
                "INSERT INTO feedback (situation_id, verdict, created_at, principal_ref, role) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (situation_id, verdict) DO NOTHING RETURNING id",
                (situation_id, verdict, ts, principal_ref, role),
            )
        else:
            cur = await self.conn.execute(
                "SELECT alarm_id FROM situation_alarm WHERE situation_id=?", (situation_id,)
            )
            key = bag_key([int(r[0]) for r in await cur.fetchall()])
            cur = await self.conn.execute(
                "INSERT INTO feedback (situation_id, verdict, created_at, principal_ref, role, "
                "bag_key) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (situation_id, verdict, bag_key) DO NOTHING RETURNING id",
                (situation_id, verdict, ts, principal_ref, role, key),
            )
        row = await cur.fetchone()
        # v0.8.0 returns the id as well. `RETURNING id` was already there — v0.7.1 only tested it
        # for None-ness — so this reads a value the statement always produced and discarded.
        return FeedbackResult(
            exists=True, inserted=row is not None, id=int(row[0]) if row is not None else None
        )

    async def label_target_exists(self, kind: str, target_id: int) -> bool:
        """Does the thing this label would name actually exist? (v0.7.1, F37)

        `label` has no foreign key and `prune()` never touched it, so v0.7.0's unconditional UPSERT
        was an unbounded, never-reclaimed write primitive: a POST naming any integer created a row.
        The caller turns a False here into the **same 404** an out-of-scope target produces, so the
        fix for F37 cannot re-introduce the existence oracle F34 closes.
        """
        # v0.16.3: three kinds, two tables. `ne` replaces `device` (DECISIONS #281) and `severity`
        # is declared **per alarm class**, so it resolves against the same table `class` does
        # (DECISIONS #283) — the qualifier that will later name a varbind is not an existence
        # question, because a class either exists or it does not.
        table = {"ne": "ne", "class": "alarm_class", "severity": "alarm_class"}.get(kind)
        if table is None:
            return False
        # nosec B608 - `table` comes from the fixed literal mapping directly above, never from input
        cur = await self.conn.execute(f"SELECT 1 FROM {table} WHERE id=?", (target_id,))  # nosec B608
        return await cur.fetchone() is not None

    async def set_label(
        self, kind: str, target_id: int, label: str, ts: float, qualifier: str = ""
    ) -> None:
        """Write one operator declaration. **The derived value is never touched** (directive 4).

        `qualifier` defaults to `''`, which means *the whole target*. Only a severity will ever
        carry another value, and not in this release: `0016` put the column and the primary key in
        place so that refining a severity declaration to class + varbind is a read rule rather than
        a second migration (DECISIONS #283).
        """
        await self.conn.execute(
            "INSERT INTO label (kind, target_id, qualifier, label, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (kind, target_id, qualifier) DO UPDATE SET label=excluded.label, "
            "updated_at=excluded.updated_at",
            (kind, target_id, qualifier, label, ts),
        )

    async def learned_severity_ranks(self, class_id: int) -> list[int]:
        """The distinct severity ranks the appliance itself learned for an alarm class.

        Read at the moment a severity is declared, so the audit row records what the appliance
        was saying when an operator said otherwise (DECISIONS #285). It is a **record**, not an
        input: nothing consumes it, and no declaration produces a training row (#286).
        """
        cur = await self.conn.execute(
            "SELECT DISTINCT severity_rank FROM alarm "
            "WHERE class_id=? AND severity_rank IS NOT NULL ORDER BY severity_rank",
            (class_id,),
        )
        return [int(r[0]) for r in await cur.fetchall()]

    async def clear_label(self, kind: str, target_id: int, qualifier: str = "") -> bool:
        """Withdraw one declaration, and say whether there was one. **The revert** (#284).

        A declaration that cannot be undone is a declaration nobody makes, and the appliance's own
        derived value is still there to fall back to — that is the whole reason precedence is a
        read-time decision and not an overwrite.
        """
        cur = await self.conn.execute(
            "DELETE FROM label WHERE kind=? AND target_id=? AND qualifier=? RETURNING target_id",
            (kind, target_id, qualifier),
        )
        return await cur.fetchone() is not None

    async def situation_opened_at(self, situation_id: int) -> float | None:
        """When the situation opened — the other half of label latency.

        A verdict four seconds after a situation opened and one after ten minutes of investigation
        are not the same evidence, and `feedback.created_at` alone cannot tell them apart. Copied
        onto the label row rather than joined later because `prune()` deletes closed situations on
        the operational schedule, taking the open time with it.
        """
        cur = await self.conn.execute(
            "SELECT created_at FROM situation WHERE id=?", (situation_id,)
        )
        row = await cur.fetchone()
        return float(row[0]) if row is not None else None

    async def add_feedback_members(
        self, feedback_id: int, source: str, alarm_ids: list[int]
    ) -> None:
        """The ordered bag, server-side or client-reported (§5.4).

        `INSERT OR REPLACE` because a *changed* verdict is a legitimate correction (F36) that
        re-posts, and the second post's bag is the one that matches its row.

        An **empty** ``alarm_ids`` writes nothing and that is correct, not a no-op to guard against:
        a verdict posted to an already-merged situation genuinely has an empty bag, and recording
        zero rows is how that population becomes countable.
        """
        if not alarm_ids:
            return
        await self.conn.executemany(
            "INSERT OR REPLACE INTO feedback_member (feedback_id, source, position, alarm_id) "
            "VALUES (?, ?, ?, ?)",
            [(feedback_id, source, i, aid) for i, aid in enumerate(alarm_ids)],
        )

    async def feedback_members(self, feedback_id: int, source: str) -> list[int]:
        cur = await self.conn.execute(
            "SELECT alarm_id FROM feedback_member WHERE feedback_id=? AND source=? "
            "ORDER BY position",
            (feedback_id, source),
        )
        return [int(r[0]) for r in await cur.fetchall()]

    async def annotate_feedback(self, feedback_id: int, **fields: Any) -> None:
        """Set the label row's dataset columns. Written once, at the moment of the verdict.

        **The store layer refuses a reconciled count that is not one** (v0.9.2). Migration `0011`
        carries the same invariant as a `CHECK`, and the schema is the *last* line rather than the
        only one: a violation that reaches SQLite is then unambiguous evidence of a bug in a layer
        above, instead of an ambiguity about which layer was responsible
        (`EVIDENCE-BOUNDARY-0.9.2.md` §4.3).

        Raising is safe here and is the correct failure. Every caller is inside
        `labels.record_label`'s `try`, so a violation degrades **capture** — it loses the
        annotation, never the operator's verdict, which the caller has already recorded, and never
        the response, its status or its timing.
        """
        if not fields:
            return
        _check_reconciled(fields)
        assignments = ", ".join(f"{name}=?" for name in fields)
        await self.conn.execute(
            f"UPDATE feedback SET {assignments} WHERE id=?",  # nosec B608
            (*fields.values(), feedback_id),
        )

    async def reconciliation_drift(self) -> list[dict[str, Any]]:
        """Rows whose stored reconciled count disagrees with a recomputation from the child tables.

        **The reconciliation query.** `excluded_reconciled` is a denormalized copy, and the
        normalized tables stay the source of truth, so the system carries a query that rebuilds it
        and compares rather than trusting the copy. `COUNT(DISTINCT alarm_id)` for the reason
        migration `0011` gives: `feedback_exclusion` is keyed on `(feedback_id, position)`, so one
        member marked three times is three evidence rows and one mark.

        **This reports. It never corrects** (DECISIONS #134). A disagreement means a write path is
        broken, and silently repairing the row would destroy the evidence of that — had this check
        existed in v0.9.1 as a corrector, F46 would have been invisible.

        Deterministic: ordered by `id`, and it returns rows rather than a verdict so the caller
        decides what to say about them. Empty on every healthy corpus.
        """
        cur = await self.conn.execute(
            "SELECT f.id AS feedback_id, f.excluded_count, f.excluded_reconciled AS stored, "
            "       f.excluded_reconciled_source AS source, ("
            "  SELECT COUNT(DISTINCT x.alarm_id) FROM feedback_exclusion x "
            "    JOIN feedback_member m ON m.feedback_id = x.feedback_id "
            "     AND m.alarm_id = x.alarm_id AND m.source = 'server' "
            "   WHERE x.feedback_id = f.id) AS recomputed "
            "FROM feedback f WHERE f.excluded_reconciled IS NOT NULL "
            "  AND f.excluded_reconciled <> ("
            "  SELECT COUNT(DISTINCT x.alarm_id) FROM feedback_exclusion x "
            "    JOIN feedback_member m ON m.feedback_id = x.feedback_id "
            "     AND m.alarm_id = x.alarm_id AND m.source = 'server' "
            "   WHERE x.feedback_id = f.id) "
            "ORDER BY f.id"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def add_feedback_exclusion(self, feedback_id: int, alarm_ids: list[int]) -> None:
        """The members the operator marked as **not belonging** (v0.9.1, migration `0010`).

        `DELETE` then insert, rather than `INSERT OR REPLACE`, because this is a *set* and a
        changed verdict re-posts (F36): replacing position-by-position would leave the tail of a
        longer previous marking behind, and the row would then assert negatives the operator had
        withdrawn. The bag next door can use `INSERT OR REPLACE` safely because the server writes
        it and it does not shrink under a correction; this one is the client's and does.

        An **empty** ``alarm_ids`` writes nothing, and the caller records `excluded_count = NULL`
        rather than 0 — *"the operator marked nothing"* is a **plain** split, not an assertion
        about an empty set.
        """
        await self.conn.execute(
            "DELETE FROM feedback_exclusion WHERE feedback_id=?", (feedback_id,)
        )
        if not alarm_ids:
            return
        await self.conn.executemany(
            "INSERT INTO feedback_exclusion (feedback_id, position, alarm_id) VALUES (?, ?, ?)",
            [(feedback_id, i, aid) for i, aid in enumerate(alarm_ids)],
        )

    async def feedback_exclusion(self, feedback_id: int) -> list[int]:
        cur = await self.conn.execute(
            "SELECT alarm_id FROM feedback_exclusion WHERE feedback_id=? ORDER BY position",
            (feedback_id,),
        )
        return [int(r[0]) for r in await cur.fetchall()]

    async def reconciled_marks(self, feedback_id: int) -> list[int]:
        """**The ids the operator actually marked**, reconciled against the bag (v0.16.1, F90).

        `excluded_reconciled` is the *count* of this set and has been stored since `0011`; the set
        itself was never readable, so `promotion_metrics._asserting_bags` rebuilt it as
        `members[:excluded_reconciled]` — a positional prefix of **live** membership. Measured, on
        a bag of eight marked `[7, 8]`: the judge reconstructed `[1, 2]`, and four of the twelve
        pairs it measured were pairs the operator had asserted.

        **The same expression `reconciliation_drift` recomputes the count from**, returning the ids
        instead of counting them, so the count and the set cannot drift apart — F46's property, one
        level down. `DISTINCT` for `0011`'s reason: `feedback_exclusion`'s primary key is
        `(feedback_id, position)`, so a client that sent `[5, 5, 5]` has three evidence rows and
        **one** mark. Ordered by `alarm_id` so the reconstruction is deterministic; the set is what
        carries meaning and the order is only so two runs agree.

        A mark that named nothing in the bag contributes nothing here and is still recorded
        verbatim next door — the join is where the untrusted half meets the trusted one, and it is
        deliberately silent (`labels.Exclusion.marked_positions`, the same intersection).
        """
        cur = await self.conn.execute(
            "SELECT DISTINCT x.alarm_id FROM feedback_exclusion x "
            "JOIN feedback_member m ON m.feedback_id = x.feedback_id "
            " AND m.alarm_id = x.alarm_id AND m.source = 'server' "
            "WHERE x.feedback_id = ? ORDER BY x.alarm_id",
            (feedback_id,),
        )
        return [int(r[0]) for r in await cur.fetchall()]
