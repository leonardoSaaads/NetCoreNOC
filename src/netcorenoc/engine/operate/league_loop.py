"""The slow loop's judge step: re-rank the league on this site's labels and record the champion.

v0.27.0 (ADR #423). The appliance runs two loops, after OpenRAN's split between the near-real-time
and the non-real-time RAN Intelligent Controllers (O-RAN.WG2, *Non-RT RIC Architecture*; O-RAN.WG3,
*Near-RT RIC Architecture*):

* the **fast loop** is the engine's batch: per activation, the champion scores the candidate pairs,
  grouping places the alarm, the lifecycle guard keeps confirmed work confirmed
  (`proposals.route`), and every challenger scores a sample in shadow (`LeagueShadow`). Bounded
  work, no I/O beyond the batch's own transaction, and it never judges;
* the **slow loop** is this step, on the maintenance cadence every :data:`JUDGE_EVERY_TICKS`
  ticks: it reads every label the site has produced, lets `league_judge.choose` re-rank the
  members on them, and — when the champion changes — writes a `league_decision` and an audit
  row. The fast loop picks the decision up at its next reload point, never mid-batch.

The interface between the two is one table row, which is what makes the slow loop's work
reviewable after the fact and keeps the fast loop's cost independent of how much the slow loop
thinks.

**Lock discipline.** The labels are read under `store.lock`; the judging — every member's log loss
on every labelled pair, which is the only expensive part — runs in a worker thread with the lock
released; the decision is written under the lock in its own transaction. A failure anywhere is
logged and rolled back, and the champion stays what it was.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from netcorenoc.crosscutting import audit
from netcorenoc.engine.model import league_judge, site, site_labels

if TYPE_CHECKING:  # pragma: no cover - type-only, no runtime edge (tests/repo/test_layers.py)
    from netcorenoc.engine.operate.engine import Engine

__all__ = ["JUDGE_EVERY_TICKS", "judge_step"]

log = logging.getLogger("netcorenoc")

JUDGE_EVERY_TICKS = 60  # five minutes at the 5 s maintenance cadence


async def judge_step(engine: Engine, now: float) -> dict[str, Any] | None:
    """One pass of the judge. Returns what it decided (also kept on ``engine.league_view``)."""
    members = engine.league_members
    if members is None or not members.members:
        return None
    store = engine.store
    async with store.lock:
        pairs = await site_labels.label_pairs(store)
        features = await store.pair_features([int(p["pair_id"]) for p in pairs])
        latest = await store.latest_league_decision()
        pinned = await store.decider_pin()
    rows = site.rows_from(pairs, features)
    current = None if latest is None else str(latest["champion"])
    choice = await asyncio.to_thread(
        league_judge.choose,
        members,
        rows=rows,
        current=current,
        pinned=pinned,
        latency_us=dict(engine.latency_us),
    )
    if choice is None:
        return None
    view: dict[str, Any] = {
        "at": now,
        **choice.as_dict(),
        "labels": {
            "pairs": len(rows),
            "incidents": len({r.incident for r in rows}),
            "negative_bags": len({r.bag for r in rows if r.y == 0}),
        },
        "pinned": pinned,
    }
    engine.league_view = view
    unchanged = (
        latest is not None
        and str(latest["champion"]) == choice.champion
        and bool(latest["pinned"]) == (pinned is not None)
    )
    if unchanged:
        return view
    async with store.lock:
        try:
            await store.add_league_decision(
                at=now,
                champion=choice.champion,
                previous=current,
                actor="judge",
                reason=choice.reason,
                pinned=pinned is not None,
                evidence={
                    "table": choice.table,
                    "comparisons": choice.comparisons,
                    "ineligible": choice.ineligible,
                    "labels": view["labels"],
                },
            )
            await audit.write_event(
                store,
                ts=now,
                actor="judge",
                role=None,
                source_ip=None,
                action="league.decide",
                outcome="ok",
                object_type="league",
                object_id=choice.champion,
                details={"previous": current, "reason": choice.reason, "pinned": pinned},
            )
            await store.commit()
        except Exception:
            await store.rollback()
            log.exception("the league judge could not record its decision; the champion stays")
            return None
    return view
