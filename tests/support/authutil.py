"""Shared helpers for the v0.2.0 auth/RBAC/audit/findings tests."""

from __future__ import annotations

import asyncio
import time

import httpx

from netcorenoc.api import create_app
from netcorenoc.crosscutting import administration, auth, totp
from netcorenoc.ingest.receiver import QueueItem
from netcorenoc.main import Engine
from netcorenoc.store import Store

PW = "correct horse battery staple"  # 28 chars, satisfies the 12-char policy
ROLE_USER = {"viewer": "vwr", "editor": "edt", "admin": "adm"}
ORIGIN = "http://netcorenoc.test"
#: The authenticator secret every ready-made account is enrolled with (ADR #482), and the code it
#: shows at the suite's frozen step (`conftest.FROZEN_TOTP_STEP`): what a step-up body carries.
TOTP_SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"


def code(secret: str = TOTP_SECRET) -> str:
    """The code ``secret`` shows now — at the suite's frozen step, unless the test is marked
    ``real_authenticator``."""
    return totp.code_at(secret, totp.step_at(time.time()))


#: The 30-second step every authenticator in the suite lives at (`conftest._frozen_authenticator`).
FROZEN_TOTP_STEP = 59_091_840
OTP = totp.code_at(TOTP_SECRET, FROZEN_TOTP_STEP)


async def make_users(store: Store) -> None:
    """Bootstrap admin (forced-change) plus ready-to-use admin/editor/viewer accounts, each with an
    authenticator already enrolled (:data:`TOTP_SECRET`, ADR #482)."""
    async with store.lock:
        await administration.bootstrap_admin(store, 0.0)
        for role, name in ROLE_USER.items():
            uid = await store.create_user(name, auth.hash_password(PW), role, False, 0.0)
            await store.enroll_second_factor(uid, TOTP_SECRET, 0, [], 0.0)
        await store.commit()


async def enroll(store: Store, username: str, secret: str = TOTP_SECRET) -> None:
    """Give an account made by a test an authenticator, as signing in would."""
    async with store.lock:
        user = await store.get_user_by_name(username)
        assert user is not None, username
        await store.enroll_second_factor(int(user["id"]), secret, 0, [], 0.0)
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


async def sign_in(
    client: httpx.AsyncClient, username: str, password: str = PW, secret: str = TOTP_SECRET
) -> httpx.Response:
    """Both steps of signing in (ADR #482): the password, then the authenticator's code. Returns
    the password step's response when it is refused, else the code step's."""
    first = await client.post("/api/login", json={"username": username, "password": password})
    if first.status_code != 200 or "challenge" not in first.json():
        return first
    return await client.post(
        "/api/login/code", json={"challenge": first.json()["challenge"], "code": code(secret)}
    )


async def login(client: httpx.AsyncClient, role: str, password: str = PW) -> httpx.Response:
    return await sign_in(client, ROLE_USER[role], password)


async def client_as(app: object, role: str | None) -> httpx.AsyncClient:
    """A client authenticated as the given role (None = anonymous)."""
    client = new_client(app)
    if role is not None:
        resp = await login(client, role)
        assert resp.status_code == 200, (role, resp.status_code, resp.text)
    return client
