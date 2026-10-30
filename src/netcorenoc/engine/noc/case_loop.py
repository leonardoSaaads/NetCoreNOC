"""The case loop: the acts nobody presses a button for (ADR #459).

Every few seconds, off the ingest path (#454): read what is due on a reader, then take
``store.lock`` once, re-read each case under it — a person may have taken it in between — and act:

* a **`new`** case is dispatched: to automated checks when one applies (`automation`), otherwise to
  the NOC queue, its take clock counted from the instant the situation opened;
* a **pending** case whose next update has come goes back to the NOC queue;
* a case whose **situation resolved** on the broker's side — every alarm cleared, an operator
  closed it, it was merged — is resolved if nobody owned it (or it was merged), and otherwise
  asks its owner to **validate** the resolution;
* a case **resolved** a day ago is closed;
* a live case whose priority is still the appliance's is **raised** when a worse alarm joined its
  situation (`case_ops.reprioritize`, ADR #464) — every `REPRIORITIZE_EVERY` passes, since it reads
  each such case's members;
* a live case **hears its alarms** on the same passes (`hearing`, ADR #474): what joined, raised
  again or cleared since it last looked is an event on its feed, and a pending case whose fault
  came back or grew is back in the queue.

A person posting on a pending case requeues it too, but that is the post's own request
(`api/routes/case_feed.py`), not a sweep: it should not wait five seconds to be seen.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from netcorenoc.engine.noc import case_ops, cases, hearing, kpi

if TYPE_CHECKING:
    from netcorenoc.store import Store

__all__ = ["LOOK_EVERY_S", "REPRIORITIZE_EVERY", "WHY_RESOLVED", "Automation", "case_loop", "tick"]

#: How often the loop looks. The NOC's shortest clock is 25 minutes; five seconds is invisible.
LOOK_EVERY_S = 5.0
#: Every how many passes the priorities are re-derived (30 s).
REPRIORITIZE_EVERY = 6

#: Why the Broker's situation left, in the words the console uses (`ui/app/views/parts/
#: lifecycle.js`): a case's feed said `(self_cleared)` until ADR #476.
WHY_RESOLVED: dict[str, str] = {
    "operator": "an operator closed it",
    "self_cleared": "every alarm cleared, the network fixed it",
    "idle": "it went quiet and timed out",
    "merged": "it was merged into another situation",
    "manual_clear": "an operator hand-cleared the last active alarm",
    "unattributed": "it closed before this appliance recorded why",
}

#: Decides, under the lock, whether a new case starts with automated checks (ADR #463).
Automation = Callable[["Store", int], Awaitable[bool]]


async def tick(
    store: Store,
    *,
    now: Callable[[], float] = time.time,
    automation: Automation | None = None,
    reprioritize: bool = True,
) -> dict[str, int]:
    """One pass; returns how many cases each act moved."""
    counts = {
        "dispatched": 0,
        "requeued": 0,
        "validate": 0,
        "resolved": 0,
        "closed": 0,
        "raised": 0,
        "told": 0,
    }
    at = now()
    async with store.reading():
        fresh = await store.noc_cases_in("new")
        due = await store.noc_cases_due(at)
        orphaned = await store.noc_cases_orphaned()
        stale = await store.noc_cases_resolved_before(at - kpi.CLOSE_AFTER_S)
        rising = await store.noc_cases_auto_priority() if reprioritize else []
        states = await store.noc_case_alarm_states() if reprioritize else {}
        heard = await store.noc_case_heard(states)
    fresh_set = set(fresh)
    rising = [sid for sid in rising if sid not in fresh_set]  # dispatch derives those itself
    news = hearing.plan(states, heard)
    if not (fresh or due or orphaned or stale or rising or news):
        return counts
    async with store.lock:
        at = now()
        for sid in rising:
            if await case_ops.reprioritize(store, sid, at) is not None:
                counts["raised"] += 1
        for sid, found in news.items():
            told = await hearing.apply(store, sid, found, at)
            if told == "requeued":
                counts["requeued"] += 1
            elif told == "told":
                counts["told"] += 1
        for sid in fresh:
            case = await store.noc_case(sid)
            if case is None or case["status"] != "new":
                continue
            auto = automation is not None and await automation(store, sid)
            await case_ops.dispatch(store, sid, at, automate=auto)
            counts["dispatched"] += 1
        for sid in due:
            case = await store.noc_case(sid)
            when = case.get("next_update_at") if case else None
            if case is None or case["status"] not in cases.PENDING or when is None or when > at:
                continue
            await case_ops.requeue(store, sid, "next_update", at)
            counts["requeued"] += 1
        for row in orphaned:
            sid = int(row["situation_id"])
            case = await store.noc_case(sid)
            if case is None or case["status"] in ("resolved", "closed", "validate"):
                continue
            said = WHY_RESOLVED.get(str(row["resolution"]), str(row["resolution"] or "closed"))
            why = f"the situation resolved: {said}"
            if row["resolution"] == "merged":
                await case_ops.auto_resolve(store, sid, at, "merged", "its situation was merged")
                counts["resolved"] += 1
            elif case["status"] in cases.UNOWNED:
                await case_ops.auto_resolve(store, sid, at, "cleared", why)
                counts["resolved"] += 1
            else:
                await case_ops.validate(store, sid, at, said)
                counts["validate"] += 1
        for sid in stale:
            case = await store.noc_case(sid)
            if case is not None and case["status"] == "resolved":
                await case_ops.close(store, sid, at)
                counts["closed"] += 1
        await store.commit()
    return counts


async def case_loop(store: Store, automation: Automation | None = None) -> None:
    """The supervised loop (a crash is the supervisor's to count and restart)."""
    passes = 0
    while True:
        await tick(store, automation=automation, reprioritize=passes % REPRIORITIZE_EVERY == 0)
        passes += 1
        await asyncio.sleep(LOOK_EVERY_S)
