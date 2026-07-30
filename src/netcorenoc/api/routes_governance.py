"""The v0.7.0 governance surface: the capability policy and the visibility scope.

Two kinds, one shape. Each GET returns the active document, the resolved effect and the immutable
history; each POST does exactly one of apply / rollback / clear through `_write_policy`, the single
write path for both kinds, and every one of the three is audited with before and after.

Nothing written here can escalate. The resolver intersects with the compiled ceiling, so the worst
a hostile document achieves is taking capabilities away — and never the admin's recovery set
(DECISIONS #53, #64). The 400 on an above-ceiling entry is a usability affordance, not the control.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request

from netcorenoc import auth, rbac, shaping
from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.api.models import PolicyIn

MAX_POLICY_HISTORY = 50


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the governance routes on `app`."""
    store, security, guarded, governance = ctx.store, ctx.security, ctx.guarded, ctx.governance
    audit_row, write_txn = ctx.perimeter.audit_row, ctx.write_txn
    route = DeclaredRoutes(app)

    # -- admin: governance (v0.7.0) ----------------------------------------------------
    #
    # Two kinds, one shape. Each GET returns the active document, the resolved effect, and the
    # immutable history; each POST does exactly one of apply / rollback / clear, and every one of
    # the three is audited with before and after.

    def _policy_row(row: dict[str, Any]) -> dict[str, Any]:
        """One history row, projected. A policy is not a secret — it *is* the perimeter."""
        return {
            "id": int(row["id"]),
            "kind": row["kind"],
            "document": row["document"],
            "doc_hash": row["doc_hash"],
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "note": row["note"],
            "active": bool(row.get("active")),
        }

    async def _active_policy(kind: str) -> dict[str, Any] | None:
        active = await store.active_governance_ids()
        policy_id = active.get(kind)
        if policy_id is None:
            return None
        return await store.get_governance_policy(policy_id)

    def _canonical(document: dict[str, Any]) -> tuple[str, str]:
        """Canonical JSON plus its sha256 — the exact bytes `doc_hash` commits to."""
        text = json.dumps(document, sort_keys=True, separators=(",", ":"))
        return text, hashlib.sha256(text.encode("utf-8")).hexdigest()

    async def _write_policy(
        kind: str,
        body: PolicyIn,
        request: Request,
        principal: auth.Principal,
        action: str,
        problems: Callable[[str], list[str]],
    ) -> dict[str, Any]:
        """Apply, roll back, or clear one policy kind. The single write path for both kinds."""
        now = time.time()
        async with write_txn():
            before = await _active_policy(kind)
            before_summary = (
                {"policy_id": int(before["id"]), "doc_hash": before["doc_hash"]} if before else None
            )
            if body.clear:
                await store.clear_active_governance_policy(kind)
                after_summary: dict[str, Any] | None = None
                outcome = "cleared"
                policy_id = None
            elif body.policy_id is not None:
                target = await store.get_governance_policy(body.policy_id)
                if target is None or target["kind"] != kind:
                    raise HTTPException(status_code=404, detail="no such policy version")
                await store.set_active_governance_policy(kind, body.policy_id, principal.actor, now)
                after_summary = {"policy_id": body.policy_id, "doc_hash": target["doc_hash"]}
                outcome = "rolled back"
                policy_id = body.policy_id
            else:
                if body.document is None:
                    raise HTTPException(
                        status_code=400, detail="provide `document`, `policy_id`, or `clear`"
                    )
                text, doc_hash = _canonical(body.document)
                # Usability, NOT the security control: the resolver's intersection already makes
                # an above-ceiling entry inert. Rejecting here only means an admin finds out
                # immediately that a line will do nothing (DECISIONS #53).
                found = problems(text)
                if found:
                    raise HTTPException(status_code=400, detail="; ".join(found))
                policy_id = await store.insert_governance_policy(
                    kind, text, doc_hash, principal.actor, now, body.note
                )
                await store.set_active_governance_policy(kind, policy_id, principal.actor, now)
                after_summary = {"policy_id": policy_id, "doc_hash": doc_hash}
                outcome = "applied"
            await audit_row(
                request,
                principal,
                action,
                "ok",
                object_type="governance_policy",
                object_id=str(policy_id) if policy_id is not None else None,
                details={
                    "kind": kind,
                    "action": outcome,
                    "before": before_summary,
                    "after": after_summary,
                    "note_len": len(body.note),
                },
            )
        return {"status": outcome, "policy_id": policy_id}

    def _capability_problems(text: str) -> list[str]:
        return rbac.capability_policy_errors(rbac.parse_capability_policy(text))

    def _scope_problems(text: str) -> list[str]:
        return shaping.scope_policy_errors(shaping.parse_scope_policy(text))

    @route.get("/api/rbac", dependencies=guarded)
    async def get_rbac_policy() -> dict[str, Any]:
        """The capability policy, what it resolves to per role, and the immutable history.

        `ceiling` is shipped alongside `resolved` so an admin can see the wall as well as where
        they are standing: a capability in `ceiling` but not in `resolved` was taken away by
        policy, and one absent from `ceiling` can never be granted at all.
        """
        async with store.lock:
            await governance.load()
            history = await store.list_governance_policies("rbac", MAX_POLICY_HISTORY)
            active = await _active_policy("rbac")
        policy = governance.capability
        return {
            "kind": "rbac",
            "active": _policy_row(active) if active else None,
            "configured": active is not None,
            "malformed": bool(policy is not None and policy.malformed),
            "malformed_reason": policy.reason if policy is not None and policy.malformed else "",
            "ceiling": {role: sorted(rbac.ceiling(role)) for role in rbac.ROLE_RANK},
            "resolved": {
                role: sorted(rbac.resolve_capabilities(role, None, policy))
                for role in rbac.ROLE_RANK
            },
            "recovery_capabilities": sorted(rbac.RECOVERY_CAPABILITIES),
            "all_capabilities": sorted(rbac.PERMISSIONS),
            "history": [_policy_row(row) for row in history],
        }

    @route.post("/api/rbac")
    async def set_rbac_policy(
        body: PolicyIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """Apply, roll back, or clear the capability policy. Audited; reversible in one call.

        Nothing written here can escalate: the resolver intersects with the compiled ceiling, so
        the worst a hostile document achieves is taking capabilities away — and never the admin's
        recovery set (DECISIONS #64), so this endpoint stays reachable to undo it.
        """
        return await _write_policy(
            "rbac", body, request, principal, "rbac.policy.update", _capability_problems
        )

    @route.get("/api/scope", dependencies=guarded)
    async def get_scope_policy() -> dict[str, Any]:
        async with store.lock:
            await governance.load()
            history = await store.list_governance_policies("scope", MAX_POLICY_HISTORY)
            active = await _active_policy("scope")
            nes = await store.list_ne_for_scope()
        policy = governance.scope
        resolved = {
            role: sorted(shaping.visible_nes(role, None, policy, nes).ne_ids)
            for role in rbac.ROLE_RANK
            if role != "admin"
        }
        return {
            "kind": "scope",
            "active": _policy_row(active) if active else None,
            "configured": active is not None,
            "malformed": bool(policy is not None and policy.malformed),
            "malformed_reason": policy.reason if policy is not None and policy.malformed else "",
            "ne_count": len(nes),
            "resolved_ne_ids": resolved,
            "admin_is_never_scoped": True,
            "not_tenant_isolation": (
                "Visibility scoping is a presentation control, NOT tenant isolation. Correlation "
                "still learns across every network element, and a situation may still form across "
                "a boundary a principal cannot see — its members are then hidden from them, not "
                "prevented from correlating."
            ),
            "history": [_policy_row(row) for row in history],
        }

    @route.post("/api/scope")
    async def set_scope_policy(
        body: PolicyIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """Apply, roll back, or clear the visibility scope. Audited; reversible in one call."""
        return await _write_policy(
            "scope", body, request, principal, "scope.policy.update", _scope_problems
        )
