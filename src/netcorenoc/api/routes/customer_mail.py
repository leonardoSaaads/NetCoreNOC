"""NOC → a customer's Email: who the customer update goes to, the mailbox replies come back to, and
every message sent or read (ADR #481).

* ``GET …/mail`` — the settings, the credentials they name (by name, host and port: never a
  field), the customer's mail credentials a custodian may choose from, and the newest messages.
* ``POST …/mail`` — change the settings. The recipients and the sender are the customer's book,
  kept by who works cases; **which credential** sends and reads is a custodian's choice, so a
  change to either asks the caller to be one — the same gate as the vault.
* ``POST …/mail/test`` — send a short test message to the recipients, through the credential set.

The mailbox password never crosses this module: the credential is opened inside the appliance by
`engine/noc/mail_loop.py` when a message is sent or read.
"""

from __future__ import annotations

import asyncio
import time
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from netcorenoc.api import noc_gate as gate
from netcorenoc.api.context import AppContext
from netcorenoc.api.customer_view import editor_of, visible_customer
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.crosscutting import auth, mail
from netcorenoc.engine.noc import custmail, mail_loop


class CustomerMailIn(BaseModel):
    """A customer's email settings, whole. ``version`` is the one read (0: none yet)."""

    model_config = ConfigDict(extra="forbid")

    recipients: str = Field(default="", max_length=2000)
    cc: str = Field(default="", max_length=2000)
    sender: str = Field(default="", max_length=254)
    sender_name: str = Field(default="", max_length=120)
    smtp_secret_id: int | None = Field(default=None, ge=1)
    imap_secret_id: int | None = Field(default=None, ge=1)
    send_updates: bool = True
    read_replies: bool = True
    version: int = Field(default=0, ge=0)


def _listed(text: str, what: str) -> str:
    """A typed list of addresses, every one valid, normalised to ``a, b`` — or a 400."""
    parts = [p for p in text.replace(";", ",").replace("\n", ",").split(",") if p.strip()]
    bad = [p.strip() for p in parts if not mail.valid_address(p.strip())]
    if bad:
        raise HTTPException(status_code=400, detail=f"{what}: {bad[0][:80]} is not an address")
    return ", ".join(custmail.addresses(text))


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register a customer's email routes on `app`."""
    store, security = ctx.store, ctx.security
    audit_row, write_txn = ctx.perimeter.audit_row, ctx.write_txn
    route = DeclaredRoutes(app)

    def credential(row: dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {k: row[k] for k in ("id", "name", "kind", "host", "port", "protocol_version")}

    async def settings_view(customer_id: int, request: Request, principal: auth.Principal) -> Any:
        await visible_customer(ctx, principal, customer_id)
        custodian = await gate.is_custodian(ctx, request, principal)
        async with store.reading():
            cfg = await store.noc_customer_mail(customer_id)
            mine = await store.noc_list_secrets(organization_id=customer_id)
            log = await store.noc_mail_log(organization_id=customer_id, limit=30)
            chosen = {
                k: await store.noc_get_secret(int(cfg[k])) if cfg and cfg[k] else None
                for k in ("smtp_secret_id", "imap_secret_id")
            }
            fallback = mail.from_document(await store.get_meta(mail.META_KEY))
        out: dict[str, Any] = {
            "recipients": cfg["recipients"] if cfg else "",
            "cc": cfg["cc"] if cfg else "",
            "sender": cfg["sender"] if cfg else "",
            "sender_name": cfg["sender_name"] if cfg else "",
            "send_updates": bool(cfg["send_updates"]) if cfg else True,
            "read_replies": bool(cfg["read_replies"]) if cfg else True,
            "smtp": credential(chosen["smtp_secret_id"]),
            "imap": credential(chosen["imap_secret_id"]),
            "polled_at": cfg["polled_at"] if cfg else None,
            "poll_error": cfg["poll_error"] if cfg else "",
            "version": int(cfg["version"]) if cfg else 0,
            "fallback": bool(fallback.host and fallback.sender),
            "custodian": custodian,
            # The customer's own mail credentials, for a custodian to choose from.
            "choices": [credential(r) for r in mine if r["kind"] in ("smtp", "imap")]
            if custodian
            else [],
            "messages": log,
            "may_edit": "cases.work" in request.state.capabilities,
            "look_every_s": mail_loop.LOOK_EVERY_S,
        }
        return out

    @route.get("/api/noc/customers/{customer_id}/mail")
    async def get_mail(
        customer_id: int, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """Whom the customer update is emailed to, the mailbox replies are read from, and the
        newest messages sent and read."""
        out: dict[str, Any] = await settings_view(customer_id, request, principal)
        return out

    @route.post("/api/noc/customers/{customer_id}/mail")
    async def set_mail(
        customer_id: int,
        body: CustomerMailIn,
        request: Request,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """Change whom the customer update goes to and which credentials send it and read the
        replies. Choosing a credential is a custodian's act."""
        await visible_customer(ctx, principal, customer_id)
        (await editor_of(ctx, request, principal)).require_work()
        recipients = _listed(body.recipients, "recipients")
        cc = _listed(body.cc, "cc")
        sender = body.sender.strip()
        if sender and not mail.valid_address(sender):
            raise HTTPException(status_code=400, detail="sender: not an address")
        async with store.reading():
            current = await store.noc_customer_mail(customer_id)
        before = {
            k: (current[k] if current else None) for k in ("smtp_secret_id", "imap_secret_id")
        }
        wanted = {"smtp_secret_id": body.smtp_secret_id, "imap_secret_id": body.imap_secret_id}
        if wanted != before:
            await gate.require_custodian(ctx, request, principal)
            async with store.reading():
                for key, kind in (("smtp_secret_id", "smtp"), ("imap_secret_id", "imap")):
                    if wanted[key] is None:
                        continue
                    row = await store.noc_get_secret(int(wanted[key] or 0))
                    if row is None or row["kind"] != kind or row["organization_id"] != customer_id:
                        raise HTTPException(
                            status_code=400,
                            detail=f"{key}: not an {kind.upper()} credential of this customer",
                        )
        async with write_txn():
            now_version = int(current["version"]) if current else 0
            fresh = await store.noc_customer_mail(customer_id)
            if (int(fresh["version"]) if fresh else 0) != body.version or (
                now_version != body.version
            ):
                raise HTTPException(
                    status_code=409, detail="the settings changed since you read them; reload"
                )
            await store.noc_set_customer_mail(
                customer_id,
                {
                    "recipients": recipients,
                    "cc": cc,
                    "sender": sender,
                    "sender_name": body.sender_name.strip(),
                    **wanted,
                    "send_updates": body.send_updates,
                    "read_replies": body.read_replies,
                },
                time.time(),
                principal.actor,
            )
            await audit_row(
                request,
                principal,
                "noc.customer.mail",
                "ok",
                object_type="organization",
                object_id=str(customer_id),
                details={
                    "recipients": len(custmail.addresses(recipients)),
                    "cc": len(custmail.addresses(cc)),
                    "credentials_changed": wanted != before,
                    "send_updates": body.send_updates,
                    "read_replies": body.read_replies,
                },
            )
        out: dict[str, Any] = await settings_view(customer_id, request, principal)
        return out

    @route.post("/api/noc/customers/{customer_id}/mail/test")
    async def test_mail(
        customer_id: int, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """Send a short test message to the recipients through the credential set (or the
        appliance's server). Says what the server answered."""
        row = await visible_customer(ctx, principal, customer_id)
        (await editor_of(ctx, request, principal)).require_work()
        async with store.reading():
            cfg = await store.noc_customer_mail(customer_id)
            fallback = mail.from_document(await store.get_meta(mail.META_KEY))
        to = custmail.addresses(str(cfg["recipients"])) if cfg else []
        if not cfg or not to:
            raise HTTPException(status_code=400, detail="recipients: add at least one first")
        opened = await mail_loop.open_credential(store, cfg["smtp_secret_id"], "smtp")
        if opened is not None:
            secret, fields = opened
            config = custmail.smtp_config(
                secret, fields, sender=str(cfg["sender"]), sender_name=str(cfg["sender_name"])
            )
            password: str | None = str(fields.get("password") or "")
        elif fallback.host and fallback.sender:
            config, password = fallback, None
        else:
            raise HTTPException(
                status_code=400, detail="no SMTP server is set up for this customer"
            )
        sender = str(cfg["sender"]) or config.sender
        message = EmailMessage()
        message["From"] = formataddr((str(cfg["sender_name"]) or config.sender_name, sender))
        message["To"] = ", ".join(to)
        message["Subject"] = f"NetCoreNOC: test message for {row['name']}"
        message["Date"] = formatdate(localtime=False)
        message["Message-ID"] = make_msgid(domain=sender.rsplit("@", 1)[-1])
        message.set_content(
            "This is a test from the NOC. Customer updates for your cases will come from this "
            "address; replying to them adds your answer to the case.\n"
        )
        try:
            await asyncio.to_thread(mail.send, config, message, password=password)
            ok, detail = True, f"sent to {', '.join(to)}"
        except mail.MailError as exc:
            ok, detail = False, str(exc)
        async with write_txn():
            await audit_row(
                request,
                principal,
                "noc.customer.mail",
                "ok" if ok else "error",
                object_type="organization",
                object_id=str(customer_id),
                details={"test": True, "sent": ok},
            )
        return {"ok": ok, "detail": detail}
