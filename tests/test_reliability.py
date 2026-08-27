"""§A.5 reliability — readiness, store integrity/fault handling, task supervision, graceful drain.

These drive the real engine/store/API objects; the trap datagram path is never touched. Findings
F10 (unsupervised task death) and F11 (sqlite operational error / damaged DB) are regression-tested
here.
"""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3

import pytest

from netcorenoc.api import create_app
from netcorenoc.crosscutting import audit
from netcorenoc.main import Engine, Supervisor
from netcorenoc.store import Store

import authutil
import util

# -- readiness (/readyz) -------------------------------------------------------------


async def test_readyz_ready_and_leaks_no_detail(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    async with authutil.new_client(app) as client:  # unauthenticated on purpose
        resp = await client.get("/readyz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ready"}  # exactly ok/not-ok, no internal detail


async def test_readyz_reports_not_ready_when_queue_saturated(store: Store) -> None:
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=10)
    engine = Engine(store, queue)  # type: ignore[arg-type]
    await engine.start()
    app = create_app(engine)
    for _ in range(9):  # 9 >= 0.9 * 10 → saturated
        queue.put_nowait(util.event())
    async with authutil.new_client(app) as client:
        resp = await client.get("/readyz")
        assert resp.status_code == 503
        assert resp.json() == {"status": "not ready"}


# -- store integrity (F11) -----------------------------------------------------------


async def test_integrity_check_clean_db_has_no_warnings(store: Store) -> None:
    assert store.integrity_warnings == []  # the fixture opened a healthy DB


async def test_integrity_check_flags_foreign_key_orphans(store: Store) -> None:
    async with store.lock:
        await store.conn.execute("PRAGMA foreign_keys=OFF")
        await store.conn.execute(
            "INSERT INTO situation_alarm (situation_id, alarm_id) VALUES (99999, 99999)"
        )
        await store.conn.execute("PRAGMA foreign_keys=ON")
        await store.commit()
        store.integrity_warnings.clear()
        await store._check_integrity()
    assert any(
        "foreign_key" in w.lower() or "orphan" in w.lower() for w in store.integrity_warnings
    )
    # Damage is a warning, not a crash — the store is still usable.
    async with store.lock:
        assert await store.schema_version() == store.latest_schema_version()


# -- task supervision (F10) ----------------------------------------------------------


async def test_supervisor_restarts_crashed_task_and_warns() -> None:
    supervisor = Supervisor(backoff_base=0.0, backoff_max=0.0)
    calls = {"n": 0}
    running = asyncio.Event()

    async def factory() -> None:
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RuntimeError("boom")  # crash twice
        running.set()
        await asyncio.Event().wait()  # then run forever

    task = asyncio.create_task(supervisor.run("worker", factory))
    try:
        await asyncio.wait_for(running.wait(), timeout=2.0)
        assert calls["n"] == 3  # two crashes, then a healthy restart
        assert supervisor.crashes["worker"] == 2
        assert supervisor.warnings() and "worker" in supervisor.warnings()[0]
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_supervisor_treats_cancellation_as_shutdown_not_a_crash() -> None:
    supervisor = Supervisor(backoff_base=0.0)
    started = asyncio.Event()

    async def factory() -> None:
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(supervisor.run("w", factory))
    await asyncio.wait_for(started.wait(), timeout=2.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert supervisor.crashes.get("w", 0) == 0  # a cancel is not a crash → no restart


# -- store operational error mid-batch (F11) -----------------------------------------


async def test_engine_survives_store_operational_error_without_breaking_chain(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = Engine(store, asyncio.Queue())
    await engine.start()
    # Seed a real audit row so there is a chain to protect.
    async with store.lock:
        await audit.write_event(
            store,
            ts=1.0,
            actor="system",
            role=None,
            source_ip=None,
            action="ingest.gap",
            outcome="ok",
            object_type="ingest_gap",
            details={},
        )
        await store.commit()

    real_commit = store.commit
    calls = {"n": 0}

    async def flaky_commit() -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        await real_commit()

    monkeypatch.setattr(store, "commit", flaky_commit)
    committed = await engine._commit_batch([util.event(device="10.0.0.9", ts=100.0)])
    assert committed is False
    assert engine.db_errors == 1
    assert engine.db_error_warnings()  # surfaced to the operator

    # The chain never advanced on the failed batch, so it still verifies.
    result = await audit.verify_chain(store)
    assert result.ok and result.checked == 1


# -- graceful shutdown drain (§A.5) --------------------------------------------------


async def test_graceful_drain_processes_queued_traps_and_chain_verifies(store: Store) -> None:
    engine = Engine(store, asyncio.Queue())
    await engine.start()
    for i in range(5):
        engine.queue.put_nowait(util.event(device=f"10.0.0.{i}", ts=100.0 + i))
    drained = await engine.drain(deadline_s=5.0)
    assert drained == 5
    assert engine.queue.empty()
    stats = await store.stats()
    assert stats["devices"] == 5
    assert (await audit.verify_chain(store)).ok  # shutdown left a consistent chain


async def test_drain_respects_its_deadline(store: Store) -> None:
    engine = Engine(store, asyncio.Queue())
    await engine.start()
    for _ in range(3):
        engine.queue.put_nowait(util.event(ts=100.0))
    drained = await engine.drain(deadline_s=0.0)  # no time budget → nothing drained
    assert drained == 0
    assert engine.queue.qsize() == 3
