"""Pending proposals (v0.27.0, migration `0027`, ADR #428). SQL only; decides nothing.

A `pending` situation holds alarms the model would have placed with an `open` one — a situation an
operator has confirmed — and **proposes** to join it. The engine decides when to propose
(`engine/operate/proposals.py`); an operator decides whether to accept. This module is the rows.

**No method here takes ``store.lock``**, which is this package's contract for callers.
"""

from __future__ import annotations

from typing import Any

from netcorenoc.store.base import StoreBase

__all__ = ["ProposalMixin"]

#: The negative half of a rejected proposal, matched against captured pairs in both orders — the
#: same two-branch union `gesture_positive_pairs` uses (F140), so an index serves each branch.
_REJECT_BRANCH = (
    "SELECT d.id AS decision_id, d.confidence, d.pending_situation_id AS incident, "
    "d.at AS label_at, p.id AS pair_id "
    "FROM proposal_decision d "
    "JOIN proposal_decision_member pm ON pm.decision_id = d.id AND pm.side = 'pending' "
    "JOIN proposal_decision_member tm ON tm.decision_id = d.id AND tm.side = 'target' "
    "JOIN dataset_pair p ON p.{first} = pm.alarm_id AND p.{second} = tm.alarm_id "
    "WHERE d.decision = 'reject' AND d.produces_training_rows = 1 AND p.lifecycle = 'dataset'"
)
_REJECT_SQL = (
    _REJECT_BRANCH.format(first="alarm_a", second="alarm_b")
    + " UNION ALL "
    + _REJECT_BRANCH.format(first="alarm_b", second="alarm_a")
    + " ORDER BY decision_id, pair_id"
)


class ProposalMixin(StoreBase):
    async def lifecycle_states(self, ids: list[int]) -> dict[int, tuple[str, int | None]]:
        """``{situation: (status, proposed_into)}`` for the named ids. Bounded by the caller."""
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        column = "proposed_into" if self._has_proposals else "NULL"
        cur = await self.conn.execute(
            f"SELECT id, status, {column} FROM situation WHERE id IN ({marks})",  # nosec B608
            tuple(ids),
        )
        return {
            int(r[0]): (str(r[1]), None if r[2] is None else int(r[2]))
            for r in await cur.fetchall()
        }

    async def pending_for(self, target: int) -> int | None:
        """The live pending situation that proposes into ``target``, if there is one."""
        if not self._has_proposals:
            return None
        cur = await self.conn.execute(
            "SELECT id FROM situation WHERE proposed_into=? AND status='pending' "
            "ORDER BY id LIMIT 1",
            (target,),
        )
        row = await cur.fetchone()
        return None if row is None else int(row[0])

    async def set_proposal(self, situation_id: int, target: int, confidence: float) -> None:
        await self.conn.execute(
            "UPDATE situation SET proposed_into=?, proposal_confidence=? WHERE id=?",
            (target, max(0.0, min(1.0, confidence)), situation_id),
        )

    async def withdraw_proposal(self, situation_id: int, ts: float) -> bool:
        """`pending` -> `new`: the proposal is void (rejected, or its target left). Idempotent."""
        cur = await self.conn.execute(
            "UPDATE situation SET status='new', proposed_into=NULL, proposal_confidence=NULL, "
            "updated_at=? WHERE id=? AND status='pending' RETURNING id",
            (ts, situation_id),
        )
        return await cur.fetchone() is not None

    async def orphan_proposals(self) -> list[int]:
        """Pending situations whose target is no longer `open` (resolved, merged away)."""
        if not self._has_proposals:
            return []
        cur = await self.conn.execute(
            "SELECT p.id FROM situation p LEFT JOIN situation t ON t.id = p.proposed_into "
            "WHERE p.status='pending' AND (t.id IS NULL OR t.status <> 'open') ORDER BY p.id"
        )
        return [int(r[0]) for r in await cur.fetchall()]

    async def proposal_of(self, situation_id: int) -> dict[str, Any] | None:
        if not self._has_proposals:
            return None
        cur = await self.conn.execute(
            "SELECT id, status, proposed_into, proposal_confidence, decider FROM situation "
            "WHERE id=?",
            (situation_id,),
        )
        row = await cur.fetchone()
        return None if row is None else dict(row)

    async def record_proposal_decision(
        self,
        *,
        pending: int,
        target: int,
        decision: str,
        actor: str,
        role: str | None,
        at: float,
        confidence: float | None,
        proposal_confidence: float | None,
        decider: str | None,
        pending_members: list[int],
        target_members: list[int],
        produces_training_rows: bool,
    ) -> int:
        cur = await self.conn.execute(
            "INSERT INTO proposal_decision (pending_situation_id, target_situation_id, decision, "
            "actor, role, at, confidence, proposal_confidence, decider, produces_training_rows) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (
                pending,
                target,
                decision,
                actor,
                role,
                at,
                confidence,
                proposal_confidence,
                decider,
                int(produces_training_rows),
            ),
        )
        row = await cur.fetchone()
        assert row is not None
        decision_id = int(row[0])
        await self.conn.executemany(
            "INSERT INTO proposal_decision_member (decision_id, side, position, alarm_id) "
            "VALUES (?, ?, ?, ?)",
            [(decision_id, "pending", i, a) for i, a in enumerate(pending_members)]
            + [(decision_id, "target", i, a) for i, a in enumerate(target_members)],
        )
        return decision_id

    async def rejected_proposals(self) -> list[tuple[int, int]]:
        """``(pending situation, target)`` for every rejection — the engine never re-proposes it."""
        if not self._has_proposals:
            return []
        cur = await self.conn.execute(
            "SELECT pending_situation_id, target_situation_id FROM proposal_decision "
            "WHERE decision='reject' ORDER BY id"
        )
        return [(int(r[0]), int(r[1])) for r in await cur.fetchall()]

    async def proposal_negative_pairs(self) -> list[dict[str, Any]]:
        """Captured pairs across a rejected proposal's two bags: **negative** labels, one bag per
        decision. Shaped like `labelled_pairs` rows so the site rows read all sources alike."""
        if not self._has_proposals or not self._has_pair_features:
            return []
        cur = await self.conn.execute(_REJECT_SQL)
        return [
            {
                "pair_id": int(r["pair_id"]),
                "verdict": "split",
                "feedback_id": int(r["decision_id"]),
                "source": "proposal",
                "confidence": r["confidence"],
                "label_at": float(r["label_at"]),
                "incident": int(r["incident"]),
            }
            for r in await cur.fetchall()
        ]

    async def proposal_stats(self) -> dict[str, dict[str, int]]:
        """Accepted and rejected proposals per proposing model: the fast loop's report card."""
        if not self._has_proposals:
            return {}
        cur = await self.conn.execute(
            "SELECT COALESCE(decider, '') AS decider, decision, COUNT(*) FROM proposal_decision "
            "GROUP BY 1, 2 ORDER BY 1, 2"
        )
        out: dict[str, dict[str, int]] = {}
        for decider, decision, n in await cur.fetchall():
            out.setdefault(str(decider), {"accept": 0, "reject": 0})[str(decision)] = int(n)
        return out
