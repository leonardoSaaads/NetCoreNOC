"""Learned state-based clear fields (S9, §5.5).

Forward-only: a learned field is stable, so an existing row is never overwritten.
"""

from __future__ import annotations

from typing import Any

from netcorenoc.store.base import StoreBase
from netcorenoc.store.types import class_display


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
            # nosec B608 - one fixed literal from `_label_join`, chosen by a schema probe.
            "SELECT s.class_id, s.varbind_oid, s.clear_value, s.raise_value, s.learned_at, "  # nosec B608
            "cl.label AS class_label, c.oid AS class_oid FROM state_clear s "
            "JOIN alarm_class c ON c.id=s.class_id "
            + self._label_join("cl", "class", "c.id")
            + "ORDER BY s.learned_at DESC"
        )
        # `class_display` rather than a second `COALESCE(cl.label, c.name, c.oid)`: `0016` dropped
        # the middle column, and the precedence between a declaration and a derivation now has one
        # implementation for both readers that compose it (v0.16.3, DECISIONS #280).
        return [
            {
                k: v
                for k, v in {
                    **dict(r),
                    "class": class_display(r["class_label"], str(r["class_oid"])),
                }.items()
                if k not in ("class_label", "class_oid")
            }
            for r in await cur.fetchall()
        ]
