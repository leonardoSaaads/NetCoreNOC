"""Routing a case to an agent, and the climb up the chain (ADR #467).

The acts, once each, for every caller — the API (a person or an agent assigns or escalates), the
agent loop (routing a queued case, escalating one that was not concluded in time):

* **assign** routes a case to an agent and, if it wakes on assignment, queues it a run about the
  case. Routing is not taking: the take clock runs until the agent takes the case, as it would for
  a person;
* **escalate** sends the case to the next agent up — the first manager in the chain who can still
  work, at the next tier (the handbook's "With NOC Tier 2…4") — and past the top to the NOC team:
  a case the agent had taken goes back to the queue for the people, a new cycle (`escalated`);
* **route** picks the entry agent for a queued case: the one with the fewest live cases.

Each act posts an `internal` event to the case feed. The caller holds the lock and commits; nothing
here commits or audits (as `case_ops`).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from netcorenoc.engine.noc import case_ops
from netcorenoc.engine.noc.agents import WORKING
from netcorenoc.engine.noc.case_ops import Actor
from netcorenoc.engine.noc.cases import CaseError

if TYPE_CHECKING:
    from netcorenoc.store import Store

__all__ = ["WORKING", "assign", "escalate", "pick_entry", "tier_label", "with_people"]


async def assign(
    store: Store,
    case: Mapping[str, Any],
    agent: Mapping[str, Any],
    actor: Actor,
    now: float,
    *,
    trigger: str = "assignment",
    tier: int | None = None,
    escalated: bool = False,
    note: str = "",
) -> None:
    """Route ``case`` to ``agent``; wake it if it wakes on assignment."""
    sid = int(case["situation_id"])
    if str(case["status"]) in ("resolved", "closed"):
        raise CaseError("a resolved case is not routed")
    if agent["status"] not in WORKING:
        raise CaseError(f"{agent['name']} is {agent['status']}: it takes no cases now")
    level = max(int(case.get("tier") or 1), int(tier or agent["tier"]))
    await store.noc_case_assign(sid, int(agent["id"]), now, tier=level, escalated=escalated)
    verb = "Escalated" if escalated else "Assigned"
    text = f"{verb} to {agent['name']} — With NOC Tier {level}. {note}".strip()
    await case_ops.event(store, sid, text, now, actor)
    if agent["wake_on_assignment"] and agent["adapter"] != "external":
        await store.noc_wake(int(agent["id"]), trigger, sid, actor.ref, now)


async def with_people(
    store: Store, case: Mapping[str, Any], actor: Actor, now: float, *, why: str
) -> None:
    """Past the agents: the case is the NOC team's. One an agent had taken goes back to the queue
    for a person to take (a new cycle, `escalated`); one still queued stays where it is."""
    sid = int(case["situation_id"])
    if str(case["status"]) == "working" and str(case.get("owner_kind")) == "agent":
        await store.noc_cycle_left(sid, now)
        await store.noc_case_enqueue(sid, "escalated", now)
    await store.noc_case_assign(sid, None, now, escalated=True, with_people=True)
    await case_ops.event(store, sid, f"Escalated to the NOC team: {why}", now, actor)


async def escalate(
    store: Store,
    case: Mapping[str, Any],
    everyone: Mapping[int, Mapping[str, Any]],
    actor: Actor,
    now: float,
    *,
    why: str,
) -> str:
    """Up one level: to the nearest manager who can work, else to the NOC team. Returns the name of
    who has the case now."""
    current = case.get("agent_id")
    boss = None if current is None else everyone.get(int(current), {}).get("reports_to")
    while boss is not None:
        manager = everyone.get(int(boss))
        if manager is None:
            break
        if manager["status"] in WORKING:
            tier = min(4, int(case.get("tier") or 1) + 1)
            await assign(
                store,
                case,
                manager,
                actor,
                now,
                trigger="escalation",
                tier=max(tier, int(manager["tier"])),
                escalated=True,
                note=why,
            )
            return str(manager["name"])
        boss = manager.get("reports_to")
    await with_people(store, case, actor, now, why=why)
    return "the NOC team"


def pick_entry(
    everyone: Mapping[int, Mapping[str, Any]], loads: Mapping[int, int]
) -> Mapping[str, Any] | None:
    """The entry agent with the fewest live cases (the lowest id on a tie), or None."""
    ready = [a for a in everyone.values() if a["entry"] and a["status"] in WORKING]
    if not ready:
        return None
    return min(ready, key=lambda a: (loads.get(int(a["id"]), 0), int(a["id"])))


def tier_label(tier: int | None, with_team: bool) -> str:
    """The handbook's Working status for a case's level."""
    return "With the NOC team" if with_team else f"With NOC Tier {tier or 1}"
