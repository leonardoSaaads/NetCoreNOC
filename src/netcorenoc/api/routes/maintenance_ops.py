"""The five **explicit operations** on a maintenance window (Part III).

    preview · confirm · cancel · end now · extend

Split from :mod:`netcorenoc.api.routes.maintenance` at the 400-line guard, on the seam Part III
already draws: that module is the resource — list it, read it, create it, change it — and these
are the verbs an operator or an agent invokes *on* one. They differ in what they assert, in who
may do them, and in what they audit, which is the same reason the five operator gestures are five
routes rather than one carrying an `operation` field (DECISIONS #255).

**Registered before the resource module**, and that is behaviour rather than taste: FastAPI
resolves the first matching route, so `POST /api/maintenance-windows/preview` must be declared
before `POST /api/maintenance-windows/{wid}` or it is read as a window whose id is `preview`.

## D6, and II.7's two edges settled here

Up to six hours: no confirmation. Over six hours: an editor or an admin confirms, and the window
waits in `pending_confirmation` — collecting nothing — until they do.

**Who confirms** (II.7's first edge, ADR #370): a window created by an **agent** waits for a human
editor or admin, and the agent cannot confirm its own. A window created by a **human** editor or
admin may be confirmed by that same person — the form asks for an explicit second gesture, no
second person. Four-eyes is a policy this appliance does not have anywhere else, and inventing it
here would be inventing it in the one place nobody asked for it; a deployment that wants it
withholds `mw.confirm` from the role that holds `mw.write`, because `resolve_capabilities` is
`ceiling n policy` and the two capabilities are separate for exactly this reason.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request

from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.api.models import MaintenanceWindowPreviewIn, WindowExtendIn
from netcorenoc.api.mw_shape import WindowAccess, needs_confirmation, restatus
from netcorenoc.crosscutting import auth, shaping
from netcorenoc.store.maintenance_windows import CONFIRMATION_THRESHOLD_S, LIVE_STATUSES


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the maintenance-window operation routes on `app`."""
    store, engine, security = ctx.store, ctx.engine, ctx.security
    audit_row, write_txn = ctx.perimeter.audit_row, ctx.write_txn
    access = WindowAccess(store, ctx.scope_for, ctx.perimeter.audit_scope_denial)
    route = DeclaredRoutes(app)

    @route.post("/api/maintenance-windows/preview")
    async def preview_window(
        body: MaintenanceWindowPreviewIn,
        request: Request,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """**The dry run** — *"if I create this, what does it cover?"* (Part III).

        The most useful call an agent can make before committing, and **the same code the form's
        live preview uses**, so the number an operator reads and the number an agent reads cannot
        disagree. Read-only: it writes nothing, audits nothing and creates nothing.

        Registered before `POST /api/maintenance-windows/{wid}` because FastAPI resolves the first
        matching route and `preview` would otherwise be read as a window id.
        """
        targets = await access.permitted_targets(body.targets, principal)
        counts = await store.preview_counts(targets, await access.scope_ne_ids(principal))
        now = time.time()
        return {
            **counts,
            "starts_in_s": body.starts_at - now,
            "duration_s": body.ends_at - body.starts_at,
            "needs_confirmation": needs_confirmation(body.starts_at, body.ends_at),
            "confirmation_threshold_s": CONFIRMATION_THRESHOLD_S,
            "tz": body.tz,
            "site_time": shaping.wall_clock(body.tz, body.starts_at),
            "site_offset": shaping.offset_label(body.tz, body.starts_at),
            "rules": len(body.rules),
            # What the window would collect per target, said in the API rather than only drawn.
            "targets_with_no_rule": sorted(
                ne for ne in targets if not any(r.ne_id == ne for r in body.rules)
            ),
        }

    @route.post("/api/maintenance-windows/{wid}/confirm")
    async def confirm_window(
        wid: int, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, bool]:
        """D6's human gesture. **An agent may not confirm** — see the module docstring, ADR #370."""
        window = await access.visible_or_404(wid, principal, request)
        if principal.is_token:
            raise HTTPException(
                status_code=403,
                detail="a maintenance window over six hours waits for a human editor or admin; a "
                "service token may create one and may not agree to it",
            )
        now = time.time()
        async with write_txn():
            if not await store.confirm_window(wid, principal.ref or "-", now):
                raise HTTPException(
                    status_code=409,
                    detail=f"this window is {window['status']}, not waiting for confirmation; "
                    "reload the card",
                )
            await audit_row(
                request,
                principal,
                "maintenance.window.confirm",
                "ok",
                object_type="maintenance_window",
                object_id=str(wid),
                details={"created_by_agent": bool(window["created_by_agent"])},
            )
        return {"confirmed": True}

    @route.post("/api/maintenance-windows/{wid}/cancel")
    async def cancel_window(
        wid: int, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, bool]:
        """Call a window off. **A state, not a delete** — the plan and its audit row survive."""
        window = await access.visible_or_404(wid, principal, request)
        now = time.time()
        async with write_txn():
            if not await store.cancel_window(wid, now):
                raise HTTPException(
                    status_code=409,
                    detail=f"this window has already {window['status']}; reload the card",
                )
            await audit_row(
                request,
                principal,
                "maintenance.window.cancel",
                "ok",
                object_type="maintenance_window",
                object_id=str(wid),
                details={"was": window["status"]},
            )
        return {"cancelled": True}

    @route.post("/api/maintenance-windows/{wid}/end")
    async def end_window(
        wid: int, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """*"The work is done, give me my alarms back."*

        The sweep that follows surfaces whatever the window suppressed and never saw clear (II.2),
        so ending early is not a way to lose a fault — it is a way to stop suppressing one.
        """
        window = await access.visible_or_404(wid, principal, request)
        now = time.time()
        async with write_txn():
            if not await store.end_window_now(wid, now):
                raise HTTPException(
                    status_code=409,
                    detail=f"this window has already {window['status']}; reload the card",
                )
            surfaced = await engine.surface_window_now(wid, now)
            await audit_row(
                request,
                principal,
                "maintenance.window.end",
                "ok",
                object_type="maintenance_window",
                object_id=str(wid),
                details={"early_by_s": round(float(window["ends_at"]) - now), "surfaced": surfaced},
            )
        return {"ended": True, "surfaced": surfaced}

    @route.post("/api/maintenance-windows/{wid}/extend")
    async def extend_window(
        wid: int,
        body: WindowExtendIn,
        request: Request,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """*"The work is running long."* **D6 is re-decided**, and the answer is in the response."""
        window = await access.visible_or_404(wid, principal, request)
        if window["status"] not in LIVE_STATUSES:
            raise HTTPException(
                status_code=409, detail=f"this window has {window['status']}; reload the card"
            )
        if body.ends_at <= float(window["ends_at"]):
            raise HTTPException(
                status_code=422,
                detail="extend moves a window's end later; to finish early use "
                "POST /api/maintenance-windows/{id}/end",
            )
        now = time.time()
        needs = needs_confirmation(float(window["starts_at"]), body.ends_at)
        status = restatus(window, needs, float(window["starts_at"]), now)
        async with write_txn():
            await store.update_window_schedule(
                wid,
                starts_at=float(window["starts_at"]),
                ends_at=body.ends_at,
                tz=str(window["tz"]),
                patch_s=float(window["patch_s"]),
                needs_confirmation=needs,
                status=status,
                now=now,
            )
            await audit_row(
                request,
                principal,
                "maintenance.window.extend",
                "ok",
                object_type="maintenance_window",
                object_id=str(wid),
                details={
                    "by_s": round(body.ends_at - float(window["ends_at"])),
                    "needs_confirmation": needs,
                    "agent": principal.is_token,
                },
            )
        return {
            "extended": True,
            "ends_at": body.ends_at,
            "needs_confirmation": needs,
            "window_status": status,
        }
