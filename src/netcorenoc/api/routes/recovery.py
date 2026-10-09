"""Recovery by email: three routes reachable without signing in (v0.30.0, ADR #445).

`rbac.PUBLIC_ROUTES` names them beside `POST /api/login`, because a person who cannot sign in is
exactly who calls them. What keeps that safe:

* **No account oracle.** A request answers the same sentence, with the same status, whether or not
  the username or address names an account — the email goes out from a background task, so the
  answer does not even take longer when it does;
* **A link is a secret, and is never stored as one.** 256 random bits in the link; only their
  SHA-256 in `password_reset`. It lives 30 minutes, works once, and the next request supersedes it;
* **The link's host is configuration, never the request's `Host` header**, which an attacker
  controls (password-reset poisoning). No console address configured, no recovery offered;
* **Bounded**: per address a token bucket, per account one link every two minutes, and every
  request — matched or not — is an audit row;
* **A reset signs out every session** of the account and clears a pending forced change: the
  person chose this password themselves, through a mailbox only they read.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.api.models import ResetConfirmIn, ResetRequestIn
from netcorenoc.api.perimeter import RateLimiter, _client_ip
from netcorenoc.crosscutting import auth, mail

log = logging.getLogger("netcorenoc")

LINK_TTL_S = 30 * 60
#: One link per account in this many seconds; a second request inside it sends nothing.
ACCOUNT_COOLDOWN_S = 120
#: Per client address: five requests at once, then one every twelve minutes.
REQUEST_CAPACITY, REQUEST_REFILL = 5.0, 5.0 / 3600.0
ANSWER = (
    "If that account has a recovery address, a link to choose a new password is on its way. "
    "It works once, for 30 minutes."
)
INVALID_LINK = "This link is invalid, already used or expired. Ask for a new one."


def _body(username: str, link: str, source_ip: str, now: float) -> str:
    when = datetime.fromtimestamp(now, UTC).strftime("%Y-%m-%d %H:%M UTC")
    return (
        f"Someone asked to reset the password of the NetCoreNOC account '{username}' "
        f"from {source_ip} at {when}.\n\n"
        f"To choose a new password, open this link within 30 minutes:\n\n{link}\n\n"
        "It works once. Every session of the account is signed out when the password changes.\n\n"
        "If this was not you, ignore this message: the password stays as it is.\n"
    )


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the public recovery routes on `app`."""
    store, audit_row, write_txn = ctx.store, ctx.perimeter.audit_row, ctx.write_txn
    route = DeclaredRoutes(app)
    limiter = RateLimiter(REQUEST_CAPACITY, REQUEST_REFILL)
    confirm_limiter = RateLimiter(REQUEST_CAPACITY * 2, REQUEST_REFILL * 4)
    # The sends in flight, held so the event loop does not collect a task before it finishes.
    sending: set[asyncio.Task[None]] = set()

    async def _deliver(config: mail.MailConfig, to: str, username: str, body: str) -> None:
        message = mail.compose(config, to, "NetCoreNOC password reset", body)
        try:
            await asyncio.to_thread(mail.send, config, message)
        except mail.MailError as exc:
            # The account is named, the link never is: the log is not a way to obtain one.
            log.warning("password-reset email for %r was not sent: %s", username, exc)

    @route.get("/api/login/options")
    async def login_options() -> dict[str, Any]:
        """What the sign-in screen may offer. A boolean, and nothing about any account."""
        async with store.lock:
            config = mail.from_document(await store.get_meta(mail.META_KEY))
        return {"recovery": config.can_recover}

    @route.post("/api/password-reset", status_code=202)
    async def request_reset(body: ResetRequestIn, request: Request) -> dict[str, str]:
        source_ip, now = _client_ip(request), time.time()
        if not limiter.allow(source_ip, time.monotonic()):
            raise HTTPException(status_code=429, detail="too many requests; try again later")
        login = body.login.strip()
        outbox: list[tuple[str, str, str]] = []
        async with write_txn():
            config = mail.from_document(await store.get_meta(mail.META_KEY))
            accounts = await store.recovery_accounts(login) if config.can_recover else []
            for account in accounts:
                uid = int(account["id"])
                if await store.reset_requested_since(uid, now - ACCOUNT_COOLDOWN_S):
                    continue
                token = secrets.token_urlsafe(32)
                await store.create_reset(
                    uid, auth.hash_token(token), now, now + LINK_TTL_S, source_ip
                )
                link = f"{config.public_url.rstrip('/')}/#/reset/{token}"
                body_text = _body(account["username"], link, source_ip, now)
                outbox.append((account["email"], account["username"], body_text))
            await audit_row(
                request,
                None,
                "password.reset.request",
                "ok" if accounts else "denied",
                actor=login[:64],
                object_type="user",
                object_id=",".join(str(a["id"]) for a in accounts) or None,
                details={"matched": len(accounts), "offered": config.can_recover},
            )
        # Sent only once the links are committed, so no mail carries a link the database lacks.
        for to, username, text in outbox:
            task = asyncio.create_task(_deliver(config, to, username, text))
            sending.add(task)
            task.add_done_callback(sending.discard)
        return {"status": ANSWER}

    @route.post("/api/password-reset/confirm")
    async def confirm_reset(body: ResetConfirmIn, request: Request) -> dict[str, str]:
        source_ip, now = _client_ip(request), time.time()
        if not confirm_limiter.allow(source_ip, time.monotonic()):
            raise HTTPException(status_code=429, detail="too many requests; try again later")
        policy = auth.validate_password(body.new_password)
        if policy is not None:
            raise HTTPException(status_code=400, detail=policy)
        async with write_txn():
            user = await store.take_reset(auth.hash_token(body.token), now)
            if user is None:
                await audit_row(
                    request, None, "password.reset", "denied", details={"reason": "invalid link"}
                )
                await store.commit()
                raise HTTPException(status_code=400, detail=INVALID_LINK)
            uid = int(user["id"])
            await store.update_user_password(uid, auth.hash_password(body.new_password), now)
            revoked = await store.revoke_user_sessions(uid)
            await audit_row(
                request,
                None,
                "password.reset",
                "ok",
                actor=user["username"],
                object_type="user",
                object_id=str(uid),
                details={"sessions_revoked": revoked},
            )
        return {"status": "Password changed. Sign in with the new one."}
