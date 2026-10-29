"""The `claude` adapter: the appliance runs an agent itself, through the Anthropic API (ADR #467).

What a `webhook` agent does elsewhere, done here: the agent's instructions and the NOC's rules are
the system prompt, why it was woken is the first message, and its tools are the MCP tools its key
may call — each one called **in process, with the agent's own key** (`api/mcp.py`), so it is
authorized, scoped, audited and recorded as a step of the run exactly as over MCP. The loop is the
API's manual tool-use loop: ask, run the tools it asked for, answer with their results, until it
ends its turn, refuses, or runs out of turns, time or budget. What it shares with the `openai`
adapter — the keys, the tools, the prompt, the steps — is `api/agent_tools.py`.

**Two keys, both from the vault, neither ever leaving this process**: the agent's own token,
sealed when it was hired (`api/agent_hire.py`), and the Anthropic API key — a vault credential
(Anthropic's kind, or an older generic API token) a custodian let agents use. A device's output
reaches the model as a tool result said to be data, never instructions (G6).

Defaults (`engine/noc/agents.py`): `claude-opus-5-5`, adaptive thinking at the agent's effort, the
server-side refusal fallback on (`fallbacks: "default"`), the system prompt and tools cached.
`anthropic` is the optional `agents` extra; without it the adapter is reported missing and a
`claude` agent's runs fail with that sentence.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any

from netcorenoc.api import agent_openai
from netcorenoc.api.agent_tools import (
    Usage,
    call_tool,
    first_message,
    open_keys,
    step,
    system_prompt,
    toolbox,
)
from netcorenoc.api.case_close import automation_broker
from netcorenoc.engine.noc import agent_loop, agents, runbook
from netcorenoc.engine.noc.agent_loop import Outcome, Runner
from netcorenoc.store import Store

if TYPE_CHECKING:
    from netcorenoc.main import Engine

__all__ = ["available", "claude_runner", "loops"]

FALLBACK_BETA = "server-side-fallback-2026-07-01"


def available() -> bool:
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def _default_client(api_key: str) -> Any:
    from anthropic import AsyncAnthropic

    return AsyncAnthropic(api_key=api_key, max_retries=2, timeout=600.0)


def claude_runner(
    app: Any, store: Store, *, client: Callable[[str], Any] = _default_client
) -> Runner | None:
    """The runner the agent loop calls for a `claude` agent's run, or None without `anthropic`."""
    if client is _default_client and not available():
        return None

    async def run_one(run: Mapping[str, Any], agent: Mapping[str, Any]) -> Outcome:
        config = json.loads(agent["adapter_config"])
        try:
            keys = await open_keys(store, agent, config, run, adapter="claude")
        except PermissionError as exc:
            return Outcome("failed", error=str(exc))
        headers, tools = await toolbox(app, keys.token)
        async with store.reading():
            case = await store.noc_case(int(run["situation_id"])) if run["situation_id"] else None
        system = system_prompt(agent)
        messages: list[dict[str, Any]] = [{"role": "user", "content": first_message(run, case)}]
        definitions = [
            {"name": t.name, "description": t.description or t.title, "input_schema": t.schema}
            for t in tools.values()
        ]
        api = client(keys.api_key)
        model = str(config.get("model", next(iter(agents.MODELS))))
        deadline = time.monotonic() + int(agent["run_timeout_s"])
        used = Usage(model)
        for _turn in range(int(config.get("max_turns", 20))):
            if time.monotonic() > deadline:
                return used.outcome("timed_out", error="past the agent's time limit")
            if used.over_budget(agent):
                return used.outcome("failed", error="its monthly budget is spent")
            response = await api.beta.messages.create(
                model=model,
                max_tokens=int(config.get("max_tokens", 16000)),
                system=system,
                tools=definitions,
                messages=messages,
                thinking={"type": "adaptive"},
                output_config={"effort": str(config.get("effort", "medium"))},
                cache_control={"type": "ephemeral"},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
            usage = response.usage
            used.fresh += int(usage.input_tokens or 0)
            used.cache_write += int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
            used.cache_read += int(getattr(usage, "cache_read_input_tokens", 0) or 0)
            used.output += int(usage.output_tokens or 0)
            messages.append({"role": "assistant", "content": response.content})
            if response.stop_reason == "refusal":
                return used.outcome("failed", error="the model declined this request")
            if response.stop_reason == "pause_turn":
                continue
            calls = [b for b in response.content if b.type == "tool_use"]
            if not calls:
                text = " ".join(b.text for b in response.content if b.type == "text").strip()
                await step(store, run, "text", "summary", True, text[:4000])
                return used.outcome("succeeded", summary=text[:8000] or "(no summary)")
            results = []
            for block in calls:
                arguments = block.input if isinstance(block.input, dict) else {}
                text, failed = await call_tool(
                    app, store, run, tools, block.name, arguments, headers
                )
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": text,
                        "is_error": failed,
                    }
                )
            messages.append({"role": "user", "content": results})
        return used.outcome("failed", error="out of turns before it finished")

    return run_one


def loops(store: Store, engine: Engine, app: Any) -> dict[str, Callable[[], Awaitable[None]]]:
    """The NOC's automation loops for the runner's supervisor: playbooks (ADR #463) and agents
    (ADR #467)."""
    broker = automation_broker(store, engine)
    return {
        "noc-playbooks": lambda: runbook.run_loop(store, broker),
        "noc-agents": lambda: agent_loop.agent_loop(
            store, claude_runner(app, store), agent_openai.openai_runner(app, store)
        ),
    }
