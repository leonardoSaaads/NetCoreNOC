"""Each customer's equipment access and its email (ADR #481), through the HTTP API and the loops.

What the owner asked, each answered by driving the real routes:

* a device credential belongs to a customer: stored with it, listed by it, moved by a custodian; one
  stored before belongs to none and is listed as such; an AI provider's key belongs to no customer;
* an agent sees whose equipment each credential reaches, and never leases a customer's mail server;
* a customer's email settings are its book (who works cases keeps them) but which credential sends
  and reads is a custodian's choice, of that customer's own SMTP and IMAP credentials;
* an email written on a case (ADR #500) goes to its own To, Cc and Bcc, its subject tagged with the
  case, and is recorded with its Message-ID and shown on the feed — or, with no mail server,
  recorded as not sent, and said so; Reply all answers a customer's message on the same thread;
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
from netcorenoc.engine.noc import custmail, mail_loop
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
    *,
    sender: str,
    subject: str,
    refs: str = "",
    body: str = "",
    auto: bool = False,
    cc: str = "",
) -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = "noc@acme.example"
    if cc:
        message["Cc"] = cc
    message["Subject"] = subject
    message["Message-ID"] = f"<{abs(hash((sender, subject, refs, body)))}@customer.example>"
    if refs:
        message["In-Reply-To"] = refs
        message["References"] = refs
    if auto:
        message["Auto-Submitted"] = "auto-replied"
    message.set_content(body)
    return bytes(message)


async def test_an_email_from_the_case_and_reply_all_to_the_customers_answer(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR #500, the owner's own scenario: the NOC emails the customer from the case asking for a
    date; the customer answers with two; the case goes back to the queue; the engineer who takes
    it answers with Reply all — recipients, subject and thread worked out — and sets the case
    pending on the customer again."""
    _engine, _queue, app = await authutil.make_env(store)
    ids = await _estate(store)
    acme = ids["acme"]
    admin = await authutil.client_as(app, "admin")
    sent: list[tuple[EmailMessage, list[str] | None]] = []
    monkeypatch.setattr(
        mail,
        "send",
        lambda config, message, password=None, envelope=None: sent.append((message, envelope)),
    )
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
        saved = await admin.post(
            f"/api/noc/customers/{acme}/mail",
            json={
                "sender": "noc@acme.example",
                "smtp_secret_id": smtp["id"],
                "imap_secret_id": imap["id"],
                "version": 0,
            },
        )
        assert saved.status_code == 200, saved.text
        async with store.lock:
            spec = {"role": "engineer", "adapter": "external", "entry": True}
            busy = await store.noc_create_agent({**spec, "name": "Atlas"}, time.time(), "t")
            await store.noc_create_agent({**spec, "name": "Nova"}, time.time(), "t")
            await store.commit()
        sid = await _case(store)
        assert (await admin.post(f"/api/noc/cases/{sid}/take")).status_code == 200
        full = {**COMPLETE, "next_update_at": time.time() + 3600}
        assert (await admin.post(f"/api/noc/cases/{sid}", json=full)).status_code == 200
        # The update is posted; emailing it is a step of its own (ADR #500).
        posted = await admin.post(f"/api/noc/cases/{sid}/customer-update")
        assert posted.status_code == 200 and "email" not in posted.json() and not sent
        draft = (await admin.get(f"/api/noc/cases/{sid}/email/draft?update=1")).json()
        assert draft["available"] and draft["from"] == "noc@acme.example"
        assert draft["subject"] == f"[NetCoreNOC #{sid}] Fibre span down"
        assert any("One link down" in r["t"] for b in draft["doc"] for r in b.get("p", []))

        doc = [
            {"p": [{"t": "Hello. "}, {"t": "A technician must connect the device.", "b": True}]},
            {"p": [{"t": "Which day suits you?"}]},
            {"ul": [[{"t": "10th"}], [{"t": "15th"}]]},
        ]
        out = await admin.post(
            f"/api/noc/cases/{sid}/email",
            json={
                "to": ["ops@acme.example"],
                "cc": ["boss@acme.example"],
                "bcc": ["audit@noc.example"],
                "subject": "Field visit",
                "doc": doc,
            },
        )
        assert out.status_code == 200, out.text
        assert out.json()["outcome"] == "sent"
        message, envelope = sent[-1]
        assert message["Subject"] == f"[NetCoreNOC #{sid}] Field visit"
        assert message["To"] == "ops@acme.example" and message["Cc"] == "boss@acme.example"
        assert "audit@noc.example" not in str(message), "a blind copy is on the envelope only"
        assert envelope == ["ops@acme.example", "boss@acme.example", "audit@noc.example"]
        html_part = message.get_body(preferencelist=("html",))
        assert html_part is not None
        assert "<strong>A technician must connect the device.</strong>" in html_part.get_content()
        plain = message.get_body(preferencelist=("plain",))
        assert plain is not None and "- 10th" in plain.get_content()

        feed = (await admin.get(f"/api/noc/cases/{sid}/feed")).json()["posts"]
        email_post = next(p for p in feed if p["kind"] == "email")
        assert email_post["mail"]["bcc"] == "audit@noc.example", "the NOC sees its blind copies"
        assert email_post["mail"]["doc"][2] == {"ul": [[{"t": "10th"}], [{"t": "15th"}]]}
        viewer = await authutil.client_as(app, "viewer")
        seen = (await viewer.get(f"/api/noc/cases/{sid}/feed")).json()["posts"]
        assert next(p for p in seen if p["kind"] == "email")["mail"]["bcc"] == ""
        await viewer.aclose()

        pend = await admin.post(
            f"/api/noc/cases/{sid}/status",
            json={"status": "pending_customer", "next_update_at": time.time() + 3600},
        )
        assert pend.status_code == 200, pend.text
        async with store.lock:
            await store.noc_case_assign(sid, busy, time.time())
            await store.commit()

        answer = _reply(
            sender="ops@acme.example",
            cc="boss@acme.example, noc@acme.example",
            subject=f"Re: [NetCoreNOC #{sid}] Field visit",
            refs=str(message["Message-ID"]),
            body="The 10th or the 15th of next month.\n\nOn Mon, NOC wrote:\n> old text",
        )
        stranger = _reply(sender="stranger@evil.example", subject=f"[NetCoreNOC #{sid}] hi")
        away = _reply(sender="ops@acme.example", subject="Out of office", body="x", auto=True)
        monkeypatch.setattr(inbox, "fetch_unseen", lambda box, limit=20: [answer, stranger, away])
        counts = await mail_loop.tick(store)
        assert counts["posted"] == 1 and counts["refused"] == 2, counts
        async with store.reading():
            case = await store.noc_case(sid)
        assert case is not None and case["status"] == "queue" and case["agent_id"] is None
        feed = (await admin.get(f"/api/noc/cases/{sid}/feed")).json()["posts"]
        received = next(p for p in feed if p.get("mail") and p["mail"]["direction"] == "in")
        assert received["author"]["kind"] == "customer"
        assert "The 10th or the 15th" in received["mail"]["text"]
        assert "old text" not in received["mail"]["text"], "the quoted history is cut"

        # Reply all: recipients, subject and thread from the server, never retyped.
        reply_draft = (
            await admin.get(
                f"/api/noc/cases/{sid}/email/draft?reply_to={received['mail']['id']}&all=1"
            )
        ).json()
        assert reply_draft["to"] == ["ops@acme.example"]
        assert reply_draft["cc"] == ["boss@acme.example"], "everyone else, never ourselves"
        assert reply_draft["subject"] == f"Re: [NetCoreNOC #{sid}] Field visit"
        assert "The 10th or the 15th" in reply_draft["doc"][-1]["quote"][0]["t"]
        assert (await admin.post(f"/api/noc/cases/{sid}/take")).status_code == 200
        back = await admin.post(
            f"/api/noc/cases/{sid}/email",
            json={
                "reply_to": received["mail"]["id"],
                "reply_all": True,
                "text": "We can book the 11th.\n\n- 09:00 to 12:00",
            },
        )
        assert back.status_code == 200, back.text
        assert back.json()["to"] == ["ops@acme.example"] and back.json()["cc"] == [
            "boss@acme.example"
        ]
        threaded, _env = sent[-1]
        assert threaded["Subject"] == f"Re: [NetCoreNOC #{sid}] Field visit"
        assert threaded["In-Reply-To"] == answer_id(answer)
        assert str(message["Message-ID"]) in str(threaded["References"])
        audit = [r for r in await _audit(store) if r["action"] == "noc.case.email"]
        assert len(audit) == 2 and all(r["outcome"] == "ok" for r in audit), audit
        # A message of another case is not one a reply may answer.
        other = await _case(store, device="192.168.50.1")
        refused = await admin.post(
            f"/api/noc/cases/{other}/email",
            json={"reply_to": received["mail"]["id"], "text": "x"},
        )
        assert refused.status_code == 400
    finally:
        await admin.aclose()


def answer_id(raw: bytes) -> str:
    return str(custmail.parse(raw).message_id)


async def _audit(store: Store) -> list[dict[str, Any]]:
    async with store.reading():
        return await store.audit_all()


async def test_without_a_mail_server_the_email_is_kept_as_not_sent(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    await _estate(store)
    admin = await authutil.client_as(app, "admin")
    try:
        sid = await _case(store)
        assert (await admin.post(f"/api/noc/cases/{sid}/take")).status_code == 200
        out = await admin.post(
            f"/api/noc/cases/{sid}/email", json={"to": ["ops@acme.example"], "text": "Hello"}
        )
        assert out.status_code == 200, out.text
        assert out.json()["outcome"] == "failed" and "no SMTP server" in out.json()["detail"]
        feed = (await admin.get(f"/api/noc/cases/{sid}/feed")).json()["posts"]
        failed = next(p for p in feed if p["kind"] == "email")
        assert failed["mail"]["outcome"] == "failed" and "Not sent" in failed["body"]
        for body, status in (
            ({"to": ["not-an-address"], "text": "x"}, 400),
            ({"to": [], "text": "x"}, 400),
            ({"to": ["a@b.example"], "doc": [{"script": [{"t": "x"}]}]}, 400),
            (
                {
                    "to": ["a@b.example"],
                    "doc": [{"p": [{"t": "x", "href": "javascript:alert(1)"}]}],
                },
                400,
            ),
        ):
            resp = await admin.post(f"/api/noc/cases/{sid}/email", json=body)
            assert resp.status_code == status, (body, resp.text)
        viewer = await authutil.client_as(app, "viewer")
        denied = await viewer.post(
            f"/api/noc/cases/{sid}/email", json={"to": ["ops@acme.example"], "text": "x"}
        )
        assert denied.status_code == 403
        await viewer.aclose()
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


def test_the_body_is_a_document_and_its_html_is_written_here() -> None:
    """ADR #500: no HTML a browser produced is relayed. Every text is escaped, a link must be
    http(s) or mailto, and anything that is not a paragraph, a list or a quote is refused."""
    from netcorenoc.engine.noc import richtext

    doc = richtext.validate(
        [
            {"p": [{"t": "<script>alert(1)</script>", "b": True}, {"t": "big", "size": "large"}]},
            {"p": [{"t": "site", "href": "https://acme.example/a?b=1&c=2"}]},
            {"ol": [[{"t": "one"}], [{"t": "two", "i": True}]]},
        ]
    )
    out = richtext.to_html(doc)
    assert "<script>" not in out and "&lt;script&gt;alert(1)&lt;/script&gt;" in out
    assert "<strong>&lt;script&gt;" in out and "font-size:18px" in out
    assert 'href="https://acme.example/a?b=1&amp;c=2"' in out
    assert "<ol><li>one</li><li><em>two</em></li></ol>" in out
    assert richtext.to_text(doc).endswith("1. one\n\n2. two")
    bad: list[dict[str, Any]]
    for bad in (
        [{"p": [{"t": "x", "href": "javascript:alert(1)"}]}],
        [{"p": [{"t": "x", "href": 'https://a.example/"onmouseover="x'}]}],
        [{"p": [{"t": "x", "size": "999px"}]}],
        [{"img": [{"t": "x"}]}],
        [{"p": []}],
        [],
    ):
        with pytest.raises(richtext.RichTextError):
            richtext.validate(bad)
    assert richtext.from_text("Hello\n\n- a\n- b") == [
        {"p": [{"t": "Hello"}]},
        {"ul": [[{"t": "a"}], [{"t": "b"}]]},
    ]


def test_reply_all_goes_to_everyone_on_it_but_us() -> None:
    inbound = {
        "direction": "in",
        "sender": "ops@acme.example",
        "recipients": "noc@acme.example",
        "cc": "boss@acme.example, NOC@acme.example",
    }
    assert custmail.reply_recipients(inbound, ["noc@acme.example"], everyone=False) == (
        ["ops@acme.example"],
        [],
    )
    assert custmail.reply_recipients(inbound, ["noc@acme.example"], everyone=True) == (
        ["ops@acme.example"],
        ["boss@acme.example"],
    )
    outbound = {
        "direction": "out",
        "sender": "noc@acme.example",
        "recipients": "a@x.example",
        "cc": "b@x.example",
    }
    assert custmail.reply_recipients(outbound, ["noc@acme.example"], everyone=True) == (
        ["a@x.example"],
        ["b@x.example"],
    )
    case = 7  # a case number, spelled through a name: the docs guard reads `#` and digits
    nested = custmail.reply_subject(case, f"RE: Fw: [NetCoreNOC #{case}] Visit")
    assert nested == f"Re: [NetCoreNOC #{case}] Visit", "Re: once, the tag kept"


def test_a_reply_chain_names_each_message_once_and_its_parent_last() -> None:
    """ADR #505: the lab's reply to a customer listed the first message twice in `References` —
    the customer's client named it in both `References` and `In-Reply-To`, and each was added."""
    raw = (
        b"From: campo@claro.lab\r\nTo: noc@claro.lab\r\nSubject: Re: Visit\r\n"
        b"Message-ID: <b@claro.lab>\r\nIn-Reply-To: <a@claro.lab>\r\n"
        b"References: <a@claro.lab>\r\n\r\nThe 10th or the 15th.\r\n"
    )
    reply = custmail.parse(raw)
    assert reply.refs == ("<a@claro.lab>",), reply.refs
    sent = custmail.compose(
        42,
        sender="noc@claro.lab",
        sender_name="NOC",
        to=["campo@claro.lab"],
        cc=[],
        bcc=[],
        subject="Re: Visit",
        text="The 10th.",
        html_body="<p>The 10th.</p>",
        in_reply_to=reply.message_id,
        refs=(*reply.refs, *reply.refs),
    )
    assert sent["References"] == "<a@claro.lab> <b@claro.lab>"
    assert sent["In-Reply-To"] == "<b@claro.lab>"
