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
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

__all__ = ["MAX_BODY_BYTES", "BodyLimit"]

#: One MiB. Twice the largest body any route accepts.
MAX_BODY_BYTES = 1024 * 1024

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
