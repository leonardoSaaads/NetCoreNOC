"""Who may hold the vault, and the leases agents hold instead (ADRs #456, #458).

Storage only: `api/routes/noc_access.py` decides who may grant and who may lease, and
`engine/noc/lease.py` what a lease is. A lease row keeps a SHA-256 of the lease's id and never the
lease, so a copy of this table cannot be replayed.
"""

from __future__ import annotations

from typing import Any

from netcorenoc.store.base import StoreBase

#: A lease row is kept this long after it expires, for the questions an audit asks the same month.
LEASE_GRACE_S = 30 * 86_400.0


class NocAccessMixin(StoreBase):
    async def noc_grants(self) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT subject_ref, role, granted_at, granted_by FROM noc_grant "
            "ORDER BY role, subject_ref"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def noc_has_grant(self, subject_ref: str, role: str) -> bool:
        cur = await self.conn.execute(
            "SELECT 1 FROM noc_grant WHERE subject_ref=? AND role=?", (subject_ref, role)
        )
        return await cur.fetchone() is not None

    async def noc_set_grant(
        self, subject_ref: str, role: str, enabled: bool, now: float, actor: str
    ) -> bool:
        """Grant or withdraw; whether anything changed."""
        if enabled:
            cur = await self.conn.execute(
                "INSERT INTO noc_grant (subject_ref, role, granted_at, granted_by) "
                "VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING RETURNING subject_ref",
                (subject_ref, role, now, actor),
            )
        else:
            cur = await self.conn.execute(
                "DELETE FROM noc_grant WHERE subject_ref=? AND role=? RETURNING subject_ref",
                (subject_ref, role),
            )
        return await cur.fetchone() is not None

    async def noc_create_lease(
        self,
        jti_hash: str,
        secret_id: int,
        subject_ref: str,
        subject_name: str,
        issued_at: float,
        expires_at: float,
    ) -> int:
        await self.conn.execute(
            "DELETE FROM noc_lease WHERE expires_at < ?", (issued_at - LEASE_GRACE_S,)
        )
        cur = await self.conn.execute(
            "INSERT INTO noc_lease (jti_hash, secret_id, subject_ref, subject_name, issued_at, "
            "expires_at) VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
            (jti_hash, secret_id, subject_ref, subject_name, issued_at, expires_at),
        )
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    async def noc_lease_by_digest(self, jti_hash: str) -> dict[str, Any] | None:
        cur = await self.conn.execute(
            "SELECT id, jti_hash, secret_id, subject_ref, subject_name, issued_at, expires_at, "
            "revoked_at, revoked_by, last_used_at, uses FROM noc_lease WHERE jti_hash=?",
            (jti_hash,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def noc_lease(self, lease_id: int) -> dict[str, Any] | None:
        cur = await self.conn.execute(
            "SELECT id, secret_id, subject_ref, subject_name, issued_at, expires_at, revoked_at "
            "FROM noc_lease WHERE id=?",
            (lease_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def noc_use_lease(self, lease_id: int, now: float) -> None:
        await self.conn.execute(
            "UPDATE noc_lease SET uses = uses + 1, last_used_at=? WHERE id=?", (now, lease_id)
        )

    async def noc_revoke_lease(self, lease_id: int, now: float, actor: str) -> bool:
        cur = await self.conn.execute(
            "UPDATE noc_lease SET revoked_at=?, revoked_by=? "
            "WHERE id=? AND revoked_at IS NULL RETURNING id",
            (now, actor, lease_id),
        )
        return await cur.fetchone() is not None

    async def noc_revoke_secret_leases(self, secret_id: int, now: float, actor: str) -> int:
        """End every live lease on one credential (it changed, or agents lost access to it)."""
        cur = await self.conn.execute(
            "UPDATE noc_lease SET revoked_at=?, revoked_by=? "
            "WHERE secret_id=? AND revoked_at IS NULL AND expires_at > ? RETURNING id",
            (now, actor, secret_id, now),
        )
        return len(await cur.fetchall())

    async def noc_revoke_subject_leases(self, subject_ref: str, now: float, actor: str) -> int:
        """End every live lease an agent holds (it is no longer an agent)."""
        cur = await self.conn.execute(
            "UPDATE noc_lease SET revoked_at=?, revoked_by=? "
            "WHERE subject_ref=? AND revoked_at IS NULL AND expires_at > ? RETURNING id",
            (now, actor, subject_ref, now),
        )
        return len(await cur.fetchall())

    async def noc_leases(
        self, *, secret_id: int | None = None, subject_ref: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Leases, newest first, for one credential or one agent (or all)."""
        cur = await self.conn.execute(
            "SELECT l.id, l.secret_id, s.name AS secret_name, l.subject_ref, l.subject_name, "
            "l.issued_at, l.expires_at, l.revoked_at, l.revoked_by, l.last_used_at, l.uses "
            "FROM noc_lease l JOIN noc_secret s ON s.id = l.secret_id "
            "WHERE (?::bigint IS NULL OR l.secret_id = ?) "
            "AND (?::text IS NULL OR l.subject_ref = ?) "
            "ORDER BY l.issued_at DESC, l.id DESC LIMIT ?",
            (secret_id, secret_id, subject_ref, subject_ref, limit),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def noc_live_leases(self, subject_ref: str, now: float) -> int:
        cur = await self.conn.execute(
            "SELECT COUNT(*) FROM noc_lease WHERE subject_ref=? AND revoked_at IS NULL "
            "AND expires_at > ?",
            (subject_ref, now),
        )
        row = await cur.fetchone()
        return int(row[0]) if row else 0
