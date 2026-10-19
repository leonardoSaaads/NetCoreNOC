"""ADRs #447, #451 — each trap is timed from **its own** arrival, against a KPI an admin sets.

The KPI is a target (60 s by default) and a limit (120 s): a trap past either is still classified —
nothing here expires a trap — and it is counted, so the Overview can say how many traps fell outside
the KPI, how late, and in which of Zabbix's delay buckets. These tests hold the appliance to
measuring that from each trap's own `ts` to the commit that placed it, to never discarding a late
trap, and to letting an administrator change the KPI without a restart.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import time

import pytest

from netcorenoc.crosscutting.settings import Settings, SettingsError
from netcorenoc.engine.operate import engine as engine_module
from netcorenoc.engine.operate.deadline import (
    DELAY_EDGES,
    KPI_KEY,
    LIMIT_S,
    TARGET_S,
    DeadlineTracker,
    Kpi,
)
from netcorenoc.main import Engine
from netcorenoc.store import Store

import authutil
import util


def test_each_trap_is_timed_from_its_own_arrival_against_the_target_and_the_limit() -> None:
    tracker = DeadlineTracker()
    now = 10_000.0
    traps = [util.event(ts=now - age) for age in (20.0, 70.0, 130.0)]
    tracker.committed(traps, now)
    assert tracker.classified == 3
    assert tracker.over_target == 2, "70 s and 130 s are past the 60 s target"
    assert tracker.over_limit == 1, "only 130 s is past the 120 s limit"
    assert tracker.worst_s == pytest.approx(130.0)
    assert tracker.percentile(0.0) == pytest.approx(20.0)


def test_the_delay_buckets_are_zabbixs() -> None:
    tracker = DeadlineTracker()
    ages = [1.0, 7.0, 25.0, 45.0, 90.0, 200.0, 500.0, 5000.0]
    tracker.committed([util.event(ts=100_000.0 - a) for a in ages], 100_000.0)
    assert DELAY_EDGES == (5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0)
    assert tracker.delays == [1, 1, 1, 1, 1, 1, 1, 1]
    report = tracker.report(0.0, 0)
    assert [d["upto_s"] for d in report["delays"]] == [*DELAY_EDGES, None]


def test_a_quarantined_packet_is_not_a_classified_trap() -> None:
    from netcorenoc.ingest.receiver import quarantine_packet

    tracker = DeadlineTracker()
    tracker.committed([quarantine_packet("10.0.0.1", b"\x30\x00", "ber-decode-failed", 1.0)], 9.0)
    assert tracker.classified == 0


def test_the_banner_is_for_the_limit_and_for_drops_not_for_the_target() -> None:
    tracker = DeadlineTracker()
    now = 1_000.0
    tracker.committed([util.event(ts=now - 90.0)], now)  # over the target, within the limit
    assert tracker.warnings(dropped=0) == [], "the target is a number on the Overview"
    tracker.committed([util.event(ts=now - 300.0)], now)
    (sentence,) = tracker.warnings(dropped=2)
    assert "1 took longer than the 120 s KPI limit" in sentence
    assert "classified late, not discarded" in sentence and "2 were dropped" in sentence


def test_an_interval_is_taken_and_a_new_one_begins() -> None:
    tracker = DeadlineTracker()
    assert tracker.take_interval() == {
        "classified": 0,
        "over_target": 0,
        "over_limit": 0,
        "p95_s": None,
        "max_s": None,
    }, "an idle interval has no latency, not a zero one"
    tracker.committed([util.event(ts=50.0 - a) for a in (1.0, 2.0, 200.0)], 50.0)
    taken = tracker.take_interval()
    assert taken["classified"] == 3 and taken["over_limit"] == 1
    assert taken["max_s"] == pytest.approx(200.0) and taken["p95_s"] == pytest.approx(200.0)
    assert tracker.take_interval()["classified"] == 0 and tracker.classified == 3


def test_the_report_states_compliance_and_the_kpi_in_force() -> None:
    tracker = DeadlineTracker(kpi=Kpi(30.0, 90.0))
    assert tracker.report(0.0, 0)["within_target_pct"] is None, "no trap, no share"
    tracker.committed([util.event(ts=100.0 - a) for a in (10.0, 40.0, 100.0, 5.0)], 100.0)
    report = tracker.report(12.5, 3)
    assert report["target_s"] == 30.0 and report["limit_s"] == 90.0
    assert report["within_target_pct"] == 50.0 and report["within_limit_pct"] == 75.0
    assert report["oldest_wait_s"] == 12.5 and report["dropped"] == 3


@pytest.mark.parametrize(
    ("target", "limit", "problem"),
    [
        (0.0, 60.0, "target must be above 0"),
        (60.0, 86_401.0, "limit must be above 0"),
        (math.nan, 60.0, "target must be above 0"),
        (120.0, 60.0, "cannot be later than the limit"),
    ],
)
def test_a_kpi_that_measures_nothing_is_refused(target: float, limit: float, problem: str) -> None:
    assert any(problem in p for p in Kpi.problems(target, limit))


def test_a_stored_kpi_round_trips_and_a_damaged_one_falls_back() -> None:
    default = Kpi()
    assert (default.target_s, default.limit_s) == (TARGET_S, LIMIT_S) == (60.0, 120.0)
    assert Kpi.from_document(Kpi(45.0, 90.0).to_document(), default) == Kpi(45.0, 90.0)
    for damaged in (None, "", "not json", '{"target_s": 10}', '{"target_s": 90, "limit_s": 30}'):
        assert Kpi.from_document(damaged, default) == default


async def test_a_trap_far_past_the_limit_is_still_classified(store: Store) -> None:
    """The KPI measures; it never discards. A trap ten minutes old is placed like any other."""
    engine = Engine(store, asyncio.Queue())
    await engine.start()
    stale = util.event(device="10.9.9.9", ts=time.time() - 600.0)
    assert await engine._commit_batch([stale])
    assert engine.deadlines.classified == 1 and engine.deadlines.over_limit == 1
    async with store.lock:
        cur = await store.conn.execute("SELECT COUNT(*) FROM alarm")
        row = await cur.fetchone()
    assert row is not None and row[0] == 1


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
    assert engine.deadlines.over_target == 0 and engine.latency_p95() < TARGET_S


async def test_stats_publish_the_pipeline(store: Store) -> None:
    engine, _queue, app = await authutil.make_env(store)
    engine.deadlines.committed([util.event(ts=time.time() - 1.0)], time.time())
    viewer = await authutil.client_as(app, "viewer")
    try:
        stats = (await viewer.get("/api/stats")).json()
    finally:
        await viewer.aclose()
    clock = stats["classification"]
    assert clock["target_s"] == TARGET_S and clock["limit_s"] == LIMIT_S
    assert clock["classified"] == 1 and clock["within_limit_pct"] == 100.0
    assert set(clock) == {
        "target_s",
        "limit_s",
        "classified",
        "over_target",
        "over_limit",
        "within_target_pct",
        "within_limit_pct",
        "dropped",
        "p50_s",
        "p95_s",
        "p99_s",
        "worst_s",
        "oldest_wait_s",
        "delays",
        "backlog",
        "queue",
        "spool",
    }
    assert clock["spool"] is None, "no spool in a test app: said so, not invented"
    assert stats["queue_depth"] == clock["backlog"]


async def test_an_admin_sets_the_kpi_and_it_applies_without_a_restart(store: Store) -> None:
    engine, _queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")
    try:
        config = (await admin.get("/api/config")).json()
        assert config["kpi"] == {"target_s": 60.0, "limit_s": 120.0}
        assert config["precedence"]["kpi"]["override"] is None
        body = {"allowlist": config["allowlist"], "retention_days": config["retention_days"]}
        resp = await admin.post("/api/config", json={**body, "kpi_target_s": 45, "kpi_limit_s": 90})
        assert resp.status_code == 200, resp.text
        assert engine.deadlines.kpi == Kpi(45.0, 90.0)
        again = (await admin.get("/api/config")).json()
        assert again["kpi"] == {"target_s": 45.0, "limit_s": 90.0}
        assert again["precedence"]["kpi"] == {
            "env": {"target_s": 60.0, "limit_s": 120.0},
            "override": {"target_s": 45.0, "limit_s": 90.0},
        }
        for bad in ({"kpi_target_s": 45}, {"kpi_target_s": 90, "kpi_limit_s": 45}):
            assert (await admin.post("/api/config", json={**body, **bad})).status_code == 422
        unchanged = await admin.post("/api/config", json=body)  # the KPI omitted: left as it is
        assert unchanged.status_code == 200 and engine.deadlines.kpi == Kpi(45.0, 90.0)
    finally:
        await admin.aclose()
    async with store.lock:
        assert Kpi.from_document(await store.get_meta(KPI_KEY), Kpi()) == Kpi(45.0, 90.0)
        rows = [r for r in await store.audit_all() if r["action"] == "config.change"]
    assert any('"kpi"' in str(r["details"]) for r in rows), "the change is audited"
    viewer = await authutil.client_as(app, "viewer")
    try:
        assert (await viewer.post("/api/config", json=body)).status_code == 403
    finally:
        await viewer.aclose()


@pytest.mark.parametrize("name", ["KPI_TARGET_S", "KPI_LIMIT_S", "SPOOL_MAX_MB"])
@pytest.mark.parametrize("bad", ["0", "-5", "nan", "inf", "soon"])
def test_the_pipeline_settings_are_refused_by_name(
    monkeypatch: pytest.MonkeyPatch, name: str, bad: str
) -> None:
    monkeypatch.setenv(f"NETCORENOC_{name}", bad)
    with pytest.raises(SettingsError, match=f"NETCORENOC_{name}"):
        Settings.from_env()


def test_a_target_later_than_the_limit_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NETCORENOC_KPI_TARGET_S", "300")
    monkeypatch.setenv("NETCORENOC_KPI_LIMIT_S", "120")
    with pytest.raises(SettingsError, match="later than"):
        Settings.from_env()


def test_the_pipeline_settings_have_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    for name in ("KPI_TARGET_S", "KPI_LIMIT_S", "INGEST_PROCESSES", "SPOOL_DIR", "SPOOL_MAX_MB"):
        monkeypatch.delenv(f"NETCORENOC_{name}", raising=False)
    # ADR #452: the spool's default is in the data directory, where it was beside the .db file.
    monkeypatch.delenv("NETCORENOC_DB", raising=False)
    monkeypatch.setenv("NETCORENOC_DATA_DIR", "/var/lib/netcorenoc")
    settings = Settings.from_env()
    assert (settings.kpi_target_s, settings.kpi_limit_s) == (60.0, 120.0)
    assert settings.ingest_processes == 1 and settings.spool_max_mb == 2048.0
    assert str(settings.spool_path) == "/var/lib/netcorenoc/spool", "in the data directory"
    monkeypatch.setenv("NETCORENOC_SPOOL_DIR", "/srv/spool")
    assert str(Settings.from_env().spool_path) == "/srv/spool"
    monkeypatch.setenv("NETCORENOC_INGEST_PROCESSES", "2")
    with pytest.raises(SettingsError, match="NETCORENOC_INGEST_PROCESSES"):
        Settings.from_env()
