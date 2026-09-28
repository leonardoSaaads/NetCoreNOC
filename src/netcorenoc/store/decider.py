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
    async def decider_mode(self) -> str:
        """The mode in force. A database without the `0026` table has only ever run the formula,
        and reading it as anything else would change its behaviour without its migration."""
        if not self._has_decider:
            return "additive"
        cur = await self.conn.execute(
            "SELECT mode FROM decider_setting ORDER BY id DESC LIMIT 1"
        )
        row = await cur.fetchone()
        return str(row[0]) if row else "shipped"

    async def set_decider_mode(self, mode: str, set_by: str, ts: float, reason: str) -> int:
        if mode not in DECIDER_MODES:
            raise ValueError(f"unknown decider mode {mode!r}")
        cur = await self.conn.execute(
            "INSERT INTO decider_setting (mode, set_by, set_at, reason) VALUES (?, ?, ?, ?) "
            "RETURNING id",
            (mode, set_by, ts, reason),
        )
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    async def decider_history(self, limit: int = 20) -> list[dict[str, Any]]:
        if not self._has_decider:
            return []
        cur = await self.conn.execute(
            "SELECT id, mode, set_by, set_at, reason FROM decider_setting ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in await cur.fetchall()]
