"""Learned pairwise state (the ``edge`` table) and the ``meta`` key/value store."""

from __future__ import annotations

from netcorenoc.store.base import StoreBase
from netcorenoc.store.types import EdgeRow


class LearnedMixin(StoreBase):
    async def load_edges(self, kind: str) -> list[EdgeRow]:
        cur = await self.conn.execute(
            "SELECT kind, a_id, b_id, weight, n, g FROM edge WHERE kind=?", (kind,)
        )
        return [
            EdgeRow(r["kind"], r["a_id"], r["b_id"], r["weight"], r["n"], r["g"])
            for r in await cur.fetchall()
        ]

    async def upsert_edges(self, rows: list[EdgeRow], ts: float) -> None:
        await self.conn.executemany(
            "INSERT INTO edge (kind, a_id, b_id, weight, n, g, version, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, ?) ON CONFLICT (kind, a_id, b_id) DO UPDATE SET "
            "weight=excluded.weight, n=excluded.n, g=excluded.g, version=version+1, "
            "updated_at=excluded.updated_at",
            [(r.kind, r.a_id, r.b_id, r.weight, r.n, r.g, ts) for r in rows],
        )

    async def get_meta(self, key: str) -> str | None:
        cur = await self.conn.execute("SELECT value FROM meta WHERE key=?", (key,))
        row = await cur.fetchone()
        return str(row[0]) if row else None

    async def set_meta(self, key: str, value: str) -> None:
        await self.conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    async def del_meta(self, key: str) -> None:
        await self.conn.execute("DELETE FROM meta WHERE key=?", (key,))
