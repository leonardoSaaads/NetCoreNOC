"""The viewer read surface: stats, graph, classes, situations, timeline, entities, state clears.

Nine handlers, seven of them scoped. Every scoped one resolves visibility through the **same**
`scope_for` the write perimeter uses (`ctx.scope_for`, one decision site), and every one that names
a resource denies through the not-found branch it already had, so "out of your scope" and "no such
thing" are one code path — same status, same body, same timing (DECISIONS #60).

The two `unscoped` routes here are `/api/classes` and `/api/state-clears`; both are keyed on a
*kind of trap* rather than on a network element, and `rbac.ROUTE_SCOPE` records the reason.
"""

from __future__ import annotations

import time
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException

from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.api.livestats import live_stats
from netcorenoc.api.mw_shape import situation_marker
from netcorenoc.crosscutting import auth, shaping
from netcorenoc.engine.correlate.learn import MIN_EDGE_N
from netcorenoc.engine.operate.engine import IDLE_CLOSE_S
from netcorenoc.ingest import known_oids

#: The range the bucketed timeline covers when the caller names none: two hours, which is the
#: window the health sampler already keeps and the one an operator reaches for first.
DEFAULT_TIMELINE_RANGE_S = 2 * 60 * 60.0

#: Buckets a caller may ask for. A chart a few hundred pixels wide cannot resolve more, and the
#: ceiling is what stops a caller turning one request into an unbounded number of GROUP BY rows.
MAX_TIMELINE_BUCKETS = 240

#: The longest needle `GET /api/situations?q=` will honour. Bounded rather than rejected, like every
#: other untrusted string on this API: a 4 KB query string would otherwise reach `LIKE` and be
#: scanned against every alarm of every listed situation. 100 characters is longer than any device
#: address, OID or operator name this console renders, so the bound cannot cut a real search short.
MAX_SEARCH_CHARS = 100


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the read routes on `app`."""
    store, engine, security, guarded = ctx.store, ctx.engine, ctx.security, ctx.guarded
    scope_for, all_warnings, extra_stats = ctx.scope_for, ctx.all_warnings, ctx.extra_stats
    route = DeclaredRoutes(app)

    # -- read endpoints (viewer+) ------------------------------------------------------

    @route.get("/api/stats")
    async def stats(principal: auth.Principal = Depends(security)) -> dict[str, Any]:
        scope = await scope_for(principal)
        async with store.lock:
            # Every enumerating counter is computed over the in-scope set, so out-of-scope activity
            # cannot move a scoped viewer's numbers and become a volume oracle (F32). The assembly
            # is shared with the `/api/events` stream (F115): this route and that one publish the
            # same object, and building it twice is how the severity census reached one of them.
            out: dict[str, Any] = await live_stats(store, engine, scope, all_warnings, extra_stats)
            # The two members the stream genuinely does not carry, kept at the call site so the
            # difference between the surfaces is visible where it is made.
            out["ingest_gaps"] = await store.list_ingest_gaps(20)
        out["open_ingest_gaps"] = engine.gap.snapshot()
        return out

    @route.get("/api/graph")
    async def graph(principal: auth.Principal = Depends(security)) -> dict[str, Any]:
        scope = await scope_for(principal)
        async with store.lock:
            snapshot = await store.graph_snapshot(min_edge_n=MIN_EDGE_N)
        projected = shaping.project_graph(snapshot, scope)  # in-scope nodes; edges need both ends
        return shaping.shape(projected, principal.role)  # coarsen device IPs below editor

    @route.get("/api/classes")
    async def classes(principal: auth.Principal = Depends(security)) -> list[dict[str, Any]]:
        """The alarm-class catalogue: trap OIDs, what an operator called them, and their severity.

        Still **not scoped**, and deliberately so — a class is a *kind* of trap, not a network
        element, and the table carries no NE reference. The count that *would* leak ("a device you
        cannot see just emitted a new trap type") is `stats.classes`, and that one is scoped.

        **It is shaped now, and it was not before.** That is the same sentence commit `8609962`
        wrote about `GET /api/situations`: the row carried an id, an OID, a name and a vendor, not
        one protected field — correctly unshaped, until it started carrying a declaration. A class
        label is text an operator typed, and an operator may type an address (F104). So the route
        takes the principal it never needed, which is why `dependencies=guarded` is gone: the
        `security` dependency it now names resolves the same identity that guard did.
        """
        async with store.lock:
            listed = await store.list_classes()
        return shaping.shape(listed, principal.role)  # coarsen an address inside a declaration

    @route.get("/api/situations")
    async def situations(
        principal: auth.Principal = Depends(security),
        status: Literal["new", "open", "resolved"] | None = None,
        limit: int = 100,
        q: str | None = None,
        ne_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Situations with at least one in-scope member; counts are of visible members only.

        **v0.22.0: `ne_id` narrows to situations with a member on that element**, in SQL, through
        the scoped branch — so an element outside the caller's scope answers the same empty list a
        nonexistent one does. The graph's element panel reads it.

        **v0.16.1: `q` searches, and it is a query filter** (`store._search_clause`). A parameter
        on the route that already lists situations rather than a `/api/search` of its own: the
        answer is a list of situations, shaped and scoped by the rules this handler already
        applies, and a second route would have had to restate every one of them. It matches the
        operator's name, the derived name, the device, the OID and the instance — and each of
        those **only where this principal would be shown it**, which is what stops a search box
        from becoming an oracle across either axis.

        Bounded at `MAX_SEARCH_CHARS` and never rejected, in the same spirit as every other
        untrusted string this API accepts. A `q` that is empty or whitespace is *no search*, not a
        search for nothing.

        **v0.16.0: the three states the console's three tabs render** (migration `0014`,
        DECISIONS #253, #254). `closed` and `merged` are gone as *statuses* — both are `resolved`,
        and `resolution` says which — so a client that still asks for one gets a 422 naming the
        three values rather than an empty list, which is the honest answer to a filter that no
        longer exists.

        `alarm_count` is the number this reader can actually see, with `redacted_count` naming how
        many they cannot — the same honest split as the detail view (DECISIONS #59). Reporting the
        global count here would leak out-of-scope volume across every listed situation at once.

        **v0.7.1 (F38):** the scope predicate is bound into the query, so `LIMIT` bounds the
        *filtered* set. v0.7.0 truncated globally and filtered afterwards, so a scoped viewer's own
        open incidents vanished from their list whenever a noisy neighbour they cannot see was
        busy — and the returned count varied with out-of-scope volume (DECISIONS #72).

        **v0.16.2: every row carries `stale`** (DECISIONS #274). True when this situation is live,
        nobody has touched it for `IDLE_CLOSE_S`, and one of its alarms is **still active** — the
        population the idle sweep used to resolve out of every live view while it was burning. It
        is **derived here and stored nowhere**, because staleness is a function of `now` and a
        column holding it would be a cached clock reading.

        The threshold is imported from the module that defines it and the predicate is
        `store.idle_active_situations`, the same expression the maintenance pass counts for its
        operator warning. A console that compared `updated_at` against a number of its own would
        be a second implementation of a server constant, which `lifecycle.js` already refuses to be
        for `m(c)`.

        **It discloses nothing.** The id set is unscoped, and it is used only to mark rows this
        caller has already been shown — never to add one.
        """
        scope = await scope_for(principal)
        needle = (q or "").strip()[:MAX_SEARCH_CHARS] or None
        stale_cutoff = time.time() - IDLE_CLOSE_S
        async with store.lock:
            wanted = None if scope.unrestricted else scope.ne_ids
            if ne_id is not None:
                wanted = frozenset({ne_id}) if scope.allows_ne(ne_id) else frozenset()
            rows = await store.list_situations(
                status,
                min(max(limit, 1), 500),
                wanted,
                needle,
                # Derived from `FIELD_RULES["ip"]`, never restated: a role whose responses coarsen
                # an address may not confirm one by typing it (`shaping.sees_raw_addresses`).
                match_addresses=shaping.sees_raw_addresses(principal.role),
            )
            rows = await store.with_idle_active(rows, stale_cutoff)
            # IV.3 on the situation list. **Read once, and free when no window is in force**: an
            # appliance with nothing planned gets one empty query and takes neither branch below,
            # so the member lookup this needs is paid only by an estate that actually has planned
            # work. A situation is marked when ANY of its member elements is under a window —
            # which is the honest reading, because the situation is the thing an operator opens
            # and one suppressed element is enough to make its alarm counts incomplete.
            markers = await store.window_markers(
                time.time(), None if scope.unrestricted else scope.ne_ids
            )
            situation_nes = (
                await store.situation_member_nes([int(r["id"]) for r in rows]) if markers else {}
            )
            if scope.unrestricted:
                # **v0.16.0: shaped, which this route did not have to be before.** Until `0014` a
                # situation row carried an id, a status, two timestamps and two counts — not one
                # protected field, so `shape()` had nothing to do and was not called.
                # `derived_name` is built from device addresses, and `fields.py`'s rule is that an
                # endpoint returning a protected field passes its body through. The stream beside
                # this route always did; this route now does too.
                return shaping.shape(
                    [
                        {**row, "maintenance": situation_marker(row, situation_nes, markers)}
                        for row in rows
                    ],
                    principal.role,
                )
            members = situation_nes or await store.situation_member_nes(
                [int(r["id"]) for r in rows]
            )
        out: list[dict[str, Any]] = []
        for row in rows:
            projected = shaping.project_situation_row(row, members.get(int(row["id"]), []), scope)
            if projected is not None:
                projected["maintenance"] = situation_marker(row, situation_nes, markers)
                out.append(projected)
        return shaping.shape(out, principal.role)

    @route.get("/api/situations/{sid}")
    async def situation(sid: int, principal: auth.Principal = Depends(security)) -> dict[str, Any]:
        scope = await scope_for(principal)
        async with store.lock:
            detail = await store.situation_detail(sid)
            member_ne = await store.situation_member_ne(sid) if detail is not None else {}
            markers = await store.window_markers(
                time.time(), None if scope.unrestricted else scope.ne_ids
            )
            # The threshold every link in THIS situation had to clear, read from the scorer
            # configuration the situation was decided under rather than from the active one
            # (F84, DECISIONS #247). Without it the console can show a score and not what it
            # cleared, which is a decomposition that cannot be checked.
            config = (
                await store.get_scorer_config(int(detail["scorer_config_id"]))
                if detail is not None and detail.get("scorer_config_id") is not None
                else None
            )
        if detail is not None:
            # Out-of-scope members are redacted to a count and their classes; a situation with no
            # visible member projects to None, which falls into the SAME not-found branch below —
            # so "not yours" and "does not exist" are one code path (DECISIONS #60).
            detail = shaping.project_situation_detail(detail, scope, member_ne_ids=member_ne)
        if detail is None:
            raise HTTPException(status_code=404, detail="no such situation")
        # **The named term list is built in the console, not on the wire** (v0.20.0, F145).
        #
        # v0.6.0 added `terms` here — `[{"name": "temporal", "contribution": …}, …]` — as the
        # typed source of the explanation, keeping `term_t`/`term_a`/`term_e` beside it for
        # compatibility (DECISIONS #50). Six releases later, **measured** on a 1 051-member
        # storm: this response is 1 843.9 KiB, of which `links` is 1 535.0 KiB, of which
        # **993 KiB is the same three floats written a second time** — 194 bytes per link,
        # 5 240 links, rebuilt in a Python loop on every read. A held card keeps it for as long
        # as the operator has the situation open, which is the memory growth the maintainer
        # reported and the reason the card is slow to arrive.
        #
        # Nothing is lost. `views/parts/why.js::termsOf` has always built the named list from
        # the three columns when `terms` is absent — that is the path every link now takes —
        # and the names live in that file already, because `TERM_LABEL` and `TERM_KEY` are
        # keyed on them. What left the wire is the restatement; what carries the decomposition
        # is the columns, which is what the database stores and what DECISIONS #50 promised
        # would stay. Principle 2 is unchanged and is asserted over the columns.
        # `None` when the configuration row is gone, never a default: a threshold the console
        # guessed would be worse than one it says it does not have.
        detail["threshold"] = float(config["threshold"]) if config is not None else None
        # IV.3 on the card an operator actually works from. Same marker, same rule: every role is
        # told that planned work is in force on one of these elements, and no role is told what it
        # is from here.
        detail["maintenance"] = next(
            (markers[ne] for ne in member_ne.values() if ne is not None and ne in markers), None
        )
        return shaping.shape(detail, principal.role)  # coarsen alarm device IPs below editor

    @route.get("/api/timeline")
    async def timeline(
        limit: int = 300,
        ne_id: int | None = None,
        since: float | None = None,
        until: float | None = None,
        buckets: int = 0,
        range_s: float | None = None,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """Recent raise/clear marks, optionally narrowed to one element and one window.

        **`buckets` switches the shape** (v0.20.0): with it, the answer is per-bucket raise and
        clear *counts* over the last `range_s` seconds rather than the marks themselves. Same
        route because it is the same question at a different resolution, and the same scope
        predicate decides what is counted.

        **v0.7.1 (F35 + F38):** the scope filter lives in the query and is keyed on `ne_id`. v0.7.0
        truncated globally and then compared the *rendered* `device` string — `COALESCE(label, ip)`
        — against the scope's address and label sets, which made a non-unique display string an
        authorization key (DECISIONS #67, #72).

        **v0.16.1: the two filters an operator asked for, and both are query filters.** `ne_id`
        names an element by the **same key the scope predicate uses** rather than by the `device`
        string the marks render — sending that string back would make v0.7.0's defect a feature,
        because two elements can share a label. `since` and `until` bound the window in SQL, so
        `limit` bounds the *filtered* set rather than a page that was truncated first.

        Neither can widen anything: both are `AND`ed with the scope, so a principal asking for an
        element outside their scope receives an empty answer through the predicate that hides it,
        which is the same non-answer they would get for an element that does not exist.
        """
        scope = await scope_for(principal)
        if buckets:
            # **The aggregate form** (v0.20.0, F144). The Overview's chart is about twenty-four
            # numbers and was being served as a thousand rows — 108.5 KiB to draw a histogram.
            # `buckets` asks for the counts instead, computed in SQL through the same scope
            # predicate the marks go through.
            #
            # No mark leaves the database here, so there is nothing for `shaping` to coarsen: a
            # count of raises in a five-minute bucket names no device and no address. The
            # principal's scope still decides *which alarms are counted*, in SQL.
            span = max(1.0, float(range_s or DEFAULT_TIMELINE_RANGE_S))
            wanted = min(max(buckets, 2), MAX_TIMELINE_BUCKETS)
            async with store.lock:
                return await store.timeline_buckets(
                    bucket_s=span / wanted,
                    buckets=wanted,
                    now=time.time(),
                    ne_ids=None if scope.unrestricted else scope.ne_ids,
                    device_ne_id=ne_id,
                )
        async with store.lock:
            marks = await store.timeline_marks(
                min(max(limit, 1), 1000),
                None if scope.unrestricted else scope.ne_ids,
                device_ne_id=ne_id,
                since=since,
                until=until,
            )
        return {"marks": shaping.shape(marks, principal.role)}  # coarsen device IPs below editor

    # -- entity tree + varbind profiler (viewer+, inspectable) -------------------------

    @route.get("/api/entities")
    async def entities(principal: auth.Principal = Depends(security)) -> list[dict[str, Any]]:
        """Every in-scope element, and **whether planned work is in force on it** (IV.3).

        The `maintenance` marker's *existence* ignores `visibility` entirely, which is prime
        directive 4 and the failure this whole feature would otherwise create: a host that goes
        quiet with no marker reads as a healthy host. So every authenticated role gets the marker;
        what it carries is a window id, a status and when it ends, and never a name, an owner, a
        description or a rule. Those are `visibility`'s, and they are served by a different query
        on a different route.

        Scoped in the WHERE clause, not here — a marker on an element this principal cannot see
        would answer *"does this exist?"*, which is the oracle the scope exists to close.
        """
        scope = await scope_for(principal)
        now = time.time()
        async with store.lock:
            nes = shaping.filter_rows(await store.list_ne(), scope, ne_key="id")
            markers = await store.window_markers(now, None if scope.unrestricted else scope.ne_ids)
            out: list[dict[str, Any]] = []
            for ne in nes:
                ents = await store.entities_for_ne(int(ne["id"]))
                out.append(
                    {
                        **ne,
                        "entity_count": len(ents),
                        "entities": ents,
                        "maintenance": markers.get(int(ne["id"])),
                    }
                )
        return shaping.shape(out, principal.role)  # coarsen NE IPs below editor

    @route.get("/api/entities/{ne_id}")
    async def entity_detail(
        ne_id: int, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        scope = await scope_for(principal)
        async with store.lock:
            ne = await store.get_ne(ne_id)
            entities_rows = await store.entities_for_ne(ne_id) if ne else []
            profiles = await store.varbind_profiles_for_ne(ne_id) if ne else []
            markers = await store.window_markers(
                time.time(), None if scope.unrestricted else scope.ne_ids
            )
        # An out-of-scope NE takes the SAME branch as a nonexistent one — same status, same body,
        # same timing. Existence is not disclosed (DECISIONS #60).
        if ne is None or not scope.allows_ne(ne_id):
            raise HTTPException(status_code=404, detail="no such NE")
        # Live profiler judgement (fresher than the flushed rows), fully broken down so the
        # operator can see why a varbind is (or is not) the entity discriminator.
        candidates = [
            {
                "varbind_oid": c.varbind_oid,
                # v0.24.0 (ADR #400): the vendor's name for it, from the built-in pack.
                "varbind_name": known_oids.varbind_name(c.varbind_oid),
                "r": round(c.r, 4),
                "x": round(c.x, 4),
                "d": round(c.d, 4),
                "score": round(c.score, 4),
                "n_obs": c.n_obs,
                "n_distinct": c.n_distinct,
                "meets_floor": c.meets_floor(),
            }
            for c in engine.profiler.candidates(ne_id)
        ]
        detail = {
            "ne": ne,
            "entities": entities_rows,
            "profiles": profiles,
            "candidates": candidates,
            # IV.3, as on the list: existence for every role, details for none of them.
            "maintenance": markers.get(ne_id),
        }
        return shaping.shape(detail, principal.role)  # coarsen NE ip below editor

    @route.get("/api/state-clears", dependencies=guarded)
    async def state_clears() -> list[dict[str, Any]]:
        """Learned state fields (S9): which class, which varbind OID, and the raise/clear
        values — the state analogue of the entity/severity inspectability surface."""
        async with store.lock:
            return await store.list_state_clears()
