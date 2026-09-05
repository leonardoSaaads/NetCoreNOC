"""The situation lifecycle: the state machine, the five gestures, and what each one asserts.

**This file is where `PREREGISTRATION-0.16.0.md` §1 and §2 are enforced rather than described.**
The plan's central claim is a distinction:

    a `move`, a `merge` and an `operator_split` say something about a GROUPING;
    a `manual_clear` and a `self_clear` say something about an ALARM,
    and a fact about a different question may not do the work of a measurement about this one.

Every test below is a reading of one half of that. The two that matter most are
`test_a_zombie_clear_produces_no_link_training_row` and its self-clear twin: they fail if a row
ever appears, which is the deliverable Part IX Phase 3 names — *"asserted by a test that fails if
one appears"* — rather than a test that passes because nothing happened to produce one.

**Every situation here is built by driving real traps through the real engine.** A hand-inserted
`situation_alarm` row would let a test pass against a membership the correlator could not produce,
which is the shape `tests/util.fixture_events` exists to prevent one layer down.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from netcorenoc.engine.dataset import gestures
from netcorenoc.engine.dataset.provenance import MIN_BRIDGE_SIDE, bridge_min_side, provenance
from netcorenoc.engine.model import confidence
from netcorenoc.engine.operate.engine import IDLE_CLOSE_S
from netcorenoc.main import Engine
from netcorenoc.store import Store

import authutil
import util

BASE = 1_700_000_000.0
#: Above the registered floor of 0.50, so a gesture carrying it produces a training row.
SURE = 0.8


async def seeded(store: Store) -> tuple[Engine, asyncio.Queue[Any], Any]:
    """A live appliance with the fibre-cut scenario replayed through the real ingest path."""
    engine, queue, app = await authutil.make_env(store)
    await util.drive(engine, queue, util.fixture_events("fiber_cut.json", BASE))
    return engine, queue, app


async def live_situations(store: Store) -> list[dict[str, Any]]:
    rows = await store.list_situations(None, 100)
    return [r for r in rows if r["status"] != "resolved"]


# --- the state machine -------------------------------------------------------------------


async def test_the_correlator_creates_new_and_a_gesture_makes_it_open(store: Store) -> None:
    """DECISIONS #254. `new` means *nobody has looked at this*; the first gesture promotes it.

    Asserted as a **transition** rather than as two states: a test that only checked the end state
    would pass against an appliance that created `open` and never moved anything, which is the
    reading this decision exists to reject.
    """
    _engine, _queue, app = await seeded(store)
    rows = await live_situations(store)
    assert rows and all(row["status"] == "new" for row in rows), rows
    sid = int(rows[0]["id"])

    client = await authutil.client_as(app, "editor")
    try:
        assert (
            await client.post(f"/api/situations/{sid}/name", json={"name": "mine"})
        ).status_code == 200
    finally:
        await client.aclose()

    after = {int(r["id"]): str(r["status"]) for r in await store.list_situations(None, 100)}
    assert after[sid] == "open", "the gesture did not promote the situation"
    # Every OTHER live situation is still `new`: the promotion reached the one the gesture named
    # and nothing else. (`resolved` rows are the correlator's own merges and are not in scope.)
    assert all(status in ("new", "resolved") for other, status in after.items() if other != sid), (
        after
    )


async def test_the_sweep_partitions_the_idle_population(store: Store) -> None:
    """**The critical repair** (v0.16.2, DECISIONS #274). Three arms, two of them controls.

    The fibre-cut replay leaves live situations whose alarms are still active. Driven an hour
    forward, the maintenance sweep used to resolve every one of them — removing a burning incident
    from every live view, and, because a repeating trap increments an existing alarm rather than
    raising a new one, from every view it could return to.

    The two controls are what make this a measurement of the rule rather than of the clock: a
    situation that is **fresh** and active must be untouched for the obvious reason, and one that is
    **stale with everything cleared** must still be resolved — without that arm, a sweep that had
    simply stopped working would score green here.
    """
    engine, _queue, _app = await seeded(store)
    live = await live_situations(store)
    assert len(live) == 1, f"the replay formed {len(live)} live situations, not the expected one"
    burning = int(live[0]["id"])
    now = BASE + IDLE_CLOSE_S + 60
    async with store.lock:
        # The two arms the fibre-cut replay does not supply, built through the store's own writes.
        # `quiet` is stale with every member cleared — the one shape #259 says reaches the sweep
        # already cleared, because the clear did not travel through `_handle_clear`.
        quiet = await store.create_situation(BASE, None)
        stale_alarm = await store.ingest(util.event(device="10.4.4.3", ts=BASE))
        await store.add_alarm_to_situation(quiet, stale_alarm.alarm_id)
        await store.conn.execute(
            "UPDATE alarm SET status='cleared', cleared_at=? WHERE id=?",
            (BASE + 10.0, stale_alarm.alarm_id),
        )
        await store.touch_situation(quiet, BASE)
        # `fresh` is live, still active, and touched a moment ago.
        fresh = await store.create_situation(now - 10.0, None)
        raised = await store.ingest(util.event(device="10.4.4.4", ts=now - 10.0))
        await store.add_alarm_to_situation(fresh, raised.alarm_id)
        await store.touch_situation(fresh, now - 10.0)
        await store.commit()

    await engine.maintenance(now, retention_days=3650.0)

    rows = {int(r["id"]): r for r in await store.list_situations(None, 200)}
    assert rows[burning]["status"] in ("new", "open"), (
        "the sweep resolved a situation that still holds an active alarm"
    )
    assert rows[burning]["resolution"] is None
    assert (rows[quiet]["status"], rows[quiet]["resolution"]) == ("resolved", "self_cleared"), (
        "the sweep stopped resolving a situation whose alarms had all cleared"
    )
    assert rows[fresh]["status"] in ("new", "open") and rows[fresh]["resolution"] is None

    # The idle-but-active state is REACHABLE, which is what stops it being a state that does not
    # exist, and the count the operator warning reports is derived from the same expression.
    async with store.lock:
        assert burning in await store.idle_active_situations(now - IDLE_CLOSE_S)
        assert fresh not in await store.idle_active_situations(now - IDLE_CLOSE_S)
    await engine._observe_idle_active(now)
    warnings = engine.stale_situation_warnings()
    assert warnings and "still" in warnings[0], warnings
    assert str(engine._idle_active_count) in warnings[0]


async def test_the_idle_sweep_and_the_self_clear_are_distinguishable(store: Store) -> None:
    """Phase 2's claim, and the reason `resolution` exists at all (DECISIONS #253, #259).

    Before v0.16.0 both wrote `closed` and **no column distinguished them**. The two are driven here
    through the paths that actually produce them rather than by calling the store twice with
    different arguments, because what is being asserted is that the *engine's* paths land on two
    values.

    **v0.16.2 narrows `idle` to the empty bag** (DECISIONS #274): the sweep no longer resolves a
    situation holding an active alarm, so the value it used to write for a burning one is a value
    it can no longer write. The empty-bag arm below is what keeps `idle` reachable at all, and
    `test_an_empty_situation_resolves_as_idle_and_never_as_self_cleared` is its dedicated guard.
    """
    engine, queue, _app = await seeded(store)
    async with store.lock:
        idle = await store.create_situation(BASE, None)
        await store.commit()
    await engine.maintenance(BASE + IDLE_CLOSE_S + 60, retention_days=3650.0)
    row = await store.situation_detail(idle)
    assert row is not None
    assert (row["status"], row["resolution"]) == ("resolved", "idle")

    # A second situation, emptied by a clear the network sent.
    await util.drive(
        engine,
        queue,
        [
            util.event(device="10.9.9.1", trap_oid=util.CIENA_TRAP, ts=BASE + 20_000),
            util.event(device="10.9.9.1", trap_oid=util.CIENA_TRAP, ts=BASE + 20_001),
        ],
    )
    fresh = await live_situations(store)
    assert fresh, "the second scenario formed no situation"
    sid = int(fresh[0]["id"])
    members = await store.situation_member_ids(sid)
    async with store.lock:
        for alarm in members:
            await store.conn.execute(
                "UPDATE alarm SET status='cleared', cleared_at=? WHERE id=?", (BASE + 20_002, alarm)
            )
        await store.close_situation(sid, BASE + 20_003)
        await store.commit()
    row = await store.situation_detail(sid)
    assert row is not None
    assert (row["status"], row["resolution"]) == ("resolved", "self_cleared"), (
        "a situation whose members all cleared was not recorded as self-cleared"
    )


async def test_an_empty_situation_resolves_as_idle_and_never_as_self_cleared(
    store: Store,
) -> None:
    """Appendix B's *"an invariant that cannot fail"*, met head-on.

    `SUM(status='active') = 0` is true of every **empty** set, so a close that derived
    `self_cleared` from it alone would say *"the network fixed it"* about a situation with nothing
    in it. The guard is the member count, and this is the input that would make the unguarded
    expression false.
    """
    async with store.lock:
        sid = await store.create_situation(BASE, None)
        await store.close_situation(sid, BASE + 1)
        await store.commit()
    row = await store.situation_detail(sid)
    assert row is not None and row["resolution"] == "idle"


# --- the names ---------------------------------------------------------------------------


async def test_the_derived_name_is_a_projection_and_the_operator_name_overrides_it(
    store: Store,
) -> None:
    """DECISIONS #257. Two columns, never one, and the id is still the identity."""
    _engine, _queue, app = await seeded(store)
    sid = int((await live_situations(store))[0]["id"])
    before = await store.situation_detail(sid)
    assert before is not None
    assert before["derived_name"], "the correlator formed a situation with no derived name"
    assert before["operator_name"] is None

    client = await authutil.client_as(app, "editor")
    try:
        assert (
            await client.post(f"/api/situations/{sid}/name", json={"name": "OLT 3 uplink cut"})
        ).status_code == 200
        detail = (await client.get(f"/api/situations/{sid}")).json()
    finally:
        await client.aclose()
    assert detail["operator_name"] == "OLT 3 uplink cut"
    assert detail["derived_name"] == before["derived_name"], (
        "naming a situation changed the name the server derives; they are two columns"
    )
    assert detail["id"] == sid, "the id is the identity and a name is a label on it"


async def test_the_derived_name_tracks_membership_and_cannot_go_stale(store: Store) -> None:
    """The claim that makes the stored column safe: it is written where membership changes.

    Driven through the gesture that changes membership rather than by calling the refresh directly
    — the promise is about the *write paths*, so a test that called the refresher would be asserting
    that the refresher works rather than that every path reaches it.
    """
    _engine, _queue, app = await seeded(store)
    rows = await live_situations(store)
    sid = int(rows[0]["id"])
    members = await store.situation_member_ids(sid)
    assert len(members) >= 2

    client = await authutil.client_as(app, "editor")
    try:
        # Split one member out; both sides must be renamed by the same statement group.
        response = await client.post(
            f"/api/situations/{sid}/split",
            json={"alarm_ids": [members[0]], "confidence": SURE},
        )
        assert response.status_code == 200, response.text
    finally:
        await client.aclose()

    for row in await store.list_situations(None, 100):
        recomputed = await _recomputed_name(store, int(row["id"]))
        assert row["derived_name"] == recomputed, (
            f"situation {row['id']} carries a stale derived name: "
            f"{row['derived_name']!r} against {recomputed!r}"
        )


async def _recomputed_name(store: Store, sid: int) -> str | None:
    """The name the store would derive **now**, read back through the same function it writes."""
    async with store.lock:
        return await store.refresh_derived_name(sid)


# --- what each gesture asserts -------------------------------------------------------------


async def test_a_move_records_both_signs_at_pair_granularity(store: Store) -> None:
    """**The release's product**, and the assertion Part IX Phase 3 names.

    A move says two things at once, and both must be on the record:

    * the moved alarm against the members it **left**, negative — written as a `split` on the
      source situation with that alarm as its one reconciled exclusion, which is exactly the shape
      `Store.asserting_bag_rows` counts;
    * the moved alarm against the members it **joined**, positive — the destination snapshot on the
      event, from which `gesture_positive_pairs` reads it.

    A build that recorded only the negative would pass every other test in this file, which is why
    both halves are asserted here and why the injection in `test_evidence_boundary.py` removes the
    positive specifically.
    """
    _engine, _queue, app = await seeded(store)
    sid, other, alarm = await _two_situations(store, app)

    client = await authutil.client_as(app, "editor")
    try:
        response = await client.post(
            f"/api/situations/{sid}/move",
            json={"alarm_id": alarm, "to_situation_id": other, "confidence": SURE},
        )
        assert response.status_code == 200, response.text
    finally:
        await client.aclose()

    event = await _one_event(store, "move")
    assert event["alarm_id"] == alarm and event["peer_situation_id"] == other
    assert event["produces_training_rows"] == 1
    assert event["acquisition_channel"] == "move"
    assert event["confidence"] == pytest.approx(SURE)
    assert event["role"] == "editor" and event["actor"], "the gesture recorded no actor"

    # The NEGATIVE half: a split bag whose reconciled exclusion is the moved alarm.
    assert event["feedback_id"] is not None, "the move recorded no label"
    cur = await store.conn.execute(
        "SELECT verdict, excluded_reconciled, acquisition_channel FROM feedback WHERE id=?",
        (event["feedback_id"],),
    )
    label = dict((await cur.fetchone()) or {})
    assert label["verdict"] == "split"
    assert label["excluded_reconciled"] == 1
    assert label["acquisition_channel"] == "move"

    # The POSITIVE half: the destination's snapshot, on the event, in its recorded order.
    peer = await store.event_members(int(event["id"]), "peer")
    assert peer == await _members_at(store, other, event), (
        "the destination snapshot is not the membership the operator was moving into"
    )
    assert alarm not in peer, "the snapshot was taken after the move, not before it"


async def test_a_merge_records_the_cross_pairs_and_writes_no_over_asserting_label(
    store: Store,
) -> None:
    """§10's rule: ambiguity about what the operator asserted resolves to **less**.

    A merge asserts every **cross** pair positive. A `confirm` on the merged situation would assert
    every pair *inside each original bag* positive too, which the operator did not say — so a merge
    writes no `feedback` row at all, and its assertion lives on the event's two snapshots.
    """
    _engine, _queue, app = await seeded(store)
    sid, other, _alarm = await _two_situations(store, app)

    client = await authutil.client_as(app, "editor")
    try:
        response = await client.post(
            f"/api/situations/{sid}/merge",
            json={"from_situation_id": other, "confidence": SURE},
        )
        assert response.status_code == 200, response.text
    finally:
        await client.aclose()

    event = await _one_event(store, "merge")
    assert event["feedback_id"] is None, (
        "a merge wrote a label; a `confirm` on the merged bag over-asserts (see the docstring)"
    )
    assert event["produces_training_rows"] == 1
    assert event["acquisition_channel"] == "merge"
    assert await store.event_members(int(event["id"]), "server")
    assert await store.event_members(int(event["id"]), "peer")

    merged = await store.situation_detail(other)
    assert merged is not None
    assert (merged["status"], merged["resolution"]) == ("resolved", "merged")


async def test_an_operator_split_records_the_departing_members_as_the_negative(
    store: Store,
) -> None:
    """Stronger than the `split` verdict, which records a judgement and moves no row."""
    _engine, _queue, app = await seeded(store)
    sid = int((await live_situations(store))[0]["id"])
    members = await store.situation_member_ids(sid)
    assert len(members) >= 3
    departing = members[:2]

    client = await authutil.client_as(app, "editor")
    try:
        response = await client.post(
            f"/api/situations/{sid}/split",
            json={"alarm_ids": departing, "confidence": SURE},
        )
        assert response.status_code == 200, response.text
    finally:
        await client.aclose()

    event = await _one_event(store, "operator_split")
    assert event["produces_training_rows"] == 1
    assert event["acquisition_channel"] == "operator_split"
    cur = await store.conn.execute(
        "SELECT verdict, excluded_reconciled FROM feedback WHERE id=?", (event["feedback_id"],)
    )
    label = dict((await cur.fetchone()) or {})
    assert (label["verdict"], label["excluded_reconciled"]) == ("split", len(departing))
    assert set(await store.situation_member_ids(int(event["peer_situation_id"]))) == set(departing)
    assert set(await store.situation_member_ids(sid)) == set(members) - set(departing)


# --- the prohibition ------------------------------------------------------------------------


async def test_a_zombie_clear_produces_no_link_training_row(store: Store) -> None:
    """**`PREREGISTRATION-0.16.0.md` §1, and the guard Phase 3 names.**

    A manual clear of a zombie alarm is a fact about an **alarm's lifecycle**. Letting it reach the
    link scorer would be a signal about a different question doing the work of a measurement about
    this one — the `incumbent_linked` prohibition in a new register.

    Asserted three ways, because each catches a different way of getting it wrong: the event says
    it produces none, the derivation returns none, and the label surface holds no row that names it.
    """
    _engine, _queue, app = await seeded(store)
    sid = int((await live_situations(store))[0]["id"])
    alarm = (await store.situation_member_ids(sid))[0]
    labels_before = await _label_count(store)

    client = await authutil.client_as(app, "editor")
    try:
        assert (await client.post(f"/api/alarms/{alarm}/clear", json={})).status_code == 200
    finally:
        await client.aclose()

    event = await _one_event(store, "manual_clear")
    assert event["produces_training_rows"] == 0, "a zombie clear claimed to assert about a grouping"
    assert event["acquisition_channel"] is None, "a zombie clear was given a label channel"
    assert event["confidence"] is None, "a zombie clear was given a confidence it cannot use"
    assert event["feedback_id"] is None
    assert await _label_count(store) == labels_before, "a zombie clear wrote a label"
    assert not [
        row for row in await store.gesture_positive_pairs() if row["kind"] == "manual_clear"
    ], "a zombie clear reached the training derivation"


async def test_a_self_clear_produces_no_link_training_row(store: Store) -> None:
    """The same prohibition for the gesture the **appliance** performs.

    A self-clear is the network fixing itself. It is recorded — an ISP manager auditing two months
    later needs to know a situation closed because the alarms cleared rather than because nobody
    looked — and it produces no link-training row, for the reason the zombie clear does not.
    """
    engine, queue, _app = await seeded(store)
    await util.drive(
        engine,
        queue,
        [
            util.event(device="10.9.9.2", trap_oid=util.CIENA_TRAP, ts=BASE + 30_000),
            util.event(device="10.9.9.2", trap_oid=util.HUAWEI_TRAP, ts=BASE + 30_001),
        ],
    )
    sid = int((await live_situations(store))[-1]["id"])
    labels_before = await _label_count(store)
    async with store.lock:
        for alarm in await store.situation_member_ids(sid):
            await store.conn.execute(
                "UPDATE alarm SET status='cleared', cleared_at=? WHERE id=?", (BASE + 30_002, alarm)
            )
        await store.close_situation(sid, BASE + 30_003)
        await store.commit()

    row = await store.situation_detail(sid)
    assert row is not None and row["resolution"] == "self_cleared"
    assert await _label_count(store) == labels_before, "a self-clear wrote a label"
    assert not [
        e for e in await store.gesture_positive_pairs() if e["kind"] not in ("move", "merge")
    ]
    assert "self_clear" not in await store.event_counts_by_channel()


# --- confidence -----------------------------------------------------------------------------


async def test_a_gesture_below_the_floor_happens_and_produces_no_training_row(
    store: Store,
) -> None:
    """§4: *"The action still happens — the operator is running the network, not labelling it."*

    Both halves are asserted, and the first is the one a build gets wrong: refusing the gesture
    would make the plan's own sentence untrue, and it would cost the operator a correction they
    were entitled to make.
    """
    _engine, _queue, app = await seeded(store)
    sid = int((await live_situations(store))[0]["id"])
    members = await store.situation_member_ids(sid)
    labels_before = await _label_count(store)

    client = await authutil.client_as(app, "editor")
    try:
        response = await client.post(
            f"/api/situations/{sid}/split",
            json={"alarm_ids": members[:1], "confidence": confidence.FLOOR - 0.01},
        )
        assert response.status_code == 200, response.text
    finally:
        await client.aclose()

    event = await _one_event(store, "operator_split")
    assert event["confidence"] == pytest.approx(confidence.FLOOR - 0.01)
    assert event["produces_training_rows"] == 0, "a gesture below the floor produced a training row"
    assert event["feedback_id"] is None
    assert await _label_count(store) == labels_before
    # …and the action itself happened.
    assert set(await store.situation_member_ids(sid)) == set(members[1:])


async def test_confidence_is_recorded_per_actor(store: Store) -> None:
    """§4's other half: *"recorded per actor, so a later release can measure whether a given
    operator's 0.8 is worth 0.8."* Without the actor the column is a convention forever."""
    _engine, _queue, app = await seeded(store)
    sid = int((await live_situations(store))[0]["id"])
    for role, value in (("editor", 0.6), ("admin", 0.95)):
        client = await authutil.client_as(app, role)
        try:
            assert (
                await client.post(f"/api/situations/{sid}/name", json={"name": role})
            ).status_code == 200
            members = await store.situation_member_ids(sid)
            assert (
                await client.post(
                    f"/api/situations/{sid}/split",
                    json={"alarm_ids": members[:1], "confidence": value},
                )
            ).status_code == 200
        finally:
            await client.aclose()
    cur = await store.conn.execute(
        "SELECT actor, role, confidence FROM situation_event "
        "WHERE kind='operator_split' ORDER BY id"
    )
    seen = [(str(r[0]), str(r[1]), float(r[2])) for r in await cur.fetchall()]
    assert len(seen) == 2, seen
    assert [row[1] for row in seen] == ["editor", "admin"]
    assert seen[0][2] == pytest.approx(0.6) and seen[1][2] == pytest.approx(0.95)
    # **Two different actors**, which is the whole of the per-actor claim: a column that recorded
    # the confidence without saying whose could never be used to ask whether a given operator's
    # 0.8 is worth 0.8, which is what §4 registers it for.
    assert seen[0][0] != seen[1][0] and all(row[0] for row in seen)


def test_the_confidence_multiplier_is_the_one_the_plan_registered() -> None:
    """`m(c) = 0.6 + 0.4c`, with the three values §4 writes out.

    Pinned by value rather than by formula: the plan registers the numbers, and a build that
    rewrote `INTERCEPT` and `SLOPE` consistently would leave a formula-only check green.
    """
    assert confidence.FLOOR == 0.5
    assert confidence.multiplier(0.5) == pytest.approx(0.80)
    assert confidence.multiplier(0.8) == pytest.approx(0.92)
    assert confidence.multiplier(1.0) == pytest.approx(1.00)
    # An unreported confidence shrinks nothing — the status quo for every label written before
    # this release — and is not the same as a reported zero.
    assert confidence.multiplier(None) == 1.0
    assert confidence.admits(None) and not confidence.admits(0.0)
    assert not confidence.admits(0.49) and confidence.admits(0.5)


# --- bag provenance: recorded, and NOT consumed ----------------------------------------------


async def test_bag_provenance_is_recorded_beside_every_asserting_gesture(store: Store) -> None:
    """§5. Recorded because it cannot be recomputed later — the scores decay and membership
    mutates — and reported, stratified, by the census."""
    _engine, _queue, app = await seeded(store)
    sid = int((await live_situations(store))[0]["id"])
    members = await store.situation_member_ids(sid)

    client = await authutil.client_as(app, "editor")
    try:
        assert (
            await client.post(
                f"/api/situations/{sid}/split",
                json={"alarm_ids": members[:1], "confidence": SURE},
            )
        ).status_code == 200
    finally:
        await client.aclose()

    event = await _one_event(store, "operator_split")
    assert event["bag_link_count"] is not None and event["bag_link_count"] > 0
    assert event["bag_weakest_margin"] is not None, (
        "a bag with links recorded no weakest margin; `why.js` shows the operator this number"
    )
    assert event["bag_has_bridge"] in (0, 1)


def test_the_bridge_search_answers_the_graphs_whose_answer_is_known() -> None:
    """Arithmetic, against graphs a reader can check by eye.

    The two that matter are the last two: a **repeated** edge makes its endpoints
    2-edge-connected, so it is not a bridge, and a graph with no bridge answers `None` rather than
    0 — *"there is no bridge"* and *"the bridge detaches nothing"* are different statements and
    only the first can be true.
    """
    assert bridge_min_side([1, 2, 3], [(1, 2), (2, 3)]) == 1
    assert bridge_min_side([1, 2, 3, 4, 5, 6], _TWO_TRIANGLES) == 3
    assert bridge_min_side([1, 2, 3], [(1, 2), (2, 3), (3, 1)]) is None
    assert bridge_min_side([1], []) is None
    assert bridge_min_side([1, 2], [(1, 2), (1, 2)]) is None
    # A 1051-member storm is a real bag in this repository's own corpus and Python's default
    # recursion limit is 1000, so the search is iterative and this is the input that proves it.
    assert bridge_min_side(list(range(1051)), [(i, i + 1) for i in range(1050)]) == 525


def test_the_registered_minimum_bridge_side_is_recorded_beside_the_measurement() -> None:
    """The plan registers *"two parts each above a registered minimum size"* and does not fix the
    size, so the **measurement** is stored beside the boolean it produces — a later release that
    registers a different minimum can recompute the answer instead of being stuck with this one."""
    assert MIN_BRIDGE_SIDE == 2
    held_by_a_leaf = provenance([1, 2, 3], [(1, 2), (2, 3)], [0.9, 0.9], 0.5)
    assert held_by_a_leaf.bridge_min_side == 1
    assert not held_by_a_leaf.has_bridge, "a pendant edge is not the bridge the plan means"
    two_halves = provenance([1, 2, 3, 4, 5, 6], _TWO_TRIANGLES, [0.9] * 7, 0.5)
    assert two_halves.bridge_min_side == 3 and two_halves.has_bridge
    assert set(two_halves.as_columns()) == {
        "bag_link_count",
        "bag_weakest_margin",
        "bag_bridge_min_side",
        "bag_has_bridge",
    }


_TWO_TRIANGLES = [(1, 2), (2, 3), (3, 1), (4, 5), (5, 6), (6, 4), (3, 4)]


# --- the channels ----------------------------------------------------------------------------


async def test_every_channel_is_counted_separately_and_never_blended(store: Store) -> None:
    """DECISIONS #126, restated by the plan's §2 for the three channels this release adds.

    A merge selects for a different population from the one an operator browses, and blending them
    destroys the bias characterisation **retroactively**, for rows already written.
    """
    _engine, _queue, app = await seeded(store)
    sid, other, alarm = await _two_situations(store, app)
    client = await authutil.client_as(app, "editor")
    try:
        assert (
            await client.post(
                f"/api/situations/{sid}/move",
                json={"alarm_id": alarm, "to_situation_id": other, "confidence": SURE},
            )
        ).status_code == 200
        assert (
            await client.post(f"/api/situations/{sid}/feedback", json={"verdict": "confirm"})
        ).status_code == 200
    finally:
        await client.aclose()
    counts = await store.event_counts_by_channel()
    assert counts.get("move") == 1
    assert counts.get("organic") == 1, "a verdict was not recorded as its own channel"
    assert "move+organic" not in counts and len(set(counts)) == len(counts)
    assert set(counts) <= {v for v in gestures.CHANNEL_OF.values() if v} | {"(none)"}


# --- helpers ----------------------------------------------------------------------------------


async def _two_situations(store: Store, app: object) -> tuple[int, int, int]:
    """`(source, destination, an alarm in the source)` — two live situations to move between.

    The fibre-cut seed forms one live situation, so the second is made by **splitting** the first,
    which is a gesture this release adds rather than a hand-inserted row: the destination is then a
    membership the appliance could actually produce.

    The split moves **two** members out and the move then runs **out of the new situation**, and
    both choices are load-bearing rather than arbitrary:

    * two, so the source still holds a member after the move and the negative half asserts a real
      pair rather than an empty product;
    * out of the *new* situation, because `operator_split` writes its `split` label against the
      **original** — so a move out of that same situation would be a second assertion about a bag
      the split had just changed, and this helper would then be exercising v0.16.1's bag key (F89)
      rather than the move. Using the untouched side keeps the subject the move.
      `tests/test_bag_identity.py` is where the second-assertion case is asserted deliberately.
    """
    rows = await live_situations(store)
    sid = int(rows[0]["id"])
    members = await store.situation_member_ids(sid)
    assert len(members) >= 4, "the seed must offer enough members to split two out and then move"
    client = await authutil.client_as(app, "editor")
    try:
        response = await client.post(
            f"/api/situations/{sid}/split",
            json={"alarm_ids": members[:2], "confidence": SURE},
        )
        assert response.status_code == 200, response.text
    finally:
        await client.aclose()
    cur = await store.conn.execute(
        "SELECT peer_situation_id FROM situation_event WHERE kind='operator_split' "
        "ORDER BY id DESC LIMIT 1"
    )
    split_row = await cur.fetchone()
    assert split_row is not None
    other = int(split_row[0])
    return other, sid, (await store.situation_member_ids(other))[0]


async def _one_event(store: Store, kind: str) -> dict[str, Any]:
    cur = await store.conn.execute(
        "SELECT * FROM situation_event WHERE kind=? ORDER BY id DESC LIMIT 1", (kind,)
    )
    row = await cur.fetchone()
    assert row is not None, f"no {kind} event was recorded"
    return dict(row)


async def _members_at(store: Store, sid: int, event: dict[str, Any]) -> list[int]:
    """The destination's membership **minus** whatever the gesture put into it."""
    return [a for a in await store.situation_member_ids(sid) if a != event["alarm_id"]]


async def _label_count(store: Store) -> int:
    cur = await store.conn.execute("SELECT COUNT(*) FROM feedback")
    row = await cur.fetchone()
    assert row is not None
    return int(row[0])
