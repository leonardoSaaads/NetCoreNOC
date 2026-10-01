"""The Pending state (v0.27.0, ADR #428): **a model never grows a situation an operator confirmed.**

The maintainer's report: traps were being added to situations already in `open`. Every test here
drives the real ingest path and the real routes, and each asserts a *transition* with its control —
a test that only looked at end states would pass against an appliance that never grouped anything.
"""

from __future__ import annotations

import asyncio
from typing import Any

from netcorenoc.engine.operate.engine import CLEAR_HOLD_S, IDLE_CLOSE_S
from netcorenoc.ingest.events import TrapEvent, Varbind
from netcorenoc.main import Engine
from netcorenoc.store import Store

import authutil
import util

BASE = 1_700_000_000.0
LOS = "1.3.6.1.4.1.1271.2.1.1"  # the fibre-cut scenario's loss-of-signal trap


def _los(device: str, port: str, ts: float) -> TrapEvent:
    return util.event(
        device=device,
        trap_oid=LOS,
        instance=port,
        ts=ts,
        varbinds=[Varbind(oid="1.3.6.1.4.1.1271.9.1", kind="str", value=port)],
    )


async def _seeded(store: Store) -> tuple[Engine, asyncio.Queue[Any], Any, int]:
    engine, queue, app = await authutil.make_env(store)
    await util.drive(engine, queue, util.fixture_events("fiber_cut.json", BASE))
    live = [r for r in await store.list_situations(None, 100) if r["status"] != "resolved"]
    assert len(live) == 1, live
    return engine, queue, app, int(live[0]["id"])


async def _members(store: Store, sid: int) -> set[int]:
    return {int(m["id"]) for m in await store.situation_members(sid)}


async def _status(store: Store, sid: int) -> tuple[str, int | None]:
    states = await store.lifecycle_states([sid])
    return states[sid]


async def test_a_new_situation_grows_and_an_open_one_gets_a_proposal_instead(store: Store) -> None:
    engine, queue, app, sid = await _seeded(store)
    # Control: while the situation is `new`, a related trap joins it — the model's territory.
    before = await _members(store, sid)
    await util.drive(engine, queue, [_los("127.0.0.2", "port-2/1", BASE + 8.0)])
    grown = await _members(store, sid)
    assert len(grown) == len(before) + 1, "the control failed: a new situation did not grow"

    editor = await authutil.client_as(app, "editor")
    try:
        assert (await editor.post(f"/api/situations/{sid}/promote")).status_code == 200
    finally:
        await editor.aclose()
    assert (await _status(store, sid))[0] == "open"

    await util.drive(engine, queue, [_los("127.0.0.3", "port-2/2", BASE + 9.0)])
    assert await _members(store, sid) == grown, "a model grew a situation an operator confirmed"
    pending = list(await store.list_situations("pending", 100))
    assert len(pending) == 1, pending
    assert pending[0]["proposed_into"] == sid
    assert 0.0 <= float(pending[0]["proposal_confidence"]) <= 1.0

    # A second related trap reuses the proposal rather than opening another.
    await util.drive(engine, queue, [_los("127.0.0.2", "port-2/3", BASE + 9.5)])
    again = await store.list_situations("pending", 100)
    assert [r["id"] for r in again] == [pending[0]["id"]]
    assert await _members(store, sid) == grown


async def test_accepting_merges_into_the_open_situation_and_labels_the_cross_pairs(
    store: Store,
) -> None:
    engine, queue, app, sid = await _seeded(store)
    editor = await authutil.client_as(app, "editor")
    try:
        assert (await editor.post(f"/api/situations/{sid}/promote")).status_code == 200
        await util.drive(engine, queue, [_los("127.0.0.3", "port-3/1", BASE + 9.0)])
        (pending,) = await store.list_situations("pending", 100)
        pid = int(pending["id"])
        proposed = await _members(store, pid)
        confirmed = await _members(store, sid)
        r = await editor.post(f"/api/situations/{pid}/proposal", json={"decision": "accept"})
        assert r.status_code == 200, r.text
        assert r.json() == {"status": "accepted", "into": sid}
        # The answer is not repeatable: the card is stale now.
        again = await editor.post(f"/api/situations/{pid}/proposal", json={"decision": "accept"})
        assert again.status_code == 409
    finally:
        await editor.aclose()
    assert await _members(store, sid) == confirmed | proposed
    assert (await _status(store, sid))[0] == "open", "accepting changed the target's state"
    detail = await store.situation_detail(pid)
    assert detail is not None and detail["status"] == "resolved"
    assert detail["resolution"] == "merged" and detail["merged_into"] == sid
    cur = await store.conn.execute(
        "SELECT COUNT(*) FROM situation_event WHERE kind='merge' AND situation_id=? "
        "AND peer_situation_id=?",
        (sid, pid),
    )
    assert (await cur.fetchone())[0] == 1, "accepting did not record the merge gesture"
    assert await store.proposal_stats() == {engine.decider_ref: {"accept": 1, "reject": 0}}
    assert all(engine.sit_of.get(a) == sid for a in proposed), "the engine's map did not follow"


async def test_rejecting_makes_it_a_situation_of_its_own_and_is_never_proposed_again(
    store: Store,
) -> None:
    engine, queue, app, sid = await _seeded(store)
    editor = await authutil.client_as(app, "editor")
    try:
        assert (await editor.post(f"/api/situations/{sid}/promote")).status_code == 200
        await util.drive(engine, queue, [_los("127.0.0.3", "port-4/1", BASE + 9.0)])
        (pending,) = await store.list_situations("pending", 100)
        pid = int(pending["id"])
        viewer = await authutil.client_as(app, "viewer")
        try:
            denied = await viewer.post(
                f"/api/situations/{pid}/proposal", json={"decision": "reject"}
            )
            assert denied.status_code == 403, "a viewer answered a proposal"
        finally:
            await viewer.aclose()
        r = await editor.post(
            f"/api/situations/{pid}/proposal", json={"decision": "reject", "confidence": 0.9}
        )
        assert r.status_code == 200, r.text
    finally:
        await editor.aclose()
    assert await _status(store, pid) == ("new", None)
    assert (pid, sid) in engine.rejected_proposals
    assert await store.rejected_proposals() == [(pid, sid)]
    confirmed = await _members(store, sid)
    # The same alarms again, and more like them: the rejected bag is not re-proposed into it.
    await util.drive(engine, queue, [_los("127.0.0.3", "port-4/2", BASE + 9.5)])
    assert await _members(store, sid) == confirmed
    for row in await store.list_situations("pending", 100):
        assert not (await _members(store, int(row["id"]))) & (await _members(store, pid))


async def test_a_proposal_lapses_to_new_when_its_target_leaves_open(store: Store) -> None:
    engine, queue, app, sid = await _seeded(store)
    editor = await authutil.client_as(app, "editor")
    try:
        assert (await editor.post(f"/api/situations/{sid}/promote")).status_code == 200
        await util.drive(engine, queue, [_los("127.0.0.2", "port-5/1", BASE + 9.0)])
        (pending,) = await store.list_situations("pending", 100)
        pid = int(pending["id"])
        assert (await editor.post(f"/api/situations/{sid}/close")).status_code == 200
    finally:
        await editor.aclose()
    await engine.maintenance(BASE + 20.0, 365.0)
    assert await _status(store, pid) == ("new", None), "a proposal outlived its target"
    stats = await store.stats()
    assert stats["pending_situations"] == 0


async def test_pending_is_live_everywhere_a_live_situation_is_counted(store: Store) -> None:
    """`LIVE` gained `pending`: an idle, empty proposal is swept; a burning one is not."""
    engine, queue, app, sid = await _seeded(store)
    editor = await authutil.client_as(app, "editor")
    try:
        assert (await editor.post(f"/api/situations/{sid}/promote")).status_code == 200
    finally:
        await editor.aclose()
    await util.drive(engine, queue, [_los("127.0.0.2", "port-6/1", BASE + 9.0)])
    (pending,) = await store.list_situations("pending", 100)
    stats = await store.stats()
    assert stats["pending_situations"] == 1
    assert stats["open_situations"] >= 2, "a pending situation is not counted as live"
    # Its alarm still burns, so an hour of silence does not resolve it (ADR #274's invariant).
    await engine.maintenance(BASE + IDLE_CLOSE_S + CLEAR_HOLD_S + 60.0, 365.0)
    assert (await _status(store, int(pending["id"])))[0] in ("pending", "new")
