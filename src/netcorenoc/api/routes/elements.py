"""`GET /api/elements/{ne_id}` — one element, as the graph's selection panel needs it (v0.22.0).

Clicking a host on the network graph did nothing (item 11). The panel it now opens needs four
facts about one element that no single read served: its name and address, the organization it
belongs to, **its active alarms by severity**, and whether planned work is in force on it. Its
situations and recent traps come from the routes that already answer them, narrowed by `ne_id` in
SQL (`/api/situations?ne_id=`, `/api/activity/groups?ne_id=`).

**No existence oracle.** An element outside the caller's scope takes the same branch, status and
body as one that does not exist (DECISIONS #60). The census is the census the Overview reads,
scoped to this one element, so a band here means what it means there.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException

from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.crosscutting import auth, shaping


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the element route on `app`."""
    store, security, scope_for = ctx.store, ctx.security, ctx.scope_for
    route = DeclaredRoutes(app)

    @route.get("/api/elements/{ne_id}")
    async def element(ne_id: int, principal: auth.Principal = Depends(security)) -> dict[str, Any]:
        """One element: identity, organization, active alarms by severity, planned work."""
        scope = await scope_for(principal)
        if not scope.allows_ne(ne_id):
            raise HTTPException(status_code=404, detail="no such NE")
        only = frozenset({ne_id})
        async with store.lock:
            ne = await store.get_ne(ne_id)
            if ne is None:
                raise HTTPException(status_code=404, detail="no such NE")
            census = await store.severity_census(only)
            markers = await store.window_markers(time.time(), only)
            org_id = (await store.ne_organizations([ne_id])).get(ne_id)
            organization = next(
                (o["name"] for o in await store.list_organizations() if o["id"] == org_id), None
            )
        body = {
            "ne_id": ne_id,
            "ip": ne["ip"],
            "label": ne.get("label"),
            "device": ne.get("label") or ne["ip"],
            "first_seen": ne["first_seen"],
            "last_seen": ne["last_seen"],
            "organization": organization,
            "active_alarms": census["active"],
            "severity": census,
            "maintenance": markers.get(ne_id),
        }
        return shaping.shape(body, principal.role)  # coarsen the address and the name below editor

    @route.get("/api/inventory")
    async def inventory(principal: auth.Principal = Depends(security)) -> dict[str, Any]:
        """Every in-scope element with its state, and the totals (v0.23.0, #395).

        The Entities screen's one read: organization, vendor, active alarms by band, components
        learned beneath each element, live situations, last trap, planned work — in a fixed number
        of grouped queries rather than one per element. Scoped in the WHERE clause.
        """
        scope = await scope_for(principal)
        async with store.lock:
            body = await store.inventory(None if scope.unrestricted else scope.ne_ids)
        return shaping.shape(body, principal.role)  # coarsen addresses and names below editor

    @route.get("/api/elements/{ne_id}/components")
    async def components(
        ne_id: int, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """The components the appliance learned beneath one element, busiest first (#395)."""
        scope = await scope_for(principal)
        if not scope.allows_ne(ne_id):
            raise HTTPException(status_code=404, detail="no such NE")
        async with store.lock:
            if await store.get_ne(ne_id) is None:
                raise HTTPException(status_code=404, detail="no such NE")
            body = await store.components(ne_id)
        return shaping.shape(body, principal.role)
