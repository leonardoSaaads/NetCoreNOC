"""The route-declaration gate: a route that has not declared itself cannot be registered.

Before v0.7.2 a route was registered with ``@app.get("/api/x")`` and its capability lived in a
separate dict in `rbac.py`, joined by a path string. The join was invisible at the point of
registration, which is exactly why the project had to invent a runtime fail-closed **and** a CI
completeness test to catch what the code could not express. F34 was the same shape one level down:
a route's *scope posture* was not expressed anywhere at all, so three write routes simply did not
have one.

This module moves the first failure forward and makes the second one exist. Registration goes
through :class:`DeclaredRoutes`, which consults `rbac.ROUTE_PERMISSIONS` and `rbac.ROUTE_SCOPE`
**as the route is registered** — that is, while `create_app` is building the application, before
the process can serve a single request. Not per request, and not only in CI.

`rbac.py` remains the single source of authority: nothing here decides anything, it only refuses
to register a route the authority has not been told about.

Two exemptions, both by explicit consultation rather than by omission:

* **`UNAUTHENTICATED_PATHS`** — ``/healthz``, ``/readyz``, ``/``, the static assets and the OpenAPI
  schema. They resolve no identity and require no capability, so they are not in the authorization
  map by construction and there is nothing for them to declare.
* **`rbac.PUBLIC_ROUTES`** — currently ``POST /api/login`` alone. Exempt from both tables, as it
  has been since v0.2.0.

**v0.7.4 — F40 and F41 close two holes in this gate.** Neither was exploited, and both were latent
holes in a guard whose entire value is completeness. `MODULE-ARCHITECTURE.md` §10.1 specifies both
fixes; `docs/security/SECURITY-REVIEW-0.7.4.md` records them.

* **F40** — :class:`DeclaredRoutes` wraps three verbs and only the decorator form, so a route
  registered directly on the FastAPI application — the non-decorator form `routes_static.py` uses
  for its asset allowlist — arrived without ever consulting the gate, and appeared in the route
  table. Wrapping the remaining verbs would have closed the paths that exist today and stayed silent
  about the next one. :func:`assert_every_route_is_declared` instead inspects the **result** —
  every route on the built application — which is complete *by construction*, because a
  registration path nobody has written yet still produces a route. `create_app` calls it before it
  returns, so a mis-declared route stops the process rather than failing only under test. The
  decorator-time refusal is **kept as well**: failing where the route is written gives a far better
  error than failing at the end of `create_app`.
* **F41** — the exemption was ``if not path.startswith("/api"): return``, which is true of today's
  public surface and **accidentally** true of anything else outside ``/api``.
  ``require_declaration("GET", "/metrics")`` returned cleanly, and `/metrics` is on the ROADMAP.
  The prefix test is now an explicit allowlist, asserted against what `routes_static.py` actually
  registers so the two cannot drift.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI
from fastapi.types import DecoratedCallable

from netcorenoc import rbac


class UndeclaredRouteError(RuntimeError):
    """A route was registered without declaring its capability, its scope posture, or both.

    Raised while the application is being built, so an appliance carrying an undeclared route does
    not start — the same class of guarantee as the capability ceiling: structural, not checked.
    """


# Every path this appliance serves **without resolving an identity** (F41). Membership here is the
# claim "no capability is required to fetch this", and it is the whole of that claim: a non-`/api`
# path that is not listed cannot be registered.
#
# Deliberately a literal set rather than an import from `routes_static`, which would be a circular
# import — `routes_static` registers through this module. The derivation is asserted instead of
# performed, by `tests/test_declaration.py::test_f41_the_allowlist_matches_what_is_actually_served`:
#
#     UNAUTHENTICATED_PATHS == {"/healthz", "/readyz", "/", OPENAPI}
#                              | {"/" + asset for asset in STATIC_ASSETS}
#
# so adding a static asset without listing it here fails, and listing one that is not served fails
# too. `/openapi.json` is FastAPI's own registration, not one of ours; it is public on this surface
# and is listed because this set is a statement about what is served, not about who registered it.
UNAUTHENTICATED_PATHS: frozenset[str] = frozenset(
    {
        "/healthz",  # liveness
        "/readyz",  # orchestrator readiness; ok/not-ok only, no detail
        "/",  # the single-page UI
        "/openapi.json",  # the schema; docs_url and redoc_url are disabled
        "/app.js",
        "/style.css",
        "/vendor/d3.v7.min.js",
        "/.well-known/security.txt",  # RFC 9116
    }
)


def require_declaration(method: str, path: str) -> None:
    """Refuse a route that `rbac.py` has not been told about. The gate itself."""
    if not path.startswith("/api"):
        if path in UNAUTHENTICATED_PATHS:
            return  # served with no identity and no capability: nothing to declare
        raise UndeclaredRouteError(
            f"{method} {path} is outside /api and is not in "
            "netcorenoc.api.declare.UNAUTHENTICATED_PATHS. A path that resolves no identity must "
            "be listed there — which is a reviewable claim that it needs no capability. A path "
            "that does resolve one belongs under /api, where it must declare its capability and "
            "its scope posture. Before v0.7.4 every non-/api path was exempt by accident (F41)."
        )
    key = (method, path)
    if key in rbac.PUBLIC_ROUTES:
        return  # deliberately public: exempt from both tables
    missing = [
        name
        for name, table in (
            ("rbac.ROUTE_PERMISSIONS", rbac.ROUTE_PERMISSIONS),
            ("rbac.ROUTE_SCOPE", rbac.ROUTE_SCOPE),
        )
        if key not in table
    ]
    if missing:
        raise UndeclaredRouteError(
            f"{method} {path} is not declared in {' and '.join(missing)}. Every /api route must "
            "state the capability it requires and the visibility-scope posture it has, or be "
            "listed in rbac.PUBLIC_ROUTES. Declare it there — never here."
        )


def assert_every_route_is_declared(app: FastAPI) -> None:
    """**The completeness half of the gate (F40).** Every route on the built app, re-checked.

    :class:`DeclaredRoutes` refuses at the point of registration, which gives the better error but
    only covers the paths it wraps — three verbs, decorator form. This inspects the *result*, so it
    is complete by construction: any registration path, including ones nobody has written yet,
    ends with a route on ``app.routes``, and every one of them is put back through
    :func:`require_declaration`. That is the difference between completeness and enumeration —
    nothing here lists the ways a route can be registered.

    Called by `create_app` **before it returns**, so an appliance carrying an undeclared route does
    not start — the same class of guarantee as the capability ceiling: structural, not checked.
    Running it only under test would make it a CI convenience rather than a property of the
    process.

    ``HEAD`` and ``OPTIONS`` are skipped: FastAPI synthesises them from the declared verb, so they
    are not separate declarations and never were.
    """
    for route in app.routes:
        path = getattr(route, "path", None)
        if path is None:
            continue
        for method in sorted(getattr(route, "methods", set()) or set()):
            if method not in ("HEAD", "OPTIONS"):
                require_declaration(method, path)


class DeclaredRoutes:
    """`app.get`/`app.post`/`app.delete`, gated. **The** registration path for the API.

    Route modules hold one of these as ``route`` and register through it, so a reader sees the
    declaration requirement at the point of registration rather than three files away. A test
    (`tests/test_declaration.py`) asserts that no ``@app.<verb>`` decorator survives anywhere
    under `netcorenoc/api/`, so the discipline cannot be bypassed by a contributor in a hurry.
    """

    def __init__(self, app: FastAPI) -> None:
        self._app = app

    def get(self, path: str, **kwargs: Any) -> Callable[[DecoratedCallable], DecoratedCallable]:
        require_declaration("GET", path)
        return self._app.get(path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> Callable[[DecoratedCallable], DecoratedCallable]:
        require_declaration("POST", path)
        return self._app.post(path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Callable[[DecoratedCallable], DecoratedCallable]:
        require_declaration("DELETE", path)
        return self._app.delete(path, **kwargs)
