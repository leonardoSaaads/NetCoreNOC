"""Privilege escalation through account management (ADR #453).

`users.manage` and `tokens.manage` may be held by a narrowed admin token — an automation that
provisions accounts, say. Everything that would turn such a token into more than it holds is
refused: creating an admin user or token, promoting someone to admin, and taking over an admin
account through its password or the address its reset link goes to.
"""

from __future__ import annotations

from netcorenoc.store import Store

import authutil

NARROWED = ["self.read", "users.manage", "tokens.manage"]


async def test_a_narrowed_admin_token_cannot_reach_past_what_it_holds(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")
    try:
        made = await admin.post("/api/tokens", json={"name": "provisioner", "role": "admin"})
        assert made.status_code == 200, made.text
        tid, value = made.json()["id"], made.json()["token"]
        narrowed = await admin.post(
            "/api/rbac/subject", json={"subject": f"token:{tid}", "capabilities": NARROWED}
        )
        assert narrowed.status_code == 200, narrowed.text
        users = {u["username"]: u["id"] for u in (await admin.get("/api/users")).json()}
    finally:
        await admin.aclose()
    bot = authutil.new_client(app)
    bot.headers["Authorization"] = f"Bearer {value}"
    try:
        assert set((await bot.get("/api/me")).json()["capabilities"]) == set(NARROWED)
        attempts = [
            ("POST", "/api/users", {"username": "mallory", "password": "x" * 14, "role": "admin"}),
            ("POST", "/api/tokens", {"name": "full", "role": "admin"}),
            ("POST", f"/api/users/{users['vwr']}/role", {"role": "admin"}),
            ("POST", f"/api/users/{users['adm']}/role", {"role": "viewer"}),
            ("POST", f"/api/users/{users['adm']}/password-reset", {}),
            ("POST", f"/api/users/{users['adm']}/profile", {"email": "m@evil.example"}),
        ]
        for method, path, body in attempts:
            refused = await bot.request(method, path, json=body)
            assert refused.status_code == 403, (path, refused.status_code, refused.text)
            assert "more access than you hold" in refused.text, path
        # What it was narrowed to, it still does.
        assert (await bot.get("/api/users")).status_code == 200
        assert (await bot.get("/api/tokens")).status_code == 200
    finally:
        await bot.aclose()


async def test_a_full_admin_meets_no_ceiling(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")
    try:
        created = await admin.post(
            "/api/users", json={"username": "ops2", "password": "x" * 14, "role": "admin"}
        )
        assert created.status_code == 200, created.text
        token = await admin.post("/api/tokens", json={"name": "full", "role": "admin"})
        assert token.status_code == 200, token.text
    finally:
        await admin.aclose()
