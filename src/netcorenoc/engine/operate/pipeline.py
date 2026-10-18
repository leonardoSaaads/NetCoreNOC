"""The trap pipeline, observed: the live report and the 30 s record (ADRs #450, #451).

Zabbix answers "is the server keeping up" with internal items sampled into its own history — the
queue by delay, each process type's busy share, the cache's free space — and draws them like any
other metric. This is the same answer for the one pipeline this appliance has::

    socket -> ingest process -> spool (disk) -> engine queue -> batch -> commit (classified)

* **In and out.** Traps received per second, beside traps classified per second. Out below in for
  long is a backlog growing; the two lines say so before any trap is late.
* **Backlog and its oldest wait.** Written to the spool and not yet classified, wherever they wait
  (on disk, on the queue, in the batch), and how long the oldest of them has waited. The backlog is
  recorded at its **peak** in each interval, so a burst between two samples is not drawn as zero.
* **Decision latency against the KPI.** p50/p95/p99 and the worst, how many traps were later than
  the target and the limit, and Zabbix's delay buckets.

Nothing here is on the trap path. The report is read per `/api/stats` request; the record is one
row every :data:`SAMPLE_INTERVAL_S`, on the loop that already writes the host's readings.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from netcorenoc.engine.operate.intake import SpoolFeed, oldest_wait
from netcorenoc.engine.operate.resources import SAMPLE_INTERVAL_S, ResourceSampler

if TYPE_CHECKING:
    from netcorenoc.engine.operate.engine import Engine
    from netcorenoc.store import Store

__all__ = ["SAMPLE_INTERVAL_S", "live_report", "sample_loop"]


def _ms(seconds: float | None) -> float | None:
    return None if seconds is None else round(seconds * 1000.0, 2)


def live_report(engine: Engine, now: float) -> dict[str, Any]:
    """What `GET /api/stats` publishes as ``classification``: counts and seconds, no element."""
    feed = engine.feed if isinstance(engine.feed, SpoolFeed) else None
    out = engine.deadlines.report(oldest_wait(feed, engine.queue, now), engine.dropped_provider())
    out["backlog"] = feed.backlog if feed is not None else engine.queue.qsize()
    out["queue"] = {"depth": engine.queue.qsize(), "capacity": engine.queue.maxsize}
    out["spool"] = None if feed is None else {**feed.report(), **feed.disk()}
    return out


async def sample_loop(
    sampler: ResourceSampler,
    store: Store,
    engine: Engine,
    received: Callable[[], int] | None = None,
) -> None:
    """Read the host and the pipeline every ``SAMPLE_INTERVAL_S``, and keep each reading.

    Supervised: a crash costs a graph, never the process. One INSERT and one bounded DELETE per
    interval (`host_sample`, migrations 0022, 0028, 0030), off the trap path.

    Rates are differences of counters over the interval — the receiver's, read and never written
    here — and the first interval has no previous count, so it records no rate rather than a made-up
    one. The latency and the KPI counts are the tracker's interval, taken and restarted here.
    """
    feed = engine.feed if isinstance(engine.feed, SpoolFeed) else None
    previous: tuple[float, int] | None = None
    started: float | None = None
    while True:
        await asyncio.sleep(SAMPLE_INTERVAL_S)
        await sampler.measure_database()
        sampler.sample()
        now = time.time()
        rate: float | None = None
        if received is not None:
            count = received()
            if previous is not None and now > previous[0] and count >= previous[1]:
                rate = round((count - previous[1]) / (now - previous[0]), 3)
            previous = (now, count)
        interval = engine.deadlines.take_interval()
        spent = None if started is None else now - started
        started = now
        disk = feed.disk() if feed is not None else {}
        pipeline = {
            "classified": interval["classified"],
            "classified_per_s": (
                round(interval["classified"] / spent, 3) if spent and spent > 0 else None
            ),
            "latency_max_ms": _ms(interval["max_s"]),
            "over_target": interval["over_target"],
            "over_limit": interval["over_limit"],
            "spool_mb": round(disk["bytes"] / 2**20, 2) if "bytes" in disk else None,
        }
        backlog = feed.take_peak() if feed is not None else engine.queue.qsize()
        async with store.lock:
            await store.record_host_sample(
                now,
                sampler.reading(),
                backlog,
                traps_per_s=rate,
                latency_ms=_ms(interval["p95_s"]),
                pipeline=pipeline,
            )
            await store.commit()
