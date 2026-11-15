"""Which situations this one most likely belongs with — the merge picker's list (ADR #489).

An operator merging a situation chooses **New** or **Open** first, and then picks from the
situations of that status ranked by how likely they are to be the same incident. The ranking is
three facts the appliance already holds, in this order:

1. **devices in common** — elements with a member in both situations;
2. **the same customer** — an element of one in an organization of the other (never the default
   organization, which every element is in until someone places it: that is no shared fact);
3. **how close they started** — the gap between the two situations' creation times.

No model is consulted. The link scorer answers *"do these two alarms belong together?"*; asking it
for every pair across two bags on every click would be the expensive half of correlation run on
the operator's time, and the three facts above are what an operator checks by eye anyway.

**Scoped like the list it narrows.** The candidates are `store.list_situations` with this
principal's element set, the counts are of visible members, and a candidate is projected exactly
as the list projects it — so the picker can never name a situation the list would not show. A
situation out of scope answers 404 like one that does not exist (DECISIONS #60).
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException

from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.crosscutting import auth, shaping

#: How many situations of the asked status are read and ranked. A merge picker that has to page is
#: a search problem, and the list beside it searches; the ranking only has to order the recent ones.
CANDIDATES = 200
#: How many the picker shows.
SHOWN = 12


def rank(
    subject: dict[str, Any],
    rows: list[dict[str, Any]],
    nes: dict[int, set[int]],
    orgs: dict[int, int],
) -> list[dict[str, Any]]:
    """The rows, most likely first, each with the facts its rank came from."""
    mine = nes.get(int(subject["id"]), set())
    my_orgs = {orgs[n] for n in mine if n in orgs}
    started = float(subject.get("created_at") or 0.0)
    ranked = []
    for row in rows:
        theirs = nes.get(int(row["id"]), set())
        shared = len(mine & theirs)
        same_customer = bool(my_orgs & {orgs[n] for n in theirs if n in orgs})
        gap = abs(float(row.get("created_at") or 0.0) - started)
        ranked.append(
            {**row, "shared_devices": shared, "same_customer": same_customer, "gap_s": round(gap)}
        )
    ranked.sort(key=lambda r: (-r["shared_devices"], not r["same_customer"], r["gap_s"]))
    return ranked


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the merge picker's read."""
    store, security, scope_for = ctx.store, ctx.security, ctx.scope_for
    situation_in_scope = ctx.perimeter.situation_in_scope
    route = DeclaredRoutes(app)

    @route.get("/api/situations/{sid}/related")
    async def related_situations(
        sid: int,
        status: Literal["new", "open"] = "new",
        principal: auth.Principal = Depends(security),
    ) -> list[dict[str, Any]]:
        """Situations of `status` this one most likely belongs with, most likely first."""
        scope = await scope_for(principal)
        if not await situation_in_scope(sid, scope):
            raise HTTPException(status_code=404, detail="no such situation")
        wanted = None if scope.unrestricted else scope.ne_ids
        async with store.reading():
            subject = await store.situation_detail(sid)
            if subject is None:
                raise HTTPException(status_code=404, detail="no such situation")
            rows = [
                r
                for r in await store.list_situations(status, CANDIDATES, wanted)
                if int(r["id"]) != sid
            ]
            rows = await store.with_roots(rows, wanted)
            members = await store.situation_member_nes([sid, *(int(r["id"]) for r in rows)])
            every_ne = {n for ids in members.values() for n in ids if n is not None}
            default = await store.default_organization_id()
            orgs = {
                ne: org
                for ne, org in (await store.ne_organizations(sorted(every_ne))).items()
                if org != default
            }
        visible = (lambda n: True) if scope.unrestricted else scope.allows_ne
        nes = {int(k): {n for n in v if n is not None and visible(n)} for k, v in members.items()}
        if not scope.unrestricted:
            projected = [
                shaping.project_situation_row(r, members.get(int(r["id"]), []), scope) for r in rows
            ]
            rows = [r for r in projected if r is not None]
        # A situation with no member left (merged away, emptied) is nothing to merge with.
        rows = [r for r in rows if int(r.get("alarm_count") or 0) > 0]
        ranked = rank({"id": sid, "created_at": subject.get("created_at")}, rows, nes, orgs)
        return shaping.shape(ranked[:SHOWN], principal.role)
