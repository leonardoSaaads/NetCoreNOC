"""A case's priority as the Broker shows it (ADR #464).

Split from `store/noc_cases.py` (ADR #470) when the case row grew its holder's photo: the case
loop asks these two questions — what is the worst severity the Broker shows for a situation, and
which cases may still rise — and `engine/noc/case_ops.reprioritize` decides from the answers.
"""

from __future__ import annotations

from netcorenoc.store.base import StoreBase

#: The severities a case priority is drawn from, worst first (`noc_case_severity`).
_WORST_FIRST = ("critical", "major", "minor", "warning", "indeterminate")


class NocPriorityMixin(StoreBase):
    async def noc_case_severity(self, situation_id: int) -> str | None:
        """What the Broker shows for the situation, worst first: its own severity (autonomy's —
        the worst member, one level higher when it spans many elements) and each active member's
        (all of them when none is active) with the Broker's precedence — declared, the
        catalogue's rule, then what the trap carried or was learned (`store/active.py`). None
        when nothing places any."""
        cur = await self.conn.execute(
            "SELECT a.class_id, c.oid, a.severity_rank, a.status FROM situation_alarm sa "
            "JOIN alarm a ON a.id = sa.alarm_id JOIN alarm_class c ON c.id = a.class_id "
            "WHERE sa.situation_id = ?",
            (situation_id,),
        )
        rows = await cur.fetchall()
        band = await self._band_of()  # type: ignore[attr-defined]
        live = [r for r in rows if r["status"] == "active"] or rows
        bands = {band(int(r["class_id"]), str(r["oid"]), r["severity_rank"]) for r in live}
        cur = await self.conn.execute(
            "SELECT severity FROM situation WHERE id = ?", (situation_id,)
        )
        own = await cur.fetchone()
        if own is not None and own[0] is not None:
            bands.add(str(own[0]))
        return next((word for word in _WORST_FIRST if word in bands), None)

    async def noc_cases_auto_priority(self, limit: int = 200) -> list[int]:
        """Live cases whose priority is still the appliance's and could still rise."""
        cur = await self.conn.execute(
            "SELECT situation_id FROM noc_case WHERE priority_by IS NULL "
            "AND priority <> 'critical' AND status NOT IN ('resolved', 'closed') "
            "ORDER BY situation_id LIMIT ?",
            (limit,),
        )
        return [int(r[0]) for r in await cur.fetchall()]
