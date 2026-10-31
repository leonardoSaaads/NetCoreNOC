"""The `openai` adapter: the appliance runs an agent through any OpenAI-compatible API (ADR #471).

OpenAI, Google AI Studio (Gemini), xAI (Grok), Meta (the Llama API) and a local Ollama all serve
the Chat Completions API with function calling, each at its own base path (`providers.py`), so one
loop drives them: the system prompt and the first message as the `claude` adapter's
(`api/agent_tools.py`), the MCP tools its key may call as functions, and the tool-calling loop —
ask, run each call **in process with the agent's own key**, answer with the results, until the model
answers in words, or the run runs out of turns or time.

The provider's key is a vault credential of its provider's kind, opened here and sent only to the
host it is bound to (`engine/noc/llm_http.py`: no redirects, TLS verified); every opening is an
audit row under the agent's token. Spend is priced at what its admin set per million tokens
(ADR #472) — the provider's contract is theirs to know — so its budget stops it as a Claude
agent's does; with no price set its tokens are counted and its spend is not.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

from netcorenoc.api.agent_tools import (
    Usage,
    briefing,
    call_tool,
    first_message,
    open_keys,
    step,
    system_prompt,
    toolbox,
)
from netcorenoc.engine.noc import llm_http, providers
from netcorenoc.engine.noc.agent_loop import Outcome, Runner
from netcorenoc.engine.noc.llm_http import ProviderCallError
from netcorenoc.engine.noc.providers import Provider
from netcorenoc.store import Store

__all__ = ["openai_runner"]

#: One Chat Completions turn: (provider, base URL, key, payload, timeout) -> the answer.
Chat = Callable[..., Awaitable[dict[str, Any]]]


def _functions(tools: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": (t.description or t.title)[:1024],
                "parameters": t.schema,
            },
        }
        for t in tools.values()
    ]


def _limit(provider: Provider, max_tokens: int) -> dict[str, int]:
    """OpenAI's newer models take `max_completion_tokens`; every other provider `max_tokens`."""
    return {"max_completion_tokens" if provider.id == "openai" else "max_tokens": max_tokens}


def openai_runner(app: Any, store: Store, *, chat: Chat = llm_http.chat) -> Runner:
    """The runner the agent loop calls for an `openai` agent's run."""

    async def run_one(run: Mapping[str, Any], agent: Mapping[str, Any]) -> Outcome:
        config = json.loads(agent["adapter_config"])
        try:
            keys = await open_keys(store, agent, config, run, adapter="openai")
        except PermissionError as exc:
            return Outcome("failed", error=str(exc))
        headers, tools = await toolbox(app, keys.token)
        case = await briefing(store, run)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt(agent)},
            {"role": "user", "content": first_message(run, case)},
        ]
        functions = _functions(tools)
        model = str(config["model"])
        deadline = time.monotonic() + int(agent["run_timeout_s"])
        # ADR #472: priced at what its admin set per million tokens; unset, its tokens are counted
        # and it costs nothing. Cached prompt tokens are priced as any other: never under.
        used = Usage(model, (float(config.get("price_in", 0)), float(config.get("price_out", 0))))
        ended = used.outcome

        for _turn in range(int(config.get("max_turns", 20))):
            left = deadline - time.monotonic()
            if left <= 0:
                return ended("timed_out", error="past the agent's time limit")
            if used.over_budget(agent):
                return ended("failed", error="its monthly budget is spent")
            payload: dict[str, Any] = {
                "model": model,
                "messages": messages,
                **_limit(keys.provider, int(config.get("max_tokens", 16000))),
            }
            if functions:
                payload |= {"tools": functions, "tool_choice": "auto"}
            try:
                answer = await chat(
                    keys.provider, keys.base_url, keys.api_key, payload, timeout=left
                )
            except ProviderCallError as exc:
                # ADR #478: a local Ollama at 127.0.0.1, called from a container, says why.
                hint = providers.loopback_hint(urlsplit(keys.base_url).hostname or "")
                return ended("failed", error=f"{keys.provider.label}: {exc}{hint}"[:500])
            usage = answer.get("usage") or {}
            used.fresh += int(usage.get("prompt_tokens") or 0)
            used.output += int(usage.get("completion_tokens") or 0)
            choices = answer.get("choices") or []
            message = (
                choices[0].get("message") if choices and isinstance(choices[0], dict) else None
            )
            if not isinstance(message, dict):
                return ended("failed", error=f"{keys.provider.label} answered with no message")
            calls = [c for c in message.get("tool_calls") or [] if isinstance(c, dict)]
            text = str(message.get("content") or "").strip()
            if not calls:
                await step(store, run, "text", "summary", True, text[:4000])
                return ended("succeeded", summary=text[:8000] or "(no summary)")
            messages.append({"role": "assistant", "content": text or None, "tool_calls": calls})
            for call in calls:
                function = call.get("function") or {}
                name = str(function.get("name") or "")
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                except ValueError:
                    arguments = None
                if not isinstance(arguments, dict):
                    result, _failed = f"the arguments to {name!r} are not a JSON object", True
                else:
                    result, _failed = await call_tool(
                        app, store, run, tools, name, arguments, headers
                    )
                messages.append(
                    {"role": "tool", "tool_call_id": str(call.get("id") or ""), "content": result}
                )
        return ended("failed", error="out of turns before it finished")

    return run_one
