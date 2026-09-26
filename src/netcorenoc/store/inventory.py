"""The estate as an inventory: one row per element, with what an operator needs (v0.23.0, #395).

`GET /api/entities` answered with one query PER ELEMENT and carried the learned sub-entities of each
and nothing about its state, which is why the Entities screen could only print addresses. This
builds the whole table in a fixed number of grouped reads, whatever the estate's size:

* each element's organization, vendor, first and last trap;
* its active alarms, by severity band — resolved with the census's precedence, like every count;
* how many components (ports, ONUs, slots) the appliance learned beneath it;
* how many live situations it is in;
* the totals the screen leads with.

Scoped in the WHERE clause (F35): an element outside the caller's scope is not a row, and the totals
count only rows the caller may see.
"""

from __future__ import annotations

import time
from collections import Counter
from typing import Any

from netcorenoc.ingest import known_oids
from netcorenoc.store.active import ALL_BANDS, ActiveMixin

#: An element that has sent nothing for this long is counted as silent.
SILENT_S = 24 * 60 * 60.0


def _in(column: str, ids: frozenset[int] | None) -> tuple[str, tuple[int, ...]]:
    """`column IN (...)` for a scope, `1=1` for none, and `0=1` for an empty one."""
    if ids is None:
        return "1=1", ()
    if not ids:
        return "0=1", ()
    return f"{column} IN ({','.join('?' * len(ids))})", tuple(sorted(ids))


class InventoryMixin(ActiveMixin):
    async def inventory(
        self, ne_ids: frozenset[int] | None, now: float | None = None
    ) -> dict[str, Any]:
        """Every in-scope element with its state, and the estate's totals."""
        at = time.time() if now is None else now
        where, args = _in("n.id", ne_ids)
        cur = await self.conn.execute(
            "SELECT n.id, n.ip, n.vendor, n.first_seen, n.last_seen, n.organization_id, "  # nosec B608
            "o.name AS organization, l.label FROM ne n "
            "LEFT JOIN organization o ON o.id=n.organization_id "
            + self._label_join("l", "ne", "n.id")
            + f"WHERE {where} ORDER BY n.id",
            args,
        )
        rows = {int(r["id"]): dict(r) for r in await cur.fetchall()}
        a_where, a_args = _in("a.ne_id", ne_ids)
        band_of = await self._band_of()
        bands: dict[int, Counter[str]] = {}
        vendors: dict[int, Counter[str]] = {}
        cur = await self.conn.execute(
            "SELECT a.ne_id, c.id, c.oid, a.severity_rank, COUNT(*) FROM alarm a "  # nosec B608
            f"JOIN alarm_class c ON c.id=a.class_id WHERE {a_where} AND a.status='active' "
            "GROUP BY a.ne_id, c.id, a.severity_rank",
            a_args,
        )
        for ne_id, cid, oid, rank, n in await cur.fetchall():
            if ne_id is None:
                continue
            bands.setdefault(int(ne_id), Counter())[band_of(int(cid), str(oid), rank)] += int(n)
            vendor = known_oids.vendor_of(str(oid))
            if vendor:
                vendors.setdefault(int(ne_id), Counter())[vendor] += int(n)
        e_where, e_args = _in("ne_id", ne_ids)
        cur = await self.conn.execute(
            f"SELECT ne_id, COUNT(*) FROM entity WHERE {e_where} AND level > 0 "  # nosec B608
            "GROUP BY ne_id",
            e_args,
        )
        components = {int(r[0]): int(r[1]) for r in await cur.fetchall()}
        cur = await self.conn.execute(
            "SELECT a.ne_id, COUNT(DISTINCT sa.situation_id) FROM situation_alarm sa "  # nosec B608
            "JOIN alarm a ON a.id=sa.alarm_id JOIN situation s ON s.id=sa.situation_id "
            f"WHERE {a_where} AND s.status IN ('new', 'open') GROUP BY a.ne_id",
            a_args,
        )
        situations = {int(r[0]): int(r[1]) for r in await cur.fetchall() if r[0] is not None}
        markers = await self.window_markers(at, ne_ids)  # type: ignore[attr-defined]
        names = dict(await self._ne_names(list(rows)))
        out = []
        for ne_id, row in rows.items():
            per = bands.get(ne_id, Counter())
            learned_vendor = vendors.get(ne_id)
            out.append(
                {
                    "ne_id": ne_id,
                    "ip": row["ip"],
                    "label": row["label"],
                    "device": names.get(ne_id, row["ip"]),
                    "vendor": row["vendor"]
                    or (learned_vendor.most_common(1)[0][0] if learned_vendor else None),
                    "organization_id": row["organization_id"],
                    "organization": row["organization"],
                    "first_seen": row["first_seen"],
                    "last_seen": row["last_seen"],
                    "silent": row["last_seen"] is None or at - float(row["last_seen"]) > SILENT_S,
                    "active": sum(per.values()),
                    "bands": {band: per.get(band, 0) for band in ALL_BANDS},
                    "components": components.get(ne_id, 0),
                    "situations": situations.get(ne_id, 0),
                    "maintenance": markers.get(ne_id),
                }
            )
        return {
            "elements": out,
            "totals": {
                "elements": len(out),
                "alarming": sum(1 for r in out if r["active"]),
                "critical": sum(1 for r in out if r["bands"]["critical"]),
                "silent": sum(1 for r in out if r["silent"]),
                "maintenance": sum(1 for r in out if r["maintenance"]),
                "components": sum(r["components"] for r in out),
            },
            "silent_after_s": SILENT_S,
        }

    async def components(self, ne_id: int, limit: int = 200) -> dict[str, Any]:
        """One element's learned components, the busiest first, each with its active alarms."""
        cur = await self.conn.execute(
            "SELECT e.id, e.key, e.key_source, e.level, e.confidence, e.last_seen, "
            "COUNT(a.id) AS active FROM entity e "
            "LEFT JOIN alarm a ON a.entity_id=e.id AND a.status='active' "
            "WHERE e.ne_id=? AND e.level > 0 GROUP BY e.id "
            "ORDER BY active DESC, e.last_seen DESC, e.id LIMIT ?",
            (ne_id, limit),
        )
        rows = [dict(r) for r in await cur.fetchall()]
        cur = await self.conn.execute(
            "SELECT COUNT(*) FROM entity WHERE ne_id=? AND level > 0", (ne_id,)
        )
        total = await cur.fetchone()
        kinds: Counter[str] = Counter()
        for row in rows:
            row["kind"] = _kind(str(row["key"]))
            kinds[row["kind"]] += 1
        return {
            "ne_id": ne_id,
            "total": int(total[0]) if total else 0,
            "kinds": dict(kinds.most_common()),
            "components": rows,
        }


def _kind(key: str) -> str:
    """`onu-165` -> `onu`, `port-1/1` -> `port`, `fpc-0` -> `fpc`: the word before the number."""
    head = key.split("-", 1)[0] if "-" in key else key.rstrip("0123456789/.:")
    return head.lower() or "component"
