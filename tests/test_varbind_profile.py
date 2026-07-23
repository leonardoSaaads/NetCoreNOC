"""The varbind profiler (S4): accumulation, the three-term score, the promotion predicate,
decoy resistance, and the bounded-memory caps. Observe-only here — no entity is created."""

from __future__ import annotations

import asyncio

from opticorr.store import Store
from opticorr.varbind_profile import (
    ENTITY_PROMOTE_OBS,
    MAX_TRACKED_VALUES,
    MAX_TRACKED_VARBINDS_PER_CLASS,
    Accumulator,
    VarbindProfiler,
)

import authutil


def test_accumulator_repeat_distinct_numeric_monotonic() -> None:
    a = Accumulator()
    for v in ["x", "x", "y"]:
        a.observe(v, 1.0)
    assert a.n_obs == 3 and a.n_repeat == 1 and a.n_distinct == 2 and a.n_numeric == 0
    b = Accumulator()
    for v in ["1", "2", "3", "2"]:  # 1<2, 2<3 increase; 3->2 does not
        b.observe(v, 1.0)
    assert b.n_numeric == 4 and b.n_monotonic == 2


def test_max_tracked_values_caps_the_dictionary() -> None:
    a = Accumulator()
    for i in range(MAX_TRACKED_VALUES + 500):
        a.observe(f"v{i}", 1.0)
    assert a.n_distinct == MAX_TRACKED_VALUES  # stops adding keys once full
    assert a.n_obs == MAX_TRACKED_VALUES + 500  # but keeps counting observations


def test_varbind_cap_evicts_least_observed() -> None:
    profiler = VarbindProfiler()
    for _ in range(300):  # a hot varbind with many observations
        profiler.observe(1, 1, [("1.3.hot", "x")], 1.0)
    for i in range(MAX_TRACKED_VARBINDS_PER_CLASS + 10):  # many cold ones, one obs each
        profiler.observe(1, 1, [(f"1.3.cold.{i}", "y")], 1.0)
    keys = [k for k in profiler.profiles if k[0] == 1 and k[1] == 1]
    assert len(keys) <= MAX_TRACKED_VARBINDS_PER_CLASS
    assert (1, 1, "1.3.hot") in profiler.profiles  # LRU-by-n_obs keeps the hot varbind


def _feed_decoy(profiler: VarbindProfiler, ne_id: int, ports: int = 40, repeats: int = 3) -> None:
    """Each event leads with a unique serial and timestamp, then the real port id, then a
    constant — the decoy layout from eval/corpus/decoy_varbinds.json."""
    serial = 0
    for _ in range(repeats):
        for class_id in (1, 2):
            for port in range(ports):
                serial += 1
                profiler.observe(
                    ne_id,
                    class_id,
                    [
                        ("1.3.serial", str(1_000_000 + serial)),
                        ("1.3.tstamp", str(1_700_000_000 + serial)),
                        ("1.3.portId", f"port-{port}"),
                        ("1.3.const", "vendor-tag"),
                    ],
                    float(serial),
                )


def test_profiler_selects_the_discriminator_and_rejects_decoys() -> None:
    profiler = VarbindProfiler()
    _feed_decoy(profiler, ne_id=1)
    best = profiler.best_promotable(1)
    assert best is not None and best.varbind_oid == "1.3.portId"
    assert best.score >= 0.6 and best.x > 0.9  # cross-class support carries it

    by_oid = {c.varbind_oid: c for c in profiler.candidates(1)}
    assert not by_oid["1.3.serial"].meets_floor()  # unique per trap: cardinality gate
    assert not by_oid["1.3.tstamp"].meets_floor()  # a timestamp: cardinality + monotonic
    assert not by_oid["1.3.const"].meets_floor()  # a constant: min-distinct gate


def test_cross_class_support_is_zero_for_single_class() -> None:
    profiler = VarbindProfiler()
    for i in range(ENTITY_PROMOTE_OBS + 50):
        profiler.observe(1, 1, [("1.3.onu", f"onu-{i % 100}")], float(i))  # one class only
    candidate = profiler.score(1, "1.3.onu")
    assert candidate.x == 0.0  # X needs two classes carrying the OID
    assert profiler.best_promotable(1) is None  # so nothing promotes on one class


def test_margin_holds_promotion_when_two_candidates_tie() -> None:
    profiler = VarbindProfiler()
    # Two varbinds with identical cross-class recurring value sets -> equal scores, no winner.
    for _ in range(3):
        for class_id in (1, 2):
            for k in range(80):
                profiler.observe(1, class_id, [("1.3.a", f"v{k}"), ("1.3.b", f"v{k}")], float(k))
    assert profiler.best_promotable(1) is None  # neither beats the other by 1.25x


def test_prune_stale_bounds_memory() -> None:
    profiler = VarbindProfiler()
    profiler.observe(1, 1, [("1.3.old", "x")], now=100.0)
    profiler.observe(1, 1, [("1.3.new", "y")], now=1000.0)
    removed = profiler.prune_stale(cutoff=500.0)
    assert removed == 1
    assert (1, 1, "1.3.old") not in profiler.profiles
    assert (1, 1, "1.3.new") in profiler.profiles


async def test_profiler_persists_and_reloads_summary_counts(store: Store) -> None:
    engine, _queue, _app = await authutil.make_env(store)
    profiler = engine.profiler
    for i in range(10):
        profiler.observe(1, 1, [("1.3.onu", f"onu-{i}")], float(i))
    async with store.lock:
        await engine._flush_profiler(now=100.0)
        await store.commit()
    rows = await store.varbind_profiles_for_ne(1)
    assert rows and rows[0]["varbind_oid"] == "1.3.onu" and rows[0]["n_obs"] == 10

    # A fresh engine reloads the summary counts (value sets rebuild from live traffic).
    fresh = type(engine)(store, asyncio.Queue())
    await fresh.start()
    assert fresh.profiler.profiles[(1, 1, "1.3.onu")].n_obs == 10


async def test_entities_api_exposes_tree_and_profiler_judgement(store: Store) -> None:
    engine, queue, app = await authutil.make_env(store)
    import util

    await util.drive(engine, queue, util.fixture_events("olt_storm.json", 1_000_000.0))
    viewer = await authutil.client_as(app, "viewer")
    try:
        listing = await viewer.get("/api/entities")
        assert listing.status_code == 200
        nes = listing.json()
        assert nes and all("entity_count" in ne and "entities" in ne for ne in nes)
        ne_id = nes[0]["id"]

        detail = await viewer.get(f"/api/entities/{ne_id}")
        assert detail.status_code == 200
        body = detail.json()
        assert body["entities"][0]["level"] == 0  # only the level-0 entity so far (S4)
        assert body["entities"][0]["key_source"] == "self"
        # The profiler's judgement is inspectable: each candidate carries R/X/D and evidence.
        assert body["candidates"], "profiler candidates must be exposed"
        top = body["candidates"][0]
        assert {"varbind_oid", "r", "x", "d", "score", "n_obs", "n_distinct"} <= top.keys()

        assert (await viewer.get("/api/entities/999999")).status_code == 404
    finally:
        await viewer.aclose()
