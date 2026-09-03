"""Public surface: liveness, readiness, the single-page UI, and its static assets.

No identity, no capability, no scope — which is why the declaration gate exempts non-``/api``
paths by consultation rather than by omission (`declare.require_declaration`). `STATIC_ASSETS` is
a compile-time allowlist, and `_asset_route` is the one registration in the package that is not a
decorator; `tests/test_declaration.py` pins both facts.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.responses import FileResponse

from netcorenoc import __version__
from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes

# v0.7.2: `api` is a package one level below `netcorenoc`, so the UI lives one directory further
# up than it did when this was `netcorenoc/api.py`. The extra `.parent` is a consequence of the
# move, not a change to what is served: the resolved path is byte-identical to v0.7.1's, and
# `tests/test_security_txt.py` / `tests/test_deploy.py` assert the served files from it.
UI_DIR = Path(__file__).parent.parent / "ui"
UI_FILE = UI_DIR / "index.html"
QUEUE_SATURATION = 0.9  # /readyz reports not-ready once the ingest queue passes this fraction

# v0.13.0: the console is an ES module graph, so every module has to be reachable. It is
# **enumerated**, not served from a directory (ADR #175).
#
# A directory route would be a path-traversal surface and — worse — it would make "what does this
# appliance serve?" unanswerable from the code. `STATIC_ASSETS` is a compile-time allowlist and
# `tests/test_declaration.py` pins that fact; turning it into a directory would delete a
# deny-by-default property to save typing.
#
# `tests/test_supply_chain.py::test_the_served_module_set_equals_the_module_set_on_disk` asserts
# the two sets are equal **in both directions**, so a module that exists and is not served, and a
# module that is served and does not exist, both fail.
_UI_MODULES = (
    "app.js",
    "app/api.js",
    "app/destructive.js",
    "app/dom.js",
    "app/format.js",
    # v0.15.3: `icons.js` is the drawn icon family that replaces seventeen Unicode glyphs (#236);
    # `password.js` is the confirmation, length meter and reveal shared by the sign-in card and the
    # account screen, so the two cannot drift about what a valid password is (V.2).
    "app/icons.js",
    "app/login.js",
    "app/parameters.js",
    "app/password.js",
    "app/registry.js",
    "app/router.js",
    "app/session.js",
    "app/shell.js",
    "app/sidebar.js",
    "app/store.js",
    "app/theme.js",
    "app/vendor.js",
    "app/views/account.js",
    "app/views/audit.js",
    "app/views/classes.js",
    "app/views/corpus.js",
    "app/views/entities.js",
    "app/views/governance.js",
    "app/views/graph.js",
    "app/views/labelling.js",
    "app/views/overview.js",
    "app/views/promotion.js",
    "app/views/quarantine.js",
    "app/views/scorer.js",
    "app/views/settings.js",
    "app/views/situations.js",
    "app/views/timeline.js",
    "app/views/tokens.js",
    "app/views/users.js",
    # `views/parts/` — modules under `views/` that are NOT views. `registry.js` imports seventeen
    # screens; these four are imported by SIBLING views (`settings` takes facts and retention,
    # `scorer` takes model, `promotion` takes verdict). They lived beside the screens until
    # v0.15.3, when the import graph was read and the directory stopped saying something false
    # (DECISIONS #239). `model.js` and `verdict.js` arrived in v0.14.0 at the seam their screens
    # already had; `facts.js` and `retention.js` are the same shape, split out of `settings`.
    "app/views/parts/facts.js",
    "app/views/parts/model.js",
    "app/views/parts/retention.js",
    "app/views/parts/verdict.js",
    "app/views/parts/lifecycle.js",
    "app/views/parts/members.js",
    "app/views/parts/why.js",
    "app/widgets.js",
)

_VENDOR_ASSETS = (
    "vendor/d3.v7.min.js",
    "vendor/preact-10.29.8.module.js",
    "vendor/htm-3.1.1.module.js",
)

STATIC_ASSETS = {
    **dict.fromkeys(_UI_MODULES, "application/javascript"),
    **dict.fromkeys(_VENDOR_ASSETS, "application/javascript"),
    "style.css": "text/css",
    # RFC 9116 machine-readable security contact. Static, public, unauthenticated, additive to
    # this allowlist (not a new dynamic surface); it is served under the same CSP/security-headers
    # middleware and shipped in the package (ui/.well-known/security.txt).
    ".well-known/security.txt": "text/plain; charset=utf-8",
}


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the static routes on `app`."""
    store, engine = ctx.store, ctx.engine
    route = DeclaredRoutes(app)

    # -- public routes -----------------------------------------------------------------

    @route.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @route.get("/readyz")
    async def readyz(response: Response) -> dict[str, str]:
        """Orchestrator readiness (§A.5): 200 only when the DB is reachable, migrations are
        applied, and the queue has headroom; 503 otherwise. Unauthenticated by design and so
        leaks no detail beyond ok/not-ok — the reasons live behind authenticated /api/stats."""
        ready = True
        try:
            async with store.lock:
                applied = await store.schema_version() == store.latest_schema_version()
            ready = applied
        except Exception:  # DB unreachable => not ready, never a 500
            ready = False
        queue = engine.queue
        if queue.maxsize and queue.qsize() >= queue.maxsize * QUEUE_SATURATION:
            ready = False  # saturated: cannot accept the load it is being sent
        if not ready:
            response.status_code = 503
            return {"status": "not ready"}
        return {"status": "ready"}

    @route.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(UI_FILE, media_type="text/html")

    def _asset_route(asset: str, media_type: str) -> None:
        async def serve() -> FileResponse:
            return FileResponse(UI_DIR / asset, media_type=media_type)

        app.add_api_route(f"/{asset}", serve, include_in_schema=False)

    for _asset, _media in STATIC_ASSETS.items():
        _asset_route(_asset, _media)
