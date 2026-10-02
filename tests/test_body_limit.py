"""No route reads a request body over 1 MiB (v0.28.0, `api/body_limit.py`).

Before it, the unauthenticated `POST /api/login` parsed whatever it was sent: a handful of
concurrent multi-hundred-MiB posts reached the container's memory limit and the kernel killed the
appliance, with the traps in flight lost and no ingest gap recorded.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from netcorenoc.api.body_limit import MAX_BODY_BYTES
from netcorenoc.store import Store

import authutil


async def test_a_declared_oversized_body_is_refused_before_it_is_read(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    async with authutil.new_client(app) as client:
        big = b'{"username": "admin", "password": "' + b"x" * (MAX_BODY_BYTES + 1) + b'"}'
        resp = await client.post(
            "/api/login", content=big, headers={"Content-Type": "application/json"}
        )
    assert resp.status_code == 413, resp.text
    assert resp.headers["X-Content-Type-Options"] == "nosniff", "a 413 left the perimeter bare"


async def test_a_streamed_body_is_cut_off_at_the_ceiling(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)

    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(40):
            yield b"x" * 65536  # 2.5 MiB in all, with no Content-Length

    async with authutil.new_client(app) as client:
        resp = await client.post("/api/login", content=chunks())
    assert resp.status_code == 413


async def test_an_ordinary_body_is_untouched(store: Store) -> None:
    """The control: the ceiling changes no answer a real client gets."""
    _engine, _queue, app = await authutil.make_env(store)
    async with authutil.new_client(app) as client:
        resp = await authutil.login(client, "viewer")
    assert resp.status_code == 200, resp.text
