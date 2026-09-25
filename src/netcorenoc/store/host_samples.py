"""Host readings on disk, and the same window read back as bucket means (v0.22.0, ADR #380).

The health sampler (`engine/operate/resources.py`) keeps a two-hour ring in memory for the top
bar's sparklines. That ring is the right size for a glance and the wrong source for the Overview,
whose range control offers up to seven days: a window the ring could not hold was drawn as the
same two hours, and a restart emptied it (F154). So every reading is also written here, and the
Overview reads the window it was asked for **in SQL**, bucketed, so a range change is a different
query rather than a different rendering of the same rows.

A bucket nothing was sampled in is `None`, never `0`: the appliance was not running, or the loop
had not started, and a line drawn through that period would invent a measurement (DECISIONS #289).
"""

from __future__ import annotations

from typing import Any

from netcorenoc.store.base import StoreBase

#: Seven days: the longest range the Overview offers. A reading older than that cannot be asked
#: for, so keeping it would be a table that only grows.
HOST_SAMPLE_RETENTION_S = 7 * 24 * 60 * 60

#: The four columns a bucket is the MEAN of, and the one it is the MAX of. A queue is read for
#: its worst moment — a mean would draw a burst that filled the queue as a quiet bucket.
_MEANS = ("cpu_pct", "mem_pct", "disk_pct", "db_mb")


class HostSampleMixin(StoreBase):
    async def record_host_sample(
        self, at: float, reading: dict[str, Any], queue_depth: int
    ) -> None:
        """Write one reading and prune what the longest range can no longer ask for."""
        await self.conn.execute(
            "INSERT OR REPLACE INTO host_sample "
            "(at, cpu_pct, mem_pct, disk_pct, db_mb, queue_depth) VALUES (?, ?, ?, ?, ?, ?)",
            (
                at,
                reading.get("cpu_pct"),
                reading.get("mem_pct"),
                reading.get("disk_pct"),
                reading.get("db_mb"),
                queue_depth,
            ),
        )
        await self.conn.execute(
            "DELETE FROM host_sample WHERE at < ?", (at - HOST_SAMPLE_RETENTION_S,)
        )

    async def host_series(self, *, since: float, until: float, buckets: int) -> dict[str, Any]:
        """Bucket means over ``[since, until)``, oldest first; an unsampled bucket is ``None``."""
        width = max(1e-6, (until - since) / buckets)
        series: dict[str, list[float | None]] = {
            key: [None] * buckets for key in (*_MEANS, "queue_depth")
        }
        cur = await self.conn.execute(
            "SELECT CAST((at - ?) / ? AS INTEGER) AS b, AVG(cpu_pct), AVG(mem_pct), "
            "AVG(disk_pct), AVG(db_mb), MAX(queue_depth), COUNT(*) FROM host_sample "
            "WHERE at >= ? AND at < ? GROUP BY b",
            (since, width, since, until),
        )
        sampled = 0
        oldest: float | None = None
        for row in await cur.fetchall():
            index = int(row[0])
            if not 0 <= index < buckets:
                continue
            sampled += int(row[6])
            for offset, key in enumerate((*_MEANS, "queue_depth")):
                value = row[1 + offset]
                series[key][index] = None if value is None else round(float(value), 2)
        cur = await self.conn.execute(
            "SELECT MIN(at) FROM host_sample WHERE at >= ? AND at < ?", (since, until)
        )
        first = await cur.fetchone()
        if first is not None and first[0] is not None:
            oldest = float(first[0])
        return {
            "from": since,
            "to": until,
            "bucket_s": width,
            "buckets": buckets,
            "samples": sampled,
            # When the first reading inside the window was taken, so the console can say how much
            # of the window was measured rather than implying all of it was.
            "measured_from": oldest,
            "series": series,
        }
