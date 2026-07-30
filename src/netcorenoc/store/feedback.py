"""Operator feedback verdicts and the device/class label table.

Both carry a v0.7.1 finding: F36 (feedback is at most once per ``(situation, verdict)``, so its
influence on learned state is bounded) and F37 (a label may only name a target that exists, and a
missing target answers the same 404 an out-of-scope one does, so the fix cannot re-introduce the
existence oracle F34 closes).
"""

from __future__ import annotations

from netcorenoc.store.base import StoreBase
from netcorenoc.store.types import FeedbackResult


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
        return FeedbackResult(exists=True, inserted=await cur.fetchone() is not None)

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
