"""The two operator gestures that assert **nothing about a grouping**.

Split from `routes_lifecycle.py` on the release's own central distinction rather than on size. Move,
merge and operator-split all say something about *correlation* and all produce link-training rows;
a hand-clear says something about an **alarm's lifecycle** and a rename says something about a
**label**, and `PREREGISTRATION-0.16.0.md` §1 turns that difference into a prohibition:

> **The same prohibition extends, for the same reason, to any signal that is not an assertion about
> a grouping.** A **manual clear of a zombie alarm** and a **self-clear** are facts about an alarm's
> lifecycle. They are recorded as events and they produce **no link-training row**. A fact about a
> different question may not do the work of a measurement about this one.

Keeping them in one module with the three that *do* assert would put the two kinds of gesture behind
one heading, and this release's most likely failure is exactly that confusion — Part X names it as
the second most likely way to get this wrong. A reader asking *"what can an operator do that teaches
the correlator nothing?"* gets one file.

The perimeter discipline is `routes_lifecycle.py`'s and is not restated: scope resolved through
`ctx.scope_for` before `write_txn`, an out-of-scope object denied through the same not-found branch
a nonexistent one takes, and a 409 for an object that exists and is visible but is no longer in the
state the gesture needs.
"""

from __future__ import annotations

import time

from fastapi import Depends, FastAPI, HTTPException, Request

from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.api.models import ClearIn, NameIn
from netcorenoc.crosscutting import auth
from netcorenoc.engine.dataset import gestures
from netcorenoc.engine.dataset.provenance import BagProvenance
from netcorenoc.engine.operate import membership


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the non-asserting operator gestures on `app`."""
    store, engine, security, scope_for = ctx.store, ctx.engine, ctx.security, ctx.scope_for
    audit_row, write_txn = ctx.perimeter.audit_row, ctx.write_txn
    situation_in_scope = ctx.perimeter.situation_in_scope
    audit_scope_denial = ctx.perimeter.audit_scope_denial
    route = DeclaredRoutes(app)

    @route.post("/api/situations/{sid}/name")
    async def name_situation(
        sid: int, body: NameIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, str]:
        """Name a situation, or send `null` to withdraw the name.

        Writes `operator_name` and never `derived_name`: the two are separate columns because an
        operator's name is a **label** with provenance and a derived name is a **projection** of
        membership. The `id` remains the identity — the permalink is unchanged, and a name is never
        a key.
        """
        scope = await scope_for(principal)
        if not await situation_in_scope(sid, scope):
            await audit_scope_denial(request, principal, "situation.name", "situation", str(sid))
            raise HTTPException(status_code=404, detail="no such situation")
        now = time.time()
        async with write_txn():
            subject = await gestures.snapshot(store, sid)
            if not await store.set_operator_name(sid, body.name, now):
                raise HTTPException(status_code=404, detail="no such situation")
            await store.promote_situation(sid, now)
            await gestures.record(
                store,
                gestures.Gesture(
                    kind="rename",
                    situation_id=sid,
                    at=now,
                    actor=principal.ref,
                    role=principal.role,
                ),
                subject,
            )
            await audit_row(
                request,
                principal,
                "situation.name",
                "ok",
                object_type="situation",
                object_id=str(sid),
                # The name itself is operator-supplied text and is not echoed into the audit trail;
                # its length is the fact a reader needs, exactly as `label.set` records it.
                details={"cleared": body.name is None, "name_len": len(body.name or "")},
            )
        return {"status": "named"}

    @route.post("/api/situations/{sid}/promote")
    async def promote_situation(
        sid: int, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, str]:
        """**Work this without judging it**: `new` -> `open`, and assert nothing.

        `PREREGISTRATION-0.16.2.md` §2.2, the second of the two actions it registers. The first is
        `POST /api/situations/{sid}/feedback` with `verdict: "confirm"` — *"this grouping is
        correct"*, which promotes **and** records the assertion under `m(c)` and the floor. This one
        promotes and records **no training row, no acquisition channel and no bag**.

        ## Why the split exists, and why this route is the cheap half of it

        The alternative was that promotion simply *is* a `confirm`: one line of code, and
        `asserting_bags` rises with every triage. §2.1 rejects it on the failure mode rather than on
        taste — an operator required to promote in order to work a situation will promote to get on
        with the shift, and the appliance would then record, at scale, `confirm` assertions meaning
        *"I needed this out of my way"*. That is **impatience wearing evidence's name**, and it is
        indistinguishable from judgement in every column the corpus stores.
        `PREREGISTRATION-0.9.0.md` §1 has already measured what such a population produces: 99.8 %
        accuracy from a model that always predicts link.

        ## What it does NOT write, and why that is load-bearing

        No `situation_event`. The event kinds are a `CHECK` constraint on `situation_event.kind`
        (`0014`), and widening a `CHECK` in SQLite is a table rebuild — of a table
        `situation_event_member` references `ON DELETE CASCADE`, under `PRAGMA foreign_keys=ON`, in
        a migration runner that uses `executescript`. The failure mode of getting that wrong is the
        **silent deletion of every membership snapshot the corpus holds**, which is a far worse
        trade than the one this release is making. So the durable record of a bare promotion is the
        **audit row** below: hash-chained, actor-attributed, and immutable in a way
        `situation_event` is not. §2.2 requires that a reader two months later can tell an
        affirmation from a bare promotion, and they can: one has a `feedback` row and a `verdict`
        event, the other has an audit row and neither.

        **A bare promotion is not weak evidence in this release** (§2.3). It is not a training row
        of any weight. Whether it should become one is an open question for a later release, to be
        decided when there are enough of them to look at.

        Idempotent: `promote_situation` is `WHERE status='new'`, so a second call is a no-op that
        still answers 200 — the operator's action is already on record, which is F36's reading of a
        repeat applied to a gesture that stores nothing.
        """
        scope = await scope_for(principal)
        if not await situation_in_scope(sid, scope):
            await audit_scope_denial(request, principal, "situation.promote", "situation", str(sid))
            raise HTTPException(status_code=404, detail="no such situation")
        async with write_txn():
            cur = await store.conn.execute("SELECT status FROM situation WHERE id=?", (sid,))
            row = await cur.fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="no such situation")
            if str(row[0]) == "resolved":
                # The same 409 every other gesture answers for a resolved situation: it exists and
                # is visible, and it is no longer in the state this action needs. Reopening is a
                # decision nobody has made (DECISIONS #254).
                raise HTTPException(
                    status_code=409, detail="that situation has resolved; reload the card"
                )
            await store.promote_situation(sid, time.time())
            await audit_row(
                request,
                principal,
                "situation.promote",
                "ok",
                object_type="situation",
                object_id=str(sid),
                # The status the situation was IN, so a reader can tell the promotion that moved it
                # from the second click that did nothing — which is the distinction §2.2 asks the
                # record to carry, and the only one this row could otherwise lose.
                details={"from": str(row[0])},
            )
        return {"status": "promoted"}

    @route.post("/api/alarms/{aid}/clear")
    async def clear_alarm(
        aid: int,
        request: Request,
        body: ClearIn | None = None,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, str]:
        """Hand-clear a zombie alarm — one whose device never sent the clear.

        **It asserts nothing about any grouping**, and this handler is where that is enforced: the
        event's `produces_training_rows` is 0 by construction, because `manual_clear` is not in
        `ASSERTING_KINDS`. A zombie clear is a fact about an *alarm's lifecycle*, and letting it
        reach the link scorer would be a signal about a different question doing the work of a
        measurement about this one — the `incumbent_linked` prohibition in a new register
        (`PREREGISTRATION-0.16.0.md` §1).

        Not under `/api/situations` for that reason: putting the alarm-lifecycle gesture in the
        correlation namespace would express the misreading the plan exists to prevent, as a URL.

        If the clear leaves every member of the alarm's situation cleared, the situation resolves
        with `resolution = 'manual_clear'` — distinguishable from `self_cleared`, because the
        network did not fix this one and an audit two months later needs to know that.
        """
        # The body is optional and carries no field yet: a `curl` call with no body, `{}`, and a
        # UI that sends nothing all behave identically. The model exists so a later release adding
        # a reason code has somewhere to put it without changing the route's shape — the same
        # discipline `CloseIn` follows.
        _ = body
        scope = await scope_for(principal)
        async with store.lock:
            sid = await store.situation_of_alarm(aid)
            exists, ne_id = await store.alarm_ne(aid)
        # An alarm that does not exist and one whose NE is out of scope take the SAME branch: same
        # status, same body, same timing. Existence is not disclosed (DECISIONS #60, F34).
        if not exists or not scope.allows_ne(ne_id):
            await audit_scope_denial(request, principal, "alarm.clear", "alarm", str(aid))
            raise HTTPException(status_code=404, detail="no such alarm")
        now = time.time()
        async with write_txn():
            subject = (
                await gestures.snapshot(store, sid)
                if sid is not None
                else gestures.Snapshot(0, (), BagProvenance(0, None, None))
            )
            if not await store.manual_clear_alarm(aid, now):
                raise HTTPException(status_code=409, detail="that alarm is not active")
            membership.cleared(engine, aid)
            if sid is not None:
                await store.promote_situation(sid, now)
                await gestures.record(
                    store,
                    gestures.Gesture(
                        kind="manual_clear",
                        situation_id=sid,
                        at=now,
                        actor=principal.ref,
                        role=principal.role,
                        alarm_id=aid,
                    ),
                    subject,
                )
                if await store.all_cleared(sid):
                    await store.resolve_situation(sid, "manual_clear", now)
                    engine.forget_situation(sid)
            await audit_row(
                request,
                principal,
                "alarm.clear",
                "ok",
                object_type="alarm",
                object_id=str(aid),
                details={"situation_id": sid},
            )
        return {"status": "cleared"}
