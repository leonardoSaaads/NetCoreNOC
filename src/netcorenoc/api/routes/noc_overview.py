"""NOC → Overview: the NOC at a glance (ADR #470).

One read, `cases.read` and scoped as the queue is: a case outside the caller's scope is in no
count, as it is in no list. What the counts mean is `engine/noc/overview.py`'s; what the console
draws from them is `ui/app/views/nocoverview.js`. An agent asks it too (`noc_overview`, an MCP
tool): how much is waiting, at its tier, before it takes the next case.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import Depends, FastAPI

from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.crosscutting import auth, shaping
from netcorenoc.engine.noc import overview

#: The window the overview counts resolutions over, and how far ahead "due soon" looks.
RESOLVED_SINCE_S = 86400.0
DUE_SOON_S = 3600.0


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the overview route on `app`."""
    store, security, scope_for = ctx.store, ctx.security, ctx.scope_for
    route = DeclaredRoutes(app)

    @route.get("/api/noc/overview", operation_id="noc_overview")
    async def noc_overview(principal: auth.Principal = Depends(security)) -> dict[str, Any]:
        """The NOC now: cases in the queue, being worked and pending — each by tier, priority and
        holder; who holds what; takes running late; next updates due; resolved in the last day."""
        scope = await scope_for(principal)
        ne_ids = None if scope.unrestricted else scope.ne_ids
        now = time.time()
        async with store.reading():
            census = await store.noc_case_census(ne_ids)
            takes = await store.noc_open_takes(ne_ids)
            moments = await store.noc_case_moments(
                now, now - RESOLVED_SINCE_S, now + DUE_SOON_S, ne_ids
            )
        out = overview.summary(census, takes, moments, now)
        # Who holds what names people and agents: below editor, nobody is named (`case_view`).
        named = shaping.sees_people(principal.role)
        if not named:
            out.update(agents=[], people=[])
        return {
            **out,
            "named": named,
            "resolved_since_s": RESOLVED_SINCE_S,
            "due_soon_s": DUE_SOON_S,
            "scoped": not scope.unrestricted,
        }
