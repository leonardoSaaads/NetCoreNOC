"""Five independent read models: stats, the graph, the class catalogue, the timeline, quarantine.

Each method here reads its own tables and calls nothing else in this module — which is what made
the v0.16.3 split obvious once it was looked for. The situation listing, its search filter and one
situation's detail formed the only cluster in the old file, and they are
:mod:`netcorenoc.store.situation_reads` now. This is a plain :class:`StoreBase` mixin again: both
sibling-inheritance edges the old header documented belonged to that cluster and went with it.

Every scoped read here is a v0.7.1 finding: F38 (``LIMIT`` bounds the *filtered* set, never the
global one) and F35 (the scope filter is on ``ne_id`` in SQL — a display string is never an
authorization key). ``ne_ids=None`` runs the unmodified v0.7.0 SQL, so parity is by construction.
"""

from __future__ import annotations

import hashlib
from typing import Any

from netcorenoc.ingest import known_oids
from netcorenoc.store.base import StoreBase
from netcorenoc.store.situations import LIVE
from netcorenoc.store.types import MAX_SCOPE_PARAMS, class_display


class ReadModelsMixin(StoreBase):
    async def stats(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for name, sql in (
            ("devices", "SELECT COUNT(*) FROM device"),
            ("classes", "SELECT COUNT(*) FROM alarm_class"),
            ("active_alarms", "SELECT COUNT(*) FROM alarm WHERE status='active'"),
            # v0.16.0 (DECISIONS #254): the LIVE population, `new` and `open` alike. The
            # correlator creates `new`, so counting `open` alone would have reported zero on a
            # working appliance the moment this release shipped — and this number has always
            # meant "situations that have not left", which is what it still means.
            ("open_situations", f"SELECT COUNT(*) FROM situation WHERE {LIVE}"),  # nosec B608
            ("quarantined", "SELECT COUNT(*) FROM quarantine"),
        ):
            cur = await self.conn.execute(sql)
            row = await cur.fetchone()
            assert row is not None
            out[name] = int(row[0])
        return out

    async def graph_snapshot(self, min_edge_n: float) -> dict[str, Any]:
        cur = await self.conn.execute(
            "SELECT d.id, d.ip, d.vendor, l.label, "
            "(SELECT COUNT(*) FROM alarm a WHERE a.device_id=d.id AND a.status='active') "
            "AS active_alarms FROM device d "
            # v0.16.3: through the ADDRESS, which is what `device` and `ne` genuinely share — both
            # tables key on a UNIQUE `ip`. Joining `l.target_id=d.id` would work only while two
            # independent AUTOINCREMENT sequences happen to agree (DECISIONS #281).
            "LEFT JOIN ne n ON n.ip=d.ip "
            + self._label_join("l", "ne", "n.id", legacy_target="d.id")
        )
        nodes = [dict(r) for r in await cur.fetchall()]
        cur = await self.conn.execute(
            "SELECT a_id, b_id, weight, n FROM edge WHERE kind='device' AND n>=? AND weight>0",
            (min_edge_n,),
        )
        edges = [dict(r) for r in await cur.fetchall()]
        return {"nodes": nodes, "edges": edges}

    async def list_classes(self) -> list[dict[str, Any]]:
        """Every learned alarm class, with what an operator declared about it (v0.16.3).

        `name` and `vendor` are derived from the `oid` rather than selected beside it — `0016`
        dropped both columns as stored derivations (DECISIONS #280) — and `severity` is the
        operator's declaration for the class, the same row `situation_detail` reads.
        """
        cur = await self.conn.execute(
            "SELECT c.id, c.oid, l.label, s.label AS severity FROM alarm_class c "
            + self._label_join("l", "class", "c.id")
            + self._label_join("s", "severity", "c.id")
            + "ORDER BY c.id"
        )
        return [
            {
                **dict(r),
                "name": known_oids.trap_name(str(r["oid"])),
                "vendor": known_oids.vendor_of(str(r["oid"])),
                "severity_rank": (
                    known_oids.severity_rank(str(r["severity"]))
                    if r["severity"] is not None
                    else None
                ),
            }
            for r in await cur.fetchall()
        ]

    async def timeline_marks(
        self,
        limit: int,
        ne_ids: frozenset[int] | None = None,
        *,
        device_ne_id: int | None = None,
        since: float | None = None,
        until: float | None = None,
    ) -> list[dict[str, Any]]:
        """Recent alarms as raise/clear marks over time (for the UI timeline view).

        **v0.7.1 (F35 + F38): the scope filter is on `ne_id`, in SQL.** v0.7.0 truncated
        globally and then filtered in Python by comparing the rendered ``device`` string —
        ``COALESCE(label, ip)`` — against the scope's address and label sets. That was wrong twice
        over. Labels are **not unique**, so an editor who copied an in-scope NE's label onto an
        out-of-scope one inherited its alarm timing and classes (F35); and truncating first made a
        scoped principal's marks a function of traffic they cannot see (F38). **A display string is
        never an authorization key.**

        **v0.16.1 adds two narrowing filters, and neither is a display string either.**

        * `device_ne_id` is an **NE id**, the same key the scope predicate uses — deliberately not
          the rendered `device` string the console shows. Sending that string back would make the
          v0.7.0 defect a feature request: two elements can carry one label, so a filter on it
          would silently union them. It is `AND`ed with the scope, so it can only ever *narrow*
          what the principal was already allowed to see; a scoped principal naming an NE outside
          their scope gets an empty answer through the same predicate that hides it.
        * `since` / `until` bound the window, in SQL, so `LIMIT` bounds the **filtered** set. A row
          survives if either of its marks falls in the range; the mark expansion below then emits
          only the marks that do, because a cleared alarm raised before the window is one mark
          inside it and not two.

        `ne_id` now **does** reach the mark, which is the one intentional shape change here: the
        console needs an identifier to filter by that is not a display string, and an NE id is
        already public — `/api/entities` has served it to viewers since v0.5.0.
        """
        # v0.16.3: the class name is composed by `types.class_display` after the fetch, not by a
        # `COALESCE(cl.label, c.name, c.oid)` in SQL. The middle term is now a call rather than a
        # column (`0016`), and the precedence had been written out twice — here and in
        # `list_state_clears` — which is one copy too many for a rule (DECISIONS #280).
        select = (
            "SELECT a.first_seen, a.cleared_at, a.ne_id, COALESCE(dl.label, d.ip) AS device, "
            "cl.label AS class_label, c.oid AS class_oid FROM alarm a "
            "JOIN device d ON d.id=a.device_id JOIN alarm_class c ON c.id=a.class_id "
            + self._label_join("dl", "ne", "a.ne_id", legacy_target="d.id")
            + self._label_join("cl", "class", "c.id")
        )
        narrow: list[str] = []
        narrow_args: list[Any] = []
        if device_ne_id is not None:
            narrow.append("a.ne_id=?")
            narrow_args.append(device_ne_id)
        if since is not None:
            narrow.append("(a.first_seen >= ? OR a.cleared_at >= ?)")
            narrow_args.extend((since, since))
        if until is not None:
            narrow.append("(a.first_seen <= ? OR a.cleared_at <= ?)")
            narrow_args.extend((until, until))
        extra = "".join(f"AND {clause} " for clause in narrow)
        rows: list[Any]
        if ne_ids is None:
            # `WHERE 1=1` only when something narrows, so the unfiltered call stays the v0.7.0
            # statement it has been since the finding that produced it.
            head = f"{select}WHERE 1=1 {extra}" if extra else select
            cur = await self.conn.execute(
                f"{head}ORDER BY a.last_seen DESC LIMIT ?",  # nosec B608 - literal + bound values
                (*narrow_args, limit),
            )
            rows = list(await cur.fetchall())
        elif not ne_ids:
            rows = []
        elif len(ne_ids) > MAX_SCOPE_PARAMS:
            # See MAX_SCOPE_PARAMS: filter here rather than truncate the bound id list. The
            # narrowing clauses stay in the query — only the scope set is too large to bind.
            head = f"{select}WHERE 1=1 {extra}" if extra else select
            cur = await self.conn.execute(  # nosec B608 - literal + bound values
                f"{head}ORDER BY a.last_seen DESC",  # nosec B608
                tuple(narrow_args),
            )
            rows = [r for r in await cur.fetchall() if r["ne_id"] in ne_ids][:limit]
        else:
            marks_sql = ",".join("?" * len(ne_ids))
            cur = await self.conn.execute(
                f"{select}WHERE a.ne_id IN ({marks_sql}) {extra}"  # nosec B608 - placeholders only
                "ORDER BY a.last_seen DESC LIMIT ?",
                (*sorted(ne_ids), *narrow_args, limit),
            )
            rows = list(await cur.fetchall())
        marks: list[dict[str, Any]] = []
        for r in rows:
            for when, kind in ((r["first_seen"], "raise"), (r["cleared_at"], "clear")):
                if when is None:
                    continue
                if (since is not None and when < since) or (until is not None and when > until):
                    continue
                marks.append(
                    {
                        "ts": when,
                        "ne_id": r["ne_id"],
                        "device": r["device"],
                        "class": class_display(r["class_label"], str(r["class_oid"])),
                        "kind": kind,
                    }
                )
        marks.sort(key=lambda m: m["ts"])
        return marks

    async def list_quarantine(self, limit: int) -> list[dict[str, Any]]:
        """Quarantine metadata only — never the raw payload (F4)."""
        cur = await self.conn.execute(
            "SELECT id, source, reason, sha256, length, first8, sanitized, raw, received_at "
            "FROM quarantine ORDER BY received_at DESC LIMIT ?",
            (limit,),
        )
        out: list[dict[str, Any]] = []
        for r in await cur.fetchall():
            row = dict(r)
            raw = row.pop("raw") or b""
            # Fallback for rows written before the F4 columns existed (v0.1.0 upgrades).
            row["sha256"] = row["sha256"] or hashlib.sha256(raw).hexdigest()
            row["length"] = row["length"] if row["length"] is not None else len(raw)
            row["first8"] = row["first8"] or raw[:8].hex()
            out.append(row)
        return out
