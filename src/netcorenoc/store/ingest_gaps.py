"""Durable records of windows in which traps were dropped (§5.6).

"Events lost between t1 and t2" — an honest gap is better than a silent one.
"""

from __future__ import annotations

from typing import Any

from netcorenoc.store.base import StoreBase


class IngestGapMixin(StoreBase):
    async def record_ingest_gap(
        self, started_at: float, ended_at: float, dropped: int, reason: str
    ) -> None:
        """Durably record a window of dropped traps: 'events lost between t1 and t2'."""
        await self.conn.execute(
            "INSERT INTO ingest_gap (started_at, ended_at, dropped, reason) VALUES (?, ?, ?, ?)",
            (started_at, ended_at, dropped, reason),
        )

    async def list_ingest_gaps(self, limit: int) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT id, started_at, ended_at, dropped, reason FROM ingest_gap "
            "ORDER BY started_at DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in await cur.fetchall()]
