"""Two readable resources a maintenance window is written against: organizations and time zones.

Both exist because **the form has to offer them and an agent has to enumerate them** (Part III).
An agent asked to *"put the south tower in maintenance tonight"* needs to know which organizations
exist and which zone string this appliance will accept, and guessing either produces a 422 it
cannot correct.

`GET /api/timezones` is the one that earns its place least obviously, so: three of the cities an
operator actually names — Washington, Brasília, Beijing — **have no IANA zone of their own**. A
client that sent the label would send something `ZoneInfo` cannot load. This route is how the
console turns a city into the canonical zone that carries the rule, and it lists what **this
host** can resolve rather than what the shipped list believes it can.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request

from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.api.models import NeOrganizationIn, OrganizationIn
from netcorenoc.crosscutting import auth, shaping
from netcorenoc.crosscutting.shaping.timezones import SEARCH_LIMIT


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the organization and time-zone routes on `app`."""
    store = ctx.store
    security = ctx.security
    audit_row, write_txn = ctx.perimeter.audit_row, ctx.write_txn
    route = DeclaredRoutes(app)

    @route.get("/api/organizations")
    async def list_organizations(
        request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """Every organization, with how many elements it holds.

        ⚠ **Attribution, not isolation.** This route answers *"whose equipment is this?"* and no
        authorization decision anywhere reads its answer. `crosscutting/shaping/scope.py` is what
        decides which elements a principal may see, and the two are separate mechanisms on
        purpose (ADR #367) — an organization name is admin-written, and F35's rule is that no
        input to the scope resolver may be writable by the party being scoped.

        `viewer`, because an operator reading a maintenance window's card sees the organization on
        it and a screen that showed a name nobody could look up would be a dead end.
        """
        return {
            "organizations": await store.list_organizations(),
            "default_organization_id": await store.default_organization_id(),
            # Said in the API and not only in the documentation, because this is the field an
            # agent would otherwise be entitled to read as a security boundary.
            "isolation": False,
            "note": (
                "An organization is attribution: which provider an element or a window belongs "
                "to. It is NOT tenant isolation. Correlation still learns across every network "
                "element and a situation may still form across an organization boundary."
            ),
        }

    @route.post("/api/organizations")
    async def create_organization(
        body: OrganizationIn,
        request: Request,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """Add an organization. Admin, because it is inventory structure rather than operation."""
        now = time.time()
        async with write_txn():
            existing = {o["slug"] for o in await store.list_organizations()}
            if body.slug in existing:
                raise HTTPException(
                    status_code=409, detail=f"an organization with slug {body.slug!r} exists"
                )
            organization_id = await store.create_organization(body.name, body.slug, now)
            await audit_row(
                request,
                principal,
                "config.change",
                "ok",
                object_type="organization",
                object_id=str(organization_id),
                details={"slug": body.slug},
            )
        return {"created": True, "id": organization_id}

    @route.post("/api/entities/{ne_id}/organization")
    async def set_ne_organization(
        ne_id: int,
        body: NeOrganizationIn,
        request: Request,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """Move one element to an organization. **Without this, D1 is a column nobody can set.**

        Every element joins the seeded default on discovery (`attribute_unassigned_nes`), so an
        appliance that never called this route still has a consistent estate — but an operator who
        creates a second organization needs a way to put something in it, and a feature whose only
        reachable state is its default is not a feature.

        `organizations.write`, so **admin** — the same rank as creating the organization, because
        this is inventory structure rather than operation. It is deliberately *not* `label.write`:
        a rename is an operator's description of a thing, and this is a statement about who owns
        it.

        ⚠ Still attribution. Moving an element changes which windows and lists name it. It does
        **not** change what any principal may see, because no authorization decision reads the
        column (ADR #366).
        """
        async with write_txn():
            organizations = await store.list_organizations()
            if body.organization_id not in {int(o["id"]) for o in organizations}:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"organization_id {body.organization_id} does not exist; "
                        "GET /api/organizations lists the ones that do"
                    ),
                )
            if not any(int(n["id"]) == ne_id for n in await store.list_ne()):
                # 404 and the same phrase `/api/entities/{ne_id}/reset` uses, so an unknown element
                # answers identically whichever admin route asked.
                raise HTTPException(status_code=404, detail="no such NE")
            await store.set_ne_organization(ne_id, body.organization_id)
            await audit_row(
                request,
                principal,
                "config.change",
                "ok",
                object_type="ne",
                object_id=str(ne_id),
                details={"organization_id": body.organization_id},
            )
        return {"updated": True, "ne_id": ne_id, "organization_id": body.organization_id}

    @route.get("/api/timezones")
    async def list_timezones(
        request: Request,
        q: str = Query(default="", max_length=120),
        limit: int = Query(default=SEARCH_LIMIT, ge=1, le=50),
        at: float | None = Query(default=None),
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """Zones this appliance can resolve — **curated cities first, then everything else** (D2).

        An empty `q` returns the head of the curated list, which is what the console shows before
        anybody types. A query matches a city label or a zone identifier.

        Every row carries the **offset at an instant**, never a stored one: a window across a DST
        transition has two offsets, and a client that cached one would render the second half of
        the window an hour wrong. `at` is that instant — the console sends the window's own start,
        so the offset it stamps onto a `datetime-local` value is the one in force *then* rather
        than the one in force while the operator is typing. It defaults to now.

        **The response carries no clock.** An earlier draft returned each zone's current local
        time, which made the body change every second — and `tests/test_declaration.py` is right
        to compare an unscoped route's two answers byte for byte, so a field that moves on its own
        would have had to weaken that guard to keep a nicety. The offset is what a client needs
        and it moves twice a year.
        """
        moment = at if at is not None else time.time()
        zones = shaping.search_zones(q, limit=limit)
        return {
            "zones": [
                {
                    "label": zone.label,
                    "zone": zone.zone,
                    "country": zone.country,
                    "curated": zone.curated,
                    "offset": shaping.offset_label(zone.zone, moment),
                }
                for zone in zones
            ],
            "default": shaping.DEFAULT_ZONE,
            # What the appliance could NOT resolve, so an operator learns it here rather than from
            # a window that schedules at the wrong hour. Empty on a healthy install.
            "warnings": shaping.timezone_selfcheck(),
        }
