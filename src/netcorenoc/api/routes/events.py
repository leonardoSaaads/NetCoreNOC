"""The Server-Sent Events stream — the primary live-update path for the UI.

A long-lived stream is the one place a perimeter change could go unnoticed: the security dependency
runs once, at connect time, so a connection opened before a policy was written would otherwise keep
pushing unfiltered snapshots for as long as it stayed open. Every snapshot therefore re-reads the
live policy, re-resolves both the capability set and the scope, and **ends the stream** if
`events.stream` has since been revoked (F30) — or, since ADR #453, if the credential itself has
ended: a sign-out, an expired or revoked session, a revoked token, or a role or password change
(each of which revokes the account's sessions).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import StreamingResponse

from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.api.livestats import live_stats
from netcorenoc.crosscutting import auth, rbac, shaping
from netcorenoc.engine.correlate.learn import MIN_EDGE_N
from netcorenoc.engine.operate.engine import IDLE_CLOSE_S

SSE_HEARTBEAT_S = 15.0
SSE_UPDATE_S = 2.0


#: How many situations of each live state the stream carries, and of every state together.
LIVE_PER_STATE = 50


async def live_situations(store: Any, ne_ids: frozenset[int] | None) -> list[dict[str, Any]]:
    """The stream's list: the latest of **each** live state, and the latest of every state.

    The 50 latest of every state alone was the list until the lab found it wrong: an appliance that
    resolves a lot pushed its older *new* situations out of the window, so the header counted 49
    new and the New tab — filtered from this list — said the network was quiet. Each live state
    now brings its own 50 (`new`, `pending`, `open`), and the latest of every state brings the
    recently resolved ones the Resolved tab shows; a row in two answers is listed once.
    """
    seen: dict[int, dict[str, Any]] = {}
    for status in (None, "new", "pending", "open"):
        for row in await store.list_situations(status, LIVE_PER_STATE, ne_ids):
            seen.setdefault(int(row["id"]), row)
    return sorted(seen.values(), key=lambda r: (r["updated_at"], r["id"]), reverse=True)


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the events routes on `app`."""
    store, engine, security, governance = ctx.store, ctx.engine, ctx.security, ctx.governance
    all_warnings, extra_stats, broker_scope = ctx.all_warnings, ctx.extra_stats, ctx.broker_scope
    route = DeclaredRoutes(app)

    # -- SSE: primary live-update path -------------------------------------------------

    @route.get("/api/events")
    async def events(
        request: Request,
        broker_id: int | None = None,
        principal: auth.Principal = Depends(security),
    ) -> StreamingResponse:
        """The live stream, re-authorized and re-scoped on **every event**.

        A long-lived stream is the one place a perimeter change could go unnoticed: the security
        dependency runs once, at connect time, so a connection opened before a policy was written
        would otherwise keep pushing unfiltered snapshots for as long as it stayed open. Each
        snapshot therefore re-reads the live policy, re-resolves both the capability set and the
        scope, and **ends the stream** if `events.stream` has since been revoked (F30).

        `broker_id` keeps the stream on one broker's network — the Overview's broker selector
        (ADR #496); the scope is narrowed, never widened, on every event like the rest.
        """

        async def snapshot() -> str | None:
            current = await ctx.perimeter.resolve_identity(request, touch=False)
            if current is None or (current.ref, current.role) != (principal.ref, principal.role):
                return None  # signed out, expired, revoked or demoted since the stream opened
            async with store.reading():
                await governance.load()
            capabilities = rbac.resolve_capabilities(
                principal.role, principal.ref, governance.capability
            )
            if "events.stream" not in capabilities:
                return None  # revoked mid-stream: stop sending, rather than serve a stale grant
            scope = await broker_scope(principal, broker_id)
            async with store.reading():
                # **One assembly, shared with `GET /api/stats`** (F115). These two surfaces
                # publish the same object and built it twice until v0.16.7, when a key added to
                # the route did not appear on the stream — and the console reads its live figures
                # from HERE, so the Overview's new band rendered "not measured" over an appliance
                # whose census was correct one route along. Nothing in this repository compares
                # them: `/api/events` is in the behaviour record's NOT_DRIVEN set.
                stats_out: dict[str, Any] = await live_stats(
                    store, engine, scope, all_warnings, extra_stats
                )
                graph_out = await store.graph_snapshot(min_edge_n=MIN_EDGE_N, broker_id=broker_id)
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
                sits = await live_situations(store, None if scope.unrestricted else scope.ne_ids)
                # v0.16.2: the same `stale` mark `GET /api/situations` adds, from the same
                # expression (DECISIONS #274). The console refreshes the polled list from this
                # stream, so a badge that appeared on one and not the other would read as the
                # situation having changed rather than as the transport disagreeing with itself.
                sits = await store.with_idle_active(sits, time.time() - IDLE_CLOSE_S)
                # ADR #476: each row names its probable root, as the polled list does.
                sits = await store.with_roots(sits, None if scope.unrestricted else scope.ne_ids)
                members = (
                    {}
                    if scope.unrestricted
                    else await store.situation_member_nes([int(s["id"]) for s in sits])
                )
            if not scope.unrestricted:
                graph_out = shaping.project_graph(graph_out, scope)
                # The same projection `GET /api/situations` uses, through the same function
                # (v0.16.0): the stream and the polled list had this written out twice and
                # identically, which is one copy too many for a redaction rule.
                sits = [
                    projected
                    for row in sits
                    if (
                        projected := shaping.project_situation_row(
                            row, members.get(int(row["id"]), []), scope
                        )
                    )
                    is not None
                ]
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
