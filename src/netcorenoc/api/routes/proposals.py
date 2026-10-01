"""An operator's answer to a pending proposal (v0.27.0, ADR #428).

``POST /api/situations/{sid}/proposal`` with ``{"decision": "accept" | "reject", "confidence"}``
on a **pending** situation — one the model opened because it would otherwise have grown a situation
an operator had confirmed.

* **accept** — the pending bag merges into its target, which stays `open`. It is the operator's
  `merge` gesture exactly (`store.operator_merge`, a `merge` event with both snapshots), so its
  cross pairs become **positive** labels by the path every merge already feeds.
* **reject** — the pending bag returns to `new`, a situation of its own that nobody has looked at,
  and the engine never proposes that bag to that target again. Its cross pairs become **negative**
  labels (`store.proposal_negative_pairs`).

Both write a `proposal_decision` row with the two bags as they stood, so each model's acceptance
rate — the fast loop's report card — is a query. The capability and scope are the merge gesture's:
an answer names two situations, and each is checked against the caller's scope, with "out of scope"
and "no such thing" one code path (DECISIONS #60, #65). The order is the one every gesture writes
in: snapshot, mutate, event, audit — in one transaction.
"""

from __future__ import annotations

import time

from fastapi import Depends, FastAPI, HTTPException, Request

from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.api.models import ProposalIn
from netcorenoc.crosscutting import auth
from netcorenoc.engine.dataset import gestures
from netcorenoc.engine.model import confidence as confidence_rules
from netcorenoc.engine.operate import membership

__all__ = ["register"]


def register(app: FastAPI, ctx: AppContext) -> None:
    store, engine, security, scope_for = ctx.store, ctx.engine, ctx.security, ctx.scope_for
    audit_row, write_txn = ctx.perimeter.audit_row, ctx.write_txn
    situation_in_scope = ctx.perimeter.situation_in_scope
    audit_scope_denial = ctx.perimeter.audit_scope_denial
    route = DeclaredRoutes(app)

    @route.post("/api/situations/{sid}/proposal")
    async def answer_proposal(
        sid: int, body: ProposalIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, object]:
        """Accept or reject a pending proposal. See the module docstring."""
        scope = await scope_for(principal)
        async with store.lock:
            head = await store.proposal_of(sid)
        target = None if head is None else head.get("proposed_into")
        for named in (sid, target):
            if named is None or not await situation_in_scope(int(named), scope):
                await audit_scope_denial(
                    request, principal, "situation.proposal", "situation", str(sid)
                )
                raise HTTPException(status_code=404, detail="no such situation")
        assert target is not None
        target = int(target)
        now = time.time()
        async with write_txn():
            head = await store.proposal_of(sid)
            if head is None or head["status"] != "pending" or head["proposed_into"] != target:
                raise HTTPException(
                    status_code=409,
                    detail="that proposal has already been answered; reload the card",
                )
            states = await store.lifecycle_states([target])
            if states.get(target, ("resolved", None))[0] != "open":
                raise HTTPException(
                    status_code=409, detail="the situation it proposed to join is no longer open"
                )
            pending = await gestures.snapshot(store, sid)
            joined = await gestures.snapshot(store, target)
            trains = confidence_rules.admits(body.confidence)
            decision_id = await store.record_proposal_decision(
                pending=sid,
                target=target,
                decision=body.decision,
                actor=principal.ref or principal.actor,
                role=principal.role,
                at=now,
                confidence=body.confidence,
                proposal_confidence=head.get("proposal_confidence"),
                decider=head.get("decider"),
                pending_members=list(pending.alarm_ids),
                target_members=list(joined.alarm_ids),
                produces_training_rows=trains,
            )
            if body.decision == "accept":
                await store.operator_merge(target, sid, now)
                membership.merged(engine, target, sid)
                await gestures.record(
                    store,
                    gestures.Gesture(
                        kind="merge",
                        situation_id=target,
                        at=now,
                        actor=principal.ref,
                        role=principal.role,
                        confidence=body.confidence,
                        peer_situation_id=sid,
                    ),
                    joined,
                    pending,
                )
            else:
                await store.withdraw_proposal(sid, now)
                engine.rejected_proposals.add((sid, target))
            await audit_row(
                request,
                principal,
                f"situation.proposal.{body.decision}",
                "ok",
                object_type="situation",
                object_id=str(sid),
                details={
                    "into": target,
                    "decision_id": decision_id,
                    "members": len(pending.alarm_ids),
                },
            )
        return {"status": "accepted" if body.decision == "accept" else "rejected", "into": target}
