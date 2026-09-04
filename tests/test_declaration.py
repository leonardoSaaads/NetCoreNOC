"""The route-declaration discipline (v0.7.2, Workstream 3).

F34 existed because a route's **scope posture** was expressed nowhere at all: three editor write
routes simply did not have one, and no table, no test and no reviewer could notice the omission.
`rbac.ROUTE_SCOPE` is that missing declaration, and these tests are what make it worth having:

* an undeclared route cannot be registered — the failure happens while the application is being
  built, so an appliance carrying one does not start;
* the gate is the **only** registration path, so it cannot be bypassed by a contributor in a hurry;
* both declaration tables are complete against the **live app object**, so they cannot drift;
* and every declared posture is checked against the route's **observed behaviour**, which is what
  makes the table a fact about the code rather than a comment on it (DECISIONS #80).
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.routing import Route

from netcorenoc.api import declare, routes_static
from netcorenoc.api.app import create_app
from netcorenoc.api.declare import DeclaredRoutes, UndeclaredRouteError, require_declaration
from netcorenoc.crosscutting import auth, rbac
from netcorenoc.store import Store

import authutil

PKG = Path(__file__).resolve().parent.parent / "src" / "netcorenoc" / "api"
_RAW_DECORATORS = ("@app.get(", "@app.post(", "@app.delete(", "@app.put(", "@app.patch(")
BASE = 1_700_000_000.0


# --- the gate itself ---------------------------------------------------------------------


def test_an_undeclared_api_route_cannot_be_registered() -> None:
    """The headline guarantee: registration raises, so the process never starts with it."""
    route = DeclaredRoutes(FastAPI())
    with pytest.raises(UndeclaredRouteError) as excinfo:

        @route.get("/api/undeclared")
        async def _handler() -> dict[str, str]:  # pragma: no cover - never registered
            return {}

    message = str(excinfo.value)
    assert "GET /api/undeclared" in message
    assert "rbac.ROUTE_PERMISSIONS" in message and "rbac.ROUTE_SCOPE" in message


def test_a_route_declared_only_in_route_permissions_is_still_refused() -> None:
    """Half a declaration is not a declaration. The message names the table that is missing."""
    route = DeclaredRoutes(FastAPI())
    key = ("GET", "/api/half-declared")
    rbac.ROUTE_PERMISSIONS[key] = "stats.read"
    try:
        with pytest.raises(UndeclaredRouteError) as excinfo:

            @route.get("/api/half-declared")
            async def _handler() -> dict[str, str]:  # pragma: no cover - never registered
                return {}

        assert "rbac.ROUTE_SCOPE" in str(excinfo.value)
        assert "rbac.ROUTE_PERMISSIONS" not in str(excinfo.value)
    finally:
        del rbac.ROUTE_PERMISSIONS[key]


def test_public_and_non_api_routes_are_exempt_by_consultation() -> None:
    """`POST /api/login` is exempt because `PUBLIC_ROUTES` says so, not because both tables
    happen to omit it — and `/healthz`-class paths are exempt because they carry no identity."""
    assert ("POST", "/api/login") in rbac.PUBLIC_ROUTES
    assert ("POST", "/api/login") not in rbac.ROUTE_PERMISSIONS
    assert ("POST", "/api/login") not in rbac.ROUTE_SCOPE
    route = DeclaredRoutes(FastAPI())

    @route.post("/api/login")
    async def _login() -> dict[str, str]:  # pragma: no cover - not called
        return {}

    @route.get("/healthz")
    async def _healthz() -> dict[str, str]:  # pragma: no cover - not called
        return {}


def test_the_gate_is_the_only_registration_path() -> None:
    """No raw `@app.<verb>` decorator anywhere under `netcorenoc/api/`.

    The discipline is only a discipline if there is no way round it. `add_api_route` is the one
    remaining non-decorator registration and it is confined to `routes_static.py`, where it serves
    a compile-time allowlist of non-`/api` files — see the test below.
    """
    offenders = [
        f"{path.name}:{n}: {line.strip()}"
        for path in sorted(PKG.rglob("*.py"))
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if line.lstrip().startswith(_RAW_DECORATORS)
    ]
    assert not offenders, (
        "routes must be registered through DeclaredRoutes, never on the FastAPI app directly:\n  "
        + "\n  ".join(offenders)
    )


def test_add_api_route_is_confined_to_the_static_asset_allowlist() -> None:
    """The one registration that is not a decorator, named rather than left to be found.

    `_asset_route` registers the four entries of `STATIC_ASSETS` — a compile-time allowlist of
    non-`/api` files served with no identity and no capability. It is outside the declaration gate
    by design (there is nothing for a static file to declare), and it must stay the only one.
    """
    sources = {path.name: path.read_text(encoding="utf-8") for path in PKG.rglob("*.py")}
    users = sorted(name for name, text in sources.items() if "add_api_route" in text)
    assert len(users) == 1, f"add_api_route must have exactly one caller, found {users}"
    assert "STATIC_ASSETS" in sources[users[0]], (
        f"add_api_route lives in {users[0]}, which does not own the static-asset allowlist"
    )


# --- v0.7.4: F40, the gate is complete for every registration path -----------------------


async def _create_app_with(store: Store, sabotage: Any) -> object:
    """Build a real app through `create_app`, with `sabotage(app)` run during registration.

    The extra route is added from inside `routes_events.register` — the last `register()` call —
    so it is present on the application *before* `create_app` returns, which is the only way to
    test that the post-hoc assertion fires where it is claimed to fire rather than in a test.
    """
    from netcorenoc.api import routes_events

    original = routes_events.register

    def patched(app: Any, ctx: Any) -> None:
        original(app, ctx)
        sabotage(app)

    routes_events.register = patched
    try:
        return (await authutil.make_env(store))[2]
    finally:
        routes_events.register = original


async def _handler() -> dict[str, str]:  # pragma: no cover - never actually served
    return {}


async def test_f40_a_route_registered_without_the_decorator_fails_create_app(store: Store) -> None:
    """**F40.** The hole: `DeclaredRoutes` wraps three verbs and only the decorator form, so a
    route registered directly on the FastAPI application reached the route table without ever
    consulting the gate — reproduced by execution in `docs/gates/v0.7.4-phase-0.md` §2.

    The fix is a post-hoc assertion over the built app, so it does not matter *how* the route got
    there. `create_app` must refuse to return.
    """
    with pytest.raises(UndeclaredRouteError) as excinfo:
        await _create_app_with(
            store,
            lambda app: app.add_api_route("/api/undeclared-bypass", _handler, methods=["GET"]),
        )
    assert "GET /api/undeclared-bypass" in str(excinfo.value)
    assert "rbac.ROUTE_PERMISSIONS" in str(excinfo.value)


@pytest.mark.parametrize("verb", ["put", "patch"])
async def test_f40_a_route_registered_by_an_unwrapped_verb_fails_create_app(
    store: Store, verb: str
) -> None:
    """**F40, the second uncovered path.** `DeclaredRoutes` has no `put` and no `patch`, so those
    verbs could only be registered on the app directly — and were therefore ungated.

    The post-hoc assertion covers them without anyone having enumerated them, which is the
    property that makes it complete by construction rather than by list: nothing in
    `assert_every_route_is_declared` mentions a verb.
    """
    path = f"/api/undeclared-{verb}"
    with pytest.raises(UndeclaredRouteError) as excinfo:
        await _create_app_with(store, lambda app: getattr(app, verb)(path)(_handler))
    assert f"{verb.upper()} {path}" in str(excinfo.value)


async def test_f40_the_assertion_runs_before_create_app_returns(store: Store) -> None:
    """It must stop the process, not fail only under test.

    A guard that runs in CI but not at startup is a guard an operator can ship past. `create_app`
    calls it as its last statement, so an appliance carrying an undeclared route does not start —
    the same class of guarantee as the capability ceiling.
    """
    source = inspect.getsource(create_app)
    assert "assert_every_route_is_declared(app)" in source
    body = source.split("assert_every_route_is_declared(app)", 1)[1]
    assert body.strip() == "return app", (
        "the declaration assertion must be the last thing create_app does before returning; "
        f"found {body.strip()!r} after it"
    )


def test_f40_the_decorator_time_refusal_is_kept_as_well() -> None:
    """The gate got a second check, not a replacement (build prompt directive 4).

    Failing at the point of registration names the route the contributor just wrote; failing at
    the end of `create_app` names it too but three hundred lines away. Both, therefore.
    """
    route = DeclaredRoutes(FastAPI())
    with pytest.raises(UndeclaredRouteError):
        route.get("/api/still-refused-at-registration")


# --- v0.7.4: F41, the exemption is an allowlist, not a path prefix -----------------------


@pytest.mark.parametrize("path", ["/metrics", "/admin/debug", "/status", "/vendor/other.js"])
def test_f41_a_non_api_path_outside_the_allowlist_is_refused(path: str) -> None:
    """**F41.** Before v0.7.4 `require_declaration` returned early for anything not under `/api`,
    so `/metrics` — already on the ROADMAP — would have been exempt **by accident**.

    Reproduced on the unmodified tree in `docs/gates/v0.7.4-phase-0.md` §2: all of these returned
    without raising.
    """
    with pytest.raises(UndeclaredRouteError) as excinfo:
        require_declaration("GET", path)
    assert "UNAUTHENTICATED_PATHS" in str(excinfo.value)


# The public surface, derived from what `routes_static.py` serves plus the health endpoints and
# FastAPI's schema route — deliberately **not** from `declare.UNAUTHENTICATED_PATHS`. Parametrising
# over the allowlist would make the test vacuous if the allowlist were ever emptied, and it would
# also make this file uncollectable against a tree that predates the constant, which is how the
# fail-on-the-unmodified-tree proof is taken (`docs/gates/v0.7.4-phase-2.md` §3).
PUBLIC_SURFACE = sorted(
    {"/healthz", "/readyz", "/", "/openapi.json"}
    | {f"/{asset}" for asset in routes_static.STATIC_ASSETS}
)


@pytest.mark.parametrize("path", PUBLIC_SURFACE)
def test_f41_every_currently_served_public_path_is_still_accepted(path: str) -> None:
    """The gate got stronger, never narrower (build prompt §4.3).

    Every path the appliance serves without an identity must still pass. If this fails, the fix
    removed a public route rather than closing a hole — a build failure, not a test to update.

    This one passes on the unmodified tree too, and that is the point: it is the direction the fix
    must **not** change.
    """
    require_declaration("GET", path)


def test_f41_the_allowlist_matches_what_is_actually_served() -> None:
    """The allowlist is *asserted against* `routes_static.py`, so the two cannot diverge.

    `declare.py` cannot import `routes_static.py` — that module registers through this one, and the
    import would be circular — so the derivation is checked here instead of performed there. Adding
    a static asset without listing it fails; listing a path that is not served fails too.
    """
    derived = {"/healthz", "/readyz", "/", "/openapi.json"} | {
        f"/{asset}" for asset in routes_static.STATIC_ASSETS
    }
    assert set(declare.UNAUTHENTICATED_PATHS) == derived, (
        "declare.UNAUTHENTICATED_PATHS has drifted from what routes_static.py registers:\n"
        f"  only in the allowlist: {sorted(set(declare.UNAUTHENTICATED_PATHS) - derived)}\n"
        f"  only served:           {sorted(derived - set(declare.UNAUTHENTICATED_PATHS))}"
    )


async def test_f41_the_allowlist_is_exactly_the_live_non_api_surface(store: Store) -> None:
    """The other direction, against the **built app** rather than against the source.

    `test_f41_the_allowlist_matches_what_is_actually_served` checks the allowlist against the
    module that registers the assets. This checks it against the routes that actually exist —
    including `/openapi.json`, which FastAPI registers itself and which no source-level derivation
    would ever see.
    """
    _engine, _queue, app = await authutil.make_env(store)
    live = {path for _method, path in _live_routes(app) if not path.startswith("/api")}
    assert live == set(declare.UNAUTHENTICATED_PATHS), (
        f"live non-/api paths {sorted(live)} != allowlist {sorted(declare.UNAUTHENTICATED_PATHS)}"
    )


def _live_routes(app: object) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for route in app.routes:  # type: ignore[attr-defined]
        path = getattr(route, "path", "")
        for method in getattr(route, "methods", set()) or set():
            if method not in ("HEAD", "OPTIONS"):
                out.append((method, path))
    return out


# --- v0.7.5: F42, the gate refuses the shapes it cannot check ----------------------------
#
# F40 closed the *registration paths*. It did not close the *route shapes*: the traversal named no
# registration mechanism, which was true, and assumed a flat object exposing `.path` and `.methods`,
# which was enumeration wearing construction's clothes. Every shape below was reproduced by
# execution, serving real traffic through a gate that reported nothing, in
# `docs/gates/v0.7.5-phase-0.md` §2.
#
# Every test in this block was proven to fail on the unmodified tree by stashing the fix; the output
# is pasted in `docs/gates/v0.7.5-phase-2.md` §2.


async def _ws_handler(websocket: Any) -> None:  # pragma: no cover - never actually served
    return None


F42_SHAPES: tuple[tuple[str, Callable[[Any], None], str], ...] = (
    (
        "include_router",
        lambda app: app.include_router(_undeclared_router()),
        "_IncludedRouter",
    ),
    (
        "mount_subapp",
        lambda app: app.mount("/api/sub", FastAPI(docs_url=None, redoc_url=None)),
        "Mount",
    ),
    (
        "mount_staticfiles",
        lambda app: app.mount("/api/static", StaticFiles(directory=str(PKG))),
        "Mount",
    ),
    (
        "websocket",
        lambda app: app.add_api_websocket_route("/api/undeclared-ws", _ws_handler),
        "APIWebSocketRoute",
    ),
)


def _undeclared_router() -> APIRouter:
    router = APIRouter()
    router.add_api_route("/api/undeclared-via-router", _handler, methods=["GET"])
    return router


def _shape_names(shapes: set[type]) -> list[str]:
    return sorted(f"{shape.__module__}.{shape.__name__}" for shape in shapes)


@pytest.mark.parametrize("name,sabotage,shape", F42_SHAPES, ids=[s[0] for s in F42_SHAPES])
async def test_f42_a_route_shape_the_gate_cannot_check_is_refused(
    store: Store, name: str, sabotage: Callable[[Any], None], shape: str
) -> None:
    """**F42.** Four shapes reached a running process unchecked. Each now stops `create_app`.

    Two of them — both `Mount`s — and the websocket route evade through the **empty-methods**
    branch rather than through a missing `.path`; the `_IncludedRouter` evades through the missing
    `.path`, and only on newer FastAPI. That is why the fix refuses on the **class** instead of
    patching either branch: patching the branch the finding's brief described would have closed one
    shape out of the four.
    """
    with pytest.raises(UndeclaredRouteError) as excinfo:
        await _create_app_with(store, sabotage)
    assert shape in str(excinfo.value), f"{name}: message does not name the offending shape"


@pytest.mark.parametrize("name,sabotage,shape", F42_SHAPES, ids=[s[0] for s in F42_SHAPES])
def test_f42_the_refusal_names_the_offending_class_and_the_way_in(
    name: str, sabotage: Callable[[Any], None], shape: str
) -> None:
    """The message must name the class *and* say where registration belongs.

    A refusal that says only "unknown route shape" tells a contributor that something is wrong and
    not what or where. `type(route).__module__` and `type(route).__name__` identify the object;
    naming `DeclaredRoutes` identifies the fix.
    """
    app = FastAPI(docs_url=None, redoc_url=None)
    sabotage(app)
    with pytest.raises(UndeclaredRouteError) as excinfo:
        declare.assert_every_route_is_declared(app)
    message = str(excinfo.value)
    assert shape in message
    assert message.startswith(("fastapi.routing.", "starlette.routing.")), message
    assert "DeclaredRoutes" in message
    assert "KNOWN_ROUTE_SHAPES" in message


def test_f42_an_untouched_app_is_not_refused() -> None:
    """**The control.** Half of F42's evidence is worthless without it.

    A bare `FastAPI()` carries an undeclared `/docs`, so a probe built on one would make every
    result look like a catch — the first probe of this finding was invalid for exactly that reason.
    This app matches `create_app`: `docs_url=None, redoc_url=None`, one declared route.
    """
    app = FastAPI(docs_url=None, redoc_url=None)
    app.get("/healthz")(_handler)
    declare.assert_every_route_is_declared(app)  # must not raise


def test_f42_a_head_only_route_is_refused_because_it_was_asked_for_explicitly() -> None:
    """v0.7.4 skipped `HEAD` unconditionally, on the theory that FastAPI synthesises it.

    It does not: a `GET`-declared `APIRoute` carries `{'GET'}` alone (see the pair test below).
    A `HEAD` that is a route's **sole** method was written by hand and is a declaration like any
    other.
    """
    app = FastAPI(docs_url=None, redoc_url=None)
    app.add_api_route("/api/undeclared-head", _handler, methods=["HEAD"])
    with pytest.raises(UndeclaredRouteError) as excinfo:
        declare.assert_every_route_is_declared(app)
    assert "HEAD /api/undeclared-head" in str(excinfo.value)


def test_f42_a_get_plus_head_pair_is_not_double_checked() -> None:
    """The other direction of the same rule, and the reason it is narrowed rather than removed.

    Starlette's own `Route` synthesises `HEAD` alongside `GET` — `/openapi.json` is the live
    example, carrying `{'GET', 'HEAD'}`. That `HEAD` is not a separate declaration and never was, so
    checking it would demand a declaration for a verb nobody wrote. `/healthz` is in
    `UNAUTHENTICATED_PATHS`, so a refusal here would name it.
    """
    app = FastAPI(docs_url=None, redoc_url=None)
    app.add_api_route("/healthz", _handler, methods=["GET", "HEAD"])
    declare.assert_every_route_is_declared(app)  # must not raise

    # …and the narrowing is real: drop the GET and the same HEAD is refused.
    solo = FastAPI(docs_url=None, redoc_url=None)
    solo.add_api_route("/api/head-alone", _handler, methods=["HEAD"])
    with pytest.raises(UndeclaredRouteError):
        declare.assert_every_route_is_declared(solo)


def test_f42_options_is_never_synthesised_so_nothing_exempts_it() -> None:
    """v0.7.4 exempted `OPTIONS` as well. Confirmed by execution: it fires on nothing.

    FastAPI does not put `OPTIONS` in `route.methods`, so the exemption never applied to a real
    route — and an `OPTIONS` route someone registers deliberately must declare itself. The
    exemption is gone; this test is what stops it coming back as a "harmless" symmetry with `HEAD`.
    """
    app = FastAPI(docs_url=None, redoc_url=None)
    app.get("/healthz")(_handler)
    for route in app.routes:
        if getattr(route, "path", None) == "/healthz":
            assert "OPTIONS" not in (getattr(route, "methods", None) or set())

    explicit = FastAPI(docs_url=None, redoc_url=None)
    explicit.add_api_route("/api/undeclared-options", _handler, methods=["OPTIONS"])
    with pytest.raises(UndeclaredRouteError) as excinfo:
        declare.assert_every_route_is_declared(explicit)
    assert "OPTIONS /api/undeclared-options" in str(excinfo.value)


# --- F43: an empty method set is unverifiable, and unverifiable means refused ------------


def _empty_method_route(path: str = "/admin/backdoor") -> Route:
    """A `Route` of a **known** shape carrying no verb — the F43 shape.

    Deliberately built by hand rather than through any registration helper: FastAPI's own
    non-decorator helper asserts a non-empty method list, and `DeclaredRoutes` passes literal
    verbs, so neither can produce this. The two reachable paths are appending one of these to
    `router.routes` and clearing `methods` after registration, and both are exercised below.
    """
    return Route(path, _handler, methods=[])


def test_f43_a_known_shape_with_no_methods_is_refused() -> None:
    """**F43.** The route the v0.7.5 gate neither checked nor refused.

    `assert_every_route_is_declared` iterates `route.methods`; over an empty set that loop runs
    **zero times**, so the route fell through the whole check silently. Its type *is* in
    `KNOWN_ROUTE_SHAPES`, so F42's refusal does not fire either — this is the gap between the two
    findings, and it is why the shape check alone did not make the completeness claim true.

    Proven to fail on the unmodified tree: with the `if not methods:` branch removed from
    `declare.assert_every_route_is_declared`, this test does not raise and fails on the
    `pytest.raises` assertion (`docs/gates/v0.8.0-phase-2.md` §2).
    """
    app = FastAPI(docs_url=None, redoc_url=None)
    app.router.routes.append(_empty_method_route())
    with pytest.raises(UndeclaredRouteError) as excinfo:
        declare.assert_every_route_is_declared(app)
    message = str(excinfo.value)
    assert "/admin/backdoor" in message
    assert "empty method set" in message
    assert "DeclaredRoutes" in message, "the refusal must say where registration belongs"


def test_f43_clearing_methods_after_registration_is_refused_too() -> None:
    """The second reachable path, and the one a real defect would take.

    Appending a bare `Route` is conspicuous. Mutating `methods` on a route that was registered
    correctly is not: the declaration ran, the gate saw `{'GET'}`, and the emptying happens
    afterwards. The result-inspecting traversal is the only thing positioned to catch it, which is
    exactly the argument F40 made for having it at all.
    """
    app = FastAPI(docs_url=None, redoc_url=None)
    app.get("/healthz")(_handler)
    declare.assert_every_route_is_declared(app)  # the control: it passes before the mutation

    target = next(r for r in app.router.routes if getattr(r, "path", None) == "/healthz")
    target.methods = set()  # type: ignore[attr-defined]
    with pytest.raises(UndeclaredRouteError) as excinfo:
        declare.assert_every_route_is_declared(app)
    assert "/healthz" in str(excinfo.value)


def test_f43_the_unreachable_construction_paths_stay_unreachable() -> None:
    """Reachability, asserted rather than described.

    The finding is **latent** — no route in this repository is registered either way — and that
    claim is only worth making if the two paths said to be closed really are. FastAPI's own
    non-decorator registration helper asserts a non-empty method list; `DeclaredRoutes` passes
    literal verbs. If a dependency upgrade ever relaxed the first, this test says so.
    """
    app = FastAPI(docs_url=None, redoc_url=None)
    with pytest.raises(AssertionError):
        app.add_api_route("/api/x", _handler, methods=[])

    for verb in ("get", "post", "delete"):
        source = inspect.getsource(getattr(DeclaredRoutes, verb))
        assert f'require_declaration("{verb.upper()}"' in source, (
            f"DeclaredRoutes.{verb} no longer passes a literal verb to the gate, so it could now "
            "produce the empty method set F43 is about"
        )


def test_f43_an_untouched_app_is_not_refused() -> None:
    """**The control.** Without it the four assertions above prove only that the gate raises.

    Same posture as `create_app`: `docs_url=None, redoc_url=None`, and routes that carry verbs.
    """
    app = FastAPI(docs_url=None, redoc_url=None)
    app.get("/healthz")(_handler)
    app.add_api_route("/openapi.json", _handler, methods=["GET", "HEAD"])
    declare.assert_every_route_is_declared(app)  # must not raise


async def test_f43_every_path_served_today_still_registers(store: Store) -> None:
    """The live control: the gate got stricter and the served surface did not move.

    The sibling of `test_f42_every_path_served_today_still_registers`, repeated for this finding
    because a refusal added in the per-route loop is exactly the kind of change that could reject a
    route the appliance depends on — and `create_app` calls this gate before returning, so the
    failure mode is an appliance that does not start.
    """
    _engine, _queue, app = await authutil.make_env(store)
    served = _live_routes(app)
    # v0.13.0: 52 -> 88. Every one of the 36 new pairs was a **static UI module** (ADR #175).
    # v0.14.0: 88 -> 90, and both new pairs are static UI modules too — `app/views/model.js` and
    # `app/views/verdict.js`.
    # v0.15.3: 90 -> 93, and all three again are static UI modules — `app/icons.js` (the drawn
    # icon family, #236), `app/password.js` (the confirmation, meter and reveal the sign-in card
    # and the account screen share, V.2) and `app/views/parts/why.js` (the redesigned "Why these
    # were grouped", V.6). The four `views/*.js` that became `views/parts/*.js` (#239) are a
    # rename and net zero. **No `/api` route was added, removed or renamed by this
    # release either**, which is the property this count is really guarding and which the
    # assertion below states directly: a release that repaired the lockout, redrew the console and
    # made it responsive changed the served API surface by exactly nothing. What DID change is the
    # shape of two existing responses (`/api/me` and the login route gained `password_policy`;
    # `/api/users` rows gained `sole_admin`) — a body, not a path, and the behaviour-identity
    # record is where that is pinned.
    # v0.16.0: 93 -> 100. **Five of the seven are `/api` routes** — the first release since
    # v0.13.0 to add one at all. Move, merge, operator-split, name and the zombie clear: five
    # gestures, five routes, five declarations rather than one overloaded route that would need
    # none of them (DECISIONS #255, #260). The other two are static UI modules,
    # `app/views/parts/lifecycle.js` (the gestures, their confidence control and the history list)
    # and `app/views/parts/members.js` (the member table, split out when the situations view
    # reached the module-graph guard).
    assert len(served) == 100, f"the served surface moved: {len(served)} method/path pairs"
    api_pairs = {(method, path) for method, path in served if path.startswith("/api")}
    assert len(api_pairs) == 49, (
        f"the /api surface moved: {len(api_pairs)} pairs. v0.16.0 adds exactly five — the "
        f"operator's five gestures — and v0.13.0/v0.14.0/v0.15.3 added none at all. A change "
        f"here means something else happened."
    )
    for _method, path in served:
        route = next(r for r in app.routes if getattr(r, "path", None) == path)  # type: ignore[attr-defined]
        assert getattr(route, "methods", None), f"{path} carries no verb, which F43 now refuses"


async def test_f42_the_live_app_produces_exactly_the_known_shapes(store: Store) -> None:
    """**The half that generalises — read this before changing `KNOWN_ROUTE_SHAPES`.**

    The specific hole is fifteen lines. The lesson is that a guard depending on an unpinned
    dependency's internal representation can regress with **no commit and no failing test**: the
    `include_router` shape was refused on `fastapi==0.115.0`, the floor of this project's own pin,
    and skipped on `0.141.1` (`docs/gates/v0.7.5-phase-0.md` §2.4). `pyproject.toml` carries no
    upper bound, there is no lockfile, and CI runs a bare `pip install -e .[dev]`.

    So this asserts the set of route classes a **real** `create_app` produces is exactly the known
    set. A FastAPI upgrade that changes the representation then fails here loudly, naming the new
    class, on the day of the upgrade — instead of silently widening the hole.

    **`KNOWN_ROUTE_SHAPES` is enumeration**, stated plainly because the same honesty was demanded of
    the documentation guard in v0.7.4. This test is what makes the enumeration *maintained* rather
    than merely written down, and it is not a substitute for it being a list. Its limit is recorded
    on `docs/ROADMAP.md`: it detects a **new shape**, not a **changed meaning**. If a future
    `APIRoute` carried its verbs somewhere other than `.methods`, the shape set would be unchanged
    and the gate would quietly check nothing.
    """
    _engine, _queue, app = await authutil.make_env(store)
    live = {type(route) for route in app.routes}  # type: ignore[attr-defined]
    known = set(declare.KNOWN_ROUTE_SHAPES)
    assert live == known, (
        "the shapes a real create_app produces are no longer exactly declare.KNOWN_ROUTE_SHAPES:\n"
        f"  produced but not known: {_shape_names(live - known)}\n"
        f"  known but not produced: {_shape_names(known - live)}\n"
        "If a dependency upgrade changed the representation, do NOT widen the tuple until the "
        "traversal in assert_every_route_is_declared can check every path and method the new shape "
        "carries. Widening it without that is how the hole F42 closed would reopen."
    )


async def test_f42_every_path_served_today_still_registers(store: Store) -> None:
    """The gate got stricter and no looser: a refusal was added, none was removed.

    If this fails, the served surface has changed — which is a build failure, not a test to update.
    """
    _engine, _queue, app = await authutil.make_env(store)
    served = _live_routes(app)
    # v0.13.0: 52 -> 88. Every one of the 36 new pairs was a **static UI module** (ADR #175).
    # v0.14.0: 88 -> 90, and both new pairs are static UI modules too — `app/views/model.js` and
    # `app/views/verdict.js`.
    # v0.15.3: 90 -> 93, and all three again are static UI modules — `app/icons.js` (the drawn
    # icon family, #236), `app/password.js` (the confirmation, meter and reveal the sign-in card
    # and the account screen share, V.2) and `app/views/parts/why.js` (the redesigned "Why these
    # were grouped", V.6). The four `views/*.js` that became `views/parts/*.js` (#239) are a
    # rename and net zero. **No `/api` route was added, removed or renamed by this
    # release either**, which is the property this count is really guarding and which the
    # assertion below states directly: a release that repaired the lockout, redrew the console and
    # made it responsive changed the served API surface by exactly nothing. What DID change is the
    # shape of two existing responses (`/api/me` and the login route gained `password_policy`;
    # `/api/users` rows gained `sole_admin`) — a body, not a path, and the behaviour-identity
    # record is where that is pinned.
    # v0.16.0: 93 -> 100. **Five of the seven are `/api` routes** — the first release since
    # v0.13.0 to add one at all. Move, merge, operator-split, name and the zombie clear: five
    # gestures, five routes, five declarations rather than one overloaded route that would need
    # none of them (DECISIONS #255, #260). The other two are static UI modules,
    # `app/views/parts/lifecycle.js` (the gestures, their confidence control and the history list)
    # and `app/views/parts/members.js` (the member table, split out when the situations view
    # reached the module-graph guard).
    assert len(served) == 100, f"the served surface moved: {len(served)} method/path pairs"
    api_pairs = {(method, path) for method, path in served if path.startswith("/api")}
    assert len(api_pairs) == 49, (
        f"the /api surface moved: {len(api_pairs)} pairs. v0.16.0 adds exactly five — the "
        f"operator's five gestures — and v0.13.0/v0.14.0/v0.15.3 added none at all. A change "
        f"here means something else happened."
    )
    paths = {path for _method, path in served}
    for public in declare.UNAUTHENTICATED_PATHS:
        assert public in paths, f"{public} is no longer served"


# --- completeness, driven from the live app so it cannot drift ---------------------------


def _live_api_routes(app: object) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for route in app.routes:  # type: ignore[attr-defined]
        path = getattr(route, "path", "")
        if not path.startswith("/api"):
            continue
        for method in getattr(route, "methods", set()) or set():
            if method not in ("HEAD", "OPTIONS"):
                out.append((method, path))
    return out


async def test_every_api_route_declares_a_scope_posture(store: Store) -> None:
    """The sibling of `test_rbac.py::test_every_api_route_is_in_the_permission_map`.

    Extended, not replaced: `ROUTE_PERMISSIONS` keeps its own completeness test and its own
    authority. This one asks the second question F34 showed nobody was asking.
    """
    _engine, _queue, app = await authutil.make_env(store)
    undeclared = [
        f"{method} {path}"
        for method, path in _live_api_routes(app)
        if (method, path) not in rbac.PUBLIC_ROUTES and (method, path) not in rbac.ROUTE_SCOPE
    ]
    assert not undeclared, f"/api routes with no declared scope posture: {undeclared}"


async def test_no_scope_declaration_is_dead(store: Store) -> None:
    """A posture for a route that no longer exists would quietly stop meaning anything."""
    _engine, _queue, app = await authutil.make_env(store)
    live = set(_live_api_routes(app))
    dead = [f"{method} {path}" for method, path in rbac.ROUTE_SCOPE if (method, path) not in live]
    assert not dead, f"ROUTE_SCOPE declares routes that are not registered: {dead}"


def test_admin_only_is_derived_from_permissions_in_both_directions() -> None:
    """`admin_only` is a claim about `PERMISSIONS`, re-derived here as well as at import.

    A second authority would be a second thing to keep true. This is the same assertion `rbac.py`
    makes when it is imported, restated where a reader looking for tests will find it.
    """
    for route, posture in rbac.ROUTE_SCOPE.items():
        admin_capability = rbac.PERMISSIONS[rbac.ROUTE_PERMISSIONS[route]] == "admin"
        assert (posture == "admin_only") == admin_capability, (route, posture)


def test_every_unscoped_declaration_carries_a_written_justification() -> None:
    """`"unscoped"` is the posture that can hide a defect, so each one must state why.

    A comment is not a proof — the behavioural test below is — but an `"unscoped"` entry with no
    stated reason is an assertion nobody has had to defend, which is how F34 happened.
    """
    source = (Path(rbac.tables.__file__)).read_text(encoding="utf-8")
    table = source.split("ROUTE_SCOPE: dict[", 1)[1].split("\n}\n", 1)[0]
    lines = table.splitlines()
    entries = [i for i, line in enumerate(lines) if line.strip().endswith(': "unscoped",')]
    # v0.11.0 adds `GET /api/promotion`: a refusal EXPLAINS why the correlator is still what
    # it is, and names no network element. Six now, and the count is asserted exactly so a
    # seventh cannot arrive without this line moving.
    assert len(entries) == 6, entries
    unjustified = [lines[i].strip() for i in entries if not lines[i - 1].strip().startswith("#")]
    assert not unjustified, (
        "every `unscoped` route must be preceded by a comment saying why it is not scoped:\n  "
        + "\n  ".join(unjustified)
    )


# --- the postures, checked against observed behaviour ------------------------------------

_CONCRETE = {"{sid}": "1", "{ne_id}": "1", "{uid}": "1", "{tid}": "1", "{aid}": "1"}


def _concrete(path: str) -> str:
    for template, value in _CONCRETE.items():
        path = path.replace(template, value)
    return path


async def _seed(store: Store) -> None:
    """Two NEs on different /24s, so a scope policy can include one and exclude the other."""
    async with store.lock:
        for ip in ("10.0.0.1", "192.168.50.1"):
            await store.device_id(ip, BASE)
            ne_id = await store.ne_id(ip, BASE)
            await store.entity_level0(ne_id, ip, BASE)
        await store.commit()


async def _activate_scope(store: Store, document: dict[str, Any] | None) -> None:
    """Install (or clear) a scope policy directly, bypassing the API."""
    async with store.lock:
        if document is None:
            await store.clear_active_governance_policy("scope")
        else:
            text = json.dumps(document, sort_keys=True, separators=(",", ":"))
            policy_id = await store.insert_governance_policy(
                "scope", text, "z" * 64, None, BASE, ""
            )
            await store.set_active_governance_policy("scope", policy_id, None, BASE)
        await store.commit()


# Viewer and editor see only 10.0.0.0/8 — so 192.168.50.1 and everything on it is invisible.
NARROW = {"version": 1, "roles": {"viewer": ["10.0.0.0/8"], "editor": ["10.0.0.0/8"]}}

ADMIN_ONLY = sorted(r for r, p in rbac.ROUTE_SCOPE.items() if p == "admin_only")
UNSCOPED = sorted(r for r, p in rbac.ROUTE_SCOPE.items() if p == "unscoped")
SCOPED = sorted(r for r, p in rbac.ROUTE_SCOPE.items() if p == "scoped")


def test_the_three_postures_are_all_populated() -> None:
    """Guard the guard: a posture with no routes would make its behavioural test vacuous."""
    # v0.8.0: 22 -> 24. The two dataset-retention routes are `admin_only`, and they could not
    # be anything else: the dataset is a scope bypass by construction (captured engine-side,
    # where visibility scoping does not exist), so there is no scoped view of it to build.
    # v0.11.0: 24 -> 25 and 5 -> 6. `POST /api/promotion` is `admin_only` because its capability's
    # minimum role is `admin` — the posture is DERIVED, never asserted independently — and
    # `GET /api/promotion` is `unscoped` for `GET /api/scorer`'s reason: a decision about the
    # correlator's arithmetic names no network element.
    assert len(ADMIN_ONLY) == 25, len(ADMIN_ONLY)
    assert len(UNSCOPED) == 6, UNSCOPED
    # v0.16.0: 12 -> 17. Every one of the five gestures names a network element and every one
    # is below `admin`, so every one is `scoped` — the write perimeter F34 established,
    # widened by exactly the routes this release adds.
    assert len(SCOPED) == 17, SCOPED
    assert len(rbac.ROUTE_SCOPE) == len(ADMIN_ONLY) + len(UNSCOPED) + len(SCOPED)


async def _status(client: httpx.AsyncClient, method: str, path: str) -> int:
    if path == "/api/events":
        # The SSE stream never ends; the security dependency raises before streaming, so a
        # timeout means "authorization passed" — the same convention test_rbac.py uses.
        try:
            resp = await asyncio.wait_for(client.get(_concrete(path)), timeout=0.6)
            return resp.status_code
        except (TimeoutError, httpx.ReadError):
            return 200
    async with client.stream(method, _concrete(path), json=_BODIES.get((method, path))) as resp:
        return resp.status_code


_BODIES: dict[tuple[str, str], dict[str, Any]] = {
    ("POST", "/api/situations/{sid}/feedback"): {"verdict": "confirm"},
    ("POST", "/api/labels"): {"kind": "device", "id": 1, "label": "x"},
    # v0.16.0. The five gestures, in shapes that pass validation so the *posture* is what is
    # observed rather than a 422 from the request model.
    ("POST", "/api/situations/{sid}/move"): {
        "alarm_id": 1,
        "to_situation_id": 2,
        "confidence": 0.9,
    },
    ("POST", "/api/situations/{sid}/merge"): {"from_situation_id": 2, "confidence": 0.9},
    ("POST", "/api/situations/{sid}/split"): {"alarm_ids": [1], "confidence": 0.9},
    ("POST", "/api/situations/{sid}/name"): {"name": "x"},
    ("POST", "/api/alarms/{aid}/clear"): {},
    ("POST", "/api/users"): {"username": "x", "password": "x" * 14, "role": "viewer"},
    ("POST", "/api/users/{uid}/role"): {"role": "viewer"},
    ("POST", "/api/tokens"): {"name": "t", "role": "viewer"},
    ("POST", "/api/config"): {"allowlist": "", "retention_days": 7},
    ("POST", "/api/scorer"): {"w_t": 0.5, "w_a": 0.3, "w_e": 0.2, "tau_s": 60, "threshold": 0.5},
    ("POST", "/api/scorer/preview"): {
        "w_t": 0.5,
        "w_a": 0.3,
        "w_e": 0.2,
        "tau_s": 60,
        "threshold": 0.5,
    },
    ("POST", "/api/scorer/rollback"): {"config_id": 1},
    ("POST", "/api/rbac"): {"clear": True},
    ("POST", "/api/scope"): {"clear": True},
}


@pytest.mark.parametrize("route", ADMIN_ONLY, ids=lambda r: f"{r[0]} {r[1]}")
async def test_admin_only_routes_are_403_for_viewer_and_editor(
    store: Store, route: tuple[str, str]
) -> None:
    """`admin_only` claims the question does not arise because only admins get here."""
    _engine, _queue, app = await authutil.make_env(store)
    method, path = route
    for role in ("viewer", "editor"):
        client = await authutil.client_as(app, role)
        try:
            assert await _status(client, method, path) == 403, (role, method, path)
        finally:
            await client.aclose()


@pytest.mark.parametrize("route", UNSCOPED, ids=lambda r: f"{r[0]} {r[1]}")
async def test_unscoped_routes_answer_a_scoped_caller_identically(
    store: Store, route: tuple[str, str]
) -> None:
    """`unscoped` claims the caller's scope does not reach the response. So: run the request
    twice as the same role, once under a policy that hides half the estate and once under none,
    and require the same status and the same body."""
    _engine, _queue, app = await authutil.make_env(store)
    await _seed(store)
    method, path = route
    seen: list[tuple[int, bytes]] = []
    for document in (NARROW, None):
        await _activate_scope(store, document)
        client = await authutil.client_as(app, "editor")
        try:
            resp = await client.request(method, _concrete(path), json=_BODIES.get(route))
            seen.append((resp.status_code, resp.content))
        finally:
            await client.aclose()
    assert seen[0] == seen[1], f"{method} {path} is declared unscoped but the scope reached it"


SCOPED_TARGETED = [
    ("GET", "/api/situations/{sid}"),
    ("GET", "/api/entities/{ne_id}"),
    ("POST", "/api/situations/{sid}/feedback"),
    ("POST", "/api/labels"),
    ("POST", "/api/situations/{sid}/close"),
    # v0.16.0: all five gestures name a resource, so all five are the targeted form — an
    # out-of-scope target takes the same 404 a nonexistent one does. `move` and `merge` name
    # **two** situations and check both, which is why they cannot be one route (DECISIONS #255).
    ("POST", "/api/situations/{sid}/move"),
    ("POST", "/api/situations/{sid}/merge"),
    ("POST", "/api/situations/{sid}/split"),
    ("POST", "/api/situations/{sid}/name"),
    ("POST", "/api/alarms/{aid}/clear"),
]
SCOPED_COLLECTION = [r for r in SCOPED if r not in SCOPED_TARGETED]


def test_the_scoped_split_covers_every_scoped_route() -> None:
    assert sorted(SCOPED_TARGETED + SCOPED_COLLECTION) == SCOPED


@pytest.mark.parametrize("route", SCOPED_TARGETED, ids=lambda r: f"{r[0]} {r[1]}")
async def test_scoped_routes_404_an_out_of_scope_target(
    store: Store, route: tuple[str, str]
) -> None:
    """`scoped`, targeted form: naming a resource the caller cannot see is indistinguishable
    from naming one that does not exist — same status, same body (DECISIONS #60, #65)."""
    engine, queue, app = await authutil.make_env(store)
    await _seed(store)
    out_of_scope_ne = await _out_of_scope_ids(store, engine, queue)
    await _activate_scope(store, NARROW)
    method, path = route
    body = dict(_BODIES.get(route) or {})
    if path == "/api/labels":
        body = {"kind": "device", "id": out_of_scope_ne["device"], "label": "x"}
    if path == "/api/situations/{sid}/move":
        body = {
            "alarm_id": out_of_scope_ne["alarm"],
            "to_situation_id": out_of_scope_ne["situation"],
            "confidence": 0.9,
        }
    if path == "/api/situations/{sid}/split":
        body = {"alarm_ids": [out_of_scope_ne["alarm"]], "confidence": 0.9}
    if path == "/api/situations/{sid}/merge":
        body = {"from_situation_id": out_of_scope_ne["situation"], "confidence": 0.9}
    concrete = (
        path.replace("{sid}", str(out_of_scope_ne["situation"]))
        .replace("{ne_id}", str(out_of_scope_ne["ne"]))
        .replace("{aid}", str(out_of_scope_ne["alarm"]))
    )
    client = await authutil.client_as(app, "editor")
    try:
        resp = await client.request(method, concrete, json=body or None)
        assert resp.status_code == 404, (method, concrete, resp.status_code, resp.text)
    finally:
        await client.aclose()


@pytest.mark.parametrize("route", SCOPED_COLLECTION, ids=lambda r: f"{r[0]} {r[1]}")
async def test_scoped_collection_routes_answer_differently_under_a_policy(
    store: Store, route: tuple[str, str]
) -> None:
    """`scoped`, collection form: the response is a function of the caller's visible set, so a
    policy that hides half the estate must change what comes back."""
    engine, queue, app = await authutil.make_env(store)
    await _out_of_scope_ids(store, engine, queue)
    method, path = route
    seen: list[bytes] = []
    for document in (None, NARROW):
        await _activate_scope(store, document)
        client = await authutil.client_as(app, "editor")
        try:
            seen.append(await _first_payload(app, client, method, path))
        finally:
            await client.aclose()
    assert seen[0] != seen[1], f"{method} {path} is declared scoped but the policy changed nothing"


async def _first_payload(app: object, client: httpx.AsyncClient, method: str, path: str) -> bytes:
    """The response body, or — for the SSE stream — its first `data:` frame."""
    if path != "/api/events":
        resp = await client.request(method, _concrete(path))
        assert resp.status_code == 200, (path, resp.status_code, resp.text)
        return resp.content
    cookie = client.cookies.get(auth.COOKIE_NAME)
    assert cookie is not None
    return await _first_sse_update(app, cookie)


class _StopSSEError(Exception):
    """Break out of the infinite SSE generator once the first update is captured."""


async def _first_sse_update(app: object, cookie: str) -> bytes:
    """The stream's first `data:` payload, driving the ASGI app directly.

    httpx's ASGITransport cannot deliver partial bodies of an infinite response, so the app is
    driven by hand — the same technique `test_governance.py::_sse_updates` and
    `test_shaping.py::test_sse_stream_graph_is_shaped_for_viewer` already use.
    """
    scope_env: dict[str, Any] = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "path": "/api/events",
        "raw_path": b"/api/events",
        "query_string": b"",
        "headers": [
            (b"cookie", f"{auth.COOKIE_NAME}={cookie}".encode()),
            (b"host", b"testserver"),
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "scheme": "http",
    }
    captured: list[bytes] = []
    buffer = b""
    sent_request = False
    never = asyncio.Event()

    async def receive() -> dict[str, Any]:
        nonlocal sent_request
        if not sent_request:
            sent_request = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await never.wait()  # a real client keeps the connection open
        return {"type": "http.disconnect"}  # pragma: no cover

    async def send(message: dict[str, Any]) -> None:
        nonlocal buffer
        if message["type"] != "http.response.body":
            return
        buffer += message.get("body", b"")
        while b"\n\n" in buffer:
            block, buffer = buffer.split(b"\n\n", 1)
            for line in block.decode().splitlines():
                if line.startswith("data: "):
                    captured.append(line[6:].encode())
                    raise _StopSSEError

    with contextlib.suppress(_StopSSEError, TimeoutError):
        await asyncio.wait_for(app(scope_env, receive, send), timeout=15.0)  # type: ignore[operator]
    assert captured, "no SSE update frame arrived"
    return captured[0]


async def _out_of_scope_ids(store: Store, engine: Any, queue: Any) -> dict[str, int]:
    """An estate with a situation whose only member sits on the out-of-scope 192.168.50.0/24."""
    import util

    await util.drive(
        engine,
        queue,
        [
            util.event(device="192.168.50.1", trap_oid=util.CIENA_TRAP, ts=BASE + 1),
            util.event(device="192.168.50.1", trap_oid=util.HUAWEI_TRAP, ts=BASE + 2),
            util.event(device="10.0.0.1", trap_oid=util.CIENA_TRAP, ts=BASE + 3),
        ],
    )
    async with store.lock:
        nes = {str(row["ip"]): int(row["id"]) for row in await store.list_ne()}
        situations = await store.list_situations(None, 100, None)
        members = await store.situation_member_nes([int(s["id"]) for s in situations])
        devices = {str(row["ip"]): int(row["id"]) for row in await store.list_ne_for_scope()}
    hidden_ne = nes["192.168.50.1"]
    hidden_situation = next(
        int(s["id"])
        for s in situations
        if members.get(int(s["id"])) and set(members[int(s["id"])]) == {hidden_ne}
    )
    async with store.lock:
        hidden_members = await store.situation_member_ids(hidden_situation)
    return {
        "ne": hidden_ne,
        "situation": hidden_situation,
        "device": devices.get("192.168.50.1", hidden_ne),
        # v0.16.0: `POST /api/alarms/{aid}/clear` names an alarm rather than a situation, so the
        # out-of-scope estate has to hand back one of those too.
        "alarm": hidden_members[0],
    }
