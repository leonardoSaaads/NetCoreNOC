"""Recovery by email, attacked (v0.30.0, ADR #445).

What must hold: no account oracle, a link that works once and expires, a link host that comes from
configuration and never from the request, every session ended by a reset, and bounded requests.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from netcorenoc.api.routes import recovery
from netcorenoc.crosscutting import auth
from netcorenoc.store import Store

import authutil
from fakesmtp import FakeSmtp

LINK = re.compile(r"https://noc\.example\.com/#/reset/([A-Za-z0-9_-]+)")


async def _setup(store: Store, server: FakeSmtp) -> tuple[Any, int]:
    _engine, _queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")
    try:
        saved = await admin.post(
            "/api/email",
            json={
                "enabled": True,
                "host": "127.0.0.1",
                "port": server.port,
                "security": "none",
                "sender": "noc@example.com",
                "public_url": "https://noc.example.com",
            },
        )
        assert saved.status_code == 200, saved.text
        users = (await admin.get("/api/users")).json()
        editor = next(u for u in users if u["username"] == "edt")
        profile = await admin.post(
            f"/api/users/{editor['id']}/profile",
            json={"display_name": "Edna", "email": "edna@example.com"},
        )
        assert profile.status_code == 200, profile.text
    finally:
        await admin.aclose()
    return app, int(editor["id"])


async def _mail(server: FakeSmtp, count: int) -> list[Any]:
    for _ in range(100):
        if len(server.messages) >= count:
            break
        await asyncio.sleep(0.02)
    return server.messages


async def test_recovery_is_offered_only_once_it_can_work(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    anon = authutil.new_client(app)
    try:
        assert (await anon.get("/api/login/options")).json() == {"recovery": False}
        refused = await anon.post("/api/password-reset", json={"login": "edt"})
        assert refused.status_code == 202  # the same answer: nothing is revealed either way
    finally:
        await anon.aclose()


async def test_a_link_arrives_sets_a_password_once_and_ends_every_session(store: Store) -> None:
    with FakeSmtp() as server:
        app, _uid = await _setup(store, server)
        editor = await authutil.client_as(app, "editor")
        anon = authutil.new_client(app)
        try:
            assert (await anon.get("/api/login/options")).json() == {"recovery": True}
            # The Host header is the attacker's to choose; the link must not follow it.
            asked = await anon.post(
                "/api/password-reset", json={"login": "edt"}, headers={"Host": "evil.example"}
            )
            assert asked.status_code == 202 and asked.json()["status"] == recovery.ANSWER
            (message,) = await _mail(server, 1)
            assert message["To"] == "edna@example.com"
            body = message.get_payload()
            assert "evil.example" not in body
            token = LINK.search(body).group(1)  # type: ignore[union-attr]
            done = await anon.post(
                "/api/password-reset/confirm",
                json={"token": token, "new_password": "a brand new passphrase"},
            )
            assert done.status_code == 200, done.text
            again = await anon.post(
                "/api/password-reset/confirm",
                json={"token": token, "new_password": "another new passphrase"},
            )
            assert again.status_code == 400 and again.json()["detail"] == recovery.INVALID_LINK
            # The editor's open session is gone; the new password signs in, the old one does not.
            assert (await editor.get("/api/me")).status_code == 401
            assert (await authutil.login(anon, "editor")).status_code == 401
            fresh = await authutil.login(anon, "editor", password="a brand new passphrase")
            assert fresh.status_code == 200
        finally:
            await editor.aclose()
            await anon.aclose()
    async with store.lock:
        actions = [(r["action"], r["outcome"]) for r in await store.audit_all()]
    assert ("password.reset.request", "ok") in actions
    assert ("password.reset", "ok") in actions and ("password.reset", "denied") in actions


async def test_an_unknown_account_gets_the_same_answer_and_no_mail(store: Store) -> None:
    with FakeSmtp() as server:
        app, _uid = await _setup(store, server)
        anon = authutil.new_client(app)
        try:
            known = await anon.post("/api/password-reset", json={"login": "edna@example.com"})
            unknown = await anon.post("/api/password-reset", json={"login": "nobody"})
            no_email = await anon.post("/api/password-reset", json={"login": "vwr"})
            assert known.json() == unknown.json() == no_email.json()
            assert known.status_code == unknown.status_code == no_email.status_code == 202
            await asyncio.sleep(0.3)
            assert len(server.messages) == 1  # only the account that has an address
        finally:
            await anon.aclose()


async def test_a_second_request_inside_the_cooldown_sends_nothing(store: Store) -> None:
    with FakeSmtp() as server:
        app, _uid = await _setup(store, server)
        anon = authutil.new_client(app)
        try:
            await anon.post("/api/password-reset", json={"login": "edt"})
            await anon.post("/api/password-reset", json={"login": "edt"})
            await asyncio.sleep(0.3)
            assert len(server.messages) == 1
        finally:
            await anon.aclose()


async def test_an_expired_link_is_refused(store: Store) -> None:
    with FakeSmtp() as server:
        app, uid = await _setup(store, server)
        now = time.time()
        async with store.lock:
            await store.create_reset(uid, auth.hash_token("x" * 43), now - 3600, now - 1, "test")
            await store.commit()
        anon = authutil.new_client(app)
        try:
            late = await anon.post(
                "/api/password-reset/confirm",
                json={"token": "x" * 43, "new_password": "a brand new passphrase"},
            )
            assert late.status_code == 400
        finally:
            await anon.aclose()


async def test_requests_from_one_address_are_bounded(store: Store) -> None:
    with FakeSmtp() as server:
        app, _uid = await _setup(store, server)
        anon = authutil.new_client(app)
        try:
            codes = [
                (await anon.post("/api/password-reset", json={"login": f"u{n}"})).status_code
                for n in range(int(recovery.REQUEST_CAPACITY) + 1)
            ]
        finally:
            await anon.aclose()
    assert codes[:-1] == [202] * int(recovery.REQUEST_CAPACITY) and codes[-1] == 429


async def test_a_weak_new_password_is_refused_before_the_link_is_spent(store: Store) -> None:
    with FakeSmtp() as server:
        app, _uid = await _setup(store, server)
        anon = authutil.new_client(app)
        try:
            await anon.post("/api/password-reset", json={"login": "edt"})
            (message,) = await _mail(server, 1)
            token = LINK.search(message.get_payload()).group(1)  # type: ignore[union-attr]
            weak = await anon.post(
                "/api/password-reset/confirm", json={"token": token, "new_password": "short"}
            )
            assert weak.status_code == 400
            good = await anon.post(
                "/api/password-reset/confirm",
                json={"token": token, "new_password": "a brand new passphrase"},
            )
            assert good.status_code == 200
        finally:
            await anon.aclose()


async def test_a_person_sets_and_clears_their_own_recovery_address(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    viewer = await authutil.client_as(app, "viewer")
    try:
        pw = {"current_password": authutil.PW}
        bad = await viewer.post(
            "/api/me/profile", json={"display_name": "V", "email": "nope", **pw}
        )
        assert bad.status_code == 400
        ok = await viewer.post(
            "/api/me/profile", json={"display_name": "V", "email": "v@ex.org", **pw}
        )
        assert ok.status_code == 200 and (await viewer.get("/api/me")).json()["email"] == "v@ex.org"
        # A save that does not mention the address leaves it alone (older consoles send only names),
        # and one that sends it unchanged needs no password.
        await viewer.post("/api/me/profile", json={"display_name": "Vera"})
        same = await viewer.post("/api/me/profile", json={"display_name": "V", "email": "v@ex.org"})
        assert (
            same.status_code == 200 and (await viewer.get("/api/me")).json()["email"] == "v@ex.org"
        )
        await viewer.post("/api/me/profile", json={"display_name": "Vera", "email": "", **pw})
        assert (await viewer.get("/api/me")).json()["email"] is None
    finally:
        await viewer.aclose()


async def test_a_session_alone_cannot_redirect_the_recovery_address(store: Store) -> None:
    """ADR #453: whoever holds only a session — a stolen cookie — must not be able to point the
    next reset link at their own mailbox and so turn the session into the account."""
    with FakeSmtp() as server:
        app, uid = await _setup(store, server)
        editor = await authutil.client_as(app, "editor")
        try:
            for password in (None, "wrong password!"):
                stolen = await editor.post(
                    "/api/me/profile",
                    json={"email": "attacker@evil.example", "current_password": password},
                )
                assert stolen.status_code == 400 and "current password" in stolen.text
            assert (await editor.get("/api/me")).json()["email"] == "edna@example.com"
            async with store.lock:
                cur = await store.conn.execute(
                    "SELECT outcome, details FROM audit_log WHERE action='profile.update' "
                    "AND object_id=? ORDER BY id",
                    (str(uid),),
                )
                rows = [(r[0], r[1]) for r in await cur.fetchall()]
            assert [o for o, _ in rows] == ["denied", "denied"]
            assert all("attacker" not in str(d) for _, d in rows), "no address in the audit log"
        finally:
            await editor.aclose()


async def test_a_new_address_withdraws_a_link_sent_to_the_old_one(store: Store) -> None:
    with FakeSmtp() as server:
        app, _uid = await _setup(store, server)
        anon = authutil.new_client(app)
        editor = await authutil.client_as(app, "editor")
        try:
            await anon.post("/api/password-reset", json={"login": "edt"})
            token = LINK.search((await _mail(server, 1))[0].get_payload()).group(1)  # type: ignore[union-attr]
            moved = await editor.post(
                "/api/me/profile",
                json={"email": "edna@new.example", "current_password": authutil.PW},
            )
            assert moved.status_code == 200, moved.text
            used = await anon.post(
                "/api/password-reset/confirm",
                json={"token": token, "new_password": "a brand new passphrase"},
            )
            assert used.status_code == 400, "a link sent to the old address must stop working"
        finally:
            await anon.aclose()
            await editor.aclose()


async def test_an_admin_replaces_a_password_and_never_reads_one(store: Store) -> None:
    """ADR #453: an administrator resets; they never see a password. The one-time password works
    once to sign in, forces a change, and every session and reset link of the account ends."""
    with FakeSmtp() as server:
        app, uid = await _setup(store, server)
        admin = await authutil.client_as(app, "admin")
        editor = await authutil.client_as(app, "editor")
        anon = authutil.new_client(app)
        try:
            listing = (await admin.get("/api/users")).json()
            assert all("password_hash" not in row for row in listing), listing
            assert "scrypt" not in str(listing), "no route returns a password hash"
            await anon.post("/api/password-reset", json={"login": "edt"})
            token = LINK.search((await _mail(server, 1))[0].get_payload()).group(1)  # type: ignore[union-attr]

            reset = await admin.post(f"/api/users/{uid}/password-reset", json={})
            assert reset.status_code == 200, reset.text
            assert reset.headers["cache-control"] == "no-store"
            temporary = reset.json()["temporary_password"]
            assert (
                auth.validate_password(temporary) is None and reset.json()["must_change_password"]
            )

            assert (await editor.get("/api/me")).status_code == 401, "their sessions end"
            stale = await anon.post(
                "/api/password-reset/confirm",
                json={"token": token, "new_password": "a brand new passphrase"},
            )
            assert stale.status_code == 400, "a link already emailed stops working"
            old = await authutil.login(anon, "editor")
            assert old.status_code == 401, "the old password no longer signs in"
            signed = await anon.post("/api/login", json={"username": "edt", "password": temporary})
            assert signed.status_code == 200 and signed.json()["must_change_password"] is True
            chosen = await anon.post(
                "/api/login",
                json={
                    "username": "edt",
                    "password": temporary,
                    "new_password": "edna's own choice",
                },
            )
            assert chosen.status_code == 200 and chosen.json()["must_change_password"] is False

            async with store.lock:
                cur = await store.conn.execute(
                    "SELECT password_hash FROM app_user WHERE id=?", (uid,)
                )
                stored = str((await cur.fetchone())[0])  # type: ignore[index]
                cur = await store.conn.execute(
                    "SELECT details FROM audit_log WHERE action='user.update' AND object_id=? "
                    "ORDER BY id DESC LIMIT 1",
                    (str(uid),),
                )
                details = str((await cur.fetchone())[0])  # type: ignore[index]
            assert stored.startswith("scrypt-p1$") and temporary not in stored
            assert '"password"' in details and temporary not in details
        finally:
            await admin.aclose()
            await editor.aclose()
            await anon.aclose()


async def test_only_an_admin_resets_and_never_their_own(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")
    editor = await authutil.client_as(app, "editor")
    try:
        me = (await admin.get("/api/me")).json()["user_id"]
        users = {u["username"]: u["id"] for u in (await admin.get("/api/users")).json()}
        assert (await editor.post(f"/api/users/{users['vwr']}/password-reset")).status_code == 403
        own = await admin.post(f"/api/users/{me}/password-reset")
        assert own.status_code == 400 and "Account" in own.text
        assert (await admin.post("/api/users/999999/password-reset")).status_code == 404
    finally:
        await admin.aclose()
        await editor.aclose()
