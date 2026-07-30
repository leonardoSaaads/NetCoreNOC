"""FastAPI application: identity, role-based authorization, tamper-evident audit, SSE.

Middleware order (per DESIGN v0.2): security headers (a real middleware) then, inside the
``security`` dependency applied to every protected ``/api`` route — origin/CSRF (cookie
mutations) → identity resolution (session cookie or Bearer token) → bootstrap gate →
RBAC (``rbac.py``, the single source) → per-client rate limit → handler. Handlers audit
every mutating action and every sensitive read (denied attempts included). The single-file
UI and its assets are served statically with no build step.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import Depends, FastAPI
from fastapi.responses import StreamingResponse

from netcorenoc import __version__, auth, rbac, shaping
from netcorenoc.api import (
    routes_admin,
    routes_audit,
    routes_auth,
    routes_governance,
    routes_operate,
    routes_read,
    routes_scorer,
    routes_static,
)
from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.api.perimeter import (
    PREVIEW_RATE_CAPACITY,
    PREVIEW_RATE_REFILL,
    RATE_CAPACITY,
    RATE_REFILL,
    Perimeter,
    RateLimiter,
)
from netcorenoc.learn import MIN_EDGE_N

if TYPE_CHECKING:
    from netcorenoc.main import Engine
    from netcorenoc.runtime import RuntimeConfig

# v0.7.2: `api` is a package one level below `netcorenoc`, so the UI lives one directory further
# up than it did when this was `netcorenoc/api.py`. The extra `.parent` is a consequence of the
# move, not a change to what is served: the resolved path is byte-identical to v0.7.1's, and
# `tests/test_security_txt.py` / `tests/test_deploy.py` assert the served files from it.
UI_DIR = Path(__file__).parent.parent / "ui"
UI_FILE = UI_DIR / "index.html"
MAX_SCORER_HISTORY = 50
MAX_POLICY_HISTORY = 50
SSE_HEARTBEAT_S = 15.0
SSE_UPDATE_S = 2.0
QUEUE_SATURATION = 0.9  # /readyz reports not-ready once the ingest queue passes this fraction
STATIC_ASSETS = {
    "app.js": "application/javascript",
    "style.css": "text/css",
    "vendor/d3.v7.min.js": "application/javascript",
    # RFC 9116 machine-readable security contact. Static, public, unauthenticated, additive to
    # this allowlist (not a new dynamic surface); it is served under the same CSP/security-headers
    # middleware and shipped in the package (ui/.well-known/security.txt).
    ".well-known/security.txt": "text/plain; charset=utf-8",
}


# Endpoints reachable while an account still owes a forced password change.
def _params_of(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """The five parameters of a stored scorer config, for a before/after audit detail."""
    if row is None:
        return None
    return {
        "config_id": int(row["id"]),
        "w_t": row["w_t"],
        "w_a": row["w_a"],
        "w_e": row["w_e"],
        "tau_s": row["tau_s"],
        "threshold": row["threshold"],
    }


def create_app(
    engine: Engine,
    extra_stats: Callable[[], dict[str, Any]] | None = None,
    rate_capacity: float = RATE_CAPACITY,
    rate_refill: float = RATE_REFILL,
    throttle: auth.LoginThrottle | None = None,
    tls_enabled: bool = False,
    runtime: RuntimeConfig | None = None,
    warnings: Callable[[], list[str]] | None = None,
    preview_rate_capacity: float = PREVIEW_RATE_CAPACITY,
    preview_rate_refill: float = PREVIEW_RATE_REFILL,
) -> FastAPI:
    app = FastAPI(title="NetCoreNOC", version=__version__, docs_url=None, redoc_url=None)
    preview_limiter = RateLimiter(preview_rate_capacity, preview_rate_refill)
    throttle = throttle or auth.LoginThrottle()
    store = engine.store

    # The whole HTTP security boundary, built once. Its methods are aliased to the names the
    # handlers below already call them by, so no handler body changes (DECISIONS #77, #78).
    perimeter = Perimeter(
        store, rate_capacity=rate_capacity, rate_refill=rate_refill, warnings=warnings
    )
    governance = perimeter.governance
    all_warnings = perimeter.all_warnings
    write_txn = perimeter.write_txn
    security = perimeter.security
    scope_for = perimeter.scope_for
    app.middleware("http")(perimeter.security_headers)

    # THE registration path: refuses a route rbac.py has not been told about, while the
    # application is being built rather than per request (DECISIONS #80).
    route = DeclaredRoutes(app)
    guarded = [Depends(security)]

    # Everything the route modules need, resolved once. Each `register()` rebinds the fields it
    # uses to local names as its first statement, which is what lets every handler body below stay
    # textually identical to v0.7.1 (DECISIONS #78).
    ctx = AppContext(
        engine=engine,
        store=store,
        perimeter=perimeter,
        security=security,
        guarded=guarded,
        scope_for=scope_for,
        all_warnings=all_warnings,
        write_txn=write_txn,
        governance=governance,
        preview_limiter=preview_limiter,
        throttle=throttle,
        extra_stats=extra_stats,
        runtime=runtime,
        tls_enabled=tls_enabled,
    )

    # Registered in the order the routes were declared at v0.7.1, which is what fixes FastAPI's
    # path-matching precedence (tests/test_architecture.py::test_route_table_order_is_unchanged).
    routes_static.register(app, ctx)

    routes_auth.register(app, ctx)
    routes_read.register(app, ctx)

    routes_operate.register(app, ctx)
    routes_admin.register(app, ctx)
    routes_scorer.register(app, ctx)
    routes_governance.register(app, ctx)
    routes_audit.register(app, ctx)
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
                sits = await store.list_situations(
                    "open", 50, None if scope.unrestricted else scope.ne_ids
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

    return app
