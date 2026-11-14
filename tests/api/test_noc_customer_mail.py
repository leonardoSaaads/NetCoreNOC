"""Each customer's equipment access and its email (ADR #481), through the HTTP API and the loops.

What the owner asked, each answered by driving the real routes:

* a device credential belongs to a customer: stored with it, listed by it, moved by a custodian; one
  stored before belongs to none and is listed as such; an AI provider's key belongs to no customer;
* an agent sees whose equipment each credential reaches, and never leases a customer's mail server;
* a customer's email settings are its book (who works cases keeps them) but which credential sends
  and reads is a custodian's choice, of that customer's own SMTP and IMAP credentials;
* a sent customer update is emailed to the customer's recipients, its subject tagged with the case,
  and recorded with its Message-ID — or, with no mail server, recorded as not sent, and said so;
* the customer's reply, read from its mailbox, lands on the case's feed as the customer's, and a
  case waiting on the customer goes back to the queue **unassigned**, for the agent at its tier with
  the fewest cases; a reply from an unregistered address, an automatic reply and a message read
  twice are never posted, and one naming another customer's case is kept as unmatched.
"""

from __future__ import annotations

import time
from email.message import EmailMessage
from typing import Any

import httpx
import pytest

from netcorenoc.crosscutting import inbox, mail
from netcorenoc.engine.noc import custmail, mail_loop, routing
from netcorenoc.store import Store

import authutil
import util

PW = authutil.PW
STEP = {"username": "adm", "password": PW, "otp": authutil.OTP}
COMPLETE = {
    "title": "Fibre span down",
    "description": "LOS on both ends since 09:12.",
    "executive_summary": "One link down between two sites",
    "current_analysis": "No light at either end: the span is cut.",
    "noc_next_step": "Open a ticket with the fibre provider.",
    "customer_next_step": "Confirm the circuit reference.",
}


async def _estate(store: Store) -> dict[str, int]:
    """Two customers: Acme on 10.0.0.1, Globex on 192.168.50.1."""
    async with store.lock:
        ids = {}
        for name, slug, ip in (("Acme", "acme", "10.0.0.1"), ("Globex", "globex", "192.168.50.1")):
            ne = await store.ne_id(ip, 1.0)
            org = await store.create_organization(name, slug, 1.0)
            await store.set_ne_organization(ne, org)
            ids[slug] = org
        await store.commit()
    return ids


async def _case(store: Store, device: str = "10.0.0.1") -> int:
    """An open situation on ``device``, its case opened (ADR #480)."""
    now = time.time()
    async with store.lock:
        sid = await store.create_situation(now, None)
        alarm = await store.ingest(util.event(device=device, instance="3", ts=now))
        await store.add_alarm_to_situation(sid, alarm.alarm_id)
        await store.promote_situation(sid, now, "promote")
        await store.noc_case_open(sid, now)
        await store.commit()
    return sid


def _cred(name: str, kind: str, host: str, customer: int | None, **fields: str) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": name,
        "kind": kind,
        "access_level": "view_only",
        "host": host,
        "fields": fields or {"username": "noc", "password": "Device-Passw0rd!"},
    }
    if customer is not None:
        body["customer_id"] = customer
    return body


async def _bearer_agent(app: object, store: Store) -> httpx.AsyncClient:
    value = await authutil.make_token(store, "atlas", "editor")
    async with store.lock:
        token_id = next(int(t["id"]) for t in await store.list_tokens() if t["name"] == "atlas")
        await store.noc_set_grant(f"token:{token_id}", "agent", True, 0.0, "test")
        await store.commit()
    client = authutil.new_client(app)
    client.headers["Authorization"] = f"Bearer {value}"
    return client


async def test_a_device_credential_belongs_to_a_customer(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    ids = await _estate(store)
    acme, globex = ids["acme"], ids["globex"]
    admin = await authutil.client_as(app, "admin")
    agent = await _bearer_agent(app, store)
    try:
        ssh = await admin.post(
            "/api/noc/secrets", json=_cred("acme core", "ssh_password", "10.0.0.1", acme)
        )
        assert ssh.status_code == 200, ssh.text
        assert ssh.json()["customer_id"] == acme and ssh.json()["customer_name"] == "Acme"
        telnet = await admin.post(
            "/api/noc/secrets", json=_cred("acme edge", "telnet", "10.0.0.9", acme)
        )
        assert telnet.status_code == 200 and telnet.json()["port"] == 992
        assert telnet.json()["protocol_version"] == "Telnet over TLS", "secure Telnet first"
        smtp = await admin.post(
            "/api/noc/secrets",
            json=_cred(
                "acme smtp",
                "smtp",
                "smtp.acme.example",
                acme,
                username="noc@acme.example",
                password="Mail-Passw0rd!",
            ),
        )
        assert smtp.status_code == 200
        old = await admin.post(
            "/api/noc/secrets", json=_cred("old switch", "ssh_password", "10.9.9.9", None)
        )
        assert old.json()["customer_id"] is None, "stored without a customer: assigned to none"
        bad = await admin.post(
            "/api/noc/secrets", json=_cred("x", "ssh_password", "10.0.0.2", 9999)
        )
        assert bad.status_code == 400 and "customer_id" in bad.json()["detail"]

        mine = (await admin.get(f"/api/noc/secrets?customer_id={acme}")).json()["secrets"]
        assert {s["name"] for s in mine} == {"acme core", "acme edge", "acme smtp"}
        assert (await admin.get(f"/api/noc/secrets?customer_id={globex}")).json()["secrets"] == []
        waiting = (await admin.get("/api/noc/secrets?unassigned=true")).json()["secrets"]
        assert [s["name"] for s in waiting] == ["old switch"]

        # A custodian moves the old one to Globex, stepped up and audited; it leaves the list.
        moved = await admin.post(
            f"/api/noc/secrets/{old.json()['id']}",
            json={
                **_cred("old switch", "ssh_password", "10.9.9.9", globex),
                "fields": None,
                "stepup": STEP,
            },
        )
        assert moved.status_code == 200, moved.text
        assert moved.json()["customer_name"] == "Globex"
        assert (await admin.get("/api/noc/secrets?unassigned=true")).json()["secrets"] == []

        # An agent sees whose equipment each reaches, and never a customer's mail server.
        for row in (ssh.json(), telnet.json(), smtp.json()):
            on = await admin.post(
                f"/api/noc/secrets/{row['id']}/agents", json={"enabled": True, "stepup": STEP}
            )
            assert on.status_code == 200, on.text
        seen = (await agent.get("/api/noc/agent")).json()["secrets"]
        assert {s["name"]: s["customer_name"] for s in seen} == {
            "acme core": "Acme",
            "acme edge": "Acme",
        }, "the SMTP credential is the appliance's to send through, not an agent's to lease"
    finally:
        await admin.aclose()
        await agent.aclose()


async def test_the_email_settings_name_the_customers_own_credentials(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    ids = await _estate(store)
    acme, globex = ids["acme"], ids["globex"]
    admin = await authutil.client_as(app, "admin")
    editor = await authutil.client_as(app, "editor")
    try:
        smtp = (
            await admin.post(
                "/api/noc/secrets",
                json=_cred(
                    "acme smtp",
                    "smtp",
                    "smtp.acme.example",
                    acme,
                    username="noc@acme.example",
                    password="Mail-Passw0rd!",
                ),
            )
        ).json()
        other = (
            await admin.post(
                "/api/noc/secrets",
                json=_cred(
                    "globex smtp",
                    "smtp",
                    "smtp.globex.example",
                    globex,
                    username="noc@globex.example",
                    password="Mail-Passw0rd!",
                ),
            )
        ).json()
        base = f"/api/noc/customers/{acme}/mail"
        first = (await editor.get(base)).json()
        assert first["version"] == 0 and first["recipients"] == "" and first["choices"] == []
        settings = {
            "recipients": "ops@acme.example; Oncall@Acme.example",
            "sender": "noc@acme.example",
        }
        saved = await editor.post(base, json={**settings, "version": 0})
        assert saved.status_code == 200, saved.text
        assert saved.json()["recipients"] == "ops@acme.example, Oncall@Acme.example"
        assert (await editor.post(base, json={**settings, "version": 0})).status_code == 409
        wrong = await editor.post(base, json={"recipients": "not-an-address", "version": 1})
        assert wrong.status_code == 400 and "recipients" in wrong.json()["detail"]
        # Choosing the credential is a custodian's; an editor is not one.
        refused = await editor.post(
            base, json={**settings, "smtp_secret_id": smtp["id"], "version": 1}
        )
        assert refused.status_code == 403
        foreign = await admin.post(
            base, json={**settings, "smtp_secret_id": other["id"], "version": 1}
        )
        assert foreign.status_code == 400 and "of this customer" in foreign.json()["detail"]
        chosen = await admin.post(
            base, json={**settings, "smtp_secret_id": smtp["id"], "version": 1}
        )
        assert chosen.status_code == 200, chosen.text
        body = chosen.json()
        assert body["smtp"]["name"] == "acme smtp" and "password" not in str(body)
        assert [c["name"] for c in body["choices"]] == ["acme smtp"]
        async with store.reading():
            cur = await store.conn.execute(
                "SELECT count(*) FROM audit_log WHERE action = 'noc.customer.mail'"
            )
            counted = await cur.fetchone()
            assert counted is not None and counted[0] == 2
    finally:
        await admin.aclose()
        await editor.aclose()


def _reply(
    *, sender: str, subject: str, refs: str = "", body: str = "", auto: bool = False
) -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = "noc@acme.example"
    message["Subject"] = subject
    message["Message-ID"] = f"<{abs(hash((sender, subject, refs, body)))}@customer.example>"
    if refs:
        message["In-Reply-To"] = refs
        message["References"] = refs
    if auto:
        message["Auto-Submitted"] = "auto-replied"
    message.set_content(body)
    return bytes(message)


async def test_the_update_is_emailed_and_the_reply_requeues_the_case(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    ids = await _estate(store)
    acme, globex = ids["acme"], ids["globex"]
    admin = await authutil.client_as(app, "admin")
    sent: list[EmailMessage] = []
    monkeypatch.setattr(mail, "send", lambda config, message, password=None: sent.append(message))
    try:
        smtp = (
            await admin.post(
                "/api/noc/secrets",
                json=_cred(
                    "acme smtp",
                    "smtp",
                    "smtp.acme.example",
                    acme,
                    username="noc@acme.example",
                    password="Mail-Passw0rd!",
                ),
            )
        ).json()
        imap = (
            await admin.post(
                "/api/noc/secrets",
                json=_cred(
                    "acme imap",
                    "imap",
                    "imap.acme.example",
                    acme,
                    username="noc@acme.example",
                    password="Mail-Passw0rd!",
                ),
            )
        ).json()
        assert imap["port"] == 993
        await admin.post(
            f"/api/noc/customers/{acme}/mail",
            json={
                "recipients": "ops@acme.example",
                "cc": "boss@acme.example",
                "sender": "noc@acme.example",
                "smtp_secret_id": smtp["id"],
                "imap_secret_id": imap["id"],
                "version": 0,
            },
        )
        # Two agents at tier 1; the case is taken by the first and set pending on the customer.
        async with store.lock:
            spec = {"role": "engineer", "adapter": "external", "entry": True}
            busy = await store.noc_create_agent({**spec, "name": "Atlas"}, time.time(), "t")
            free = await store.noc_create_agent({**spec, "name": "Nova"}, time.time(), "t")
            await store.commit()
        sid = await _case(store)
        assert (await admin.post(f"/api/noc/cases/{sid}/take")).status_code == 200
        assert (
            await admin.post(
                f"/api/noc/cases/{sid}", json={**COMPLETE, "next_update_at": time.time() + 3600}
            )
        ).status_code == 200
        async with store.lock:
            await store.noc_case_assign(sid, busy, time.time())
            await store.commit()
        answer = await admin.post(f"/api/noc/cases/{sid}/customer-update")
        assert answer.status_code == 200, answer.text
        assert (
            answer.json()["email"]
            and "Emailed the customer update to Acme" in answer.json()["email"][0]
        )
        assert len(sent) == 1
        message = sent[0]
        assert message["Subject"] == f"[NetCoreNOC #{sid}] Fibre span down"
        assert message["To"] == "ops@acme.example" and message["Cc"] == "boss@acme.example"
        assert "One link down between two sites" in message.get_content()
        pend = await admin.post(
            f"/api/noc/cases/{sid}/status",
            json={"status": "pending_customer", "next_update_at": time.time() + 3600},
        )
        assert pend.status_code == 200, pend.text

        replies = [
            _reply(
                sender="ops@acme.example",
                subject=f"Re: [NetCoreNOC #{sid}] Fibre span down",
                refs=str(message["Message-ID"]),
                body="Circuit ref ACME-778. Go ahead.\n\nOn Mon, NOC wrote:\n> old text",
            ),
            _reply(sender="stranger@evil.example", subject=f"[NetCoreNOC #{sid}] hi", body="x"),
            _reply(sender="ops@acme.example", subject="Out of office", body="away", auto=True),
        ]
        monkeypatch.setattr(inbox, "fetch_unseen", lambda box, limit=20: list(replies))
        counts = await mail_loop.tick(store)
        assert counts["posted"] == 1 and counts["refused"] == 2, counts
        async with store.reading():
            case = await store.noc_case(sid)
            cur = await store.conn.execute(
                "SELECT body, author_kind, visibility FROM noc_case_post WHERE situation_id = ? "
                "AND author_kind = 'customer'",
                (sid,),
            )
            customer_posts = [dict(r) for r in await cur.fetchall()]
            cur = await store.conn.execute(
                "SELECT reason FROM noc_case_cycle WHERE situation_id = ? ORDER BY id DESC LIMIT 1",
                (sid,),
            )
            last = await cur.fetchone()
            assert last is not None
            reason = last[0]
        assert case is not None and case["status"] == "queue" and case["agent_id"] is None, (
            "back in the queue, off its agent, for the routing to balance"
        )
        assert reason == "customer_email"
        assert len(customer_posts) == 1 and customer_posts[0]["visibility"] == "all"
        assert "Circuit ref ACME-778. Go ahead." in customer_posts[0]["body"]
        assert "old text" not in customer_posts[0]["body"], "the quoted history is cut"
        # Read twice: posted once.
        assert (await mail_loop.tick(store))["known"] == 3
        # The routing gives it to the agent with the fewest cases at its tier.
        async with store.reading():
            everyone = {int(a["id"]): a for a in await store.noc_agents()}
        loads = {busy: 3, free: 0}
        picked = routing.pick_entry(everyone, loads)
        assert picked is not None and picked["id"] == free

        # A reply naming a case of another customer is kept, not posted.
        other = await _case(store, device="192.168.50.1")
        stray = _reply(
            sender="ops@acme.example", subject=f"[NetCoreNOC #{other}] status?", body="?"
        )
        monkeypatch.setattr(inbox, "fetch_unseen", lambda box, limit=20: [stray])
        assert (await mail_loop.tick(store))["unmatched"] == 1
        log = (await admin.get(f"/api/noc/customers/{acme}/mail")).json()["messages"]
        assert {"sent", "posted", "refused", "unmatched"} <= {m["outcome"] for m in log}
        del globex
    finally:
        await admin.aclose()


async def test_without_a_mail_server_the_update_says_it_was_not_emailed(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    ids = await _estate(store)
    admin = await authutil.client_as(app, "admin")
    try:
        await admin.post(
            f"/api/noc/customers/{ids['acme']}/mail",
            json={"recipients": "ops@acme.example", "version": 0},
        )
        sid = await _case(store)
        assert (await admin.post(f"/api/noc/cases/{sid}/take")).status_code == 200
        full = {**COMPLETE, "next_update_at": time.time() + 3600}
        assert (await admin.post(f"/api/noc/cases/{sid}", json=full)).status_code == 200
        answer = (await admin.post(f"/api/noc/cases/{sid}/customer-update")).json()
        assert answer["sent"] is True
        assert answer["email"] == [
            "The customer update was not emailed to Acme: no SMTP server is set up."
        ]
    finally:
        await admin.aclose()


def test_a_reply_is_read_as_the_customers_words() -> None:
    raw = _reply(
        sender="Ops Team <OPS@acme.example>",
        subject=f"RE: [NetCoreNOC #{42}] Fibre span down",
        body="Approved.\r\n\r\nEm seg., 3 de nov. de 2026, NOC escreveu:\r\n> anything",
    )
    reply = custmail.parse(raw)
    assert reply.sender == "ops@acme.example" and not reply.automatic
    assert reply.text == "Approved."
    assert custmail.match(reply, None) == 42
    assert custmail.match(reply, 7) == 7, "a referenced message wins over the subject"
    assert custmail.addresses("a@x.example; b@y.example, bad, a@x.example") == [
        "a@x.example",
        "b@y.example",
    ]
    html_only = EmailMessage()
    html_only["From"] = "ops@acme.example"
    html_only["Subject"] = f"[NetCoreNOC #{5}] x"
    html_only.set_content(
        "<p>Line one</p><script>alert(1)</script><p>Line &amp; two</p>", subtype="html"
    )
    text = custmail.parse(bytes(html_only)).text
    assert "Line one" in text and "Line & two" in text and "alert" not in text
    no_id = custmail.parse(b"From: a@b.example\r\nSubject: hi\r\n\r\nbody")
    assert no_id.message_id.endswith("@netcorenoc.invalid>")
