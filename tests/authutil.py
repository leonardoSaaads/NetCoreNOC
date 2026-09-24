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


async def make_token(store: Store, name: str, role: str) -> str:
    """Issue a service token and return its plaintext value (v0.21.0).

    **The one way a test authenticates as an agent.** Phase 2 reaches this appliance with a bearer
    token, and two behaviours turn on `Principal.is_token`: an agent-created maintenance window is
    marked, and an agent may not confirm one over six hours. A test that faked the principal
    instead of presenting a credential would assert against its own mock.
    """
    value = f"tok-{name}-{'z' * 32}"
    async with store.lock:
        await store.create_token(auth.hash_token(value), name, role, "adm", 0.0)
        await store.commit()
    return value


async def set_scope(store: Store, document: dict[str, object]) -> None:
    """Install a visibility-scope policy, through the same rows the API writes.

    Written here rather than in each test for the reason `make_users` is: the two-step insert and
    activate is the shape `routes/governance.py` uses, and a test that wrote the rows differently
    would be exercising a path the product does not have.
    """
    import json

    text = json.dumps({"version": 1, **document}, sort_keys=True, separators=(",", ":"))
    async with store.lock:
        policy_id = await store.insert_governance_policy("scope", text, "x" * 64, None, 0.0, "")
        await store.set_active_governance_policy("scope", policy_id, None, 0.0)
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
