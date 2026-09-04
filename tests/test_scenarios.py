"""Gate 2 evidence: fixture replays through the full engine.

Timestamps are synthetic (fixtures pass through the real wire encoder and parser), so
the scenarios are deterministic: training rounds are separated by more than the window,
alarms are cleared and situations closed between rounds — every closed situation is a
learning epoch, exactly as the scope prescribes.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from netcorenoc.engine.correlate.learn import MIN_EDGE_N
from netcorenoc.ingest.events import TrapEvent
from netcorenoc.ingest.receiver import QueueItem
from netcorenoc.main import IDLE_CLOSE_S, Engine
from netcorenoc.store import Store

import util

LOS = "1.3.6.1.4.1.1271.2.1.1"
LOF = "1.3.6.1.4.1.1271.2.1.2"
UPLINK = "1.3.6.1.4.1.2011.5.104.1"
ONU = "1.3.6.1.4.1.2011.5.104.2"
BASE = 1_000_000.0
ROUND = 5_000.0


@pytest.fixture
async def engine_env(store: Store) -> tuple[Engine, asyncio.Queue[QueueItem]]:
    queue: asyncio.Queue[QueueItem] = asyncio.Queue()
    engine = Engine(store, queue)
    await engine.start()
    return engine, queue


async def clear_and_close(engine: Engine, store: Store, now: float) -> None:
    """Between training rounds: operations clears everything, idle situations close."""
    await store.conn.execute("UPDATE alarm SET status='cleared', cleared_at=?", (now,))
    await store.commit()
    await engine.maintenance(now + IDLE_CLOSE_S + 10, retention_days=365.0, tick=1)


async def train_fiber_cut(
    engine: Engine, queue: asyncio.Queue[QueueItem], store: Store, rounds: int = 3
) -> None:
    for i in range(rounds):
        base = BASE + i * ROUND
        await util.drive(engine, queue, util.fixture_events("fiber_cut.json", base))
        await clear_and_close(engine, store, base + 100)


async def test_fiber_cut_cold_start_groups_per_device_until_evidence_accrues(
    engine_env: tuple[Engine, asyncio.Queue[QueueItem]], store: Store
) -> None:
    """Honest cold start: the first cross-device alarms stay apart; the merge happens
    only once n >= 5 co-occurrences have accumulated, mid-incident."""
    engine, queue = engine_env
    events = util.fixture_events("fiber_cut.json", BASE)
    await util.drive(engine, queue, events[:4])
    listed = await store.list_situations(status="new", limit=100)
    assert len(listed) == 2  # one per NE while the E edge is still untrusted
    assert {row["alarm_count"] for row in listed} == {2}
    await util.drive(engine, queue, events[4:])
    listed = await store.list_situations(status="new", limit=100)
    assert len(listed) == 1 and listed[0]["alarm_count"] == 8


async def test_fiber_cut_after_training_collapses_to_one_situation(
    engine_env: tuple[Engine, asyncio.Queue[QueueItem]], store: Store
) -> None:
    engine, queue = engine_env
    device_a = await store.device_id("127.0.0.2", BASE)
    device_b = await store.device_id("127.0.0.3", BASE)
    affinity_cold = engine.learner.device_affinity(device_a, device_b)
    await train_fiber_cut(engine, queue, store)

    # Matrices demonstrably changed, and the E edge is trusted (n >= 5) and persisted.
    affinity_trained = engine.learner.device_affinity(device_a, device_b)
    assert affinity_cold == 0.0 and affinity_trained > 0.9
    assert engine.learner.E.pair_mass(device_a, device_b) >= MIN_EDGE_N
    device_edges = await store.load_edges("device")
    assert any({e.a_id, e.b_id} == {device_a, device_b} and e.weight > 0.9 for e in device_edges)

    await util.drive(engine, queue, util.fixture_events("fiber_cut.json", BASE + 10 * ROUND))
    listed = await store.list_situations(status="new", limit=100)
    assert len(listed) == 1
    detail = await store.situation_detail(listed[0]["id"])
    assert detail is not None and len(detail["alarms"]) == 8

    # Plausible root: the first LOS on the NE that alarms first, learned via precedence.
    root = next(a for a in detail["alarms"] if a["id"] == detail["root_alarm_id"])
    assert root["class_oid"] == LOS and root["device_ip"] == "127.0.0.2"

    # Every link is explained by its three stored terms.
    assert len(detail["links"]) >= 7
    for link in detail["links"]:
        assert link["score"] > 0.5
        assert abs(link["score"] - (link["term_t"] + link["term_a"] + link["term_e"])) < 1e-9


async def test_background_noise_does_not_merge_into_the_fiber_cut(
    engine_env: tuple[Engine, asyncio.Queue[QueueItem]], store: Store
) -> None:
    engine, queue = engine_env
    await train_fiber_cut(engine, queue, store)
    eval_base = BASE + 10 * ROUND
    mixed = sorted(
        util.fixture_events("fiber_cut.json", eval_base)
        + util.fixture_events("background_noise.json", eval_base),
        key=lambda e: e.ts,
    )
    await util.drive(engine, queue, mixed)
    listed = await store.list_situations(status="new", limit=100)
    fiber_oids = {LOS, LOF, "1.3.6.1.4.1.1271.2.1.3", "1.3.6.1.4.1.1271.2.1.4"}
    fiber_sits, noise_sits = [], []
    for row in listed:
        detail = await store.situation_detail(row["id"])
        assert detail is not None
        oids = {a["class_oid"] for a in detail["alarms"]}
        if oids & fiber_oids:
            assert oids <= fiber_oids, "noise merged into the fiber-cut situation"
            fiber_sits.append(detail)
        else:
            noise_sits.append(detail)
    assert len(fiber_sits) == 1 and len(fiber_sits[0]["alarms"]) == 8
    assert len(noise_sits) == 24  # every noise alarm stays a singleton
    assert all(len(d["alarms"]) == 1 for d in noise_sits)


async def test_olt_storm_collapses_to_one_situation_with_damped_learning(
    engine_env: tuple[Engine, asyncio.Queue[QueueItem]], store: Store
) -> None:
    engine, queue = engine_env
    await util.drive(engine, queue, util.fixture_events("olt_storm.json", BASE))
    listed = await store.list_situations(status="new", limit=100)
    assert len(listed) == 1 and listed[0]["alarm_count"] == 501

    # The probable root is the uplink alarm, not one of the 500 symptoms.
    cur = await store.conn.execute(
        "SELECT a.id FROM alarm a JOIN alarm_class c ON c.id=a.class_id WHERE c.oid=?",
        (UPLINK,),
    )
    row = await cur.fetchone()
    assert row is not None
    detail = await store.situation_detail(listed[0]["id"])
    assert detail is not None and detail["root_alarm_id"] == row["id"]

    # Storm damping: one observation per activation means ~499 undamped onu-onu
    # observations; with 10x damping from window occupancy 50 the mass lands near
    # 48 + 451*0.1 ~ 93. An undamped run would be ~5x larger.
    onu_cls = await store.class_id(ONU, BASE)
    onu_pair_mass = engine.learner.A.pair_mass(onu_cls, onu_cls)
    assert 50.0 < onu_pair_mass < 150.0

    # Alternation learning pauses during storms: random class interleavings inside the
    # storm window must not be promoted to raise/clear pairs (regression for a false
    # mass-clear found during load testing).
    x_oid, y_oid = "1.3.6.1.4.1.9.9.777.0.1", "1.3.6.1.4.1.9.9.777.0.2"
    alternating = [
        util.event(device="127.0.0.4", trap_oid=oid, instance="mix", ts=BASE + 31 + i)
        for i, oid in enumerate([x_oid, y_oid, x_oid, y_oid, x_oid, y_oid])
    ]
    await util.drive(engine, queue, alternating)
    assert engine.learner.clears.raise_to_clear == {}


async def test_flapping_link_is_demoted_and_stops_correlating(
    engine_env: tuple[Engine, asyncio.Queue[QueueItem]], store: Store
) -> None:
    engine, queue = engine_env
    await util.drive(engine, queue, util.fixture_events("flapping_noise.json", BASE))
    cur = await store.conn.execute(
        "SELECT a.is_flapping, a.count, a.status FROM alarm a "
        "JOIN alarm_class c ON c.id=a.class_id WHERE c.oid='1.3.6.1.6.3.1.1.5.3'"
    )
    row = await cur.fetchone()
    assert row is not None
    assert row["is_flapping"] == 1  # demoted by the periodic-flapping detector
    assert row["count"] == 12  # every raise deduplicated into one fingerprint
    assert row["status"] == "cleared"  # the seeded linkDown→linkUp pair cleared it
    assert (await store.stats())["open_situations"] == 0


async def test_vendor_clear_pair_learned_by_alternation(
    engine_env: tuple[Engine, asyncio.Queue[QueueItem]], store: Store
) -> None:
    engine, queue = engine_env
    raise_oid, clear_oid = "1.3.6.1.4.1.9.9.187.0.1", "1.3.6.1.4.1.9.9.187.0.2"

    def ev(oid: str, ts: float) -> TrapEvent:
        return util.event(device="10.1.1.1", trap_oid=oid, instance="if-3", ts=ts)

    events = [ev(raise_oid, BASE), ev(clear_oid, BASE + 5)]
    events += [ev(raise_oid, BASE + 400), ev(clear_oid, BASE + 405)]
    await util.drive(engine, queue, events)
    raise_cls = await store.class_id(raise_oid, BASE)
    clear_cls = await store.class_id(clear_oid, BASE)
    assert engine.learner.clears.raise_to_clear.get(raise_cls) == clear_cls
    cur = await store.conn.execute("SELECT status FROM alarm")
    statuses = [r["status"] for r in await cur.fetchall()]
    assert statuses and all(s == "cleared" for s in statuses)
    assert (await store.stats())["open_situations"] == 0
    # The learned pair survives a restart via the edge table.
    await engine.maintenance(BASE + 1000, retention_days=365.0, tick=1)
    fresh = Engine(store, asyncio.Queue())
    await fresh.start()
    assert fresh.learner.clears.clear_to_raise.get(clear_cls) == raise_cls


async def test_split_feedback_measurably_reduces_affinity(
    engine_env: tuple[Engine, asyncio.Queue[QueueItem]], store: Store
) -> None:
    engine, queue = engine_env
    await train_fiber_cut(engine, queue, store)
    await util.drive(engine, queue, util.fixture_events("fiber_cut.json", BASE + 10 * ROUND))
    listed = await store.list_situations(status="new", limit=10)
    sid = listed[0]["id"]
    device_a = await store.device_id("127.0.0.2", BASE)
    device_b = await store.device_id("127.0.0.3", BASE)
    mass_before = engine.learner.E.pair_mass(device_a, device_b)
    affinity_before = engine.learner.device_affinity(device_a, device_b)
    assert (await engine.apply_feedback(sid, "split", BASE + 11 * ROUND)).exists
    assert engine.learner.E.pair_mass(device_a, device_b) == mass_before * 0.5
    assert engine.learner.device_affinity(device_a, device_b) <= affinity_before
    cur = await store.conn.execute("SELECT verdict FROM feedback WHERE situation_id=?", (sid,))
    row = await cur.fetchone()
    assert row is not None and row["verdict"] == "split"


async def test_confirm_feedback_reinforces(
    engine_env: tuple[Engine, asyncio.Queue[QueueItem]], store: Store
) -> None:
    engine, queue = engine_env
    await train_fiber_cut(engine, queue, store, rounds=1)
    await util.drive(engine, queue, util.fixture_events("fiber_cut.json", BASE + 10 * ROUND))
    listed = await store.list_situations(status="new", limit=10)
    device_a = await store.device_id("127.0.0.2", BASE)
    device_b = await store.device_id("127.0.0.3", BASE)
    mass_before = engine.learner.E.pair_mass(device_a, device_b)
    for row in listed:
        # v0.7.1 (F36): the result is (exists, inserted); `.exists` is the 404 question.
        assert (await engine.apply_feedback(row["id"], "confirm", BASE + 11 * ROUND)).exists
    assert engine.learner.E.pair_mass(device_a, device_b) > mass_before * 0.9
    assert not (await engine.apply_feedback(999_999, "confirm", BASE)).exists


# --- the scenario loader (#205) --------------------------------------------------------------
#
# Until v0.15.0 the four scenarios replayed above were stored twice: labelled in `eval/corpus/`,
# and again unlabelled in `tests/fixtures/`. `eval/corpus_gen.py` generated the first from the
# second, and nothing at all compared them — so an edit to one was invisible to the other until
# somebody ran `make corpus`, and the two were replayed by different gates. `util.scenario`
# derives the unlabelled stream at load time instead, and the four files are deleted. These are
# the guards for the derivation, and each has a control, because an invariant that cannot fail is
# not an invariant.


def test_the_loader_returns_the_corpus_stream_without_its_labels() -> None:
    for name in sorted(util.DERIVED_SCENARIOS):
        raw = json.loads((util.CORPUS / name).read_text())
        loaded = util.scenario(name)
        assert "description" not in loaded, f"{name}: the corpus-only key survived the strip"
        assert not any("truth" in event for event in loaded["events"]), (
            f"{name}: ground truth reached a replay — the engine would be shown the answer"
        )
        assert loaded["name"] == raw["name"]
        assert len(loaded["events"]) == len(raw["events"])


def test_each_half_of_the_scenario_strip_is_load_bearing() -> None:
    """The control for the test above: BOTH halves must do something on every scenario.

    A strip whose halves are no-ops would pass the assertions above by accident — `description`
    absent because the corpus never had one, `truth` absent because nothing labels these files.
    So assert the corpus carries both, on every scenario, and that removing either one alone
    leaves the other behind.
    """
    for name in sorted(util.DERIVED_SCENARIOS):
        raw = json.loads((util.CORPUS / name).read_text())
        assert "description" in raw, f"{name}: nothing for the first half of the strip to remove"
        assert all("truth" in event for event in raw["events"]), (
            f"{name}: nothing for the second half of the strip to remove"
        )
        only_description_removed = {k: v for k, v in raw.items() if k != "description"}
        assert any("truth" in e for e in only_description_removed["events"])
        only_truth_removed = dict(raw)
        only_truth_removed["events"] = [
            {k: v for k, v in e.items() if k != "truth"} for e in raw["events"]
        ]
        assert "description" in only_truth_removed
        assert util.strip_labels(raw) != only_description_removed
        assert util.strip_labels(raw) != only_truth_removed


def test_the_loader_hands_out_a_fresh_document_every_call() -> None:
    """A cached document a consumer mutated would poison every later test in the process."""
    first = util.scenario("fiber_cut.json")
    first["events"][0]["source"] = "0.0.0.0"
    first["events"].append({"poisoned": True})
    second = util.scenario("fiber_cut.json")
    assert second["events"][0]["source"] != "0.0.0.0"
    assert len(second["events"]) == len(first["events"]) - 1


def test_the_loader_refuses_a_name_it_does_not_derive() -> None:
    """`eval/corpus/` holds ten scenarios; only four were ever duplicated under tests/fixtures/,
    and only those four are what this loader promises. A silent widening would let a test replay
    a 672 KB corpus file by typo."""
    assert (util.CORPUS / "camera_nvr.json").is_file()
    with pytest.raises(KeyError):
        util.scenario("camera_nvr.json")
    with pytest.raises(KeyError):
        util.scenario("fiber_cut")  # no extension


def test_the_four_duplicated_fixtures_are_gone() -> None:
    """The point of the change, asserted rather than assumed: a re-added copy is a copy again."""
    for name in sorted(util.DERIVED_SCENARIOS):
        assert not (util.FIXTURES / name).exists(), (
            f"tests/fixtures/{name} is back; it is a second copy of eval/corpus/{name} that "
            f"nothing compares (#205)"
        )
