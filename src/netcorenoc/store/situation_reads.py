"""The situation read model: the listing, the search filter, and one situation's detail.

**Split out of `read_models.py` in v0.16.3, and the seam is the import graph rather than the line
count.** That module held one cohesive cluster and five independent leaves: `stats`,
`graph_snapshot`, `list_classes`, `timeline_marks` and `list_quarantine` call nothing else in the
file, while the four members below call each other and nothing else calls them.

The two sibling-inheritance edges `read_models.py` documented in its own header existed **only for
this cluster**, and they came here with it:

* :class:`GovernanceMixin`, because :meth:`list_situations`'s over-cap branch calls
  :meth:`situation_member_nes` to filter in Python rather than truncating a bound id list
  (DECISIONS #88);
* :class:`SituationEventMixin`, because :meth:`situation_detail` serves a situation's gesture
  history and a stub that resolved instead of the real method would be a silent empty list rather
  than a loud failure (v0.16.0).

So the move leaves `read_models.py` a plain :class:`StoreBase` mixin again, which is the check that
this is a seam and not a size accident: a split that had to carry both edges to both halves would
have been the wrong cut.

Every scoped read here is a v0.7.1 finding: F38 (``LIMIT`` bounds the *filtered* set, never the
global one) and F35 (the scope filter is on ``ne_id`` in SQL — a display string is never an
authorization key). ``ne_ids=None`` runs the unmodified v0.7.0 SQL, so parity is by construction.
"""

from __future__ import annotations

from typing import Any

from netcorenoc.crosscutting import shaping
from netcorenoc.ingest import known_oids
from netcorenoc.store.governance import GovernanceMixin
from netcorenoc.store.situation_events import SituationEventMixin
from netcorenoc.store.types import MAX_SCOPE_PARAMS


class SituationReadsMixin(GovernanceMixin, SituationEventMixin):
    @staticmethod
    def _project_class(row: dict[str, Any]) -> dict[str, Any]:
        """Fill an alarm row's class fields from the `oid` they are all derived from (v0.16.3).

        Three of the four are a function of `class_oid` and nothing else, so `0008`'s rule keeps
        them out of the schema and puts them here:

        * ``class_name`` — the standard-trap name, or None for a vendor trap. Was a column until
          `0016`; it held exactly this value for 48 of 48 classes (DECISIONS #280).
        * ``class_vendor`` — the enterprise arc's vendor, which resolves for 46 of those 48 and
          appeared on no screen an operator reads. It is **not** put into the name chain: a vendor
          is not a name (DECISIONS #282).
        * ``declared_severity`` / ``declared_severity_rank`` — the operator's declaration for this
          alarm class, and the rank derived from it. The **learned** ``severity`` and
          ``severity_rank`` beside them are untouched: precedence is a read-time decision and the
          appliance's own judgement is never overwritten (directive 4, DECISIONS #284).
        """
        oid = str(row["class_oid"])
        declared = row.pop("class_severity_label", None)
        return {
            **row,
            "class_name": known_oids.trap_name(oid),
            "class_vendor": known_oids.vendor_of(oid),
            "declared_severity": declared,
            "declared_severity_rank": (
                known_oids.severity_rank(declared) if declared is not None else None
            ),
        }

    async def _attach_severity_scale(self, alarms: list[dict[str, Any]]) -> None:
        """Give each alarm the **whole rank set of its NE's severity field** (v0.16.3, F99).

        `severity.py::_candidate_ranks` returns an `int`-kind candidate whose rank is the varbind's
        raw integer, bounded in count (8 distinct values) and not in magnitude. A vendor numbering
        severity 10/20/30 writes ranks 10, 20 and 30, all three of which fall off the end of the
        four rendered bands and arrive at the console as one identical pill — the ordering the
        appliance validated against observed lifetimes, discarded at the last step.

        The console places such a rank by **order within the set**, not by its magnitude, because
        order is the only thing `confirm_ordinality` established. That needs the set, and the set
        is exactly the distinct ranks recorded for that NE: one confirmed severity field per NE,
        one rank per observed value. It is not derivable from the rows on screen — a pill whose
        band moved when a situation gained a member would be a worse defect than the one being
        repaired.

        A vocabulary-ranked field needs none of this and gets it anyway: `[0, 1, 2]` places on
        itself, so the value costs one bounded query and no special case.
        """
        ne_ids = {int(a["ne_id"]) for a in alarms if a.get("ne_id") is not None}
        if not ne_ids:
            return
        marks = ",".join("?" * len(ne_ids))
        cur = await self.conn.execute(
            "SELECT DISTINCT ne_id, severity_rank FROM alarm "  # nosec B608 - placeholders only
            f"WHERE ne_id IN ({marks}) AND severity_rank IS NOT NULL",  # nosec B608
            tuple(sorted(ne_ids)),
        )
        scale: dict[int, list[int]] = {}
        for row in await cur.fetchall():
            scale.setdefault(int(row["ne_id"]), []).append(int(row["severity_rank"]))
        for alarm in alarms:
            ne = alarm.get("ne_id")
            alarm["severity_ranks"] = sorted(scale.get(int(ne), [])) if ne is not None else []

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
        the predicate.

        **v0.16.3 brings declared text under that same rule, and it was not under it before.** The
        reasoning used to be that an operator name and a device label *"are free text a person
        typed, `shape` passes them through for every role"*. The first half is still true and the
        second is not: `shape` coarsens the addresses **inside** them now (F104), because an editor
        may type one. A `LIKE` against the stored column would return the octet the render just
        hid. So the declared columns join the predicate only when the requester may see raw
        addresses **or** the needle contains none — which leaves finding a situation by the name an
        operator gave it, the case the v0.16.1 search exists for, exactly as it was.

        `LIKE` with an explicit `ESCAPE`, and the caller's `%` and `_` escaped rather than
        stripped: a search for `10.1.2_4` must look for that string and not for any character
        there. `LOWER` is SQLite's, so folding is ASCII-only — an accented operator name matches
        only its own case, which is stated here rather than discovered.
        """
        if not query:
            return "", []
        escaped = query.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        needle = f"%{escaped}%"
        member_columns = ["LOWER(COALESCE(a3.instance, ''))", "LOWER(c3.oid)"]
        head_columns: list[str] = []
        # **The declared columns are gated exactly as `d3.ip` is, and that is new** (v0.16.3,
        # DECISIONS #287).
        #
        # A declaration an editor typed is coarsened for a viewer now, because it may contain an
        # address the same response hides. Matching the STORED text would hand that address
        # straight back: the viewer types `10.1.2.77`, the `LIKE` finds the raw column, and a
        # situation comes back — the shaping oracle `sees_raw_addresses` refuses one field away,
        # arriving through a column nobody thought of as an address. So a role that may not see
        # raw addresses may not search declared text **for an address**. Searching it by name,
        # which is the whole of what the v0.16.1 search exists for, is untouched: the gate is on
        # what the needle contains, not on who is asking.
        if addresses or not shaping.contains_address(query):
            member_columns.append("LOWER(COALESCE(cl3.label, ''))")
            member_columns.append("LOWER(COALESCE(dl3.label, ''))")
            if self._has_lifecycle:
                head_columns.append("LOWER(COALESCE(s.operator_name, ''))")
        # **The derived class name is a call now, not a column** (`0016`, DECISIONS #280), so it
        # cannot be a `LIKE` — and dropping it would have quietly narrowed the search this
        # release's predecessor built. `trap_name` is a lookup over eight bundled OIDs, so the
        # needle is resolved against that table **before** the query runs and the matching OIDs
        # are searched for directly. The capability is identical and the derivation stays single.
        standard = [
            oid for oid, name in known_oids.STANDARD_TRAPS.items() if query.lower() in name.lower()
        ]
        if addresses:
            member_columns.append("LOWER(d3.ip)")
            if self._has_lifecycle:
                head_columns.append("LOWER(COALESCE(s.derived_name, ''))")
        like = " OR ".join(f"{column} LIKE ? ESCAPE '\\'" for column in member_columns)
        if standard:
            like += f" OR c3.oid IN ({','.join('?' * len(standard))})"
        heads = "".join(f"{column} LIKE ? ESCAPE '\\' OR " for column in head_columns)
        scope = f"AND a3.ne_id IN ({','.join('?' * len(scope_ids))}) " if scope_ids else ""
        args: list[Any] = [needle] * len(head_columns)
        args.extend(scope_ids or [])
        args.extend([needle] * len(member_columns))
        args.extend(standard)
        return (
            f" AND ({heads}EXISTS (SELECT 1 FROM situation_alarm sa3 "  # nosec B608
            "JOIN alarm a3 ON a3.id=sa3.alarm_id "
            "JOIN alarm_class c3 ON c3.id=a3.class_id "
            "JOIN device d3 ON d3.id=a3.device_id "
            # v0.16.3: the equipment label is `kind='ne'`, reached through the alarm's own `ne_id`
            # rather than through `device.id` (DECISIONS #281).
            + self._label_join("dl3", "ne", "a3.ne_id", legacy_target="d3.id")
            + self._label_join("cl3", "class", "c3.id")
            + f"WHERE sa3.situation_id=s.id {scope}AND ({like})))",  # nosec B608 - placeholders
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
        # `_lifecycle_columns` is one of two literals chosen by the schema probe; no value from
        # outside this class reaches it, and every user value below is a bound parameter.
        head = (
            f"SELECT s.id, s.status, {self._lifecycle_columns}"  # nosec B608 - see above
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
            # nosec B608 - three fixed literals from `_label_join`, chosen by a schema probe;
            # the only bound value is the situation id.
            "SELECT a.id, a.instance, a.status, a.is_flapping, a.count, a.first_seen, "  # nosec B608
            "a.last_seen, a.severity, a.severity_rank, a.ne_id, d.ip AS device_ip, d.vendor AS "
            "device_vendor, dl.label AS device_label, c.oid AS class_oid, "
            "c.id AS class_id, cl.label AS class_label, sl.label AS class_severity_label "
            "FROM situation_alarm sa JOIN alarm a ON a.id=sa.alarm_id "
            "JOIN device d ON d.id=a.device_id JOIN alarm_class c ON c.id=a.class_id "
            # v0.16.3: the equipment label is keyed on `ne`, and the join reaches it through the
            # alarm's OWN `ne_id` rather than through `device.id` — which equals it on every
            # database anyone has, and is a coincidence of insertion order rather than a
            # constraint (DECISIONS #281). The declared severity is per alarm class, at the
            # qualifier that means "the whole class" (#283).
            + self._label_join("dl", "ne", "a.ne_id", legacy_target="d.id")
            + self._label_join("cl", "class", "c.id")
            + self._label_join("sl", "severity", "c.id")
            + "WHERE sa.situation_id=? ORDER BY a.first_seen",
            (situation_id,),
        )
        alarms = [self._project_class(dict(r)) for r in await cur.fetchall()]
        await self._attach_severity_scale(alarms)
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
