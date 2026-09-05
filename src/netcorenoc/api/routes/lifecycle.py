"""The three operator gestures that assert something about a **grouping**.

Move, merge and operator-split. The two gestures that assert *nothing* about a grouping — a
zombie clear and a rename — are `routes_annotate.py`, split off on that distinction rather than on
size, because this release's second most likely failure is confusing the two kinds.

**Five routes rather than one overloaded restructure route** (DECISIONS #255, #260). The
alternative — a single `POST …/{sid}/restructure` carrying an `operation` field — needs one
declaration, one capability and one `ROUTE_SCOPE` entry, and that is exactly its defect. These
differ in **who may do them**, in **what they assert** (`PREREGISTRATION-0.16.0.md` §2 gives each
its own row), in **which objects they name** — a move and a merge name *two* situations, so the
scope decision is two `situation_in_scope` calls, not one — and in **what they audit**.

**Every route here is the write perimeter** in the sense v0.7.1's F34 established: its capability is
below `admin` and it names a network element. Each resolves scope through the same `ctx.scope_for`
the reads use and denies through the not-found branch it already had, so *"out of your scope"* and
*"no such thing"* are one code path — same status, same body, same timing (DECISIONS #60, #65).
`scope_for` is awaited **before** `write_txn()`, because it takes `store.lock` itself and the lock
is not reentrant.

## The order every gesture writes in, and why it is that order

    snapshot -> label (when the assertion has a label's shape) -> mutate -> event -> audit

The **snapshot first**, because the mutation destroys the membership it records and a moment not
captured is not captured late. The **label before the mutation**, because `apply_feedback` reads the
server's own bag from engine state at the instant of the verdict and that bag has to be the one the
operator was judging. The **event last**, because it carries the label's id and `apply_feedback`
may legitimately not insert one (F36). All of it inside one `write_txn`, so a refusal anywhere rolls
back everything — including a label written against a move that then turned out to be impossible.

## What a 409 means here, and why it is not a 404

A gesture whose *object* the caller may not see answers 404, indistinguishably from a nonexistent
one. A gesture whose object exists and is visible but is **no longer in the state the gesture
needs** — the alarm has already moved, the situation has resolved, the zombie already cleared —
answers **409**. That is a different fact and the operator can act on it: refresh the card. The
held card (#173) makes this a real case rather than a theoretical one, because an operator may be
looking at a payload up to one poll interval old, and #258 records that the answer is to show the
change and refuse the stale gesture rather than to apply it silently.
"""

from __future__ import annotations

import time

from fastapi import Depends, FastAPI, HTTPException, Request

from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.api.models import MergeIn, MoveIn, SplitIn
from netcorenoc.crosscutting import auth, shaping
from netcorenoc.engine.dataset import gestures
from netcorenoc.engine.model import confidence as confidence_rules
from netcorenoc.engine.operate import membership


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the situation-lifecycle routes on `app`."""
    store, engine, security, scope_for = ctx.store, ctx.engine, ctx.security, ctx.scope_for
    audit_row, write_txn = ctx.perimeter.audit_row, ctx.write_txn
    situation_in_scope = ctx.perimeter.situation_in_scope
    audit_scope_denial = ctx.perimeter.audit_scope_denial
    route = DeclaredRoutes(app)

    async def live_or_404(sid: int) -> str:
        """The situation's status, or a 404 — the one place the gestures' precondition is read.

        A gesture on a **resolved** situation is refused rather than reopening it: reopening is a
        decision nobody has made, and making it as a side effect of a move would be making it
        silently (DECISIONS #254).
        """
        cur = await store.conn.execute("SELECT status FROM situation WHERE id=?", (sid,))
        row = await cur.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="no such situation")
        return str(row[0])

    @route.post("/api/situations/{sid}/move")
    async def move_alarm(
        sid: int, body: MoveIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, str]:
        """Move one alarm from this situation to another.

        **The gesture this release exists for.** It is not a labelling task — it is the operator
        fixing their console — and it happens to be a pair-level assertion with a negative and a
        positive in the same action, which is what six releases of evidence machinery have been
        waiting for.

        The negative half is written as a `split` on the **source** situation carrying the moved
        alarm as its one marked member, which asserts *"this member does not belong with the rest,
        and nothing else"* — precisely what the operator did, and precisely the shape
        `Store.asserting_bag_rows` counts. The positive half is the destination's snapshot on the
        event, from which the derivation reads it; a `confirm` on the destination would have
        asserted every pair inside it positive, which the operator did not say.
        """
        scope = await scope_for(principal)
        for named in (sid, body.to_situation_id):
            if not await situation_in_scope(named, scope):
                await audit_scope_denial(
                    request, principal, "situation.move", "situation", str(named)
                )
                raise HTTPException(status_code=404, detail="no such situation")
        if sid == body.to_situation_id:
            raise HTTPException(status_code=409, detail="the alarm is already in that situation")
        now = time.time()
        async with write_txn():
            for named in (sid, body.to_situation_id):
                if await live_or_404(named) == "resolved":
                    raise HTTPException(
                        status_code=409, detail="that situation has resolved; reload the card"
                    )
            subject = await gestures.snapshot(store, sid)
            peer = await gestures.snapshot(store, body.to_situation_id)
            if body.alarm_id not in subject.alarm_ids:
                raise HTTPException(
                    status_code=409, detail="that alarm is no longer in this situation"
                )
            feedback_id = await _label_the_negative(
                ctx, sid, scope, principal, [body.alarm_id], body.confidence, "move", now
            )
            if not await store.move_alarm(body.alarm_id, sid, body.to_situation_id):
                raise HTTPException(
                    status_code=409, detail="that alarm is no longer in this situation"
                )
            membership.moved(engine, body.alarm_id, sid, body.to_situation_id)
            # **The subject only** (v0.16.2, DECISIONS #273). The operator read *this* situation
            # and took an alarm out of it, so `open` — *"an operator is working it"* (#254) — is
            # true of it. The destination is an **id they typed**: this module offers no picker,
            # deliberately, because the id is what an operator pastes from a chat during an
            # incident. Promoting it claimed somebody had looked at a situation nobody had opened,
            # and moved its card out of the **New** tab, which is where the operator who has not
            # looked at it would find it.
            await store.promote_situation(sid, now)
            await gestures.record(
                store,
                gestures.Gesture(
                    kind="move",
                    situation_id=sid,
                    at=now,
                    actor=principal.ref,
                    role=principal.role,
                    confidence=body.confidence,
                    peer_situation_id=body.to_situation_id,
                    alarm_id=body.alarm_id,
                    feedback_id=feedback_id,
                ),
                subject,
                peer,
            )
            await audit_row(
                request,
                principal,
                "situation.move",
                "ok",
                object_type="situation",
                object_id=str(sid),
                details={"alarm_id": body.alarm_id, "to": body.to_situation_id},
            )
        return {"status": "moved"}

    @route.post("/api/situations/{sid}/merge")
    async def merge_situations(
        sid: int, body: MergeIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, str]:
        """Merge another situation into this one, because an operator says they are one incident.

        **This writes no `feedback` row**, and that is a decision rather than an omission. A merge
        asserts every *cross* pair positive; a `confirm` on the merged situation would additionally
        assert every pair *inside each original bag* positive, which the operator did not say. §10's
        rule — ambiguity about what the operator asserted resolves to **less** — decides it, and the
        cross pairs are derived from the event's two snapshots instead.
        """
        scope = await scope_for(principal)
        for named in (sid, body.from_situation_id):
            if not await situation_in_scope(named, scope):
                await audit_scope_denial(
                    request, principal, "situation.merge", "situation", str(named)
                )
                raise HTTPException(status_code=404, detail="no such situation")
        if sid == body.from_situation_id:
            raise HTTPException(status_code=409, detail="a situation cannot merge into itself")
        now = time.time()
        async with write_txn():
            for named in (sid, body.from_situation_id):
                if await live_or_404(named) == "resolved":
                    raise HTTPException(
                        status_code=409, detail="that situation has resolved; reload the card"
                    )
            subject = await gestures.snapshot(store, sid)
            peer = await gestures.snapshot(store, body.from_situation_id)
            await store.operator_merge(sid, body.from_situation_id, now)
            membership.merged(engine, sid, body.from_situation_id)
            await store.promote_situation(sid, now)
            await gestures.record(
                store,
                gestures.Gesture(
                    kind="merge",
                    situation_id=sid,
                    at=now,
                    actor=principal.ref,
                    role=principal.role,
                    confidence=body.confidence,
                    peer_situation_id=body.from_situation_id,
                ),
                subject,
                peer,
            )
            await audit_row(
                request,
                principal,
                "situation.merge",
                "ok",
                object_type="situation",
                object_id=str(sid),
                details={"from": body.from_situation_id},
            )
        return {"status": "merged"}

    @route.post("/api/situations/{sid}/split")
    async def split_situation(
        sid: int, body: SplitIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, str]:
        """Split the named members out into a new situation.

        Stronger than the `split` **verdict**, which records that a grouping was wrong and moves no
        row. This moves the rows *and* records the assertion, and the assertion is the same one a
        marked split makes: the departing members against the remainder, negative, and nothing else.
        """
        scope = await scope_for(principal)
        if not await situation_in_scope(sid, scope):
            await audit_scope_denial(request, principal, "situation.split", "situation", str(sid))
            raise HTTPException(status_code=404, detail="no such situation")
        now = time.time()
        async with write_txn():
            if await live_or_404(sid) == "resolved":
                raise HTTPException(
                    status_code=409, detail="that situation has resolved; reload the card"
                )
            subject = await gestures.snapshot(store, sid)
            departing = [a for a in body.alarm_ids if a in subject.alarm_ids]
            # Every named member must still be one, and the whole situation may not depart: a
            # "split" that moved everything is a rename with extra steps, and it would leave an
            # empty situation asserting a negative against nothing.
            if len(departing) != len(set(body.alarm_ids)) or not 0 < len(departing) < len(
                subject.alarm_ids
            ):
                raise HTTPException(
                    status_code=409,
                    detail="those members are no longer a proper part of this "
                    "situation; reload the card",
                )
            feedback_id = await _label_the_negative(
                ctx, sid, scope, principal, departing, body.confidence, "operator_split", now
            )
            new_id = await store.operator_split(sid, departing, now)
            membership.split(engine, sid, new_id, set(departing))
            await store.promote_situation(sid, now)
            peer = await gestures.snapshot(store, new_id)
            await gestures.record(
                store,
                gestures.Gesture(
                    kind="operator_split",
                    situation_id=sid,
                    at=now,
                    actor=principal.ref,
                    role=principal.role,
                    confidence=body.confidence,
                    peer_situation_id=new_id,
                    feedback_id=feedback_id,
                ),
                subject,
                peer,
            )
            await audit_row(
                request,
                principal,
                "situation.split",
                "ok",
                object_type="situation",
                object_id=str(sid),
                details={"members": len(departing), "into": new_id},
            )
        return {"status": "split"}


async def _label_the_negative(
    ctx: AppContext,
    sid: int,
    scope: shaping.Scope,
    principal: auth.Principal,
    marked: list[int],
    confidence: float,
    channel: str,
    now: float,
) -> int | None:
    """Write the gesture's negative half through the **existing** label path, or write none.

    A `move` and an `operator_split` both assert *"these members do not belong with the rest, and
    nothing else"*, which is exactly a `split` carrying marked members (DECISIONS #124). So they go
    through `engine.apply_feedback` — the same call a card's Split button makes — with the channel
    naming which gesture produced it, and the corpus grows in a shape `Store.asserting_bag_rows`
    already counts. **The judge is not changed; it is fed.**

    Returns `None` in two cases, and both are honest rather than failures:

    * **Below the registered confidence floor** (§4): the action still happens and its event is
      recorded in full, and it produces no training row. Nothing is written here at all — no label,
      and therefore no learning effect either, because an operator who says they are unsure should
      not move the appliance's learned state.
    * **A repeat about an unchanged bag** (F36's bound, kept): the key is
      `(situation_id, verdict, bag_key)` since `0015`, so a *second* gesture whose bag has actually
      changed records its own label — which is F89's repair — while N identical posts about one
      unchanged bag still record once. `PREREGISTRATION-0.16.1.md` §2 registered which of the two
      a gesture is before the code decided it.
    """
    if not confidence_rules.admits(confidence):
        return None
    label = await ctx.label_context(sid, scope, channel, excluded_ids=marked)
    recorded = await ctx.engine.apply_feedback(
        sid,
        "split",
        now,
        principal_ref=principal.ref,
        role=principal.role,
        label=label,
    )
    return recorded.id if recorded.inserted else None
