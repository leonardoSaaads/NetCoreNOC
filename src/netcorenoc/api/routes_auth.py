"""Sign in, sign out, who am I, change my password.

`POST /api/login` is the one route in `rbac.PUBLIC_ROUTES`: it is how a principal comes to exist,
so it cannot require one. Everything else here is `self.read` — it acts on the caller's own
session or account and names no network element, which is why three of the four are declared
`unscoped` in `rbac.ROUTE_SCOPE` and `/api/me` is not (it reports the caller's own scope).
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response

from netcorenoc import auth
from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.api.models import LoginIn, PasswordIn
from netcorenoc.api.perimeter import _client_ip


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the auth routes on `app`."""
    store, security, tls_enabled = ctx.store, ctx.security, ctx.tls_enabled
    throttle, scope_for = ctx.throttle, ctx.scope_for
    audit_row, write_txn = ctx.perimeter.audit_row, ctx.write_txn
    route = DeclaredRoutes(app)

    def _cookie_kwargs() -> dict[str, Any]:
        return {"httponly": True, "samesite": "strict", "path": "/", "secure": tls_enabled}

    @route.post("/api/login")
    async def login(body: LoginIn, request: Request, response: Response) -> dict[str, Any]:
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
                return {"must_change_password": True}  # nosec B105 - response flag, not a secret
            assert outcome.principal is not None and outcome.session_token is not None
            if body.new_password:
                await audit_row(
                    request,
                    outcome.principal,
                    "password.change",
                    "ok",
                    object_type="user",
                    object_id=str(outcome.principal.user_id),
                )
            await audit_row(
                request,
                outcome.principal,
                "login.ok",
                "ok",
                object_type="user",
                object_id=str(outcome.principal.user_id),
            )
        response.set_cookie(auth.COOKIE_NAME, outcome.session_token, **_cookie_kwargs())
        return {
            "user": outcome.principal.actor,
            "role": outcome.principal.role,
            "must_change_password": False,  # nosec B105 - response flag, not a secret
        }

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
        return {
            "user": principal.actor,
            "role": principal.role,
            "must_change_password": principal.must_change_password,
            "capabilities": sorted(capabilities),
            "scope": {
                "scoped": not scope.unrestricted,
                "ne_count": None if scope.unrestricted else len(scope.ne_ids),
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
