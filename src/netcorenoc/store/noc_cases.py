"""NOC cases: the case row, its queue cycles and its per-organization summaries (ADRs #459, #460).

Storage only. What a case may do is `engine/noc/cases.py`; what its clocks say is
`engine/noc/kpi.py`; who may do it is `api/routes/cases.py`. A case is created by the database,
in the transaction that opens its situation (`migrations/0034_noc_cases.sql`), never here.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from netcorenoc.store.base import StoreBase

#: The case row as every read returns it, with the situation's own facts beside it.
_CASE = (
    "c.situation_id, c.status, c.priority, c.title, c.description, c.executive_summary, "
    "c.current_analysis, c.noc_next_step, c.customer_next_step, c.next_update_at, c.owner_ref, "
    "c.owner_name, c.owner_kind, c.opened_at, c.status_at, c.cause, c.solution, c.resolution, "
    "c.resolved_at, c.resolved_by, c.alarms_cleared, c.version, c.created_at, c.updated_at, "
    "c.updated_by, c.priority_by, c.restored_at, c.agent_id, c.assigned_at, c.tier, "
    "c.escalated_at, c.with_people, "
    # The holder's photo digest, when a person holds it (ADR #470): `user:<id>` is the owner ref.
    "(SELECT a.sha256 FROM user_avatar a WHERE c.owner_ref = 'user:' || a.user_id) "
    "AS owner_avatar, "
    "(SELECT g.name FROM noc_agent g WHERE g.id = c.agent_id) AS agent_name, "
    "(SELECT g.escalate_after_s FROM noc_agent g WHERE g.id = c.agent_id) "
    "AS agent_escalate_after_s, "
    "s.status AS situation_status, s.severity, "
    "s.resolution AS situation_resolution, "
    "COALESCE(s.operator_name, s.model_name, s.derived_name) AS situation_name, "
    "(SELECT COUNT(*) FROM situation_alarm sa WHERE sa.situation_id = c.situation_id) AS members"
)
_CYCLE = (
    "id, situation_id, reason, priority, entered_at, taken_at, taken_by, documented_at, "
    "left_at, ended_at"
)
#: Fields a person or an agent writes directly. `executive_summary` per organization is
#: `noc_case_summary`; the status, owner and resolution move only through the methods below.
EDITABLE = (
    "title",
    "description",
    "executive_summary",
    "current_analysis",
    "noc_next_step",
    "customer_next_step",
    "next_update_at",
    "priority",
)
IN_SCOPE = (
    "EXISTS (SELECT 1 FROM situation_alarm sa JOIN alarm a ON a.id = sa.alarm_id "
    "WHERE sa.situation_id = c.situation_id AND a.ne_id = ANY(?))"
)


class NocCaseMixin(StoreBase):
    # -- reads ---------------------------------------------------------------------------------

    async def noc_case(self, situation_id: int) -> dict[str, Any] | None:
        cur = await self.conn.execute(
            f"SELECT {_CASE} FROM noc_case c JOIN situation s ON s.id = c.situation_id "  # nosec B608
            "WHERE c.situation_id = ?",
            (situation_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def noc_cases(
        self,
        *,
        statuses: Iterable[str] | None,
        owner_ref: str | None = None,
        ne_ids: frozenset[int] | None = None,
        limit: int = 100,
        priorities: Iterable[str] | None = None,
        tier: int | None = None,
    ) -> list[dict[str, Any]]:
        """Cases in ``statuses`` (all when ``None``), most urgent first: live before over, then by
        priority, then the longest-waiting. ``ne_ids`` is the caller's scope (``None``: all);
        ``tier`` 1-4 an agents' tier, 0 the NOC team (past the agents)."""
        where: list[str] = ["TRUE"]
        args: list[Any] = []
        for column, values in (("c.status", statuses), ("c.priority", priorities)):
            if values is not None:
                where.append(f"{column} = ANY(?)")
                args.append(sorted(values))
        if tier is not None:
            where.append("c.with_people" if tier == 0 else "NOT c.with_people AND c.tier = ?")
            args.extend([] if tier == 0 else [tier])
        if owner_ref is not None:
            where.append("c.owner_ref = ?")
            args.append(owner_ref)
        if ne_ids is not None:
            where.append(IN_SCOPE)
            args.append(sorted(ne_ids))
        cur = await self.conn.execute(
            f"SELECT {_CASE} FROM noc_case c JOIN situation s ON s.id = c.situation_id "  # nosec B608
            f"WHERE {' AND '.join(where)} ORDER BY c.status IN ('resolved', 'closed'), "
            "CASE c.priority WHEN 'critical' THEN 0 WHEN 'major' THEN 1 ELSE 2 END, "
            "c.status_at, c.situation_id LIMIT ?",
            (*args, limit),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def noc_case_counts(self, ne_ids: frozenset[int] | None = None) -> dict[str, int]:
        """How many cases are in each status, within the caller's scope."""
        where, args = ("WHERE " + IN_SCOPE, (sorted(ne_ids),)) if ne_ids is not None else ("", ())
        cur = await self.conn.execute(
            f"SELECT c.status, COUNT(*) AS n FROM noc_case c {where} "  # nosec B608
            "GROUP BY c.status ORDER BY c.status",
            args,
        )
        return {str(r["status"]): int(r["n"]) for r in await cur.fetchall()}

    async def noc_case_cycles(self, situation_id: int) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            f"SELECT {_CYCLE} FROM noc_case_cycle WHERE situation_id = ? ORDER BY id",  # nosec B608
            (situation_id,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def noc_case_cycles_since(
        self, since: float, ne_ids: frozenset[int] | None = None
    ) -> list[dict[str, Any]]:
        """Every cycle that entered the queue at or after ``since``, for the KPI summary."""
        scope, args = (" AND " + IN_SCOPE, (sorted(ne_ids),)) if ne_ids is not None else ("", ())
        cur = await self.conn.execute(
            f"SELECT {', '.join('y.' + c for c in _CYCLE.split(', '))} "  # nosec B608
            "FROM noc_case_cycle y JOIN noc_case c ON c.situation_id = y.situation_id "
            f"WHERE y.entered_at >= ?{scope} ORDER BY y.id",
            (since, *args),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def noc_case_organizations(self, situation_id: int) -> list[dict[str, Any]]:
        """The organizations whose elements are members, each with its summary if it has one."""
        cur = await self.conn.execute(
            "SELECT o.id, o.name, o.is_default, m.summary, m.updated_at, m.updated_by "
            "FROM organization o "
            "LEFT JOIN noc_case_summary m ON m.organization_id = o.id AND m.situation_id = ? "
            "WHERE m.situation_id IS NOT NULL OR o.id IN (SELECT n.organization_id "
            "FROM situation_alarm sa JOIN alarm a ON a.id = sa.alarm_id "
            "JOIN ne n ON n.id = a.ne_id WHERE sa.situation_id = ?) ORDER BY o.name, o.id",
            (situation_id, situation_id),
        )
        return [dict(r) for r in await cur.fetchall()]

    # -- the loop's questions ------------------------------------------------------------------

    async def noc_cases_in(self, status: str, limit: int = 200) -> list[int]:
        cur = await self.conn.execute(
            "SELECT situation_id FROM noc_case WHERE status = ? ORDER BY status_at, situation_id "
            "LIMIT ?",
            (status, limit),
        )
        return [int(r[0]) for r in await cur.fetchall()]

    async def noc_cases_due(self, now: float, limit: int = 200) -> list[int]:
        """Pending cases whose next update has come."""
        cur = await self.conn.execute(
            "SELECT situation_id FROM noc_case WHERE status LIKE 'pending\\_%' "
            "AND next_update_at <= ? ORDER BY next_update_at, situation_id LIMIT ?",
            (now, limit),
        )
        return [int(r[0]) for r in await cur.fetchall()]

    async def noc_cases_orphaned(self, limit: int = 200) -> list[dict[str, Any]]:
        """Live cases whose situation has resolved, with how it did."""
        cur = await self.conn.execute(
            "SELECT c.situation_id, c.status, s.resolution, s.closed_at FROM noc_case c "
            "JOIN situation s ON s.id = c.situation_id WHERE s.status = 'resolved' "
            "AND c.status NOT IN ('resolved', 'closed', 'validate') "
            "ORDER BY c.situation_id LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def noc_cases_resolved_before(self, before: float, limit: int = 200) -> list[int]:
        cur = await self.conn.execute(
            "SELECT situation_id FROM noc_case WHERE status = 'resolved' AND resolved_at < ? "
            "ORDER BY resolved_at, situation_id LIMIT ?",
            (before, limit),
        )
        return [int(r[0]) for r in await cur.fetchall()]

    # -- writes --------------------------------------------------------------------------------

    async def noc_case_set_fields(
        self, situation_id: int, fields: Mapping[str, Any], now: float, actor: str
    ) -> None:
        names = [name for name in EDITABLE if name in fields]
        if not names:
            return
        sets = ", ".join(f"{name} = ?" for name in names)
        # A priority a person or an agent sets is theirs from then on (`priority_by`, 0036).
        owned = ", priority_by = ?" if "priority" in names else ""
        await self.conn.execute(
            f"UPDATE noc_case SET {sets}{owned}, version = version + 1, updated_at = ?, "  # nosec B608
            "updated_by = ? WHERE situation_id = ?",
            (
                *(fields[name] for name in names),
                *([actor] if owned else []),
                now,
                actor,
                situation_id,
            ),
        )
        if owned:
            await self._cycle_priority(situation_id, str(fields["priority"]))

    async def noc_case_raise_priority(self, situation_id: int, priority: str, now: float) -> None:
        """The appliance's own priority change: `priority_by` stays NULL, so it keeps deciding."""
        await self.conn.execute(
            "UPDATE noc_case SET priority = ?, version = version + 1, updated_at = ?, "
            "updated_by = 'system' WHERE situation_id = ?",
            (priority, now, situation_id),
        )
        await self._cycle_priority(situation_id, priority)

    async def _cycle_priority(self, situation_id: int, priority: str) -> None:
        """The open, untaken cycle's take target follows the case (0036); a taken one keeps the
        priority it was taken under."""
        await self.conn.execute(
            "UPDATE noc_case_cycle SET priority = ? "
            "WHERE situation_id = ? AND taken_at IS NULL AND ended_at IS NULL",
            (priority, situation_id),
        )

    async def noc_case_set_summary(
        self, situation_id: int, organization_id: int, summary: str, now: float, actor: str
    ) -> None:
        """An organization's own executive summary; an empty one falls back to the case's."""
        if not summary:
            await self.conn.execute(
                "DELETE FROM noc_case_summary WHERE situation_id = ? AND organization_id = ?",
                (situation_id, organization_id),
            )
            return
        await self.conn.execute(
            "INSERT INTO noc_case_summary (situation_id, organization_id, summary, updated_at, "
            "updated_by) VALUES (?, ?, ?, ?, ?) ON CONFLICT (situation_id, organization_id) "
            "DO UPDATE SET summary = EXCLUDED.summary, updated_at = EXCLUDED.updated_at, "
            "updated_by = EXCLUDED.updated_by",
            (situation_id, organization_id, summary, now, actor),
        )

    async def noc_case_move(
        self,
        situation_id: int,
        status: str,
        now: float,
        actor: str,
        *,
        owner: tuple[str, str, str] | None = None,
        keep_owner: bool = True,
    ) -> None:
        """Set the status. ``owner`` (ref, name, kind) takes it; ``keep_owner=False`` clears it."""
        if owner is not None:
            await self.conn.execute(
                "UPDATE noc_case SET status = ?, status_at = ?, owner_ref = ?, owner_name = ?, "
                "owner_kind = ?, version = version + 1, updated_at = ?, updated_by = ? "
                "WHERE situation_id = ?",
                (status, now, *owner, now, actor, situation_id),
            )
            return
        clear = "" if keep_owner else "owner_ref = NULL, owner_name = NULL, owner_kind = NULL, "
        await self.conn.execute(
            f"UPDATE noc_case SET status = ?, status_at = ?, {clear}"  # nosec B608 - literal
            "version = version + 1, updated_at = ?, updated_by = ? WHERE situation_id = ?",
            (status, now, now, actor, situation_id),
        )

    async def noc_case_enqueue(self, situation_id: int, reason: str, now: float) -> str:
        """Into the NOC queue: unowned, at the case's priority (`engine/noc/case_ops.py` derives it
        first), and a new cycle for the take clock. ``reason == 'opened'`` counts from when it
        opened."""
        cur = await self.conn.execute(
            "UPDATE noc_case SET status = 'queue', status_at = ?, "
            "owner_ref = NULL, owner_name = NULL, owner_kind = NULL, version = version + 1, "
            "updated_at = ?, updated_by = 'system' "
            "WHERE situation_id = ? RETURNING priority, opened_at",
            (now, now, situation_id),
        )
        row = await cur.fetchone()
        assert row is not None
        entered = float(row["opened_at"]) if reason == "opened" else now
        await self.conn.execute(
            "INSERT INTO noc_case_cycle (situation_id, reason, priority, entered_at) "
            "VALUES (?, ?, ?, ?)",
            (situation_id, reason, row["priority"], entered),
        )
        return str(row["priority"])

    async def _latest_cycle(self, situation_id: int, condition: str) -> int | None:
        cur = await self.conn.execute(
            f"SELECT id FROM noc_case_cycle WHERE situation_id = ? AND {condition} "  # nosec B608
            "ORDER BY id DESC LIMIT 1",
            (situation_id,),
        )
        row = await cur.fetchone()
        return int(row[0]) if row else None

    async def noc_cycle_taken(self, situation_id: int, now: float, actor: str) -> None:
        """The first take of the open cycle stops its take clock; a later take changes nothing."""
        cycle = await self._latest_cycle(situation_id, "taken_at IS NULL AND ended_at IS NULL")
        if cycle is not None:
            await self.conn.execute(
                "UPDATE noc_case_cycle SET taken_at = ?, taken_by = ? WHERE id = ?",
                (now, actor, cycle),
            )

    async def noc_cycle_documented(self, situation_id: int, now: float) -> None:
        cycle = await self._latest_cycle(
            situation_id, "taken_at IS NOT NULL AND documented_at IS NULL AND left_at IS NULL"
        )
        if cycle is not None:
            await self.conn.execute(
                "UPDATE noc_case_cycle SET documented_at = ? WHERE id = ?", (now, cycle)
            )

    async def noc_cycle_left(self, situation_id: int, now: float) -> None:
        cycle = await self._latest_cycle(situation_id, "taken_at IS NOT NULL AND left_at IS NULL")
        if cycle is not None:
            await self.conn.execute(
                "UPDATE noc_case_cycle SET left_at = ? WHERE id = ?", (now, cycle)
            )

    async def noc_cycle_withdrawn(self, situation_id: int, now: float) -> None:
        cycle = await self._latest_cycle(situation_id, "taken_at IS NULL AND ended_at IS NULL")
        if cycle is not None:
            await self.conn.execute(
                "UPDATE noc_case_cycle SET ended_at = ? WHERE id = ?", (now, cycle)
            )

    async def noc_case_resolve(
        self,
        situation_id: int,
        now: float,
        actor: str,
        *,
        resolution: str,
        cause: str | None,
        solution: str,
        alarms_cleared: bool,
    ) -> None:
        await self.conn.execute(
            "UPDATE noc_case SET status = 'resolved', status_at = ?, resolution = ?, cause = ?, "
            "solution = ?, alarms_cleared = ?, resolved_at = ?, resolved_by = ?, "
            "version = version + 1, updated_at = ?, updated_by = ? WHERE situation_id = ?",
            (
                now,
                resolution,
                cause,
                solution,
                alarms_cleared,
                now,
                actor,
                now,
                actor,
                situation_id,
            ),
        )

    async def noc_case_members_active(self, situation_id: int) -> list[int]:
        """The member alarms still active: what a verified resolution clears."""
        cur = await self.conn.execute(
            "SELECT a.id FROM situation_alarm sa JOIN alarm a ON a.id = sa.alarm_id "
            "WHERE sa.situation_id = ? AND a.status = 'active' ORDER BY a.id",
            (situation_id,),
        )
        return [int(r[0]) for r in await cur.fetchall()]
