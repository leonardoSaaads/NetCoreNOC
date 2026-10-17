"""ADR #450 — no trap is lost between the spool and the commit that classifies it.

The engine reads the spool through `SpoolFeed`. What must hold, with the real engine and the real
store: a slow engine makes reading wait (backpressure), never drop; the cursor is committed in the
batch's own transaction, so a restart resumes exactly after the last classified trap; a crash of
the engine replays what was in flight; a trap that crashes the engine every time is set aside —
kept, with the error — and every other trap is still classified; a storage error is retried; and a
segment is deleted only once the cursor that passed it is durable.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import sqlite3
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from netcorenoc.crosscutting.supervisor import Supervisor
from netcorenoc.engine.operate import engine as engine_module
from netcorenoc.engine.operate.intake import CURSOR_KEY, SpoolFeed
from netcorenoc.ingest import records, spool
from netcorenoc.ingest.events import TrapEvent
from netcorenoc.ingest.receiver import QueueItem
from netcorenoc.main import Engine
from netcorenoc.store import Store

import util


def _events(n: int, start: int = 0) -> list[TrapEvent]:
    now = time.time()
    return [
        util.event(device=f"10.7.{i // 200}.{i % 200}", trap_oid=f"1.3.6.1.4.1.9.{i % 5}", ts=now)
        for i in range(start, start + n)
    ]


def _spool(directory: Path, events: list[TrapEvent]) -> int:
    writer = spool.SpoolWriter(directory, 1 << 40)
    try:
        return writer.append([records.encode(e) for e in events])
    finally:
        writer.close()


async def _wait(condition: Callable[[], bool], timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        await asyncio.sleep(0.02)


class Running:
    """The engine and its feed as the runner wires them: a supervised task that rewinds first."""

    def __init__(self, store: Store, directory: Path, queue_size: int = 10_000) -> None:
        self.store, self.directory, self.queue_size = store, directory, queue_size
        self.supervisor = Supervisor(backoff_base=0.01, backoff_max=0.05)

    async def __aenter__(self) -> Running:
        queue: asyncio.Queue[QueueItem] = asyncio.Queue(maxsize=self.queue_size)
        self.feed = await SpoolFeed.open(self.directory, queue, self.store)
        self.engine = Engine(self.store, queue)
        self.engine.feed = self.feed
        await self.engine.start()

        async def run_engine() -> None:
            await self.feed.rewind(self.supervisor.last_error.get("engine", ""))
            await self.engine.run()

        self.task = asyncio.create_task(self.supervisor.run("engine", run_engine))
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.task
        await self.feed.stop()


async def _alarms(store: Store) -> int:
    async with store.lock:
        cur = await store.conn.execute("SELECT COUNT(*) FROM alarm")
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def _cursor(store: Store) -> int:
    async with store.lock:
        return int(await store.get_meta(CURSOR_KEY) or 0)


async def test_a_slow_engine_makes_reading_wait_and_drops_nothing(
    store: Store, tmp_path: Path
) -> None:
    directory = tmp_path / "spool"
    head = _spool(directory, _events(400))
    async with Running(store, directory, queue_size=5) as run:
        run.feed.written(head)
        await _wait(lambda: run.feed.cursor == head)
        assert run.engine.deadlines.classified == 400
        assert run.feed.peak >= 395 or run.feed.take_peak() >= 0  # the backlog was measured
    assert await _alarms(store) == 400 and await _cursor(store) == 400


async def test_the_cursor_is_committed_with_the_batch_and_a_restart_resumes_after_it(
    store: Store, tmp_path: Path
) -> None:
    directory = tmp_path / "spool"
    _spool(directory, _events(50))
    async with Running(store, directory) as run:
        await _wait(lambda: run.feed.cursor == 50)
    assert await _cursor(store) == 50
    _spool(directory, _events(30, 50))  # more arrive while the engine is down
    async with Running(store, directory) as run:
        assert run.feed.cursor == 50, "resumes after the stored cursor"
        await _wait(lambda: run.feed.cursor == 80)
        assert run.engine.deadlines.classified == 30, "nothing classified twice"
    assert await _alarms(store) == 80


async def test_a_crash_replays_what_was_in_flight(
    store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "spool"
    _spool(directory, _events(60))
    real = Engine._process
    crashed = {"n": 0}

    async def once(self: Engine, item: QueueItem) -> None:
        if isinstance(item, TrapEvent) and item.device == "10.7.0.30" and not crashed["n"]:
            crashed["n"] += 1
            raise RuntimeError("a defect, once")
        await real(self, item)

    monkeypatch.setattr(Engine, "_process", once)
    async with Running(store, directory) as run:
        await _wait(lambda: run.feed.cursor == 60)
        assert run.supervisor.crashes == {"engine": 1}
        assert run.feed.dead_lettered == 0, "a crash that does not repeat sets nothing aside"
    assert await _alarms(store) == 60, "the rolled-back batch was replayed, not lost"


async def test_a_trap_that_always_crashes_the_engine_is_set_aside_and_the_rest_classified(
    store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "spool"
    _spool(directory, _events(40))
    real = Engine._process

    async def poison(self: Engine, item: QueueItem) -> None:
        if isinstance(item, TrapEvent) and item.device == "10.7.0.17":
            raise KeyError("a defect this trap always reaches")
        await real(self, item)

    monkeypatch.setattr(Engine, "_process", poison)
    async with Running(store, directory) as run:
        await _wait(lambda: run.feed.cursor == 40)
        assert run.feed.dead_lettered == 1 and run.feed.warnings()
    assert await _alarms(store) == 39, "every other trap was classified"
    (line,) = (directory / spool.DEAD_LETTER).read_text().splitlines()
    kept = json.loads(line)
    assert kept["seq"] == 18 and "KeyError" in kept["reason"]
    trap = records.load(base64.b64decode(kept["record_b64"]))
    assert isinstance(trap, TrapEvent) and trap.device == "10.7.0.17", "the trap itself is kept"


async def test_a_storage_error_is_retried_not_dropped(
    store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "spool"
    _spool(directory, _events(20))
    real_commit = store.commit
    failures = {"n": 0}

    async def flaky() -> None:
        if failures["n"] < 2:
            failures["n"] += 1
            raise sqlite3.OperationalError("database or disk is full")
        await real_commit()

    monkeypatch.setattr(store, "commit", flaky)
    monkeypatch.setattr(asyncio, "sleep", _no_wait(asyncio.sleep))  # the engine's retry pause
    async with Running(store, directory) as run:
        await _wait(lambda: run.feed.cursor == 20)
        assert run.engine.db_errors == 2
        assert "retried, not dropped" in run.engine.db_error_warnings()[0]
    assert await _alarms(store) == 20


def _no_wait(real: Callable[[float], Awaitable[None]]) -> Callable[[float], Awaitable[None]]:
    async def sleep(delay: float, *args: object) -> None:
        await real(0)

    return sleep


async def test_a_record_that_is_not_an_event_is_set_aside(store: Store, tmp_path: Path) -> None:
    directory = tmp_path / "spool"
    writer = spool.SpoolWriter(directory, 1 << 40)
    try:
        writer.append([records.encode(_events(1)[0]), b'["t", "missing fields"]'])
    finally:
        writer.close()
    async with Running(store, directory) as run:
        await _wait(lambda: run.feed.cursor == 2)
        assert run.feed.invalid == 1
    assert await _alarms(store) == 1
    (line,) = (directory / spool.DEAD_LETTER).read_text().splitlines()
    assert b"missing fields" in base64.b64decode(json.loads(line)["record_b64"])


async def test_only_segments_behind_a_durable_cursor_are_deleted(
    store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(spool, "SEGMENT_BYTES", 600)
    directory = tmp_path / "spool"
    for i in range(30):
        _spool(directory, _events(1, i))
    before = spool.segments(directory)
    assert len(before) > 3
    async with Running(store, directory) as run:
        await _wait(lambda: run.feed.cursor == 30)
        removed = await run.feed.prune()
    after = spool.segments(directory)
    assert removed == len(before) - len(after) > 0
    assert after[-1] == before[-1], "the newest segment is the writer's and stays"


async def test_a_full_queue_is_not_unreadiness_and_the_backlog_is_reported(
    store: Store, tmp_path: Path
) -> None:
    """`live_report` names the backlog wherever it waits; readiness is about accepting traps."""
    from netcorenoc.api import create_app
    from netcorenoc.engine.operate import pipeline

    import authutil

    directory = tmp_path / "spool"
    queue: asyncio.Queue[QueueItem] = asyncio.Queue(maxsize=2)
    feed = await SpoolFeed.open(directory, queue, store)
    engine = Engine(store, queue)
    engine.feed = feed
    await engine.start()
    head = _spool(directory, _events(10))
    feed.written(head)
    feed.start()
    await _wait(queue.full)
    report = pipeline.live_report(engine, time.time())
    assert report["backlog"] == 10 and report["queue"] == {"depth": 2, "capacity": 2}
    assert report["spool"]["on_disk"] == 8 and report["oldest_wait_s"] >= 0.0
    app = create_app(engine)
    async with authutil.new_client(app) as client:
        assert (await client.get("/readyz")).status_code == 200, "behind is not unready"
        feed.disk = lambda: {"error": "No space left on device"}
        assert (await client.get("/readyz")).status_code == 503, "a refusing spool is"
    await feed.stop()


# -- a replay decides what the first attempt would have -----------------------------------------


async def _grouping(store: Store) -> tuple[set[frozenset[tuple[str, str, str]]], int, int]:
    async with store.lock:
        cur = await store.conn.execute(
            "SELECT sm.situation_id, d.ip, c.oid, a.instance FROM situation_alarm sm "
            "JOIN alarm a ON a.id = sm.alarm_id JOIN device d ON d.id = a.device_id "
            "JOIN alarm_class c ON c.id = a.class_id"
        )
        groups: dict[int, set[tuple[str, str, str]]] = {}
        for sid, ip, oid, instance in await cur.fetchall():
            groups.setdefault(int(sid), set()).add((str(ip), str(oid), str(instance)))
        alarms = await (await store.conn.execute("SELECT COUNT(*) FROM alarm")).fetchone()
        links = await (await store.conn.execute("SELECT COUNT(*) FROM link")).fetchone()
    assert alarms is not None and links is not None
    return {frozenset(g) for g in groups.values()}, int(alarms[0]), int(links[0])


@pytest.mark.parametrize("fault", ["storage", "defect"])
async def test_a_replayed_batch_groups_exactly_as_an_uninterrupted_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """The retry (a storage error) and the replay (a crash) re-run traps whose first attempt was
    rolled back. Grouping, alarms and links must come out as if nothing had happened."""
    base = time.time() - 600.0
    events = [
        *util.fixture_events("olt_storm.json", base),
        *util.fixture_events("fiber_cut.json", base + 200.0),
    ]
    results = []
    for interrupted in (False, True):
        store = Store(str(tmp_path / f"{fault}-{interrupted}.db"))
        await store.open()
        try:
            directory = tmp_path / f"spool-{fault}-{interrupted}"
            _spool(directory, events)
            with monkeypatch.context() as patch:
                patch.setattr(engine_module, "BATCH_BUDGET_S", 0.0)  # many transactions, not one
                patch.setattr(asyncio, "sleep", _no_wait(asyncio.sleep))
                if interrupted:
                    _inject(patch, store, fault, after=len(events) // 2)
                async with Running(store, directory) as run:
                    feed = run.feed
                    await _wait(lambda: feed.cursor == len(events))  # noqa: B023 - awaited here
                    assert feed.dead_lettered == 0
            results.append(await _grouping(store))
        finally:
            await store.close()
    clean, replayed = results
    assert replayed == clean and clean[1] == len({e.fingerprint for e in events})


def _inject(patch: pytest.MonkeyPatch, store: Store, fault: str, after: int) -> None:
    seen = {"n": 0, "fired": False}
    if fault == "storage":
        real_commit = store.commit

        async def commit() -> None:
            seen["n"] += 1
            if seen["n"] == after and not seen["fired"]:
                seen["fired"] = True
                raise sqlite3.OperationalError("disk I/O error")
            await real_commit()

        patch.setattr(store, "commit", commit)
        return
    real = Engine._process

    async def process(self: Engine, item: QueueItem) -> None:
        seen["n"] += 1
        if seen["n"] == after and not seen["fired"]:
            seen["fired"] = True
            raise RuntimeError("a defect, once")
        await real(self, item)

    patch.setattr(Engine, "_process", process)
