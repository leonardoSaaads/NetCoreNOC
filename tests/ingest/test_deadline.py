"""ADR #447 — every trap is classified within the deadline of **its own** arrival, or counted.

The KPI the operator set: a trap that arrives 20 s after another still has its own full minute.
What a storm costs a trap is the time it waits behind the traps that arrived before it, and these
tests hold the appliance to measuring exactly that — from each trap's own `ts` to the commit that
placed it — and to saying so when a trap misses, whatever the reason.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import sqlite3
import time

import pytest

from netcorenoc.api import create_app
from netcorenoc.crosscutting.settings import Settings, SettingsError
from netcorenoc.engine.operate import engine as engine_module
from netcorenoc.engine.operate.deadline import DEADLINE_S, DeadlineTracker
from netcorenoc.main import Engine
from netcorenoc.store import Store

import authutil
import util


def test_each_trap_is_timed_from_its_own_arrival() -> None:
    tracker = DeadlineTracker()
    now = 10_000.0
    early, late = util.event(ts=now - 70.0), util.event(ts=now - 20.0)
    tracker.committed([early, late], now)
    assert tracker.classified == 2
    assert tracker.late == 1, "only the trap that arrived 70 s ago is past its own 60 s"
    assert tracker.worst_s == pytest.approx(70.0)
    assert tracker.percentile(0.0) == pytest.approx(20.0)
    assert tracker.warnings(dropped=0) and "1 classified late" in tracker.warnings(0)[0]


def test_a_miss_is_any_trap_not_classified_in_time() -> None:
    tracker = DeadlineTracker(deadline_s=DEADLINE_S)
    assert tracker.warnings(dropped=0) == [], "silent while nothing is missed"
    tracker.rolled_back([util.event(ts=1.0), util.event(ts=2.0)])
    sentence = tracker.warnings(dropped=3)[0]
    assert sentence.startswith("5 trap(s) missed the 60 s classification deadline")
    assert "2 lost to a storage error" in sentence and "3 dropped at a full queue" in sentence


def test_the_head_age_is_the_oldest_queued_trap() -> None:
    queue: asyncio.Queue[object] = asyncio.Queue()
    assert DeadlineTracker.head_age(queue, 100.0) == 0.0
    queue.put_nowait(util.event(ts=40.0))
    queue.put_nowait(util.event(ts=90.0))
    assert DeadlineTracker.head_age(queue, 100.0) == pytest.approx(60.0)


async def test_a_storm_commits_in_slices_and_every_trap_is_classified(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the time budget at zero every trap is its own transaction — the lock is released
    between them — and none is lost or double-counted."""
    monkeypatch.setattr(engine_module, "BATCH_BUDGET_S", 0.0)
    engine = Engine(store, asyncio.Queue())
    await engine.start()
    commits = {"n": 0}
    real_commit = store.commit

    async def counting_commit() -> None:
        commits["n"] += 1
        await real_commit()

    monkeypatch.setattr(store, "commit", counting_commit)
    now = time.time()
    for i in range(6):
        engine.queue.put_nowait(util.event(device=f"10.9.0.{i}", ts=now))
    task = asyncio.create_task(engine.run())
    try:
        for _ in range(200):
            if engine.deadlines.classified == 6:
                break
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    assert engine.deadlines.classified == 6
    assert commits["n"] == 6, "one transaction per trap at a zero budget"
    assert engine.deadlines.late == 0 and engine.latency_p95() < DEADLINE_S


async def test_a_rolled_back_batch_counts_its_traps_as_lost(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = Engine(store, asyncio.Queue())
    await engine.start()

    async def broken_commit() -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "commit", broken_commit)
    committed = await engine._commit_batch([util.event(ts=1.0), util.event(ts=2.0)])
    assert committed is False
    assert engine.deadlines.lost == 2 and engine.deadlines.classified == 0


async def test_readyz_is_not_ready_once_the_oldest_queued_trap_is_past_its_deadline(
    store: Store,
) -> None:
    engine = Engine(store, asyncio.Queue())
    await engine.start()
    app = create_app(engine)
    async with authutil.new_client(app) as client:
        engine.queue.put_nowait(util.event(ts=time.time() - 5.0))
        assert (await client.get("/readyz")).status_code == 200
        engine.queue.get_nowait()
        engine.queue.put_nowait(util.event(ts=time.time() - DEADLINE_S - 1.0))
        resp = await client.get("/readyz")
        assert resp.status_code == 503 and resp.json() == {"status": "not ready"}


async def test_stats_publish_the_classification_clock(store: Store) -> None:
    engine, _queue, app = await authutil.make_env(store)
    engine.deadlines.committed([util.event(ts=time.time() - 1.0)], time.time())
    admin = await authutil.client_as(app, "viewer")
    try:
        stats = (await admin.get("/api/stats")).json()
    finally:
        await admin.aclose()
    clock = stats["classification"]
    assert clock["deadline_s"] == DEADLINE_S and clock["classified"] == 1
    assert set(clock) == {
        "deadline_s",
        "classified",
        "late",
        "lost",
        "dropped",
        "p50_s",
        "p95_s",
        "p99_s",
        "worst_s",
        "head_age_s",
    }


@pytest.mark.parametrize("bad", ["0", "-5", "nan", "inf", "soon"])
def test_the_deadline_setting_is_refused_by_name(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    monkeypatch.setenv("NETCORENOC_CLASSIFY_DEADLINE_S", bad)
    with pytest.raises(SettingsError, match="NETCORENOC_CLASSIFY_DEADLINE_S"):
        Settings.from_env()


def test_the_deadline_and_ingest_settings_have_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NETCORENOC_CLASSIFY_DEADLINE_S", raising=False)
    monkeypatch.delenv("NETCORENOC_INGEST_PROCESSES", raising=False)
    settings = Settings.from_env()
    assert math.isclose(settings.classify_deadline_s, 60.0) and settings.ingest_processes == 1
    monkeypatch.setenv("NETCORENOC_INGEST_PROCESSES", "2")
    with pytest.raises(SettingsError, match="NETCORENOC_INGEST_PROCESSES"):
        Settings.from_env()
