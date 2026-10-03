"""C.4 — abuse tests against NetCoreNOC's own attack surface, through the real HTTP path.

This suite consolidates the v0.4.0-surface abuse checks; the broader matrix lives in
``test_rbac`` (route x role x method, fail-closed), XSS/headers in ``test_security_ui``, auth
throttle/session in ``test_auth``, audit tamper-evidence in ``test_audit``, and the entity-key
forgery bound in ``test_promotion``. Focused here: CSRF (renamed header after the rebrand — a
regression the rename could have silently broken), the new ``/readyz`` route's headers, and
injection through the *shaped* viewer path.
"""

from __future__ import annotations

import httpx
import pytest

from netcorenoc.api import CSP
from netcorenoc.store import Store

import authutil
import util

BASE = 1_700_000_000.0
ORIGIN = authutil.ORIGIN
HOST = "netcorenoc.test"


def _raw_client(app: object) -> httpx.AsyncClient:
    """A client with NO default Origin / CSRF header, so each test sets exactly what it means to."""
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    return httpx.AsyncClient(transport=transport, base_url=f"http://{HOST}")


async def _login_admin(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/login", json={"username": "adm", "password": authutil.PW}, headers={"Origin": ORIGIN}
    )
    assert resp.status_code == 200, resp.text


LABEL = {"kind": "ne", "id": 1, "label": "x"}


async def _seed_label_target(store: Store) -> None:
    """Create NE id 1 so a label write has a real target.

    v0.7.1 (F37) rejects a label write to a target that does not exist, and `make_env` drives no
    traps, so NE 1 did not exist. The two CSRF tests below assert **200** because they are about
    the CSRF gate, not about label semantics — so they are given a real element rather than having
    their assertion weakened (DECISIONS #70). It is the **NE** since v0.16.3: that is what a label
    names, and the device id beside it is a different key that happens to agree (#281).
    """
    async with store.lock:
        await store.device_id("10.0.0.1", BASE)
        assert await store.ne_id("10.0.0.1", BASE) == LABEL["id"]
        await store.commit()


async def test_csrf_cookie_mutation_without_client_header_is_rejected(store: Store) -> None:
    _e, _q, app = await authutil.make_env(store)
    async with _raw_client(app) as client:
        await _login_admin(client)
        # Cookie session, matching Origin, but the X-NetCoreNOC-Client header is absent → 403.
        resp = await client.post("/api/labels", json=LABEL, headers={"Origin": ORIGIN})
        assert resp.status_code == 403
        assert "csrf" in resp.text.lower()


async def test_csrf_origin_host_mismatch_is_rejected(store: Store) -> None:
    _e, _q, app = await authutil.make_env(store)
    async with _raw_client(app) as client:
        await _login_admin(client)
        resp = await client.post(
            "/api/labels",
            json=LABEL,
            headers={"Origin": "http://evil.example", "X-NetCoreNOC-Client": "ui"},
        )
        assert resp.status_code == 403


async def test_csrf_missing_origin_is_rejected(store: Store) -> None:
    _e, _q, app = await authutil.make_env(store)
    async with _raw_client(app) as client:
        await _login_admin(client)
        resp = await client.post("/api/labels", json=LABEL, headers={"X-NetCoreNOC-Client": "ui"})
        assert resp.status_code == 403


async def test_csrf_valid_cookie_mutation_succeeds(store: Store) -> None:
    _e, _q, app = await authutil.make_env(store)
    await _seed_label_target(store)
    async with _raw_client(app) as client:
        await _login_admin(client)
        resp = await client.post(
            "/api/labels", json=LABEL, headers={"Origin": ORIGIN, "X-NetCoreNOC-Client": "ui"}
        )
        assert resp.status_code == 200


async def test_bearer_token_mutation_needs_no_csrf_header(store: Store) -> None:
    """Service tokens are not cookie credentials, so the CSRF gate does not apply to them."""
    from netcorenoc.crosscutting import auth

    _e, _q, app = await authutil.make_env(store)
    await _seed_label_target(store)
    async with store.lock:
        await store.create_token(auth.hash_token("svc"), "svc", "editor", "adm", 0.0)
        await store.commit()
    async with _raw_client(app) as client:
        resp = await client.post("/api/labels", json=LABEL, headers={"Authorization": "Bearer svc"})
        assert resp.status_code == 200  # no Origin, no client header, still fine


async def test_session_cookie_is_samesite_strict_and_httponly(store: Store) -> None:
    _e, _q, app = await authutil.make_env(store)
    async with _raw_client(app) as client:
        resp = await client.post(
            "/api/login",
            json={"username": "adm", "password": authutil.PW},
            headers={"Origin": ORIGIN},
        )
        set_cookie = resp.headers.get("set-cookie", "")
        assert "netcorenoc_session=" in set_cookie
        assert "samesite=strict" in set_cookie.lower()
        assert "httponly" in set_cookie.lower()


async def test_readyz_carries_full_security_headers(store: Store) -> None:
    _e, _q, app = await authutil.make_env(store)
    async with _raw_client(app) as client:
        resp = await client.get("/readyz")
        assert resp.headers["content-security-policy"] == CSP
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["x-frame-options"] == "DENY"
        assert resp.headers["referrer-policy"] == "no-referrer"


async def test_hostile_label_renders_inert_json_on_the_shaped_viewer_path(store: Store) -> None:
    """A hostile string persisted through the real ingest path renders as inert JSON data on a
    viewer's shaped views — never markup, and the device IP is coarsened."""
    xss = "<img src=x onerror=alert(1)>"
    engine, queue, app = await authutil.make_env(store)
    await util.drive(engine, queue, [util.event(device="10.5.5.5", ts=BASE)])
    async with store.lock:
        ne = await store.ne_id("10.5.5.5", BASE)
        await store.set_label("ne", ne, xss, BASE)
        await store.commit()
    viewer = await authutil.client_as(app, "viewer")
    try:
        resp = await viewer.get("/api/graph")
        node = next(n for n in resp.json()["nodes"] if n["label"] == xss)
        assert node["label"] == xss  # round-trips as a string value, not markup
        assert node["ip"] == "10.5.5.0/24"  # shaped for the viewer
        assert "application/json" in resp.headers["content-type"]
        assert not resp.text.lstrip().lower().startswith("<!doctype")
        # dangerous characters are JSON-escaped in the body
        assert "<img" in resp.text or "\\u003cimg" in resp.text
    finally:
        await viewer.aclose()


@pytest.mark.parametrize("path", ["/", "/healthz", "/readyz", "/api/stats"])
async def test_all_v04_route_classes_carry_security_headers(store: Store, path: str) -> None:
    _e, _q, app = await authutil.make_env(store)
    async with _raw_client(app) as client:
        resp = await client.get(path)
        assert resp.headers["content-security-policy"] == CSP
