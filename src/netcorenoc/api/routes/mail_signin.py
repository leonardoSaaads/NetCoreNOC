"""Sign a mailbox in with Microsoft: start the sign-in, then ask whether it was approved (ADR #510).

Used by Settings → Email (the appliance's own mailbox) and by a customer's Email (the mailbox its
cases write from). Who may start one is whoever may set either up — an admin of the appliance's
settings, or a vault custodian — and the sign-in then belongs to them alone (`signin_flows`).
Starting or polling stores nothing: the tokens reach storage only when the settings that use them
are saved, by the route that saves them, which is where the audit row is written.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from netcorenoc.api import noc_gate as gate
from netcorenoc.api import signin_flows
from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.crosscutting import auth, mailauth


class SignInStartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_id: str = Field(min_length=36, max_length=36)
    tenant: str = Field(default="consumers", min_length=1, max_length=64)


class SignInPollIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flow: str = Field(min_length=10, max_length=64)


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the Microsoft sign-in routes on `app`."""
    security = ctx.security
    route = DeclaredRoutes(app)

    async def may(request: Request, principal: auth.Principal) -> str:
        """Whoever may set a mailbox up: the appliance's settings, or a customer's (custodian)."""
        if "config.write" in request.state.capabilities:
            return str(principal.ref)
        if await gate.is_custodian(ctx, request, principal):
            return str(principal.ref)
        raise HTTPException(status_code=403, detail=gate.NOT_CUSTODIAN)

    @route.post("/api/mail/oauth/start")
    async def start_signin(
        body: SignInStartIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """Ask Microsoft for a code the mailbox's owner enters at microsoft.com/devicelogin."""
        owner = await may(request, principal)
        try:
            flow = await asyncio.to_thread(mailauth.start, body.client_id, body.tenant)
        except mailauth.AuthError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        signin_flows.hold(owner, flow)
        return flow.public()

    @route.post("/api/mail/oauth/poll")
    async def poll_signin(
        body: SignInPollIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """Whether the owner approved: `waiting`, or `signed_in` with the account it names."""
        held = signin_flows.mine(await may(request, principal), body.flow)
        if held.tokens is not None:
            return {"status": "signed_in", "account": held.tokens.account}
        try:
            tokens = await asyncio.to_thread(mailauth.poll, held.flow)
        except mailauth.AuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if tokens is None:
            return {"status": "waiting", "interval": held.flow.interval}
        held.tokens, held.done_at = tokens, time.time()
        return {"status": "signed_in", "account": tokens.account}
