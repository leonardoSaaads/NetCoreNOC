"""A ceiling on every request body, enforced before any route reads one (v0.28.0).

The two upload routes already capped their own streams (`people.py`, `catalogue.py`), but every
other route parsed its JSON body through FastAPI, which reads the **whole** body into memory first
— including the unauthenticated `POST /api/login`. A few concurrent posts of a few hundred MiB
reached the compose file's 512 MiB limit, the kernel killed the process, and every trap in flight
was lost with no ingest gap recorded (`docker-compose.yml` says why that is the one loss this
appliance cannot account for).

The ceiling is far above anything the console sends — the largest legitimate body is a trap-list
import, capped at about 0.5 MiB by its own route — so it changes no answer a real client gets.
A declared `Content-Length` over it is refused at once; a chunked body is counted as it arrives
and refused the moment it passes it, never buffered beyond it.

**One route takes more** (ADR #473): a customer's document, up to 12 MiB (`WIDER`). Its body is not
buffered here but passed through as it arrives, counted, so the route's own authentication runs
before any of it is read — an anonymous request is refused without its body ever being held — and
the route reads it under its own cap; past `WIDER`'s, the stream ends as a disconnect.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

__all__ = ["MAX_BODY_BYTES", "WIDER", "BodyLimit"]

#: One MiB. Twice the largest body any route accepts.
MAX_BODY_BYTES = 1024 * 1024
#: The routes that read a larger body themselves, under their own cap: method, path, ceiling.
WIDER: tuple[tuple[str, re.Pattern[str], int], ...] = (
    ("POST", re.compile(r"^/api/noc/customers/[0-9]{1,10}/docs$"), 13 * 1024 * 1024),
)

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class BodyLimit:
    """Pure ASGI middleware: 413 for a body over `limit` bytes, before the app sees any of it."""

    def __init__(self, app: ASGIApp, limit: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = _content_length(scope)
        wider = _wider(scope)
        if wider is not None:
            if declared is not None and declared > wider:
                await _too_large(send, wider)
                return
            await self.app(scope, _counted(receive, wider), send)
            return
        if declared is not None and declared > self.limit:
            await _too_large(send, self.limit)
            return
        messages: list[Message] = []
        size = 0
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] != "http.request":
                break
            size += len(message.get("body", b""))
            if size > self.limit:
                await _too_large(send, self.limit)
                return
            if not message.get("more_body", False):
                break
        replay = iter(messages)

        async def replayed() -> Message:
            return next(replay, None) or await receive()

        await self.app(scope, replayed, send)


def _wider(scope: Scope) -> int | None:
    for method, path, limit in WIDER:
        if scope.get("method") == method and path.fullmatch(str(scope.get("path", ""))):
            return limit
    return None


def _counted(receive: Receive, limit: int) -> Receive:
    """``receive``, passed through and counted: past ``limit`` the client is said to be gone."""
    size = 0

    async def counted() -> Message:
        nonlocal size
        message = await receive()
        if message["type"] == "http.request":
            size += len(message.get("body", b""))
            if size > limit:
                return {"type": "http.disconnect"}
        return message

    return counted


def _content_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", ()):
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


async def _too_large(send: Send, limit: int) -> None:
    body = json.dumps({"detail": f"request body over {limit // 1024} KiB"}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"connection", b"close"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
