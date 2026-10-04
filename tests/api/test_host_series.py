"""The host series: readings persisted, and the range the Overview asks for reaching SQL (F154).

Item 5 of v0.22.0: the Overview's range control offered up to seven days and the four host charts
drew the same in-memory two hours whatever was picked. The repair has two halves and each has a
test here — the reading is written (`host_sample`, migration 0022), and a range is a different
query (`host_series(since, until)`), not a different drawing of the same rows.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from netcorenoc.store import Store
from netcorenoc.store.host_samples import HOST_SAMPLE_RETENTION_S

import authutil

NOW = 1_800_000_000.0
HOUR = 3600.0


async def _record(store: Store, at: float, cpu: float, queue: int = 0) -> None:
    async with store.lock:
        await store.record_host_sample(
            at, {"cpu_pct": cpu, "mem_pct": 50.0, "disk_pct": 10.0, "db_mb": 5.0}, queue
        )
        await store.commit()


async def test_the_range_reaches_the_query(store: Store) -> None:
    """A reading from three days ago is in the 7-day window and absent from the 2-hour one.

    This is the assertion F154 needed: with the old ring, both windows drew the same rows.
    """
    await _record(store, NOW - 3 * 24 * HOUR, 90.0)
    await _record(store, NOW - 10 * 60, 10.0)
    async with store.lock:
        short = await store.host_series(since=NOW - 2 * HOUR, until=NOW, buckets=12)
        long = await store.host_series(since=NOW - 7 * 24 * HOUR, until=NOW, buckets=7)
    assert short["samples"] == 1 and long["samples"] == 2
    assert 90.0 not in [v for v in short["series"]["cpu_pct"] if v is not None]
    assert 90.0 in long["series"]["cpu_pct"], long["series"]["cpu_pct"]


async def test_an_unsampled_bucket_is_null_and_a_queue_is_its_worst_moment(store: Store) -> None:
    for offset, (cpu, queue) in enumerate([(10.0, 3), (30.0, 40)]):
        await _record(store, NOW - HOUR + 60 + offset * 60, cpu, queue)
    async with store.lock:
        series = await store.host_series(since=NOW - HOUR, until=NOW, buckets=6)
    cpu = series["series"]["cpu_pct"]
    assert cpu[0] == 20.0, cpu  # the mean of the two readings in the first bucket
    assert cpu[1:] == [None] * 5, "an unsampled bucket was drawn as a value"
    assert series["series"]["queue_depth"][0] == 40, "a queue burst was averaged away"
    assert series["measured_from"] == NOW - HOUR + 60


async def test_a_reading_older_than_the_longest_range_is_pruned(store: Store) -> None:
    await _record(store, NOW - HOST_SAMPLE_RETENTION_S - 1, 1.0)
    await _record(store, NOW, 2.0)
    async with store.lock:
        cur = await store.conn.execute("SELECT COUNT(*) FROM host_sample")
        row = await cur.fetchone()
    assert row is not None and int(row[0]) == 1


async def test_the_route_answers_the_window_it_was_asked_for_on_a_bucket_boundary(
    store: Store,
) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    viewer = await authutil.client_as(app, "viewer")
    try:
        for range_s, buckets in ((900, 15), (7 * 24 * 3600, 28)):
            body = (
                await viewer.get("/api/resources", params={"range_s": range_s, "buckets": buckets})
            ).json()
            assert round(body["to"] - body["from"]) == range_s, body
            assert body["buckets"] == buckets
            # The end is a bucket boundary, so two polls inside one bucket read the same buckets.
            assert abs(body["to"] / body["bucket_s"] - round(body["to"] / body["bucket_s"])) < 1e-6
        clamped = (await viewer.get("/api/resources", params={"range_s": 10**9})).json()
        assert clamped["to"] - clamped["from"] <= HOST_SAMPLE_RETENTION_S + 1
    finally:
        await viewer.aclose()


async def test_the_work_series_rate_is_a_mean_and_latency_its_worst_moment(store: Store) -> None:
    """v0.29.0 (ADR #437): what the appliance is DOING, beside what the host is doing.

    The trap rate is averaged over a bucket like CPU; the correlation latency is read for its worst
    moment like the queue. A reading taken before the rate had two counts to difference stores
    NULL, and the bucket it falls in draws no value rather than a made-up zero.
    """
    host = {"cpu_pct": 5.0, "mem_pct": 50.0, "disk_pct": 10.0, "db_mb": 5.0}
    async with store.lock:
        await store.record_host_sample(NOW - HOUR + 30, host, 0)  # the first: no rate yet
        await store.record_host_sample(NOW - HOUR + 600, host, 0, traps_per_s=10.0, latency_ms=2.0)
        await store.record_host_sample(NOW - HOUR + 900, host, 0, traps_per_s=30.0, latency_ms=9.5)
        await store.commit()
        series = await store.host_series(since=NOW - HOUR, until=NOW, buckets=2)
    assert series["series"]["traps_per_s"] == [20.0, None], series["series"]["traps_per_s"]
    assert series["series"]["latency_ms"] == [9.5, None], "a latency spike was averaged away"
    async with store.lock:
        quiet = await store.host_series(since=NOW - HOUR, until=NOW - HOUR + 60, buckets=1)
    assert quiet["series"]["traps_per_s"] == [None], "a reading with no rate was drawn as zero"
    assert quiet["series"]["cpu_pct"] == [5.0], "the control: the host half of that row is read"


async def test_the_sampler_differences_the_receiver_counter_and_never_invents_a_first_rate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rate is a DIFFERENCE of the receiver's own counter over the interval, read and never
    written by the sampler; the first interval has nothing to difference and records no rate. A
    counter that went backwards (a restart of the receiver's stats) is not a negative rate."""
    from netcorenoc import runner

    clock = iter([100.0, 130.0, 160.0, 190.0])
    counts = iter([0, 300, 900, 50])
    written: list[dict[str, Any]] = []

    class Sampler:
        def sample(self) -> None: ...

        def reading(self) -> dict[str, Any]:
            return {"cpu_pct": 1.0}

    class FakeStore:
        lock = asyncio.Lock()

        async def record_host_sample(self, at: float, reading: Any, depth: int, **kw: Any) -> None:
            written.append({"at": at, **kw})
            if len(written) == 4:
                raise asyncio.CancelledError

        async def commit(self) -> None: ...

    monkeypatch.setattr(runner, "SAMPLE_INTERVAL_S", 0.0)
    monkeypatch.setattr("netcorenoc.runner.time.time", lambda: next(clock))
    with pytest.raises(asyncio.CancelledError):
        await runner._sample_resources(
            Sampler(),  # type: ignore[arg-type]
            FakeStore(),  # type: ignore[arg-type]
            asyncio.Queue(),
            received=lambda: next(counts),
            latency_p95_s=lambda: 0.0042,
        )
    assert [w["traps_per_s"] for w in written] == [None, 10.0, 20.0, None], written
    assert {w["latency_ms"] for w in written} == {4.2}
