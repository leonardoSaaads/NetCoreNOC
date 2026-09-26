"""Active alarms over time — a stock, not a flow (v0.23.0, #393).

The Overview's "What is happening" chart counted alarms RAISED per bucket. The report: ten critical
alarms appear and the chart drops to zero at that moment. Both were true — a fault that keeps
firing moves `last_seen` and raises nothing, so the flow was empty while the stock was at its
highest. An operator reading a chart under "what is happening" reads the stock: how many alarms
are active, and whether that number is rising. This module counts that, in SQL:

* an alarm is active on ``[first_seen, cleared_at)`` — open-ended while it has not cleared;
* the value at each bucket's END is the baseline active at ``since`` plus every raise, minus every
  clear, up to that instant — one grouped read of each, never a row per alarm;
* the last point is the instant the read was made, so the chart's latest value is the number on
  the severity card beside it, band for band.

Bands resolve with the census's precedence (declaration, rule, what the trap carried or the
appliance learned), per (class, rank) group, so a band here means what it means on that card.
An alarm nothing can place is counted under `unplaced`, never dropped.
"""

from __future__ import annotations

from typing import Any

from netcorenoc.ingest import known_oids
from netcorenoc.store.activity import BANDS, ActivityMixin
from netcorenoc.store.narrow import Narrow, narrowed

#: Every band a count can land in, most severe first. `vendor` is a vendor's own numbering (F99).
ALL_BANDS: tuple[str, ...] = (*BANDS, "unplaced", "vendor")


class ActiveMixin(ActivityMixin):
    async def _band_of(self) -> Any:
        """A resolver `(class_id, oid, placed_rank) -> band` with the census's precedence."""
        declared = await self.class_severity_declarations()
        catalogue = await self.catalogue()
        memo: dict[tuple[int, str, Any], str] = {}

        def band(class_id: int, oid: str, placed: Any) -> str:
            key = (class_id, oid, placed)
            hit = memo.get(key)
            if hit is not None:
                return hit
            token = declared.get(class_id)
            rank = (
                known_oids.severity_rank(token)
                if token is not None
                else catalogue.resolve(oid).severity_rank
            )
            if rank is None:
                rank = placed
            out = (
                "unplaced"
                if rank is None
                else BANDS[int(rank)]
                if int(rank) < len(BANDS)
                else "vendor"
            )
            memo[key] = out
            return out

        return band

    async def _sweep(
        self,
        *,
        since: float,
        until: float,
        buckets: int,
        where: str,
        args: tuple[Any, ...],
        per_ne: bool,
    ) -> dict[int | None, dict[str, list[int]]]:
        """`{ne_id or None: {band: [active at each bucket end]}}` over ``[since, until)``."""
        width = max(1e-6, (until - since) / buckets)
        ne = "a.ne_id" if per_ne else "NULL"
        band_of = await self._band_of()
        start: dict[tuple[Any, str], int] = {}
        delta: dict[tuple[Any, str], list[int]] = {}
        # Active at `since`: raised before it, not cleared before it.
        cur = await self.conn.execute(
            f"SELECT {ne}, c.id, c.oid, a.severity_rank, COUNT(*) FROM alarm a "  # nosec B608
            f"JOIN alarm_class c ON c.id=a.class_id WHERE {where} AND a.first_seen < ? "
            "AND (a.cleared_at IS NULL OR a.cleared_at >= ?) "
            f"GROUP BY {ne}, c.id, a.severity_rank",
            (*args, since, since),
        )
        for ne_id, cid, oid, rank, n in await cur.fetchall():
            key = (ne_id, band_of(int(cid), str(oid), rank))
            start[key] = start.get(key, 0) + int(n)
        # Raises (+1) and clears (-1) inside the window, by the bucket they fall in.
        for column, sign in (("a.first_seen", 1), ("a.cleared_at", -1)):
            cur = await self.conn.execute(
                f"SELECT {ne}, CAST(({column} - ?) / ? AS INTEGER) AS b, c.id, c.oid, "  # nosec B608
                f"a.severity_rank, COUNT(*) FROM alarm a JOIN alarm_class c ON c.id=a.class_id "
                f"WHERE {where} AND {column} >= ? AND {column} < ? "
                f"GROUP BY {ne}, b, c.id, a.severity_rank",
                (since, width, *args, since, until),
            )
            for ne_id, bucket, cid, oid, rank, n in await cur.fetchall():
                if not 0 <= int(bucket) < buckets:
                    continue
                key = (ne_id, band_of(int(cid), str(oid), rank))
                delta.setdefault(key, [0] * buckets)[int(bucket)] += sign * int(n)
        out: dict[int | None, dict[str, list[int]]] = {}
        for key in set(start) | set(delta):
            ne_id, band = key
            running = start.get(key, 0)
            steps = delta.get(key, [0] * buckets)
            series = []
            for step in steps:
                running += step
                series.append(max(running, 0))
            per = out.setdefault(None if ne_id is None else int(ne_id), {})
            if band in per:
                per[band] = [a + b for a, b in zip(per[band], series, strict=True)]
            else:
                per[band] = series
        return out

    async def activity_active(
        self,
        *,
        since: float,
        until: float,
        buckets: int,
        ne_ids: frozenset[int] | None,
        narrow: Narrow | None = None,
        device_ne_id: int | None = None,
    ) -> dict[str, Any]:
        """Active alarms at the end of each bucket, by severity band, over the window."""
        where, args = narrowed(*self._timeline_scope(ne_ids, device_ne_id), narrow)
        swept = await self._sweep(
            since=since, until=until, buckets=buckets, where=where, args=args, per_ne=False
        )
        by_band = swept.get(None, {})
        series = {band: by_band.get(band, [0] * buckets) for band in ALL_BANDS}
        if not any(series["vendor"]):
            del series["vendor"]  # absent rather than a zero band nobody can read (F99)
        return {
            "from": since,
            "to": until,
            "bucket_s": (until - since) / buckets,
            "buckets": buckets,
            "measure": "active at the end of each bucket",
            "series": series,
            "now": {band: values[-1] for band, values in series.items()},
        }

    async def activity_top(
        self,
        *,
        since: float,
        until: float,
        buckets: int,
        ne_ids: frozenset[int] | None,
        bands: tuple[str, ...],
        limit: int = 10,
        narrow: Narrow | None = None,
    ) -> dict[str, Any]:
        """The `limit` elements with the most alarms active now in `bands`, each with its trend.

        Ranked by the count active NOW in the chosen bands, then by the most severe band's count,
        then by id — a decided order. The trend is the same count at each bucket's end, so the row's
        number is the last point of its own line.
        """
        chosen = [band for band in ALL_BANDS if band in bands] or list(ALL_BANDS)
        where, args = narrowed(*self._timeline_scope(ne_ids, None), narrow)
        swept = await self._sweep(
            since=since, until=until, buckets=buckets, where=where, args=args, per_ne=True
        )
        rows = []
        for ne_id, per in swept.items():
            if ne_id is None:
                continue
            trend = [
                sum(values)
                for values in zip(*(per.get(b, [0] * buckets) for b in chosen), strict=True)
            ]
            now = {band: per.get(band, [0] * buckets)[-1] for band in ALL_BANDS}
            if trend[-1] == 0 and not any(trend):
                continue
            rows.append({"ne_id": ne_id, "trend": trend, "now": now, "count": trend[-1]})
        rows.sort(key=lambda r: (-r["count"], *(-r["now"][band] for band in chosen), r["ne_id"]))
        top = rows[: max(1, limit)]
        names = await self._ne_names([r["ne_id"] for r in top])
        for row in top:
            row["device"] = names.get(row["ne_id"], str(row["ne_id"]))
        return {
            "from": since,
            "to": until,
            "bucket_s": (until - since) / buckets,
            "buckets": buckets,
            "bands": chosen,
            "elements": len(rows),
            "top": top,
        }
