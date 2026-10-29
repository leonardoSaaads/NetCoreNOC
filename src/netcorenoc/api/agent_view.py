"""What the API answers about the NOC's agents: one agent, its runs, and the organization
(ADRs #465, #467).

Built for both readers: the console draws the org chart and the agent page from it, and an agent
reading it over MCP gets the same facts with explicit labels, ISO 8601 times beside the epoch
ones, and its chain of command. **Never a key, a token or a sealed value**: an agent's identity is
described by whether it has one (`has_token`), never by what it is.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from netcorenoc.engine.noc import agents, providers

__all__ = ["agent_view", "catalogue", "org_tree", "run_view"]


def _iso(value: Any) -> str | None:
    return None if value is None else datetime.fromtimestamp(float(value), UTC).isoformat()


def catalogue() -> dict[str, Any]:
    """What a hiring form — the console's or an agent's — is built from."""
    return {
        "roles": agents.ROLES,
        "icons": list(agents.ICONS),
        "adapters": agents.ADAPTERS,
        "models": list(agents.MODELS),
        "efforts": list(agents.EFFORTS),
        "statuses": agents.STATUSES,
        "triggers": agents.TRIGGERS,
        "run_statuses": agents.RUN_STATUSES,
        "escalate_every_s": agents.ESCALATE_EVERY_S,
        # ADR #471: the AI providers an agent the appliance runs may think with.
        "providers": providers.describe(),
    }


def run_view(run: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": run["id"],
        "agent_id": run["agent_id"],
        "agent_name": run.get("agent_name"),
        "trigger": run["trigger"],
        "trigger_label": agents.TRIGGERS.get(str(run["trigger"]), run["trigger"]),
        "situation_id": run["situation_id"],
        "status": run["status"],
        "status_label": agents.RUN_STATUSES.get(str(run["status"]), run["status"]),
        "queued_at": run["queued_at"],
        "started_at": run["started_at"],
        "ended_at": run["ended_at"],
        "ended_at_iso": _iso(run["ended_at"]),
        "requested_by": run["requested_by"],
        "summary": run["summary"],
        "error": run["error"],
        "input_tokens": run["input_tokens"],
        "output_tokens": run["output_tokens"],
        "cost_cents": float(run["cost_cents"]),
        "tool_calls": run["tool_calls"],
    }


def agent_view(
    agent: Mapping[str, Any], everyone: Mapping[int, Mapping[str, Any]], *, whole: bool = True
) -> dict[str, Any]:
    """``whole`` adds the instructions and the adapter's settings (the agent page, its own
    `me`); a listing leaves them out."""
    aid = int(agent["id"])
    boss = agent["reports_to"]
    out: dict[str, Any] = {
        "id": aid,
        "name": agent["name"],
        "role": agent["role"],
        "role_label": agents.ROLES.get(str(agent["role"]), agent["role"]),
        "title": agent["title"],
        "icon": agent["icon"],
        "tier": agent["tier"],
        "reports_to": boss,
        "reports_to_name": everyone[boss]["name"] if boss in everyone else None,
        "capabilities": agent["capabilities"],
        "adapter": agent["adapter"],
        "adapter_label": agents.ADAPTERS.get(str(agent["adapter"]), agent["adapter"]),
        "entry": agent["entry"],
        "wake_on_assignment": agent["wake_on_assignment"],
        "heartbeat_s": agent["heartbeat_s"],
        "escalate_after_s": agent["escalate_after_s"],
        "run_timeout_s": agent["run_timeout_s"],
        "budget_month_cents": agent["budget_month_cents"],
        "spent_month_cents": float(agent["spent_cents"])
        if agent["spend_month"] == time.strftime("%Y-%m", time.gmtime())
        else 0.0,
        "status": agent["status"],
        "status_label": agents.STATUSES.get(str(agent["status"]), agent["status"]),
        "pause_reason": agent["pause_reason"],
        "paused_at": agent["paused_at"],
        "error_reason": agent["error_reason"],
        "last_run_at": agent["last_run_at"],
        "last_run_at_iso": _iso(agent["last_run_at"]),
        "open_cases": agent["open_cases"],
        "has_token": agent["token_id"] is not None,
        "acts_in_process": agent["acts_in_process"],
        "version": agent["version"],
        "created_at": agent["created_at"],
        "created_by": agent["created_by"],
        "chain_of_command": [
            {"id": b, "name": everyone[b]["name"], "role": everyone[b]["role"]}
            for b in agents.chain(everyone, aid)
            if b in everyone
        ],
        "reports": sorted(
            int(other["id"]) for other in everyone.values() if other["reports_to"] == aid
        ),
    }
    config = json.loads(agent["adapter_config"])
    # ADR #471: the AI provider an agent the appliance runs thinks with, for its logo; ADR #472:
    # how its runs are priced, so a spend of $0 says whether it is free or unpriced.
    out["provider"] = "anthropic" if agent["adapter"] == "claude" else config.get("provider")
    out["priced"] = agents.priced(str(agent["adapter"]), config)
    if whole:
        out["instructions"] = agent["instructions"]
        out["adapter_config"] = config
    return out


def org_tree(everyone: Mapping[int, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The organization as nested nodes, the people's direct reports first: each node an agent's
    id, name, role, title, icon, tier and status, and its reports."""
    below: dict[int | None, list[Mapping[str, Any]]] = {}
    for agent in everyone.values():
        boss = agent["reports_to"]
        below.setdefault(boss if boss in everyone else None, []).append(agent)

    def node(agent: Mapping[str, Any], seen: frozenset[int]) -> dict[str, Any]:
        aid = int(agent["id"])
        kids = [] if aid in seen else below.get(aid, [])
        return {
            "id": aid,
            "name": agent["name"],
            "role": agent["role"],
            "role_label": agents.ROLES.get(str(agent["role"]), agent["role"]),
            "title": agent["title"],
            "icon": agent["icon"],
            "tier": agent["tier"],
            "status": agent["status"],
            "adapter": agent["adapter"],
            "entry": agent["entry"],
            "open_cases": agent["open_cases"],
            "reports": [
                node(k, seen | {aid}) for k in sorted(kids, key=lambda a: (-a["tier"], a["name"]))
            ],
        }

    tops = sorted(below.get(None, []), key=lambda a: (-a["tier"], a["name"]))
    return [node(a, frozenset()) for a in tops]
