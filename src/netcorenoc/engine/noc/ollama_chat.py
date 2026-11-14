"""An agent's turn on Ollama, in Ollama's own words: `POST /api/chat` (ADR #487).

The agent loop (`api/agent_openai.py`) speaks the OpenAI Chat Completions shape to every provider.
Ollama serves that shape too (`/v1/chat/completions`), and until ADR #487 the loop used it — which
is why an agent on a local model answered but did not act: **the OpenAI-compatible endpoint has no
way to set the context window**, so Ollama used its default (2 048 or 4 096 tokens, by version) and
silently dropped the start of the prompt. An agent's prompt — its instructions, the case and the
NOC's tool definitions — is several times that; what was cut was the instructions and the tools.
AnythingLLM works on the same model because it sets the window and its prompts are short.

So for Ollama the turn goes to the native API, with:

* ``options.num_ctx`` — the context window, per agent (:data:`DEFAULT_NUM_CTX` unless its admin
  chose another, :data:`CONTEXTS`);
* ``keep_alive`` — the model stays loaded between a case's turns (:data:`KEEP_ALIVE`), instead of
  being loaded again for each, which on a CPU costs more than the turn;
* the tools in Ollama's format, which is OpenAI's with the arguments as an object, not a string.

:func:`to_native` turns the loop's request into Ollama's and :func:`from_native` turns Ollama's
answer back, so the loop is the same for every provider. Pure.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

__all__ = ["CONTEXTS", "DEFAULT_NUM_CTX", "KEEP_ALIVE", "from_native", "to_native"]

#: The context window an Ollama agent gets unless its admin chose another. An agent's first turn is
#: around 6 000 tokens before the case grows; 16 384 leaves room for a dozen tool results.
DEFAULT_NUM_CTX = 16_384
#: The windows the hiring form offers. Larger costs memory on the machine Ollama runs on.
CONTEXTS = (8_192, 16_384, 32_768, 65_536)
#: How long the model stays loaded after a turn.
KEEP_ALIVE = "30m"


def _arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def to_native(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The loop's Chat Completions request as an Ollama `/api/chat` request."""
    names: dict[str, str] = {}
    messages: list[dict[str, Any]] = []
    for m in payload.get("messages") or []:
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            calls = []
            for c in m["tool_calls"]:
                fn = c.get("function") or {}
                names[str(c.get("id") or "")] = str(fn.get("name") or "")
                arguments = _arguments(fn.get("arguments"))
                calls.append({"function": {"name": fn.get("name"), "arguments": arguments}})
            content = m.get("content") or ""
            messages.append({"role": "assistant", "content": content, "tool_calls": calls})
        elif role == "tool":
            out = {"role": "tool", "content": str(m.get("content") or "")}
            name = names.get(str(m.get("tool_call_id") or ""))
            if name:
                out["tool_name"] = name
            messages.append(out)
        else:
            messages.append({"role": role, "content": str(m.get("content") or "")})
    native: dict[str, Any] = {
        "model": payload["model"],
        "messages": messages,
        "stream": False,
        "keep_alive": KEEP_ALIVE,
        "options": {"num_ctx": int(payload.get("num_ctx") or DEFAULT_NUM_CTX)},
    }
    limit = payload.get("max_tokens") or payload.get("max_completion_tokens")
    if limit:
        native["options"]["num_predict"] = int(limit)
    if payload.get("tools"):
        native["tools"] = payload["tools"]
    return native


def from_native(answer: Mapping[str, Any]) -> dict[str, Any]:
    """Ollama's `/api/chat` answer in the Chat Completions shape the loop reads."""
    message = answer.get("message") if isinstance(answer.get("message"), dict) else {}
    assert isinstance(message, dict)
    calls = []
    for i, c in enumerate(message.get("tool_calls") or []):
        fn = c.get("function") if isinstance(c, dict) else None
        if not isinstance(fn, dict) or not fn.get("name"):
            continue
        calls.append(
            {
                "id": f"call_{i}_{fn['name']}",
                "type": "function",
                "function": {
                    "name": str(fn["name"]),
                    "arguments": json.dumps(_arguments(fn.get("arguments"))),
                },
            }
        )
    out: dict[str, Any] = {"role": "assistant", "content": str(message.get("content") or "")}
    if calls:
        out["tool_calls"] = calls
    return {
        "choices": [{"message": out}],
        "usage": {
            "prompt_tokens": int(answer.get("prompt_eval_count") or 0),
            "completion_tokens": int(answer.get("eval_count") or 0),
        },
    }
