"""Outgoing email: the SMTP server, and a test message (v0.30.0, ADR #445).

`config.read` to see the configuration and `config.write` to change it or send a test — the
capabilities of every other appliance setting. The password is write-only: it is never in a
response, an audit row or a log line, and a save that leaves it out keeps the stored one.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request

from netcorenoc import __version__
from netcorenoc.api import signin_flows
from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.api.models import EmailIn, EmailTestIn
from netcorenoc.crosscutting import auth, mail, mailauth


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the email configuration routes on `app`."""
    store, security, guarded = ctx.store, ctx.security, ctx.guarded
    audit_row, write_txn = ctx.perimeter.audit_row, ctx.write_txn
    route = DeclaredRoutes(app)

    def drafted(
        body: EmailIn, stored: mail.MailConfig, principal: auth.Principal, *, keep: bool = False
    ) -> mail.MailConfig:
        """The configuration a body describes: a password left out keeps the stored one, and an
        approved sign-in (`flow`) brings its refresh token — taken from memory as it is saved,
        or only read (`keep`) for a test."""
        fields = body.model_dump(exclude={"password", "flow"})
        password = stored.password if body.password is None else body.password
        refresh = stored.oauth_refresh if body.auth == "oauth" else ""
        if body.auth == "oauth" and body.flow:
            owner = str(principal.ref)
            if keep:
                held = signin_flows.mine(owner, body.flow).tokens
                if held is None:
                    raise HTTPException(status_code=409, detail="the sign-in is not approved yet")
                refresh = held.refresh_token
            else:
                refresh = signin_flows.take(owner, body.flow).refresh_token
        return mail.MailConfig(**fields, password=password, oauth_refresh=refresh)

    async def keep_rotation(config: mail.MailConfig) -> None:
        """ADR #510: Microsoft replaced the refresh token while sending — store the new one."""
        if config.auth != "oauth":
            return
        newer = mailauth.latest(config.oauth_client_id, config.oauth_refresh)
        if newer is None:
            return
        async with write_txn():
            current = mail.from_document(await store.get_meta(mail.META_KEY))
            if current.oauth_refresh == config.oauth_refresh:
                await store.set_meta(
                    mail.META_KEY, mail.to_document(replace(current, oauth_refresh=newer))
                )

    @route.get("/api/email", dependencies=guarded)
    async def get_email() -> dict[str, Any]:
        """The SMTP configuration without its password, and the providers the console offers."""
        async with store.reading():
            config = mail.from_document(await store.get_meta(mail.META_KEY))
        return {
            **mail.public_view(config),
            "providers": mail.catalogue(),
        }

    @route.post("/api/email")
    async def set_email(
        body: EmailIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        async with write_txn():
            stored = mail.from_document(await store.get_meta(mail.META_KEY))
            config = drafted(body, stored, principal)
            problems = mail.problems(config)
            if problems:
                raise HTTPException(status_code=400, detail="; ".join(problems))
            await store.set_meta(mail.META_KEY, mail.to_document(config))
            await audit_row(
                request,
                principal,
                "email.config.change",
                "ok",
                object_type="config",
                details={
                    "enabled": config.enabled,
                    "provider": config.provider,
                    "server": f"{config.host}:{config.port}",
                    "security": config.security,
                    "recovery": config.recovery,
                    "password_changed": body.password is not None,
                    # ADR #510: a Microsoft sign-in stored, and whose (never the token).
                    "auth": config.auth,
                    "signed_in": body.flow is not None,
                },
            )
        return {"status": "saved", **mail.public_view(config)}

    @route.post("/api/email/test")
    async def test_email(
        body: EmailTestIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """Send one message now with the saved configuration, and say exactly what happened."""
        if not mail.valid_address(body.to):
            raise HTTPException(status_code=400, detail="that is not an email address")
        async with store.reading():
            stored = mail.from_document(await store.get_meta(mail.META_KEY))
        # ADR #510: the wizard tests its draft before anything is saved; the draft's sign-in
        # stays held (it is taken when the draft is saved).
        config = drafted(body.draft, stored, principal, keep=True) if body.draft else stored
        if not config.host:
            raise HTTPException(status_code=409, detail="save an SMTP server first")
        message = mail.compose(
            replace(config, enabled=True),
            body.to,
            "NetCoreNOC test message",
            f"This is a test message from NetCoreNOC {__version__}, sent by {principal.actor}.\n\n"
            "If you can read it, the appliance can send password-recovery links through "
            f"{config.host}.\n",
        )
        outcome, error = "ok", None
        try:
            await asyncio.to_thread(mail.send, config, message)
        except mail.MailError as exc:
            outcome, error = "error", str(exc)
        if body.draft is None:
            await keep_rotation(stored)
        async with write_txn():
            await audit_row(
                request,
                principal,
                "email.test",
                outcome,
                object_type="config",
                details={"server": f"{config.host}:{config.port}", "error": error},
            )
        if error is not None:
            raise HTTPException(status_code=502, detail=error)
        return {"status": "sent", "to": body.to}
