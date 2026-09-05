"""The idle sweep, reproduced with its controls, and the re-trigger half that makes it critical.

`engine/operate/engine.py:551` calls `store.idle_open_situations(now - IDLE_CLOSE_S)` and closes
everything it returns. That query asks two questions — *is it live?* and *has nobody touched it?* —
and never asks the third, which `store.all_cleared` answers eight lines below it in the same file:
*is one of its alarms still on?*

## Four arms, and two of them are controls

Without the controls this probe measures the clock rather than the rule.

    A  TREATMENT  stale, one ACTIVE member    selected today; must NOT be selected after the repair
    B  CONTROL    fresh, one ACTIVE member    must never be selected — if it is, the probe is
                                              measuring `updated_at` and not the rule
    C  CONTROL    stale, every member CLEARED must ALWAYS be selected — if it is not, a "repair"
                                              that simply broke the sweep would score green here
    D  CONTROL    stale, EMPTY bag            must always be selected, and resolve as `idle`
                                              rather than `self_cleared` (DECISIONS #259)

## The re-trigger half

A trap that repeats against an alarm which is already `active` **increments `count`**; it does not
raise a second alarm, so it forms no second situation. That is why the defect is critical rather
than untidy: once the sweep has resolved the situation, the still-burning alarm is in no live view
and nothing the network does afterwards puts it back in one. The symptom of the defect is the
absence of a symptom.

That half is driven through the real ingest path — wire encoding, `parse_trap`, the batch loop —
because a hand-made `TrapEvent` would be measuring a different appliance from the one that ships.
Its control is the same replay against a situation the sweep did not touch, which must behave
normally.

Run it from the repository root:

    python tools/evidence/idle_sweep_probe.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

#: Derived from this file's location, never pinned to a build container's absolute path — the
#: mistake `render_drive.py` records, which made a reproduction command false on every machine but
#: the one it was written on.
ROOT = Path(__file__).resolve().parents[2]
for extra in ("tests", "src", "tools"):
    sys.path.insert(0, str(ROOT / extra))

#: One clock for the whole probe. Appendix B's first trap is driving traffic at a fixture epoch
#: while sweeping at wall-clock, which makes everything look 990 days old and produces a wrong
#: conclusion from a correct measurement.
BASE = 1_700_000_000.0


async def _seed_arm(store: Any, *, name: str, ts: float, members: int, active: bool) -> int:
    """One situation with `members` alarms, all active or all cleared, last touched at `ts`."""
    import util

    sid = await store.create_situation(ts, None)
    for n in range(members):
        raised = await store.ingest(util.event(device=f"10.7.7.{n + 1}", instance=name, ts=ts))
        await store.add_alarm_to_situation(sid, raised.alarm_id)
        if not active:
            await store.conn.execute(
                "UPDATE alarm SET status='cleared', cleared_at=? WHERE id=?", (ts, raised.alarm_id)
            )
    # `add_alarm_to_situation` touches the situation, so the age is set last or it is not set.
    await store.touch_situation(sid, ts)
    return int(sid)


async def _row(store: Any, sid: int) -> dict[str, Any]:
    detail = await store.situation_detail(sid)
    assert detail is not None
    return {
        "status": str(detail["status"]),
        "resolution": detail["resolution"],
        "all_cleared": await store.all_cleared(sid),
    }


async def sweep_arms() -> dict[str, Any]:
    """A, B, C and D: what the query selects, what `all_cleared` says, and where each ends."""
    from netcorenoc.engine.operate.engine import IDLE_CLOSE_S
    from netcorenoc.store import Store

    import authutil

    db = ROOT / ".demos" / "idle_sweep_probe.db"
    db.parent.mkdir(exist_ok=True)
    for stale in db.parent.glob("idle_sweep_probe.db*"):
        stale.unlink()
    store = Store(str(db))
    await store.open()
    try:
        engine, _queue, _app = await authutil.make_env(store)
        now = BASE + IDLE_CLOSE_S + 60.0
        stale_ts, fresh_ts = BASE, now - 10.0
        async with store.lock:
            arms = {
                name: await _seed_arm(store, name=name, ts=ts, members=members, active=active)
                for name, ts, members, active in (
                    ("A_stale_active", stale_ts, 2, True),
                    ("B_fresh_active", fresh_ts, 2, True),
                    ("C_stale_cleared", stale_ts, 2, False),
                    ("D_stale_empty", stale_ts, 0, True),
                )
            }
            selected = await store.idle_open_situations(now - IDLE_CLOSE_S)
            before = {name: await _row(store, sid) for name, sid in arms.items()}
            await store.commit()
        await engine.maintenance(now, retention_days=3650.0)
        async with store.lock:
            after = {name: await _row(store, sid) for name, sid in arms.items()}
        return {
            "ids": arms,
            "selected_by_the_query": sorted(selected),
            "before": before,
            "after": after,
        }
    finally:
        await store.close()


async def retrigger() -> dict[str, Any]:
    """The half that makes it critical: a repeat trap increments, and forms no new situation."""
    from netcorenoc.engine.operate.engine import IDLE_CLOSE_S
    from netcorenoc.store import Store

    import authutil
    import util

    db = ROOT / ".demos" / "idle_sweep_retrigger.db"
    db.parent.mkdir(exist_ok=True)
    for stale in db.parent.glob("idle_sweep_retrigger.db*"):
        stale.unlink()
    store = Store(str(db))
    await store.open()
    try:
        engine, queue, _app = await authutil.make_env(store)
        # TREATMENT: one device, swept while its alarm is still active.
        # CONTROL: a second device whose situation is created fresh and is never swept.
        treatment = [util.event(device="10.8.8.1", instance="if1", ts=BASE)]
        await util.drive(engine, queue, treatment)
        now = BASE + IDLE_CLOSE_S + 60.0
        control = [util.event(device="10.8.8.2", instance="if1", ts=now - 30.0)]
        await util.drive(engine, queue, control)

        async def snapshot() -> dict[str, Any]:
            async with store.lock:
                cur = await store.conn.execute(
                    "SELECT a.id, a.device_id, a.status, a.count, sa.situation_id, s.status AS ss "
                    "FROM alarm a LEFT JOIN situation_alarm sa ON sa.alarm_id=a.id "
                    "LEFT JOIN situation s ON s.id=sa.situation_id ORDER BY a.id"
                )
                alarms = [dict(r) for r in await cur.fetchall()]
                cur = await store.conn.execute("SELECT COUNT(*) FROM situation")
                row = await cur.fetchone()
            assert row is not None
            return {"alarms": alarms, "situations": int(row[0])}

        before_sweep = await snapshot()
        await engine.maintenance(now, retention_days=3650.0)
        after_sweep = await snapshot()
        # The same trap again, at both devices, after the sweep.
        await util.drive(
            engine,
            queue,
            [
                util.event(device="10.8.8.1", instance="if1", ts=now + 10.0),
                util.event(device="10.8.8.2", instance="if1", ts=now + 10.0),
            ],
        )
        after_replay = await snapshot()
        return {
            "before_sweep": before_sweep,
            "after_sweep": after_sweep,
            "after_replay": after_replay,
        }
    finally:
        await store.close()


async def main() -> None:
    out = {"sweep_arms": await sweep_arms(), "retrigger": await retrigger()}
    print(json.dumps(out, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
