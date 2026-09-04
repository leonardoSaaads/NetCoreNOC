"""The viewer read surface: stats, graph, classes, situations, timeline, entities, state clears.

Nine handlers, seven of them scoped. Every scoped one resolves visibility through the **same**
`scope_for` the write perimeter uses (`ctx.scope_for`, one decision site), and every one that names
a resource denies through the not-found branch it already had, so "out of your scope" and "no such
thing" are one code path — same status, same body, same timing (DECISIONS #60).

The two `unscoped` routes here are `/api/classes` and `/api/state-clears`; both are keyed on a
*kind of trap* rather than on a network element, and `rbac.ROUTE_SCOPE` records the reason.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException

from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.crosscutting import auth, shaping
from netcorenoc.engine.correlate.learn import MIN_EDGE_N

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
            # cannot move a scoped viewer's numbers and become a volume oracle (F32).
            out: dict[str, Any] = dict(
                await store.stats()
                if scope.unrestricted
                else await store.scoped_stats(scope.ne_ids, scope.ips)
            )
            out["ingest_gaps"] = await store.list_ingest_gaps(20)
        out["open_ingest_gaps"] = engine.gap.snapshot()
        out["latency_p95_s"] = round(engine.latency_p95(), 4)
        out["queue_depth"] = engine.queue.qsize()
        out["warnings"] = all_warnings()
        if extra_stats is not None:
            out.update(extra_stats())
        return out

    @route.get("/api/graph")
    async def graph(principal: auth.Principal = Depends(security)) -> dict[str, Any]:
        scope = await scope_for(principal)
        async with store.lock:
            snapshot = await store.graph_snapshot(min_edge_n=MIN_EDGE_N)
        projected = shaping.project_graph(snapshot, scope)  # in-scope nodes; edges need both ends
        return shaping.shape(projected, principal.role)  # coarsen device IPs below editor

    @route.get("/api/classes", dependencies=guarded)
    async def classes() -> list[dict[str, Any]]:
        """The alarm-class catalogue: trap OIDs and their labels.

        Not scoped, and deliberately so — a class is a *kind* of trap, not a network element, and
        the table carries no NE reference. The count that *would* leak ("a device you cannot see
        just emitted a new trap type") is `stats.classes`, and that one is scoped.
        """
        async with store.lock:
            return await store.list_classes()

    @route.get("/api/situations")
    async def situations(
        principal: auth.Principal = Depends(security),
        status: Literal["new", "open", "resolved"] | None = None,
        limit: int = 100,
        q: str | None = None,
    ) -> list[dict[str, Any]]:
        """Situations with at least one in-scope member; counts are of visible members only.

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
        """
        scope = await scope_for(principal)
        needle = (q or "").strip()[:MAX_SEARCH_CHARS] or None
        async with store.lock:
            rows = await store.list_situations(
                status,
                min(max(limit, 1), 500),
                None if scope.unrestricted else scope.ne_ids,
                needle,
                # Derived from `FIELD_RULES["ip"]`, never restated: a role whose responses coarsen
                # an address may not confirm one by typing it (`shaping.sees_raw_addresses`).
                match_addresses=shaping.sees_raw_addresses(principal.role),
            )
            if scope.unrestricted:
                # **v0.16.0: shaped, which this route did not have to be before.** Until `0014` a
                # situation row carried an id, a status, two timestamps and two counts — not one
                # protected field, so `shape()` had nothing to do and was not called.
                # `derived_name` is built from device addresses, and `fields.py`'s rule is that an
                # endpoint returning a protected field passes its body through. The stream beside
                # this route always did; this route now does too.
                return shaping.shape(rows, principal.role)
            members = await store.situation_member_nes([int(r["id"]) for r in rows])
        out: list[dict[str, Any]] = []
        for row in rows:
            projected = shaping.project_situation_row(row, members.get(int(row["id"]), []), scope)
            if projected is not None:
                out.append(projected)
        return shaping.shape(out, principal.role)

    @route.get("/api/situations/{sid}")
    async def situation(sid: int, principal: auth.Principal = Depends(security)) -> dict[str, Any]:
        scope = await scope_for(principal)
        async with store.lock:
            detail = await store.situation_detail(sid)
            member_ne = await store.situation_member_ne(sid) if detail is not None else {}
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
        # v0.6.0: every link carries its explanation as a typed, *named* term list — the same
        # three numbers, from one source (`LinkScore.terms`) rather than three ad-hoc columns.
        # The columns stay for compatibility and remain byte-identical (DECISIONS #50).
        for link in detail.get("links", []):
            link["terms"] = [
                {"name": "temporal", "contribution": link["term_t"]},
                {"name": "class_affinity", "contribution": link["term_a"]},
                {"name": "entity_affinity", "contribution": link["term_e"]},
            ]
        # `None` when the configuration row is gone, never a default: a threshold the console
        # guessed would be worse than one it says it does not have.
        detail["threshold"] = float(config["threshold"]) if config is not None else None
        return shaping.shape(detail, principal.role)  # coarsen alarm device IPs below editor

    @route.get("/api/timeline")
    async def timeline(
        limit: int = 300, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """Recent raise/clear marks.

        **v0.7.1 (F35 + F38):** the scope filter lives in the query and is keyed on `ne_id`. v0.7.0
        truncated globally and then compared the *rendered* `device` string — `COALESCE(label, ip)`
        — against the scope's address and label sets, which made a non-unique display string an
        authorization key (DECISIONS #67, #72).
        """
        scope = await scope_for(principal)
        async with store.lock:
            marks = await store.timeline_marks(
                min(max(limit, 1), 1000), None if scope.unrestricted else scope.ne_ids
            )
        return {"marks": shaping.shape(marks, principal.role)}  # coarsen device IPs below editor

    # -- entity tree + varbind profiler (viewer+, inspectable) -------------------------

    @route.get("/api/entities")
    async def entities(principal: auth.Principal = Depends(security)) -> list[dict[str, Any]]:
        scope = await scope_for(principal)
        async with store.lock:
            nes = shaping.filter_rows(await store.list_ne(), scope, ne_key="id")
            out: list[dict[str, Any]] = []
            for ne in nes:
                ents = await store.entities_for_ne(int(ne["id"]))
                out.append({**ne, "entity_count": len(ents), "entities": ents})
        return shaping.shape(out, principal.role)  # coarsen NE IPs below editor

    @route.get("/api/entities/{ne_id}")
    async def entity_detail(
        ne_id: int, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        scope = await scope_for(principal)
        async with store.lock:
            ne = next((n for n in await store.list_ne() if int(n["id"]) == ne_id), None)
            entities_rows = await store.entities_for_ne(ne_id) if ne else []
            profiles = await store.varbind_profiles_for_ne(ne_id) if ne else []
        # An out-of-scope NE takes the SAME branch as a nonexistent one — same status, same body,
        # same timing. Existence is not disclosed (DECISIONS #60).
        if ne is None or not scope.allows_ne(ne_id):
            raise HTTPException(status_code=404, detail="no such NE")
        # Live profiler judgement (fresher than the flushed rows), fully broken down so the
        # operator can see why a varbind is (or is not) the entity discriminator.
        candidates = [
            {
                "varbind_oid": c.varbind_oid,
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
        }
        return shaping.shape(detail, principal.role)  # coarsen NE ip below editor

    @route.get("/api/state-clears", dependencies=guarded)
    async def state_clears() -> list[dict[str, Any]]:
        """Learned state fields (S9): which class, which varbind OID, and the raise/clear
        values — the state analogue of the entity/severity inspectability surface."""
        async with store.lock:
            return await store.list_state_clears()
