"""The site judge and the in-product search (ADRs #411, #413), through the real runner.

One of Part VIII's injections lives here, with its control: *training blocking ingestion* —
`test_the_search_runs_out_of_process_and_never_blocks_a_tick` fails if the fit runs in the event
loop, because a tick then takes as long as the fit.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import time
from typing import Any

import pytest

from netcorenoc.engine.correlate.features import FEATURE_NAMES
from netcorenoc.engine.model import gam, shipped, site
from netcorenoc.engine.model.site_search import MIN_BAGS, Runner
from netcorenoc.engine.operate.engine import Engine
from netcorenoc.store import Store

from modelutil import TEST_MODEL, T

DAY = 86400.0


def _pairs(
    bags: int, *, days: int = 4, seed: int = 0
) -> tuple[list[dict[str, Any]], dict[int, str]]:
    """Labelled pairs with structure: close in time on one element links, far apart does not."""
    rng = random.Random(seed)
    pairs: list[dict[str, Any]] = []
    features: dict[int, str] = {}
    pid = 0
    for bag in range(bags):
        verdict = "confirm" if bag % 3 else "split"
        for _ in range(4):
            pid += 1
            same = verdict == "confirm"
            vec = [0.0] * len(FEATURE_NAMES)
            vec[FEATURE_NAMES.index("dt")] = rng.uniform(0, 20) if same else rng.uniform(200, 900)
            vec[FEATURE_NAMES.index("same_ne")] = 1.0 if same else float(rng.random() < 0.2)
            vec[FEATURE_NAMES.index("same_class")] = float(rng.random() < (0.7 if same else 0.2))
            vec[FEATURE_NAMES.index("oid_arcs")] = 9.0 if same else 7.0
            features[pid] = json.dumps(vec)
            pairs.append(
                {
                    "pair_id": pid,
                    "feedback_id": bag + 1,
                    "verdict": verdict,
                    "confidence": None,
                    "label_at": T + (bag % days) * DAY + bag,
                }
            )
    return pairs, features


def _feed(monkeypatch: pytest.MonkeyPatch, store: Store, bags: int) -> None:
    pairs, features = _pairs(bags)

    async def labelled(**_: object) -> list[dict[str, Any]]:
        return pairs

    async def none() -> list[dict[str, Any]]:
        return []

    async def vectors(ids: list[int]) -> dict[int, str]:
        return {i: features[i] for i in ids if i in features}

    monkeypatch.setattr(store, "labelled_pairs", labelled)
    monkeypatch.setattr(store, "gesture_positive_pairs", none)
    monkeypatch.setattr(store, "pair_features", vectors)


async def _open_run(store: Store, **budget: float) -> int:
    async with store.lock:
        run = await store.open_search_run(
            by="admin",
            seed=3,
            at=T,
            budget={
                "trials": 2,
                "max_rounds": 20,
                "min_rounds": 10,
                "eta": 3,
                "seconds": 60.0,
                **budget,
            },
        )
        await store.commit()
    return run


async def _tick(runner: Runner, engine: Engine) -> float:
    started = time.perf_counter()
    async with engine.store.lock:
        await runner.tick(engine, time.time())
        await engine.store.commit()
    return time.perf_counter() - started


async def _until_finished(runner: Runner, engine: Engine, limit_s: float = 120.0) -> list[float]:
    ticks: list[float] = []
    deadline = time.monotonic() + limit_s
    while time.monotonic() < deadline:
        ticks.append(await _tick(runner, engine))
        if not runner.busy and (await engine.store.search_runs(1))[0]["status"] != "running":
            return ticks
        await asyncio.sleep(0.2)
    raise AssertionError("the search did not finish in time")


async def test_too_few_labels_is_refused_with_the_reason(
    store: Store, test_model: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = Engine(store, asyncio.Queue())
    await engine.start()
    _feed(monkeypatch, store, MIN_BAGS - 1)
    await _open_run(store)
    runner = Runner()
    await _tick(runner, engine)
    run = (await store.search_runs(1))[0]
    assert run["status"] == "refused" and "labelled bag" in run["note"]
    assert not runner.busy


async def test_the_search_runs_out_of_process_and_never_blocks_a_tick(
    store: Store, test_model: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Part VIII: *training blocking ingestion* must be red. The fit is in another process, and
    every tick — the only thing the event loop does for the search — returns in milliseconds."""
    engine = Engine(store, asyncio.Queue())
    await engine.start()
    # Big enough that the fit takes seconds: an inline fit would hold a tick that long.
    _feed(monkeypatch, store, 400)
    run_id = await _open_run(store, trials=4, max_rounds=90, min_rounds=30)
    runner = Runner()
    first = await _tick(runner, engine)
    assert first < 0.5, f"starting the search held the event loop for {first:.2f}s"
    assert runner.job is not None, "a search with enough labels did not start"
    assert runner.job.process.pid not in (None, os.getpid()), "the fit is not in another process"
    ticks = await _until_finished(runner, engine)
    assert max(ticks) < 0.5, f"a tick blocked the event loop for {max(ticks):.2f}s"
    run = (await store.search_runs(1))[0]
    assert run["id"] == run_id and run["status"] == "done", run
    trials = await store.search_trials(run_id)
    assert trials and all(t["params"] for t in trials), "every trial is recorded"
    assert run["judgement"]["verdict"] in ("BETTER", "NOT_BETTER", "INSUFFICIENT_EVIDENCE")
    versions = [v for v in await store.list_model_versions(5) if v["kind"] == gam.KIND]
    assert [v["id"] for v in versions] == [run["model_version_id"]]
    assert await store.decider_mode() == "shipped", "a search never switches what decides"
    assert await store.active_model_version() is None, "a search never activates its model"


async def test_stopping_a_search_stops_the_worker(
    store: Store, test_model: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = Engine(store, asyncio.Queue())
    await engine.start()
    _feed(monkeypatch, store, 30)
    run_id = await _open_run(store, trials=6, max_rounds=400, min_rounds=40)
    runner = Runner()
    await _tick(runner, engine)
    assert runner.job is not None
    process = runner.job.process
    async with store.lock:
        await store.finish_search_run(run_id, "stopped", time.time(), "stopped by admin")
        await store.commit()
    await _until_finished(runner, engine)
    assert (await store.search_runs(1))[0]["status"] == "stopped"
    process.join(timeout=10)
    assert not process.is_alive()


# -- the judge (ADR #411) ---------------------------------------------------------------------


def _rows(bags: int, days: int = 4) -> list[site.SiteRow]:
    pairs, features = _pairs(bags, days=days)
    return site.rows_from(pairs, features)


def test_below_the_floors_the_verdict_is_insufficient_evidence() -> None:
    base = shipped.load_from(
        TEST_MODEL,
        json.dumps(
            {"artifact": {"sha256": __import__("hashlib").sha256(TEST_MODEL.encode()).hexdigest()}}
        ),
    ).scorer
    rows = _rows(8, days=2)
    _train, test = site.split_by_time(rows)
    verdict = site.judge(base, base, rows, test, [])
    assert verdict.verdict == "INSUFFICIENT_EVIDENCE"
    assert any(u.startswith("incidents") for u in verdict.unmet)
    assert any(u.startswith("label_days") for u in verdict.unmet)


def test_the_newest_labels_are_the_test_and_whole_bags_never_straddle() -> None:
    rows = _rows(30)
    train, test = site.split_by_time(rows)
    assert {r.bag for r in train}.isdisjoint({r.bag for r in test})
    assert max(r.label_at for r in train) <= min(r.label_at for r in test) + 4 * DAY
    newest = sorted({(r.label_at, r.bag) for r in rows})[-1][1]
    assert newest in {r.bag for r in test}


def test_a_site_model_that_harms_the_benchmark_is_not_better() -> None:
    """Do no harm: better here, much worse on the generated benchmark, is `NOT_BETTER`."""
    base = gam.load(TEST_MODEL)
    worse = json.loads(TEST_MODEL)
    worse["intercept"] = 2.5  # links nearly everything: fine on the confirms, poor elsewhere
    candidate = gam.load(json.dumps(worse, sort_keys=True, separators=(",", ":")))
    rows = _rows(40)
    _train, test = site.split_by_time(rows)
    bench = [
        site.SiteRow(
            tuple([900.0] + [0.0] * (len(FEATURE_NAMES) - 1)), 0, 1.0, ("bench", i), i, 0.0
        )
        for i in range(50)
    ]
    verdict = site.judge(base, candidate, rows, test, bench)
    assert verdict.verdict in ("NOT_BETTER", "INSUFFICIENT_EVIDENCE")
    assert verdict.verdict != "BETTER"
