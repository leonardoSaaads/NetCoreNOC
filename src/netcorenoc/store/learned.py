"""Learned pairwise state (the ``edge`` and ``episode_pair`` tables) and the ``meta`` store."""

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

    async def upsert_episodes(self, rows: list[tuple[str, tuple[int, ...], int, float]]) -> None:
        """Persist dirty episode counts (`engine/correlate/episodes.py`). A store without the
        `0026` table keeps them in memory only, which is what it did before the table existed."""
        if not rows or not await self._has_table("episode_pair"):
            return
        await self.conn.executemany(
            "INSERT INTO episode_pair (kind, k, count, last_ts) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (kind, k) DO UPDATE SET count=excluded.count, last_ts=excluded.last_ts",
            [(kind, ",".join(map(str, key)), count, last) for kind, key, count, last in rows],
        )

    async def prune_episodes(self, older_than: float) -> None:
        if await self._has_table("episode_pair"):
            await self.conn.execute("DELETE FROM episode_pair WHERE last_ts < ?", (older_than,))

    async def load_episodes(self) -> list[tuple[str, tuple[int, ...], int, float]]:
        if not await self._has_table("episode_pair"):
            return []
        cur = await self.conn.execute(
            "SELECT kind, k, count, last_ts FROM episode_pair ORDER BY kind, k"
        )
        return [
            (str(r[0]), tuple(int(x) for x in str(r[1]).split(",")), int(r[2]), float(r[3]))
            for r in await cur.fetchall()
        ]

    async def _has_table(self, name: str) -> bool:
        cur = await self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        )
        return await cur.fetchone() is not None

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
