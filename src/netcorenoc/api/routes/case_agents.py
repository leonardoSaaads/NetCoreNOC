"""Routing a case to an agent, and escalating it up the chain (ADR #467).

Both are case routes — `cases.work`, the case's scope, the same 404 — with a rule on top of who may:

* **a person** assigns a case nobody has taken, or their own; `cases.manage` assigns any. They
  escalate their own, or one routed to an agent;
* **an agent** delegates only to agents below it, and only a case that is its own — routed to it
  or taken by it; it escalates only its own (the handbook's "pass it to the next tier").

Assigning to nobody (`agent_id` null) hands the case to the NOC team. Each act is one write
transaction, one `noc.case.assign` or `noc.case.escalate` audit row, and an event in the feed.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request

from netcorenoc.api.case_view import actor_of, case_view, fail, manages, reader_of
from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.api.models_agents import AssignIn, EscalateIn
from netcorenoc.crosscutting import auth, shaping
from netcorenoc.engine.noc import agents, cases, routing
from netcorenoc.engine.noc.cases import CaseError

NO_SUCH = "no such case"


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the case-routing routes on `app`."""
    store, security, scope_for = ctx.store, ctx.security, ctx.scope_for
    audit_row, write_txn = ctx.perimeter.audit_row, ctx.write_txn
    route = DeclaredRoutes(app)

    async def visible_case(sid: int, scope: shaping.Scope) -> dict[str, Any]:
        async with store.reading():
            case = await store.noc_case(sid)
        if case is None or not await ctx.perimeter.situation_in_scope(sid, scope):
            raise HTTPException(status_code=404, detail=NO_SUCH)
        return case

    async def fresh(sid: int) -> dict[str, Any]:
        case = await store.noc_case(sid)
        if case is None:
            raise HTTPException(status_code=404, detail=NO_SUCH)
        return case

    async def answer(
        sid: int, request: Request, principal: auth.Principal, scope: shaping.Scope
    ) -> dict[str, Any]:
        case = await visible_case(sid, scope)
        async with store.reading():
            cycles = await store.noc_case_cycles(sid)
        return case_view(
            case,
            reader_of(request, principal),
            now=time.time(),
            cycles=cycles,
            whole=scope.unrestricted,
        )

    def owns(case: dict[str, Any], principal: auth.Principal, me: dict[str, Any] | None) -> bool:
        """The case is the caller's: taken by them, or — for an agent — routed to it."""
        if principal.ref is not None and case.get("owner_ref") == principal.ref:
            return True
        return me is not None and case.get("agent_id") == me["id"]

    async def caller_agent(principal: auth.Principal) -> dict[str, Any] | None:
        if not principal.is_token or principal.token_id is None:
            return None
        return await store.noc_agent_by_token(principal.token_id)

    @route.post("/api/noc/cases/{sid}/assign", operation_id="noc_assign_case")
    async def assign_case(
        sid: int, body: AssignIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """Route the case to an agent (`agent_id`), or to the NOC team (`null`). An agent
        delegates only to agents below it."""
        scope = await scope_for(principal)
        await visible_case(sid, scope)
        now = time.time()
        try:
            async with write_txn():
                case = await fresh(sid)
                me = await caller_agent(principal)
                everyone = {int(a["id"]): a for a in await store.noc_agents()}
                if str(case["status"]) not in cases.LIVE:
                    raise CaseError("a resolved case is not routed")
                if me is not None:
                    if not owns(case, principal, me):
                        raise CaseError("an agent delegates only its own cases", 403)
                    if body.agent_id not in agents.subtree(everyone, int(me["id"])):
                        raise CaseError("an agent delegates only to agents below it", 403)
                elif not (
                    manages(request)
                    or owns(case, principal, None)
                    or str(case["status"]) in cases.UNOWNED
                ):
                    raise CaseError("take the case first: only its owner may route it", 403)
                actor = actor_of(principal)
                if body.agent_id is None:
                    await routing.with_people(
                        store, case, actor, now, why=body.note or "assigned by hand"
                    )
                else:
                    target = everyone.get(body.agent_id)
                    if target is None:
                        raise CaseError("agent_id: no such agent", 400)
                    await routing.assign(store, case, target, actor, now, note=body.note)
                await audit_row(
                    request,
                    principal,
                    "noc.case.assign",
                    "ok",
                    object_type="noc_case",
                    object_id=str(sid),
                    details={"agent": body.agent_id},
                )
        except CaseError as exc:
            raise fail(exc) from exc
        return await answer(sid, request, principal, scope)

    @route.post("/api/noc/cases/{sid}/escalate", operation_id="noc_escalate_case")
    async def escalate_case(
        sid: int, body: EscalateIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """Send the case to the next tier now: its agent's manager, or — past the top of the
        agents — the NOC team. Say why: it is posted to the feed."""
        scope = await scope_for(principal)
        await visible_case(sid, scope)
        now = time.time()
        try:
            async with write_txn():
                case = await fresh(sid)
                me = await caller_agent(principal)
                if str(case["status"]) not in cases.LIVE:
                    raise CaseError("a resolved case does not escalate")
                mine = owns(case, principal, me)
                if me is not None and not mine:
                    raise CaseError("an agent escalates only its own cases", 403)
                if me is None and not (mine or manages(request) or case.get("agent_id")):
                    raise CaseError("take the case first: only its owner may escalate it", 403)
                if me is not None and case.get("agent_id") != me["id"]:
                    # Taken by the agent but routed elsewhere: it climbs from the agent itself.
                    case = {**case, "agent_id": me["id"]}
                everyone = {int(a["id"]): a for a in await store.noc_agents()}
                now_with = await routing.escalate(
                    store, case, everyone, actor_of(principal), now, why=body.reason
                )
                await audit_row(
                    request,
                    principal,
                    "noc.case.escalate",
                    "ok",
                    object_type="noc_case",
                    object_id=str(sid),
                    details={"to": now_with},
                )
        except CaseError as exc:
            raise fail(exc) from exc
        return await answer(sid, request, principal, scope)
