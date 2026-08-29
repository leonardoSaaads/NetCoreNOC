"""Shared helpers for the v0.2.0 auth/RBAC/audit/findings tests."""

from __future__ import annotations

import asyncio

import httpx

from netcorenoc.api import create_app
from netcorenoc.crosscutting import administration, auth
from netcorenoc.ingest.receiver import QueueItem
from netcorenoc.main import Engine
from netcorenoc.store import Store

PW = "correct horse battery staple"  # 28 chars, satisfies the 12-char policy
ROLE_USER = {"viewer": "vwr", "editor": "edt", "admin": "adm"}
ORIGIN = "http://netcorenoc.test"


async def make_users(store: Store) -> None:
    """Bootstrap admin (forced-change) plus ready-to-use admin/editor/viewer accounts."""
    async with store.lock:
        await administration.bootstrap_admin(store, 0.0)
        for role, name in ROLE_USER.items():
            await store.create_user(name, auth.hash_password(PW), role, False, 0.0)
        await store.commit()


async def make_env(
    store: Store, rate_capacity: float = 100000.0, **kwargs: object
) -> tuple[Engine, asyncio.Queue[QueueItem], object]:
    queue: asyncio.Queue[QueueItem] = asyncio.Queue()
    engine = Engine(store, queue)
    await engine.start()
    await make_users(store)
    app = create_app(engine, rate_capacity=rate_capacity, **kwargs)  # type: ignore[arg-type]
    return engine, queue, app


def new_client(app: object) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    return httpx.AsyncClient(
        transport=transport,
        base_url=ORIGIN,
        headers={"X-NetCoreNOC-Client": "ui", "Origin": ORIGIN},
    )


async def login(client: httpx.AsyncClient, role: str, password: str = PW) -> httpx.Response:
    return await client.post("/api/login", json={"username": ROLE_USER[role], "password": password})


async def client_as(app: object, role: str | None) -> httpx.AsyncClient:
    """A client authenticated as the given role (None = anonymous)."""
    client = new_client(app)
    if role is not None:
        resp = await login(client, role)
        assert resp.status_code == 200, (role, resp.status_code, resp.text)
    return client
