"""Learned state-based clear fields (S9, §5.5).

Forward-only: a learned field is stable, so an existing row is never overwritten.
"""

from __future__ import annotations

from typing import Any

from netcorenoc.store.base import StoreBase


class StateClearMixin(StoreBase):
    async def upsert_state_clears(self, rows: list[tuple[Any, ...]], ts: float) -> None:
        """Persist learned (class, varbind) state fields. Forward-only: a learned field is
        stable, so an existing row is never overwritten (DO NOTHING on conflict)."""
        await self.conn.executemany(
            "INSERT INTO state_clear (class_id, varbind_oid, clear_value, raise_value, "
            "learned_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT (class_id, varbind_oid) DO NOTHING",
            [(class_id, oid, clear_v, raise_v, ts) for class_id, oid, clear_v, raise_v in rows],
        )

    async def load_state_clears(self) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT class_id, varbind_oid, clear_value, raise_value FROM state_clear"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def list_state_clears(self) -> list[dict[str, Any]]:
        """Learned state fields joined to their class, for inspection (which OID, which values)."""
        cur = await self.conn.execute(
            "SELECT s.class_id, s.varbind_oid, s.clear_value, s.raise_value, s.learned_at, "
            "COALESCE(cl.label, c.name, c.oid) AS class FROM state_clear s "
            "JOIN alarm_class c ON c.id=s.class_id "
            "LEFT JOIN label cl ON cl.kind='class' AND cl.target_id=c.id "
            "ORDER BY s.learned_at DESC"
        )
        return [dict(r) for r in await cur.fetchall()]
