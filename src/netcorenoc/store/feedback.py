"""The label row and everything hanging off it, and the device/class label table.

Both original halves carry a v0.7.1 finding: F36 (feedback is at most once per
``(situation, verdict)``, so its influence on learned state is bounded) and F37 (a label may only
name a target that exists, and a missing target answers the same 404 an out-of-scope one does, so
the fix cannot re-introduce the existence oracle F34 closes).

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

from typing import Any

from netcorenoc.store.base import StoreBase
from netcorenoc.store.types import FeedbackResult


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
        """Record one verdict, **at most once per (situation, verdict)** (v0.7.1, F36).

        v0.7.0 inserted unconditionally, so N identical posts wrote N rows and drove N learning
        effects — each of which advanced the global forgetting epoch. The `UNIQUE` index added by
        migration `0007` makes the repeat a no-op at the storage layer, and the returned
        :class:`FeedbackResult` is what lets `Engine.apply_feedback` apply the learning effect
        **only** on a genuine insert. A situation has two possible verdicts, so its total influence
        on the learned state is bounded at two applications however many times anyone posts.

        `principal_ref` / `role` attribute the row. They are nullable because rows written before
        `0007` have no author and inventing one would be worse than admitting none.
        """
        cur = await self.conn.execute("SELECT 1 FROM situation WHERE id=?", (situation_id,))
        if await cur.fetchone() is None:
            return FeedbackResult(exists=False, inserted=False)
        cur = await self.conn.execute(
            "INSERT INTO feedback (situation_id, verdict, created_at, principal_ref, role) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT (situation_id, verdict) DO NOTHING RETURNING id",
            (situation_id, verdict, ts, principal_ref, role),
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
        table = {"device": "device", "class": "alarm_class"}.get(kind)
        if table is None:
            return False
        # nosec B608 - `table` comes from the fixed literal mapping directly above, never from input
        cur = await self.conn.execute(f"SELECT 1 FROM {table} WHERE id=?", (target_id,))  # nosec B608
        return await cur.fetchone() is not None

    async def set_label(self, kind: str, target_id: int, label: str, ts: float) -> None:
        await self.conn.execute(
            "INSERT INTO label (kind, target_id, label, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (kind, target_id) DO UPDATE SET label=excluded.label, "
            "updated_at=excluded.updated_at",
            (kind, target_id, label, ts),
        )

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
