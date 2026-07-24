"""Phase 4.1 — authorization matrix and fail-closed enforcement.

Expectations are generated from `rbac.py` (the single source), so the test cannot drift
from the enforcement it checks: every registered `/api` route is exercised for every
role, anonymous included, and the outcome must match `role_allows`. A route missing from
the map fails the suite (deny-by-default), and 403 is proven to precede 404 (no oracle).
"""

from __future__ import annotations

import asyncio

import httpx

from netcorenoc import api, audit, rbac
from netcorenoc.store import Store

import authutil

# Concrete substitutions for templated path params (ids chosen to not exist).
PARAMS = {"{sid}": "999999", "{uid}": "999999", "{tid}": "999999"}
# Minimal valid bodies so business validation never masks the authorization outcome.
BODIES: dict[tuple[str, str], dict[str, object]] = {
    ("POST", "/api/password"): {"old_password": "x" * 12, "new_password": "y" * 12},
    ("POST", "/api/situations/{sid}/feedback"): {"verdict": "confirm"},
    ("POST", "/api/labels"): {"kind": "device", "id": 1, "label": "x"},
    ("POST", "/api/users"): {"username": "probe", "password": "probe-pw-1234", "role": "viewer"},
    ("POST", "/api/users/{uid}/role"): {"role": "viewer"},
    ("POST", "/api/tokens"): {"name": "probe", "role": "viewer"},
    ("POST", "/api/config"): {"allowlist": "", "retention_days": 7},
}


def _concrete(path: str) -> str:
    for token, value in PARAMS.items():
        path = path.replace(token, value)
    return path


async def _status(client: httpx.AsyncClient, method: str, path: str, key: tuple[str, str]) -> int:
    if path == "/api/events":
        # The SSE stream never ends; the security dependency raises 401/403 *before*
        # streaming, so an authorized caller hangs (no socket timeout under ASGITransport).
        # A timeout therefore means "authorization passed" — report it as 200.
        try:
            resp = await asyncio.wait_for(client.get(_concrete(path)), timeout=0.6)
            return resp.status_code
        except (TimeoutError, httpx.ReadError):
            return 200
    async with client.stream(method, _concrete(path), json=BODIES.get(key)) as resp:
        return resp.status_code


async def test_authorization_matrix(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    failures: list[str] = []
    for (method, path), permission in rbac.ROUTE_PERMISSIONS.items():
        key = (method, path)
        for role in (None, "viewer", "editor", "admin"):
            # A fresh login per case: some routes (logout, password) revoke the caller's
            # own session, so clients must not be shared across routes.
            client = await authutil.client_as(app, role)
            try:
                status = await _status(client, method, path, key)
            finally:
                await client.aclose()
            if role is None:
                ok, want = status == 401, "401"
            elif not rbac.role_allows(role, permission):
                ok, want = status == 403, "403"
            else:
                ok, want = status not in (401, 403), "not 401/403"
            if not ok:
                failures.append(f"{method} {path} as {role}: got {status}, want {want}")
    assert not failures, "authorization mismatches:\n" + "\n".join(failures)


async def test_every_api_route_is_in_the_permission_map(store: Store) -> None:
    """Fail-closed: any registered /api route not mapped (and not public) fails CI."""
    _engine, _queue, app = await authutil.make_env(store)
    unmapped = []
    for route in app.routes:  # type: ignore[attr-defined]
        path = getattr(route, "path", "")
        if not path.startswith("/api"):
            continue
        for method in getattr(route, "methods", set()) or set():
            if method in ("HEAD", "OPTIONS"):
                continue
            key = (method, path)
            if key in rbac.PUBLIC_ROUTES:
                continue
            if key not in rbac.ROUTE_PERMISSIONS:
                unmapped.append(f"{method} {path}")
    assert not unmapped, f"unmapped /api routes (fail closed): {unmapped}"


def test_permission_map_only_references_known_permissions() -> None:
    for _route, permission in rbac.ROUTE_PERMISSIONS.items():
        assert permission in rbac.PERMISSIONS, permission


def test_f8_audited_denied_single_source() -> None:
    """F8: the audited-denied set has ONE source of truth (`rbac`); `api.DENIED_ACTION` is only
    its presentation mapping and may never diverge. Each mapped catalog action must be real."""
    assert set(api.DENIED_ACTION) == set(rbac.AUDITED_DENIED_PERMISSIONS)
    for permission, action in api.DENIED_ACTION.items():
        assert permission in rbac.PERMISSIONS, permission
        assert action in audit.ACTIONS, action


def test_f9_config_read_is_its_own_least_privilege_capability() -> None:
    """F9: reading runtime config is gated by `config.read`, not the write capability, and a
    denied read is still audited (the allowlist reveals network-security posture)."""
    assert rbac.permission_for("GET", "/api/config") == "config.read"
    assert rbac.permission_for("POST", "/api/config") == "config.write"
    assert rbac.PERMISSIONS["config.read"] == "admin"  # still least-privilege: admin only
    assert not rbac.role_allows("editor", "config.read")
    assert "config.read" in rbac.AUDITED_DENIED_PERMISSIONS


async def test_403_precedes_404_no_existence_oracle(store: Store) -> None:
    """An under-privileged caller gets 403 for a missing resource (not 404) — no oracle;
    the authorized caller gets 404, proving authorization runs before resource lookup."""
    _engine, _queue, app = await authutil.make_env(store)
    editor = await authutil.client_as(app, "editor")
    admin = await authutil.client_as(app, "admin")
    try:
        # DELETE /api/users/{uid} requires admin. uid 999999 does not exist.
        assert (await editor.delete("/api/users/999999")).status_code == 403
        assert (await admin.delete("/api/users/999999")).status_code == 404
    finally:
        await editor.aclose()
        await admin.aclose()
