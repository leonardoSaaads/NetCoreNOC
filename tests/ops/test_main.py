from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from netcorenoc.ingest.events import QuarantinedPacket
from netcorenoc.ingest.receiver import QueueItem
from netcorenoc.main import GAP_CLOSE_S, Engine, FlapDetector, GapTracker, Settings
from netcorenoc.store import Store

import util
from util import run_engine_until


def test_settings_from_env_defaults() -> None:
    settings = Settings.from_env()
    assert settings.trap_port == 162
    assert settings.allowlist == ""


def test_settings_from_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NETCORENOC_TRAP_PORT", "1162")
    monkeypatch.setenv("NETCORENOC_ALLOWLIST", "10.0.0.0/8")
    settings = Settings.from_env()
    assert settings.trap_port == 1162
    assert settings.allowlist == "10.0.0.0/8"


async def test_f26_legacy_env_prefix_is_a_startup_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """F26 / DECISIONS #45: the OPTICORR_* aliases were removed in v0.6.0. Setting one is a hard
    startup error naming every offending variable and its NETCORENOC_* replacement — never a
    silent no-op, because an ignored OPTICORR_ALLOWLIST means every trap source is accepted."""
    from netcorenoc.main import LegacyEnvRemovedError, run

    monkeypatch.delenv("NETCORENOC_TRAP_PORT", raising=False)
    monkeypatch.setenv("OPTICORR_TRAP_PORT", "1162")
    monkeypatch.setenv("OPTICORR_ALLOWLIST", "10.0.0.0/8")
    settings = Settings.from_env()
    assert settings.legacy_env == ("OPTICORR_ALLOWLIST", "OPTICORR_TRAP_PORT")
    assert settings.trap_port == 162  # the alias is NOT honoured: the coded default stands

    with pytest.raises(LegacyEnvRemovedError) as caught:
        await run(Settings(db_path=str(tmp_path / "x.db"), legacy_env=settings.legacy_env))
    message = str(caught.value)
    for name in (
        "OPTICORR_TRAP_PORT",
        "NETCORENOC_TRAP_PORT",
        "OPTICORR_ALLOWLIST",
        "NETCORENOC_ALLOWLIST",
        "MIGRATION.md",
    ):
        assert name in message, message


async def test_f26_legacy_env_error_names_no_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """F26: the error names variables, never their values — an allowlist or a TLS path is
    deployment detail that must not be echoed to a console or a log."""
    from netcorenoc.main import LegacyEnvRemovedError, run

    monkeypatch.setenv("OPTICORR_ALLOWLIST", "10.9.9.0/24")
    monkeypatch.setenv("OPTICORR_TLS_KEY", "/secret/path/server.key")
    with pytest.raises(LegacyEnvRemovedError) as caught:
        await run(
            Settings(db_path=str(tmp_path / "x.db"), legacy_env=Settings.from_env().legacy_env)
        )
    message = str(caught.value)
    assert "10.9.9.0/24" not in message
    assert "/secret/path/server.key" not in message


def test_no_legacy_env_names_when_none_are_set(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.startswith("OPTICORR_"):
            monkeypatch.delenv(name, raising=False)
    assert Settings.from_env().legacy_env == ()


async def test_gap_tracker_opens_persists_and_audits(store: Store) -> None:
    """§5.6: a gap opens on the first drop, stays open while drops continue, and closes
    (persisting an ingest_gap row) only after GAP_CLOSE_S with no further drops."""
    tracker = GapTracker()
    tracker.observe("queue_full", 40, now=100.0)
    assert tracker.snapshot() and tracker.snapshot()[0]["dropped"] == 40
    async with store.lock:
        assert await tracker.flush(store, now=105.0) == []  # still within GAP_CLOSE_S
        tracker.observe("queue_full", 10, now=106.0)  # more drops extend the gap
        closed = await tracker.flush(store, now=106.0 + GAP_CLOSE_S + 1)
        await store.commit()
    assert len(closed) == 1 and closed[0][0] == "queue_full"
    gaps = await store.list_ingest_gaps(10)
    assert len(gaps) == 1
    assert gaps[0]["dropped"] == 50 and gaps[0]["reason"] == "queue_full"
    assert gaps[0]["started_at"] == 100.0 and gaps[0]["ended_at"] == 106.0
    assert not tracker.snapshot()  # closed, no longer open


async def test_engine_maintenance_records_queue_full_gap(store: Store) -> None:
    """The engine folds the receiver's cumulative drop counter into a durable gap and
    audits it as a system action, without touching the trap path."""
    engine = Engine(store, asyncio.Queue())
    await engine.start()
    dropped = {"n": 0}
    engine.dropped_provider = lambda: dropped["n"]

    dropped["n"] = 25  # the receiver shed 25 traps under load
    await engine.maintenance(now=1000.0, retention_days=365.0)
    assert engine.gap.snapshot()[0]["dropped"] == 25  # gap open, surfaced live

    await engine.maintenance(now=1000.0 + GAP_CLOSE_S + 1, retention_days=365.0)  # no new drops
    gaps = await store.list_ingest_gaps(10)
    assert len(gaps) == 1 and gaps[0]["dropped"] == 25 and gaps[0]["reason"] == "queue_full"
    async with store.lock:
        cur = await store.conn.execute(
            "SELECT actor, outcome FROM audit_log WHERE action='ingest.gap'"
        )
        rows = list(await cur.fetchall())
    assert rows and rows[0]["actor"] == "system" and rows[0]["outcome"] == "ok"


def test_flap_detector_flags_periodic_reactivation() -> None:
    detector = FlapDetector()
    fingerprint = ("10.0.0.1", "1.3.6.1.6.3.1.1.5.3", "7")
    verdicts = [detector.observe(fingerprint, ts=float(i * 30)) for i in range(8)]
    assert verdicts[-1] is True
    assert verdicts[0] is False


def test_flap_detector_ignores_irregular_raises() -> None:
    detector = FlapDetector()
    fingerprint = ("10.0.0.1", "1.3.6.1.6.3.1.1.5.3", "7")
    times = [0.0, 3.0, 400.0, 401.0, 800.0, 2000.0, 2001.0, 2500.0]
    assert not any(detector.observe(fingerprint, ts) for ts in times)


def test_flap_detector_resets_after_quiet_gap() -> None:
    detector = FlapDetector()
    fingerprint = ("10.0.0.1", "1.3.6.1.6.3.1.1.5.3", "7")
    for i in range(6):
        detector.observe(fingerprint, ts=float(i * 30))
    assert detector.observe(fingerprint, ts=30_000.0) is False
    assert len(detector.history[fingerprint]) == 1


def test_flap_detector_treats_simultaneous_burst_as_storm() -> None:
    detector = FlapDetector()
    fingerprint = ("10.0.0.1", "1.3.6.1.4.1.9.1", "")
    assert not any(detector.observe(fingerprint, ts=5.0) for _ in range(10))


async def test_engine_persists_dedups_and_quarantines(store: Store) -> None:
    queue: asyncio.Queue[QueueItem] = asyncio.Queue()
    engine = Engine(store, queue)
    queue.put_nowait(util.event(device="10.0.0.1", ts=100.0))
    queue.put_nowait(util.event(device="10.0.0.1", ts=101.0))
    queue.put_nowait(util.event(device="10.0.0.2", trap_oid=util.HUAWEI_TRAP, ts=102.0))
    queue.put_nowait(QuarantinedPacket(source="10.9.9.9", raw=b"z", reason="r", ts=103.0))
    await run_engine_until(engine, queue, count=3)
    stats = await store.stats()
    assert stats["devices"] == 2
    assert stats["active_alarms"] == 2
    assert stats["quarantined"] == 1
    cur = await store.conn.execute("SELECT count FROM alarm ORDER BY id LIMIT 1")
    row = await cur.fetchone()
    assert row is not None and row["count"] == 2
    assert engine.latency_p95() > 0.0


async def test_engine_demotes_flapping_fingerprint(store: Store) -> None:
    queue: asyncio.Queue[QueueItem] = asyncio.Queue()
    engine = Engine(store, queue)
    engine.flap.min_raises = 3
    for cycle in range(4):
        queue.put_nowait(util.event(ts=1000.0 + cycle * 30))
        await run_engine_until(engine, queue, count=cycle + 1)
        await store.conn.execute("UPDATE alarm SET status='cleared'")
        await store.commit()
    cur = await store.conn.execute("SELECT is_flapping FROM alarm")
    row = await cur.fetchone()
    assert row is not None and row["is_flapping"] == 1
