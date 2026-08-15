"""Administration: user accounts, service tokens, and runtime configuration.

Every route here is `admin_only`, which `rbac.py` asserts at import against `PERMISSIONS` rather
than accepting as a claim. Admin is never scoped (DECISIONS #58), so none of these resolves
visibility — and `rbac.ROUTE_SCOPE` records that as a derived fact, not an omission.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request

from netcorenoc import auth, capture
from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.api.models import ConfigIn, RetentionIn, RoleIn, TokenIn, UserIn
from netcorenoc.settings import Settings

# The tier names, in `RetentionPolicy.as_key()` order, so the audit detail reads as a policy
# rather than as an unlabelled tuple. Before and after are both captured. Imported from the module
# that owns the policy (v0.8.1) rather than restated here, so the audit detail and the stored form
# cannot drift apart.
_TIERS = capture.TIER_NAMES


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the admin routes on `app`."""
    store, security, guarded, runtime = ctx.store, ctx.security, ctx.guarded, ctx.runtime
    engine = ctx.engine  # v0.8.0: the live retention policy and capture state
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
        """The live configuration, and — from v0.13.0 — **why each value is what it is**.

        The two top-level keys are unchanged, so nothing that reads this route today notices. Two
        objects are added (ADR #179):

        * ``precedence`` — the environment default and the database override **separately**, so a
          console can show three columns. An operator's real question during an incident is *"why
          is this value what it is?"*, and two sources behind one displayed number cannot answer
          it. `RuntimeConfig` holds only the merged value, and growing it is what
          `UI-0.13-DRAFT.md` §12.5 closes, so the defaults are re-read from the environment here —
          which is exactly what they are.
        * ``startup`` — the settings `Settings` reads once and never mutates, so the console can
          say *which changes need a restart* before one is attempted rather than after. A settings
          page that accepted a new ``trap_port`` and appeared to succeed, while the receiver kept
          listening on the old one, would be worse than no settings page.

        **`tls_cert` and `tls_key` are reported as one boolean, and `api_token` is not reported.**
        This capability is in ``AUDITED_DENIED_PERMISSIONS`` precisely because reading the
        allowlist reveals network-security posture (F9); adding filesystem paths to the response
        would widen what one audited capability discloses for no operator benefit.
        """
        env = Settings.from_env()
        allowlist = runtime.allowlist if runtime else ""
        retention = runtime.retention_days if runtime else 0.0
        async with store.lock:
            saved_allow = await store.get_meta("config.allowlist")
            saved_ret = await store.get_meta("config.retention_days")
        return {
            "allowlist": saved_allow if saved_allow is not None else allowlist,
            "retention_days": float(saved_ret) if saved_ret is not None else retention,
            "precedence": {
                "allowlist": {"env": env.allowlist, "override": saved_allow},
                "retention_days": {
                    "env": env.retention_days,
                    "override": float(saved_ret) if saved_ret is not None else None,
                },
            },
            "startup": {
                "db_path": env.db_path,
                "trap_host": env.trap_host,
                "trap_port": env.trap_port,
                "http_host": env.http_host,
                "http_port": env.http_port,
                "audit_retention_days": env.audit_retention_days,
                "tls_enabled": env.tls_enabled,
                "log_json": env.log_json,
            },
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

    # -- admin: the feedback dataset's retention (v0.8.0) ------------------------------
    #
    # THE ONLY DESTRUCTIVE CONTROL IN THIS PRODUCT. Every other admin configuration is
    # reversible — `scorer_config` is append-only and rollback is a pointer move, governance
    # policies are versioned — but **lowering retention deletes rows and there is no rollback for
    # a DELETE**. An admin moving the training tier from twelve months to three destroys nine
    # months of human labels: the most expensive and least reconstructible asset in the system,
    # and the effect lands later, when the maintenance loop runs.
    #
    # So this reuses the pattern v0.6.0 built for exactly this class of decision (`preview.py`):
    # preview before apply, audited as its own action, and never applied by a background loop
    # before the operator has seen the count. Both routes are admin-only, and admin is never
    # scoped — the dataset is a scope bypass by construction and has no scoped view.

    @route.get("/api/dataset/retention", dependencies=guarded)
    async def get_retention() -> dict[str, Any]:
        """The policy in effect, and what capture currently costs in rows.

        `dataset stats` as a route as well as a CLI view: zero-config means the default is good,
        not that the operator is blind about what it is storing. **Aggregates only** — counts and
        timestamps, never a row.
        """
        policy = engine.retention
        async with store.lock:
            stats = await store.dataset_stats()
        return {
            "sink_days": policy.sink_days,
            "sink_rows": policy.sink_rows,
            "training_days": policy.training_days,
            "audit_days": policy.audit_days,
            "capture_enabled": engine.capture.enabled,
            "stats": stats,
            # What the background audit sweep has destroyed since this process started. Deleting a
            # label is the most consequential thing this product does, so the count is reported
            # rather than discarded the way `store.prune`'s is.
            "audit_swept": dict(engine.capture.audit_swept),
        }

    @route.post("/api/dataset/retention")
    async def set_retention(
        body: RetentionIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """Preview a retention change, or apply one.

        **`preview` defaults to True**, so the destructive branch cannot be reached by accident or
        by a client that forgot a field: applying requires `preview=False` sent deliberately, after
        the count has been seen. The preview is bounded, read-only and deterministic, and it is
        audited too — *"who looked at what this would destroy"* is part of the record, not only
        *"who destroyed it"*.
        """
        candidate = capture.RetentionPolicy(
            sink_days=body.sink_days,
            sink_rows=body.sink_rows,
            training_days=body.training_days,
            audit_days=body.audit_days,
        )
        # Fail-closed, with a precise reason. `validate` explains *which* tier is wrong and why,
        # because "invalid" on a form of four numbers is not something an admin can act on.
        reason = candidate.validate()
        if reason is not None:
            raise HTTPException(status_code=400, detail=reason)
        # **The AUDIT bound, not the training bound (v0.8.1, DECISIONS #110).** v0.8.0 cut here on
        # `training_days`, which made the middle tier the destructive one and left the preview
        # reporting a `labels` figure the apply never deleted. Under the tier semantics this
        # release adopts, training *selects* — reducing it destroys nothing — and the audit bound is
        # the outer edge of the data's life, so it is the one an operator can ask to be enforced.
        cutoff = time.time() - candidate.audit_days * 86400.0
        async with store.lock:
            impact = await store.preview_retention(cutoff)
        if body.preview:
            async with write_txn():
                await audit_row(
                    request,
                    principal,
                    "retention.preview",
                    "ok",
                    object_type="config",
                    details=dict(impact),
                )
            return {
                "status": "preview",
                "would_delete": impact,
                "applied": False,
                # Which tier these counts belong to, so nobody reads them as the training tier's.
                # Lowering `training_days` destroys nothing — it narrows what a model may read.
                "bound": "audit",
                "training_deletes": 0,
            }
        before = engine.retention
        async with write_txn():
            await audit_row(
                request,
                principal,
                "retention.change",
                "ok",
                object_type="config",
                details={
                    "before": dict(zip(_TIERS, before.as_key(), strict=True)),
                    "after": dict(zip(_TIERS, candidate.as_key(), strict=True)),
                    "previewed_impact": dict(impact),
                },
            )
            # **The deletion happens HERE, in the audited transaction, not later.** Directive 9
            # forbids the maintenance loop destroying labels on a schedule; it does not make the
            # reduction a no-op. The operator has now seen the count and asked for it, so it
            # happens once, visibly, attributed to them — and the audit row above precedes it, so
            # a crash mid-delete leaves the intent on record.
            deleted = await store.prune_dataset_audit(cutoff)
            # **v0.8.1: the policy is PERSISTED.** v0.8.0 answered "saved", audited the change, and
            # kept it only in memory — so the destruction an admin asked for was permanent and the
            # configuration they asked for was not. One `meta` value, in the same transaction as
            # the audit row and the deletion, so the record and the policy cannot disagree.
            # No migration: `meta` is where this product has always kept operator configuration.
            await store.set_meta(capture.RETENTION_META_KEY, candidate.as_json())
        engine.retention = candidate
        return {
            "status": "saved",
            "would_delete": impact,
            "deleted": deleted,
            "applied": True,
            "bound": "audit",
            "training_deletes": 0,
        }
