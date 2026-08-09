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
        """Set the label row's dataset columns. Written once, at the moment of the verdict."""
        if not fields:
            return
        assignments = ", ".join(f"{name}=?" for name in fields)
        await self.conn.execute(
            f"UPDATE feedback SET {assignments} WHERE id=?",  # nosec B608
            (*fields.values(), feedback_id),
        )

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
