"""NOC → Playbooks: the troubleshooting trees agents write, and how each has run (ADR #463).

**Reading, writing and switching one on are a lead's** (ADRs #465, #470): `playbooks.read` or
`playbooks.write` and, on top, `noc_gate.is_lead` — an admin, an editor an admin named a lead, or
an agent (the grant that lets a token use credentials, which is what a playbook does). Switching
one off is a kill switch, so anyone with `playbooks.write` may. **It is active as soon as it is
stored** (the owner's choice), and that is safe because of what a playbook can say: read-only
commands only, checked when it is stored and again when it runs (`engine/noc/commands.py`), four
fixed tests on what came back, and three ways to end.

Every write is a `noc.playbook.*` audit row; every run is a `noc.playbook.run` row and a trace.
"""

from __future__ import annotations

import json
import time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request

from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.api.models_cases import PlaybookEnabledIn, PlaybookIn
from netcorenoc.api.noc_gate import is_lead, require_lead
from netcorenoc.crosscutting import auth
from netcorenoc.engine.noc import cases, executor, playbook
from netcorenoc.engine.noc.playbook import PlaybookError

NO_SUCH = "no such playbook"
NOT_AUTHOR = (
    "a playbook acts with the credentials agents may use: it is written, or switched on, by an "
    "agent the NOC hired, an admin, or an editor an admin named a NOC lead"
)


def _view(row: dict[str, Any], *, runs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    document = json.loads(row["document"])
    out = {
        "id": row["id"],
        "name": row["name"],
        "description": document.get("description", ""),
        "match": document["match"],
        "timeout_s": document.get("timeout_s", 300),
        "steps": len(document["steps"]),
        "version": row["version"],
        "enabled": row["enabled"],
        "source_case": row["source_case"],
        "created_at": row["created_at"],
        "created_by": row["created_by"],
        "updated_at": row["updated_at"],
        "runs": row.get("runs"),
        "last_run_at": row.get("last_run_at"),
    }
    if runs is not None:
        out["document"] = document
        out["recent_runs"] = [
            {
                **{
                    k: r[k]
                    for k in ("id", "version", "situation_id", "started_at", "ended_at", "outcome")
                },
                "trace": json.loads(r["trace"]),
            }
            for r in runs
        ]
    return out


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the playbook routes on `app`."""
    store, security = ctx.store, ctx.security
    audit_row, write_txn = ctx.perimeter.audit_row, ctx.write_txn
    route = DeclaredRoutes(app)

    async def require_author(request: Request, principal: auth.Principal) -> None:
        if not await is_lead(ctx, request, principal):
            raise HTTPException(status_code=403, detail=NOT_AUTHOR)

    def checked(document: Any) -> dict[str, Any]:
        try:
            return playbook.validate(document)
        except PlaybookError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @route.get("/api/noc/playbooks", operation_id="noc_list_playbooks")
    async def list_playbooks(
        request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """Every playbook: what it matches, whether it is on, how often it ran."""
        await require_lead(ctx, request, principal)
        async with store.reading():
            rows = await store.noc_playbooks()
        return {
            "playbooks": [_view(r) for r in rows],
            "actions": list(playbook.ACTIONS),
            "ends": list(playbook.ENDS),
            # What a `resolve` leaf's cause and a `pending` leaf's status may be, with their labels.
            "causes": cases.CAUSES,
            "pending": {s: cases.STATUSES[s] for s in sorted(cases.PENDING)},
            # Whether SSH steps can run here at all (the optional `noc` extra, ADR #462).
            "ssh_available": executor.available(),
        }

    @route.post("/api/noc/playbooks", operation_id="noc_create_playbook")
    async def create_playbook(
        body: PlaybookIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """Store a playbook — active at once. Read-only commands only; refused with the path to
        the first thing wrong."""
        await require_author(request, principal)
        document = checked(body.document)
        text = playbook.canonical(document)
        async with write_txn():
            if body.source_case is not None and await store.noc_case(body.source_case) is None:
                raise HTTPException(status_code=400, detail="source_case: no such case")
            playbook_id = await store.noc_create_playbook(
                document["name"].strip(), text, body.source_case, time.time(), principal.actor
            )
            await audit_row(
                request,
                principal,
                "noc.playbook.create",
                "ok",
                object_type="noc_playbook",
                object_id=str(playbook_id),
                details={"source_case": body.source_case},
            )
            row = await store.noc_playbook(playbook_id)
        assert row is not None
        return _view(row, runs=[])

    @route.get("/api/noc/playbooks/{playbook_id}", operation_id="noc_get_playbook")
    async def get_playbook(
        playbook_id: int, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """One playbook, its whole tree, and its last twenty runs with their traces."""
        await require_lead(ctx, request, principal)
        async with store.reading():
            row = await store.noc_playbook(playbook_id)
            runs = await store.noc_runs(playbook_id=playbook_id) if row else []
        if row is None:
            raise HTTPException(status_code=404, detail=NO_SUCH)
        return _view(row, runs=runs)

    @route.post("/api/noc/playbooks/{playbook_id}", operation_id="noc_update_playbook")
    async def update_playbook(
        playbook_id: int,
        body: PlaybookIn,
        request: Request,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """Replace a playbook's tree: a new version, validated as a new one is."""
        await require_author(request, principal)
        document = checked(body.document)
        async with write_txn():
            if await store.noc_playbook(playbook_id) is None:
                raise HTTPException(status_code=404, detail=NO_SUCH)
            await store.noc_update_playbook(
                playbook_id,
                document["name"].strip(),
                playbook.canonical(document),
                time.time(),
                principal.actor,
            )
            await audit_row(
                request,
                principal,
                "noc.playbook.update",
                "ok",
                object_type="noc_playbook",
                object_id=str(playbook_id),
            )
            row = await store.noc_playbook(playbook_id)
        assert row is not None
        return _view(row, runs=[])

    @route.post("/api/noc/playbooks/{playbook_id}/enabled", operation_id="noc_enable_playbook")
    async def enable_playbook(
        playbook_id: int,
        body: PlaybookEnabledIn,
        request: Request,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """Switch a playbook on or off. Off takes effect for every case not already running it."""
        if body.enabled:
            await require_author(request, principal)
        async with write_txn():
            if await store.noc_playbook(playbook_id) is None:
                raise HTTPException(status_code=404, detail=NO_SUCH)
            await store.noc_enable_playbook(playbook_id, body.enabled, time.time(), principal.actor)
            await audit_row(
                request,
                principal,
                "noc.playbook.update",
                "ok",
                object_type="noc_playbook",
                object_id=str(playbook_id),
                details={"enabled": body.enabled},
            )
            row = await store.noc_playbook(playbook_id)
        assert row is not None
        return _view(row)
