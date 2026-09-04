"""The read models the API serves: stats, listings, detail, graph, timeline, quarantine.

Inherits :class:`GovernanceMixin` because :meth:`list_situations`'s over-cap branch calls
:meth:`situation_member_nes` to filter in Python rather than truncating a bound id list. That is
the second of the two sibling-inheritance edges in this package (DECISIONS #88).

**v0.16.0 adds a second edge here**, to :class:`SituationEventMixin`, for the same reason and with
the same discipline: :meth:`situation_detail` serves a situation's gesture history, and a stub that
resolved instead of the real method would be a silent empty list rather than a loud failure.

Every scoped read here is a v0.7.1 finding: F38 (``LIMIT`` bounds the *filtered* set, never the
global one) and F35 (the scope filter is on ``ne_id`` in SQL — a display string is never an
authorization key). ``ne_ids=None`` runs the unmodified v0.7.0 SQL, so parity is by construction.
"""

from __future__ import annotations

import hashlib
from typing import Any

from netcorenoc.store.governance import GovernanceMixin
from netcorenoc.store.situation_events import SituationEventMixin
from netcorenoc.store.situations import LIVE
from netcorenoc.store.types import MAX_SCOPE_PARAMS


class ReadModelsMixin(GovernanceMixin, SituationEventMixin):
    @property
    def _lifecycle_columns(self) -> str:
        """The v0.16.0 situation columns, or nothing on a schema that predates them.

        A **literal chosen by the schema probe**, never by a caller: the string is one of two
        constants and no value from outside this class reaches it. It exists because
        `tests/test_upgrade.py` drives the current store against a migration directory frozen at an
        older version — the property that makes "the migration changes behaviour and the code does
        not" checkable — and a listing that named `resolution` unconditionally would raise there.

        `situation_detail` needs no such guard: it is `SELECT *`, which is exactly the shape that
        adapts to whichever columns the schema has.
        """
        return "s.resolution, s.derived_name, s.operator_name, " if self._has_lifecycle else ""

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

    def _search_clause(
        self, query: str | None, *, addresses: bool, scope_ids: list[int] | None
    ) -> tuple[str, list[Any]]:
        """The v0.16.1 search, **as a query filter**, or `("", [])` when nothing was asked.

        `docs/plans/v0.16.1-visualisation.md` §5: the console's box filtered on
        `` `#${s.id} ${s.status}` `` — id and status only — so an operator who had just named a
        situation *"fibre cut, Ridgeway ring"* could not find it by that name.

        **Two rules govern every clause below, and both are older than this release.**

        *Scope is a query filter.* The alarm-side match carries the **same** `ne_id IN (…)`
        predicate the listing does, so a situation with one in-scope member and one out-of-scope
        member — which a scoped viewer legitimately receives, redacted — cannot be *found* by the
        address of the member they may not see. Without that clause the search would be an
        existence oracle over exactly the population scoping exists to hide: F35 and F38 arriving
        through a text box (DECISIONS #67, #72).

        *A field is matched only where the requester would be shown it.* `addresses` comes from
        `shaping.sees_raw_addresses`, which reads `FIELD_RULES["ip"]`, so below editor the raw
        address and the server-derived name — both coarsened on the way out — are simply not in
        the predicate. An operator name and a device **label** always are: they are free text a
        person typed, `shape` passes them through for every role, and finding a situation by the
        name an operator just gave it is the case this release exists for.

        `LIKE` with an explicit `ESCAPE`, and the caller's `%` and `_` escaped rather than
        stripped: a search for `10.1.2_4` must look for that string and not for any character
        there. `LOWER` is SQLite's, so folding is ASCII-only — an accented operator name matches
        only its own case, which is stated here rather than discovered.
        """
        if not query:
            return "", []
        escaped = query.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        needle = f"%{escaped}%"
        member_columns = [
            "LOWER(COALESCE(a3.instance, ''))",
            "LOWER(c3.oid)",
            "LOWER(COALESCE(cl3.label, c3.name, ''))",
            "LOWER(COALESCE(dl3.label, ''))",
        ]
        head_columns = ["LOWER(COALESCE(s.operator_name, ''))"] if self._has_lifecycle else []
        if addresses:
            member_columns.append("LOWER(d3.ip)")
            if self._has_lifecycle:
                head_columns.append("LOWER(COALESCE(s.derived_name, ''))")
        like = " OR ".join(f"{column} LIKE ? ESCAPE '\\'" for column in member_columns)
        heads = "".join(f"{column} LIKE ? ESCAPE '\\' OR " for column in head_columns)
        scope = f"AND a3.ne_id IN ({','.join('?' * len(scope_ids))}) " if scope_ids else ""
        args: list[Any] = [needle] * len(head_columns)
        args.extend(scope_ids or [])
        args.extend([needle] * len(member_columns))
        return (
            f" AND ({heads}EXISTS (SELECT 1 FROM situation_alarm sa3 "  # nosec B608
            "JOIN alarm a3 ON a3.id=sa3.alarm_id "
            "JOIN alarm_class c3 ON c3.id=a3.class_id "
            "JOIN device d3 ON d3.id=a3.device_id "
            "LEFT JOIN label dl3 ON dl3.kind='device' AND dl3.target_id=d3.id "
            "LEFT JOIN label cl3 ON cl3.kind='class' AND cl3.target_id=c3.id "
            f"WHERE sa3.situation_id=s.id {scope}AND ({like})))",  # nosec B608 - placeholders only
            args,
        )

    async def list_situations(
        self,
        status: str | None,
        limit: int,
        ne_ids: frozenset[int] | None = None,
        query: str | None = None,
        *,
        match_addresses: bool = True,
    ) -> list[dict[str, Any]]:
        """Recent situations, newest first, optionally narrowed by a **query-side** text search.

        **v0.7.1 (F38): `LIMIT` bounds the *filtered* set, not the global one.** v0.7.0 truncated
        over the global ordering and let the caller filter afterwards, so a scoped principal's own
        open incidents disappeared from their list whenever a noisy neighbour they cannot see was
        busy — and the returned count varied with out-of-scope volume, which is the aggregate
        oracle F32 claims is closed. **The same rule governs `query`**: it narrows *before* the
        `LIMIT`, so a search can never answer "nothing" because the page that would have matched
        was truncated first.

        `ne_ids=None` **with no query** runs the **unmodified v0.7.0 SQL**, so parity for the
        common case is by construction rather than by inspection. `ne_ids` empty means "exactly
        nothing" and is answered without a query. See :data:`MAX_SCOPE_PARAMS` for the bound on the
        scoped branch and :meth:`_search_clause` for what the search may match.
        """
        where, args = ("WHERE s.status=?", [status]) if status else ("", [])
        head = (
            "SELECT s.id, s.status, "
            f"{self._lifecycle_columns}"
            "s.created_at, s.updated_at, s.root_alarm_id, "
            "COUNT(sa.alarm_id) AS alarm_count FROM situation s "
            "LEFT JOIN situation_alarm sa ON sa.situation_id=s.id "
        )
        tail = "GROUP BY s.id ORDER BY s.updated_at DESC LIMIT ?"
        if ne_ids is None:
            search, search_args = self._search_clause(
                query, addresses=match_addresses, scope_ids=None
            )
            # `WHERE 1=1` only where a search exists and a status filter does not, so the
            # no-search call is byte-identical to v0.7.0's statement.
            opener = where or ("WHERE 1=1" if search else "")
            cur = await self.conn.execute(
                # nosec B608 - `where`, `opener` and `search` are fixed literals built from
                # constants in this method; every value is a bound parameter
                f"{head}{opener}{search} {tail}",  # nosec B608
                (*args, *search_args, limit),
            )
            return [dict(r) for r in await cur.fetchall()]
        if not ne_ids:
            return []
        if len(ne_ids) > MAX_SCOPE_PARAMS:
            # Too many ids to bind. Fetch unbounded and filter here rather than truncating the id
            # list, which would silently answer a different question than the one asked. **The
            # search travels with it**: dropping `query` here would make a very large scope the one
            # shape in which the text filter stopped being a query filter.
            rows = await self.list_situations(
                status, -1, None, query, match_addresses=match_addresses
            )
            members = await self.situation_member_nes([int(r["id"]) for r in rows])
            keep = [r for r in rows if any(n in ne_ids for n in members.get(int(r["id"]), []))]
            return keep[:limit]
        bound = sorted(ne_ids)
        marks = ",".join("?" * len(bound))
        search, search_args = self._search_clause(query, addresses=match_addresses, scope_ids=bound)
        scoped = f"{where} AND " if where else "WHERE "
        cur = await self.conn.execute(
            # A situation is listed when **at least one** member is in scope — the same predicate
            # `project_situation_detail` uses, so the list and the detail can never disagree.
            # nosec B608 - `scoped` is a fixed literal; `marks` is only "?" placeholders
            f"{head}{scoped}EXISTS (SELECT 1 FROM situation_alarm sa2 "  # nosec B608
            "JOIN alarm a2 ON a2.id=sa2.alarm_id "
            f"WHERE sa2.situation_id=s.id AND a2.ne_id IN ({marks})){search} {tail}",  # nosec B608
            (*args, *bound, *search_args, limit),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def situation_detail(self, situation_id: int) -> dict[str, Any] | None:
        cur = await self.conn.execute("SELECT * FROM situation WHERE id=?", (situation_id,))
        head = await cur.fetchone()
        if head is None:
            return None
        cur = await self.conn.execute(
            "SELECT a.id, a.instance, a.status, a.is_flapping, a.count, a.first_seen, "
            "a.last_seen, a.severity, a.severity_rank, d.ip AS device_ip, d.vendor AS "
            "device_vendor, dl.label AS device_label, c.oid AS class_oid, c.name AS "
            "class_name, cl.label AS class_label "
            "FROM situation_alarm sa JOIN alarm a ON a.id=sa.alarm_id "
            "JOIN device d ON d.id=a.device_id JOIN alarm_class c ON c.id=a.class_id "
            "LEFT JOIN label dl ON dl.kind='device' AND dl.target_id=d.id "
            "LEFT JOIN label cl ON cl.kind='class' AND cl.target_id=c.id "
            "WHERE sa.situation_id=? ORDER BY a.first_seen",
            (situation_id,),
        )
        alarms = [dict(r) for r in await cur.fetchall()]
        cur = await self.conn.execute(
            "SELECT alarm_a, alarm_b, score, term_t, term_a, term_e FROM link "
            "WHERE situation_id=? ORDER BY id",
            (situation_id,),
        )
        links = [dict(r) for r in await cur.fetchall()]
        # v0.16.0: links whose two endpoints are **not both current members** are filtered out.
        # A `link` records what the correlator computed and an operator move does not delete one —
        # the arithmetic is still true — but a link to an alarm that has left is not a statement
        # about *this* bag, and rendering it would name a member the card does not list. Before this
        # release the two sets could not disagree, because only the correlator moved rows, so this
        # filter changes no existing response.
        member_ids = {int(alarm["id"]) for alarm in alarms}
        links = [
            link
            for link in links
            if int(link["alarm_a"]) in member_ids and int(link["alarm_b"]) in member_ids
        ]
        return {
            **dict(head),
            "alarms": alarms,
            "links": links,
            # What happened to this situation, and who did it. Four columns, deliberately — see
            # `situation_events`: the row carries member digests and a peer situation id, and a
            # scoped reader must not learn either from a history panel.
            "events": await self.situation_events(situation_id),
        }

    async def graph_snapshot(self, min_edge_n: float) -> dict[str, Any]:
        cur = await self.conn.execute(
            "SELECT d.id, d.ip, d.vendor, l.label, "
            "(SELECT COUNT(*) FROM alarm a WHERE a.device_id=d.id AND a.status='active') "
            "AS active_alarms FROM device d "
            "LEFT JOIN label l ON l.kind='device' AND l.target_id=d.id"
        )
        nodes = [dict(r) for r in await cur.fetchall()]
        cur = await self.conn.execute(
            "SELECT a_id, b_id, weight, n FROM edge WHERE kind='device' AND n>=? AND weight>0",
            (min_edge_n,),
        )
        edges = [dict(r) for r in await cur.fetchall()]
        return {"nodes": nodes, "edges": edges}

    async def list_classes(self) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT c.id, c.oid, c.vendor, c.name, l.label FROM alarm_class c "
            "LEFT JOIN label l ON l.kind='class' AND l.target_id=c.id ORDER BY c.id"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def timeline_marks(
        self, limit: int, ne_ids: frozenset[int] | None = None
    ) -> list[dict[str, Any]]:
        """Recent alarms as raise/clear marks over time (for the UI timeline view).

        **v0.7.1 (F35 + F38): the scope filter is on `ne_id`, in SQL.** v0.7.0 truncated
        globally and then filtered in Python by comparing the rendered ``device`` string —
        ``COALESCE(label, ip)`` — against the scope's address and label sets. That was wrong twice
        over. Labels are **not unique**, so an editor who copied an in-scope NE's label onto an
        out-of-scope one inherited its alarm timing and classes (F35); and truncating first made a
        scoped principal's marks a function of traffic they cannot see (F38). **A display string is
        never an authorization key.**

        `ne_id` is selected for the filter only and never reaches a mark, so the rendered response
        is byte-identical to v0.7.0. `ne_ids=None` runs the unmodified v0.7.0 SQL.
        """
        select = (
            "SELECT a.first_seen, a.cleared_at, a.ne_id, COALESCE(dl.label, d.ip) AS device, "
            "COALESCE(cl.label, c.name, c.oid) AS class FROM alarm a "
            "JOIN device d ON d.id=a.device_id JOIN alarm_class c ON c.id=a.class_id "
            "LEFT JOIN label dl ON dl.kind='device' AND dl.target_id=d.id "
            "LEFT JOIN label cl ON cl.kind='class' AND cl.target_id=c.id "
        )
        rows: list[Any]
        if ne_ids is None:
            cur = await self.conn.execute(
                f"{select}ORDER BY a.last_seen DESC LIMIT ?",  # nosec B608 - literal + bound values
                (limit,),
            )
            rows = list(await cur.fetchall())
        elif not ne_ids:
            rows = []
        elif len(ne_ids) > MAX_SCOPE_PARAMS:
            # See MAX_SCOPE_PARAMS: filter here rather than truncate the bound id list.
            cur = await self.conn.execute(f"{select}ORDER BY a.last_seen DESC")  # nosec B608
            rows = [r for r in await cur.fetchall() if r["ne_id"] in ne_ids][:limit]
        else:
            marks_sql = ",".join("?" * len(ne_ids))
            cur = await self.conn.execute(
                f"{select}WHERE a.ne_id IN ({marks_sql}) ORDER BY a.last_seen DESC LIMIT ?",  # nosec B608 - placeholders only
                (*sorted(ne_ids), limit),
            )
            rows = list(await cur.fetchall())
        marks: list[dict[str, Any]] = []
        for r in rows:
            marks.append(
                {"ts": r["first_seen"], "device": r["device"], "class": r["class"], "kind": "raise"}
            )
            if r["cleared_at"] is not None:
                marks.append(
                    {
                        "ts": r["cleared_at"],
                        "device": r["device"],
                        "class": r["class"],
                        "kind": "clear",
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
