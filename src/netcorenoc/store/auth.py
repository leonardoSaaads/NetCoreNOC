"""Identity storage: users, sessions, and service tokens.

Storage only. The crypto — scrypt parameters, constant-time comparison, token minting — lives in
``netcorenoc.auth``, and nothing here decides whether a principal is authorized.
"""

from __future__ import annotations

from typing import Any

from netcorenoc.store.base import StoreBase


class AuthMixin(StoreBase):
    async def count_users(self) -> int:
        cur = await self.conn.execute("SELECT COUNT(*) FROM user")
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    async def count_enabled_admins(self, *, excluding: int | None = None) -> int:
        """Enabled admin accounts, optionally not counting one row (F79).

        `excluding` is what makes this answer a question about a *proposed* state rather than the
        current one: "how many enabled admins would remain if this user stopped being one" is the
        same query with its own row taken out, so a role change, a deletion and a disable all ask
        it the same way instead of each computing the remainder differently.
        """
        sql = "SELECT COUNT(*) FROM user WHERE role='admin' AND disabled=0"
        params: tuple[int, ...] = ()
        if excluding is not None:
            sql += " AND id<>?"
            params = (excluding,)
        cur = await self.conn.execute(sql, params)
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    async def create_user(
        self, username: str, password_hash: str, role: str, must_change_password: bool, now: float
    ) -> int:
        cur = await self.conn.execute(
            "INSERT INTO user (username, password_hash, role, must_change_password, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
            (username, password_hash, role, int(must_change_password), now, now),
        )
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    async def get_user_by_name(self, username: str) -> dict[str, Any] | None:
        cur = await self.conn.execute("SELECT * FROM user WHERE username=?", (username,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def get_user(self, user_id: int) -> dict[str, Any] | None:
        cur = await self.conn.execute("SELECT * FROM user WHERE id=?", (user_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def list_users(self) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT id, username, role, must_change_password, disabled, created_at "
            "FROM user ORDER BY username"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def update_user_password(self, user_id: int, password_hash: str, now: float) -> None:
        await self.conn.execute(
            "UPDATE user SET password_hash=?, must_change_password=0, updated_at=? WHERE id=?",
            (password_hash, now, user_id),
        )

    async def set_user_role(self, user_id: int, role: str, now: float) -> None:
        await self.conn.execute(
            "UPDATE user SET role=?, updated_at=? WHERE id=?", (role, now, user_id)
        )

    async def set_must_change_password(self, user_id: int, flag: bool, now: float) -> None:
        await self.conn.execute(
            "UPDATE user SET must_change_password=?, updated_at=? WHERE id=?",
            (int(flag), now, user_id),
        )

    async def delete_user(self, user_id: int) -> bool:
        cur = await self.conn.execute("DELETE FROM user WHERE id=? RETURNING id", (user_id,))
        return await cur.fetchone() is not None

    async def create_session(
        self,
        session_hash: str,
        user_id: int,
        created_at: float,
        last_seen_at: float,
        idle_expiry: float,
        absolute_expiry: float,
        source_ip: str | None,
    ) -> None:
        await self.conn.execute(
            "INSERT INTO session (session_hash, user_id, created_at, last_seen_at, idle_expiry, "
            "absolute_expiry, source_ip) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                session_hash,
                user_id,
                created_at,
                last_seen_at,
                idle_expiry,
                absolute_expiry,
                source_ip,
            ),
        )

    async def get_session(self, session_hash: str) -> dict[str, Any] | None:
        cur = await self.conn.execute(
            "SELECT s.session_hash, s.user_id, s.created_at, s.idle_expiry, s.absolute_expiry, "
            "u.username, u.role, u.must_change_password, u.disabled "
            "FROM session s JOIN user u ON u.id=s.user_id WHERE s.session_hash=?",
            (session_hash,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def touch_session(
        self, session_hash: str, last_seen_at: float, idle_expiry: float
    ) -> None:
        await self.conn.execute(
            "UPDATE session SET last_seen_at=?, idle_expiry=? WHERE session_hash=?",
            (last_seen_at, idle_expiry, session_hash),
        )

    async def delete_session(self, session_hash: str) -> None:
        await self.conn.execute("DELETE FROM session WHERE session_hash=?", (session_hash,))

    async def revoke_user_sessions(self, user_id: int) -> int:
        cur = await self.conn.execute(
            "DELETE FROM session WHERE user_id=? RETURNING session_hash", (user_id,)
        )
        return len(list(await cur.fetchall()))

    async def purge_expired_sessions(self, now: float) -> int:
        cur = await self.conn.execute(
            "DELETE FROM session WHERE absolute_expiry < ? OR idle_expiry < ? "
            "RETURNING session_hash",
            (now, now),
        )
        return len(list(await cur.fetchall()))

    async def create_token(
        self, token_hash: str, name: str, role: str, created_by: str | None, now: float
    ) -> int:
        cur = await self.conn.execute(
            "INSERT INTO api_token (token_hash, name, role, created_at, created_by) "
            "VALUES (?, ?, ?, ?, ?) RETURNING id",
            (token_hash, name, role, now, created_by),
        )
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    async def get_token(self, token_hash: str) -> dict[str, Any] | None:
        cur = await self.conn.execute(
            "SELECT id, token_hash, name, role, revoked FROM api_token WHERE token_hash=?",
            (token_hash,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def list_tokens(self) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT id, name, role, created_at, created_by, last_used_at, revoked "
            "FROM api_token ORDER BY name"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def touch_token_used(self, token_hash: str, now: float) -> None:
        await self.conn.execute(
            "UPDATE api_token SET last_used_at=? WHERE token_hash=?", (now, token_hash)
        )

    async def revoke_token(self, token_id: int, now: float) -> dict[str, Any] | None:
        cur = await self.conn.execute(
            "UPDATE api_token SET revoked=1, revoked_at=? WHERE id=? AND revoked=0 RETURNING name",
            (now, token_id),
        )
        row = await cur.fetchone()
        return {"name": str(row[0])} if row else None
