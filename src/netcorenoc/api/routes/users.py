"""User accounts: list, create, change a role, delete (split from `routes/admin.py`, ADR #453).

Every route here is `admin_only`, which `rbac.py` asserts at import against `PERMISSIONS` rather
than accepting as a claim. Admin is never scoped (DECISIONS #58), so none of these resolves
visibility — and `rbac.ROUTE_SCOPE` records that as a derived fact, not an omission.

**No caller reaches past their own access.** `users.manage` can be held by a narrowed admin token,
so creating an account, or moving one to or from a role, requires holding that role's whole
ceiling (`administration.within_grant`): a token narrowed to managing users cannot mint the full
admin its narrowing withheld. A full admin holds every ceiling and never meets the refusal.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request

from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.api.models import RoleIn, UserIn
from netcorenoc.crosscutting import administration, auth


def _within(request: Request, role: str) -> None:
    if not administration.within_grant(request.state.capabilities, role):
        raise HTTPException(status_code=403, detail=administration.BEYOND_GRANT)


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the user-account routes on `app`."""
    store, guarded = ctx.store, ctx.guarded
    audit_row, write_txn = ctx.perimeter.audit_row, ctx.write_txn
    security = ctx.security
    route = DeclaredRoutes(app)

    @route.get("/api/users", dependencies=guarded)
    async def list_users() -> list[dict[str, Any]]:
        """Accounts, each carrying whether it is **the** enabled admin (v0.15.3).

        `sole_admin` is computed here rather than left for the console to derive, and that is the
        same rule F28 set for capabilities: the client renders the answer the server gave, never a
        second one it worked out from a role. `tests/ui/test_security_ui.py` enforces it as an
        absolute — no console module may compare a role to `"admin"` — and the first carve-out
        would turn that guard into a judgement call on every future diff.

        It is an affordance and not a control: the refusal is in `change_role` and `delete_user`.
        """
        async with store.reading():
            rows = await store.list_users()
            enabled_admins = [r for r in rows if r["role"] == "admin" and not r["disabled"]]
        sole = enabled_admins[0]["id"] if len(enabled_admins) == 1 else None
        return [{**row, "sole_admin": row["id"] == sole} for row in rows]

    @route.post("/api/users")
    async def create_user(
        body: UserIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        _within(request, body.role)
        policy = auth.validate_password(body.password)
        if policy is not None:
            raise HTTPException(status_code=400, detail=policy)
        async with write_txn():
            if await store.get_user_by_name(body.username) is not None:
                raise HTTPException(status_code=409, detail="username already exists")
            now = time.time()
            # v0.25.0 (ADR #401): the admin knows this password, so the person sets their own.
            uid = await store.create_user(
                body.username, auth.hash_password(body.password), body.role, True, now
            )
            if display := (body.display_name or "").strip() or None:
                await store.set_display_name(uid, display, now)
            await audit_row(
                request,
                principal,
                "user.create",
                "ok",
                object_type="user",
                object_id=str(uid),
                details={
                    "username": body.username,
                    "role": body.role,
                    "display_name_set": display is not None,
                },
            )
        return {"id": uid, "username": body.username, "role": body.role, "display_name": display}

    @route.post("/api/users/{uid}/role")
    async def change_role(
        uid: int, body: RoleIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, str]:
        _within(request, body.role)
        async with write_txn():
            user = await store.get_user(uid)
            if user is None:
                raise HTTPException(status_code=404, detail="no such user")
            _within(request, str(user["role"]))  # nor demote someone holding more than you
            # F79: the invariant, refused server-side. The console hides the control too, and that
            # is an affordance — this is the control (principle 6, DECISIONS #233).
            if await administration.would_remove_last_admin(store, user, new_role=body.role):
                raise HTTPException(status_code=400, detail=administration.LAST_ADMIN_REFUSAL)
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
            user = await store.get_user(uid)
            if user is None:
                raise HTTPException(status_code=404, detail="no such user")
            # Self-deletion is already refused above; this is the case where an admin deletes
            # ANOTHER admin that happens to be the last enabled one (F79).
            if await administration.would_remove_last_admin(store, user, deleting=True):
                raise HTTPException(status_code=400, detail=administration.LAST_ADMIN_REFUSAL)
            await store.revoke_user_sessions(uid)
            await store.delete_user(uid)
            await audit_row(
                request, principal, "user.delete", "ok", object_type="user", object_id=str(uid)
            )
        return {"status": "deleted"}
