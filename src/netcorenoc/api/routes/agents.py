"""NOC → Agents and the organization: hire, configure, wake, pause and retire the NOC's AI agents,
and read what each has done (ADRs #465, #467).

Paperclip's "company" is the NOC here: an organization of agents — head, supervisors, engineers —
each reporting to another or to the NOC team, the people at the top. Who may do what:

* **seeing** the organization, an agent and its runs is the NOC's (`agents.read`);
* **waking, pausing, resuming** an agent is too (`agents.run`) — pausing is a kill switch. A
  caller that *is* an agent may do it only to agents below it;
* **hiring, changing, terminating** an agent and **giving it a new key** are an admin's
  (`agents.manage`). The key is shown once, when the agent runs elsewhere; the appliance keeps it
  sealed when it runs the agent itself (`api/agent_hire.py`).

Every act is one write transaction and one `noc.agent.*` audit row. An agent reads itself at
`/me` — its instructions, its chain of command, its cases — and reports a run over at
`/api/noc/runs/{run_id}/finish`.
"""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request

from netcorenoc.api import agent_hire
from netcorenoc.api.agent_view import agent_view, catalogue, org_tree, run_view
from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.api.models_agents import AgentIn, AgentPatchIn, FinishIn, InvokeIn, PauseIn
from netcorenoc.crosscutting import auth
from netcorenoc.engine.noc import agents

NO_SUCH = "no such agent"
NO_RUN = "no such run"
DAY = 86400.0


def _fail(exc: agents.AgentError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=str(exc))


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the agent routes on `app`."""
    store, security = ctx.store, ctx.security
    audit_row, write_txn = ctx.perimeter.audit_row, ctx.write_txn
    route = DeclaredRoutes(app)

    async def everyone() -> dict[int, dict[str, Any]]:
        return {int(a["id"]): a for a in await store.noc_agents()}

    async def caller(principal: auth.Principal) -> dict[str, Any] | None:
        """The agent the caller is, when the caller is one."""
        if not principal.is_token or principal.token_id is None:
            return None
        return await store.noc_agent_by_token(principal.token_id)

    async def subject(agent_id: int) -> dict[str, Any]:
        agent = await store.noc_agent(agent_id)
        if agent is None or agent["status"] == "terminated":
            raise HTTPException(status_code=404, detail=NO_SUCH)
        return agent

    async def may_run(principal: auth.Principal, agent_id: int) -> None:
        """A person with `agents.run` acts on any agent; an agent only on those below it."""
        async with store.reading():
            me = await caller(principal)
            below = agents.subtree(await everyone(), int(me["id"])) if me else set()
        if me is not None and agent_id not in below:
            raise HTTPException(
                status_code=403, detail="an agent wakes or pauses only the agents below it"
            )

    async def audited(
        request: Request, principal: auth.Principal, action: str, agent_id: int, **details: Any
    ) -> None:
        await audit_row(
            request,
            principal,
            action,
            "ok",
            object_type="noc_agent",
            object_id=str(agent_id),
            details=details or None,
        )

    def settings(fields: dict[str, Any], current: dict[str, Any] | None) -> dict[str, Any]:
        """Check what is being set, against the agent as it is: role, icon, adapter."""
        out = dict(fields)
        if "role" in out and out["role"] not in agents.ROLES:
            raise HTTPException(400, f"role: one of {', '.join(agents.ROLES)}")
        if "icon" in out and out["icon"] not in agents.ICONS:
            raise HTTPException(400, f"icon: one of {', '.join(agents.ICONS)}")
        if "adapter" in out or "adapter_config" in out:
            adapter = out.get("adapter") or (current or {}).get("adapter", "")
            given = out.get("adapter_config")
            config = (
                given
                if given is not None
                else json.loads((current or {}).get("adapter_config", "{}"))
            )
            try:
                out["adapter_config"] = json.dumps(
                    agents.validate_config(adapter, config), sort_keys=True
                )
            except agents.AgentError as exc:
                raise _fail(exc) from exc
        if out.get("heartbeat_s") and not 60 <= out["heartbeat_s"] <= 86400:
            raise HTTPException(400, "heartbeat_s: 0 (off) or 60 to 86400 seconds")
        if out.get("escalate_after_s") == 0:
            out["escalate_after_s"] = None  # back to the handbook's per priority
        return out

    async def check_links(fields: dict[str, Any], agent_id: int | None) -> None:
        """A manager that exists and works, no loop; run here, a usable provider key."""
        boss = fields.get("reports_to")
        if boss:
            if (await store.noc_agent(boss) or {}).get("status") in (None, "terminated"):
                raise HTTPException(400, "reports_to: no such agent")
            if agent_id is not None and agents.cycle(await everyone(), agent_id, boss):
                raise HTTPException(400, "reports_to: that agent is below this one — a loop")
        await agent_hire.check_key(store, json.loads(fields.get("adapter_config") or "{}"))

    @route.get("/api/noc/agents", operation_id="noc_list_agents")
    async def list_agents(principal: auth.Principal = Depends(security)) -> dict[str, Any]:
        """The NOC's organization: every agent (status, role, tier, open cases), the tree of who
        reports to whom, and the catalogue a hiring form is built from."""
        async with store.reading():
            people = await everyone()
        return {
            "agents": [agent_view(a, people, whole=False) for a in people.values()],
            "org": org_tree(people),
            "mcp_path": "/api/mcp",
            **catalogue(),
        }

    @route.post("/api/noc/agents", operation_id="noc_hire_agent")
    async def hire_agent(
        body: AgentIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """Hire an agent. Its key is in the answer once — or kept sealed, when it runs here."""
        fields = settings(body.model_dump(), None)
        now = time.time()
        async with write_txn():
            await check_links(fields, None)
            agent_id = await store.noc_create_agent(fields, now, principal.actor)
            identity = await agent_hire.issue(
                store, agent_id, body.name, body.adapter, now, principal.actor
            )
            await audited(
                request,
                principal,
                "noc.agent.create",
                agent_id,
                name=body.name,
                role=body.role,
                adapter=body.adapter,
                reports_to=body.reports_to,
            )
            people = await everyone()
        return {
            "agent": agent_view(people[agent_id], people),
            "token": identity.token,
            "webhook_key": identity.webhook_key,
            "shown_once": identity.token is not None,
        }

    @route.get("/api/noc/agents/me", operation_id="noc_me")
    async def me(principal: auth.Principal = Depends(security)) -> dict[str, Any]:
        """Who you are, as an agent: your instructions, your chain of command, your reports, your
        cases and your current run. 404 for a caller that is not an agent."""
        async with store.reading():
            self_ = await caller(principal)
            if self_ is None:
                raise HTTPException(status_code=404, detail="you are not one of the NOC's agents")
            people = await everyone()
            cases_ = await store.noc_agent_cases(int(self_["id"]), limit=20)
            run = await store.noc_agent_run_active(int(self_["id"]))
        return {
            "agent": agent_view(self_, people),
            "cases": cases_,
            "run": None if run is None else run_view(run),
        }

    @route.get("/api/noc/agents/{agent_id}", operation_id="noc_get_agent")
    async def get_agent(
        agent_id: int, days: int = 14, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """One agent: its settings and instructions, chain of command, latest runs, cases, and
        its activity over ``days`` (runs per day and status, cases by priority and status)."""
        span = min(max(days, 1), 90)
        async with store.reading():
            people = await everyone()
            if agent_id not in people:
                raise HTTPException(status_code=404, detail=NO_SUCH)
            runs = await store.noc_agent_runs(agent_id, limit=20)
            cases_ = await store.noc_agent_cases(agent_id)
            activity = await store.noc_agent_activity(agent_id, time.time() - span * DAY)
        return {
            "agent": agent_view(people[agent_id], people),
            "runs": [run_view(r) for r in runs],
            "cases": cases_,
            "activity": {"days": span, **activity},
        }

    @route.post("/api/noc/agents/{agent_id}", operation_id="noc_update_agent")
    async def update_agent(
        agent_id: int,
        body: AgentPatchIn,
        request: Request,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """Change any of an agent's settings, against the ``version`` you read. Changing its
        adapter between `claude` and an agent elsewhere gives it a new key."""
        given = body.model_dump(exclude_unset=True)
        version = given.pop("version")
        if given.get("reports_to") == 0:
            given["reports_to"] = None  # to the NOC team
        now, identity = time.time(), None
        async with write_txn():
            agent = await subject(agent_id)
            fields = settings(given, agent)
            await check_links(fields, agent_id)
            if not await store.noc_update_agent(agent_id, fields, version, now, principal.actor):
                raise HTTPException(
                    409, f"the agent changed since you read it (now {agent['version']})"
                )
            moved = "adapter" in fields and (fields["adapter"] in agents.IN_PROCESS) != (
                agent["adapter"] in agents.IN_PROCESS
            )
            name = fields.get("name", agent["name"])
            if moved:
                await agent_hire.retire(store, agent["token_id"], now, principal.actor)
                identity = await agent_hire.issue(
                    store, agent_id, name, fields["adapter"], now, principal.actor
                )
            elif "name" in fields and agent["token_id"] is not None:
                if await store.noc_token_name_taken(name):
                    raise HTTPException(409, f"a service token is already called {name!r}")
                await store.noc_rename_token(int(agent["token_id"]), name)
            await audited(request, principal, "noc.agent.update", agent_id, fields=sorted(fields))
            people = await everyone()
        return {
            "agent": agent_view(people[agent_id], people),
            "token": None if identity is None else identity.token,
            "webhook_key": None if identity is None else identity.webhook_key,
        }

    @route.post("/api/noc/agents/{agent_id}/invoke", operation_id="noc_invoke_agent")
    async def invoke_agent(
        agent_id: int,
        body: InvokeIn,
        request: Request,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """Wake an agent now (merged into the run it already has queued, if any)."""
        await may_run(principal, agent_id)
        async with write_txn():
            agent = await subject(agent_id)
            if agent["adapter"] == "external":
                raise HTTPException(409, f"{agent['name']} is external: it comes on its own")
            if agent["status"] == "paused":
                raise HTTPException(409, f"{agent['name']} is paused: resume it first")
            if body.situation_id is not None and await store.noc_case(body.situation_id) is None:
                raise HTTPException(400, "situation_id: no such case")
            run_id = await store.noc_wake(
                agent_id,
                "on_demand",
                body.situation_id,
                principal.ref or principal.actor,
                time.time(),
            )
            await audited(request, principal, "noc.agent.invoke", agent_id, run=run_id)
            run = await store.noc_agent_run(run_id)
        assert run is not None
        return {"run": run_view(run)}

    @route.post("/api/noc/agents/{agent_id}/pause", operation_id="noc_pause_agent")
    async def pause_agent(
        agent_id: int,
        body: PauseIn,
        request: Request,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """Stop an agent's heartbeats and wake-ups; its cases climb to its manager."""
        await may_run(principal, agent_id)
        async with write_txn():
            await subject(agent_id)
            reason = body.reason.strip() or f"paused by {principal.actor}"
            await store.noc_set_agent_status(
                agent_id, "paused", time.time(), principal.actor, reason=reason
            )
            await audited(request, principal, "noc.agent.pause", agent_id)
            people = await everyone()
        return {"agent": agent_view(people[agent_id], people)}

    @route.post("/api/noc/agents/{agent_id}/resume", operation_id="noc_resume_agent")
    async def resume_agent(
        agent_id: int, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """Let a paused agent — or one in error — work again."""
        await may_run(principal, agent_id)
        async with write_txn():
            agent = await subject(agent_id)
            if agent["status"] not in ("paused", "error"):
                raise HTTPException(409, f"{agent['name']} is {agent['status']}, not paused")
            await store.noc_set_agent_status(agent_id, "idle", time.time(), principal.actor)
            await audited(request, principal, "noc.agent.resume", agent_id)
            people = await everyone()
        return {"agent": agent_view(people[agent_id], people)}

    @route.post("/api/noc/agents/{agent_id}/terminate", operation_id="noc_terminate_agent")
    async def terminate_agent(
        agent_id: int, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """Retire an agent for good: its key is revoked, its reports move up to its manager, its
        cases climb. Irreversible; its runs and audit history stay."""
        now = time.time()
        async with write_txn():
            agent = await subject(agent_id)
            people = await everyone()
            for report in [a for a in people.values() if a["reports_to"] == agent_id]:
                await store.noc_update_agent(
                    int(report["id"]),
                    {"reports_to": agent["reports_to"]},
                    int(report["version"]),
                    now,
                    principal.actor,
                )
            await agent_hire.retire(store, agent["token_id"], now, principal.actor)
            await store.noc_set_agent_status(agent_id, "terminated", now, principal.actor)
            await audited(request, principal, "noc.agent.terminate", agent_id, name=agent["name"])
        return {"terminated": agent_id}

    @route.post("/api/noc/agents/{agent_id}/key", operation_id="noc_rotate_agent_key")
    async def rotate_key(
        agent_id: int, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """A new key: the old token is revoked and its leases end. Shown once, as at hiring."""
        now = time.time()
        async with write_txn():
            agent = await subject(agent_id)
            await agent_hire.retire(store, agent["token_id"], now, principal.actor)
            identity = await agent_hire.issue(
                store, agent_id, agent["name"], agent["adapter"], now, principal.actor
            )
            await audited(request, principal, "noc.agent.rotate", agent_id)
        return {"token": identity.token, "webhook_key": identity.webhook_key}

    @route.get("/api/noc/runs/{run_id}", operation_id="noc_get_run")
    async def get_run(run_id: int, principal: auth.Principal = Depends(security)) -> dict[str, Any]:
        """One run and every step of it: each tool call, what it answered, and how it ended."""
        async with store.reading():
            run = await store.noc_agent_run(run_id)
            steps = await store.noc_agent_run_steps(run_id) if run else []
        if run is None:
            raise HTTPException(status_code=404, detail=NO_RUN)
        return {"run": run_view(run), "steps": steps}

    @route.post("/api/noc/runs/{run_id}/finish", operation_id="noc_finish_run")
    async def finish_run(
        run_id: int,
        body: FinishIn,
        request: Request,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """Report your run over: what you did, and whether it worked. A person with `agents.run`
        may end anyone's, as cancelled."""
        now = time.time()
        async with write_txn():
            run = await store.noc_agent_run(run_id)
            if run is None:
                raise HTTPException(status_code=404, detail=NO_RUN)
            me = await caller(principal)
            if me is not None and int(me["id"]) != int(run["agent_id"]):
                raise HTTPException(403, "an agent reports only its own runs")
            status = body.outcome if me is not None else "cancelled"
            if not await store.noc_agent_run_finish(run_id, status, now, summary=body.summary):
                raise HTTPException(409, f"the run already ended ({run['status']})")
            await audited(
                request,
                principal,
                "noc.run.finish",
                int(run["agent_id"]),
                run=run_id,
                outcome=status,
            )
            ended = await store.noc_agent_run(run_id)
        assert ended is not None
        return {"run": run_view(ended)}
