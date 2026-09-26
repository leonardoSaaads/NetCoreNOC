"""`/api/activity/*` — alarm activity over a window the SQL applies (v0.22.0, F155, ADR #381).

Three reads for the Timeline and the Overview, each bounded **by the window** and never by a row
count that could truncate it — the defect `/api/timeline?limit=` had under a chart labelled with
hours. Every one is scoped through the same predicate the marks go through (F35, F38), and every
filter is a WHERE clause: an element outside the caller's scope answers the same empty result an
element that does not exist answers.
"""

from __future__ import annotations

import time
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException

from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.crosscutting import auth, shaping
from netcorenoc.store.active import ALL_BANDS
from netcorenoc.store.narrow import OID, Narrow

#: The longest window any of these answers: the operational retention's default, so a longer one
#: could only be a request for rows the appliance has deleted.
MAX_RANGE_S = 30 * 24 * 60 * 60.0
#: The same ceiling the bucketed timeline has always had.
MAX_BUCKETS = 240
#: A page of the grouped list. Fifty bursts is a screen; more is a scroll nobody finishes.
MAX_PAGE = 200
#: Lanes drawn before the rest become one "everything else" lane.
MAX_LANES = 12


def _window(range_s: float) -> tuple[float, float]:
    now = time.time()
    return now - min(max(60.0, float(range_s)), MAX_RANGE_S), now


#: The most elements the Top list answers for.
MAX_TOP = 25


def _narrow(organization_id: int | None, oid: str | None, sid: int | None) -> Narrow:
    """The optional filters, validated before they reach SQL (#392)."""
    cleaned = oid.strip().lstrip(".") if oid else None
    if cleaned and not OID.match(cleaned):
        raise HTTPException(
            status_code=422, detail="oid: must be dotted numbers, like 1.3.6.1.4.1.2011"
        )
    return Narrow(organization_id=organization_id, oid=cleaned or None, situation_id=sid)


def _bands(raw: str) -> tuple[str, ...]:
    chosen = tuple(part for part in (p.strip() for p in raw.split(",")) if part)
    unknown = [band for band in chosen if band not in ALL_BANDS]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"bands: {', '.join(unknown)} — use any of {', '.join(ALL_BANDS)}",
        )
    return chosen or ALL_BANDS


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the activity routes on `app`."""
    store, security, scope_for = ctx.store, ctx.security, ctx.scope_for
    route = DeclaredRoutes(app)

    @route.get("/api/activity/severity")
    async def activity_severity(
        range_s: float = 7200.0,
        buckets: int = 24,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """Raises per bucket by severity band, `unplaced` included, over the last `range_s`."""
        scope = await scope_for(principal)
        since, until = _window(range_s)
        async with store.lock:
            return await store.activity_severity(
                since=since,
                until=until,
                buckets=min(max(int(buckets), 2), MAX_BUCKETS),
                ne_ids=None if scope.unrestricted else scope.ne_ids,
            )

    @route.get("/api/activity/lanes")
    async def activity_lanes(
        range_s: float = 3600.0,
        buckets: int = 48,
        top: int = 8,
        ne_id: int | None = None,
        organization_id: int | None = None,
        oid: str | None = None,
        sid: int | None = None,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """Raises per element per bucket for the busiest `top`, the rest as one lane."""
        narrow = _narrow(organization_id, oid, sid)
        scope = await scope_for(principal)
        since, until = _window(range_s)
        async with store.lock:
            lanes = await store.activity_lanes(
                since=since,
                until=until,
                buckets=min(max(int(buckets), 2), MAX_BUCKETS),
                top=min(max(int(top), 1), MAX_LANES),
                ne_ids=None if scope.unrestricted else scope.ne_ids,
                device_ne_id=ne_id,
                narrow=narrow,
            )
        return shaping.shape(lanes, principal.role)  # coarsen device names below editor

    @route.get("/api/activity/groups")
    async def activity_groups(
        range_s: float = 3600.0,
        ne_id: int | None = None,
        kind: Literal["raise", "clear", "both"] = "both",
        class_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
        organization_id: int | None = None,
        oid: str | None = None,
        sid: int | None = None,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """Bursts of one trap on one element (*"x14 over 2 min"*), newest first, paged."""
        narrow = _narrow(organization_id, oid, sid)
        scope = await scope_for(principal)
        since, until = _window(range_s)
        async with store.lock:
            groups = await store.activity_groups(
                since=since,
                until=until,
                ne_ids=None if scope.unrestricted else scope.ne_ids,
                device_ne_id=ne_id,
                kinds=("raise", "clear") if kind == "both" else (kind,),
                class_id=class_id,
                limit=min(max(int(limit), 1), MAX_PAGE),
                offset=max(int(offset), 0),
                narrow=narrow,
            )
        return shaping.shape(groups, principal.role)  # coarsen device and class text

    @route.get("/api/activity/active")
    async def activity_active(
        range_s: float = 7200.0,
        buckets: int = 24,
        organization_id: int | None = None,
        oid: str | None = None,
        ne_id: int | None = None,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """Active alarms at each bucket's end by severity band — the stock, not the flow (#393)."""
        narrow = _narrow(organization_id, oid, None)
        scope = await scope_for(principal)
        since, until = _window(range_s)
        async with store.lock:
            return await store.activity_active(
                since=since,
                until=until,
                buckets=min(max(int(buckets), 2), MAX_BUCKETS),
                ne_ids=None if scope.unrestricted else scope.ne_ids,
                narrow=narrow,
                device_ne_id=ne_id,
            )

    @route.get("/api/activity/top")
    async def activity_top(
        range_s: float = 7200.0,
        buckets: int = 24,
        bands: str = "",
        limit: int = 10,
        organization_id: int | None = None,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """The elements with the most alarms active now in `bands` (every band when none is named),
        each with its trend (#393)."""
        chosen = _bands(bands)
        narrow = _narrow(organization_id, None, None)
        scope = await scope_for(principal)
        since, until = _window(range_s)
        async with store.lock:
            top = await store.activity_top(
                since=since,
                until=until,
                buckets=min(max(int(buckets), 2), MAX_BUCKETS),
                ne_ids=None if scope.unrestricted else scope.ne_ids,
                bands=chosen,
                limit=min(max(int(limit), 1), MAX_TOP),
                narrow=narrow,
            )
        return shaping.shape(top, principal.role)  # coarsen device names below editor
