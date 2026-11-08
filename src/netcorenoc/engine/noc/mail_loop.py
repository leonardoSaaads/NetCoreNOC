"""The customers' email: sending a case's customer update, and reading the replies (ADR #481).

**Sending** (:func:`send_update`) follows the post that sends the update to the case's feed: for
each customer whose equipment the case touches and whose mailbox is set up, the update is emailed
to its recipients — through the customer's own SMTP credential, opened from the vault inside the
appliance, or else through the appliance's server (Settings → Email). Each send is recorded with
its Message-ID and an internal event on the feed says where it went or why it did not.

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
import logging
import time
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from netcorenoc.crosscutting import inbox, mail, secretbox
from netcorenoc.crosscutting.vault import VaultError
from netcorenoc.engine.noc import case_ops, cases, custmail, kinds
from netcorenoc.engine.noc.agents import WORKING

if TYPE_CHECKING:
    from netcorenoc.store import Store

__all__ = ["LOOK_EVERY_S", "mail_loop", "open_credential", "send_update", "tick"]

log = logging.getLogger(__name__)

#: How often the customers' mailboxes are read. A reply waits at most this long to be seen.
LOOK_EVERY_S = 60.0
#: The most messages read from one mailbox per pass.
PER_PASS = 20
CUSTOMER = case_ops.Actor("customer", "The customer", "system", None)


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


async def send_update(store: Store, case: Mapping[str, Any], now: float) -> list[str]:
    """Email the case's customer update to each of its customers whose mailbox is set up. Returns
    one sentence per customer, already posted as an internal event. Call outside the lock."""
    sid = int(case["situation_id"])
    async with store.reading():
        organizations = await store.noc_case_organizations(sid)
        settings = {
            int(o["id"]): await store.noc_customer_mail(int(o["id"])) for o in organizations
        }
        fallback = mail.from_document(await store.get_meta(mail.META_KEY))
    said: list[str] = []
    for org in organizations:
        oid = int(org["id"])
        cfg = settings.get(oid)
        if not cfg or not cfg["send_updates"]:
            continue
        to = custmail.addresses(str(cfg["recipients"]))
        cc = custmail.addresses(str(cfg["cc"]))
        if not to:
            continue
        opened = await open_credential(store, cfg["smtp_secret_id"], "smtp")
        if opened is not None:
            secret, fields = opened
            config = custmail.smtp_config(
                secret, fields, sender=str(cfg["sender"]), sender_name=str(cfg["sender_name"])
            )
            password: str | None = str(fields.get("password") or "")
            through = f"{secret['name']}"
        elif fallback.host and fallback.sender:
            config, password, through = fallback, None, "the appliance's email server"
        else:
            config, password, through = None, None, ""
        name = str(org.get("name") or f"customer {oid}")
        if config is None:
            outcome, detail, message_id = (
                "failed",
                "no SMTP server is set up",
                f"<unsent-{sid}-{now}>",
            )
        else:
            sender = str(cfg["sender"]) or config.sender
            message = custmail.compose_update(
                case,
                sender=sender,
                sender_name=str(cfg["sender_name"]) or config.sender_name,
                to=to,
                cc=cc,
            )
            message_id = str(message["Message-ID"])
            try:
                await asyncio.to_thread(mail.send, config, message, password=password)
                outcome, detail = "sent", ""
            except mail.MailError as exc:
                outcome, detail = "failed", str(exc)
        recipients = ", ".join(to + cc)
        sentence = (
            f"Emailed the customer update to {name} ({recipients}) through {through}."
            if outcome == "sent"
            else f"The customer update was not emailed to {name}: {detail}."
        )
        async with store.lock:
            await store.noc_mail_record(
                oid,
                sid,
                "out",
                outcome,
                message_id,
                time.time(),
                subject=custmail.subject_for(sid, str(case.get("title") or "")),
                sender=str(cfg["sender"]),
                recipients=recipients,
                detail=detail,
            )
            await case_ops.event(store, sid, sentence, time.time())
            await store.commit()
        said.append(sentence)
    return said


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
        kind="post",
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
