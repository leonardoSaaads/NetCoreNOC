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
from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.api.models import EmailIn, EmailTestIn
from netcorenoc.crosscutting import auth, mail


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the email configuration routes on `app`."""
    store, security, guarded = ctx.store, ctx.security, ctx.guarded
    audit_row, write_txn = ctx.perimeter.audit_row, ctx.write_txn
    route = DeclaredRoutes(app)

    @route.get("/api/email", dependencies=guarded)
    async def get_email() -> dict[str, Any]:
        """The SMTP configuration without its password, and the providers the console offers."""
        async with store.lock:
            config = mail.from_document(await store.get_meta(mail.META_KEY))
        return {
            **mail.public_view(config),
            "providers": {
                key: {
                    "label": p.label,
                    "host": p.host,
                    "port": p.port,
                    "security": p.security,
                    "username": p.username,
                    "note": p.note,
                }
                for key, p in mail.PROVIDERS.items()
            },
        }

    @route.post("/api/email")
    async def set_email(
        body: EmailIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        async with write_txn():
            stored = mail.from_document(await store.get_meta(mail.META_KEY))
            fields = body.model_dump(exclude={"password"})
            password = stored.password if body.password is None else body.password
            config = mail.MailConfig(**fields, password=password)
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
        async with store.lock:
            config = mail.from_document(await store.get_meta(mail.META_KEY))
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
