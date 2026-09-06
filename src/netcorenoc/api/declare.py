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

**v0.7.5 — F42 closes the third hole, and corrects a claim.** F40's fix was described here and in
`SECURITY-REVIEW-0.7.4.md` as *"complete by construction… nothing here lists the ways a route can be
registered"*. The second clause is true; **the first does not follow from it**. The traversal named
no registration mechanism and assumed a *shape* — a flat object exposing ``.path`` and ``.methods``
— which is enumeration wearing construction's clothes. Five shapes evaded it, all serving real
traffic, and whether one of them evaded depended on the version of an **unpinned** dependency: the
``include_router`` case was refused on ``fastapi==0.115.0`` and skipped on ``0.141.1``, so the gate
regressed with no commit and no failing test. See :data:`KNOWN_ROUTE_SHAPES` and
:func:`assert_every_route_is_declared` below, `docs/security/SECURITY-REVIEW-0.7.5.md` for the
finding, and DECISIONS #98 for why the fix refuses unknown shapes rather than learning to walk them.

**v0.8.0 — F43 narrows a claim v0.7.5 left too strong.** That release said *"every object on
``app.routes`` is either checked or refused; none is skipped"*. F42 made the **shape** half true.
The **method** half was not: within a known shape the traversal iterates ``route.methods``, and an
**empty** set produces zero iterations — so the route was neither checked nor refused, while
Starlette serves *every* verb on a route whose ``methods`` is falsy.

* **F43** — an empty method set is **unverifiable**: there is no verb to look up in either
  authorization table, so there is nothing to check. It is therefore **refused**, by the same
  reasoning that refuses an unknown shape. Reproduced by execution in
  `docs/gates/v0.8.0-phase-0.md` §7, where such a route answered 200 to GET, POST, PUT, DELETE,
  PATCH, HEAD and OPTIONS while this gate passed it. `docs/security/SECURITY-REVIEW-0.8.0.md`
  carries the finding and issues the correction; DECISIONS #106 records the choice.

The claim this module now supports, written so it is checkable rather than reassuring: **every
object on ``app.routes`` is either checked against both authorization tables for every verb it
carries, or refused — as an unknown shape, or as a known shape carrying no verb to check.**
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.types import DecoratedCallable
from starlette.routing import Route

from netcorenoc.crosscutting import rbac


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
        "/style.css",
        "/.well-known/security.txt",  # RFC 9116
        # v0.13.0: the console is an ES module graph (ADR #175). Every module is listed
        # individually, and that verbosity is the point: each line is the reviewable claim
        # "fetching this needs no capability". A glob here would make the claim unreadable
        # and would re-open F41 — an exemption that is accidentally true of the next path
        # somebody adds.
        "/app.js",
        "/app/api.js",
        "/app/destructive.js",
        "/app/dom.js",
        "/app/format.js",
        # v0.15.3: the drawn icon family (#236) and the shared password surface (V.2). Both are
        # fetched before any identity exists — the sign-in card imports them — so both are
        # unauthenticated by necessity as well as by claim.
        "/app/icons.js",
        "/app/login.js",
        "/app/parameters.js",
        "/app/password.js",
        "/app/registry.js",
        "/app/router.js",
        "/app/session.js",
        "/app/shell.js",
        "/app/sidebar.js",
        "/app/store.js",
        "/app/theme.js",
        "/app/vendor.js",
        "/app/views/account.js",
        "/app/views/audit.js",
        "/app/views/classes.js",
        "/app/views/corpus.js",
        "/app/views/entities.js",
        "/app/views/governance.js",
        "/app/views/graph.js",
        "/app/views/labelling.js",
        "/app/views/overview.js",
        "/app/views/promotion.js",
        "/app/views/quarantine.js",
        "/app/views/scorer.js",
        "/app/views/settings.js",
        "/app/views/situations.js",
        "/app/views/timeline.js",
        "/app/views/tokens.js",
        "/app/views/users.js",
        # v0.15.3: the four modules under `views/` that were never views (#239).
        "/app/views/parts/facts.js",
        "/app/views/parts/model.js",
        "/app/views/parts/retention.js",
        "/app/views/parts/verdict.js",
        # v0.16.0: the operator gestures, their confidence control and the history list.
        "/app/views/parts/lifecycle.js",
        "/app/views/parts/declare.js",
        "/app/views/parts/members.js",
        "/app/views/parts/why.js",
        # v0.16.1: the situation card, split out when the server-side search pushed
        # `views/situations.js` over the module-graph guard (DECISIONS #265).
        "/app/views/parts/card.js",
        # v0.16.4: the judgement surface, split out when the state-dependent action surface pushed
        # `views/parts/card.js` over the same guard (DECISIONS #291, #293).
        "/app/views/parts/judge.js",
        # v0.16.1: the console icon (F96). `img-src 'self'` forbids a data: URI.
        "/favicon.svg",
        "/app/widgets.js",
        # Vendored third-party assets, pinned by CHECKSUMS.txt.
        "/vendor/d3.v7.min.js",
        "/vendor/htm-3.1.1.module.js",
        "/vendor/preact-10.29.8.module.js",
    }
)


# **F42.** The route shapes :func:`assert_every_route_is_declared` knows how to check. An object on
# `app.routes` whose type is not exactly one of these is **refused, not skipped**. The gate need not
# know what a `Mount` is in order to refuse it, and that is the point.
#
# Matched on **exact type**, not `isinstance`. `APIRoute` subclasses `Route`, so an `isinstance`
# test would silently admit any future subclass of either — which is the same fail-open-on-the-
# unknown this finding is about, one inheritance level down (DECISIONS #98).
#
# This tuple is **enumeration**, and saying so is the point of
# `tests/test_declaration.py::test_f42_the_live_app_produces_exactly_the_known_shapes`: it asserts
# that the shapes a real `create_app` produces are *exactly* this set, so a dependency upgrade that
# changes the representation fails the suite loudly, naming the new class, on the day of the
# upgrade. That test is the half that generalises; this tuple is the half that does not.
KNOWN_ROUTE_SHAPES: tuple[type, ...] = (APIRoute, Route)


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

    **v0.7.5 — F42 makes the traversal refuse what it cannot classify.** The version above named no
    registration mechanism, which was true, and assumed a *shape*: a flat object exposing ``.path``
    and ``.methods``. That is enumeration wearing construction's clothes, and it failed open twice
    — ``if path is None: continue``, and an inner loop over a ``methods`` set that is empty for
    every shape carrying no verbs. Five shapes evaded it, all reproduced by execution and all
    serving real traffic, in `docs/gates/v0.7.5-phase-0.md` §2:

    * ``fastapi.routing._IncludedRouter`` (``app.include_router``) — no ``.path``;
    * ``starlette.routing.Mount`` for a sub-application and for ``StaticFiles`` — empty ``methods``,
      a whole subtree served unchecked;
    * ``fastapi.routing.APIWebSocketRoute`` — empty ``methods``;
    * an explicitly-registered ``HEAD``-only route — the exemption below, before it was narrowed.

    The assumption was not even stable across dependency versions: the ``include_router`` case was
    **refused** on ``fastapi==0.115.0``, the floor of this project's own pin, and **skipped** on
    ``0.141.1``. `pyproject.toml` carries no upper bound and CI has no lockfile, so the gate's
    completeness had become a property of whatever pip resolved that morning — and it regressed
    with no commit and no failing test.

    Now: any object on ``app.routes`` outside :data:`KNOWN_ROUTE_SHAPES` raises. The gate refuses
    rather than skips, so a future FastAPI that invents a sixth shape is caught the day it arrives.
    Teaching the traversal to *walk* each container was rejected — every attribute it would need
    (``include_context``, ``effective_route_contexts``) is an undocumented FastAPI internal, so that
    fix would rebuild this very defect against a private attribute. DECISIONS #98.

    ``HEAD`` is skipped **only when ``GET`` is present on the same route**, because that is the only
    case Starlette synthesises — confirmed by execution: a ``GET``-declared FastAPI ``APIRoute``
    carries ``{'GET'}`` alone, while Starlette's own ``Route`` for ``/openapi.json`` carries
    ``{'GET', 'HEAD'}``. A ``HEAD`` that is a route's **sole** method was asked for explicitly and
    must declare itself. ``OPTIONS`` is **never** synthesised into ``route.methods`` — also
    confirmed by execution — so the v0.7.4 exemption for it fired on nothing and is gone.
    """
    for route in app.routes:
        if type(route) not in KNOWN_ROUTE_SHAPES:
            raise UndeclaredRouteError(
                f"{type(route).__module__}.{type(route).__name__} is a route shape this gate "
                "cannot check, so it is refused rather than skipped (F42). Registration goes "
                "through netcorenoc.api.declare.DeclaredRoutes, which produces a "
                "fastapi.routing.APIRoute. If a new shape is genuinely needed, it must be added to "
                "declare.KNOWN_ROUTE_SHAPES together with the traversal that checks every path and "
                "method it carries — never by skipping it."
            )
        # Narrowing only, no runtime effect: the check above has established that `route` is one of
        # the two known shapes, and both are `starlette.routing.Route` subclasses carrying `.path`
        # and `.methods` as declared attributes. That is exactly what the allowlist buys — v0.7.4
        # had to `getattr` its way through objects it could not name, and `getattr(..., None)` on an
        # object you cannot name is the fail-open this finding is about.
        checked = cast(Route, route)
        methods: set[str] = checked.methods or set()
        # **F43.** An empty method set is an *unverifiable* route, and it is refused by exactly the
        # same logic that refuses an unknown shape. The loop below is the whole of the per-route
        # check, and over an empty set it runs zero times: the route would be neither checked nor
        # refused. Starlette does not filter by verb when `methods` is falsy, so such a route serves
        # **every** verb — reproduced in `docs/gates/v0.8.0-phase-0.md` §7, where a
        # `Route("/admin/backdoor", ep, methods=[])` passed this gate and answered 200 to GET, POST,
        # PUT, DELETE, PATCH, HEAD and OPTIONS.
        #
        # This is the same fail-open F42 closed one level up, and the same reason: a guard whose
        # entire value is completeness may not have a branch that silently does nothing. There is no
        # verb to pass to `require_declaration`, so there is no way to check it — and the project's
        # posture on the uncheckable is to refuse, never to skip (DECISIONS #98, #106).
        #
        # `DeclaredRoutes` cannot produce this — its three verbs are literals — and FastAPI's own
        # non-decorator registration helper asserts a non-empty method list, so it cannot either.
        # The two reachable paths are appending a `Route` directly to `router.routes` and clearing
        # `methods` after registration. Latent, like F40/F41/F42.
        #
        # (That helper is deliberately not named here. The guard confining it to the static-asset
        # allowlist counts textual *mentions* rather than calls, so naming it even in a comment
        # fails that test — a known limitation recorded on `docs/ROADMAP.md`. v0.7.4 met the same
        # wall and reworded its prose rather than the test; this follows that precedent instead of
        # widening scope to rebuild the guard. `docs/gates/v0.8.0-phase-2.md` names it in full.)
        if not methods:
            raise UndeclaredRouteError(
                f"{checked.path} is registered with an empty method set, which this gate cannot "
                "check: there is no verb to look up in rbac.ROUTE_PERMISSIONS or rbac.ROUTE_SCOPE. "
                "Starlette does not filter by verb when `methods` is falsy, so such a route serves "
                "every verb — it is refused rather than skipped (F43). Register through "
                "netcorenoc.api.declare.DeclaredRoutes, which always names the verb."
            )
        for method in sorted(methods):
            if method == "HEAD" and "GET" in methods:
                continue  # Starlette synthesises HEAD alongside GET; not a separate declaration
            require_declaration(method, checked.path)


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
