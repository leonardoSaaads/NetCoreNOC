"""The Server-Sent Events stream — the primary live-update path for the UI.

A long-lived stream is the one place a perimeter change could go unnoticed: the security dependency
runs once, at connect time, so a connection opened before a policy was written would otherwise keep
pushing unfiltered snapshots for as long as it stayed open. Every snapshot therefore re-reads the
live policy, re-resolves both the capability set and the scope, and **ends the stream** if
`events.stream` has since been revoked (F30).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.responses import StreamingResponse

from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.crosscutting import auth, rbac, shaping
from netcorenoc.engine.correlate.learn import MIN_EDGE_N

SSE_HEARTBEAT_S = 15.0
SSE_UPDATE_S = 2.0


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the events routes on `app`."""
    store, engine, security, governance = ctx.store, ctx.engine, ctx.security, ctx.governance
    scope_for, all_warnings, extra_stats = ctx.scope_for, ctx.all_warnings, ctx.extra_stats
    route = DeclaredRoutes(app)

    # -- SSE: primary live-update path -------------------------------------------------

    @route.get("/api/events")
    async def events(principal: auth.Principal = Depends(security)) -> StreamingResponse:
        """The live stream, re-authorized and re-scoped on **every event**.

        A long-lived stream is the one place a perimeter change could go unnoticed: the security
        dependency runs once, at connect time, so a connection opened before a policy was written
        would otherwise keep pushing unfiltered snapshots for as long as it stayed open. Each
        snapshot therefore re-reads the live policy, re-resolves both the capability set and the
        scope, and **ends the stream** if `events.stream` has since been revoked (F30).
        """

        async def snapshot() -> str | None:
            async with store.lock:
                await governance.load()
            capabilities = rbac.resolve_capabilities(
                principal.role, principal.ref, governance.capability
            )
            if "events.stream" not in capabilities:
                return None  # revoked mid-stream: stop sending, rather than serve a stale grant
            scope = await scope_for(principal)
            async with store.lock:
                stats_out: dict[str, Any] = dict(
                    await store.stats()
                    if scope.unrestricted
                    else await store.scoped_stats(scope.ne_ids, scope.ips)
                )
                graph_out = await store.graph_snapshot(min_edge_n=MIN_EDGE_N)
                # F38 applies to the stream too: truncating globally would make a scoped
                # subscriber's live list a function of traffic they cannot see.
                # **v0.16.0: `None`, not `"open"`.** The correlator now creates a situation as
                # `new` and only an operator's gesture promotes it to `open` (DECISIONS #254), so
                # a stream that asked for `"open"` published an EMPTY list on a console whose
                # header — served by `stats.open_situations`, which counts both — said two. Found
                # in a browser, because nothing else looks at this route: `/api/events` is in the
                # behaviour record's `NOT_DRIVEN` set (a stream has no single response to hash),
                # and the DOM harness captures route payloads rather than the stream.
                #
                # The resolved ones are dropped where the tabs are, not here: the console's three
                # tabs need all three states, and a filter in the transport would make one of them
                # unreachable for a reason no reader of `situations.js` could see.
                sits = await store.list_situations(
                    None, 50, None if scope.unrestricted else scope.ne_ids
                )
                members = (
                    {}
                    if scope.unrestricted
                    else await store.situation_member_nes([int(s["id"]) for s in sits])
                )
            stats_out["latency_p95_s"] = round(engine.latency_p95(), 4)
            stats_out["queue_depth"] = engine.queue.qsize()
            stats_out["warnings"] = all_warnings()
            if extra_stats is not None:
                stats_out.update(extra_stats())
            if not scope.unrestricted:
                graph_out = shaping.project_graph(graph_out, scope)
                scoped_sits = []
                for row in sits:
                    member_nes = members.get(int(row["id"]), [])
                    shown = sum(1 for ne_id in member_nes if scope.allows_ne(ne_id))
                    if shown:
                        scoped_sits.append(
                            {**row, "alarm_count": shown, "redacted_count": len(member_nes) - shown}
                        )
                sits = scoped_sits
            # Shape the live stream by the subscriber's role, exactly like the polled endpoints.
            payload = {
                "stats": stats_out,
                "graph": shaping.shape(graph_out, principal.role),
                "situations": shaping.shape(sits, principal.role),
            }
            return "event: update\ndata: " + json.dumps(payload) + "\n\n"

        async def gen() -> AsyncIterator[str]:
            yield ": connected\n\n"
            first = await snapshot()
            if first is None:
                return
            yield first
            last_beat = time.monotonic()
            while True:
                await asyncio.sleep(SSE_UPDATE_S)
                event = await snapshot()
                if event is None:
                    return
                yield event
                now = time.monotonic()
                if now - last_beat >= SSE_HEARTBEAT_S:
                    last_beat = now
                    yield ": heartbeat\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")
