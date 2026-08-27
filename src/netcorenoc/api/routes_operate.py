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

from netcorenoc import capture
from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.api.models import CloseIn, FeedbackIn, LabelIn
from netcorenoc.crosscutting import auth, shaping


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
        """Everything a verdict records except the verdict, built once for both routes that write
        one. Two copies of this would be two chances for a label acquired through `close` to differ
        from one acquired on a card in some way nobody intended — and the whole value of the channel
        column depends on the two being **identical apart from the channel**.

        `scope` is the one the caller's 404 decision already used, so the record cannot disagree
        with the decision that produced it.
        """
        # v0.9.2 (F47): ONE read, and both scope facts derived from it. `hidden_member_ids` reuses
        # the same `situation_member_ne` + `scope.allows_ne` pair the 404 decision used, so the
        # count of hidden members and the identity of the hidden members are one answer rather than
        # two that can drift (DECISIONS #137).
        hidden = await ctx.perimeter.hidden_member_ids(sid, scope)
        return capture.LabelContext(
            # v0.8.0 §5.5: the scope fingerprint. A scoped editor labels a **partial view** and
            # cannot say which part, so without this the label is uninterpretable — and the noise is
            # *systematic*, because it correlates with the policy, so it does not average out.
            capture.LabelScope(
                policy_id=ctx.governance.scope_id,
                restricted=not scope.unrestricted,
                redacted_members=len(hidden),
                hidden_members=hidden,
            ),
            # §5.4b: the client's report, bounded and never rejected. `accept` can only truncate,
            # and records that it did. No id here is looked up, compared, or validated.
            capture.ClientFingerprint.accept(body.member_ids, body.updated_at)
            if body.member_ids is not None
            else None,
            # v0.9.1: **which members do not belong**, under the identical discipline — bounded,
            # never rejected, never used to validate the existence of anything. Absence means "the
            # operator marked nothing", which is a plain `split`, and never a guess.
            capture.Exclusion.accept(body.excluded_ids, body.remainder_together)
            if body.excluded_ids is not None
            else None,
            channel,
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
