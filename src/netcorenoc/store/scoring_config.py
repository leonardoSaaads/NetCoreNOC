"""The v0.6.0 scoring seam's storage: an append-only history plus a one-row active pointer.

History is immutable (triggers abort UPDATE and DELETE), so applying and rolling back a
configuration are the same operation — moving the pointer.
"""

from __future__ import annotations

from typing import Any

from netcorenoc.store.base import StoreBase


class ScoringConfigMixin(StoreBase):
    async def active_scorer_config(self) -> dict[str, Any] | None:
        """The configuration the one-row pointer names, or None if the pointer is unset.

        Read at the engine's configuration reload point — never per packet, never per candidate
        pair, and never in ``receiver.datagram_received`` (prime directive 2)."""
        cur = await self.conn.execute(
            "SELECT c.* FROM scorer_active a JOIN scorer_config c ON c.id = a.config_id "
            "WHERE a.id = 1"
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def get_scorer_config(self, config_id: int) -> dict[str, Any] | None:
        cur = await self.conn.execute("SELECT * FROM scorer_config WHERE id=?", (config_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def list_scorer_configs(self, limit: int) -> list[dict[str, Any]]:
        """The immutable configuration history, newest first, with the active one flagged."""
        cur = await self.conn.execute(
            "SELECT c.*, (a.config_id IS NOT NULL) AS active FROM scorer_config c "
            "LEFT JOIN scorer_active a ON a.config_id = c.id AND a.id = 1 "
            "ORDER BY c.id DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def insert_scorer_config(
        self,
        scorer_id: str,
        contract_version: str,
        w_t: float,
        w_a: float,
        w_e: float,
        tau_s: float,
        threshold: float,
        params_hash: str,
        created_by: str | None,
        created_at: float,
        note: str,
    ) -> int:
        """Append one immutable configuration row. Never UPDATE, never DELETE (triggers abort)."""
        cur = await self.conn.execute(
            "INSERT INTO scorer_config (scorer_id, contract_version, w_t, w_a, w_e, tau_s, "
            "threshold, params_hash, created_by, created_at, note) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (
                scorer_id,
                contract_version,
                w_t,
                w_a,
                w_e,
                tau_s,
                threshold,
                params_hash,
                created_by,
                created_at,
                note,
            ),
        )
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    async def set_active_scorer_config(
        self, config_id: int, activated_by: str | None, ts: float
    ) -> bool:
        """Point the single active row at a configuration. Apply and rollback are this one call —
        history is immutable, so reverting is moving the pointer, never editing or deleting.

        **v0.11.0: it also clears `model_version_id`, in the same statement.** `0013`'s `CHECK`
        admits exactly one pointer, so an `ON CONFLICT` clause that updated `config_id` and left the
        other column alone would raise `IntegrityError` the first time an admin retuned the additive
        scorer while a model version was active — half-way through a write, on a path that has
        nothing to do with promotion. The `CHECK` is the second line of defence; writing both
        columns every time is the first.
        """
        cur = await self.conn.execute("SELECT 1 FROM scorer_config WHERE id=?", (config_id,))
        if await cur.fetchone() is None:
            return False
        await self.conn.execute(
            "INSERT INTO scorer_active (id, config_id, model_version_id, activated_by, "
            "activated_at) VALUES (1, ?, NULL, ?, ?) "
            "ON CONFLICT (id) DO UPDATE SET config_id=excluded.config_id, model_version_id=NULL, "
            "activated_by=excluded.activated_by, activated_at=excluded.activated_at",
            (config_id, activated_by, ts),
        )
        return True

    async def recent_alarms_for_preview(self, limit: int) -> list[dict[str, Any]]:
        """A bounded, read-only snapshot of recent alarms for the scorer what-if (v0.6.0).

        Most-recent-first at the SQL level so the cap keeps the *newest* window, then returned in
        chronological order so the replay ordering — and therefore the preview result — is
        deterministic. Reads five columns and writes nothing."""
        cur = await self.conn.execute(
            "SELECT id, ne_id, entity_id, class_id, first_seen FROM alarm "
            "ORDER BY first_seen DESC, id DESC LIMIT ?",
            (limit,),
        )
        rows = [dict(r) for r in await cur.fetchall()]
        rows.reverse()
        return rows
