"""A case's priority and its clocks follow what the Broker shows (ADR #464).

The owner's test, as tests: a link-down the Broker showed as critical opened a **minor** case —
the case read `situation.severity`, which only autonomy writes — and raising the case to critical
left the take target at the minor's two hours. Now:

* a case is dispatched at the worst severity the Broker shows for its situation: each member's,
  with the Broker's precedence (declared, the catalogue's rule, what the trap carried), and the
  situation's own when autonomy set one;
* while the priority is still the appliance's, a worse alarm joining raises it — never lowers it;
  once a person sets it, it is theirs;
* the open cycle's take target follows the priority: raised to critical after 30 minutes in the
  queue, the case is late by five, not on time for another hour and a half;
* the handbook's restore clock runs from the event to `validate` or `resolved`.
"""

from __future__ import annotations

import time

from netcorenoc.engine.noc import case_loop
from netcorenoc.store import Store

import authutil
import util

LINK_DOWN = "1.3.6.1.6.3.1.1.5.3"


async def _rule(store: Store, oid: str, severity: str) -> None:
    async with store.lock:
        await store.put_class_rule(
            oid=oid,
            subtree=False,
            name=None,
            severity=severity,
            vendor=None,
            source="declared",
            origin=None,
            actor="test",
        )
        await store.commit()


async def _situation(store: Store, *, opened: float, trap: str = LINK_DOWN) -> int:
    async with store.lock:
        sid = await store.create_situation(opened, None)
        alarm = await store.ingest(util.event(device="10.0.0.1", trap_oid=trap, ts=opened))
        await store.add_alarm_to_situation(sid, alarm.alarm_id)
        await store.promote_situation(sid, opened, "promote")
        await store.commit()
    return sid


async def test_a_case_opens_at_the_severity_the_broker_shows(store: Store) -> None:
    await _rule(store, LINK_DOWN, "critical")
    sid = await _situation(store, opened=time.time())
    async with store.reading():
        assert (await store.noc_case(sid) or {})["priority"] == "minor", (
            "situation.severity is NULL"
        )
    await case_loop.tick(store)
    async with store.reading():
        case = await store.noc_case(sid)
        cycles = await store.noc_case_cycles(sid)
        posts = await store.noc_posts(sid)
    assert case is not None and case["priority"] == "critical" and case["priority_by"] is None
    assert [c["priority"] for c in cycles] == ["critical"]
    assert any("Priority raised to Critical" in p["body"] for p in posts)


async def test_autonomy_s_severity_raises_a_queued_case_and_its_take_target(store: Store) -> None:
    opened = time.time() - 30 * 60
    sid = await _situation(store, opened=opened, trap=util.CIENA_TRAP)
    await case_loop.tick(store)
    async with store.lock:
        await store.set_situation_severity(sid, "critical", "model:test")
        await store.commit()
    moved = await case_loop.tick(store)
    assert moved["raised"] == 1
    _engine, _queue, app = await authutil.make_env(store)
    editor = await authutil.client_as(app, "editor")
    try:
        body = (await editor.get(f"/api/noc/cases/{sid}")).json()
    finally:
        await editor.aclose()
    take = body["clocks"]["take"]
    assert body["priority"] == "critical" and body["priority_auto"] is True
    assert take["target_s"] == 25 * 60 and take["state"] == "late"
    assert 4 * 60 < take["overrun_s"] < 6 * 60
    assert body["clocks"]["restore"]["target_s"] == 4 * 3600
    assert body["clocks"]["restore"]["state"] == "running"


async def test_a_person_s_priority_is_theirs_and_the_restore_clock_stops(store: Store) -> None:
    sid = await _situation(store, opened=time.time() - 3600, trap=util.CIENA_TRAP)
    await case_loop.tick(store)
    _engine, _queue, app = await authutil.make_env(store)
    editor = await authutil.client_as(app, "editor")
    try:
        assert (await editor.post(f"/api/noc/cases/{sid}/take")).status_code == 200
        case = (await editor.get(f"/api/noc/cases/{sid}")).json()
        set_ = await editor.post(
            f"/api/noc/cases/{sid}", json={"version": case["version"], "priority": "major"}
        )
        assert set_.status_code == 200, set_.text
        assert set_.json()["priority_auto"] is False
        async with store.lock:
            await store.set_situation_severity(sid, "critical", "model:test")
            await store.commit()
        assert (await case_loop.tick(store))["raised"] == 0, "the person decided"
        async with store.lock:
            await store.resolve_situation(sid, "self_cleared", time.time())
            await store.commit()
        await case_loop.tick(store)
        after = (await editor.get(f"/api/noc/cases/{sid}")).json()
    finally:
        await editor.aclose()
    assert after["status"] == "validate"
    assert after["clocks"]["restore"]["state"] == "met"
    assert after["clocks"]["restore"]["target_s"] == 24 * 3600
