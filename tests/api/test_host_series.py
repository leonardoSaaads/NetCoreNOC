"""The host series: readings persisted, and the range the Overview asks for reaching SQL (F154).

Item 5 of v0.22.0: the Overview's range control offered up to seven days and the four host charts
drew the same in-memory two hours whatever was picked. The repair has two halves and each has a
test here — the reading is written (`host_sample`, migration 0022), and a range is a different
query (`host_series(since, until)`), not a different drawing of the same rows.
"""

from __future__ import annotations

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
