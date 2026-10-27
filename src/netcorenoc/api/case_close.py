"""The broker's half of resolving a case: close the situation, and clear its alarms only when the
resolver verified them clear on the device (ADR #459).

A resolved case closes its situation — the NOC says it is over, and a situation the NOC has
finished should not stay open on the broker's screens. **Its alarms are cleared only when the
resolver states they were checked on the device and are no longer active**
(`alarms_verified_clear`): that is the one circumstance in which a hand clear is a true fact — the
clear trap never arrived — rather than a convenience. Otherwise the situation is closed as an
operator closes it (`resolution='operator'`), alarms untouched, so no `manual_clear` fact is
manufactured about an alarm nobody cleared (`store/situations.py`, #274).

Both paths are the broker's own gestures, recorded exactly as `routes/annotate.py` and
`routes/operate.py` record them — `manual_clear` per alarm, `operator_close` for the close — with
one audit row per cleared alarm and one for the close, under the resolver's name. **A scoped
resolver clears only the members they can see**, as the bulk clear does.

Runs inside the caller's write transaction; the caller forgets the situation in the engine after
it commits, as every close does.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from fastapi import Request

from netcorenoc.crosscutting import auth, shaping
from netcorenoc.engine.dataset import gestures
from netcorenoc.engine.operate import membership

if TYPE_CHECKING:
    from netcorenoc.api.context import AppContext

__all__ = ["close_for_case"]


async def close_for_case(
    ctx: AppContext,
    request: Request,
    principal: auth.Principal,
    sid: int,
    scope: shaping.Scope,
    *,
    clear: bool,
) -> dict[str, Any]:
    """Close ``sid`` for its case; returns what was done (cleared count, closed or not)."""
    store, now = ctx.store, time.time()
    cleared: list[int] = []
    closed = False
    if clear:
        hidden = await ctx.perimeter.hidden_member_ids(sid, scope)
        subject = await gestures.snapshot(store, sid)
        for aid in await store.noc_case_members_active(sid):
            if aid in hidden or not await store.manual_clear_alarm(aid, now):
                continue
            membership.cleared(ctx.engine, aid)
            cleared.append(aid)
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
            await ctx.perimeter.audit_row(
                request,
                principal,
                "alarm.clear",
                "ok",
                object_type="alarm",
                object_id=str(aid),
                details={"situation_id": sid, "case": True},
            )
        if cleared and await store.all_cleared(sid):
            closed = await store.resolve_situation(sid, "manual_clear", now)
    if not closed:
        closed = await store.manual_close_situation(sid, now)
        if closed:
            await gestures.record(
                store,
                gestures.Gesture(
                    kind="operator_close",
                    situation_id=sid,
                    at=now,
                    actor=principal.ref,
                    role=principal.role,
                ),
                await gestures.snapshot(store, sid),
            )
            await ctx.perimeter.audit_row(
                request,
                principal,
                "situation.close",
                "ok",
                object_type="situation",
                object_id=str(sid),
                details={"case": True},
            )
    return {"cleared": len(cleared), "situation_closed": closed}
