"""Quarantine and the audit log: read, export, prune.

The only routes in the estate where a **read** is itself audited, and where the read therefore
takes the write transaction: `quarantine.read`, `audit.read` and `audit.export` each write a row
recording that they happened. That is why every handler here opens `write_txn()` even though three
of the four return data rather than changing it.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import Depends, FastAPI, Request, Response

from netcorenoc import audit, auth
from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the audit routes on `app`."""
    store, engine, security = ctx.store, ctx.engine, ctx.security
    audit_row, write_txn = ctx.perimeter.audit_row, ctx.write_txn
    route = DeclaredRoutes(app)

    # -- admin: quarantine (the read itself is audited) --------------------------------

    @route.get("/api/quarantine")
    async def read_quarantine(
        request: Request, limit: int = 100, principal: auth.Principal = Depends(security)
    ) -> list[dict[str, Any]]:
        async with write_txn():
            rows = await store.list_quarantine(min(max(limit, 1), 500))
            await audit_row(
                request,
                principal,
                "quarantine.read",
                "ok",
                object_type="quarantine",
                details={"count": len(rows)},
            )
        return rows

    # -- admin: audit ------------------------------------------------------------------

    @route.get("/api/audit")
    async def read_audit(
        request: Request, limit: int = 200, principal: auth.Principal = Depends(security)
    ) -> list[dict[str, Any]]:
        async with write_txn():
            rows = await store.list_audit(min(max(limit, 1), 1000))
            await audit_row(request, principal, "audit.read", "ok", details={"count": len(rows)})
        return rows

    @route.get("/api/audit/export")
    async def export_audit(
        request: Request, principal: auth.Principal = Depends(security)
    ) -> Response:
        async with write_txn():
            lines, final_hash = await audit.export_ndjson(store)
            await audit_row(
                request, principal, "audit.export", "ok", details={"final_hash": final_hash}
            )
        body = "\n".join(lines) + ("\n" if lines else "")
        return Response(
            content=body,
            media_type="application/x-ndjson",
            headers={"X-Audit-Final-Hash": final_hash},
        )

    @route.post("/api/audit/prune")
    async def prune_audit(
        request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        async with write_txn():
            retention_days = float(
                await store.get_meta("config.audit_retention_days") or engine.audit_retention_days
            )
            removed = await store.prune_audit(time.time() - retention_days * 86400.0)
            await audit_row(
                request,
                principal,
                "prune.manual",
                "ok",
                object_type="audit_log",
                details={"removed": removed},
            )
        return {"status": "pruned", "removed": removed}
