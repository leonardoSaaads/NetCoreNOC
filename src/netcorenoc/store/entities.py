"""The entity model and the varbind profiler's persisted evidence (§5.1/§5.2).

Entities are forward-only: a promoted NE attributes *new* alarms to the finest entity its learned
discriminator names, and history is never rewritten.
"""

from __future__ import annotations

import json
from typing import Any

from netcorenoc.store.base import StoreBase


class EntityMixin(StoreBase):
    async def list_ne(self) -> list[dict[str, Any]]:
        """Every network element, **with the name an operator gave it** (v0.16.3).

        The join is the whole of F99's sibling complaint — *"I renamed the host and nothing changed
        in Entities"*. This method selected five columns and joined no label, while
        `ui/app/views/entities.js` rendered `${ne.label || ne.ip}` — a field the route had never
        served, so the fallback was permanent. The label was not missing: the same row resolved
        through the alarm projection and on the graph, which is the control that made this a broken
        join rather than a broken write.

        `kind='ne'`, not `kind='device'`. The console wrote against `device.id`, which equals
        `ne.id` on every database anyone has and is a coincidence of insertion order rather than a
        constraint — `0016` moves those rows across by ADDRESS (DECISIONS #281).
        """
        cur = await self.conn.execute(
            "SELECT n.id, n.ip, n.vendor, n.first_seen, n.last_seen, l.label FROM ne n "
            # On a pre-`0016` schema the equipment label is keyed on `device.id`, and there is no
            # device alias here to key it through — which is not a gap: on that schema this screen
            # never showed a label at all, and reproducing that exactly is what the frozen-schema
            # upgrade tests are for. `n.id` under `kind='device'` is refused rather than assumed.
            + self._label_join("l", "ne", "n.id", legacy_target="NULL")
            + "ORDER BY n.id"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def entities_for_ne(self, ne_id: int) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT id, ne_id, parent_id, level, key, key_source, confidence, first_seen, "
            "last_seen FROM entity WHERE ne_id=? ORDER BY level, id",
            (ne_id,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def entity_count_for_ne(self, ne_id: int) -> int:
        cur = await self.conn.execute("SELECT COUNT(*) FROM entity WHERE ne_id=?", (ne_id,))
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    async def get_or_create_entity(
        self,
        ne_id: int,
        parent_id: int,
        level: int,
        key: str,
        key_source: str,
        confidence: float,
        ts: float,
    ) -> int:
        """Insert-or-get a promoted (level ≥ 1) entity; serialised under the store lock."""
        cached = self._entity_ids.get((ne_id, key))
        if cached is not None:
            return cached
        cur = await self.conn.execute(
            "SELECT id FROM entity WHERE ne_id=? AND parent_id=? AND key=?",
            (ne_id, parent_id, key),
        )
        row = await cur.fetchone()
        if row is None:
            cur = await self.conn.execute(
                "INSERT INTO entity (ne_id, parent_id, level, key, key_source, confidence, "
                "first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
                (ne_id, parent_id, level, key, key_source, confidence, ts, ts),
            )
            row = await cur.fetchone()
        assert row is not None
        self._entity_ids[(ne_id, key)] = int(row[0])
        return int(row[0])

    async def entity_keys_for_ne(self, ne_id: int) -> list[str]:
        """Keys of the promoted (level ≥ 1) entities on an NE — seeds the engine's cap set."""
        cur = await self.conn.execute("SELECT key FROM entity WHERE ne_id=? AND level>=1", (ne_id,))
        return [str(r[0]) for r in await cur.fetchall()]

    async def promoted_discriminators(self) -> list[dict[str, Any]]:
        """The learned entity discriminator chain per NE, reconstructed coarsest→finest from
        the promoted entities' levels (for engine restart). One row per (ne, level)."""
        cur = await self.conn.execute(
            "SELECT ne_id, level, key_source AS varbind_oid, MAX(confidence) AS score "
            "FROM entity WHERE level >= 1 GROUP BY ne_id, level, key_source ORDER BY ne_id, level"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def promoted_severities(self) -> list[dict[str, Any]]:
        """The confirmed severity varbind per NE (role='severity'), for engine restart (S8)."""
        cur = await self.conn.execute(
            "SELECT DISTINCT ne_id, varbind_oid FROM varbind_profile WHERE role='severity'"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def closed_alarm_varbind_lifetimes(
        self, ne_id: int, varbind_oid: str, limit: int = 2000
    ) -> list[tuple[str, float]]:
        """(varbind value, lifetime in seconds) for recent closed alarms on the NE whose stored
        varbinds include ``varbind_oid`` — the evidence the severity ordinality test needs (S8).
        Reads the varbinds JSON already on the alarm: no new column, no trap-path cost."""
        cur = await self.conn.execute(
            "SELECT varbinds, first_seen, cleared_at FROM alarm "
            "WHERE ne_id=? AND status='cleared' AND cleared_at IS NOT NULL "
            "ORDER BY cleared_at DESC LIMIT ?",
            (ne_id, limit),
        )
        out: list[tuple[str, float]] = []
        for row in await cur.fetchall():
            lifetime = float(row["cleared_at"]) - float(row["first_seen"])
            for vb in json.loads(row["varbinds"]):
                if vb.get("oid") == varbind_oid:
                    out.append((str(vb.get("value")), lifetime))
                    break
        return out

    async def varbind_profiles_for_ne(self, ne_id: int) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT ne_id, class_id, varbind_oid, n_obs, n_repeat, n_monotonic, n_numeric, "
            "n_distinct, score, role, updated_at FROM varbind_profile WHERE ne_id=? "
            "ORDER BY score DESC, varbind_oid",
            (ne_id,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def upsert_varbind_profiles(self, rows: list[tuple[Any, ...]]) -> None:
        await self.conn.executemany(
            "INSERT INTO varbind_profile (ne_id, class_id, varbind_oid, n_obs, n_repeat, "
            "n_monotonic, n_numeric, n_distinct, score, role, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (ne_id, class_id, varbind_oid) DO UPDATE SET n_obs=excluded.n_obs, "
            "n_repeat=excluded.n_repeat, n_monotonic=excluded.n_monotonic, "
            "n_numeric=excluded.n_numeric, n_distinct=excluded.n_distinct, "
            "score=excluded.score, role=excluded.role, updated_at=excluded.updated_at",
            rows,
        )

    async def load_varbind_profiles(self) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT ne_id, class_id, varbind_oid, n_obs, n_repeat, n_monotonic, n_numeric, "
            "updated_at FROM varbind_profile"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def delete_stale_varbind_profiles(self, cutoff: float) -> int:
        cur = await self.conn.execute(
            "DELETE FROM varbind_profile WHERE updated_at < ? RETURNING ne_id", (cutoff,)
        )
        return len(list(await cur.fetchall()))

    async def clear_varbind_roles(self, ne_id: int) -> int:
        """Null out the learned roles for an NE (S11 reset), keeping the evidence counters."""
        cur = await self.conn.execute(
            "UPDATE varbind_profile SET role=NULL WHERE ne_id=? AND role IS NOT NULL "
            "RETURNING ne_id",
            (ne_id,),
        )
        return len(list(await cur.fetchall()))

    async def delete_varbind_profiles_for_ne(self, ne_id: int) -> None:
        await self.conn.execute("DELETE FROM varbind_profile WHERE ne_id=?", (ne_id,))

    async def reset_ne_ids(self) -> set[int]:
        """NEs whose learned entity discriminator has been reset (S11); the engine skips
        reloading their discriminator on restart until it is legitimately re-learned."""
        cur = await self.conn.execute("SELECT key FROM meta WHERE key LIKE 'entity_reset:%'")
        return {int(str(r[0]).split(":", 1)[1]) for r in await cur.fetchall()}
