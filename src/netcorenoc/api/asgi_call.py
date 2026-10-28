"""One request to this appliance's own API, in process (ADR #466).

An MCP tool and a step of the `claude` agent loop are each a route with a different envelope: they
reach the route the way a client would — through the perimeter, the declaration gate, RBAC, scope,
the write transaction and the audit row — by calling the ASGI application directly, with the
caller's own credentials. Nothing is reimplemented and nothing is bypassed: a tool can do exactly
what its caller could do with `curl`, and is recorded exactly as that would be.

No HTTP client is needed (the runtime has none): the request is an ASGI scope, its body one
message, its answer collected from the messages the application sends.

**One request, one turn of the rate limit.** A tool call is the MCP request that carries it, which
the perimeter already counted; the scope is marked (`IN_PROCESS`, a key no HTTP client can set) so
the route it reaches does not count it a second time. Counted twice, an agent making ten calls in a
second ran out of a 30-request bucket mid-case (the owner's test round, ADR #469).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterable, Mapping, MutableMapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode

__all__ = ["IN_PROCESS", "Answer", "call", "path_for"]

#: The scope key marking a request this appliance made to itself.
IN_PROCESS = "netcorenoc.in_process"

Scope = MutableMapping[str, Any]
ASGIApp = Callable[
    [Scope, Callable[[], Awaitable[dict[str, Any]]], Callable[[dict[str, Any]], Awaitable[None]]],
    Awaitable[None],
]


@dataclass(frozen=True)
class Answer:
    status: int
    body: Any  # the decoded JSON, or the text when it was not JSON, or None when empty

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def detail(self) -> str:
        """What a refusal said, as a sentence."""
        if isinstance(self.body, Mapping) and "detail" in self.body:
            detail = self.body["detail"]
            return detail if isinstance(detail, str) else json.dumps(detail)[:2000]
        return str(self.body)[:2000]


def path_for(template: str, values: Mapping[str, Any]) -> str:
    """``/api/noc/cases/{sid}`` with each placeholder filled and percent-encoded."""
    out = template
    for name, value in values.items():
        out = out.replace("{" + name + "}", quote(str(value), safe=""))
    return out


async def call(
    app: Any,
    method: str,
    path: str,
    *,
    headers: Iterable[tuple[str, str]],
    query: Mapping[str, Any] | None = None,
    body: Any = None,
    client: tuple[str, int] = ("127.0.0.1", 0),
) -> Answer:
    """Send one request to ``app`` and collect its answer."""
    payload = b"" if body is None else json.dumps(body).encode()
    raw = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers]
    if body is not None:
        raw.append((b"content-type", b"application/json"))
        raw.append((b"content-length", str(len(payload)).encode()))
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": urlencode(
            {k: str(v).lower() if isinstance(v, bool) else v for k, v in (query or {}).items()}
        ).encode(),
        "headers": raw,
        "client": client,
        "server": ("netcorenoc.internal", 80),
        IN_PROCESS: True,
    }
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": payload, "more_body": False}

    status = 500
    chunks: list[bytes] = []

    async def send(message: dict[str, Any]) -> None:
        nonlocal status
        if message["type"] == "http.response.start":
            status = int(message["status"])
        elif message["type"] == "http.response.body":
            chunks.append(bytes(message.get("body", b"")))

    target: ASGIApp = app
    await target(scope, receive, send)
    text = b"".join(chunks).decode("utf-8", errors="replace")
    if not text:
        return Answer(status, None)
    try:
        return Answer(status, json.loads(text))
    except json.JSONDecodeError:
        return Answer(status, text)
