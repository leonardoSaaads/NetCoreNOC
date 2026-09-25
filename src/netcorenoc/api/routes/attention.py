"""Operator acknowledgements that change what is shown, never what is known (v0.22.0).

* `GET /api/notices` — the current warnings, each marked security-posture or not and whether THIS
  user has snoozed it; for an admin, also every live snooze of a security warning by anyone, so the
  posture is never invisible to the person responsible for it (item 1, ADR #387).
* `POST /api/notices/snooze`, `DELETE /api/notices/snooze/{digest}` — snooze or restore one
  warning, for oneself. **A security warning's snooze always expires** (24 h or 7 days); only an
  operational one may be snoozed "until it changes". Only a warning currently emitted can be
  snoozed: a digest of text nobody is shown is refused rather than stored.
* `POST /api/alarms/{aid}/outlived/ack` — the marker on a fault that outlived a maintenance window
  has been seen (item 8, ADR #388). The alarm stays active and stays where it is.

Every write is audited with the actor. None of them is evidence, and none reaches a dataset table.
"""

from __future__ import annotations

import time
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.crosscutting import auth, posture, rbac
from netcorenoc.store.attention import notice_digest


class SnoozeIn(BaseModel):
    """Snooze one warning, named by the digest `GET /api/notices` served for it."""

    digest: str = Field(min_length=8, max_length=64, pattern=r"^[0-9a-f]+$")
    mode: Literal["24h", "7d", "change"]


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the acknowledgement routes on `app`."""
    store, security, scope_for = ctx.store, ctx.security, ctx.scope_for
    audit_row, write_txn, governance = ctx.perimeter.audit_row, ctx.write_txn, ctx.governance
    all_warnings = ctx.all_warnings
    audit_scope_denial = ctx.perimeter.audit_scope_denial
    route = DeclaredRoutes(app)

    def current() -> dict[str, str]:
        return {notice_digest(text): text for text in all_warnings()}

    @route.get("/api/notices")
    async def notices(principal: auth.Principal = Depends(security)) -> dict[str, Any]:
        """The warnings as this user sees them: which are snoozed, which are security posture."""
        now = time.time()
        async with store.lock:
            mine = await store.snoozes_for(principal.user_id, now) if principal.user_id else {}
            await governance.load()
            admin = "config.read" in rbac.resolve_capabilities(
                principal.role, principal.ref, governance.capability
            )
            others = await store.security_snoozes(now) if admin else None
        out = []
        for digest, text in current().items():
            snooze = mine.get(digest)
            out.append(
                {
                    "digest": digest,
                    "text": text,
                    "security": posture.is_security_posture(text),
                    "snoozed": (
                        {"mode": snooze["mode"], "until": snooze["until"]} if snooze else None
                    ),
                }
            )
        return {"notices": out, "security_snoozes": others}

    @route.post("/api/notices/snooze")
    async def snooze(
        body: SnoozeIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        text = current().get(body.digest)
        if text is None:
            raise HTTPException(status_code=404, detail="no such warning is being shown")
        if principal.user_id is None:
            raise HTTPException(
                status_code=409, detail="only a signed-in user can snooze a warning"
            )
        is_security = posture.is_security_posture(text)
        if is_security and body.mode == "change":
            raise HTTPException(
                status_code=422,
                detail="a security warning can be snoozed for 24h or 7d, not until it changes",
            )
        now = time.time()
        async with write_txn():
            snoozed = await store.snooze_notice(
                user_id=principal.user_id, text=text, security=is_security, mode=body.mode, now=now
            )
            await audit_row(
                request,
                principal,
                "notice.snooze",
                "ok",
                object_type="notice",
                object_id=body.digest,
                details={"mode": body.mode, "security": is_security, "until": snoozed["until"]},
            )
        return snoozed

    @route.delete("/api/notices/snooze/{digest}")
    async def unsnooze(
        digest: str, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, str]:
        if principal.user_id is None:
            raise HTTPException(status_code=404, detail="no such snooze")
        async with write_txn():
            if not await store.unsnooze_notice(principal.user_id, digest[:64]):
                raise HTTPException(status_code=404, detail="no such snooze")
            await audit_row(
                request, principal, "notice.unsnooze", "ok", object_type="notice", object_id=digest
            )
        return {"status": "restored"}

    @route.post("/api/alarms/{aid}/outlived/ack")
    async def acknowledge_outlived(
        aid: int, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, str]:
        scope = await scope_for(principal)
        async with store.lock:
            exists, ne_id = await store.alarm_ne(aid)
        # Nonexistent and out of scope take the same branch (DECISIONS #60).
        if not exists or not scope.allows_ne(ne_id):
            await audit_scope_denial(request, principal, "alarm.outlived.ack", "alarm", str(aid))
            raise HTTPException(status_code=404, detail="no such alarm")
        async with write_txn():
            if not await store.acknowledge_outlived(aid, principal.actor, time.time()):
                raise HTTPException(status_code=409, detail="that alarm carries no such marker")
            await audit_row(
                request,
                principal,
                "alarm.outlived.ack",
                "ok",
                object_type="alarm",
                object_id=str(aid),
            )
        return {"status": "acknowledged"}
