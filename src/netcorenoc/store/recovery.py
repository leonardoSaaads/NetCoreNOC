"""Recovery by email: a person's address and the single-use reset links (v0.30.0, ADR #445).

Storage only. What a valid address is, how long a link lives and who may ask for one are decided by
the routes (`api/routes/recovery.py`); this module keeps the rows and the one rule only SQL can
keep cheaply — a link is consumed by the same statement that checks it, so it cannot be used twice.
"""

from __future__ import annotations

import os
from typing import Any

from netcorenoc.store.base import StoreBase

#: Expired links are kept this long (for an audit question asked the same day), then deleted.
RESET_GRACE_S = 86_400.0
#: The most accounts one address may reach; a shared mailbox is real, an unbounded fan-out is not.
MAX_ACCOUNTS_PER_EMAIL = 5


class RecoveryMixin(StoreBase):
    async def community_hmac_key(self) -> bytes:
        """The per-install 32-byte key communities are hashed under (F4), made once in `meta`.

        Read by the receiver at start and by the SNMP settings route, which hashes the communities
        an admin accepts under the same key, so the two always agree. The caller commits.
        """
        cur = await self.conn.execute("SELECT value FROM meta WHERE key='community_hmac_key'")
        row = await cur.fetchone()
        if row is not None:
            return bytes.fromhex(str(row[0]))
        key = os.urandom(32)
        await self.conn.execute(
            "INSERT INTO meta (key, value) VALUES ('community_hmac_key', ?)", (key.hex(),)
        )
        return key

    async def set_user_email(self, user_id: int, email: str | None, now: float) -> None:
        await self.conn.execute(
            "UPDATE user SET email=?, updated_at=? WHERE id=?", (email, now, user_id)
        )

    async def recovery_accounts(self, login: str) -> list[dict[str, Any]]:
        """The enabled accounts a reset request names: by username, else by address."""
        cur = await self.conn.execute(
            "SELECT id, username, email FROM user WHERE disabled=0 AND email IS NOT NULL "
            "AND (username=? OR (lower(email)=lower(?) AND NOT EXISTS "
            "(SELECT 1 FROM user WHERE username=?))) ORDER BY id LIMIT ?",
            (login, login, login, MAX_ACCOUNTS_PER_EMAIL),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def reset_requested_since(self, user_id: int, since: float) -> bool:
        cur = await self.conn.execute(
            "SELECT 1 FROM password_reset WHERE user_id=? AND created_at>=? LIMIT 1",
            (user_id, since),
        )
        return await cur.fetchone() is not None

    async def create_reset(
        self, user_id: int, token_hash: str, now: float, expires_at: float, source_ip: str
    ) -> None:
        """A new link for `user_id`, superseding any it still had, and the table kept small."""
        await self.conn.execute(
            "DELETE FROM password_reset WHERE (user_id=? AND used_at IS NULL) OR expires_at<?",
            (user_id, now - RESET_GRACE_S),
        )
        await self.conn.execute(
            "INSERT INTO password_reset (user_id, token_hash, created_at, expires_at, source_ip) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, token_hash, now, expires_at, source_ip),
        )

    async def take_reset(self, token_hash: str, now: float) -> dict[str, Any] | None:
        """Consume a live link and return its account, or None. One statement: never twice."""
        cur = await self.conn.execute(
            "UPDATE password_reset SET used_at=? WHERE token_hash=? AND used_at IS NULL "
            "AND expires_at>? RETURNING user_id",
            (now, token_hash, now),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        cur = await self.conn.execute(
            "SELECT id, username, disabled FROM user WHERE id=?", (int(row["user_id"]),)
        )
        user = await cur.fetchone()
        return dict(user) if user is not None and not user["disabled"] else None
