"""The case loop: the acts nobody presses a button for (ADR #459).

Every few seconds, off the ingest path (#454): read what is due on a reader, then take
``store.lock`` once, re-read each case under it — a person may have taken it in between — and act:

* a **`new`** case is dispatched: to automated checks when one applies (`automation`), otherwise to
  the NOC queue, its take clock counted from the instant the situation opened;
* a **pending** case whose next update has come goes back to the NOC queue;
* a case whose **situation resolved** on the broker's side — every alarm cleared, an operator
  closed it, it was merged — is resolved if nobody owned it (or it was merged), and otherwise
  asks its owner to **validate** the resolution;
* a case **resolved** a day ago is closed.

A person posting on a pending case requeues it too, but that is the post's own request
(`api/routes/case_feed.py`), not a sweep: it should not wait five seconds to be seen.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from netcorenoc.engine.noc import case_ops, cases, kpi

if TYPE_CHECKING:
    from netcorenoc.store import Store

__all__ = ["LOOK_EVERY_S", "Automation", "case_loop", "tick"]

#: How often the loop looks. The NOC's shortest clock is 25 minutes; five seconds is invisible.
LOOK_EVERY_S = 5.0

#: Decides, under the lock, whether a new case starts with automated checks.
Automation = Callable[["Store", int], Awaitable[bool]]


async def tick(
    store: Store,
    *,
    now: Callable[[], float] = time.time,
    automation: Automation | None = None,
) -> dict[str, int]:
    """One pass; returns how many cases each act moved."""
    counts = {"dispatched": 0, "requeued": 0, "validate": 0, "resolved": 0, "closed": 0}
    at = now()
    async with store.reading():
        fresh = await store.noc_cases_in("new")
        due = await store.noc_cases_due(at)
        orphaned = await store.noc_cases_orphaned()
        stale = await store.noc_cases_resolved_before(at - kpi.CLOSE_AFTER_S)
    if not (fresh or due or orphaned or stale):
        return counts
    async with store.lock:
        at = now()
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
            why = f"the situation resolved: {row['resolution'] or 'closed'}"
            if row["resolution"] == "merged":
                await case_ops.auto_resolve(store, sid, at, "merged", "its situation was merged")
                counts["resolved"] += 1
            elif case["status"] in cases.UNOWNED:
                await case_ops.auto_resolve(store, sid, at, "cleared", why)
                counts["resolved"] += 1
            else:
                await case_ops.validate(store, sid, at, str(row["resolution"] or "closed"))
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
    while True:
        await tick(store, automation=automation)
        await asyncio.sleep(LOOK_EVERY_S)
