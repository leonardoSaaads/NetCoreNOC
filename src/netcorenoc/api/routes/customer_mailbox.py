"""A customer's mailbox, set up in steps from its Email tab (ADR #510).

The owner's rule: Access is for the customer's **hosts**; its email is set up where it is used, on
the Email tab — provider, account, test, save — with its own login, never by storing a credential
in Access first. So these two routes take the account the wizard describes:

* ``POST …/mail/try`` — send a test message through it (and, when replies are read, sign in to its
  mailbox) **without storing anything**: the password travels once, to be used and forgotten;
* ``POST …/mail/account`` — keep it: the login is sealed into the **vault** as the customer's
  SMTP (and IMAP) credential — the same box, two layers, custodians only, every act audited — and
  the customer's email settings point at it. A Microsoft sign-in (`/api/mail/oauth/…`) brings its
  refresh token from the server's memory to the vault without ever reaching the browser.

Both are a custodian's (the vault's gate) and need the customer within the caller's NOC scope.
"""

from __future__ import annotations

import asyncio
import time
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from secrets import token_hex
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from netcorenoc.api import noc_gate as gate
from netcorenoc.api import signin_flows
from netcorenoc.api.context import AppContext
from netcorenoc.api.customer_view import editor_of
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.crosscutting import auth, inbox, mail, mailauth, secretbox
from netcorenoc.engine.noc import custmail, kinds, mail_loop

#: The wizard's security words → the vault's protocol versions for each kind.
_SMTP_VERSION = {"starttls": "STARTTLS", "tls": "SMTPS", "none": "plain"}
_IMAP_VERSION = {"tls": "IMAPS", "starttls": "STARTTLS"}


class AccountIn(BaseModel):
    """The mailbox a customer's cases write from, as the wizard's three steps describe it."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(default="custom", max_length=32)
    sender: str = Field(min_length=3, max_length=254)
    sender_name: str = Field(default="", max_length=120)
    username: str = Field(default="", max_length=254)
    #: Left out, the stored one is kept (an edit that does not retype it).
    password: str | None = Field(default=None, max_length=512)
    auth: Literal["password", "oauth"] = "password"
    oauth_client_id: str = Field(default="", max_length=36)
    oauth_tenant: str = Field(default="consumers", max_length=64)
    flow: str | None = Field(default=None, max_length=64)
    smtp_host: str = Field(min_length=1, max_length=253)
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_security: Literal["starttls", "tls", "none"] = "starttls"
    #: Empty: replies are not read from a mailbox.
    imap_host: str = Field(default="", max_length=253)
    imap_port: int = Field(default=993, ge=1, le=65535)
    imap_security: Literal["tls", "starttls"] = "tls"


class TryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to: str = Field(min_length=3, max_length=254)
    account: AccountIn


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register a customer's mailbox wizard routes on `app`."""
    store, security, scope_for = ctx.store, ctx.security, ctx.scope_for
    audit_row, write_txn = ctx.perimeter.audit_row, ctx.write_txn
    route = DeclaredRoutes(app)

    async def customer(customer_id: int, scope: Any) -> dict[str, Any]:
        async with store.reading():
            row = await store.noc_customer(customer_id)
        if row is None or not scope.allows_org(customer_id):
            raise HTTPException(status_code=404, detail="no such customer")
        return dict(row)

    async def stored_fields(secret_id: int | None, kind: str) -> dict[str, str]:
        opened = await mail_loop.open_credential(store, secret_id, kind)
        return dict(opened[1]) if opened is not None else {}

    async def fields_for(
        body: AccountIn, principal: auth.Principal, keep: dict[str, str], *, take: bool
    ) -> dict[str, str]:
        """The sealed fields of the login: the password typed (else the stored one), or the
        Microsoft sign-in's refresh token (taken from memory when saved, read when tried)."""
        if not mail.valid_address(body.sender):
            raise HTTPException(status_code=400, detail="sender: not an address")
        out = {"username": (body.username or body.sender).strip()}
        if body.auth == "password":
            password = body.password if body.password is not None else keep.get("password", "")
            if not password:
                raise HTTPException(status_code=400, detail="password: required")
            return {**out, "password": password}
        if not mailauth.valid_client_id(body.oauth_client_id):
            raise HTTPException(status_code=400, detail="oauth_client_id: a GUID")
        if not mailauth.valid_tenant(body.oauth_tenant):
            raise HTTPException(status_code=400, detail="oauth_tenant: not a tenant")
        refresh = keep.get("oauth_refresh", "")
        if body.flow:
            owner = str(principal.ref)
            tokens = (
                signin_flows.take(owner, body.flow)
                if take
                else signin_flows.mine(owner, body.flow).tokens
            )
            if tokens is None:
                raise HTTPException(status_code=409, detail="the sign-in is not approved yet")
            refresh = tokens.refresh_token
        if not refresh:
            raise HTTPException(status_code=400, detail="sign in with Microsoft first")
        return {
            **out,
            "password": "",  # nosec B105 - a Microsoft sign-in has no password
            "oauth_client_id": body.oauth_client_id,
            "oauth_tenant": body.oauth_tenant,
            "oauth_refresh": refresh,
        }

    def sender_of(body: AccountIn, fields: dict[str, str]) -> mail.MailConfig:
        secret = {
            "host": body.smtp_host,
            "port": body.smtp_port,
            "protocol_version": _SMTP_VERSION[body.smtp_security],
        }
        return custmail.smtp_config(
            secret, fields, sender=body.sender, sender_name=body.sender_name
        )

    @route.post("/api/noc/customers/{customer_id}/mail/try")
    async def try_mailbox(
        customer_id: int,
        body: TryIn,
        request: Request,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """Send a test through the mailbox described, and sign in to its inbox when replies are
        read — storing nothing. Says what each server answered."""
        scope = await scope_for(principal)
        row = await customer(customer_id, scope)
        await gate.require_custodian(ctx, request, principal)
        if not mail.valid_address(body.to):
            raise HTTPException(status_code=400, detail="to: not an address")
        async with store.reading():
            cfg = await store.noc_customer_mail(customer_id)
        keep = await stored_fields(cfg["smtp_secret_id"] if cfg else None, "smtp")
        fields = await fields_for(body.account, principal, keep, take=False)
        config = sender_of(body.account, fields)
        message = EmailMessage()
        message["From"] = formataddr((body.account.sender_name or "NOC", body.account.sender))
        message["To"] = body.to
        message["Subject"] = f"NetCoreNOC: test message for {row['name']}"
        message["Date"] = formatdate(localtime=False)
        message["Message-ID"] = make_msgid(domain=body.account.sender.rsplit("@", 1)[-1])
        message.set_content(
            "This is a test from the NOC. Emails about your cases will come from this address; "
            "replying to them adds your answer to the case.\n"
        )
        sent, sent_detail = True, f"the message went to {body.to}"
        try:
            await asyncio.to_thread(mail.send, config, message, password=fields["password"])
        except mail.MailError as exc:
            sent, sent_detail = False, str(exc)
        read: bool | None = None
        read_detail = ""
        if body.account.imap_host:
            box = custmail.mailbox(
                {
                    "host": body.account.imap_host,
                    "port": body.account.imap_port,
                    "protocol_version": _IMAP_VERSION[body.account.imap_security],
                },
                fields,
            )
            try:
                await asyncio.to_thread(inbox.check, box)
                read, read_detail = True, "signed in to the inbox"
            except inbox.InboxError as exc:
                read, read_detail = False, str(exc)
        async with write_txn():
            await audit_row(
                request,
                principal,
                "noc.customer.mail",
                "ok" if sent and read is not False else "error",
                object_type="organization",
                object_id=str(customer_id),
                details={"test": True, "sent": sent, "read": read, "draft": True},
            )
        return {
            "ok": sent and read is not False,
            "sent": sent,
            "detail": sent_detail,
            "read": read,
            "read_detail": read_detail,
        }

    @route.post("/api/noc/customers/{customer_id}/mail/account")
    async def save_mailbox(
        customer_id: int,
        body: AccountIn,
        request: Request,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """Keep the mailbox: its login sealed in the vault as this customer's SMTP (and IMAP)
        credential, and the customer's email settings pointed at them."""
        scope = await scope_for(principal)
        row = await customer(customer_id, scope)
        (await editor_of(ctx, request, principal)).require_work()
        await gate.require_custodian(ctx, request, principal)
        async with store.reading():
            cfg = await store.noc_customer_mail(customer_id)
        smtp_id = cfg["smtp_secret_id"] if cfg else None
        imap_id = cfg["imap_secret_id"] if cfg else None
        keep = await stored_fields(smtp_id, "smtp")
        fields = await fields_for(body, principal, keep, take=True)
        now = time.time()
        plan = [
            ("smtp", body.smtp_host, body.smtp_port, _SMTP_VERSION[body.smtp_security], smtp_id)
        ]
        if body.imap_host:
            plan.append(
                ("imap", body.imap_host, body.imap_port, _IMAP_VERSION[body.imap_security], imap_id)
            )
        ids: dict[str, int | None] = {"smtp": None, "imap": None}
        async with write_txn():
            for kind, host, port, version, current in plan:
                try:
                    clean_host = kinds.clean_host(host)
                except kinds.CredentialError as exc:
                    raise HTTPException(status_code=400, detail=f"{kind}: {exc}") from exc
                meta = {
                    "name": f"{row['name']} — {'sending' if kind == 'smtp' else 'replies'}"[:80],
                    "description": "Set up on the customer's Email tab (ADR #510).",
                    "kind": kind,
                    "access_level": "view_only",
                    "host": clean_host,
                    "port": port,
                    "protocol_version": version,
                    "monitor": False,
                    "organization_id": customer_id,
                }
                existing = await store.noc_get_secret(int(current)) if current else None
                same = existing is not None and existing["organization_id"] == customer_id
                ref = str(existing["ref"]) if same and existing is not None else token_hex(16)
                binding = kinds.binding(ref, kind, clean_host, port, version, "view_only")
                sealed = secretbox.current().seal(binding, fields)
                if same and existing is not None:
                    secret_id = int(existing["id"])
                    await store.noc_update_secret(
                        secret_id, {**meta, "sealed": sealed}, now, principal.actor, resealed=True
                    )
                    action = "noc.secret.update"
                else:
                    secret_id = await store.noc_create_secret(
                        {**meta, "ref": ref, "sealed": sealed}, now, principal.actor
                    )
                    action = "noc.secret.create"
                ids[kind] = secret_id
                await audit_row(
                    request,
                    principal,
                    action,
                    "ok",
                    object_type="noc_secret",
                    object_id=str(secret_id),
                    details={
                        "kind": kind,
                        "customer": customer_id,
                        "auth": body.auth,
                        "fields": sorted(k for k in fields if fields[k]),
                    },
                )
            await store.noc_set_customer_mail(
                customer_id,
                {
                    "recipients": str(cfg["recipients"]) if cfg else "",
                    "cc": str(cfg["cc"]) if cfg else "",
                    "sender": body.sender.strip(),
                    "sender_name": body.sender_name.strip(),
                    "smtp_secret_id": ids["smtp"],
                    "imap_secret_id": ids["imap"],
                    "send_updates": bool(cfg["send_updates"]) if cfg else True,
                    "read_replies": bool(body.imap_host),
                },
                now,
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
                    "provider": body.provider,
                    "auth": body.auth,
                    "reads": bool(body.imap_host),
                },
            )
        return {"saved": True, "smtp_secret_id": ids["smtp"], "imap_secret_id": ids["imap"]}
