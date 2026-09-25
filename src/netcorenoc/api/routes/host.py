"""`GET /api/resources` — the host's readings over the window the Overview asked for (v0.22.0).

The range control on the Overview offered fifteen minutes to seven days, and the four host charts
beneath it drew the same in-memory two hours whatever was picked (F154, ADR #380). This route is the
other half of the repair: the window arrives as a query parameter and reaches SQL, so a different
range is a different read rather than the same rows drawn again.

Unscoped: a CPU percentage names no network element and no principal, and every role that may read
`/api/stats` already sees the current reading of all four.
"""

from __future__ import annotations

import math
import time
from typing import Any

from fastapi import FastAPI

from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.store.host_samples import HOST_SAMPLE_RETENTION_S

#: The same ceiling the timeline's bucketed form uses: a chart cannot resolve more, and it bounds
#: the GROUP BY a caller can ask for.
MAX_BUCKETS = 240


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the host-series route on `app`."""
    store, guarded = ctx.store, ctx.guarded
    route = DeclaredRoutes(app)

    @route.get("/api/resources", dependencies=guarded)
    async def resources(range_s: float = 7200.0, buckets: int = 24) -> dict[str, Any]:
        """CPU, memory, storage, database size and queue depth, bucketed over the last `range_s`.

        `range_s` is clamped to what the table can hold — a window longer than the retention would
        be a request for readings the appliance deleted, and answering it would draw their absence
        as a quiet week. An empty bucket is `null`, never zero.
        """
        span = min(max(60.0, float(range_s)), float(HOST_SAMPLE_RETENTION_S))
        wanted = min(max(int(buckets), 2), MAX_BUCKETS)
        # The window ends on a bucket boundary, so two polls inside one bucket read the same
        # buckets: the chart does not shimmer as its edges slide by a few seconds each poll.
        width = span / wanted
        until = math.ceil(time.time() / width) * width
        async with store.lock:
            return await store.host_series(since=until - span, until=until, buckets=wanted)
