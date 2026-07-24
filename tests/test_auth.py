"""Phase 4.2 / 4.3 — passwords, policy, login throttle, and session lifecycle."""

from __future__ import annotations

import pytest

from netcorenoc import auth
from netcorenoc.store import Store

import authutil

# -- passwords and policy ------------------------------------------------------------


def test_password_hash_roundtrip_and_format() -> None:
    stored = auth.hash_password("correct horse battery staple")
    assert stored.startswith("scrypt$")
    assert stored.count("$") == 5
    assert auth.verify_password("correct horse battery staple", stored)
    assert not auth.verify_password("wrong password here", stored)


def test_password_hashes_are_salted_and_unique() -> None:
    assert auth.hash_password("same password 123") != auth.hash_password("same password 123")


def test_verify_rejects_malformed_stored_hash() -> None:
    for bad in ("", "notscrypt$x", "scrypt$a$b$c$d$e", "plain"):
        assert not auth.verify_password("whatever", bad)


def test_password_policy_length_bounds() -> None:
    assert auth.validate_password("short") is not None
    assert auth.validate_password("x" * 11) is not None
    assert auth.validate_password("x" * 12) is None
    assert auth.validate_password("x" * 128) is None
    assert auth.validate_password("x" * 129) is not None


def test_production_scrypt_parameters() -> None:
    """The shipped work factor is n=2**17 (the test suite lowers only the live SCRYPT_N)."""
    assert auth.PRODUCTION_SCRYPT_N == 2**17
    assert (auth.SCRYPT_R, auth.SCRYPT_P, auth.SCRYPT_DKLEN) == (8, 1, 32)
    assert auth.SCRYPT_MAXMEM == 256 * 1024 * 1024


# -- login throttle ------------------------------------------------------------------


def test_throttle_locks_after_five_failures_with_backoff() -> None:
    t = auth.LoginThrottle()
    now = 1000.0
    for _ in range(4):
        t.record_failure("bob", "10.0.0.1", now)
    assert t.locked_for("bob", "10.0.0.1", now) == 0.0  # 4 failures: not yet locked
    t.record_failure("bob", "10.0.0.1", now)
    first = t.locked_for("bob", "10.0.0.1", now)
    assert first == pytest.approx(1.0)  # 5th failure: 1 s
    t.record_failure("bob", "10.0.0.1", now)
    assert t.locked_for("bob", "10.0.0.1", now) == pytest.approx(2.0)  # then 2 s
    t.record_failure("bob", "10.0.0.1", now)
    assert t.locked_for("bob", "10.0.0.1", now) == pytest.approx(4.0)  # then 4 s


def test_throttle_caps_at_fifteen_minutes() -> None:
    t = auth.LoginThrottle()
    for _ in range(40):
        t.record_failure("bob", "10.0.0.1", 0.0)
    assert t.locked_for("bob", "10.0.0.1", 0.0) == auth.LOCKOUT_MAX_S == 900.0


def test_throttle_resets_on_success() -> None:
    t = auth.LoginThrottle()
    for _ in range(6):
        t.record_failure("bob", "10.0.0.1", 0.0)
    assert t.locked_for("bob", "10.0.0.1", 0.0) > 0
    t.record_success("bob", "10.0.0.1")
    assert t.locked_for("bob", "10.0.0.1", 0.0) == 0.0


def test_throttle_is_per_ip_too() -> None:
    t = auth.LoginThrottle()
    for _ in range(6):
        t.record_failure("alice", "10.0.0.9", 0.0)  # same IP, one username
    # A different username from the same IP is still throttled by the IP counter.
    assert t.locked_for("mallory", "10.0.0.9", 0.0) > 0


# -- login flow: no enumeration ------------------------------------------------------


async def test_login_no_user_enumeration(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    async with authutil.new_client(app) as c:
        no_user = await c.post("/api/login", json={"username": "ghost", "password": "x" * 12})
        bad_pw = await c.post("/api/login", json={"username": "adm", "password": "x" * 12})
    assert no_user.status_code == bad_pw.status_code == 401
    assert no_user.json()["detail"] == bad_pw.json()["detail"]  # identical message


# -- sessions (unit, controlled clock) -----------------------------------------------


async def test_session_idle_expiry(store: Store) -> None:
    async with store.lock:
        uid = await store.create_user("u", auth.hash_password(authutil.PW), "viewer", False, 0.0)
        fresh = await auth.open_session(store, uid, "10.0.0.1", now=1000.0)
        stale = await auth.open_session(store, uid, "10.0.0.1", now=1000.0)
        await store.commit()
    assert await auth.resolve_session(store, fresh, now=1000.0 + 60) is not None
    # An independent session left untouched past the 30-min idle window: rejected + purged.
    assert await auth.resolve_session(store, stale, now=1000.0 + auth.IDLE_TIMEOUT_S + 1) is None
    assert await store.get_session(auth.hash_token(stale)) is None


async def test_session_absolute_expiry(store: Store) -> None:
    async with store.lock:
        uid = await store.create_user("u", auth.hash_password(authutil.PW), "viewer", False, 0.0)
        token = await auth.open_session(store, uid, "10.0.0.1", now=1000.0)
        await store.commit()
    # Keep sliding idle by touching repeatedly, staying under the 12 h absolute cap...
    now = 1000.0
    for _ in range(20):
        now += auth.IDLE_TIMEOUT_S - 60
        assert await auth.resolve_session(store, token, now=now) is not None
    # ...but past 12 h absolute the session dies regardless of recent activity.
    assert (
        await auth.resolve_session(store, token, now=1000.0 + auth.ABSOLUTE_TIMEOUT_S + 1) is None
    )


async def test_session_idle_slides_on_activity(store: Store) -> None:
    async with store.lock:
        uid = await store.create_user("u", auth.hash_password(authutil.PW), "viewer", False, 0.0)
        token = await auth.open_session(store, uid, "10.0.0.1", now=0.0)
        await store.commit()
    # Touch at t=1000 slides idle to 1000+1800; a request at 1000+1700 is still valid.
    assert await auth.resolve_session(store, token, now=1000.0) is not None
    assert await auth.resolve_session(store, token, now=1000.0 + 1700) is not None


# -- sessions (integration) ----------------------------------------------------------


async def test_session_fixation_new_id_each_login(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    async with authutil.new_client(app) as c:
        first = (await authutil.login(c, "viewer")).cookies["netcorenoc_session"]
        second = (await authutil.login(c, "viewer")).cookies["netcorenoc_session"]
    assert first != second  # a fresh id is minted on every login


async def test_logout_revokes_session(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    c = await authutil.client_as(app, "editor")
    try:
        assert (await c.get("/api/me")).status_code == 200
        assert (await c.post("/api/logout")).status_code == 200
        assert (await c.get("/api/me")).status_code == 401  # session gone server-side
    finally:
        await c.aclose()


async def test_password_change_revokes_all_sessions(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    c = await authutil.client_as(app, "editor")
    other = await authutil.client_as(app, "editor")  # a second live session, same user
    try:
        resp = await c.post(
            "/api/password", json={"old_password": authutil.PW, "new_password": "brand-new-pw-9999"}
        )
        assert resp.status_code == 200
        assert (await c.get("/api/me")).status_code == 401
        assert (await other.get("/api/me")).status_code == 401  # the other session too
    finally:
        await c.aclose()
        await other.aclose()


async def test_role_change_revokes_sessions(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")
    viewer = await authutil.client_as(app, "viewer")
    try:
        async with store.lock:
            row = await store.get_user_by_name("vwr")
        assert row is not None
        assert (
            await admin.post(f"/api/users/{row['id']}/role", json={"role": "editor"})
        ).status_code == 200
        assert (await viewer.get("/api/me")).status_code == 401  # revoked by the role change
    finally:
        await admin.aclose()
        await viewer.aclose()


async def test_session_cookie_flags(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    async with authutil.new_client(app) as c:
        resp = await authutil.login(c, "viewer")
    header = resp.headers["set-cookie"].lower()
    assert "httponly" in header and "samesite=strict" in header and "path=/" in header
    assert "secure" not in header  # no TLS in this app


async def test_secure_cookie_when_tls_enabled(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store, tls_enabled=True)
    async with authutil.new_client(app) as c:
        resp = await authutil.login(c, "viewer")
    assert "secure" in resp.headers["set-cookie"].lower()  # F5: auto-Secure under TLS


# -- bootstrap -----------------------------------------------------------------------


async def test_bootstrap_admin_created_once(store: Store) -> None:
    async with store.lock:
        password = await auth.bootstrap_admin(store, 0.0)
        again = await auth.bootstrap_admin(store, 0.0)
        await store.commit()
    assert password is not None and len(password) == auth.BOOTSTRAP_PASSWORD_CHARS
    assert again is None  # never re-created, never re-printed
    async with store.lock:
        admin = await store.get_user_by_name("admin")
    assert admin is not None and admin["role"] == "admin" and admin["must_change_password"] == 1


async def test_bootstrap_login_requires_password_change_then_grants_access(store: Store) -> None:
    import asyncio

    from netcorenoc.api import create_app
    from netcorenoc.main import Engine

    engine = Engine(store, asyncio.Queue())
    await engine.start()
    async with store.lock:
        bootstrap_pw = await auth.bootstrap_admin(store, 0.0)
        await store.commit()
    assert bootstrap_pw is not None
    app = create_app(engine, rate_capacity=100000.0)
    async with authutil.new_client(app) as c:
        first = await c.post("/api/login", json={"username": "admin", "password": bootstrap_pw})
        assert first.json() == {"must_change_password": True}  # session withheld until change
        changed = await c.post(
            "/api/login",
            json={
                "username": "admin",
                "password": bootstrap_pw,
                "new_password": "a-good-new-pw-1234",
            },
        )
        assert changed.status_code == 200 and changed.json()["role"] == "admin"
        assert (await c.get("/api/stats")).status_code == 200  # unlocked


async def test_forced_password_change_gate_locks_a_live_session(store: Store) -> None:
    """A live session whose user is flagged must_change is locked to me/password/logout."""
    _engine, _queue, app = await authutil.make_env(store)
    c = await authutil.client_as(app, "editor")
    try:
        async with store.lock:
            row = await store.get_user_by_name("edt")
            assert row is not None
            await store.set_must_change_password(row["id"], True, 0.0)
            await store.commit()
        assert (await c.get("/api/stats")).status_code == 403  # gated
        assert (await c.get("/api/me")).status_code == 200  # allowed
        unlock = await c.post(
            "/api/password", json={"old_password": authutil.PW, "new_password": "another-good-pw-1"}
        )
        assert unlock.status_code == 200
    finally:
        await c.aclose()
