"""Who may read which routine, and how one is shown (ADRs #492, #499, #501).

Split from `routes/playbooks.py` at the 400-line guard, on the seam the routes already had: the
rules a list, a read, a run and an earlier version all answer by — the caller's tier, the
customers they serve, the case they name — are written once, here, and the routes call them.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException

from netcorenoc.api.context import AppContext
from netcorenoc.crosscutting import auth

NO_SUCH = "no such routine"


def routine_view(
    row: dict[str, Any],
    *,
    runs: list[dict[str, Any]] | None = None,
    versions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """A routine as the API shows it; with ``runs`` its tree and runs, with ``versions`` how it
    improved (ADR #501)."""
    document = json.loads(row["document"])
    out = {
        "id": row["id"],
        "name": row["name"],
        "description": document.get("description", ""),
        "match": document["match"],
        "timeout_s": document.get("timeout_s", 300),
        "steps": len(document["steps"]),
        # What a reader counts (ADR #492): the checks a routine runs, not the ends it may reach.
        "checks": sum(1 for step in document["steps"].values() if "do" in step),
        "version": row["version"],
        "enabled": row["enabled"],
        "customer_id": row.get("organization_id"),
        "customer": row.get("customer"),
        "tier": row.get("tier", 1),
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
                "started_by": r.get("started_by"),
                "trace": json.loads(r["trace"]),
            }
            for r in runs
        ]
    if versions is not None:
        # ADR #501: how the routine improved — each version, who wrote it and why, and how the runs
        # of that version ended.
        out["versions"] = [
            {k: v[k] for k in ("version", "tier", "written_at", "written_by", "note", "outcomes")}
            for v in versions
        ]
    return out


class RoutineAccess:
    """The tier and customer gates of the routine routes, over one app's store and scope."""

    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self.store = ctx.store

    async def ceiling(self, principal: auth.Principal) -> tuple[int, dict[str, Any] | None]:
        """The highest tier the caller may see, and the agent it is (ADR #492): a hired agent its
        own tier, any other token holding the agent grant the least one, a person who passed the
        lead gate every tier."""
        if not principal.is_token:
            return 4, None
        agent = None
        if principal.token_id is not None:
            async with self.store.reading():
                agent = await self.store.noc_agent_by_token(principal.token_id)
        return (int(agent["tier"]), agent) if agent else (1, None)

    @staticmethod
    def reaches(row: dict[str, Any], scope: Any) -> bool:
        """ADR #499: a customer's routine for those who serve it; a shared one for everyone."""
        return row["organization_id"] is None or scope.allows_org(row["organization_id"])

    async def visible(self, playbook_id: int, principal: auth.Principal) -> dict[str, Any]:
        """The routine, or the 404 of one that does not exist: above the caller's tier, or for a
        customer they do not serve."""
        top, _agent = await self.ceiling(principal)
        scope = await self.ctx.scope_for(principal)
        async with self.store.reading():
            row = await self.store.noc_playbook(playbook_id)
        if row is None or int(row["tier"]) > top or not self.reaches(row, scope):
            raise HTTPException(status_code=404, detail=NO_SUCH)
        return row

    @staticmethod
    def readable(version: dict[str, Any], top: int, scope: Any) -> bool:
        """ADR #501: an earlier version is read under its own tier and customer as well as the
        routine's — one moved from Claro to every customer does not show Claro's old tree."""
        return int(version["tier"]) <= top and RoutineAccess.reaches(version, scope)

    async def case_facts(self, sid: int, principal: auth.Principal) -> dict[str, Any]:
        """The case's facts, or the 404 an unknown or out-of-scope case answers alike."""
        scope = await self.ctx.scope_for(principal)
        if not await self.ctx.perimeter.situation_in_scope(sid, scope):
            raise HTTPException(status_code=404, detail="no such case")
        async with self.store.reading():
            case = await self.store.noc_case(sid)
            facts = await self.store.noc_situation_facts(sid) if case else {}
        if case is None:
            raise HTTPException(status_code=404, detail="no such case")
        return {"case": case, **facts}
