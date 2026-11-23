"""Which family decides links (v0.26.0, migration `0026`, ADR #405). SQL only; decides nothing.

The setting is append-only: the newest row is in force. **No method here takes ``store.lock``**,
which is this package's contract for callers.
"""

from __future__ import annotations

from typing import Any

from netcorenoc.store.base import StoreBase

__all__ = ["DECIDER_MODES", "DeciderMixin"]

DECIDER_MODES = ("shipped", "site", "additive")


class DeciderMixin(StoreBase):
    async def decider_mode(self, broker_id: int = 1) -> str:
        """The mode in force for a broker (ADR #495): its newest setting row, or `shipped`."""
        cur = await self.conn.execute(
            "SELECT mode FROM decider_setting WHERE broker_id=? ORDER BY id DESC LIMIT 1",
            (broker_id,),
        )
        row = await cur.fetchone()
        return str(row[0]) if row else "shipped"

    async def set_decider_mode(
        self, mode: str, set_by: str, ts: float, reason: str, broker_id: int = 1
    ) -> int:
        if mode not in DECIDER_MODES:
            raise ValueError(f"unknown decider mode {mode!r}")
        cur = await self.conn.execute(
            "INSERT INTO decider_setting (mode, set_by, set_at, reason, broker_id) "
            "VALUES (?, ?, ?, ?, ?) RETURNING id",
            (mode, set_by, ts, reason, broker_id),
        )
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    async def decider_history(self, limit: int = 20, broker_id: int = 1) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT id, mode, set_by, set_at, reason FROM decider_setting WHERE broker_id=? "
            "ORDER BY id DESC LIMIT ?",
            (broker_id, limit),
        )
        return [dict(r) for r in await cur.fetchall()]
