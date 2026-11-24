"""The customers' email: sending a case's customer update, and reading the replies (ADR #481).

**Sending** (:func:`send_case_email`, ADR #500) is an email written on the case's feed: its own
To, Cc and Bcc, a subject with the case's tag, threaded under the message it answers — through the
customer's SMTP credential, opened from the vault inside the appliance, or else the appliance's
server (Settings → Email). The message is kept whole, and a post shows it on the feed.

**Reading** (:func:`tick`, every :data:`LOOK_EVERY_S` from :func:`mail_loop`) takes each
customer's unread replies from its IMAP mailbox and, for each:

* an address the customer has not registered (its recipients, copies and workbook) is ``refused``
  and an automatic reply is too — neither is posted;
* a reply that names no case of this customer is kept as ``unmatched``, for the Email tab;
* otherwise it is posted to the case's feed as the customer's (visible to everyone who reads the
  case), and a case **pending** goes back to the NOC queue, unassigned, so the routing gives it to
  the agent with the fewest cases at its tier — the balance the owner asked for. A case being
  worked stays with its owner, and an agent holding it is woken to read the reply.

The network is never touched under ``store.lock``: secrets are opened and messages fetched first,
then the lock is taken once to record and post.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from netcorenoc.crosscutting import inbox, mail, secretbox
from netcorenoc.crosscutting.vault import VaultError
from netcorenoc.engine.noc import case_ops, cases, custmail, kinds, richtext
from netcorenoc.engine.noc.agents import WORKING

if TYPE_CHECKING:
    from netcorenoc.store import Store

__all__ = ["LOOK_EVERY_S", "Draft", "mail_loop", "open_credential", "send_case_email", "tick"]

log = logging.getLogger(__name__)

#: How often the customers' mailboxes are read. A reply waits at most this long to be seen.
LOOK_EVERY_S = 60.0
#: The most messages read from one mailbox per pass.
PER_PASS = 20


async def open_credential(
    store: Store, secret_id: int | None, kind: str
) -> tuple[dict[str, Any], dict[str, str]] | None:
    """A customer's mail credential and its fields, opened inside the appliance — or None."""
    if secret_id is None:
        return None
    async with store.reading():
        row = await store.noc_get_secret(int(secret_id), sealed=True)
    if row is None or str(row["kind"]) != kind:
        return None
    binding = kinds.binding(
        str(row["ref"]),
        str(row["kind"]),
        str(row["host"]),
        int(row["port"]),
        str(row["protocol_version"]),
        str(row["access_level"]),
    )
    try:
        return row, secretbox.current().open(binding, str(row["sealed"]))
    except VaultError:
        return None


@dataclass(frozen=True)
class Draft:
    """An email written on a case (ADR #500): whom it is for, what it says, what it answers."""

    to: list[str]
    cc: list[str]
    bcc: list[str]
    subject: str
    doc: list[dict[str, Any]]
    in_reply_to: str = ""
    refs: tuple[str, ...] = ()


async def _sender_for(
    store: Store, cfg: Mapping[str, Any] | None
) -> tuple[mail.MailConfig | None, str | None, str]:
    """How a customer's email leaves: its own SMTP credential, else the appliance's server."""
    async with store.reading():
        fallback = mail.from_document(await store.get_meta(mail.META_KEY))
    opened = await open_credential(store, cfg["smtp_secret_id"], "smtp") if cfg else None
    if opened is not None and cfg is not None:
        secret, fields = opened
        config = custmail.smtp_config(
            secret, fields, sender=str(cfg["sender"]), sender_name=str(cfg["sender_name"])
        )
        return config, str(fields.get("password") or ""), str(secret["name"])
    if fallback.host and fallback.sender:
        return fallback, None, "the appliance's email server"
    return None, None, ""


async def send_case_email(
    store: Store, case: Mapping[str, Any], organization_id: int, draft: Draft, actor: Any
) -> dict[str, Any]:
    """Send one email written on a case and put it on the case's feed (ADR #500).

    The network round trip happens first, outside the lock; then, in one transaction, the message
    is recorded whole (blind copies included, for the NOC) and a feed post shows it — sent or not,
    since a message that failed is still something the NOC tried to tell the customer. Returns
    ``{mail_id, post_id, outcome, detail, through}``.
    """
    sid = int(case["situation_id"])
    async with store.reading():
        cfg = await store.noc_customer_mail(organization_id)
    config, password, through = await _sender_for(store, cfg)
    text = richtext.to_text(draft.doc)
    sender = str(cfg["sender"]) if cfg and cfg["sender"] else (config.sender if config else "")
    name = str(cfg["sender_name"]) if cfg and cfg["sender_name"] else "NetCoreNOC"
    subject = custmail.tagged(sid, draft.subject)
    if config is None or not sender:
        outcome, detail = "failed", "no SMTP server is set up for this customer or the appliance"
        message_id = f"<unsent-{sid}-{time.time()}@netcorenoc.invalid>"
    else:
        message = custmail.compose(
            sid,
            sender=sender,
            sender_name=name,
            to=draft.to,
            cc=draft.cc,
            subject=subject,
            text=text,
            html_body=richtext.to_html(draft.doc),
            in_reply_to=draft.in_reply_to,
            refs=draft.refs,
        )
        message_id = str(message["Message-ID"])
        try:
            envelope = [*draft.to, *draft.cc, *draft.bcc]
            await asyncio.to_thread(
                mail.send, config, message, password=password, envelope=envelope
            )
            outcome, detail = "sent", ""
        except mail.MailError as exc:
            outcome, detail = "failed", str(exc)
    now = time.time()
    head = f"Email to {', '.join(draft.to)} — {subject}"
    body = f"{head}\n\n{text}" if outcome == "sent" else f"{head}\n\nNot sent: {detail}"
    async with store.lock:
        mail_id = await store.noc_mail_add(
            organization_id,
            sid,
            "out",
            outcome,
            message_id,
            now,
            subject=subject,
            sender=sender,
            sender_name=name,
            recipients=", ".join(draft.to),
            cc=", ".join(draft.cc),
            bcc=", ".join(draft.bcc),
            in_reply_to=draft.in_reply_to,
            refs=" ".join(draft.refs),
            body_text=text,
            body_doc=json.dumps(draft.doc, separators=(",", ":")),
            sent_by=str(actor.name),
            detail=detail,
        )
        post_id = await store.noc_post(
            sid, {**actor.author()}, visibility="all", kind="email", body=body[:7900], now=now
        )
        if mail_id is not None:
            await store.noc_mail_attach(mail_id, post_id)
        await store.commit()
    return {
        "mail_id": mail_id,
        "post_id": post_id,
        "outcome": outcome,
        "detail": detail,
        "through": through,
    }


async def _read(store: Store, cfg: Mapping[str, Any]) -> tuple[list[bytes], str]:
    opened = await open_credential(store, cfg["imap_secret_id"], "imap")
    if opened is None:
        return [], "the IMAP credential is missing or cannot be opened"
    secret, fields = opened
    try:
        return await asyncio.to_thread(
            inbox.fetch_unseen, custmail.mailbox(secret, fields), limit=PER_PASS
        ), ""
    except inbox.InboxError as exc:
        return [], str(exc)


async def _receive(store: Store, oid: int, raw: bytes, now: float) -> str:
    """One message, under the lock: its outcome."""
    reply = custmail.parse(raw)
    if await store.noc_mail_known(oid, reply.message_id):
        return "known"

    async def kept(outcome: str, sid: int | None, detail: str = "", post: int | None = None) -> str:
        await store.noc_mail_record(
            oid,
            sid,
            "in",
            outcome,
            reply.message_id,
            now,
            subject=reply.subject,
            sender=reply.sender,
            sender_name=reply.sender_name,
            recipients=reply.to,
            cc=reply.cc,
            refs=" ".join(reply.refs),
            in_reply_to=reply.refs[-1] if reply.refs else "",
            body_text=reply.text,
            post_id=post,
            detail=detail,
        )
        return outcome

    if reply.automatic:
        return await kept("refused", None, "an automatic reply")
    if reply.sender not in await store.noc_customer_addresses(oid):
        return await kept("refused", None, "not an address this customer registered")
    sid = custmail.match(reply, await store.noc_mail_case_for(oid, reply.refs))
    case = await store.noc_case(sid) if sid is not None else None
    if sid is None or case is None or case["status"] == "closed":
        return await kept("unmatched", None, "names no open case of this customer")
    if not any(int(o["id"]) == oid for o in await store.noc_case_organizations(sid)):
        return await kept("unmatched", None, "names a case of another customer")
    author = {"ref": f"email:{reply.sender}", "name": reply.sender, "kind": "customer"}
    post = await store.noc_post(
        sid,
        {**author, "role": None},
        visibility="all",
        kind="email",
        body=custmail.post_body(reply),
        now=now,
    )
    await kept("posted", sid, post=post)
    if case["status"] in cases.PENDING:
        await case_ops.requeue(store, sid, "customer_email", now, rebalance=True)
    elif case["status"] == "working" and case.get("agent_id") is not None:
        agent = await store.noc_agent(int(case["agent_id"]))
        if agent is not None and agent["status"] in WORKING and agent["adapter"] != "external":
            await store.noc_wake(int(agent["id"]), "assignment", sid, "system", now)
    return "posted"


async def tick(store: Store, *, now: float | None = None) -> dict[str, int]:
    """Read every set-up mailbox once; how many messages ended each way."""
    counts = {"posted": 0, "refused": 0, "unmatched": 0, "known": 0, "errors": 0}
    async with store.reading():
        mailboxes = await store.noc_customer_mails_to_read()
    for cfg in mailboxes:
        oid = int(cfg["organization_id"])
        messages, error = await _read(store, cfg)
        at = time.time() if now is None else now
        async with store.lock:
            for raw in messages:
                counts[await _receive(store, oid, raw, at)] += 1
            await store.noc_mail_polled(oid, at, error)
            await store.commit()
        counts["errors"] += bool(error)
    return counts


async def mail_loop(store: Store) -> None:
    """Forever: read the customers' mailboxes every :data:`LOOK_EVERY_S`."""
    while True:
        try:
            await tick(store)
        except Exception:  # a pass that fails is logged; the next one runs
            log.exception("reading the customers' mailboxes failed")
        await asyncio.sleep(LOOK_EVERY_S)
