"""The `claude` adapter: the appliance runs an agent itself, through the Anthropic API (ADR #467).

What a `webhook` agent does elsewhere, done here: the agent's instructions and the NOC's rules are
the system prompt, why it was woken is the first message, and its tools are the MCP tools its key
may call — each one called **in process, with the agent's own key** (`api/mcp.py`), so it is
authorized, scoped, audited and recorded as a step of the run exactly as over MCP. The loop is the
API's manual tool-use loop: ask, run the tools it asked for, answer with their results, until it
ends its turn, refuses, or runs out of turns, time or budget.

**Two keys, both from the vault, neither ever leaving this process**: the agent's own token,
sealed when it was hired (`api/agent_hire.py`), and the Anthropic API key — a vault credential of
kind API token that a custodian let agents use. A device's output reaches the model as a tool
result said to be data, never instructions (G6).

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

from netcorenoc.api import asgi_call, mcp, mcp_tools
from netcorenoc.api.agent_hire import binding
from netcorenoc.api.case_close import automation_broker
from netcorenoc.crosscutting import secretbox
from netcorenoc.engine.noc import agent_loop, agents, kinds, runbook
from netcorenoc.engine.noc.agent_loop import Outcome, Runner
from netcorenoc.store import Store

if TYPE_CHECKING:
    from netcorenoc.main import Engine

__all__ = ["available", "claude_runner", "loops"]

FALLBACK_BETA = "server-side-fallback-2026-07-01"
#: The most of one tool result the model is sent; the rest is counted, not shown.
MAX_RESULT = 20_000


def available() -> bool:
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def _default_client(api_key: str) -> Any:
    from anthropic import AsyncAnthropic

    return AsyncAnthropic(api_key=api_key, max_retries=2, timeout=600.0)


async def _keys(
    store: Store, agent: Mapping[str, Any], config: Mapping[str, Any]
) -> tuple[str, str]:
    """The agent's own token and the Anthropic API key, opened here, in this process."""
    async with store.reading():
        sealed = await store.noc_agent_sealed(int(agent["id"]))
        secret = await store.noc_get_secret(int(config["secret_id"]), sealed=True)
    if not sealed or not sealed.get("token_sealed"):
        raise PermissionError("the agent has no key the appliance can act with: give it a new key")
    if secret is None or secret["kind"] != "api_token" or not secret["agent_access"]:
        raise PermissionError("its Anthropic API key is gone, or agents may no longer use it")
    box = secretbox.current()
    token = box.open(binding(int(agent["id"]), "token"), str(sealed["token_sealed"]))["token"]
    fields = box.open(
        kinds.binding(
            str(secret["ref"]),
            str(secret["kind"]),
            str(secret["host"]),
            int(secret["port"]),
            str(secret["protocol_version"]),
            str(secret["access_level"]),
        ),
        str(secret["sealed"]),
    )
    return token, fields["token"]


def _first_message(run: Mapping[str, Any], case: Mapping[str, Any] | None) -> str:
    why = agents.TRIGGERS.get(str(run["trigger"]), run["trigger"])
    if case is None:
        return (
            f"You were woken ({why}, run {run['id']}). Call noc_me, look at the cases routed to "
            "you and the NOC queue, act on what needs you, and finish the run."
        )
    return (
        f"You were woken ({why}, run {run['id']}) about case #{case['situation_id']} — "
        f"{case['priority']}, {case['status']}: {case['title'] or 'no title yet'}. Work it as "
        "the NOC's rules say, then finish the run with what you did."
    )


def claude_runner(
    app: Any, store: Store, *, client: Callable[[str], Any] = _default_client
) -> Runner | None:
    """The runner the agent loop calls for a `claude` agent's run, or None without `anthropic`."""
    if client is _default_client and not available():
        return None

    async def run_one(run: Mapping[str, Any], agent: Mapping[str, Any]) -> Outcome:
        config = json.loads(agent["adapter_config"])
        try:
            token, api_key = await _keys(store, agent, config)
        except PermissionError as exc:
            return Outcome("failed", error=str(exc))
        headers = [("authorization", f"Bearer {token}")]
        caller = ("127.0.0.1", 0)
        me = await asgi_call.call(app, "GET", "/api/me", headers=headers, client=caller)
        allowed = (
            set(me.body.get("capabilities", [])) if me.ok and isinstance(me.body, dict) else set()
        )
        tools = {
            t.name: t
            for t in mcp_tools.build(app.openapi()).values()
            if t.capability in allowed and t.name != "noc_finish_run"
        }
        async with store.reading():
            case = await store.noc_case(int(run["situation_id"])) if run["situation_id"] else None
        system = agent["instructions"].strip() + "\n\n" if agent["instructions"].strip() else ""
        system += mcp.GUIDE.replace("8. End your run with `noc_finish_run`", "8. End your turn")
        messages: list[dict[str, Any]] = [{"role": "user", "content": _first_message(run, case)}]
        definitions = [
            {"name": t.name, "description": t.description or t.title, "input_schema": t.schema}
            for t in tools.values()
        ]
        api = client(api_key)
        model = str(config.get("model", next(iter(agents.MODELS))))
        deadline = time.monotonic() + int(agent["run_timeout_s"])
        used_in = used_out = 0
        budget = int(agent["budget_month_cents"])
        for _turn in range(int(config.get("max_turns", 20))):
            if time.monotonic() > deadline:
                return Outcome(
                    "timed_out",
                    error="past the agent's time limit",
                    input_tokens=used_in,
                    output_tokens=used_out,
                    cost_cents=agents.cost_cents(model, used_in, used_out),
                )
            spent = float(agent["spent_cents"]) + agents.cost_cents(model, used_in, used_out)
            if budget and spent >= budget:
                return Outcome(
                    "failed",
                    error="its monthly budget is spent",
                    input_tokens=used_in,
                    output_tokens=used_out,
                    cost_cents=agents.cost_cents(model, used_in, used_out),
                )
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
            used_in += int(usage.input_tokens or 0) + int(
                getattr(usage, "cache_creation_input_tokens", 0) or 0
            )
            used_out += int(usage.output_tokens or 0)
            messages.append({"role": "assistant", "content": response.content})
            cost = agents.cost_cents(model, used_in, used_out)
            if response.stop_reason == "refusal":
                return Outcome(
                    "failed",
                    error="the model declined this request",
                    input_tokens=used_in,
                    output_tokens=used_out,
                    cost_cents=cost,
                )
            if response.stop_reason == "pause_turn":
                continue
            calls = [b for b in response.content if b.type == "tool_use"]
            if not calls:
                text = " ".join(b.text for b in response.content if b.type == "text").strip()
                await _step(store, run, "text", "summary", True, text[:4000])
                return Outcome(
                    "succeeded",
                    summary=text[:8000] or "(no summary)",
                    input_tokens=used_in,
                    output_tokens=used_out,
                    cost_cents=cost,
                )
            results = []
            for block in calls:
                tool = tools.get(block.name)
                arguments = block.input if isinstance(block.input, dict) else {}
                if tool is None:
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": f"no tool {block.name!r}",
                            "is_error": True,
                        }
                    )
                    continue
                answer, out = await mcp.run_tool(app, tool, arguments, headers, caller)
                outcome = str(answer.status) + ("" if answer.ok else f": {answer.detail()[:300]}")
                await _step(
                    store, run, "tool", tool.name, answer.ok, f"{mcp.redact(arguments)} → {outcome}"
                )
                text = "\n".join(c["text"] for c in out["content"])
                if len(text) > MAX_RESULT:
                    text = text[:MAX_RESULT] + f"… ({len(text) - MAX_RESULT} more characters)"
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": text,
                        "is_error": out["isError"],
                    }
                )
            messages.append({"role": "user", "content": results})
        cost = agents.cost_cents(model, used_in, used_out)
        return Outcome(
            "failed",
            error="out of turns before it finished",
            input_tokens=used_in,
            output_tokens=used_out,
            cost_cents=cost,
        )

    return run_one


async def _step(
    store: Store, run: Mapping[str, Any], kind: str, name: str, ok: bool, detail: str
) -> None:
    async with store.lock:
        await store.noc_agent_run_step(int(run["id"]), kind, name, ok, detail, time.time())
        await store.commit()


def loops(store: Store, engine: Engine, app: Any) -> dict[str, Callable[[], Awaitable[None]]]:
    """The NOC's automation loops for the runner's supervisor: playbooks (ADR #463) and agents
    (ADR #467)."""
    broker = automation_broker(store, engine)
    return {
        "noc-playbooks": lambda: runbook.run_loop(store, broker),
        "noc-agents": lambda: agent_loop.agent_loop(store, claude_runner(app, store)),
    }
