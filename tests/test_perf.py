"""Phase 4.7 — ingestion stays lossless and bounded with auth + audit active.

A full 1000 traps/s x 60 s UDP load test is `make loadtest`; this in-process regression
proves the invariant that matters for v0.2.0: authenticated, audited API traffic hitting
the store lock concurrently must not make the engine drop or stall trap ingestion, and
mutating API calls must still write their audit rows during the storm.
"""

from __future__ import annotations

import asyncio
import contextlib
import time

from opticorr.store import Store

import authutil
import util

EVENTS = 6000


async def test_ingestion_lossless_with_concurrent_authed_audited_api(store: Store) -> None:
    engine, queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")

    events = [
        util.event(
            device=f"10.2.0.{i % 40}",
            trap_oid=f"1.3.6.1.4.1.9.9.55.{i % 25}",
            instance=str(i % 4),
            ts=3_000_000.0 + i * 0.001,
        )
        for i in range(EVENTS)
    ]
    for e in events:
        queue.put_nowait(e)

    start = time.perf_counter()
    engine_task = asyncio.create_task(engine.run())
    api_latencies: list[float] = []
    label_writes = 0
    try:
        # Hammer read + mutating (audited) endpoints while the engine drains the queue.
        while engine.processed < EVENTS or not queue.empty():
            assert not engine_task.done(), engine_task.exception()
            t0 = time.perf_counter()
            r = await admin.get("/api/stats")
            api_latencies.append(time.perf_counter() - t0)
            assert r.status_code == 200
            lab = await admin.post("/api/labels", json={"kind": "device", "id": 1, "label": "x"})
            assert lab.status_code == 200
            label_writes += 1
            await asyncio.sleep(0)
        assert engine.processed == EVENTS  # zero loss: every trap processed
    finally:
        engine_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await engine_task
        await admin.aclose()

    elapsed = time.perf_counter() - start
    api_latencies.sort()
    p95 = api_latencies[min(len(api_latencies) - 1, int(0.95 * len(api_latencies)))]
    # Sanity envelope (generous; this is a correctness regression, not a benchmark).
    assert elapsed < 60.0
    assert p95 < 2.0

    # Audit rows for the mutating calls were written during the storm.
    async with store.lock:
        cur = await store.conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='label.set'")
        row = await cur.fetchone()
    assert row is not None and row[0] == label_writes

    # The trap path produced alarms throughout the storm.
    async with store.lock:
        cur = await store.conn.execute("SELECT COUNT(*) FROM alarm")
        row = await cur.fetchone()
    assert row is not None and row[0] > 0
