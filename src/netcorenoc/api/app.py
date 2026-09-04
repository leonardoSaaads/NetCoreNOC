"""`create_app` — build the perimeter, build the context, register the routes. Nothing else.

The middleware order and the perimeter's step sequence are described once, in
`docs/architecture/DESIGN.md` § "v0.7.2 — the perimeter as a named component". They used to be a
paragraph at the top of a 1 752-line `api.py`; they are not restated here, because one description
is easier to keep true than two.

Two things in this file are load-bearing rather than cosmetic:

* **The `register()` calls run in the order the routes were declared at v0.7.1.** FastAPI resolves
  the first matching route, so `/api/situations` before `/api/situations/{sid}` and
  `/api/scorer/preview` before the bare `POST /api/scorer` decide which handler answers.
  `tests/test_architecture.py::test_route_table_order_is_unchanged` pins the whole 48-entry table.
* **The perimeter's methods are aliased to the names the handlers already call them by**, and each
  route module rebinds them again at the top of its `register()`. That is what let 1 752 lines
  become sixteen modules with every handler body textually unchanged (DECISIONS #78).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from fastapi import Depends, FastAPI

from netcorenoc import __version__
from netcorenoc.api import (
    declare,
    routes_admin,
    routes_annotate,
    routes_audit,
    routes_auth,
    routes_events,
    routes_governance,
    routes_lifecycle,
    routes_operate,
    routes_promotion,
    routes_read,
    routes_scorer,
    routes_static,
)
from netcorenoc.api.context import AppContext
from netcorenoc.api.perimeter import (
    PREVIEW_RATE_CAPACITY,
    PREVIEW_RATE_REFILL,
    RATE_CAPACITY,
    RATE_REFILL,
    Perimeter,
    RateLimiter,
)
from netcorenoc.crosscutting import auth

if TYPE_CHECKING:
    from netcorenoc.crosscutting.runtime import RuntimeConfig
    from netcorenoc.main import Engine


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

    # The whole HTTP security boundary, built once.
    perimeter = Perimeter(
        store, rate_capacity=rate_capacity, rate_refill=rate_refill, warnings=warnings
    )
    app.middleware("http")(perimeter.security_headers)

    # Everything the route modules need, resolved once. Each `register()` rebinds the fields it
    # uses to local names as its first statement, which is what keeps every handler body
    # textually identical to v0.7.1 (DECISIONS #78).
    ctx = AppContext(
        engine=engine,
        store=store,
        perimeter=perimeter,
        security=perimeter.security,
        guarded=[Depends(perimeter.security)],
        scope_for=perimeter.scope_for,
        all_warnings=perimeter.all_warnings,
        write_txn=perimeter.write_txn,
        governance=perimeter.governance,
        preview_limiter=preview_limiter,
        throttle=throttle,
        extra_stats=extra_stats,
        runtime=runtime,
        tls_enabled=tls_enabled,
    )

    # Registration order is v0.7.1's declaration order, and it is behaviour: see the module
    # docstring. Do not sort these.
    routes_static.register(app, ctx)
    routes_auth.register(app, ctx)
    routes_read.register(app, ctx)
    # v0.16.0: the five operator gestures, split across two modules on whether they assert
    # anything about a grouping — the distinction `PREREGISTRATION-0.16.0.md` §1 turns into a
    # prohibition, expressed in the module tree rather than only in a docstring.
    #
    # **Registered before `routes_operate`**, and the reason is the behaviour record rather than
    # the router: `tests/behaviour_identity.py` drives every route in registration order against
    # one database, so a `close` driven first leaves every gesture after it answering 409 on a
    # resolved situation. That is a true response and a weak pin — it would not catch a regression
    # in any of the three handlers. Ordering is free here (every path is a distinct literal and
    # none can shadow another), so it is chosen to make the record say more.
    routes_lifecycle.register(app, ctx)
    routes_annotate.register(app, ctx)
    routes_operate.register(app, ctx)
    routes_admin.register(app, ctx)
    routes_scorer.register(app, ctx)
    routes_promotion.register(app, ctx)
    routes_governance.register(app, ctx)
    routes_audit.register(app, ctx)
    routes_events.register(app, ctx)

    # F40: the gate's completeness half. `DeclaredRoutes` refuses at registration and gives the
    # better error; this re-checks the *result*, so a route registered by any other path — an
    # unwrapped verb, a direct registration on the app, something not yet written — cannot reach a
    # running process undeclared. Before returning, deliberately: a mis-declared route must stop
    # startup, not fail only under test.
    declare.assert_every_route_is_declared(app)
    return app
