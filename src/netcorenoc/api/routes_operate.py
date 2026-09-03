"""The write surface: the two admin resets, and the three editor-level mutations.

This is the module v0.7.1's F34 was about. The three `editor` routes below are the only mutating
routes whose capability is beneath `admin` and which name a network element; each resolves scope
through `ctx.scope_for` and denies through `ctx.perimeter.situation_in_scope` /
`scope.allows_ne`, both of which live in the perimeter precisely so a read and a write can never
disagree about what "yours" means (DECISIONS #65, #76).

No helper here re-implements a perimeter decision. `situation_in_scope` and `audit_scope_denial`
are the perimeter's bound methods, received rather than restated.
"""

from __future__ import annotations

import time

from fastapi import Depends, FastAPI, HTTPException, Request

from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.api.models import CloseIn, FeedbackIn, LabelIn
from netcorenoc.crosscutting import auth, shaping
from netcorenoc.engine.dataset import capture, gestures


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the operate routes on `app`."""
    store, engine, security, scope_for = ctx.store, ctx.engine, ctx.security, ctx.scope_for
    audit_row, write_txn = ctx.perimeter.audit_row, ctx.write_txn
    situation_in_scope = ctx.perimeter.situation_in_scope
    audit_scope_denial = ctx.perimeter.audit_scope_denial
    route = DeclaredRoutes(app)

    @route.post("/api/entities/{ne_id}/reset")
    async def reset_entity(
        ne_id: int, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, str]:
        """Admin recourse for a poisoned identity: forget the NE's learned entity/severity
        decision (history untouched). The next sweep re-decides from current evidence."""
        async with write_txn():
            if not any(int(n["id"]) == ne_id for n in await store.list_ne()):
                raise HTTPException(status_code=404, detail="no such NE")
            await engine.reset_entity(ne_id, time.time())
            await audit_row(
                request, principal, "entity.reset", "ok", object_type="ne", object_id=str(ne_id)
            )
        return {"status": "entity decision reset"}

    @route.post("/api/profiles/{ne_id}/reset")
    async def reset_profile(
        ne_id: int, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, str]:
        """Wipe the NE's profiler evidence as well, so identity and severity re-measure from
        scratch — the recovery when the accumulated evidence itself is poisoned."""
        async with write_txn():
            if not any(int(n["id"]) == ne_id for n in await store.list_ne()):
                raise HTTPException(status_code=404, detail="no such NE")
            await engine.reset_profile(ne_id, time.time())
            await audit_row(
                request, principal, "profile.reset", "ok", object_type="ne", object_id=str(ne_id)
            )
        return {"status": "profiler evidence reset"}

    # -- operate (editor+) -------------------------------------------------------------

    # v0.7.1 (F34): the three routes below are the write perimeter — the only mutating routes
    # whose capability is below `admin` and which name a network element. Every other mutating
    # route is admin-only, and admin is never scoped (DECISIONS #58). Each resolves scope through
    # the SAME `scope_for` the reads use, and each denies by falling into the not-found branch it
    # already had, so "out of your scope" and "no such thing" are one code path — the same status,
    # the same body, the same timing (DECISIONS #60, #65). `scope_for` is awaited *before*
    # `write_txn()`, because it takes `store.lock` itself and the lock is not reentrant.

    async def label_context(
        sid: int, scope: shaping.Scope, body: FeedbackIn | CloseIn, channel: str
    ) -> capture.LabelContext:
        """The verdict's provenance. **v0.16.0: the body of this moved to `AppContext`**, which is
        where its third and fourth callers are — a `move` and an `operator_split` write a label
        through exactly this path, and a label acquired through a restructuring gesture must be
        identical to one acquired on a card apart from its channel.

        This wrapper stays so the two handlers below keep their call sites, and it is the whole of
        what remains here: it unpacks a request model the context deliberately does not know about.
        """
        return await ctx.label_context(
            sid,
            scope,
            channel,
            member_ids=body.member_ids,
            updated_at=body.updated_at,
            excluded_ids=body.excluded_ids,
            remainder_together=body.remainder_together,
        )

    @route.post("/api/situations/{sid}/feedback")
    async def feedback(
        sid: int, body: FeedbackIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, str]:
        scope = await scope_for(principal)
        if not await situation_in_scope(sid, scope):
            await audit_scope_denial(request, principal, "feedback", "situation", str(sid))
            raise HTTPException(status_code=404, detail="no such situation")
        label = await label_context(sid, scope, body, "organic")
        async with write_txn():
            # F36: `recorded.exists` is the 404 question; `recorded.inserted` is whether this
            # verdict was new. A repeat is a no-op that still answers 200 — the operator's
            # statement is already on record (DECISIONS #68).
            recorded = await engine.apply_feedback(
                sid,
                body.verdict,
                time.time(),
                principal_ref=principal.ref,
                role=principal.role,
                label=label,
            )
            # v0.16.0 (DECISIONS #254): a verdict is an operator looking at a situation, so it
            # promotes `new` to `open`. Here rather than inside `apply_feedback` because
            # `engine/operate/engine.py` is byte-identical through this release (#259), and
            # because the promotion is a *console* fact rather than a learning one — it must not
            # sit on the same path as the learned-state effect F36 bounds.
            if recorded.exists:
                await store.promote_situation(sid, time.time())
                # **The verdict is an operator gesture and is recorded as one.** Without this row
                # `situation_event` would hold four of the five gestures and the census would
                # report that most labelling never happened — and the bag provenance a verdict's
                # training rows are entitled to (§5) would exist for a move and not for a confirm.
                # The snapshot is taken here rather than before `apply_feedback` because a verdict
                # changes no membership: the bag is the same on both sides of the call.
                await gestures.record(
                    store,
                    gestures.Gesture(
                        kind="verdict",
                        situation_id=sid,
                        at=time.time(),
                        actor=principal.ref,
                        role=principal.role,
                        feedback_id=recorded.id if recorded.inserted else None,
                    ),
                    await gestures.snapshot(store, sid),
                )
            if recorded.exists and recorded.inserted:
                await audit_row(
                    request,
                    principal,
                    "feedback",
                    "ok",
                    object_type="situation",
                    object_id=str(sid),
                    details={"verdict": body.verdict},
                )
        if not recorded.exists:
            raise HTTPException(status_code=404, detail="no such situation")
        return {"status": "recorded", "verdict": body.verdict}

    @route.post("/api/labels")
    async def set_label(
        body: LabelIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, str]:
        """Name a device or an alarm class.

        A **class** is deliberately not scoped: it is a *kind of trap*, not a network element, and
        the table carries no NE reference — the same reasoning as `GET /api/classes`. A **device**
        is scoped (F34), and the nonexistent-target case (F37) leaves through the very same 404, so
        neither discloses whether the other applied.
        """
        scope = await scope_for(principal)
        if body.kind == "device" and not scope.allows_ne(body.id):
            await audit_scope_denial(request, principal, "label.set", body.kind, str(body.id))
            raise HTTPException(status_code=404, detail="no such label target")
        async with write_txn():
            # F37: `label` has no foreign key and `prune()` never touched it, so v0.7.0's
            # unconditional UPSERT was an unbounded, never-reclaimed write primitive against the
            # database file — a POST naming any integer created a row.
            if not await store.label_target_exists(body.kind, body.id):
                raise HTTPException(status_code=404, detail="no such label target")
            await store.set_label(body.kind, body.id, body.label, time.time())
            await audit_row(
                request,
                principal,
                "label.set",
                "ok",
                object_type=body.kind,
                object_id=str(body.id),
                details={"label_len": len(body.label)},
            )
        return {"status": "labelled"}

    @route.post("/api/situations/{sid}/close")
    async def close_situation(
        sid: int,
        request: Request,
        body: CloseIn | None = None,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, str]:
        """Close a situation, optionally recording the verdict the operator already formed.

        **v0.9.1 (Workstream 2).** Every field of `CloseIn` is optional and the body may be absent
        entirely, so a `curl` call, an old client and a UI that sends `{}` all behave exactly as
        they did at v0.9.0. Closing without judging stays exactly as easy as it was — no modal, no
        prompt, no required field, and no close that fails for want of a verdict.

        A verdict recorded here is written with **`acquisition_channel = 'close'`**, because closing
        selects for *resolved* incidents and that is a different population from the one an operator
        browses and labels spontaneously (DECISIONS #126).
        """
        scope = await scope_for(principal)
        if not await situation_in_scope(sid, scope):
            await audit_scope_denial(request, principal, "situation.close", "situation", str(sid))
            raise HTTPException(status_code=404, detail="no such open situation")
        verdict = body.verdict if body is not None else None
        # `feedback.write` and `situation.close` are DISTINCT capabilities, and a stored governance
        # policy may grant one while restricting the other (`resolve_capabilities` is
        # `ceiling ∩ policy`, so that configuration is reachable). A close carrying a verdict needs
        # both. `request.state.capabilities` is the set the perimeter already resolved for this
        # request, so no authorization decision is re-implemented here (DECISIONS #65, #76).
        #
        # Refused rather than silently stripped of its verdict: discarding a judgement without
        # saying so is the exact failure this release exists to end. Closing WITHOUT a verdict is
        # unaffected — the check is not reached.
        if verdict is not None and "feedback.write" not in request.state.capabilities:
            raise HTTPException(status_code=403, detail="insufficient role")
        label = (
            await label_context(sid, scope, body, "close")
            if body is not None and verdict is not None
            else None
        )
        async with write_txn():
            # The close runs FIRST, because it is what decides the 404: a verdict must never be
            # recorded against a situation that was not open. Both are in one transaction, so they
            # land together or not at all, and the bag is unaffected either way —
            # `forget_situation` runs after the boundary.
            closed = await store.manual_close_situation(sid, time.time())
            if closed and label is not None and verdict is not None:
                await engine.apply_feedback(
                    sid,
                    verdict,
                    time.time(),
                    principal_ref=principal.ref,
                    role=principal.role,
                    label=label,
                )
            if closed:
                # An operator's close is a gesture too, and `resolution='operator'` is the fact
                # that distinguishes it from the idle sweep. Recorded whether or not it carried a
                # verdict: the population that closes WITHOUT judging is the one v0.9.1 wanted
                # counted, and until now nothing recorded it as an event.
                await gestures.record(
                    store,
                    gestures.Gesture(
                        kind="operator_close",
                        situation_id=sid,
                        at=time.time(),
                        actor=principal.ref,
                        role=principal.role,
                    ),
                    await gestures.snapshot(store, sid),
                )
                await audit_row(
                    request,
                    principal,
                    "situation.close",
                    "ok",
                    object_type="situation",
                    object_id=str(sid),
                    # No new audit ACTION: the verdict rides along on the row the close already
                    # wrote, so one request stays one audit row.
                    details={"verdict": verdict} if verdict is not None else None,
                )
        if not closed:
            raise HTTPException(status_code=404, detail="no such open situation")
        engine.forget_situation(sid)
        return {"status": "closed"}
