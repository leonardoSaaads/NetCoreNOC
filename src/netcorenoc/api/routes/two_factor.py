"""Two-factor sign-in: the second step, a person's own authenticator, and an admin's reset
(ADR #482).

* ``POST /api/login/code`` — the second step of signing in (public, like ``/api/login``): the
  challenge the password step answered with, and a code. Right, and the session opens; for a person
  enrolling, the authenticator becomes theirs at the same moment.
* ``POST /api/me/recovery-codes`` — ten new recovery codes, shown once; the old ones stop working.
  Asks for the password **and** a current code: a session alone is not enough.
* ``POST /api/me/two-factor/reset`` — forget my authenticator (a new phone): password and code
  again, every session of mine ends, and the next sign-in enrols the new one.
* ``POST /api/users/{uid}/two-factor/reset`` — an admin forgets **someone else's** authenticator,
  for a person who lost both the phone and the recovery codes. Their sessions end; they enrol again
  at their next sign-in. Never one's own (that is the route above, with the code).

The secret never leaves the appliance after enrolment, and no route returns it.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.api.perimeter import _client_ip
from netcorenoc.crosscutting import auth, totp, twostep

WRONG = "That code was not accepted."
EXPIRED = "This sign-in has expired. Enter your password again."
LOCKED = "Too many wrong codes; try again later."
CONFIRM = "confirm with your password and a current code from your authenticator"


class LoginCodeIn(BaseModel):
    """The second step: the challenge from ``POST /api/login`` and the code."""

    model_config = ConfigDict(extra="forbid")

    challenge: str = Field(min_length=16, max_length=128)
    code: str = Field(min_length=1, max_length=32)


class ConfirmIn(BaseModel):
    """A person proving it is them again: the password and a current authenticator code."""

    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=1, max_length=auth.MAX_PASSWORD)
    code: str = Field(min_length=1, max_length=32)


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the two-factor routes on `app`."""
    store, security, tls_enabled = ctx.store, ctx.security, ctx.tls_enabled
    audit_row, write_txn = ctx.perimeter.audit_row, ctx.write_txn
    codes_throttle = ctx.code_throttle
    route = DeclaredRoutes(app)

    @route.post("/api/login/code")
    async def login_code(body: LoginCodeIn, request: Request, response: Response) -> dict[str, Any]:
        """The sign-in's second step. A right code opens the session (and sets its cookie)."""
        source_ip, now = _client_ip(request), time.time()
        async with write_txn():
            done = await twostep.finish(
                store,
                codes_throttle,
                challenge=body.challenge,
                code=body.code,
                source_ip=source_ip,
                now=now,
            )
            if done.status != "ok":
                if done.username is not None:
                    await audit_row(
                        request,
                        None,
                        "login.lockout" if done.status == "locked" else "login.fail",
                        "denied",
                        actor=done.username,
                        object_type="user",
                        object_id=str(done.user_id),
                        details={"reason": "second-factor", "remaining": done.remaining},
                    )
                await store.commit()
                if done.status == "locked":
                    raise HTTPException(status_code=429, detail=LOCKED)
                if done.status == "expired" or done.remaining <= 0:
                    raise HTTPException(status_code=410, detail=EXPIRED)
                left = f" {done.remaining} more {'try' if done.remaining == 1 else 'tries'}."
                raise HTTPException(status_code=401, detail=WRONG + left)
            assert done.principal is not None and done.session_token is not None
            if done.method == "enrolled":
                await audit_row(
                    request,
                    done.principal,
                    "twofactor.enroll",
                    "ok",
                    object_type="user",
                    object_id=str(done.user_id),
                    details={"recovery_codes": totp.RECOVERY_CODES},
                )
            await audit_row(
                request,
                done.principal,
                "login.ok",
                "ok",
                object_type="user",
                object_id=str(done.user_id),
                details={"second_factor": done.method, "recovery_left": done.recovery_left},
            )
        response.set_cookie(
            auth.COOKIE_NAME,
            done.session_token,
            httponly=True,
            samesite="strict",
            path="/",
            secure=tls_enabled,
        )
        return {
            "user": done.principal.actor,
            "role": done.principal.role,
            "must_change_password": False,  # nosec B105 - response flag, not a secret
            "second_factor": done.method,
            "recovery_left": done.recovery_left,
        }

    async def confirmed(body: ConfirmIn, request: Request, principal: auth.Principal) -> int:
        """The caller's user id, once the password and a code prove it is them — else a 403."""
        if principal.user_id is None:
            raise HTTPException(status_code=403, detail="service tokens have no authenticator")
        uid, now, source_ip = int(principal.user_id), time.time(), _client_ip(request)
        if codes_throttle.locked_for(principal.actor, source_ip, now) > 0:
            raise HTTPException(status_code=429, detail=LOCKED)
        async with store.reading():
            user = await store.get_user(uid)
            stored = await store.second_factor_secret(uid)
        right = user is not None and await asyncio.to_thread(
            auth.verify_password, body.password, str(user["password_hash"])
        )
        step = (
            totp.matching_step(stored[0], body.code, now, last_step=stored[1])
            if stored is not None
            else None
        )
        if not right or step is None:
            codes_throttle.record_failure(principal.actor, source_ip, now)
            raise HTTPException(status_code=403, detail=CONFIRM)
        async with write_txn():
            if not await store.second_factor_use(uid, step, now) and totp.REPLAY_GUARD:
                raise HTTPException(status_code=403, detail=CONFIRM)
        codes_throttle.record_success(principal.actor, source_ip)
        return uid

    @route.post("/api/me/recovery-codes")
    async def new_recovery_codes(
        body: ConfirmIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """Ten new recovery codes, shown once. The ones before stop working."""
        uid = await confirmed(body, request, principal)
        codes = totp.new_recovery_codes()
        async with write_txn():
            await store.replace_recovery_codes(
                uid, [totp.recovery_digest(c) for c in codes], time.time()
            )
            await audit_row(
                request,
                principal,
                "twofactor.recovery_codes",
                "ok",
                object_type="user",
                object_id=str(uid),
                details={"count": len(codes)},
            )
        return {"recovery_codes": codes}

    @route.post("/api/me/two-factor/reset")
    async def reset_my_two_factor(
        body: ConfirmIn,
        request: Request,
        response: Response,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, str]:
        """Forget my authenticator (a new phone). Every session of mine ends; the next sign-in
        enrols the new one."""
        uid = await confirmed(body, request, principal)
        async with write_txn():
            await store.clear_second_factor(uid)
            revoked = await store.revoke_user_sessions(uid)
            await audit_row(
                request,
                principal,
                "twofactor.reset",
                "ok",
                object_type="user",
                object_id=str(uid),
                details={"by": "self", "sessions_revoked": revoked},
            )
        response.delete_cookie(auth.COOKIE_NAME, path="/")
        return {"status": "Sign in again to set up your new authenticator."}

    @route.post("/api/users/{uid}/two-factor/reset")
    async def reset_user_two_factor(
        uid: int, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """Forget another person's authenticator: they lost the phone and the recovery codes.
        Their sessions end and they enrol a new one at their next sign-in."""
        if principal.user_id is not None and int(principal.user_id) == uid:
            raise HTTPException(
                status_code=400, detail="reset your own authenticator under Your account"
            )
        async with write_txn():
            user = await store.get_user(uid)
            if user is None:
                raise HTTPException(status_code=404, detail="no such user")
            had = await store.clear_second_factor(uid)
            revoked = await store.revoke_user_sessions(uid)
            await audit_row(
                request,
                principal,
                "twofactor.reset",
                "ok",
                object_type="user",
                object_id=str(uid),
                details={"by": "admin", "had_one": had, "sessions_revoked": revoked},
            )
        return {"reset": had, "sessions_revoked": revoked}
