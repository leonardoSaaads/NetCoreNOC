"""Sign in, sign out, who am I, change my password.

`POST /api/login` is in `rbac.PUBLIC_ROUTES`: it is how a principal comes to exist, so it cannot
require one. Since ADR #482 it is the **first** of two steps: a right password answers with a
challenge, never a session, and `POST /api/login/code` (`routes/two_factor.py`) opens the session
once the authenticator code is right. Everything else here is `self.read` — it acts on the
caller's own session or account and names no network element, which is why three of the four are
declared `unscoped` in `rbac.ROUTE_SCOPE` and `/api/me` is not (it reports the caller's own
scope).
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response

from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.api.models import LoginIn, PasswordIn
from netcorenoc.api.perimeter import _client_ip
from netcorenoc.crosscutting import auth, twostep


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the auth routes on `app`."""
    store, security = ctx.store, ctx.security
    throttle, scope_for = ctx.throttle, ctx.scope_for
    audit_row, write_txn = ctx.perimeter.audit_row, ctx.write_txn
    route = DeclaredRoutes(app)

    @route.post("/api/login")
    async def login(body: LoginIn, request: Request) -> dict[str, Any]:
        source_ip = _client_ip(request)
        async with write_txn():
            outcome = await auth.perform_login(
                store,
                throttle,
                username=body.username,
                password=body.password,
                new_password=body.new_password,
                source_ip=source_ip,
                now=time.time(),
            )
            if outcome.status == "locked":
                await audit_row(
                    request,
                    None,
                    "login.lockout",
                    "denied",
                    actor=body.username,
                    object_type="user",
                    object_id=body.username,
                )
                await store.commit()
                raise HTTPException(status_code=429, detail=outcome.message)
            if outcome.status in ("fail", "policy"):
                await audit_row(
                    request,
                    None,
                    "login.fail",
                    "denied",
                    actor=body.username,
                    object_type="user",
                    object_id=body.username,
                    details={"reason": outcome.status},
                )
                await store.commit()
                if outcome.status == "policy":
                    raise HTTPException(status_code=400, detail=outcome.message)
                raise HTTPException(status_code=401, detail=outcome.message)
            if outcome.status == "must_change":
                await store.commit()
                # The policy travels with the demand, because this is the one moment the console
                # needs it and has no session to ask `/api/me` for (v0.15.3, DECISIONS #240). It
                # states a bound this route already enforces and reveals nothing about the account.
                return {
                    "must_change_password": True,  # nosec B105 - response flag, not a secret
                    "password_policy": auth.password_policy(),
                }
            assert outcome.principal is not None and outcome.principal.user_id is not None
            if body.new_password:
                await audit_row(
                    request,
                    outcome.principal,
                    "password.change",
                    "ok",
                    object_type="user",
                    object_id=str(outcome.principal.user_id),
                )
            # ADR #482: the password is right, so the second step begins — no session, no cookie
            # yet. `POST /api/login/code` answers it.
            begun = await twostep.begin(
                store, int(outcome.principal.user_id), body.username, source_ip, time.time()
            )
        return {"must_change_password": False, **begun.response()}  # nosec B105 - a flag

    @route.post("/api/logout")
    async def logout(
        request: Request, response: Response, principal: auth.Principal = Depends(security)
    ) -> dict[str, str]:
        cookie = request.cookies.get(auth.COOKIE_NAME)
        async with write_txn():
            if cookie:
                await store.delete_session(auth.hash_token(cookie))
            await audit_row(request, principal, "logout", "ok")
        response.delete_cookie(auth.COOKIE_NAME, path="/")
        return {"status": "logged out"}

    @route.get("/api/me")
    async def me(request: Request, principal: auth.Principal = Depends(security)) -> dict[str, Any]:
        """Who am I, and — since v0.7.0 — what may I actually do and see?

        `capabilities` is the resolved set from the one resolver, not a role-rank lookup, so the
        UI gates its affordances on the same answer the server enforces (F28). `scope` is a summary
        only: whether this principal is scoped and how many NEs they can see, never which ones — a
        count is what the UI needs to explain a partial picture, and an id list would be an
        inventory the caller has not otherwise been given.
        """
        capabilities: frozenset[str] = request.state.capabilities
        scope = await scope_for(principal)
        person = factor = None
        if principal.user_id is not None:
            async with store.reading():
                person = await store.person(int(principal.user_id))
                factor = await store.second_factor(int(principal.user_id))
        return {
            "user": principal.actor,
            # v0.25.0 (ADR #401, #402): who you are as a person — your id, the name you chose and
            # your photo's digest (the console builds the photo's URL from the two).
            "user_id": principal.user_id,
            "display_name": person["display_name"] if person else None,
            "avatar": person["avatar"] if person else None,
            # v0.30.0 (ADR #445): where this person's recovery links go; null when nowhere.
            "email": person["email"] if person else None,
            "role": principal.role,
            "must_change_password": principal.must_change_password,
            "capabilities": sorted(capabilities),
            "scope": {
                "scoped": not scope.unrestricted,
                "ne_count": None if scope.unrestricted else len(scope.ne_ids),
            },
            "password_policy": auth.password_policy(),
            # ADR #482: this person's authenticator — whether one is enrolled, since when, last
            # used, and how many recovery codes are left. Never the secret. Null for a token.
            "two_factor": None
            if principal.user_id is None
            else {
                "enrolled": factor is not None,
                "enrolled_at": factor["enrolled_at"] if factor else None,
                "last_used_at": factor["last_used_at"] if factor else None,
                "recovery_left": int(factor["recovery_left"]) if factor else 0,
            },
        }

    @route.post("/api/password")
    async def change_password(
        body: PasswordIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, str]:
        if principal.user_id is None:
            raise HTTPException(status_code=403, detail="service tokens have no password")
        policy = auth.validate_password(body.new_password)
        if policy is not None:
            raise HTTPException(status_code=400, detail=policy)
        async with write_txn():
            user = await store.get_user(principal.user_id)
            if user is None or not auth.verify_password(body.old_password, user["password_hash"]):
                await audit_row(
                    request,
                    principal,
                    "password.change",
                    "denied",
                    object_type="user",
                    object_id=str(principal.user_id),
                )
                await store.commit()
                raise HTTPException(status_code=400, detail="current password incorrect")
            await store.update_user_password(
                principal.user_id, auth.hash_password(body.new_password), time.time()
            )
            await store.revoke_user_sessions(principal.user_id)
            await audit_row(
                request,
                principal,
                "password.change",
                "ok",
                object_type="user",
                object_id=str(principal.user_id),
            )
        return {"status": "password changed; sign in again"}
