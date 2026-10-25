"""The credential vault's rows: credentials and their reachability checks (ADRs #455, #457).

Storage only. What a credential is lives in `engine/noc/kinds.py`, how it is sealed in
`crosscutting/secretbox.py`, and who may touch it in `api/routes/secrets.py`. The sealed column is
read by exactly one method, :meth:`NocVaultMixin.noc_get_secret` with ``sealed=True``; every list
leaves it out, so no list a route returns can carry it by accident.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from netcorenoc.store.base import StoreBase

#: The checks kept per credential, newest first: enough for the light and a short history.
KEEP_CHECKS = 50

_METADATA = (
    "id, ref, name, description, kind, access_level, host, port, protocol_version, version, "
    "agent_access, monitor, created_at, created_by, updated_at, updated_by, revealed_at, "
    "revealed_by, checked_at"
)


class NocVaultMixin(StoreBase):
    async def noc_create_secret(self, row: dict[str, Any], now: float, actor: str) -> int:
        cur = await self.conn.execute(
            "INSERT INTO noc_secret (ref, name, description, kind, access_level, host, port, "
            "protocol_version, sealed, monitor, created_at, created_by, updated_at, updated_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (
                row["ref"],
                row["name"],
                row["description"],
                row["kind"],
                row["access_level"],
                row["host"],
                row["port"],
                row["protocol_version"],
                row["sealed"],
                row["monitor"],
                now,
                actor,
                now,
                actor,
            ),
        )
        found = await cur.fetchone()
        assert found is not None
        return int(found[0])

    async def noc_list_secrets(self) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            f"SELECT {_METADATA} FROM noc_secret ORDER BY name, id"  # nosec B608 - a literal
        )
        return [dict(r) for r in await cur.fetchall()]

    async def noc_get_secret(
        self, secret_id: int, *, sealed: bool = False
    ) -> dict[str, Any] | None:
        columns = f"{_METADATA}, sealed" if sealed else _METADATA
        cur = await self.conn.execute(
            f"SELECT {columns} FROM noc_secret WHERE id=?",  # nosec B608 - two literals
            (secret_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def noc_update_secret(
        self, secret_id: int, row: dict[str, Any], now: float, actor: str, *, resealed: bool
    ) -> None:
        """Every editable column at once. ``resealed`` counts a new version of the credential."""
        await self.conn.execute(
            "UPDATE noc_secret SET name=?, description=?, kind=?, access_level=?, host=?, port=?, "
            "protocol_version=?, sealed=?, monitor=?, updated_at=?, updated_by=?, "
            "version = version + ? WHERE id=?",
            (
                row["name"],
                row["description"],
                row["kind"],
                row["access_level"],
                row["host"],
                row["port"],
                row["protocol_version"],
                row["sealed"],
                row["monitor"],
                now,
                actor,
                1 if resealed else 0,
                secret_id,
            ),
        )

    async def noc_delete_secret(self, secret_id: int) -> bool:
        cur = await self.conn.execute(
            "DELETE FROM noc_secret WHERE id=? RETURNING id", (secret_id,)
        )
        return await cur.fetchone() is not None

    async def noc_set_agent_access(
        self, secret_id: int, enabled: bool, now: float, actor: str
    ) -> bool:
        """Whether the switch moved."""
        cur = await self.conn.execute(
            "UPDATE noc_secret SET agent_access=?, updated_at=?, updated_by=? "
            "WHERE id=? AND agent_access <> ? RETURNING id",
            (enabled, now, actor, secret_id, enabled),
        )
        return await cur.fetchone() is not None

    async def noc_mark_revealed(self, secret_id: int, now: float, actor: str) -> None:
        await self.conn.execute(
            "UPDATE noc_secret SET revealed_at=?, revealed_by=? WHERE id=?",
            (now, actor, secret_id),
        )

    async def noc_due_checks(self, before: float, kinds: Sequence[str]) -> list[dict[str, Any]]:
        """Monitored credentials of a checkable kind not checked since ``before``, oldest first."""
        if not kinds:
            return []
        marks = ", ".join("?" for _ in kinds)
        cur = await self.conn.execute(
            f"SELECT id, kind, host, port FROM noc_secret WHERE monitor "  # nosec B608 - marks
            f"AND kind IN ({marks}) AND (checked_at IS NULL OR checked_at < ?) "
            "ORDER BY checked_at NULLS FIRST, id LIMIT 500",
            (*kinds, before),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def noc_record_check(
        self,
        secret_id: int,
        at: float,
        ok: bool,
        latency_ms: float | None,
        detail: str,
        trigger: str,
    ) -> None:
        """One check, and the credential's history trimmed to :data:`KEEP_CHECKS`."""
        cur = await self.conn.execute(
            "UPDATE noc_secret SET checked_at=? WHERE id=? RETURNING id", (at, secret_id)
        )
        if await cur.fetchone() is None:
            return  # deleted while it was being checked
        await self.conn.execute(
            "INSERT INTO noc_secret_check (secret_id, at, ok, latency_ms, detail, trigger) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (secret_id, at, ok, latency_ms, detail[:200], trigger),
        )
        await self.conn.execute(
            "DELETE FROM noc_secret_check WHERE secret_id=? AND id NOT IN (SELECT id FROM "
            "noc_secret_check WHERE secret_id=? ORDER BY at DESC, id DESC LIMIT ?)",
            (secret_id, secret_id, KEEP_CHECKS),
        )

    async def noc_checks(
        self, secret_ids: Sequence[int], *, per: int = 5
    ) -> dict[int, list[dict[str, Any]]]:
        """The ``per`` newest checks of each credential, newest first."""
        out: dict[int, list[dict[str, Any]]] = {int(i): [] for i in secret_ids}
        if not out:
            return out
        cur = await self.conn.execute(
            "SELECT secret_id, at, ok, latency_ms, detail, trigger FROM ("
            "SELECT c.*, row_number() OVER (PARTITION BY secret_id ORDER BY at DESC, id DESC) "
            "AS n FROM noc_secret_check c WHERE secret_id = ANY(?)) ranked WHERE n <= ? "
            "ORDER BY secret_id, at DESC, n",
            (list(out), per),
        )
        for row in await cur.fetchall():
            out[int(row["secret_id"])].append(dict(row))
        return out

    async def noc_history(self, secret_id: int, limit: int = 50) -> list[dict[str, Any]]:
        """The audit rows about one credential, newest first: who created, changed, revealed,
        leased and used it. Read from the chain itself, so it cannot disagree with the log."""
        cur = await self.conn.execute(
            "SELECT id, ts, actor, role, action, outcome, details FROM audit_log "
            "WHERE object_type='noc_secret' AND object_id=? ORDER BY id DESC LIMIT ?",
            (str(secret_id), limit),
        )
        return [{**dict(r), "details": json.loads(r["details"])} for r in await cur.fetchall()]
