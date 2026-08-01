"""Situations: open, join, merge, close, and the link rows that justify a grouping."""

from __future__ import annotations

import sqlite3
from typing import Any

from netcorenoc.store.base import StoreBase


class SituationMixin(StoreBase):
    async def create_situation(self, ts: float, scorer_config_id: int | None = None) -> int:
        """Open a situation, recording which scorer configuration formed it (v0.6.0 provenance).

        The engine passes the active `config_id`; it is written here, on the store side under the
        batch lock the engine already holds — never on the datagram path.

        ``None`` means "no configuration is in effect" — the fail-safe state where the engine is
        running on the coded defaults. The column is then left out of the statement entirely
        rather than written as NULL, so this call still succeeds against a schema that predates
        `0005_scorer_config.sql`. That is not hypothetical tidiness: it is what lets the same
        engine code run before and after the migration, which is how the upgrade test proves the
        migration changes no grouping."""
        if scorer_config_id is None:
            cur = await self.conn.execute(
                "INSERT INTO situation (created_at, updated_at) VALUES (?, ?) RETURNING id",
                (ts, ts),
            )
        else:
            cur = await self.conn.execute(
                "INSERT INTO situation (created_at, updated_at, scorer_config_id) "
                "VALUES (?, ?, ?) RETURNING id",
                (ts, ts, scorer_config_id),
            )
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    async def add_alarm_to_situation(self, situation_id: int, alarm_id: int) -> None:
        await self.conn.execute(
            "INSERT OR IGNORE INTO situation_alarm (situation_id, alarm_id) VALUES (?, ?)",
            (situation_id, alarm_id),
        )

    async def merge_situations(self, dst: int, src: int, ts: float) -> None:
        await self.conn.execute(
            "INSERT OR IGNORE INTO situation_alarm (situation_id, alarm_id) "
            "SELECT ?, alarm_id FROM situation_alarm WHERE situation_id=?",
            (dst, src),
        )
        await self.conn.execute("DELETE FROM situation_alarm WHERE situation_id=?", (src,))
        await self.conn.execute("UPDATE link SET situation_id=? WHERE situation_id=?", (dst, src))
        # v0.8.0: `merged_into` records **where the members went**. Until this release the merge
        # marked the source `merged` and said nothing about the destination, so the merge chain was
        # unrecoverable: a reader holding a labelled situation id could not follow it forward to the
        # situation that absorbed it. Phase 0 proved the consequence — a label's referent is
        # destroyed entirely, and no query recovers the bag.
        #
        # One extra column on an UPDATE that already ran: no new statement, no new index, nothing
        # added to the batch path's cost.
        #
        # The fallback is the same discipline `create_situation` follows for `scorer_config_id`
        # (0005): **this call must still succeed against a schema that predates its own column.**
        # That is not defensive tidiness — it is what lets the identical engine code run before and
        # after the migration, which is how `tests/test_upgrade.py` proves that `0008` changes
        # behaviour and the code does not. Merges are rare (four across the whole eval corpus), so
        # the cost of learning this once is a single caught error per process on an old schema.
        if self._has_merged_into is not False:
            try:
                await self.conn.execute(
                    "UPDATE situation SET status='merged', closed_at=?, updated_at=?, "
                    "merged_into=? WHERE id=?",
                    (ts, ts, dst, src),
                )
                self._has_merged_into = True
                await self.touch_situation(dst, ts)
                return
            except sqlite3.OperationalError:
                self._has_merged_into = False  # pre-0008 schema; record it and use the old form
        await self.conn.execute(
            "UPDATE situation SET status='merged', closed_at=?, updated_at=? WHERE id=?",
            (ts, ts, src),
        )
        await self.touch_situation(dst, ts)

    async def touch_situation(self, situation_id: int, ts: float) -> None:
        await self.conn.execute("UPDATE situation SET updated_at=? WHERE id=?", (ts, situation_id))

    async def close_situation(self, situation_id: int, ts: float) -> None:
        await self.conn.execute(
            "UPDATE situation SET status='closed', closed_at=?, updated_at=? "
            "WHERE id=? AND status='open'",
            (ts, ts, situation_id),
        )

    async def set_root(self, situation_id: int, alarm_id: int) -> None:
        await self.conn.execute(
            "UPDATE situation SET root_alarm_id=? WHERE id=?", (alarm_id, situation_id)
        )

    async def manual_close_situation(self, situation_id: int, ts: float) -> bool:
        """Operator ack: close an open situation. Returns False if it was not open."""
        cur = await self.conn.execute(
            "UPDATE situation SET status='closed', closed_at=?, updated_at=? "
            "WHERE id=? AND status='open' RETURNING id",
            (ts, ts, situation_id),
        )
        return await cur.fetchone() is not None

    async def add_link(
        self,
        situation_id: int,
        alarm_a: int,
        alarm_b: int,
        score: float,
        term_t: float,
        term_a: float,
        term_e: float,
        ts: float,
    ) -> None:
        await self.conn.execute(
            "INSERT INTO link (situation_id, alarm_a, alarm_b, score, term_t, term_a, term_e, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (situation_id, alarm_a, alarm_b, score, term_t, term_a, term_e, ts),
        )

    async def idle_open_situations(self, cutoff: float) -> list[int]:
        cur = await self.conn.execute(
            "SELECT id FROM situation WHERE status='open' AND updated_at < ?", (cutoff,)
        )
        return [int(r[0]) for r in await cur.fetchall()]

    async def all_cleared(self, situation_id: int) -> bool:
        cur = await self.conn.execute(
            "SELECT COUNT(*) FROM situation_alarm sa JOIN alarm a ON a.id=sa.alarm_id "
            "WHERE sa.situation_id=? AND a.status='active'",
            (situation_id,),
        )
        row = await cur.fetchone()
        assert row is not None
        return int(row[0]) == 0

    async def open_situation_members(self) -> list[dict[str, Any]]:
        """Members of all open situations, for rebuilding engine state at startup."""
        cur = await self.conn.execute(
            "SELECT sa.situation_id, a.id AS alarm_id, a.class_id, a.device_id, a.first_seen "
            "FROM situation s JOIN situation_alarm sa ON sa.situation_id=s.id "
            "JOIN alarm a ON a.id=sa.alarm_id WHERE s.status='open'"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def situation_members(self, situation_id: int) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT a.id, a.device_id, a.class_id, a.first_seen FROM situation_alarm sa "
            "JOIN alarm a ON a.id=sa.alarm_id WHERE sa.situation_id=?",
            (situation_id,),
        )
        return [dict(r) for r in await cur.fetchall()]
