"""Settings → Email: the SMTP client and its routes (v0.30.0, ADR #445).

Sent through a real SMTP conversation with a local server (`fakesmtp`), not a mock of `smtplib`,
so what is checked is what a mail server would receive.
"""

from __future__ import annotations

from typing import Any

import pytest

from netcorenoc.crosscutting import mail
from netcorenoc.store import Store

import authutil
from fakesmtp import FakeSmtp


def _config(port: int, **extra: Any) -> dict[str, Any]:
    return {
        "enabled": True,
        "provider": "custom",
        "host": "127.0.0.1",
        "port": port,
        "security": "none",
        "username": "noc@example.com",
        "password": "app-password",
        "sender": "noc@example.com",
        "public_url": "https://noc.example.com",
        **extra,
    }


def test_a_message_goes_through_a_real_smtp_conversation() -> None:
    with FakeSmtp("noc@example.com", "app-password") as server:
        config = mail.MailConfig(**_config(server.port))
        mail.send(config, mail.compose(config, "ops@example.com", "Subject", "Body text\n"))
    assert server.logins == [("noc@example.com", "app-password")]
    (message,) = server.messages
    assert message["To"] == "ops@example.com" and message["Subject"] == "Subject"
    assert "noc@example.com" in message["From"] and message["Auto-Submitted"]
    assert "Body text" in message.get_payload()


def test_a_refused_password_says_what_to_do() -> None:
    with FakeSmtp("noc@example.com", "the-right-one") as server:
        config = mail.MailConfig(**_config(server.port))
        with pytest.raises(mail.MailError, match="app password"):
            mail.send(config, mail.compose(config, "ops@example.com", "s", "b"))


def test_an_unreachable_server_is_a_sentence_not_a_traceback() -> None:
    config = mail.MailConfig(**_config(1, timeout_s=3.0))
    with pytest.raises(mail.MailError, match=r"127\.0\.0\.1:1"):
        mail.send(config, mail.compose(config, "ops@example.com", "s", "b"))


def test_the_environment_password_wins_and_nothing_else_is_needed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NETCORENOC_SMTP_PASSWORD", "from-env")
    config = mail.MailConfig(**_config(25, password=""))
    assert config.effective_password == "from-env" and config.password_source == "environment"


def test_a_header_cannot_be_injected_through_a_name() -> None:
    bad = mail.MailConfig(**_config(25, sender_name="NOC\r\nBcc: victim@example.com"))
    assert mail.problems(bad)


async def test_the_password_is_write_only(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")
    try:
        first = (await admin.get("/api/email")).json()
        assert first["enabled"] is False and first["password_set"] is False
        assert first["providers"]["gmail"]["host"] == "smtp.gmail.com"
        saved = await admin.post("/api/email", json=_config(2525))
        assert saved.status_code == 200, saved.text
        view = (await admin.get("/api/email")).json()
        assert "password" not in view and view["password_set"] is True
        assert view["recovery_available"] is True
        kept = {k: v for k, v in _config(2525).items() if k != "password"}
        assert (await admin.post("/api/email", json=kept)).status_code == 200
        async with store.lock:
            stored = mail.from_document(await store.get_meta(mail.META_KEY))
        assert stored.password == "app-password"
        assert "app-password" not in str(await admin.get("/api/email"))
        bad = await admin.post("/api/email", json=_config(2525, public_url="javascript:x"))
        assert bad.status_code == 400
    finally:
        await admin.aclose()
    async with store.lock:
        rows = [r for r in await store.audit_all() if r["action"] == "email.config.change"]
    assert rows and all("app-password" not in r["details"] for r in rows)


async def test_a_test_message_reports_success_and_failure(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")
    try:
        with FakeSmtp("noc@example.com", "app-password") as server:
            await admin.post("/api/email", json=_config(server.port))
            sent = await admin.post("/api/email/test", json={"to": "ops@example.com"})
            assert sent.status_code == 200, sent.text
            assert len(server.messages) == 1
            await admin.post("/api/email", json=_config(server.port, password="wrong"))
            failed = await admin.post("/api/email/test", json={"to": "ops@example.com"})
        assert failed.status_code == 502 and "app password" in failed.json()["detail"]
    finally:
        await admin.aclose()
    async with store.lock:
        outcomes = [r["outcome"] for r in await store.audit_all() if r["action"] == "email.test"]
    assert outcomes == ["ok", "error"]
