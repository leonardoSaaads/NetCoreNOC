"""NOC → Overview: the NOC at a glance (ADR #470).

One read, `cases.read` and scoped as the queue is: a case outside the caller's scope is in no
count, as it is in no list. What the counts mean is `engine/noc/overview.py`'s; what the console
draws from them is `ui/app/views/nocoverview.js`. An agent asks it too (`noc_overview`, an MCP
tool): how much is waiting, at its tier, before it takes the next case.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import Depends, FastAPI, Request

from netcorenoc.api import noc_gate as gate
from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.crosscutting import auth, shaping
from netcorenoc.engine.noc import overview
from netcorenoc.store.noc_vault import KEEP_CHECKS

#: The window the overview counts resolutions over, and how far ahead "due soon" looks.
RESOLVED_SINCE_S = 86400.0
DUE_SOON_S = 3600.0


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the overview route on `app`."""
    store, security, scope_for = ctx.store, ctx.security, ctx.scope_for
    route = DeclaredRoutes(app)

    @route.get("/api/noc/overview", operation_id="noc_overview")
    async def noc_overview(
        request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """The NOC now: cases in the queue, being worked and pending — each by tier, priority and
        holder; who holds what; takes running late; next updates due; resolved in the last day;
        and the equipment the NOC can no longer reach over management (`reachability`)."""
        scope = await scope_for(principal)
        ne_ids = None if scope.unrestricted else scope.ne_ids
        now = time.time()
        async with store.reading():
            census = await store.noc_case_census(ne_ids)
            takes = await store.noc_open_takes(ne_ids)
            moments = await store.noc_case_moments(
                now, now - RESOLVED_SINCE_S, now + DUE_SOON_S, ne_ids
            )
            credentials = await store.noc_list_secrets()
            # All the checks kept, not the light's five: "no answer since" must reach back to the
            # run's first failure, which five checks a few minutes apart cut short.
            checks = await store.noc_checks([int(r["id"]) for r in credentials], per=KEEP_CHECKS)
            nes = await store.list_ne()
        out = overview.summary(census, takes, moments, now)
        # Who holds what names people and agents: below editor, nobody is named (`case_view`).
        named = shaping.sees_people(principal.role)
        if not named:
            out.update(agents=[], people=[])
        return {
            **out,
            "reachability": await _reach(request, principal, scope, credentials, checks, nes),
            "named": named,
            "resolved_since_s": RESOLVED_SINCE_S,
            "due_soon_s": DUE_SOON_S,
            "scoped": not scope.unrestricted,
        }

    async def _reach(
        request: Request,
        principal: auth.Principal,
        scope: Any,
        credentials: list[dict[str, Any]],
        checks: dict[int, list[dict[str, Any]]],
        nes: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """`overview.reachability`, as this reader may see it: the elements in its scope (a host
        no element has is an unrestricted reader's only); an address only to a role that sees
        addresses (a network otherwise, and no detail that could carry one); and the credential to
        open only for a custodian — anyone else is told the element, not the vault's row."""
        elements = {str(n["ip"]): {"id": int(n["id"]), "label": n["label"]} for n in nes}
        if not scope.unrestricted:
            mine = {ip for ip, e in elements.items() if e["id"] in scope.ne_ids}
            credentials = [c for c in credentials if str(c["host"]) in mine]
        reach = overview.reachability(credentials, checks, elements)
        raw = shaping.sees_raw_addresses(principal.role)
        custodian = await gate.is_custodian(ctx, request, principal)
        for entry in reach["trouble"]:
            if not raw:
                # A name an operator gave may itself hold an address: the composite rule's.
                named = shaping.shape({"label": entry["element"]}, principal.role)["label"]
                entry.update(
                    host=shaping.coarsen_ip(entry["host"]), port=None, reason=None, element=named
                )
            if not custodian:
                entry["secret_id"] = None
        return reach
