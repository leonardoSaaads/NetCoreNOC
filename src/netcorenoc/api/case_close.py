"""The broker's half of resolving a case: close the situation, and clear its alarms only when the
resolver verified them clear on the device (ADRs #459, #463).

A resolved case closes its situation — the NOC says it is over, and a situation the NOC has
finished should not stay open on the broker's screens. **Its alarms are cleared only when the
resolver states they were checked on the device and are no longer active**
(`alarms_verified_clear`, or a playbook leaf's `clear_alarms`): that is the one circumstance in
which a hand clear is a true fact — the clear trap never arrived — rather than a convenience.
Otherwise the situation is closed as an operator closes it (`resolution='operator'`), alarms
untouched, so no `manual_clear` fact is manufactured about an alarm nobody cleared
(`store/situations.py`, #274).

Both paths are the broker's own gestures, recorded exactly as `routes/annotate.py` and
`routes/operate.py` record them — `manual_clear` per alarm, `operator_close` for the close — with
one audit row per cleared alarm and one for the close, under the resolver's name: a person, an
agent, or a playbook. **A scoped resolver clears only the members they can see**, as the bulk
clear does; a playbook acts for the appliance and sees them all.

Runs inside the caller's write transaction; the situation is forgotten by the engine after the
commit, as every close does.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from fastapi import Request

from netcorenoc.crosscutting import audit, auth, shaping
from netcorenoc.engine.dataset import gestures
from netcorenoc.engine.noc.runbook import Broker
from netcorenoc.engine.operate import membership

if TYPE_CHECKING:
    from netcorenoc.api.context import AppContext
    from netcorenoc.main import Engine
    from netcorenoc.store import Store

__all__ = ["automation_broker", "close_for_case"]

#: Writes one audit row: (action, object type, object id, details).
_Audit = Callable[[str, str, str, dict[str, Any]], Awaitable[None]]


async def _close(
    store: Store,
    engine: Engine,
    sid: int,
    *,
    clear: bool,
    actor: str | None,
    role: str | None,
    hidden: frozenset[int],
    audited: _Audit,
) -> dict[str, Any]:
    now = time.time()
    cleared: list[int] = []
    closed = False
    if clear:
        subject = await gestures.snapshot(store, sid)
        for aid in await store.noc_case_members_active(sid):
            if aid in hidden or not await store.manual_clear_alarm(aid, now):
                continue
            membership.cleared(engine, aid)
            cleared.append(aid)
            gesture = gestures.Gesture(
                kind="manual_clear", situation_id=sid, at=now, actor=actor, role=role, alarm_id=aid
            )
            await gestures.record(store, gesture, subject)
            await audited("alarm.clear", "alarm", str(aid), {"situation_id": sid, "case": True})
        if cleared and await store.all_cleared(sid):
            closed = await store.resolve_situation(sid, "manual_clear", now)
    if not closed:
        closed = await store.manual_close_situation(sid, now)
        if closed:
            gesture = gestures.Gesture(
                kind="operator_close", situation_id=sid, at=now, actor=actor, role=role
            )
            await gestures.record(store, gesture, await gestures.snapshot(store, sid))
            await audited("situation.close", "situation", str(sid), {"case": True})
    return {"cleared": len(cleared), "situation_closed": closed}


async def close_for_case(
    ctx: AppContext,
    request: Request,
    principal: auth.Principal,
    sid: int,
    scope: shaping.Scope,
    *,
    clear: bool,
) -> dict[str, Any]:
    """Close ``sid`` for a person's or an agent's resolution of its case."""

    async def audited(action: str, kind: str, object_id: str, details: dict[str, Any]) -> None:
        await ctx.perimeter.audit_row(
            request, principal, action, "ok", object_type=kind, object_id=object_id, details=details
        )

    return await _close(
        ctx.store,
        ctx.engine,
        sid,
        clear=clear,
        actor=principal.ref,
        role=principal.role,
        hidden=await ctx.perimeter.hidden_member_ids(sid, scope),
        audited=audited,
    )


def automation_broker(store: Store, engine: Engine) -> Broker:
    """What a playbook's `resolve` leaf needs from the broker (`engine/noc/runbook.py`)."""

    async def close(sid: int, clear: bool, actor: str) -> bool:
        async def audited(action: str, kind: str, object_id: str, details: dict[str, Any]) -> None:
            await audit.write_event(
                store,
                ts=time.time(),
                actor=actor,
                role=None,
                source_ip=None,
                action=action,
                outcome="ok",
                object_type=kind,
                object_id=object_id,
                details=details,
            )

        done = await _close(
            store,
            engine,
            sid,
            clear=clear,
            actor=actor,
            role=None,
            hidden=frozenset(),
            audited=audited,
        )
        return bool(done["situation_closed"])

    return Broker(close=close, forget=engine.forget_situation)
