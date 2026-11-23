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

from netcorenoc.engine.noc import ollama, ollama_chat
from netcorenoc.engine.noc.providers import Provider
from netcorenoc.engine.noc.providers import api_root as _api_root

__all__ = [
    "MAX_BODY",
    "ProviderCallError",
    "chat",
    "headers_for",
    "list_models",
    "ollama_models",
    "ollama_probe",
]

#: The most models `ollama_models` asks `/api/show` about, and how many at once.
SHOW_AT_MOST = 40
SHOW_AT_ONCE = 4

#: The most of one answer read: a chat turn with tool calls is kilobytes, a model list less.
MAX_BODY = 4 * 1024 * 1024
#: Anthropic's API version header, as its SDK sends it.
ANTHROPIC_VERSION = "2023-06-01"


#: Statuses a provider answers when it is busy rather than when the request is wrong: a chat turn
#: is tried again after a pause (ADR #505). The lab met it: Gemini's free tier answered 503
#: "high demand" in the middle of a case, and the run ended with the case half worked.
TRANSIENT = frozenset({429, 500, 502, 503, 504})
#: The pauses before each retry, in seconds; a `Retry-After` the provider sends wins, up to the cap.
RETRY_DELAYS_S = (2.0, 6.0, 15.0)
RETRY_AFTER_CAP_S = 30.0


class ProviderCallError(RuntimeError):
    """A provider that did not answer as asked: unreachable, refused, or not JSON. `status` is the
    HTTP status when there was one, and `retry_after` the provider's own `Retry-After`."""

    def __init__(
        self, message: str, *, status: int | None = None, retry_after: float | None = None
    ):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


def _retry_after(value: str | None) -> float | None:
    try:
        return max(0.0, float(value)) if value else None
    except ValueError:
        return None  # an HTTP date is not worth parsing for a pause capped at half a minute


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
    # Gemini's OpenAI-compatible API answers a refusal as a list of one error object.
    if isinstance(body, list) and body and isinstance(body[0], dict):
        body = body[0]
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
        raise ProviderCallError(
            f"HTTP {exc.code}: {_message(exc.read(64 * 1024))}",
            status=exc.code,
            retry_after=_retry_after(exc.headers.get("Retry-After") if exc.headers else None),
        ) from None
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


async def ollama_models(
    provider: Provider, root: str, key: str, *, timeout: float = 15.0
) -> list[dict[str, Any]]:
    """What an Ollama server has, ranked (`engine/noc/ollama.py`): its models (`GET /api/tags`),
    what each can do (`POST /api/show`, at most `SHOW_AT_MOST`) and, on one's own machine, which
    are loaded now (`GET /api/ps`). A model `/api/show` cannot describe is listed without it."""
    headers = headers_for(provider, key)
    tags = await asyncio.to_thread(_call, f"{root}/api/tags", headers, None, timeout)
    if not isinstance(tags.get("models"), list):
        raise ProviderCallError("the model list has no `models`")
    rows = [r for r in tags["models"][:500] if isinstance(r, dict)]
    loaded: set[str] = set()
    if provider.own_host:
        try:
            ps = await asyncio.to_thread(_call, f"{root}/api/ps", headers, None, timeout)
            loaded = {
                str(m.get("model") or m.get("name"))
                for m in ps.get("models") or []
                if isinstance(m, dict)
            }
        except ProviderCallError:
            pass  # which are loaded is a nicety; the list stands without it
    gate = asyncio.Semaphore(SHOW_AT_ONCE)

    async def show(name: str) -> dict[str, Any] | None:
        async with gate:
            try:
                return await asyncio.to_thread(
                    _call, f"{root}/api/show", headers, {"model": name}, timeout
                )
            except ProviderCallError:
                return None

    names = [str(r.get("model") or r.get("name") or "") for r in rows]
    asked = [n for n in names if n][:SHOW_AT_MOST]
    found = dict(zip(asked, await asyncio.gather(*(show(n) for n in asked)), strict=True))
    return ollama.rank(
        [ollama.row(r, found.get(n), n in loaded) for r, n in zip(rows, names, strict=True)]
    )


async def chat(
    provider: Provider, base: str, key: str, payload: dict[str, Any], *, timeout: float = 600.0
) -> dict[str, Any]:
    """One Chat Completions turn (`POST {base}/chat/completions`) — or, for Ollama, one turn of
    its native `POST /api/chat`, which takes a context window (ADR #487, `ollama_chat.py`)."""
    if provider.ollama:
        root = base[: -len(provider.base_path)] if base.endswith(provider.base_path) else base
        answer = await _patiently(
            _call,
            f"{root}/api/chat",
            headers_for(provider, key),
            ollama_chat.to_native(payload),
            timeout,
        )
        return ollama_chat.from_native(answer)
    return await _patiently(
        _call, f"{base}/chat/completions", headers_for(provider, key), payload, timeout
    )


async def _patiently(call: Any, *args: Any) -> dict[str, Any]:
    """`call` in a worker thread, tried again after a pause while the provider says it is busy
    (`TRANSIENT`); any other refusal, and the last busy one, is raised as it came."""
    for pause in (*RETRY_DELAYS_S, None):
        try:
            return await asyncio.to_thread(call, *args)
        except ProviderCallError as exc:
            if pause is None or exc.status not in TRANSIENT:
                raise
            wait = min(exc.retry_after, RETRY_AFTER_CAP_S) if exc.retry_after is not None else pause
            await _sleep(wait)
    raise AssertionError("unreachable")  # pragma: no cover - the loop returns or raises


#: The pause, by name, so a test can take the waiting out of a retry.
_sleep = asyncio.sleep


async def ollama_probe(
    candidates: list[tuple[str, int]], *, timeout: float = 1.5
) -> list[dict[str, Any]]:
    """Which of ``candidates`` answers as an Ollama server (ADR #487): each one's version and the
    models pulled there. The candidates are the appliance's own fixed list
    (`providers.ollama_candidates`), never an address a caller typed."""

    async def one(host: str, port: int) -> dict[str, Any] | None:
        root = _api_root(host, port, "HTTP")
        try:
            version = await asyncio.to_thread(_call, f"{root}/api/version", {}, None, timeout)
            tags = await asyncio.to_thread(_call, f"{root}/api/tags", {}, None, timeout)
        except ProviderCallError:
            return None
        models = [
            str(m.get("model") or m.get("name") or "")[:200]
            for m in (tags.get("models") or [])
            if isinstance(m, dict)
        ]
        return {
            "host": host,
            "port": port,
            "version": str(version.get("version") or "")[:32],
            "models": [m for m in models if m][:200],
        }

    found = await asyncio.gather(*(one(h, p) for h, p in candidates))
    return [f for f in found if f is not None]
