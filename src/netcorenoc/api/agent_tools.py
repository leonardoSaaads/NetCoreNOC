"""What both in-process adapters share: the keys, the tools, the first words, the steps (ADRs #467,
#471).

The appliance runs an agent itself through one of two loops — `claude` (`agent_runtime.py`, the
Anthropic SDK) and `openai` (`agent_openai.py`, any OpenAI-compatible API: OpenAI, Google AI
Studio, xAI, Meta, Ollama). Everything that is not the provider's wire format is here, once:

* **The two keys**, opened in this process and nowhere else: the agent's own token, sealed when it
  was hired, and its provider's API key — a vault credential a custodian let agents use, of its
  provider's kind (`providers.py`; a Claude agent also takes an older generic API token). **Every
  opening of a provider key is an audit row** (`noc.secret.use`) under the agent's token, naming
  the credential and the run: who stored the key, who read it, and which bot used it are three rows
  of the same log.
* **The tools**: the MCP tools its key may call, each called in process with that key
  (`api/mcp.py`), so authorized, scoped, audited and recorded as a step of the run.
* **The system prompt** (its manager's instructions, then how the NOC works here) and **the first
  message** (why it was woken).
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from netcorenoc.api import asgi_call, mcp, mcp_tools
from netcorenoc.api.agent_hire import binding
from netcorenoc.crosscutting import audit, secretbox
from netcorenoc.engine.noc import agents, kinds, providers
from netcorenoc.engine.noc.providers import Provider
from netcorenoc.store import Store

__all__ = [
    "APPLIANCE",
    "MAX_RESULT",
    "Keys",
    "call_tool",
    "first_message",
    "open_keys",
    "step",
    "system_prompt",
    "toolbox",
]

#: Where an in-process call says it came from.
APPLIANCE = ("127.0.0.1", 0)
#: The most of one tool result the model is sent; the rest is counted, not shown.
MAX_RESULT = 20_000
#: The credential kinds each in-process adapter takes its key from.
KEY_KINDS: dict[str, frozenset[str]] = {
    "claude": frozenset({"api_token", providers.PROVIDERS["anthropic"].kind}),
    "openai": frozenset(p.kind for p in providers.PROVIDERS.values() if p.adapter == "openai"),
}


@dataclass(frozen=True)
class Keys:
    """The agent's own token, its provider's key (empty for a keyless Ollama), and where to call."""

    token: str
    api_key: str
    provider: Provider
    base_url: str


async def open_keys(
    store: Store,
    agent: Mapping[str, Any],
    config: Mapping[str, Any],
    run: Mapping[str, Any],
    *,
    adapter: str,
) -> Keys:
    """Open both keys for ``run``, and write the audit row saying the agent used its provider's.
    Refuses with the sentence the run fails with when either is gone."""
    async with store.reading():
        sealed = await store.noc_agent_sealed(int(agent["id"]))
        secret = await store.noc_get_secret(int(config["secret_id"]), sealed=True)
    if not sealed or not sealed.get("token_sealed"):
        raise PermissionError("the agent has no key the appliance can act with: give it a new key")
    if secret is None or secret["kind"] not in KEY_KINDS[adapter] or not secret["agent_access"]:
        raise PermissionError("its provider's API key is gone, or agents may no longer use it")
    provider = providers.by_kind(str(secret["kind"])) or providers.PROVIDERS["anthropic"]
    wanted = config.get("provider")
    if wanted is not None and wanted != provider.id:
        raise PermissionError(f"its credential is not a {wanted} key any more")
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
    api_key = str(fields.get("api_key") or fields.get("token") or "")
    if provider.key_required and not api_key:
        raise PermissionError(f"its {provider.label} credential holds no API key")
    async with store.lock:
        await audit.write_event(
            store,
            ts=time.time(),
            actor=f"token:{agent['token_id']}" if agent.get("token_id") else f"agent:{agent['id']}",
            role=None,
            source_ip=None,
            action="noc.secret.use",
            outcome="ok",
            object_type="noc_secret",
            object_id=str(secret["id"]),
            details={"agent": int(agent["id"]), "run": int(run["id"]), "provider": provider.id},
        )
        await store.commit()
    base = providers.base_url(
        provider, str(secret["host"]), int(secret["port"]), str(secret["protocol_version"])
    )
    return Keys(token, api_key, provider, base)


async def toolbox(app: Any, token: str) -> tuple[list[tuple[str, str]], dict[str, mcp_tools.Tool]]:
    """The agent's headers and the tools its key may call (all but finishing its own run: the loop
    ends the run when the model ends its turn)."""
    headers = [("authorization", f"Bearer {token}")]
    me = await asgi_call.call(app, "GET", "/api/me", headers=headers, client=APPLIANCE)
    allowed = set(me.body.get("capabilities", [])) if me.ok and isinstance(me.body, dict) else set()
    tools = {
        t.name: t
        for t in mcp_tools.build(app.openapi()).values()
        if t.capability in allowed and t.name != "noc_finish_run"
    }
    return headers, tools


def system_prompt(agent: Mapping[str, Any]) -> str:
    own = str(agent["instructions"]).strip()
    guide = mcp.GUIDE.replace("8. End your run with `noc_finish_run`", "8. End your turn")
    return f"{own}\n\n{guide}" if own else guide


def first_message(run: Mapping[str, Any], case: Mapping[str, Any] | None) -> str:
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


async def step(
    store: Store, run: Mapping[str, Any], kind: str, name: str, ok: bool, detail: str
) -> None:
    async with store.lock:
        await store.noc_agent_run_step(int(run["id"]), kind, name, ok, detail, time.time())
        await store.commit()


async def call_tool(
    app: Any,
    store: Store,
    run: Mapping[str, Any],
    tools: Mapping[str, mcp_tools.Tool],
    name: str,
    arguments: Mapping[str, Any],
    headers: list[tuple[str, str]],
) -> tuple[str, bool]:
    """Run one tool the model asked for, as a step of the run: its text, and whether it failed."""
    tool = tools.get(name)
    if tool is None:
        return f"no tool {name!r}", True
    answer, out = await mcp.run_tool(app, tool, arguments, headers, APPLIANCE)
    outcome = str(answer.status) + ("" if answer.ok else f": {answer.detail()[:300]}")
    await step(store, run, "tool", tool.name, answer.ok, f"{mcp.redact(arguments)} → {outcome}")
    text = "\n".join(c["text"] for c in out["content"])
    if len(text) > MAX_RESULT:
        text = text[:MAX_RESULT] + f"… ({len(text) - MAX_RESULT} more characters)"
    return text, bool(out["isError"])
