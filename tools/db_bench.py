#!/usr/bin/env python3
"""Database benchmark: the store's write path, the console's reads, and both at once.

ADR #452. The instrument behind the PostgreSQL migration's "at least 30 % faster" claim, written to
run unchanged against any `Store`: it touches nothing but the store's own methods, the engine and
the HTTP API, so the same workload measures every backend. Four phases, one database::

    python tools/db_bench.py                       # a throwaway database, 20 000 alarms
    python tools/db_bench.py --traps 5000 --json out.json
    python tools/db_bench.py --db postgresql://user@host/empty_db   # an empty database

1. **samples** — seven days of 30 s host samples (the Overview's longest range), written in
   batches of 120 per commit, as rows per second;
2. **ingest** — a storm of distinct alarms (the expensive case: new fingerprints) put on the
   engine's queue at once and classified by the real engine with the packaged league, while
   ``--readers`` console clients poll the API with a fixed think time. Reported as traps per second,
   the main process's CPU seconds per 1 000 traps (what the database took off it), and each route's
   latency **under load**;
3. **reads** — every route again on the populated, idle database, ``--repeat`` times each;
4. **size** — the database's bytes on disk for what was written.

No UDP and no decoder: the traps go straight onto the engine's queue, so the numbers are the engine
and its database and not the network. The end-to-end KPI, with the receiver and the spool, is
``make storm``. The numbers are the host's; compare runs made on the same host.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import random
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from netcorenoc.api import create_app
from netcorenoc.crosscutting import auth
from netcorenoc.engine.operate.engine import Engine
from netcorenoc.ingest.events import TrapEvent, Varbind
from netcorenoc.ingest.receiver import QueueItem
from netcorenoc.store import Store

import pgtemp

TOKEN = "bench-" + "x" * 40  # nosec B105 - a throwaway database's bearer token
#: The console's reads, cheap and heavy: what an operator's screens poll.
ROUTES = (
    "/api/stats",
    "/api/situations?limit=100",
    "/api/resources?range_s=604800&buckets=48",
    "/api/resources?range_s=7200&buckets=24",
    "/api/timeline?range_s=3600&buckets=60",
    "/api/activity/top?range_s=7200",
    "/api/activity/lanes?range_s=3600",
    "/api/inventory",
    "/api/entities",
    "/api/notices",
)


def storm(traps: int, devices: int, classes: int, start: float, rate: float) -> list[TrapEvent]:
    """``traps`` distinct alarms from ``devices`` sources, ``rate`` per second from ``start``."""
    rng = random.Random(1)  # nosec B311 - synthetic traffic, not crypto
    out = []
    for i in range(traps):
        device, cls, port = rng.randrange(devices), rng.randrange(classes), rng.randrange(48)
        out.append(
            TrapEvent(
                device=f"127.0.1.{1 + device}",
                trap_oid=f"1.3.6.1.4.1.2011.5.25.{cls % 6}.{cls}",
                ts=start + i / rate,
                varbinds=[
                    Varbind(oid=f"1.3.6.1.2.1.2.2.1.1.{port}", kind="int", value=str(port)),
                    Varbind(oid="1.3.6.1.4.1.2011.5.25.99", kind="str", value=f"seq {i}"),
                ],
            )
        )
    return out


def summary(values: list[float]) -> dict[str, float]:
    """p50, p95, max and n of ``values`` in milliseconds."""
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    p95 = ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]
    return {
        "n": len(ordered),
        "p50_ms": round(statistics.median(ordered) * 1000, 2),
        "p95_ms": round(p95 * 1000, 2),
        "max_ms": round(ordered[-1] * 1000, 2),
    }


async def samples(store: Store, now: float, days: float = 7.0) -> dict[str, Any]:
    """``days`` of host samples (seven: the Overview's longest range), 120 per commit; rows per
    second."""
    reading = {"cpu_pct": 12.5, "mem_pct": 40.0, "disk_pct": 55.0, "db_mb": 120.0}
    pipeline = {"classified": 900, "over_target": 3, "over_limit": 0, "classified_per_s": 30.0}
    start = now - days * 24 * 3600
    rows = int(days * 24 * 3600 / 30)
    began = time.perf_counter()
    for i in range(rows):
        if i % 120 == 0 and i:
            async with store.lock:
                await store.commit()
        async with store.lock:
            await store.record_host_sample(
                start + i * 30, reading, i % 50, traps_per_s=4.0, latency_ms=12.0, pipeline=pipeline
            )
    async with store.lock:
        await store.commit()
    elapsed = time.perf_counter() - began
    return {"rows": rows, "seconds": round(elapsed, 2), "rows_per_s": round(rows / elapsed, 1)}


async def reader(
    client: httpx.AsyncClient, stop: asyncio.Event, think_s: float, seen: dict[str, list[float]]
) -> None:
    """Poll every route in turn until ``stop``; record each response's latency."""
    i = 0
    while not stop.is_set():
        route = ROUTES[i % len(ROUTES)]
        began = time.perf_counter()
        response = await client.get(route)
        seen.setdefault(route, []).append(time.perf_counter() - began)
        if response.status_code != 200:
            raise RuntimeError(f"{route}: HTTP {response.status_code}")
        i += 1
        await asyncio.sleep(think_s)


async def database_bytes(store: Store) -> int:
    """The schema's tables, indexes and TOAST on the server."""
    async with store.reading():
        return await store.database_bytes()


async def bench(args: argparse.Namespace, db: str) -> dict[str, Any]:
    store = Store(db)
    await store.open()
    queue: asyncio.Queue[QueueItem] = asyncio.Queue()
    engine = Engine(store, queue)
    await engine.start()
    async with store.lock:
        await store.create_token(auth.hash_token(TOKEN), "bench", "admin", "bench", 0.0)
        await store.commit()
    app = create_app(engine, rate_capacity=1e9, rate_refill=1e9)
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://bench.test",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    now = time.time()
    report: dict[str, Any] = {"db": pgtemp.redact(db), "traps": args.traps}
    report["samples"] = await samples(store, now, args.sample_days)

    traps = storm(args.traps, args.devices, args.classes, now - args.traps / 5000, 5000.0)
    task = asyncio.create_task(engine.run())
    stop = asyncio.Event()
    under_load: dict[str, list[float]] = {}
    readers = [
        asyncio.create_task(reader(client, stop, args.think_ms / 1000, under_load))
        for _ in range(args.readers)
    ]
    cpu, began = time.process_time(), time.perf_counter()
    for trap in traps:
        queue.put_nowait(trap)
    while engine.deadlines.classified < args.traps:
        if time.perf_counter() - began > args.timeout:
            raise TimeoutError(f"{engine.deadlines.classified} of {args.traps} classified")
        await asyncio.sleep(0.05)
    elapsed, cpu = time.perf_counter() - began, time.process_time() - cpu
    stop.set()
    await asyncio.gather(*readers)
    report["ingest"] = {
        "seconds": round(elapsed, 2),
        "traps_per_s": round(args.traps / elapsed, 1),
        "main_cpu_s_per_1000": round(1000 * cpu / args.traps, 3),
        "readers": args.readers,
        "requests": sum(len(v) for v in under_load.values()),
        "under_load": {route: summary(v) for route, v in under_load.items()},
        "under_load_all": summary([x for v in under_load.values() for x in v]),
    }

    at_rest: dict[str, list[float]] = {}
    for route in ROUTES:
        for _ in range(args.repeat):
            began = time.perf_counter()
            response = await client.get(route)
            at_rest.setdefault(route, []).append(time.perf_counter() - began)
            response.raise_for_status()
    report["reads"] = {route: summary(v) for route, v in at_rest.items()}
    report["reads_all"] = summary([x for v in at_rest.values() for x in v])
    report["size_bytes"] = await database_bytes(store)

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    await client.aclose()
    await store.close()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", help="the database (default: a throwaway one)")
    parser.add_argument("--sample-days", type=float, default=7.0, help="host samples to write")
    parser.add_argument("--traps", type=int, default=20_000, help="alarms in the storm")
    parser.add_argument("--devices", type=int, default=250, help="source addresses (<= 254)")
    parser.add_argument("--classes", type=int, default=60, help="distinct trap types")
    parser.add_argument("--readers", type=int, default=4, help="console clients during ingest")
    parser.add_argument("--think-ms", type=float, default=50.0, help="each client's pause")
    parser.add_argument("--repeat", type=int, default=20, help="reads per route at rest")
    parser.add_argument("--timeout", type=float, default=1800.0, help="give up after (s)")
    parser.add_argument("--json", type=Path, help="also write the report here")
    args = parser.parse_args(argv)
    if args.db:
        pgtemp.throwaway_vault()  # the engine seals what it audits (ADR #453)
        report = asyncio.run(bench(args, args.db))
    else:
        with pgtemp.database() as url:
            pgtemp.throwaway_vault()
            report = asyncio.run(bench(args, url))
    text = json.dumps(report, indent=2)
    print(text)
    if args.json:
        args.json.write_text(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
