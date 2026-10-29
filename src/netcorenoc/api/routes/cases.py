"""NOC → Cases: the queue, a case, and the acts that move it (ADRs #459, #460).

A case is visible to whoever may read its situation: `cases.read`, and the same scope predicate
the situation's own routes use (`perimeter.situation_in_scope`), so a case outside the caller's
scope is the same 404 as one that does not exist. Working one — taking, editing the customer
update, setting it pending, resolving, releasing — is `cases.work`; setting it pending, resolving
and releasing belong to the case's owner (`cases.manage`, an admin's, acts on any), while the
customer update is anyone's who works cases, kept current as facts arrive (ADR #470). Every act
is one write transaction with one audit row under the actor's name, and an `internal` event in
the case feed.

**Built for agents as much as for people** (`docs/plans/noc-phase2.md`): every route has an
explicit operation id (the MCP tool name it will become), the list answers with the catalogue
an agent needs to fill a case in (statuses, priorities, causes, visibilities, targets), every case
says which acts the caller may take on it now (`actions`), times are accepted as ISO 8601, a
refusal's status says what kind it is (400 wrong, 403 not yours, 409 not now), and a ``version``
lets two workers edit without overwriting each other.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request

from netcorenoc.api import case_close
from netcorenoc.api.case_view import actor_of, case_view, fail, manages, reader_of
from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.api.models_cases import CaseFieldsIn, ReleaseIn, ResolveIn, StatusIn
from netcorenoc.api.noc_gate import require_lead
from netcorenoc.crosscutting import auth, shaping
from netcorenoc.engine.noc import case_ops, cases, feed, kpi
from netcorenoc.engine.noc.cases import CaseError

NO_SUCH = "no such case"
MAX_LIST = 200
#: The list's views, each a set of statuses (``None``: every status).
VIEWS: dict[str, frozenset[str] | None] = {
    "queue": frozenset({"new", "auto", "queue"}),
    "working": frozenset({"working", "validate"}),
    "pending": cases.PENDING,
    "live": cases.LIVE,
    "resolved": frozenset({"resolved", "closed"}),
    "all": None,
}


def catalogue() -> dict[str, Any]:
    """What an agent needs to fill a case in, from the module that enforces it."""
    return {
        "statuses": cases.STATUSES,
        "pending": sorted(cases.PENDING),
        "priorities": cases.PRIORITIES,
        "causes": cases.CAUSES,
        "visibilities": feed.VISIBILITIES,
        "fields": {
            name: {"label": label, "max": cap} for name, (label, cap) in cases.FIELD_LIMITS.items()
        },
        "targets": {
            "take_s": kpi.TAKE_TARGET_S,
            "document_s": kpi.DOCUMENT_TARGET_S,
            "update_cadence_s": kpi.UPDATE_CADENCE_S,
        },
    }


def current(case: dict[str, Any], version: int | None) -> None:
    """Refuse an edit made against a version someone else has since replaced."""
    if version is not None and version != case["version"]:
        raise HTTPException(
            status_code=409,
            detail=f"the case changed since you read it (now version {case['version']})",
        )


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the case routes on `app`."""
    store, security, scope_for = ctx.store, ctx.security, ctx.scope_for
    audit_row, write_txn = ctx.perimeter.audit_row, ctx.write_txn
    route = DeclaredRoutes(app)

    async def visible_case(sid: int, scope: shaping.Scope) -> dict[str, Any]:
        """The case, or the 404 that also answers "not in your scope"."""
        async with store.reading():
            case = await store.noc_case(sid)
        if case is None or not await ctx.perimeter.situation_in_scope(sid, scope):
            raise HTTPException(status_code=404, detail=NO_SUCH)
        return case

    async def fresh(sid: int) -> dict[str, Any]:
        """The case as the write transaction sees it — after anyone else's act."""
        case = await store.noc_case(sid)
        if case is None:
            raise HTTPException(status_code=404, detail=NO_SUCH)
        return case

    async def detail(
        sid: int, request: Request, principal: auth.Principal, scope: shaping.Scope
    ) -> dict[str, Any]:
        case = await visible_case(sid, scope)
        async with store.reading():
            cycles = await store.noc_case_cycles(sid)
            organizations = await store.noc_case_organizations(sid)
        return case_view(
            case,
            reader_of(request, principal),
            now=time.time(),
            cycles=cycles,
            organizations=organizations,
            whole=scope.unrestricted,
        )

    async def audited(
        request: Request, principal: auth.Principal, action: str, sid: int, **details: Any
    ) -> None:
        await audit_row(
            request,
            principal,
            action,
            "ok",
            object_type="noc_case",
            object_id=str(sid),
            details=details or None,
        )

    @route.get("/api/noc/cases", operation_id="noc_list_cases")
    async def list_cases(
        request: Request,
        view: str = "live",
        mine: bool = False,
        limit: int = 100,
        priority: str | None = None,
        tier: int | None = None,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """The cases in one view (queue, working, pending, live, resolved, all), most urgent first,
        each with its clocks; the counts per status; and the catalogue. ``priority`` narrows to
        some priorities (``critical,major``); ``tier`` to an agents' tier (1-4) or 0, the NOC
        team."""
        if view not in VIEWS:
            raise HTTPException(status_code=400, detail=f"view: one of {', '.join(VIEWS)}")
        priorities = None if not priority else frozenset(priority.split(","))
        if priorities is not None and not priorities <= set(cases.PRIORITIES):
            raise HTTPException(
                status_code=400, detail=f"priority: any of {', '.join(cases.PRIORITIES)}"
            )
        if tier is not None and not 0 <= tier <= 4:
            raise HTTPException(status_code=400, detail="tier: 1 to 4, or 0 for the NOC team")
        scope = await scope_for(principal)
        ne_ids = None if scope.unrestricted else scope.ne_ids
        async with store.reading():
            rows = await store.noc_cases(
                statuses=VIEWS[view],
                owner_ref=principal.ref if mine else None,
                ne_ids=ne_ids,
                limit=min(max(limit, 1), MAX_LIST),
                priorities=priorities,
                tier=tier,
            )
            counts = await store.noc_case_counts(ne_ids)
            cycles = {
                int(r["situation_id"]): await store.noc_case_cycles(int(r["situation_id"]))
                for r in rows
            }
        now, reader = time.time(), reader_of(request, principal)
        return {
            "now": now,
            "view": view,
            "cases": [
                case_view(
                    r,
                    reader,
                    now=now,
                    cycles=cycles[int(r["situation_id"])][-1:],
                    whole=scope.unrestricted,
                )
                for r in rows
            ],
            "counts": counts,
            "works": reader.works,
            **catalogue(),
        }

    @route.get("/api/noc/cases/kpis", operation_id="noc_case_kpis")
    async def case_kpis(
        request: Request, days: float = 7.0, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """The NOC's clocks over the last ``days``: take and documentation met/missed, medians,
        the overrun, per priority; and how many cases are in each status now. An admin's, a
        named lead's or an agent's (ADR #470)."""
        await require_lead(ctx, request, principal)
        span = min(max(days, 1 / 24), 90.0)
        scope = await scope_for(principal)
        ne_ids = None if scope.unrestricted else scope.ne_ids
        now = time.time()
        async with store.reading():
            cycles = await store.noc_case_cycles_since(now - span * 86400, ne_ids)
            counts = await store.noc_case_counts(ne_ids)
        return {
            "days": span,
            "now": now,
            **kpi.summary(cycles, now),
            "counts": counts,
            "priorities": cases.PRIORITIES,
        }

    @route.get("/api/noc/cases/{sid}", operation_id="noc_get_case")
    async def get_case(
        sid: int, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """One case: the customer update, the executive summary per organization, its clocks,
        every queue cycle it has been through, and what you may do to it. Its feed is `/feed`."""
        return await detail(sid, request, principal, await scope_for(principal))

    @route.post("/api/noc/cases/{sid}/take", operation_id="noc_take_case")
    async def take_case(
        sid: int, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """Take the case: it becomes yours and `working`; its queue cycle's take clock stops."""
        scope = await scope_for(principal)
        await visible_case(sid, scope)
        try:
            async with write_txn():
                await case_ops.take(store, await fresh(sid), actor_of(principal), time.time())
                await audited(request, principal, "noc.case.take", sid)
        except CaseError as exc:
            raise fail(exc) from exc
        return await detail(sid, request, principal, scope)

    @route.post("/api/noc/cases/{sid}/release", operation_id="noc_release_case")
    async def release_case(
        sid: int, body: ReleaseIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """Put your case back in the NOC queue, for someone else to take."""
        scope = await scope_for(principal)
        await visible_case(sid, scope)
        try:
            async with write_txn():
                await case_ops.release(
                    store,
                    await fresh(sid),
                    actor_of(principal),
                    time.time(),
                    admin=manages(request),
                    note=body.note,
                )
                await audited(request, principal, "noc.case.release", sid)
        except CaseError as exc:
            raise fail(exc) from exc
        return await detail(sid, request, principal, scope)

    @route.post("/api/noc/cases/{sid}", operation_id="noc_update_case")
    async def update_case(
        sid: int,
        body: CaseFieldsIn,
        request: Request,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """Edit the customer update: any of title, description, executive summary (and per
        organization), current analysis, NOC and customer next steps, next update, priority."""
        scope = await scope_for(principal)
        await visible_case(sid, scope)
        fields = body.model_dump(exclude_none=True, exclude={"summaries", "version"})
        summaries = body.summaries or {}
        async with store.reading():
            orgs = frozenset(int(o["id"]) for o in await store.noc_case_organizations(sid))
        try:
            async with write_txn():
                case = await fresh(sid)
                current(case, body.version)
                await case_ops.update(
                    store,
                    case,
                    actor_of(principal),
                    time.time(),
                    fields=fields,
                    summaries=summaries,
                    organizations=orgs,
                    admin=manages(request),
                )
                names = sorted({*fields, *(f"summary:{o}" for o in summaries)})
                await audited(request, principal, "noc.case.update", sid, fields=names)
        except CaseError as exc:
            raise fail(exc) from exc
        return await detail(sid, request, principal, scope)

    @route.post("/api/noc/cases/{sid}/status", operation_id="noc_set_case_pending")
    async def set_status(
        sid: int, body: StatusIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """Set your case pending — on the customer, a provider, a maintenance window, a dispatch,
        device access, connectivity… — until its next update, when it returns to the queue. With
        no ``next_update_at`` and none ahead, the handbook's cadence for its priority sets it."""
        scope = await scope_for(principal)
        await visible_case(sid, scope)
        actor, admin = actor_of(principal), manages(request)
        try:
            async with write_txn():
                case = await fresh(sid)
                current(case, body.version)
                now = time.time()
                when = body.next_update_at
                ahead = case["next_update_at"] is not None and float(case["next_update_at"]) > now
                if when is None and not ahead:
                    when = kpi.next_update_due(case, now)
                if when is not None:
                    case = await case_ops.update(
                        store,
                        case,
                        actor,
                        now,
                        fields={"next_update_at": when},
                        summaries={},
                        organizations=frozenset(),
                        admin=admin,
                    )
                await case_ops.pend(
                    store, case, actor, now, status=body.status, admin=admin, note=body.note
                )
                await audited(request, principal, "noc.case.status", sid, status=body.status)
        except CaseError as exc:
            raise fail(exc) from exc
        return await detail(sid, request, principal, scope)

    @route.post("/api/noc/cases/{sid}/resolve", operation_id="noc_resolve_case")
    async def resolve_case(
        sid: int, body: ResolveIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """Resolve your case with a cause and the solution. Closes its situation; clears its alarms
        only with ``alarms_verified_clear`` — you checked the device and they are not active."""
        scope = await scope_for(principal)
        await visible_case(sid, scope)
        try:
            async with write_txn():
                case = await fresh(sid)
                current(case, body.version)
                await case_ops.resolve(
                    store,
                    case,
                    actor_of(principal),
                    time.time(),
                    cause=body.cause,
                    solution=body.solution,
                    alarms_cleared=body.alarms_verified_clear,
                    admin=manages(request),
                )
                closed = await case_close.close_for_case(
                    ctx, request, principal, sid, scope, clear=body.alarms_verified_clear
                )
                await audited(
                    request,
                    principal,
                    "noc.case.resolve",
                    sid,
                    cause=body.cause,
                    alarms_verified_clear=body.alarms_verified_clear,
                )
        except CaseError as exc:
            raise fail(exc) from exc
        if closed["situation_closed"]:
            ctx.engine.forget_situation(sid)
        return {**await detail(sid, request, principal, scope), "closed": closed}
