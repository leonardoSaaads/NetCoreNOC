"""The agent loop: routing, the climb, heartbeats and runs (ADR #467).

Every few seconds, off the ingest path (#454): read on a reader what is due, take ``store.lock``
once to act on it — re-reading each case under it, since a person may have acted in between — and
then start the runs that are ready, each in a task of its own with nothing held across its I/O:

* a **queued** case with no agent goes to an entry agent — the one with the fewest live cases;
* a case still with an agent past its escalation time **climbs** (`routing.escalate`): to the
  manager at the next tier, past the top to the NOC team. So does one whose agent was paused or
  retired. A case a person took is theirs, and does not climb;
* an idle agent whose **heartbeat** has come is woken (`timer`);
* a run past its time limit is ended `timed_out`;
* a **queued run** starts: a `webhook` agent is sent its signed wake-up and works over MCP until it
  reports the run finished; a `claude` or `openai` agent is run by the appliance (the runners are
  injected — they call this appliance's API in process, which this domain may not import).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from netcorenoc.crosscutting import secretbox
from netcorenoc.engine.noc import agents, routing, webhook
from netcorenoc.engine.noc.case_ops import SYSTEM

if TYPE_CHECKING:
    from netcorenoc.store import Store

__all__ = ["LOOK_EVERY_S", "Outcome", "Runner", "agent_loop", "start_ready", "tick"]

log = logging.getLogger("netcorenoc")

LOOK_EVERY_S = 5.0


@dataclass(frozen=True)
class Outcome:
    """How a run the appliance drove itself ended, and what it cost."""

    status: str  # succeeded | failed | timed_out
    summary: str = ""
    error: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_cents: float = 0.0


#: Runs one appliance-run agent's run to its end (`api/agent_runtime.py`, `api/agent_openai.py`).
Runner = Callable[[Mapping[str, Any], Mapping[str, Any]], Awaitable[Outcome]]
#: The adapters the appliance runs itself, and what a run says when its runner is missing.
IN_PROCESS: dict[str, str] = {
    "claude": "the Claude adapter is not installed",
    "openai": "the OpenAI-compatible adapter is not available",
}


def _climb_reason(
    case: Mapping[str, Any], everyone: Mapping[int, Mapping[str, Any]], at: float
) -> str | None:
    """Why a routed case should climb now, or None."""
    if str(case.get("owner_ref") or "").startswith("user:"):
        return None  # a person took it: it is theirs
    agent = everyone.get(int(case["agent_id"]))
    if agent is None:
        return "its agent left the NOC"
    if agent["status"] == "paused":
        return f"{agent['name']} is paused"
    limit = agents.escalate_after(agent, str(case["priority"]))
    if at - float(case["assigned_at"]) > limit:
        return f"not concluded by {agent['name']} within {limit // 60} minutes"
    return None


async def tick(store: Store, *, now: Callable[[], float] = time.time) -> dict[str, int]:
    """Routing, the climb, heartbeats and run limits — one pass; how many of each it did."""
    counts = {"routed": 0, "escalated": 0, "woken": 0, "timed_out": 0}
    at = now()
    async with store.reading():
        everyone = {int(a["id"]): a for a in await store.noc_agents()}
        unrouted = await store.noc_cases_unrouted() if everyone else []
        routed = await store.noc_cases_routed()
        due = await store.noc_agents_heartbeat_due(at)
        overdue = await store.noc_agent_runs_overdue(at)
        loads = dict(await store.noc_agent_loads())
    climbing = [(c, why) for c in routed if (why := _climb_reason(c, everyone, at))]
    entry = routing.pick_entry(everyone, loads)
    if not ((unrouted and entry) or climbing or due or overdue):
        return counts
    async with store.lock:
        at = now()
        for sid in unrouted:
            entry = routing.pick_entry(everyone, loads)
            case = await store.noc_case(sid)
            if entry is None or case is None or case["status"] != "queue" or case["agent_id"]:
                continue
            await routing.assign(store, case, entry, SYSTEM, at, note="Routed from the queue.")
            loads[int(entry["id"])] = loads.get(int(entry["id"]), 0) + 1
            counts["routed"] += 1
        for row, why in climbing:
            case = await store.noc_case(int(row["situation_id"]))
            if case is None or case["agent_id"] != row["agent_id"] or case["with_people"]:
                continue
            await routing.escalate(store, case, everyone, SYSTEM, at, why=why)
            counts["escalated"] += 1
        for aid in due:
            await store.noc_wake(aid, "timer", None, "system", at)
            counts["woken"] += 1
        for run in overdue:
            if await store.noc_agent_run_finish(
                int(run["id"]), "timed_out", at, error="past the agent's time limit"
            ):
                await store.noc_agent_run_step(int(run["id"]), "error", "timeout", False, "", at)
                counts["timed_out"] += 1
        await store.commit()
    return counts


def _payload(run: Mapping[str, Any], agent: Mapping[str, Any], case: Any) -> dict[str, Any]:
    return {
        "event": "wake",
        "run_id": run["id"],
        "trigger": run["trigger"],
        "agent": {k: agent[k] for k in ("id", "name", "role", "tier")},
        "case": None
        if case is None
        else {
            "id": case["situation_id"],
            "status": case["status"],
            "priority": case["priority"],
            "title": case["title"],
        },
        "mcp_path": "/api/mcp",
        "finish_path": f"/api/noc/runs/{run['id']}/finish",
        "sent_at": time.time(),
    }


async def _wake_webhook(store: Store, run: Mapping[str, Any], agent: Mapping[str, Any]) -> None:
    config = json.loads(agent["adapter_config"])
    async with store.reading():
        sealed = await store.noc_agent_sealed(int(agent["id"]))
        case = await store.noc_case(int(run["situation_id"])) if run["situation_id"] else None
    key_sealed = (sealed or {}).get("webhook_key_sealed")
    if not key_sealed:
        delivery = webhook.Delivery(False, None, "the agent has no signing key: give it a new key")
    else:
        key = secretbox.current().open(f"noc-agent:{agent['id']}:webhook", key_sealed)["key"]
        delivery = await webhook.deliver(
            config["url"], key, _payload(run, agent, case), float(config.get("timeout_s", 10))
        )
    async with store.lock:
        at = time.time()
        await store.noc_agent_run_step(
            int(run["id"]), "wake", "webhook", delivery.ok, delivery.detail, at
        )
        if not delivery.ok:
            await store.noc_agent_run_finish(int(run["id"]), "failed", at, error=delivery.detail)
            await store.noc_set_agent_status(
                int(agent["id"]), "error", at, "system", reason=f"wake-up failed: {delivery.detail}"
            )
        await store.commit()


async def _run_in_process(
    store: Store, run: Mapping[str, Any], agent: Mapping[str, Any], runner: Runner
) -> None:
    try:
        outcome = await runner(run, agent)
    except Exception as exc:  # a failed run is recorded, never a stuck agent
        log.exception("agent %s run %s failed", agent["id"], run["id"])
        outcome = Outcome("failed", error=type(exc).__name__)
    async with store.lock:
        at = time.time()
        usage = (outcome.input_tokens, outcome.output_tokens, outcome.cost_cents)
        await store.noc_agent_run_finish(
            int(run["id"]),
            outcome.status,
            at,
            summary=outcome.summary,
            error=outcome.error,
            usage=usage,
        )
        spent = await store.noc_agent_spend(
            int(agent["id"]), outcome.cost_cents, time.strftime("%Y-%m", time.gmtime(at))
        )
        budget = int(agent["budget_month_cents"])
        if budget and spent >= budget:
            await store.noc_set_agent_status(
                int(agent["id"]), "paused", at, "system", reason="its monthly budget is spent"
            )
        await store.commit()


async def start_ready(
    store: Store,
    running: dict[int, asyncio.Task[None]],
    claude: Runner | None,
    openai: Runner | None = None,
) -> int:
    """Start every queued run whose agent is free; how many started."""
    runners = {"claude": claude, "openai": openai}
    async with store.reading():
        ready = await store.noc_agent_runs_ready()
        everyone = {int(a["id"]): a for a in await store.noc_agents()}
    started = 0
    for run in ready:
        agent = everyone.get(int(run["agent_id"]))
        if agent is None or int(run["id"]) in running:
            continue
        async with store.lock:
            at = time.time()
            adapter = str(agent["adapter"])
            if adapter in IN_PROCESS and runners.get(adapter) is None:
                await store.noc_agent_run_finish(
                    int(run["id"]), "failed", at, error=IN_PROCESS[adapter]
                )
                await store.commit()
                continue
            await store.noc_agent_run_start(int(run["id"]), at)
            await store.noc_agent_run_step(
                int(run["id"]), "wake", run["trigger"], True, agents.TRIGGERS[run["trigger"]], at
            )
            await store.commit()
        runner = runners.get(str(agent["adapter"]))
        work = (
            _run_in_process(store, run, agent, runner)
            if runner is not None
            else _wake_webhook(store, run, agent)
        )
        running[int(run["id"])] = asyncio.create_task(work)
        started += 1
    return started


async def agent_loop(
    store: Store, claude: Runner | None = None, openai: Runner | None = None
) -> None:
    """The supervised loop (a crash is the supervisor's to count and restart)."""
    running: dict[int, asyncio.Task[None]] = {}
    while True:
        await tick(store)
        await start_ready(store, running, claude, openai)
        for run_id in [r for r, task in running.items() if task.done()]:
            running.pop(run_id)
        await asyncio.sleep(LOOK_EVERY_S)
