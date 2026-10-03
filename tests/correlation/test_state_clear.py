"""State-based clear (S9, §5.5): some platforms send one trap OID whose state varbind carries
both the raise and the clear. A varbind that strictly alternates between exactly two values for
enough full cycles is learned as that class's state field, its terminating value as the clear.
Additive to the class-level learner; until a field is learned nothing is routed."""

from __future__ import annotations

import asyncio
from typing import Any

from netcorenoc.engine.correlate.learn import StateClearLearner
from netcorenoc.ingest import known_oids
from netcorenoc.ingest.events import TrapEvent, Varbind
from netcorenoc.main import Engine
from netcorenoc.store import Store

import authutil

DEV = "10.40.0.1"
STATE_CLS = "1.3.6.1.4.1.9.9.276.1.1.55"  # a single-OID vendor portStateChange trap
STATE_OID = "1.3.6.1.4.1.9.9.276.1.1.9.1"  # its state varbind: down/up
BASE = 7_000_000.0


def _event(port: str, state: str, ts: float) -> TrapEvent:
    return TrapEvent(
        device=DEV,
        trap_oid=STATE_CLS,
        instance=port,
        ts=ts,
        varbinds=[Varbind(oid=STATE_OID, kind="str", value=state)],
    )


async def _process_all(engine: Engine, store: Store, events: list[TrapEvent]) -> None:
    async with store.lock:
        for ev in events:
            await engine._process(ev)
        await store.commit()


async def _status(store: Store, port: str) -> Any:
    async with store.lock:
        cur = await store.conn.execute("SELECT status FROM alarm WHERE instance=?", (port,))
        row = await cur.fetchone()
    assert row is not None
    return row["status"]


async def test_state_field_is_learned_and_then_clears_the_alarm(store: Store) -> None:
    engine, _queue, _app = await authutil.make_env(store)
    # down/up alternates for two full cycles -> the field is learned (up = clear).
    await _process_all(
        engine,
        store,
        [_event("eth-1", s, BASE + i) for i, s in enumerate(["down", "up", "down", "up"])],
    )
    class_id = await store.class_id(STATE_CLS, BASE)
    states = engine.learner.states
    assert states.clear_value.get((class_id, STATE_OID)) == "up"
    assert states.raise_value.get((class_id, STATE_OID)) == "down"

    # Forward: a fresh 'down' opens the alarm, the following 'up' closes it.
    await _process_all(engine, store, [_event("eth-1", "down", BASE + 100)])
    assert await _status(store, "eth-1") == "active"
    await _process_all(engine, store, [_event("eth-1", "up", BASE + 101)])
    assert await _status(store, "eth-1") == "cleared"


async def test_clear_before_learning_does_not_close(store: Store) -> None:
    """Additive: while the field is unlearned, an 'up' trap is just another raise of the same
    fingerprint (a re-activation), never a clear — grouping is unchanged until we learn."""
    engine, _queue, _app = await authutil.make_env(store)
    await _process_all(
        engine, store, [_event("eth-9", "down", BASE), _event("eth-9", "up", BASE + 1)]
    )
    class_id = await store.class_id(STATE_CLS, BASE)
    assert (class_id, STATE_OID) not in engine.learner.states.clear_value
    assert await _status(store, "eth-9") == "active"  # 'up' did not clear anything


async def test_three_valued_varbind_is_never_a_state_field(store: Store) -> None:
    engine, _queue, _app = await authutil.make_env(store)
    seq = ["down", "up", "degraded", "up", "down", "up"]  # a third value poisons the slot
    await _process_all(engine, store, [_event("eth-2", s, BASE + i) for i, s in enumerate(seq)])
    class_id = await store.class_id(STATE_CLS, BASE)
    assert (class_id, STATE_OID) not in engine.learner.states.clear_value


async def test_state_field_survives_restart(store: Store) -> None:
    engine, _queue, _app = await authutil.make_env(store)
    await _process_all(
        engine,
        store,
        [_event("eth-3", s, BASE + i) for i, s in enumerate(["down", "up", "down", "up"])],
    )
    class_id = await store.class_id(STATE_CLS, BASE)
    assert (class_id, STATE_OID) in engine.learner.states.clear_value
    await engine.maintenance(now=BASE + 10, retention_days=365.0)  # persists via states.flush

    fresh = Engine(store, asyncio.Queue())
    await fresh.start()
    assert fresh.learner.states.clear_value.get((class_id, STATE_OID)) == "up"

    rows = await store.list_state_clears()
    assert rows and rows[0]["varbind_oid"] == STATE_OID and rows[0]["clear_value"] == "up"


# -- pure learner ------------------------------------------------------------------------


def test_learner_learns_two_value_alternation_and_answers_is_clear() -> None:
    learner = StateClearLearner()
    for state in ["down", "up", "down", "up"]:
        learner.observe(1, "eth-1", 7, [(STATE_OID, state)])
    assert learner.clear_value[(7, STATE_OID)] == "up"
    assert learner.is_clear(7, [(STATE_OID, "up")])
    assert not learner.is_clear(7, [(STATE_OID, "down")])
    assert not learner.is_clear(8, [(STATE_OID, "up")])  # a different class is unaffected


def test_learner_ignores_constants_and_framing_varbinds() -> None:
    learner = StateClearLearner()
    for _ in range(10):  # a constant value never alternates
        learner.observe(1, "eth-1", 7, [(STATE_OID, "up")])
    assert (7, STATE_OID) not in learner.clear_value
    for state in ["down", "up", "down", "up"]:  # framing OIDs are skipped
        learner.observe(1, "eth-1", 7, [(known_oids.SNMP_TRAP_OID, state)])
    assert (7, known_oids.SNMP_TRAP_OID) not in learner.clear_value


# --- F134: a flapping link went invisible, through the engine rather than the learner ----------

LINK_DOWN = "1.3.6.1.6.3.1.1.5.3"
LINK_UP = "1.3.6.1.6.3.1.1.5.4"


def _link(oid: str, ts: float, port: str = "7") -> TrapEvent:
    return TrapEvent(device=DEV, trap_oid=oid, instance=port, ts=ts, varbinds=[])


async def test_a_flapping_link_is_still_reported_down_after_several_cycles(
    store: Store,
) -> None:
    """**The defect an operator would have met first** (v0.18.0, F134), end to end.

    `CLEAR_PAIR_SEEDS` ships `linkDown → linkUp`, and the alternation learner registered the
    **inverse** of it as soon as a `(device, instance)` alternation began with `linkUp` — which
    is the ordinary case for an appliance deployed while a link is already down, and for any
    slot a third alarm class restarted. From that moment every `linkDown` trap was dispatched to
    `_handle_clear` and no alarm was ever raised for it again.

    Measured before the fix on a real appliance over UDP: eight traps ending in `linkDown`,
    **0 active alarms**. The link was down and the console said the network was clean.

    This drives the engine, not the learner, because the learner's state is only half the story:
    what makes it a defect is the dispatch in `_process` that reads it.
    """
    engine, _queue, _app = await authutil.make_env(store)
    # The first trap is the recovery, then the link flaps and ends DOWN.
    events = []
    t = BASE
    for _ in range(4):
        events.append(_link(LINK_UP, t))
        events.append(_link(LINK_DOWN, t + 1))
        t += 10.0
    await _process_all(engine, store, events)

    assert await _status(store, "7") == "active", (
        "the link is down and the appliance holds no active alarm for it: the linkDown trap was "
        "dispatched as a clear (F134)"
    )
    down_class = await store.class_id(LINK_DOWN, BASE)
    assert engine.learner.clears.clear_to_raise.get(down_class) is None, (
        "linkDown is registered as a clear class; every future linkDown is swallowed"
    )


async def test_the_seeded_pair_still_clears_the_alarm_the_right_way_round(
    store: Store,
) -> None:
    """The control. A guard that simply stopped learning pairs would pass the test above and
    break the feature: `linkUp` must still clear the alarm `linkDown` raised."""
    engine, _queue, _app = await authutil.make_env(store)
    await _process_all(engine, store, [_link(LINK_DOWN, BASE)])
    assert await _status(store, "7") == "active"
    await _process_all(engine, store, [_link(LINK_UP, BASE + 1)])
    assert await _status(store, "7") == "cleared"
