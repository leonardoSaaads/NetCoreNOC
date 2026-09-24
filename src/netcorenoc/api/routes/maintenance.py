"""`/api/maintenance-windows` — the resource: list, read, create, update.

**A clean resource first and a form second** (Part 0.3). Phase 2 is an AI agent reaching this
appliance through MCP. It will create windows — *"put the south tower in maintenance tonight"* —
and, more importantly, it will **ask whether a member of a correlated situation is under one**
before it acts, because an agent that escalates planned work as an outage is worse than no agent.
So this is designed for a caller that cannot read a docstring: `Literal` enums so `/openapi.json`
carries the permitted values, errors that name the field and the rule, and an idempotency key so a
retry after a timeout does not create two windows.

The five **explicit operations** — preview, confirm, cancel, end, extend — are
:mod:`netcorenoc.api.routes.maintenance_ops`, split at the 400-line guard on the seam Part III
already draws between the resource and the verbs it offers. They register **first**, because
FastAPI resolves the first matching route and `POST /api/maintenance-windows/preview` would
otherwise be read as `POST /api/maintenance-windows/{wid}` with `wid="preview"`.

## The three security rules both modules are built around

**No existence oracle.** A window naming an element outside the caller's scope must not reveal, by
error or by preview count, whether that element exists. `WindowAccess.permitted_targets` narrows
silently; `preview_counts` drops out-of-scope elements before counting.

**The existence of a window is public; its details are not.** Prime directive 4: a host that goes
quiet with no marker reads as healthy. `mw.read` is `viewer` and every role sees the marker; the
name, description, owner and rules follow `visibility`, and that filter is **in the query** —
`window_markers` is a different statement returning different columns.

**Every write is audited with the actor and whether it was a human or a token.** Seven actions
rather than one carrying a verb, for the reason the five operator gestures are five actions.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request

from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.api.models import MaintenanceWindowIn, MaintenanceWindowUpdateIn
from netcorenoc.api.mw_shape import (
    WindowAccess,
    initial_status,
    needs_confirmation,
    parse_statuses,
    restatus,
    shape,
)
from netcorenoc.crosscutting import auth, rbac
from netcorenoc.store.maintenance_windows import LIVE_STATUSES, WindowDraft
from netcorenoc.store.mw_reads import MAX_WINDOW_LIMIT

#: D7's three sizes are 5 / 10 / 20; this is the default a caller who names none gets, and
#: `MAX_WINDOW_LIMIT` in the store is the ceiling they cannot exceed.
DEFAULT_LIMIT = 20


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the maintenance-window resource routes on `app`."""
    store, security, governance = ctx.store, ctx.security, ctx.governance
    audit_row, write_txn = ctx.perimeter.audit_row, ctx.write_txn
    access = WindowAccess(store, ctx.scope_for, ctx.perimeter.audit_scope_denial)
    route = DeclaredRoutes(app)

    @route.get("/api/maintenance-windows")
    async def list_windows(
        request: Request,
        status: str | None = Query(default=None),
        organization_id: int | None = Query(default=None),
        ne_id: int | None = Query(default=None),
        mine: bool = Query(default=False),
        since: float | None = Query(default=None),
        until: float | None = Query(default=None),
        limit: int = Query(default=DEFAULT_LIMIT, ge=1, le=MAX_WINDOW_LIMIT),
        offset: int = Query(default=0, ge=0),
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """The upcoming list (D7), and every other filtered view of it.

        `limit` serves D7's 5 / 10 / 20 and is bounded by the store rather than trusted. The
        response carries `total` so the console can say *"showing 5 of 23"* honestly rather than
        implying the page is the population.

        **Rows a caller may not see in full are shaped, not dropped.** A viewer sees every window's
        existence, status and timing — prime directive 4 — and sees the name, owner and description
        only where `visibility` permits. Dropping the row would recreate the quiet-host problem in
        the list instead of on the device.

        **A row naming nothing the caller may see is a different case and is dropped** — by the
        query, since v0.21.1. It is not their window at all, and `total` counts what the same
        predicate returns, so the page and the total cannot disagree (F151).
        """
        statuses = parse_statuses(status)
        scope_ne_ids = await access.scope_ne_ids(principal)
        filters: dict[str, Any] = {
            "statuses": statuses,
            "organization_id": organization_id,
            "ne_id": ne_id,
            "owner_ref": principal.ref if mine else None,
            "since": since,
            "until": until,
            "scope_ne_ids": scope_ne_ids,
        }
        rows = await store.list_maintenance_windows(**filters, limit=limit, offset=offset)
        scope = await ctx.scope_for(principal)
        out: list[dict[str, Any]] = []
        for row in rows:
            if not scope.unrestricted:
                # The belt for the one case the query cannot carry: a scope larger than
                # `MAX_SCOPE_PARAMS` binds no id list, so the statement above returns unscoped rows
                # and the drop happens here — the same fallback `window_markers` makes.
                targets = await store.window_targets(int(row["id"]))
                if targets and not any(scope.allows_ne(t["ne_id"]) for t in targets):
                    continue
            out.append(shape(row, principal, time.time()))
        return {
            "windows": out,
            "total": await store.count_maintenance_windows(**filters),
            "limit": limit,
            "offset": offset,
            "now": time.time(),
        }

    @route.post("/api/maintenance-windows")
    async def create_window(
        body: MaintenanceWindowIn,
        request: Request,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """Declare planned work. **D6 decides here whether a human has to agree to it.**"""
        if body.idempotency_key:
            existing = await store.window_for_idempotency_key(body.idempotency_key)
            if existing is not None:
                # **The retry answer** (Part III). Same id, same body, no second window and no
                # 409 for the agent to interpret.
                #
                # The key is unique across the appliance rather than per caller, so this is also
                # the one route that can hand a caller a window they did not create. It asks the
                # scope question every other window route asks, and a key belonging to work over
                # elements this caller cannot see answers with the **public half**: the key is
                # taken, the window exists, and nothing else (F152).
                window = await store.maintenance_window(existing)
                assert window is not None
                return {
                    "created": False,
                    **shape(
                        window,
                        principal,
                        time.time(),
                        in_scope=await access.in_scope(window, principal),
                    ),
                }
        organization_id = body.organization_id or await store.default_organization_id()
        if not await store.organization_exists(organization_id):
            raise HTTPException(
                status_code=422,
                detail=f"organization_id {organization_id} does not exist; "
                "GET /api/organizations lists them",
            )
        targets = await access.permitted_targets(body.targets, principal)
        needs = needs_confirmation(body.starts_at, body.ends_at)
        now = time.time()
        draft = WindowDraft(
            name=body.name,
            description=body.description,
            organization_id=organization_id,
            tz=body.tz,
            starts_at=body.starts_at,
            ends_at=body.ends_at,
            all_day=body.all_day,
            patch_s=body.patch_s,
            ledger_enabled=body.ledger_enabled,
            visibility=body.visibility,
            owner_ref=principal.ref or "-",
            owner_role=principal.role,
            created_by_agent=principal.is_token,
            needs_confirmation=needs,
            status="pending_confirmation" if needs else initial_status(body.starts_at, now),
            idempotency_key=body.idempotency_key,
        )
        async with write_txn():
            window_id = await store.create_maintenance_window(draft, now)
            await store.set_window_targets(window_id, targets)
            await store.set_window_rules(
                window_id,
                [rule.as_row() for rule in body.rules if rule.ne_id in set(targets)],
                now,
            )
            await audit_row(
                request,
                principal,
                "maintenance.window.create",
                "ok",
                object_type="maintenance_window",
                object_id=str(window_id),
                details={
                    "targets": len(targets),
                    "rules": len(body.rules),
                    "duration_s": round(body.ends_at - body.starts_at),
                    "needs_confirmation": needs,
                    "tz": body.tz,
                    # **An agent-created window is marked**, and the audit log is the record that
                    # cannot be edited afterwards (Part III).
                    "agent": principal.is_token,
                },
            )
        window = await store.maintenance_window(window_id)
        assert window is not None
        # **`created`, not `status`** — and the distinction is a contract bug this release's own
        # test caught. `status` is the WINDOW's state (`scheduled`, `pending_confirmation`, ...)
        # everywhere else on this resource, so an envelope field of the same name would mean two
        # things depending on which call produced the body, and the resource field would win the
        # merge silently. An agent reading `status` now reads one thing, always.
        return {"created": True, **shape(window, principal, now)}

    @route.get("/api/maintenance-windows/{wid}")
    async def read_window(
        wid: int, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """One window in full, with its targets and its rules — **if the caller may see it.**"""
        window = await access.visible_or_404(wid, principal, request, "maintenance.window.read")
        now = time.time()
        async with store.lock:
            await governance.load()
        may_read_ledger = "mw.ledger" in rbac.resolve_capabilities(
            principal.role, principal.ref, governance.capability
        )
        return {
            **shape(window, principal, now),
            "targets": await store.window_targets(wid),
            "rules": await store.window_rules(wid),
            # Counts only, and behind `mw.ledger`: the ledger is not a view of the network and
            # this is not a way to make it one (`store/mw_ledger.py`). Resolved through
            # `rbac.resolve_capabilities` rather than compared against a role, which is F28 — a
            # role test in a handler is an authorization decision nobody reviewing the capability
            # table can see.
            "ledger": (await store.ledger_summary(wid) if may_read_ledger else None),
        }

    @route.post("/api/maintenance-windows/{wid}")
    async def update_window(
        wid: int,
        body: MaintenanceWindowUpdateIn,
        request: Request,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """Change a window. **D6 is re-decided here**, in the one statement that moves the bounds.

        An extend that pushes a four-hour window past six hours makes it need confirmation again,
        and it says so in the response rather than quietly re-arming a gate the operator thought
        they had passed.
        """
        window = await access.visible_or_404(wid, principal, request, "maintenance.window.update")
        if window["status"] not in LIVE_STATUSES:
            raise HTTPException(
                status_code=409,
                detail=f"this window has {window['status']}; reload the card",
            )
        targets = await access.permitted_targets(body.targets, principal)
        needs = needs_confirmation(body.starts_at, body.ends_at)
        now = time.time()
        status = restatus(window, needs, body.starts_at, now)
        async with write_txn():
            await store.update_window_fields(
                wid,
                name=body.name,
                description=body.description,
                visibility=body.visibility,
                ledger_enabled=body.ledger_enabled,
                now=now,
            )
            await store.update_window_schedule(
                wid,
                starts_at=body.starts_at,
                ends_at=body.ends_at,
                tz=body.tz,
                patch_s=body.patch_s,
                needs_confirmation=needs,
                status=status,
                now=now,
            )
            await store.set_window_targets(wid, targets)
            await store.set_window_rules(
                wid,
                [rule.as_row() for rule in body.rules if rule.ne_id in set(targets)],
                now,
            )
            await audit_row(
                request,
                principal,
                "maintenance.window.update",
                "ok",
                object_type="maintenance_window",
                object_id=str(wid),
                details={
                    "targets": len(targets),
                    "rules": len(body.rules),
                    "status": status,
                    "needs_confirmation": needs,
                    "agent": principal.is_token,
                },
            )
        updated = await store.maintenance_window(wid)
        assert updated is not None
        return {"updated": True, **shape(updated, principal, now)}
