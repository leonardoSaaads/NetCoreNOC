"""How the NOC's cases stand right now, counted for its overview (ADR #470).

Storage only: three counted reads over the live cases (every status but `resolved` and `closed`),
each within the caller's scope (``ne_ids``; ``None`` is the whole estate). What they add up to —
queue, working, pending; per tier, per priority, per holder; the takes running late — is
`engine/noc/overview.py`'s.
"""

from __future__ import annotations

from typing import Any

from netcorenoc.store.base import StoreBase
from netcorenoc.store.noc_cases import IN_SCOPE

_LIVE = "c.status NOT IN ('resolved', 'closed')"


def _scoped(ne_ids: frozenset[int] | None) -> tuple[str, tuple[Any, ...]]:
    return ("", ()) if ne_ids is None else (" AND " + IN_SCOPE, (sorted(ne_ids),))


class NocOverviewMixin(StoreBase):
    async def noc_case_census(self, ne_ids: frozenset[int] | None = None) -> list[dict[str, Any]]:
        """The live cases counted by status, priority, tier and holder: a person who took it, the
        agent it is routed to (or that took it), or nobody."""
        scope, args = _scoped(ne_ids)
        cur = await self.conn.execute(
            "SELECT c.status, c.priority, c.tier, c.with_people, "  # nosec B608 - literals
            "CASE WHEN c.owner_kind = 'person' THEN 'person' "
            "WHEN c.agent_id IS NOT NULL OR c.owner_kind = 'agent' THEN 'agent' "
            "ELSE 'none' END AS holder, c.agent_id, g.name AS agent_name, "
            "CASE WHEN c.owner_kind = 'person' THEN c.owner_ref END AS person_ref, "
            "CASE WHEN c.owner_kind = 'person' THEN c.owner_name END AS person_name, "
            "ua.sha256 AS person_avatar, COUNT(*) AS n FROM noc_case c "
            "LEFT JOIN noc_agent g ON g.id = c.agent_id LEFT JOIN user_avatar ua "
            "ON c.owner_kind = 'person' AND c.owner_ref = 'user:' || ua.user_id "
            f"WHERE {_LIVE}{scope} GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10 ORDER BY 1, 2, 3",
            args,
        )
        return [dict(r) for r in await cur.fetchall()]

    async def noc_open_takes(self, ne_ids: frozenset[int] | None = None) -> list[dict[str, Any]]:
        """The queue cycles nobody has taken yet, of live cases: each one's take clock running."""
        scope, args = _scoped(ne_ids)
        cur = await self.conn.execute(
            "SELECT y.situation_id, y.priority, y.entered_at FROM noc_case_cycle y "  # nosec B608
            "JOIN noc_case c ON c.situation_id = y.situation_id "
            f"WHERE y.taken_at IS NULL AND y.ended_at IS NULL AND {_LIVE}{scope} ORDER BY y.id",
            args,
        )
        return [dict(r) for r in await cur.fetchall()]

    async def noc_case_moments(
        self, now: float, since: float, soon: float, ne_ids: frozenset[int] | None = None
    ) -> dict[str, int]:
        """Next updates overdue and due before ``soon``; and the cases resolved since ``since``,
        with how many of them an agent resolved and how many resolved without the NOC."""
        scope, args = _scoped(ne_ids)
        cur = await self.conn.execute(
            "SELECT COUNT(*) FILTER (WHERE "  # nosec B608 - literals and placeholders
            f"{_LIVE} AND c.next_update_at <= ?) AS overdue, "
            f"COUNT(*) FILTER (WHERE {_LIVE} AND c.next_update_at > ? "
            "AND c.next_update_at <= ?) AS due_soon, "
            "COUNT(*) FILTER (WHERE c.resolved_at >= ?) AS resolved, "
            "COUNT(*) FILTER (WHERE c.resolved_at >= ? AND c.resolved_by LIKE 'token:%') "
            "AS resolved_by_agents, "
            "COUNT(*) FILTER (WHERE c.resolved_at >= ? AND c.resolution <> 'noc') "
            "AS resolved_without_noc "
            f"FROM noc_case c WHERE TRUE{scope}",
            (now, now, soon, since, since, since, *args),
        )
        row = await cur.fetchone()
        return {k: int(v or 0) for k, v in dict(row).items()} if row else {}
