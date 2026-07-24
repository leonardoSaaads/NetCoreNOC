from __future__ import annotations

import asyncio

import pytest
from netcorenoc.events import QuarantinedPacket
from netcorenoc.main import GAP_CLOSE_S, Engine, FlapDetector, GapTracker, Settings
from netcorenoc.receiver import QueueItem
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


def test_settings_legacy_env_alias_still_works(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Rebrand (v0.4.0, DECISIONS #34): legacy OPTICORR_* env names are honoured for one
    version and emit a single deprecation warning per variable, naming the variable only."""
    import netcorenoc.main as main_mod

    main_mod._warned_legacy_env.clear()
    monkeypatch.delenv("NETCORENOC_TRAP_PORT", raising=False)
    monkeypatch.setenv("OPTICORR_TRAP_PORT", "1162")
    with caplog.at_level("WARNING", logger="netcorenoc"):
        settings = Settings.from_env()
        Settings.from_env()  # a second read must NOT warn again
    assert settings.trap_port == 1162
    warnings = [r for r in caplog.records if "OPTICORR_TRAP_PORT" in r.getMessage()]
    assert len(warnings) == 1
    assert "NETCORENOC_TRAP_PORT" in warnings[0].getMessage()  # points at the new name
    assert "1162" not in warnings[0].getMessage()  # never the value


def test_settings_new_env_takes_precedence_over_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    import netcorenoc.main as main_mod

    main_mod._warned_legacy_env.clear()
    monkeypatch.setenv("NETCORENOC_TRAP_PORT", "5000")
    monkeypatch.setenv("OPTICORR_TRAP_PORT", "1162")
    assert Settings.from_env().trap_port == 5000


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
