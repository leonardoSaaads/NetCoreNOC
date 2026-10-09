"""People: a display name and a profile photo per account (v0.25.0, ADR #401, #402).

Storage only. What a photo may be is decided in `crosscutting/avatar.py` before anything reaches
here; who may change whose is decided by the routes. The photo lives in `user_avatar`, a table of
its own, so that every read that lists people carries the digest and never the image.
"""

from __future__ import annotations

from typing import Any

from netcorenoc.crosscutting.avatar import Avatar
from netcorenoc.store.base import StoreBase


class PeopleMixin(StoreBase):
    async def set_display_name(self, user_id: int, name: str | None, now: float) -> None:
        await self.conn.execute(
            "UPDATE user SET display_name=?, updated_at=? WHERE id=?", (name, now, user_id)
        )

    async def put_avatar(self, user_id: int, avatar: Avatar, now: float) -> None:
        await self.conn.execute(
            "INSERT INTO user_avatar (user_id, mime, image, sha256, width, height, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET mime=excluded.mime, "
            "image=excluded.image, sha256=excluded.sha256, width=excluded.width, "
            "height=excluded.height, updated_at=excluded.updated_at",
            (user_id, avatar.mime, avatar.image, avatar.sha256, avatar.width, avatar.height, now),
        )

    async def delete_avatar(self, user_id: int) -> bool:
        cur = await self.conn.execute(
            "DELETE FROM user_avatar WHERE user_id=? RETURNING user_id", (user_id,)
        )
        return await cur.fetchone() is not None

    async def get_avatar(self, user_id: int) -> dict[str, Any] | None:
        """The image and its type and digest — the one read that carries the bytes."""
        cur = await self.conn.execute(
            "SELECT mime, image, sha256 FROM user_avatar WHERE user_id=?", (user_id,)
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def person(self, user_id: int) -> dict[str, Any] | None:
        """One account as a person: id, username, display name, address, role, photo digest."""
        cur = await self.conn.execute(
            "SELECT u.id, u.username, u.display_name, u.email, u.role, a.sha256 AS avatar "
            "FROM user u "
            "LEFT JOIN user_avatar a ON a.user_id=u.id WHERE u.id=?",
            (user_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def set_token_purpose(self, token_id: int, purpose: str | None) -> None:
        await self.conn.execute("UPDATE api_token SET purpose=? WHERE id=?", (purpose, token_id))
