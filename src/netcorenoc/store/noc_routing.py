"""Which agent a case is with, and the tier it has climbed to (ADR #467). Storage only.

The routing and escalation rules are `engine/noc/agent_loop.py` and `engine/noc/agents.py`; these
are the rows they read and write.
"""

from __future__ import annotations

from typing import Any

from netcorenoc.store.base import StoreBase

#: Statuses in which a case is still the agents' to conclude: not waiting on someone outside the
#: NOC, not confirming a resolution, not over.
_ACTIVE = "('new', 'auto', 'queue', 'working')"


class NocRoutingMixin(StoreBase):
    async def noc_case_assign(
        self,
        situation_id: int,
        agent_id: int | None,
        now: float,
        *,
        tier: int | None = None,
        escalated: bool = False,
        with_people: bool = False,
    ) -> None:
        """Route a case to ``agent_id`` (None: to nobody). ``tier`` raises the case's tier;
        ``escalated`` stamps the climb; ``with_people`` marks it past the agents."""
        await self.conn.execute(
            "UPDATE noc_case SET agent_id = ?, assigned_at = ?, "
            "tier = GREATEST(tier, COALESCE(?, tier)), "
            "escalated_at = CASE WHEN ? THEN ? ELSE escalated_at END, with_people = ?, "
            "version = version + 1, updated_at = ? WHERE situation_id = ?",
            (agent_id, now, tier, escalated, now, with_people, now, situation_id),
        )

    async def noc_cases_unrouted(self, limit: int = 100) -> list[int]:
        """Queued cases with no agent and not yet handed to the people."""
        cur = await self.conn.execute(
            "SELECT situation_id FROM noc_case WHERE status = 'queue' AND agent_id IS NULL "
            "AND NOT with_people ORDER BY status_at, situation_id LIMIT ?",
            (limit,),
        )
        return [int(r[0]) for r in await cur.fetchall()]

    async def noc_cases_routed(self, limit: int = 500) -> list[dict[str, Any]]:
        """Cases with an agent that are still the agents' to conclude (`_ACTIVE`)."""
        cur = await self.conn.execute(
            "SELECT situation_id, agent_id, assigned_at, priority, status, tier, owner_ref "
            f"FROM noc_case WHERE agent_id IS NOT NULL AND status IN {_ACTIVE} "  # nosec B608
            "ORDER BY assigned_at, situation_id LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def noc_agent_loads(self) -> dict[int, int]:
        """Live cases per agent: what routing balances."""
        cur = await self.conn.execute(
            "SELECT agent_id, COUNT(*) FROM noc_case WHERE agent_id IS NOT NULL "
            "AND status NOT IN ('resolved', 'closed') GROUP BY agent_id"
        )
        return {int(r[0]): int(r[1]) for r in await cur.fetchall()}

    async def noc_agent_cases(self, agent_id: int, limit: int = 50) -> list[dict[str, Any]]:
        """The cases an agent has, or took, most recent first — for its page."""
        cur = await self.conn.execute(
            "SELECT c.situation_id, c.status, c.priority, c.title, c.tier, c.updated_at, "
            "COALESCE(s.operator_name, s.model_name, s.derived_name) AS situation_name "
            "FROM noc_case c JOIN situation s ON s.id = c.situation_id "
            "WHERE c.agent_id = ? OR c.situation_id IN (SELECT DISTINCT situation_id "
            "FROM noc_agent_run WHERE agent_id = ? AND situation_id IS NOT NULL) "
            "ORDER BY c.updated_at DESC LIMIT ?",
            (agent_id, agent_id, limit),
        )
        return [dict(r) for r in await cur.fetchall()]
