"""The NOC's agents: who they are, how they are woken, the chain a case climbs (ADRs #465, #467).

An **agent** is an AI the NOC hired — Paperclip's "employee" — with a role, a title, a tier and a
manager (`reports_to`; none means it reports to the NOC team, the people at the top). Its identity
is a service token, so what it may do is decided where everyone's is (RBAC, scope, audit); this
module decides only what is the NOC's own:

* **the organization is a forest, never a loop** — `cycle` refuses a manager that is already below;
* **how an agent is woken** (`ADAPTERS`): a signed `webhook` to an agent running elsewhere (Claude
  Code, Codex, OpenClaw, a service of one's own), the appliance's own `claude` loop through the
  Anthropic API or `openai` loop through any OpenAI-compatible API (OpenAI, Google AI Studio, xAI,
  Meta, a local Ollama — ADR #471), or `external` — an agent that comes over MCP on its own
  schedule and is never called;
* **the chain a case climbs** (the handbook's escalation reminders, Table 4.1): a case starts with
  an entry agent at Tier 1; when it is not concluded within the agent's escalation time it goes
  to that agent's manager, and from the top of the chain to the NOC team. Critical cases climb
  every 30 minutes — Tier 4 by the 90th minute — major every hour, minor every two;
* **what a run costs**, from the token counts the API reports and the published prices — for
  Claude, Anthropic's rates (cache writes at 1.25 and reads at 0.1 times the input rate); for any
  other provider, the price per million tokens its admin set, since only they know their contract
  (ADR #472). An unpriced run counts its tokens and costs nothing, so its budget cannot stop it.

Pure.
"""

from __future__ import annotations

import ipaddress
import json
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

__all__ = [
    "ADAPTERS",
    "EFFORTS",
    "ESCALATE_EVERY_S",
    "ICONS",
    "IN_PROCESS",
    "MODELS",
    "OPENAI_PROVIDERS",
    "ROLES",
    "RUN_STATUSES",
    "STATUSES",
    "TRIGGERS",
    "AgentError",
    "chain",
    "cost_cents",
    "cycle",
    "escalate_after",
    "priced",
    "subtree",
    "validate_config",
]


class AgentError(ValueError):
    """An agent this appliance will not hire or change, with the sentence why and an HTTP status."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


#: The roles of a NOC's organization, with their labels. `migrations/0037_noc_agents.sql` holds the
#: same set in a CHECK.
ROLES: dict[str, str] = {
    "head": "NOC head",
    "supervisor": "Supervisor",
    "manager": "Project manager",
    "engineer": "NOC engineer",
    "specialist": "Specialist",
    "analyst": "Analyst",
}
#: The icons an agent may wear (drawn by `ui/app/icons.js`).
ICONS: tuple[str, ...] = (
    "bot",
    "crown",
    "shield",
    "router",
    "network",
    "wrench",
    "search",
    "chart",
    "headset",
    "globe",
    "cpu",
    "bolt",
)
ADAPTERS: dict[str, str] = {
    "webhook": "Webhook — an agent running elsewhere, woken by a signed POST",
    "claude": "Claude — the appliance runs the agent through the Anthropic API",
    "openai": "OpenAI-compatible — the appliance runs it on OpenAI, Gemini, Grok, Llama or Ollama",
    "external": "External — connects over MCP on its own; never woken",
}
#: The adapters the appliance runs itself: their token is kept sealed, never shown.
IN_PROCESS = frozenset({"claude", "openai"})
#: An OpenAI-compatible model's name: what the provider's own model list says.
_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/|@+-]{0,199}$")
#: The providers the `openai` adapter drives (`providers.py`; kept here as names, not imported,
#: since `providers.py` reads this module's model prices).
OPENAI_PROVIDERS: tuple[str, ...] = ("openai", "google", "xai", "meta", "ollama")
STATUSES: dict[str, str] = {
    "idle": "Idle",
    "running": "Running",
    "paused": "Paused",
    "error": "Error",
    "terminated": "Terminated",
}
TRIGGERS: dict[str, str] = {
    "timer": "Heartbeat",
    "assignment": "Case assigned",
    "on_demand": "Invoked",
    "escalation": "Case escalated",
    "external": "On its own (MCP)",
}
RUN_STATUSES: dict[str, str] = {
    "queued": "Queued",
    "running": "Running",
    "succeeded": "Succeeded",
    "failed": "Failed",
    "timed_out": "Timed out",
    "cancelled": "Cancelled",
}
#: The handbook's escalation reminders (Table 4.1) — for critical cases the next tier every 30
#: minutes — and the same proportion as the take targets for the others.
ESCALATE_EVERY_S: dict[str, int] = {"critical": 1800, "major": 3600, "minor": 7200}

#: The models the `claude` adapter offers, with their price per million tokens (input, output) in
#: US dollars — Anthropic's published rates. The default is the first.
MODELS: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-fable-5-1": (10.0, 50.0),
}
EFFORTS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")
#: What a cache write and a cache read cost, against the model's input rate (Anthropic's).
CACHE_WRITE = 1.25
CACHE_READ = 0.1
#: The most a price per million tokens may be set to, in US dollars.
MAX_PRICE = 1000.0


def _int(where: str, value: Any, low: int, high: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise AgentError(f"{where}: a whole number from {low} to {high}")
    return value


def _webhook(config: Mapping[str, Any]) -> dict[str, Any]:
    url = config.get("url")
    if not isinstance(url, str) or len(url) > 2000:
        raise AgentError("adapter_config.url: the agent's webhook address")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise AgentError("adapter_config.url: an http:// or https:// address")
    if parts.username or parts.password:
        raise AgentError("adapter_config.url: no credentials in the address — it is signed")
    try:
        address = ipaddress.ip_address(parts.hostname)
    except ValueError:
        address = None
    if address is not None and (address.is_multicast or address.is_unspecified):
        raise AgentError("adapter_config.url: not a multicast or unspecified address")
    out: dict[str, Any] = {
        "url": url,
        "timeout_s": _int("timeout_s", config.get("timeout_s", 10), 1, 30),
    }
    return out


def _claude(config: Mapping[str, Any]) -> dict[str, Any]:
    model = config.get("model", next(iter(MODELS)))
    if model not in MODELS:
        raise AgentError(f"adapter_config.model: one of {', '.join(MODELS)}")
    effort = config.get("effort", "medium")
    if effort not in EFFORTS:
        raise AgentError(f"adapter_config.effort: one of {', '.join(EFFORTS)}")
    secret = config.get("secret_id")
    if not isinstance(secret, int) or isinstance(secret, bool) or secret < 1:
        raise AgentError(
            "adapter_config.secret_id: the vault credential holding the Anthropic API key"
        )
    return {
        "model": model,
        "effort": effort,
        "secret_id": secret,
        "max_turns": _int("adapter_config.max_turns", config.get("max_turns", 20), 1, 60),
        "max_tokens": _int(
            "adapter_config.max_tokens", config.get("max_tokens", 16000), 1024, 64000
        ),
    }


def _price(where: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= MAX_PRICE:
        raise AgentError(f"{where}: US dollars per million tokens, from 0 to {MAX_PRICE:g}")
    return float(value)


def _openai(config: Mapping[str, Any]) -> dict[str, Any]:
    provider = config.get("provider")
    if provider not in OPENAI_PROVIDERS:
        raise AgentError(f"adapter_config.provider: one of {', '.join(OPENAI_PROVIDERS)}")
    model = config.get("model")
    if not isinstance(model, str) or not _MODEL.fullmatch(model):
        raise AgentError("adapter_config.model: the model's name, as the provider lists it")
    secret = config.get("secret_id")
    if not isinstance(secret, int) or isinstance(secret, bool) or secret < 1:
        raise AgentError("adapter_config.secret_id: the vault credential with the provider's key")
    return {
        "provider": provider,
        "model": model,
        "secret_id": secret,
        "max_turns": _int("adapter_config.max_turns", config.get("max_turns", 20), 1, 60),
        "max_tokens": _int(
            "adapter_config.max_tokens", config.get("max_tokens", 16000), 1024, 64000
        ),
        # ADR #472: what its provider charges, set by its admin; 0 is unpriced (or free, local).
        "price_in": _price("adapter_config.price_in", config.get("price_in", 0)),
        "price_out": _price("adapter_config.price_out", config.get("price_out", 0)),
    }


def validate_config(adapter: str, config: Any) -> dict[str, Any]:
    """The adapter's configuration, checked and with its defaults filled in."""
    if adapter not in ADAPTERS:
        raise AgentError(f"adapter: one of {', '.join(ADAPTERS)}")
    if not isinstance(config, dict):
        raise AgentError("adapter_config: an object")
    if len(json.dumps(config)) > 4000:
        raise AgentError("adapter_config: at most 4000 characters")
    if adapter == "webhook":
        return _webhook(config)
    if adapter == "claude":
        return _claude(config)
    if adapter == "openai":
        return _openai(config)
    if config:
        raise AgentError("adapter_config: an external agent has no configuration here")
    return {}


def cycle(agents: Mapping[int, Mapping[str, Any]], agent_id: int, reports_to: int | None) -> bool:
    """Would ``agent_id`` reporting to ``reports_to`` make a loop?"""
    seen = {agent_id}
    current = reports_to
    while current is not None:
        if current in seen:
            return True
        seen.add(current)
        boss = agents.get(current)
        current = None if boss is None else boss.get("reports_to")
    return False


def chain(agents: Mapping[int, Mapping[str, Any]], agent_id: int) -> list[int]:
    """``agent_id``'s managers, nearest first, up to the top of the organization."""
    out: list[int] = []
    current = agents.get(agent_id, {}).get("reports_to")
    while current is not None and current not in out and current != agent_id:
        out.append(int(current))
        current = agents.get(current, {}).get("reports_to")
    return out


def subtree(agents: Mapping[int, Mapping[str, Any]], agent_id: int) -> set[int]:
    """Everyone below ``agent_id``, at any depth (not including it)."""
    below: dict[int, list[int]] = {}
    for aid, row in agents.items():
        boss = row.get("reports_to")
        if boss is not None:
            below.setdefault(int(boss), []).append(int(aid))
    out: set[int] = set()
    stack = list(below.get(agent_id, []))
    while stack:
        aid = stack.pop()
        if aid not in out:
            out.add(aid)
            stack.extend(below.get(aid, []))
    return out


def escalate_after(agent: Mapping[str, Any], priority: str) -> int:
    """How long a case stays with ``agent`` before it climbs: the agent's own setting, else the
    handbook's per priority."""
    own = agent.get("escalate_after_s")
    return int(own) if own else ESCALATE_EVERY_S.get(priority, ESCALATE_EVERY_S["minor"])


def cost_cents(
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    cache_write: int = 0,
    cache_read: int = 0,
    price: tuple[float, float] | None = None,
) -> float:
    """What a call cost, in US cents: ``price`` (per million tokens, input and output) when given,
    else the model's published price (0 for an unknown model). ``input_tokens`` are the uncached
    ones; cache writes and reads are priced against the input rate."""
    price_in, price_out = price if price is not None else MODELS.get(model, (0.0, 0.0))
    inputs = input_tokens + cache_write * CACHE_WRITE + cache_read * CACHE_READ
    return round((inputs * price_in + output_tokens * price_out) / 10_000, 4)


def priced(adapter: str, config: Mapping[str, Any]) -> str:
    """How a run of this agent is priced: ``published`` (Anthropic's rates), ``set`` (its admin's
    price), ``local`` (Ollama, on one's own hardware, unpriced), ``unpriced`` or ``elsewhere``."""
    if adapter == "claude":
        return "published"
    if adapter != "openai":
        return "elsewhere"
    if config.get("price_in") or config.get("price_out"):
        return "set"
    return "local" if config.get("provider") == "ollama" else "unpriced"
