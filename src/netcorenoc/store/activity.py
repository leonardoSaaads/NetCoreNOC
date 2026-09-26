"""The Timeline screen's three reads, each over a window the SQL applies (v0.22.0, F155, ADR #381).

## What was wrong, measured

The screen asked `/api/timeline?limit=300` — the 300 alarms most recently *seen* — and drew them on
an axis from their oldest to their newest mark, labelled with clock times. With the window control
at "last 24 hours" the depth still decided which rows came back, so on the reference lab the axis
covered **124 seconds** of a storm while the control said a day; and below it, a hundred raw rows
with the trap OID as the primary column. The window was in the query, but so was a `LIMIT` that
truncated it, and the axis followed the rows rather than the window.

Each read here is **bounded by the window, never by a row count that could truncate it**:

* :meth:`activity_lanes` — raises per element per bucket, for the few busiest elements, over the
  window. The axis is the window, divided evenly, so a burst is a tall cell and a quiet hour is an
  empty one. What the drawing needs, and nothing it would have to count itself.
* :meth:`activity_groups` — the list an operator reads: consecutive marks of one kind of trap on
  one element, less than `gap_s` apart, are **one row** (*"x14 over 2 min"*). Paged, with the total,
  and every filter a WHERE clause.

Both go through `TimelineMixin._timeline_scope`, the one construction of *"which alarms may this
principal see"* (F35, F38), and a scope too large to bind answers empty rather than counting the
estate.
"""

from __future__ import annotations

from typing import Any, Literal

from netcorenoc.ingest import known_oids
from netcorenoc.store.class_rules import ClassRuleMixin
from netcorenoc.store.narrow import Narrow, narrowed
from netcorenoc.store.timeline_models import TimelineMixin

Kind = Literal["raise", "clear"]

#: The X.733 bands by rank, most severe first; rank 4 is `indeterminate` (and `cleared`, which is
#: not a fault and which a RAISE never carries in practice). Derived from the bundled vocabulary so
#: a band added there appears here — the census's rule (F92).
BANDS: tuple[str, ...] = tuple(
    sorted(
        {rank: token for token, rank in reversed(known_oids.SEVERITY_VOCAB.items())}.values(),
        key=lambda token: known_oids.SEVERITY_VOCAB[token],
    )
)

#: How close two marks of one trap on one element must be to count as the same burst. Five
#: minutes: a flapping port re-raises inside it, and two outages an hour apart stay two rows.
DEFAULT_GAP_S = 300.0


class ActivityMixin(ClassRuleMixin, TimelineMixin):
    """Inherits the timeline's scope predicate and the catalogue's names."""

    def _marks_cte(
        self, where: str, kinds: tuple[Kind, ...], class_id: int | None
    ) -> tuple[str, list[str]]:
        """A `marks(ne_id, class_id, kind, ts)` CTE over the window, and the parts it binds.

        A raise is an alarm's `first_seen`; a clear is its `cleared_at` — the same two marks
        `timeline_marks` has always expanded, now kept in SQL so a window's worth of them never
        has to leave the database to be counted.
        """
        extra = " AND a.class_id=?" if class_id is not None else ""
        parts = []
        order: list[str] = []
        for kind in kinds:
            column = "a.first_seen" if kind == "raise" else "a.cleared_at"
            parts.append(
                f"SELECT a.ne_id AS ne_id, a.class_id AS class_id, '{kind}' AS kind, "  # nosec B608
                f"{column} AS ts FROM alarm a WHERE {where}{extra} "
                f"AND {column} >= ? AND {column} < ?"
            )
            order.append(kind)
        return "WITH marks AS (" + " UNION ALL ".join(parts) + ") ", order

    async def activity_lanes(
        self,
        *,
        since: float,
        until: float,
        buckets: int,
        top: int,
        ne_ids: frozenset[int] | None,
        device_ne_id: int | None = None,
        narrow: Narrow | None = None,
    ) -> dict[str, Any]:
        """Raises per bucket for the `top` busiest elements, plus everything else as one lane."""
        width = max(1e-6, (until - since) / buckets)
        where, args = narrowed(*self._timeline_scope(ne_ids, device_ne_id), narrow)
        cur = await self.conn.execute(
            "SELECT a.ne_id, CAST((a.first_seen - ?) / ? AS INTEGER) AS b, COUNT(*) "  # nosec B608
            f"FROM alarm a WHERE {where} AND a.first_seen >= ? AND a.first_seen < ? "
            "GROUP BY a.ne_id, b",
            (since, width, *args, since, until),
        )
        per: dict[int, list[int]] = {}
        for ne_id, bucket, n in await cur.fetchall():
            if ne_id is None or not 0 <= int(bucket) < buckets:
                continue
            per.setdefault(int(ne_id), [0] * buckets)[int(bucket)] += int(n)
        ranked = sorted(per.items(), key=lambda item: (-sum(item[1]), item[0]))
        shown, rest = ranked[:top], ranked[top:]
        names = await self._ne_names([ne for ne, _ in shown])
        lanes = [
            {"ne_id": ne, "device": names.get(ne, str(ne)), "total": sum(v), "counts": v}
            for ne, v in shown
        ]
        other = [sum(col) for col in zip(*(v for _, v in rest), strict=False)] if rest else []
        return {
            "from": since,
            "to": until,
            "bucket_s": width,
            "buckets": buckets,
            "lanes": lanes,
            # Every element not drawn, summed, so the chart's total is the window's total.
            "others": {"elements": len(rest), "counts": other or [0] * buckets},
        }

    async def activity_groups(
        self,
        *,
        since: float,
        until: float,
        ne_ids: frozenset[int] | None,
        device_ne_id: int | None = None,
        kinds: tuple[Kind, ...] = ("raise", "clear"),
        class_id: int | None = None,
        gap_s: float = DEFAULT_GAP_S,
        limit: int = 50,
        offset: int = 0,
        narrow: Narrow | None = None,
    ) -> dict[str, Any]:
        """Bursts of one trap on one element, newest first, paged — and how many there are."""
        where, args = narrowed(*self._timeline_scope(ne_ids, device_ne_id), narrow)
        cte, order = self._marks_cte(where, kinds, class_id)
        bound: list[Any] = []
        for _ in order:
            bound.extend(args)
            if class_id is not None:
                bound.append(class_id)
            bound.extend((since, until))
        # A new burst starts where the previous mark of the same (element, trap, kind) is more than
        # `gap_s` earlier — `LAG` over the partition — and a running SUM numbers the bursts.
        # `cte` is built from literals in `_marks_cte`; every value below is a bound parameter.
        grouped = (
            cte  # nosec B608
            + ", ordered AS (SELECT ne_id, class_id, kind, ts, CASE WHEN ts - LAG(ts) OVER "
            "(PARTITION BY ne_id, class_id, kind ORDER BY ts) <= ? THEN 0 ELSE 1 END AS fresh "
            "FROM marks), numbered AS (SELECT ne_id, class_id, kind, ts, SUM(fresh) OVER "
            "(PARTITION BY ne_id, class_id, kind ORDER BY ts ROWS UNBOUNDED PRECEDING) AS g "
            "FROM ordered), bursts AS (SELECT ne_id, class_id, kind, COUNT(*) AS n, "
            "MIN(ts) AS first, MAX(ts) AS last FROM numbered GROUP BY ne_id, class_id, kind, g) "
        )
        cur = await self.conn.execute(
            grouped + "SELECT COUNT(*), COALESCE(SUM(n), 0) FROM bursts",  # nosec B608
            (*bound, gap_s),
        )
        totals = await cur.fetchone()
        cur = await self.conn.execute(
            grouped  # nosec B608 - literals and placeholders only
            + "SELECT ne_id, class_id, kind, n, first, last FROM bursts "
            "ORDER BY last DESC, ne_id, class_id LIMIT ? OFFSET ?",
            (*bound, gap_s, limit, offset),
        )
        rows = [dict(r) for r in await cur.fetchall()]
        # Alarms raised BEFORE the window and re-reported in it: a repeating trap increments the
        # alarm rather than raising a new one, so a fault that keeps firing leaves no raise mark.
        # Counted, so an empty window can say "nothing new" instead of "nothing" (live pass).
        cur = await self.conn.execute(
            "SELECT COUNT(*) FROM alarm a WHERE "  # nosec B608 - `where` is `_timeline_scope`'s
            + where
            + (" AND a.class_id=?" if class_id is not None else "")
            + " AND a.last_seen >= ? AND a.last_seen < ? AND a.first_seen < ?",
            (*args, *((class_id,) if class_id is not None else ()), since, until, since),
        )
        repeated = await cur.fetchone()
        names = await self._ne_names([int(r["ne_id"]) for r in rows if r["ne_id"] is not None])
        classes = await self._class_names([int(r["class_id"]) for r in rows])
        for row in rows:
            row["device"] = names.get(row["ne_id"], str(row["ne_id"]))
            row.update(classes.get(int(row["class_id"]), {}))
        return {
            "from": since,
            "to": until,
            "gap_s": gap_s,
            "total": int(totals[0]) if totals else 0,
            "marks": int(totals[1]) if totals else 0,
            "offset": offset,
            "repeated": int(repeated[0]) if repeated else 0,
            "groups": rows,
        }

    async def activity_severity(
        self, *, since: float, until: float, buckets: int, ne_ids: frozenset[int] | None
    ) -> dict[str, Any]:
        """Raises per bucket **by severity band**, with `unplaced` a band of its own (item 4).

        The Overview's "what is happening" chart. Grouped in SQL by bucket, class and placed rank,
        then resolved per class with the census's precedence — the per-class declaration, a rule,
        what the trap carried or the appliance learned — so a band here means what it means on the
        severity card beside it. **An alarm nothing can place is counted under `unplaced`, never
        dropped**: on an estate whose devices send no severity word that band is most of the chart,
        and a chart that let it vanish would draw a quiet network.
        """
        width = max(1e-6, (until - since) / buckets)
        where, args = self._timeline_scope(ne_ids, None)
        cur = await self.conn.execute(
            "SELECT CAST((a.first_seen - ?) / ? AS INTEGER) AS b, c.id, c.oid, "  # nosec B608
            "a.severity_rank, COUNT(*) FROM alarm a JOIN alarm_class c ON c.id=a.class_id "
            f"WHERE {where} AND a.first_seen >= ? AND a.first_seen < ? "
            "GROUP BY b, c.id, a.severity_rank",
            (since, width, *args, since, until),
        )
        rows = list(await cur.fetchall())
        declared = await self.class_severity_declarations()
        catalogue = await self.catalogue()
        bands = [*BANDS, "unplaced"]
        series: dict[str, list[int]] = {band: [0] * buckets for band in bands}
        vendor = [0] * buckets
        for bucket, class_id, oid, placed_rank, n in rows:
            if not 0 <= int(bucket) < buckets:
                continue
            token = declared.get(int(class_id))
            rank = (
                known_oids.severity_rank(token)
                if token is not None
                else catalogue.resolve(str(oid)).severity_rank
            )
            if rank is None:
                rank = placed_rank
            if rank is None:
                series["unplaced"][int(bucket)] += int(n)
            elif int(rank) < len(BANDS):
                series[BANDS[int(rank)]][int(bucket)] += int(n)
            else:
                vendor[int(bucket)] += int(n)
        out: dict[str, Any] = {
            "from": since,
            "to": until,
            "bucket_s": width,
            "buckets": buckets,
            "series": series,
        }
        if any(vendor):
            # A vendor's own numbering, placed per NE on the alarm itself (F99). Only present when
            # something is in it, so the absence of the key is not read as a zero band.
            out["series"]["vendor"] = vendor
        return out

    async def _ne_names(self, ne_ids: list[int]) -> dict[int, str]:
        """The name an operator gave each element, falling back to its address."""
        if not ne_ids:
            return {}
        marks = ",".join("?" * len(set(ne_ids)))
        cur = await self.conn.execute(
            "SELECT n.id, COALESCE(l.label, n.ip) FROM ne n "  # nosec B608
            + self._label_join("l", "ne", "n.id")
            + f"WHERE n.id IN ({marks})",
            tuple(sorted(set(ne_ids))),
        )
        return {int(r[0]): str(r[1]) for r in await cur.fetchall()}

    async def _class_names(self, class_ids: list[int]) -> dict[int, dict[str, Any]]:
        """`{class_id: {class, class_oid, class_named}}` — the name if anything gives one.

        The catalogue's precedence (ADR #385): the per-class declaration, then a rule on the OID or
        a branch above it, then a standard trap's bundled name. `class_named` is False when none of
        those exists and the OID is all there is, which is what the list shows in that case.
        """
        if not class_ids:
            return {}
        marks = ",".join("?" * len(set(class_ids)))
        cur = await self.conn.execute(
            "SELECT c.id, c.oid, l.label FROM alarm_class c "  # nosec B608
            + self._label_join("l", "class", "c.id")
            + f"WHERE c.id IN ({marks})",
            tuple(sorted(set(class_ids))),
        )
        catalogue = await self.catalogue()
        out: dict[int, dict[str, Any]] = {}
        for cid, oid, label in await cur.fetchall():
            name = label or catalogue.resolve(str(oid)).name or known_oids.trap_name(str(oid))
            out[int(cid)] = {
                "class": name or str(oid),
                "class_oid": str(oid),
                "class_named": bool(name),
                "class_vendor": known_oids.vendor_of(str(oid)),
            }
        return out
