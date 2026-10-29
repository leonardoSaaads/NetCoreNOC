"""Talking to an AI provider's HTTP API: its model list, and one OpenAI-compatible turn (ADR #471).

The standard library's `urllib`, in a worker thread, so the appliance needs no SDK for the
providers the `openai` adapter drives (Anthropic keeps its own, the optional `agents` extra). What
makes it safe to hand a key to:

* **No redirect is followed.** A key goes to the host its credential is bound to and nowhere else;
  urllib would otherwise replay the `Authorization` header to wherever a 3xx pointed.
* **TLS is verified** (the default context) whenever the scheme is HTTPS; only `http` and `https`
  are spoken at all.
* **The key travels in a header**, never in the URL, and no error carries the request: a refusal
  says the status and the provider's own message, cut short.
* **What comes back is bounded** (`MAX_BODY`) and must be a JSON object.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit

from netcorenoc.engine.noc.providers import Provider

__all__ = ["MAX_BODY", "ProviderCallError", "chat", "headers_for", "list_models"]

#: The most of one answer read: a chat turn with tool calls is kilobytes, a model list less.
MAX_BODY = 4 * 1024 * 1024
#: Anthropic's API version header, as its SDK sends it.
ANTHROPIC_VERSION = "2023-06-01"


class ProviderCallError(RuntimeError):
    """A provider that did not answer as asked: unreachable, refused, or not JSON."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        raise ProviderCallError("the provider answered with a redirect; it is not followed")


_OPENER = urllib.request.build_opener(_NoRedirect())


def headers_for(provider: Provider, key: str) -> dict[str, str]:
    """The provider's authentication headers for ``key`` (none for a keyless Ollama)."""
    if not key:
        return {}
    if provider.adapter == "claude":
        return {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}
    return {"Authorization": f"Bearer {key}"}


def _message(raw: bytes) -> str:
    """The provider's own reason, from an OpenAI- or Anthropic-shaped error body."""
    try:
        body = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return raw.decode("utf-8", "replace")[:200]
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or error.get("type") or "")[:300]
    return str(error or body)[:300]


def _call(url: str, headers: dict[str, str], payload: Any, timeout: float) -> dict[str, Any]:
    if urlsplit(url).scheme not in ("http", "https"):
        raise ProviderCallError("only http:// and https:// are spoken")
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(
        url,
        data=data,
        method="GET" if data is None else "POST",
        headers={"Accept": "application/json", **headers}
        | ({"Content-Type": "application/json"} if data is not None else {}),
    )
    try:
        with _OPENER.open(request, timeout=timeout) as answer:  # nosec B310
            raw = answer.read(MAX_BODY + 1)
    except urllib.error.HTTPError as exc:
        raise ProviderCallError(f"HTTP {exc.code}: {_message(exc.read(64 * 1024))}") from None
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise ProviderCallError(f"unreachable: {reason}") from None
    if len(raw) > MAX_BODY:
        raise ProviderCallError("the answer is larger than this appliance reads")
    try:
        body = json.loads(raw)
    except ValueError:
        raise ProviderCallError("the answer is not JSON") from None
    if not isinstance(body, dict):
        raise ProviderCallError("the answer is not a JSON object")
    return body


async def list_models(
    provider: Provider, base: str, key: str, *, timeout: float = 15.0
) -> list[str]:
    """The model ids the provider serves this key, sorted (`GET {base}/models`)."""
    body = await asyncio.to_thread(
        _call, f"{base}/models", headers_for(provider, key), None, timeout
    )
    rows = body.get("data")
    if not isinstance(rows, list):
        raise ProviderCallError("the model list has no `data`")
    return sorted({str(r["id"]) for r in rows if isinstance(r, dict) and r.get("id")})


async def chat(
    provider: Provider, base: str, key: str, payload: dict[str, Any], *, timeout: float = 600.0
) -> dict[str, Any]:
    """One Chat Completions turn (`POST {base}/chat/completions`)."""
    return await asyncio.to_thread(
        _call, f"{base}/chat/completions", headers_for(provider, key), payload, timeout
    )
