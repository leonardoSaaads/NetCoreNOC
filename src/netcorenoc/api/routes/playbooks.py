"""NOC → Routines (formerly Playbooks): the troubleshooting trees, and how each has run (ADR #463).

**ADR #492: a routine is a customer's and a tier's.** It may be for one customer (it runs only on
that customer's cases, and is chosen before a routine for every customer) and has a tier: the
least tier of agent that may see it, run it and write one like it. A person who reads routines is a
lead (below); an agent reads only those at or below its own tier, and a routine above it answers
404 as one that does not exist. **An agent runs a routine on demand** (`…/{id}/run`): its read-only
checks run on the case's element and the conclusion it reaches is handed back — never applied —
so a provider's troubleshooting is one call instead of a session of commands typed one by one.

**Reading, writing and switching one on are a lead's** (ADRs #465, #470): `playbooks.read` or
`playbooks.write` and, on top, `noc_gate.is_lead` — an admin, an editor an admin named a lead, or
an agent (the grant that lets a token use credentials, which is what a playbook does). Switching
one off is a kill switch, so anyone with `playbooks.write` may. **It is active as soon as it is
stored** (the owner's choice), and that is safe because of what a playbook can say: read-only
commands only, checked when it is stored and again when it runs (`engine/noc/commands.py`), four
fixed tests on what came back, and three ways to end.

Every write is a `noc.playbook.*` audit row; every run is a `noc.playbook.run` row and a trace.
"""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request

from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.api.models_cases import PlaybookEnabledIn, PlaybookIn, RoutineRunIn
from netcorenoc.api.noc_gate import is_lead, require_lead
from netcorenoc.crosscutting import auth
from netcorenoc.engine.noc import cases, executor, playbook, runbook
from netcorenoc.engine.noc.case_ops import Actor
from netcorenoc.engine.noc.playbook import PlaybookError

NO_SUCH = "no such routine"
#: ADR #492: the cases a routine is running on because somebody asked — one at a time per case,
#: and at most :data:`MAX_ON_DEMAND` at once, so a burst of asks never becomes a burst of sessions
#: on the customers' devices (each run is bounded by `runbook.ON_DEMAND_S`).
_ON_DEMAND: set[int] = set()
MAX_ON_DEMAND = 8
NOT_AUTHOR = (
    "a playbook acts with the credentials agents may use: it is written, or switched on, by an "
    "agent the NOC hired, an admin, or an editor an admin named a NOC lead"
)


def _view(row: dict[str, Any], *, runs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    document = json.loads(row["document"])
    out = {
        "id": row["id"],
        "name": row["name"],
        "description": document.get("description", ""),
        "match": document["match"],
        "timeout_s": document.get("timeout_s", 300),
        "steps": len(document["steps"]),
        # What a reader counts (ADR #492): the checks a routine runs, not the ends it may reach.
        "checks": sum(1 for step in document["steps"].values() if "do" in step),
        "version": row["version"],
        "enabled": row["enabled"],
        "customer_id": row.get("organization_id"),
        "customer": row.get("customer"),
        "tier": row.get("tier", 1),
        "source_case": row["source_case"],
        "created_at": row["created_at"],
        "created_by": row["created_by"],
        "updated_at": row["updated_at"],
        "runs": row.get("runs"),
        "last_run_at": row.get("last_run_at"),
    }
    if runs is not None:
        out["document"] = document
        out["recent_runs"] = [
            {
                **{
                    k: r[k]
                    for k in ("id", "version", "situation_id", "started_at", "ended_at", "outcome")
                },
                "started_by": r.get("started_by"),
                "trace": json.loads(r["trace"]),
            }
            for r in runs
        ]
    return out


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the playbook routes on `app`."""
    store, security = ctx.store, ctx.security
    audit_row, write_txn = ctx.perimeter.audit_row, ctx.write_txn
    route = DeclaredRoutes(app)

    async def require_author(request: Request, principal: auth.Principal) -> None:
        if not await is_lead(ctx, request, principal):
            raise HTTPException(status_code=403, detail=NOT_AUTHOR)

    def checked(document: Any) -> dict[str, Any]:
        try:
            return playbook.validate(document)
        except PlaybookError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    async def ceiling(principal: auth.Principal) -> tuple[int, dict[str, Any] | None]:
        """The highest tier the caller may see, and the agent it is (ADR #492): a hired agent its
        own tier, any other token holding the agent grant the least one, a person who passed the
        lead gate every tier."""
        if not principal.is_token:
            return 4, None
        agent = None
        if principal.token_id is not None:
            async with store.reading():
                agent = await store.noc_agent_by_token(principal.token_id)
        return (int(agent["tier"]), agent) if agent else (1, None)

    async def owner_or_400(body: PlaybookIn, principal: auth.Principal) -> None:
        """The customer must be one, and an agent writes only at or below its own tier."""
        if body.customer_id is not None:
            async with store.reading():
                customer = await store.noc_customer(body.customer_id)
            if customer is None:
                raise HTTPException(status_code=400, detail="customer_id: no such customer")
        if not (await ctx.scope_for(principal)).allows_org(body.customer_id):
            # ADR #499: a scoped author writes for its own customers, never a shared routine.
            raise HTTPException(status_code=400, detail="customer_id: not a customer you serve")
        top, _agent = await ceiling(principal)
        if body.tier > top:
            raise HTTPException(
                status_code=403,
                detail=f"a tier-{top} agent writes routines of tier {top} or below",
            )

    def reaches(row: dict[str, Any], scope: Any) -> bool:
        """ADR #499: a customer's routine for those who serve it; a shared one for everyone."""
        return row["organization_id"] is None or scope.allows_org(row["organization_id"])

    async def visible(playbook_id: int, principal: auth.Principal) -> dict[str, Any]:
        top, _agent = await ceiling(principal)
        scope = await ctx.scope_for(principal)
        async with store.reading():
            row = await store.noc_playbook(playbook_id)
        if row is None or int(row["tier"]) > top or not reaches(row, scope):
            raise HTTPException(status_code=404, detail=NO_SUCH)
        return row

    async def case_facts(sid: int, principal: auth.Principal) -> dict[str, Any]:
        """The case's facts, or the 404 an unknown or out-of-scope case answers alike."""
        scope = await ctx.scope_for(principal)
        if not await ctx.perimeter.situation_in_scope(sid, scope):
            raise HTTPException(status_code=404, detail="no such case")
        async with store.reading():
            case = await store.noc_case(sid)
            facts = await store.noc_situation_facts(sid) if case else {}
        if case is None:
            raise HTTPException(status_code=404, detail="no such case")
        return {"case": case, **facts}

    @route.get("/api/noc/playbooks", operation_id="noc_list_playbooks")
    async def list_playbooks(
        request: Request,
        customer_id: int | None = None,
        situation_id: int | None = None,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """Every routine the caller may see: its customer, tier, match, state and runs. With
        `customer_id`, that customer's and every customer's; with `situation_id`, only the enabled
        ones that apply to that case — the question an agent asks before it acts."""
        await require_lead(ctx, request, principal)
        top, _agent = await ceiling(principal)
        facts = await case_facts(situation_id, principal) if situation_id is not None else None
        scope = await ctx.scope_for(principal)
        async with store.reading():
            rows = [
                r
                for r in await store.noc_playbooks()
                if int(r["tier"]) <= top and reaches(r, scope)
            ]
            customers = await store.noc_customers(None if scope.unrestricted else scope.orgs)
        if customer_id is not None:
            rows = [r for r in rows if r["organization_id"] in (None, customer_id)]
        if facts is not None:
            rows = [r for r in rows if r["enabled"] and runbook.applies(r, facts)]
        return {
            "playbooks": [_view(r) for r in rows],
            # ADR #492: whom a routine may be for, and the tiers it may have.
            "customers": [{"id": c["id"], "name": c["name"]} for c in customers],
            "tiers": [t for t in (1, 2, 3, 4) if t <= top],
            "actions": list(playbook.ACTIONS),
            "ends": list(playbook.ENDS),
            # What a `resolve` leaf's cause and a `pending` leaf's status may be, with their labels.
            "causes": cases.CAUSES,
            "pending": {s: cases.STATUSES[s] for s in sorted(cases.PENDING)},
            # Whether SSH steps can run here at all (the optional `noc` extra, ADR #462).
            "ssh_available": executor.available(),
        }

    @route.post("/api/noc/playbooks", operation_id="noc_create_playbook")
    async def create_playbook(
        body: PlaybookIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """Store a playbook — active at once. Read-only commands only; refused with the path to
        the first thing wrong."""
        await require_author(request, principal)
        document = checked(body.document)
        await owner_or_400(body, principal)
        text = playbook.canonical(document)
        async with write_txn():
            if body.source_case is not None and await store.noc_case(body.source_case) is None:
                raise HTTPException(status_code=400, detail="source_case: no such case")
            playbook_id = await store.noc_create_playbook(
                document["name"].strip(),
                text,
                body.source_case,
                time.time(),
                principal.actor,
                organization_id=body.customer_id,
                tier=body.tier,
            )
            await audit_row(
                request,
                principal,
                "noc.playbook.create",
                "ok",
                object_type="noc_playbook",
                object_id=str(playbook_id),
                details={
                    "source_case": body.source_case,
                    "customer": body.customer_id,
                    "tier": body.tier,
                },
            )
            row = await store.noc_playbook(playbook_id)
        assert row is not None
        return _view(row, runs=[])

    @route.get("/api/noc/playbooks/{playbook_id}", operation_id="noc_get_playbook")
    async def get_playbook(
        playbook_id: int, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """One routine, its whole tree, and its last twenty runs with their traces."""
        await require_lead(ctx, request, principal)
        row = await visible(playbook_id, principal)
        scope = await ctx.scope_for(principal)
        async with store.reading():
            runs = await store.noc_runs(playbook_id=playbook_id)
        if not scope.unrestricted:  # a shared routine's runs on another customer's cases
            runs = [
                r for r in runs if await ctx.perimeter.situation_in_scope(r["situation_id"], scope)
            ]
        return _view(row, runs=runs)

    @route.post("/api/noc/playbooks/{playbook_id}", operation_id="noc_update_playbook")
    async def update_playbook(
        playbook_id: int,
        body: PlaybookIn,
        request: Request,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """Replace a playbook's tree: a new version, validated as a new one is."""
        await require_author(request, principal)
        document = checked(body.document)
        await owner_or_400(body, principal)
        await visible(playbook_id, principal)
        async with write_txn():
            if await store.noc_playbook(playbook_id) is None:
                raise HTTPException(status_code=404, detail=NO_SUCH)
            await store.noc_update_playbook(
                playbook_id,
                document["name"].strip(),
                playbook.canonical(document),
                time.time(),
                principal.actor,
                organization_id=body.customer_id,
                tier=body.tier,
            )
            await audit_row(
                request,
                principal,
                "noc.playbook.update",
                "ok",
                object_type="noc_playbook",
                object_id=str(playbook_id),
            )
            row = await store.noc_playbook(playbook_id)
        assert row is not None
        return _view(row, runs=[])

    @route.post("/api/noc/playbooks/{playbook_id}/enabled", operation_id="noc_enable_playbook")
    async def enable_playbook(
        playbook_id: int,
        body: PlaybookEnabledIn,
        request: Request,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """Switch a routine on or off. Off takes effect for every case not already running it."""
        if body.enabled:
            await require_author(request, principal)
        await visible(playbook_id, principal)
        async with write_txn():
            if await store.noc_playbook(playbook_id) is None:
                raise HTTPException(status_code=404, detail=NO_SUCH)
            await store.noc_enable_playbook(playbook_id, body.enabled, time.time(), principal.actor)
            await audit_row(
                request,
                principal,
                "noc.playbook.update",
                "ok",
                object_type="noc_playbook",
                object_id=str(playbook_id),
                details={"enabled": body.enabled},
            )
            row = await store.noc_playbook(playbook_id)
        assert row is not None
        return _view(row)

    @route.post("/api/noc/playbooks/{playbook_id}/run", operation_id="noc_run_playbook")
    async def run_playbook(
        playbook_id: int,
        body: RoutineRunIn,
        request: Request,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """Run a routine on a case now and report what it found and where it ends (ADR #492).

        Each check is the routine's own — read-only, with the vault's credentials agents may use —
        and is a line on the case's feed and an audit row under the caller. The conclusion (resolve,
        wait, or the NOC queue) is **advice**: nothing is applied to the case. One run per case at a
        time; a routine above the caller's tier, or another customer's, is refused.
        """
        await require_lead(ctx, request, principal)
        row = await visible(playbook_id, principal)
        facts = await case_facts(body.situation_id, principal)
        if facts["case"]["status"] in ("resolved", "closed"):
            raise HTTPException(status_code=409, detail="that case is over")
        if not row["enabled"]:
            raise HTTPException(status_code=409, detail="that routine is switched off")
        if not runbook.applies(row, facts):
            raise HTTPException(
                status_code=409,
                detail="that routine does not apply to this case: it is for another customer or "
                "matches other alarms",
            )
        _top, agent = await ceiling(principal)
        invoker = (
            Actor(str(principal.ref), str(agent["name"]), "agent", None)
            if agent
            else Actor(str(principal.ref), principal.actor, "person", principal.role)
        )
        sid = body.situation_id
        if sid in _ON_DEMAND:
            raise HTTPException(status_code=409, detail="a routine is already running on this case")
        if len(_ON_DEMAND) >= MAX_ON_DEMAND:
            raise HTTPException(
                status_code=429,
                detail=f"{MAX_ON_DEMAND} routines are running already; try again in a minute",
                headers={"Retry-After": "30"},
            )
        _ON_DEMAND.add(sid)
        try:
            # The run is opened and closed here, each in the transaction helper; the walk between
            # them is the engine's, outside any transaction, since it waits on devices (ADR #462).
            async with write_txn():
                # Who asked, by name for the screen; the audit row below carries the exact ref.
                run_id = await store.noc_run_start(
                    int(row["id"]), int(row["version"]), sid, time.time(), invoker.name[:120]
                )
            report = await runbook.advise(store, row, sid, run_id, facts, invoker)
            trace = report.pop("trace")
            async with write_txn():
                await store.noc_run_end(
                    run_id, time.time(), "advised", json.dumps(trace, separators=(",", ":"))[:60000]
                )
                await audit_row(
                    request,
                    principal,
                    "noc.playbook.run",
                    "ok",
                    object_type="noc_case",
                    object_id=str(sid),
                    # The same exact identity as each check's `noc.exec` row (the engine's `ref`).
                    actor=invoker.ref,
                    details={
                        "playbook": row["id"],
                        "version": row["version"],
                        "outcome": "advised",
                        "advice": str(report["advice"].get("end")),
                        "steps": len(trace),
                    },
                )
            return report
        finally:
            _ON_DEMAND.discard(sid)
