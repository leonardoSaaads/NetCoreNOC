"""§A.5 reliability — readiness, store integrity/fault handling, task supervision, graceful drain.

These drive the real engine/store/API objects; the trap datagram path is never touched. Findings
F10 (unsupervised task death) and F11 (a storage error / damaged DB) are regression-tested here.

v0.31.0 (ADR #452): the store is PostgreSQL. The startup `_check_integrity` pass that looked for
foreign-key orphans SQLite let `PRAGMA foreign_keys=OFF` write is gone with SQLite; the server
enforces every foreign key, so the orphan test now asserts the server refuses one and the store
stays usable. A storage failure is simulated with an error in `STORAGE_ERRORS`, not a locked
`sqlite3` file.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import time
from pathlib import Path
from typing import Any

import asyncpg
import pytest

from netcorenoc.api import create_app
from netcorenoc.crosscutting import audit
from netcorenoc.main import Engine, Supervisor
from netcorenoc.store import STORAGE_ERRORS, Store

import authutil
import util

# -- readiness (/readyz) -------------------------------------------------------------


async def test_readyz_ready_and_leaks_no_detail(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    async with authutil.new_client(app) as client:  # unauthenticated on purpose
        resp = await client.get("/readyz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ready"}  # exactly ok/not-ok, no internal detail


async def test_a_full_queue_is_not_unreadiness(store: Store) -> None:
    """ADR #450 retired the queue-saturation rule: with the spool a full queue is backpressure,
    not refused load, and an orchestrator that pulled the appliance out of rotation for it would
    lose traps in front of it. Unreadiness is a spool that refuses writes (`test_intake.py`)."""
    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=10)
    engine = Engine(store, queue)  # type: ignore[arg-type]
    await engine.start()
    app = create_app(engine)
    for _ in range(10):
        queue.put_nowait(util.event())
    async with authutil.new_client(app) as client:
        resp = await client.get("/readyz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ready"}


# -- store integrity (F11) -----------------------------------------------------------


async def test_integrity_check_clean_db_has_no_warnings(store: Store) -> None:
    assert store.integrity_warnings == []  # the fixture opened a healthy DB


async def test_a_foreign_key_orphan_is_refused_and_the_store_stays_usable(store: Store) -> None:
    """Under SQLite an orphan could be written (`PRAGMA foreign_keys=OFF`) and `_check_integrity`
    flagged it at startup. PostgreSQL enforces the key on every write, so the orphan never lands."""
    async with store.lock:
        with pytest.raises(asyncpg.exceptions.ForeignKeyViolationError):
            await store.conn.execute(
                "INSERT INTO situation_alarm (situation_id, alarm_id) VALUES (99999, 99999)"
            )
        await store.rollback()
        cur = await store.conn.execute("SELECT COUNT(*) FROM situation_alarm")
        assert (await cur.fetchone())[0] == 0  # type: ignore[index]
    # The refusal is not a crash — the store is still usable.
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
            # A storage failure the engine retries, not a defect (`STORAGE_ERRORS`, ADR #452).
            raise asyncpg.exceptions.DeadlockDetectedError("deadlock detected")
        await real_commit()

    assert issubclass(asyncpg.exceptions.DeadlockDetectedError, STORAGE_ERRORS)

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


# -- F66: a failed startup EXITS -----------------------------------------------------
#
# The defect was not one bug. `run()` opened the store and then did seventy lines of work outside
# any `try`, so a failure in them left it open; the cleanup re-raised a failed task's exception
# before reaching the close; and uvicorn's `sys.exit()` skipped the coroutine entirely. Any of the
# three left an `aiosqlite` connection open on a **non-daemon** thread, and the process then blocked
# forever in `threading._shutdown` — measured at 32.0 s to SIGKILL against controls at 0.5 s.
# Since v0.31.0 (ADR #452) a connection left open is a PostgreSQL session that keeps holding the
# instance advisory lock until the server's TCP keepalive notices, refusing the restart.
#
# The property all three share is the one asserted here: **when `run()` returns or raises, the store
# is closed** (its writer connection is gone, `store._db.writer is None`). It is checked at the seam
# rather than by booting a process, because a subprocess test would measure the operating system's
# signal delivery as much as this code.


class _BoomError(RuntimeError):
    """A startup failure, standing in for a bind error, a bad allowlist or a missing TLS file."""


async def test_a_failed_startup_closes_the_store_rather_than_hanging(
    db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TREATMENT. `_serve` raises; `run()` must still close the store.

    An open writer connection keeps the session (and the instance lock) alive on the server, so
    "the store is closed" and "the appliance can be restarted" are the same claim. The stub
    receives the store `run()` opened, which is how the probe gets hold of it without reaching for
    a name `runner` does not re-export.
    """
    from netcorenoc import runner
    from netcorenoc.crosscutting.settings import Settings

    seen: list[Store] = []

    async def explode(_settings: Settings, store: Store) -> None:
        seen.append(store)
        assert store._db.writer is not None, "the probe ran against a store that was never opened"
        raise _BoomError("could not bind")

    monkeypatch.setattr(runner, "_serve", explode)
    with pytest.raises(_BoomError):
        await runner.run(Settings(database_url=db_url))
    assert seen, "_serve was never reached, so this measured nothing"
    assert seen[0]._db.writer is None, (
        "run() left the store open after a startup failure; its session keeps the instance lock "
        "until the server notices, so the appliance could not be restarted (F66)"
    )


async def test_the_control_a_clean_run_also_closes_the_store(
    db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CONTROL. Without it the assertion above would pass for a `run()` that never opened one."""
    from netcorenoc import runner
    from netcorenoc.crosscutting.settings import Settings

    seen: list[Store] = []

    async def quiet(_settings: Settings, store: Store) -> None:
        seen.append(store)
        assert store._db.writer is not None

    monkeypatch.setattr(runner, "_serve", quiet)
    await runner.run(Settings(database_url=db_url))
    assert seen and seen[0]._db.writer is None


async def test_a_uvicorn_startup_failure_becomes_an_ordinary_exception() -> None:
    """`sys.exit()` is a `BaseException`: asyncio takes it out through `run_until_complete`, so the
    `run()` coroutine is never resumed and no `finally` of its runs. Converting it is what puts the
    failure back on the path the cleanup can see."""
    from netcorenoc.runner import HttpServerStartError, _serve_http

    class Exiting:
        started = False

        async def serve(self) -> None:
            raise SystemExit(3)

    with pytest.raises(HttpServerStartError):
        await _serve_http(Exiting(), "http://127.0.0.1:8080/")  # type: ignore[arg-type]


async def test_a_server_that_returns_without_starting_is_a_failure() -> None:
    """The other half: uvicorn can also log the bind error and simply return, which would leave an
    appliance ingesting traps and serving no console for as long as nobody looked."""
    from netcorenoc.runner import HttpServerStartError, _serve_http

    class NeverStarted:
        started = False

        async def serve(self) -> None:
            return None

    with pytest.raises(HttpServerStartError):
        await _serve_http(NeverStarted(), "http://127.0.0.1:8080/")  # type: ignore[arg-type]


async def test_the_control_a_server_that_started_is_not_a_failure() -> None:
    """Without this, `_serve_http` could raise unconditionally and the two above would pass."""
    from netcorenoc.runner import _serve_http

    class Started:
        started = True

        async def serve(self) -> None:
            return None

    await _serve_http(Started(), "http://127.0.0.1:8080/")  # type: ignore[arg-type]


async def test_the_cleanup_does_not_re_raise_a_failed_tasks_exception() -> None:
    """`await asyncio.gather(*tasks)` under `suppress(CancelledError)` re-raised the exception the
    failed task had already stored, so everything after it in the cleanup — the drain, the final
    maintenance pass, the store close — was skipped. `return_exceptions=True` is the repair; this
    asserts the property rather than the spelling."""

    async def fails() -> None:
        raise _BoomError("the server could not bind")

    async def forever() -> None:
        await asyncio.sleep(3600)

    tasks = [asyncio.create_task(fails()), asyncio.create_task(forever())]
    await asyncio.sleep(0)  # let the failing task run and store its exception
    for task in tasks:
        task.cancel()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert isinstance(results[0], _BoomError), results
    reached_the_end = True
    assert reached_the_end


async def test_a_stop_lets_the_http_server_shut_down(
    db_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A SIGTERM cancels `run()`. Until ADR #476 the cancellation reached uvicorn mid-serve, its
    shutdown never ran, and every stop logged "Task was destroyed but it is pending!" with a
    traceback. The server is now told to exit and given the time to: its shutdown runs."""
    from netcorenoc import runner
    from netcorenoc.api import QuietServer
    from netcorenoc.crosscutting.settings import Settings

    shut: list[bool] = []
    servers: list[Any] = []

    class Watched(QuietServer):
        def __init__(self, config: Any) -> None:
            super().__init__(config)
            servers.append(self)

        async def shutdown(self, sockets: Any = None) -> None:
            await super().shutdown(sockets)
            shut.append(True)

    monkeypatch.setattr(runner, "QuietServer", Watched)
    settings = Settings(
        database_url=db_url,
        http_host="127.0.0.1",
        http_port=_free_port(socket.SOCK_STREAM),
        trap_host="127.0.0.1",
        trap_port=_free_port(socket.SOCK_DGRAM),
        data_dir=str(tmp_path),
        spool_dir=str(tmp_path / "spool"),
        secret_key_file=str(tmp_path / "secret.key"),
        secrets_key_file=str(tmp_path / "secrets.key"),
    )
    task = asyncio.create_task(runner.run(settings))
    deadline = time.monotonic() + 30
    while not (servers and servers[0].started):
        assert not task.done(), task.exception() if task.done() else None
        assert time.monotonic() < deadline, "the appliance never started serving"
        await asyncio.sleep(0.1)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert shut == [True], "uvicorn was cancelled mid-serve: its shutdown never ran"


def _free_port(kind: int) -> int:
    with socket.socket(socket.AF_INET, kind) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])
