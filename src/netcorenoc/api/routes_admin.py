"""Administration: user accounts, service tokens, and runtime configuration.

Every route here is `admin_only`, which `rbac.py` asserts at import against `PERMISSIONS` rather
than accepting as a claim. Admin is never scoped (DECISIONS #58), so none of these resolves
visibility — and `rbac.ROUTE_SCOPE` records that as a derived fact, not an omission.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request

from netcorenoc import auth
from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.api.models import ConfigIn, RoleIn, TokenIn, UserIn


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the admin routes on `app`."""
    store, security, guarded, runtime = ctx.store, ctx.security, ctx.guarded, ctx.runtime
    audit_row, write_txn = ctx.perimeter.audit_row, ctx.write_txn
    route = DeclaredRoutes(app)

    # -- admin: users ------------------------------------------------------------------

    @route.get("/api/users", dependencies=guarded)
    async def list_users() -> list[dict[str, Any]]:
        async with store.lock:
            return await store.list_users()

    @route.post("/api/users")
    async def create_user(
        body: UserIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        policy = auth.validate_password(body.password)
        if policy is not None:
            raise HTTPException(status_code=400, detail=policy)
        async with write_txn():
            if await store.get_user_by_name(body.username) is not None:
                raise HTTPException(status_code=409, detail="username already exists")
            uid = await store.create_user(
                body.username, auth.hash_password(body.password), body.role, False, time.time()
            )
            await audit_row(
                request,
                principal,
                "user.create",
                "ok",
                object_type="user",
                object_id=str(uid),
                details={"username": body.username, "role": body.role},
            )
        return {"id": uid, "username": body.username, "role": body.role}

    @route.post("/api/users/{uid}/role")
    async def change_role(
        uid: int, body: RoleIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, str]:
        async with write_txn():
            if await store.get_user(uid) is None:
                raise HTTPException(status_code=404, detail="no such user")
            await store.set_user_role(uid, body.role, time.time())
            await store.revoke_user_sessions(uid)  # role change revokes sessions
            await audit_row(
                request,
                principal,
                "role.change",
                "ok",
                object_type="user",
                object_id=str(uid),
                details={"role": body.role},
            )
        return {"status": "role changed"}

    @route.delete("/api/users/{uid}")
    async def delete_user(
        uid: int, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, str]:
        if principal.user_id == uid:
            raise HTTPException(status_code=400, detail="cannot delete your own account")
        async with write_txn():
            if await store.get_user(uid) is None:
                raise HTTPException(status_code=404, detail="no such user")
            await store.revoke_user_sessions(uid)
            await store.delete_user(uid)
            await audit_row(
                request, principal, "user.delete", "ok", object_type="user", object_id=str(uid)
            )
        return {"status": "deleted"}

    # -- admin: service tokens ---------------------------------------------------------

    @route.get("/api/tokens", dependencies=guarded)
    async def list_tokens() -> list[dict[str, Any]]:
        async with store.lock:
            return await store.list_tokens()

    @route.post("/api/tokens")
    async def create_token(
        body: TokenIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        token_value = auth.new_session_token()
        async with write_txn():
            if await store.get_token(auth.hash_token(token_value)) is not None:
                raise HTTPException(status_code=500, detail="token collision")  # pragma: no cover
            try:
                tid = await store.create_token(
                    auth.hash_token(token_value), body.name, body.role, principal.actor, time.time()
                )
            except Exception as exc:  # duplicate name (UNIQUE)
                raise HTTPException(status_code=409, detail="token name already exists") from exc
            await audit_row(
                request,
                principal,
                "token.create",
                "ok",
                object_type="token",
                object_id=str(tid),
                details={"name": body.name, "role": body.role},
            )
        return {"id": tid, "name": body.name, "role": body.role, "token": token_value}

    @route.delete("/api/tokens/{tid}")
    async def revoke_token(
        tid: int, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, str]:
        async with write_txn():
            revoked = await store.revoke_token(tid, time.time())
            if revoked is None:
                raise HTTPException(status_code=404, detail="no such active token")
            await audit_row(
                request,
                principal,
                "token.revoke",
                "ok",
                object_type="token",
                object_id=str(tid),
                details={"name": revoked["name"]},
            )
        return {"status": "revoked"}

    # -- admin: config -----------------------------------------------------------------

    @route.get("/api/config", dependencies=guarded)
    async def get_config() -> dict[str, Any]:
        allowlist = runtime.allowlist if runtime else ""
        retention = runtime.retention_days if runtime else 0.0
        async with store.lock:
            saved_allow = await store.get_meta("config.allowlist")
            saved_ret = await store.get_meta("config.retention_days")
        return {
            "allowlist": saved_allow if saved_allow is not None else allowlist,
            "retention_days": float(saved_ret) if saved_ret is not None else retention,
        }

    @route.post("/api/config")
    async def set_config(
        body: ConfigIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, str]:
        async with write_txn():
            await store.set_meta("config.allowlist", body.allowlist)
            await store.set_meta("config.retention_days", str(body.retention_days))
            await audit_row(
                request,
                principal,
                "config.change",
                "ok",
                object_type="config",
                details={
                    "retention_days": body.retention_days,
                    "allowlist_entries": len([x for x in body.allowlist.split(",") if x.strip()]),
                },
            )
        if runtime is not None:
            runtime.apply_allowlist(body.allowlist)
            runtime.retention_days = body.retention_days
        return {"status": "saved"}
