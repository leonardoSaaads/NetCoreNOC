"""A case's feed: append-only posts and the people a `specified` post names (ADR #461).

Storage only. Who may read a post is `engine/noc/feed.py`, decided per reader over what this
returns; the table refuses an edit or a delete (`migrations/0034_noc_cases.sql`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from netcorenoc.store.base import StoreBase


class NocFeedMixin(StoreBase):
    async def noc_post(
        self,
        situation_id: int,
        author: Mapping[str, Any],
        *,
        visibility: str,
        kind: str,
        body: str,
        readers: Sequence[int] = (),
        now: float,
    ) -> int:
        """One post. ``author`` carries ref, name, kind and role (role ``None`` for the system)."""
        cur = await self.conn.execute(
            "INSERT INTO noc_case_post (situation_id, author_ref, author_name, author_kind, "
            "author_role, visibility, kind, body, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "RETURNING id",
            (
                situation_id,
                author["ref"],
                author["name"],
                author["kind"],
                author.get("role"),
                visibility,
                kind,
                body,
                now,
            ),
        )
        row = await cur.fetchone()
        assert row is not None
        post_id = int(row[0])
        for user_id in sorted(set(readers)):
            await self.conn.execute(
                "INSERT INTO noc_case_post_reader (post_id, user_id) VALUES (?, ?)",
                (post_id, user_id),
            )
        return post_id

    async def noc_posts(
        self, situation_id: int, *, limit: int = 100, before: int | None = None
    ) -> list[dict[str, Any]]:
        """Newest first, each with the user ids it names (empty unless `specified`)."""
        cur = await self.conn.execute(
            "SELECT p.id, p.author_ref, p.author_name, p.author_kind, p.author_role, "
            "p.visibility, p.kind, p.body, p.created_at, "
            "COALESCE(array_agg(r.user_id ORDER BY r.user_id) "
            "FILTER (WHERE r.user_id IS NOT NULL), '{}') AS readers FROM noc_case_post p "
            "LEFT JOIN noc_case_post_reader r ON r.post_id = p.id "
            "WHERE p.situation_id = ? AND (?::bigint IS NULL OR p.id < ?) "
            "GROUP BY p.id ORDER BY p.id DESC LIMIT ?",
            (situation_id, before, before, limit),
        )
        return [{**dict(r), "readers": list(r["readers"])} for r in await cur.fetchall()]

    async def noc_people(self) -> list[dict[str, Any]]:
        """Every enabled account a `specified` post may name: id and username only."""
        cur = await self.conn.execute(
            "SELECT id, username, role FROM app_user WHERE NOT disabled ORDER BY username"
        )
        return [dict(r) for r in await cur.fetchall()]
