"""ADR #510 — a mailbox signed in with Microsoft, for Outlook.com and Microsoft 365.

Outlook.com refuses a password for SMTP (`535 5.7.139 basic authentication is disabled`): the
console says so and points at the sign-in, instead of calling the password wrong. The sign-in is the
device code flow: the browser sees a user code, never the device code; the approved tokens stay in
the server until the settings that use them are saved; nobody else can poll or save another
person's sign-in; and what is saved is never returned. Sending presents a fresh access token as
XOAUTH2, and a refresh token Microsoft rotates is stored.
"""

from __future__ import annotations

from typing import Any

import pytest

from netcorenoc.crosscutting import mail, mailauth
from netcorenoc.store import Store

import authutil
from fakesmtp import FakeSmtp

CLIENT = "11111111-2222-3333-4444-555555555555"


class FakeMicrosoft:
    """Microsoft's identity platform, answering the device code flow and refreshes."""

    def __init__(self) -> None:
        self.approved = False
        self.refreshes = 0
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(self, url: str, form: dict[str, str]) -> dict[str, Any]:
        self.calls.append((url, form))
        if url.endswith("/devicecode"):
            return {
                "device_code": "DEVICE-SECRET",
                "user_code": "ABCD-EFGH",
                "expires_in": 900,
                "interval": 0,
                "verification_uri": "https://microsoft.com/devicelogin",
            }
        if form.get("grant_type") == "refresh_token":
            self.refreshes += 1
            return {
                "access_token": f"access-{self.refreshes}",
                "expires_in": 3600,
                "refresh_token": f"refresh-{self.refreshes + 1}",
            }
        if not self.approved:
            return {"error": "authorization_pending"}
        return {"access_token": "access-0", "refresh_token": "refresh-1", "expires_in": 3600}


@pytest.fixture
def microsoft(monkeypatch: pytest.MonkeyPatch) -> FakeMicrosoft:
    fake = FakeMicrosoft()
    monkeypatch.setattr(mailauth, "_post", fake)
    mailauth._HELD.clear()
    return fake


def test_a_password_microsoft_refuses_is_explained_not_called_wrong() -> None:
    with FakeSmtp("me@outlook.com", "an-app-password", basic_off=True) as server:
        config = mail.MailConfig(
            enabled=True,
            provider="outlook",
            host="127.0.0.1",
            port=server.port,
            security="none",
            username="me@outlook.com",
            password="an-app-password",
            sender="me@outlook.com",
        )
        with pytest.raises(mail.MailError, match="Sign in with Microsoft"):
            mail.send(config, mail.compose(config, "ops@example.com", "s", "b"))


async def test_an_admin_signs_the_mailbox_in_and_it_sends_as_xoauth2(
    store: Store, microsoft: FakeMicrosoft
) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")
    editor = await authutil.client_as(app, "editor")
    try:
        started = await admin.post(
            "/api/mail/oauth/start", json={"client_id": CLIENT, "tenant": "consumers"}
        )
        assert started.status_code == 200, started.text
        flow = started.json()
        assert flow["user_code"] == "ABCD-EFGH" and "DEVICE-SECRET" not in started.text
        waiting = await admin.post("/api/mail/oauth/poll", json={"flow": flow["flow"]})
        assert waiting.json()["status"] == "waiting"
        microsoft.approved = True
        done = await admin.post("/api/mail/oauth/poll", json={"flow": flow["flow"]})
        assert done.json()["status"] == "signed_in", done.text
        stolen = await editor.post("/api/mail/oauth/poll", json={"flow": flow["flow"]})
        assert stolen.status_code in (403, 404), "another person's sign-in is not theirs"

        with FakeSmtp(token="access-1") as server:
            draft = {
                "enabled": True,
                "provider": "outlook",
                "host": "127.0.0.1",
                "port": server.port,
                "security": "none",
                "username": "me@outlook.com",
                "sender": "me@outlook.com",
                "auth": "oauth",
                "oauth_client_id": CLIENT,
                "oauth_tenant": "consumers",
                "flow": flow["flow"],
            }
            tested = await admin.post(
                "/api/email/test", json={"to": "ops@example.com", "draft": draft}
            )
            assert tested.status_code == 200, tested.text
            assert server.logins == [("me@outlook.com", "xoauth2:access-1")]
            saved = await admin.post("/api/email", json=draft)
            assert saved.status_code == 200, saved.text
        assert "refresh" not in (await admin.get("/api/email")).text.replace("signed_in", "")
        async with store.reading():
            stored = mail.from_document(await store.get_meta(mail.META_KEY))
        assert stored.signed_in and stored.oauth_refresh == "refresh-1"
        reused = await admin.post("/api/email", json=draft)
        assert reused.status_code == 404, "a sign-in is taken once"

        mailauth._HELD.clear()
        with FakeSmtp(token="access-2") as server:
            again = {**draft, "port": server.port, "flow": None}
            assert (await admin.post("/api/email", json=again)).status_code == 200
            sent = await admin.post("/api/email/test", json={"to": "ops@example.com"})
            assert sent.status_code == 200, sent.text
        async with store.reading():
            rotated = mail.from_document(await store.get_meta(mail.META_KEY))
        assert rotated.oauth_refresh == "refresh-3", "the token Microsoft rotated is stored"
    finally:
        await admin.aclose()
        await editor.aclose()


def test_a_client_id_and_a_tenant_are_checked_before_microsoft_is_asked() -> None:
    assert mailauth.valid_client_id(CLIENT) and not mailauth.valid_client_id("not-a-guid")
    assert mailauth.valid_tenant("consumers") and not mailauth.valid_tenant("evil.example/x")
    with pytest.raises(mailauth.AuthError, match="GUID"):
        mailauth.start("nope", "consumers")


async def test_a_customers_mailbox_is_tried_then_kept_in_the_vault(store: Store) -> None:
    """ADR #510: the Email tab's wizard tries the mailbox storing nothing, then seals it."""
    _engine, _queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")
    viewer = await authutil.client_as(app, "viewer")
    try:
        made = await admin.post("/api/organizations", json={"name": "Claro", "slug": "claro"})
        cid = int(made.json()["id"])
        with FakeSmtp("noc@claro.example", "app-pass") as server:
            account = {
                "provider": "custom",
                "sender": "noc@claro.example",
                "sender_name": "Claro NOC",
                "password": "wrong",
                "smtp_host": "127.0.0.1",
                "smtp_port": server.port,
                "smtp_security": "none",
            }
            base = f"/api/noc/customers/{cid}/mail"
            refused = await admin.post(
                f"{base}/try", json={"to": "ops@example.com", "account": account}
            )
            assert refused.status_code == 200 and refused.json()["ok"] is False
            async with store.reading():
                assert not await store.noc_list_secrets(organization_id=cid), "a try stores nothing"
            tried = await admin.post(
                f"{base}/try",
                json={"to": "ops@example.com", "account": {**account, "password": "app-pass"}},
            )
            assert tried.json()["ok"] is True, tried.text
            assert len(server.messages) == 1
            denied = await viewer.post(f"{base}/account", json={**account, "password": "app-pass"})
            assert denied.status_code == 403
            kept = await admin.post(f"{base}/account", json={**account, "password": "app-pass"})
            assert kept.status_code == 200, kept.text
            async with store.reading():
                secrets = await store.noc_list_secrets(organization_id=cid)
            assert [s["kind"] for s in secrets] == ["smtp"]
            assert "app-pass" not in str(secrets), "the vault keeps it sealed"
            settings = (await admin.get(base)).json()
            assert (
                settings["sender"] == "noc@claro.example"
                and settings["smtp"]["port"] == server.port
            )
            again = await admin.post(f"{base}/account", json={**account, "password": None})
            assert again.status_code == 200, "an edit that does not retype the password keeps it"
            async with store.reading():
                assert len(await store.noc_list_secrets(organization_id=cid)) == 1, "updated"
    finally:
        await admin.aclose()
        await viewer.aclose()
