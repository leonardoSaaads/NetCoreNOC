"""Quarantine and the audit log: read, export, prune.

The only routes in the estate where a **read** is itself audited, and where the read therefore
takes the write transaction: `quarantine.read`, `audit.read` and `audit.export` each write a row
recording that they happened. That is why every handler here opens `write_txn()` even though three
of the four return data rather than changing it.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.crosscutting import audit, audit_sections, auth

#: Audit rows per page of a streamed export.
EXPORT_PAGE = 2000
SECTION_NAMES = ", ".join(audit_sections.SECTIONS)


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
        request: Request,
        limit: int = 200,
        since: float | None = None,
        until: float | None = None,
        actor: str | None = None,
        section: str | None = None,
        outcome: str | None = None,
        q: str | None = None,
        before: int | None = None,
        principal: auth.Principal = Depends(security),
    ) -> list[dict[str, Any]]:
        """The newest rows, narrowed (ADR #486): a period, a person, a section of the product, an
        outcome, a text, and a cursor (`before`, an id) for the next page. The read is audited with
        the filters it used."""
        if section is not None and section not in audit_sections.SECTIONS:
            raise HTTPException(status_code=400, detail=f"section: one of {SECTION_NAMES}")
        if outcome is not None and outcome not in ("ok", "denied", "error"):
            raise HTTPException(status_code=400, detail="outcome: ok, denied or error")
        who = (actor or "").strip()[:64] or None
        text = (q or "").strip()[:100] or None
        filters: dict[str, Any] = {
            "since": since,
            "until": until,
            "actor": who,
            "section": section,
            "outcome": outcome,
            "q": text,
            "before": before,
        }
        async with write_txn():
            rows = await store.list_audit(
                min(max(limit, 1), 1000),
                since=since,
                until=until,
                actor=who,
                words=audit_sections.SECTIONS[section][1] if section else None,
                outcome=outcome,
                text=text,
                before_id=before,
            )
            used = {k: v for k, v in filters.items() if v is not None}
            await audit_row(
                request,
                principal,
                "audit.read",
                "ok",
                details={"count": len(rows), **({"filters": used} if used else {})},
            )
        return rows

    @route.get("/api/audit/facets")
    async def audit_facets(
        request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """What the filters can offer: the people in the log, each section with its count, and
        the period the log covers. Aggregates only; the read is audited."""
        async with write_txn():
            facets = await store.audit_facets()
            await audit_row(request, principal, "audit.read", "ok", details={"facets": True})
        counts: dict[str, int] = dict.fromkeys(audit_sections.SECTIONS, 0)
        for action, n in facets.pop("actions").items():
            name = audit_sections.section_of(action)
            if name is not None:
                counts[name] += n
        facets["sections"] = [
            {"id": name, "label": label, "words": list(words), "count": counts[name]}
            for name, (label, words) in audit_sections.SECTIONS.items()
        ]
        return facets

    @route.get("/api/audit/export")
    async def export_audit(
        request: Request, principal: auth.Principal = Depends(security)
    ) -> Response:
        """The chain up to its newest row, as NDJSON. The export is recorded first, under the write
        lock, and the rows are then streamed a page at a time from readers (ADR #453): a year's
        500k rows held the lock 7.6 s and 570 MiB — more than the container's limit — when they
        were read whole inside the transaction. The bytes are the same."""
        async with write_txn():
            last_id, final_hash = await store.audit_tail()
            await audit_row(
                request, principal, "audit.export", "ok", details={"final_hash": final_hash}
            )

        async def lines() -> AsyncIterator[str]:
            after = 0
            while after < last_id:
                async with store.reading():
                    page = await store.audit_page(after, last_id, EXPORT_PAGE)
                if not page:
                    return
                yield "".join(audit.export_line(row) + "\n" for row in page)
                after = int(page[-1]["id"])

        return StreamingResponse(
            lines(), media_type="application/x-ndjson", headers={"X-Audit-Final-Hash": final_hash}
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
