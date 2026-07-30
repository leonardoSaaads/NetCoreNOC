"""Bounded growth: drop old cleared alarms, closed situations, and quarantine."""

from __future__ import annotations

from netcorenoc.store.base import StoreBase


class RetentionMixin(StoreBase):
    async def prune(self, now: float, retention_s: float) -> dict[str, int]:
        """Bounded growth: drop old cleared alarms, closed situations, and quarantine."""
        cutoff = now - retention_s
        counts: dict[str, int] = {}
        cur = await self.conn.execute(
            "SELECT id FROM situation WHERE status IN ('closed','merged') AND closed_at < ?",
            (cutoff,),
        )
        gone = [int(r[0]) for r in await cur.fetchall()]
        counts["situations"] = len(gone)
        if gone:
            # nosec B608 - `marks` is only "?" placeholders; ids are bound parameters
            marks = ",".join("?" * len(gone))
            await self.conn.execute(
                f"DELETE FROM situation_alarm WHERE situation_id IN ({marks})",  # nosec B608
                gone,
            )
            await self.conn.execute(
                f"DELETE FROM link WHERE situation_id IN ({marks})",  # nosec B608
                gone,
            )
            await self.conn.execute(
                f"DELETE FROM feedback WHERE situation_id IN ({marks})",  # nosec B608
                gone,
            )
            await self.conn.execute(
                f"DELETE FROM situation WHERE id IN ({marks})",  # nosec B608
                gone,
            )
        cur = await self.conn.execute(
            "DELETE FROM alarm WHERE status='cleared' AND last_seen < ? AND id NOT IN "
            "(SELECT alarm_id FROM situation_alarm) RETURNING id",
            (cutoff,),
        )
        counts["alarms"] = len(list(await cur.fetchall()))
        cur = await self.conn.execute(
            "DELETE FROM quarantine WHERE received_at < ? RETURNING id", (cutoff,)
        )
        counts["quarantine"] = len(list(await cur.fetchall()))
        return counts
