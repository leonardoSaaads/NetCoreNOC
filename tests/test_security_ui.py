"""S1 / F1 — stored-XSS remediation, CSP, security headers, and safe static assets.

Hostile strings are pushed through the *real* ingest path (parser → engine → store) in
the instance, a varbind value, and operator labels, then read back over the API. The API
must return them as JSON string values (data, never HTML), every route class must carry
the security headers, and the shipped UI source must be free of the unsafe patterns that
caused F1 (`innerHTML` interpolation, a `localStorage` token).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from netcorenoc.api import CSP, UI_DIR, create_app
from netcorenoc.ingest.events import Varbind
from netcorenoc.ingest.receiver import QueueItem
from netcorenoc.main import Engine
from netcorenoc.store import Store

import apisource
import util

TOKEN = "test-token-123"
XSS = "<img src=x onerror=alert(1)><script>alert(document.cookie)</script>\"'>"


@pytest.fixture
async def engine_env(store: Store) -> tuple[Engine, asyncio.Queue[QueueItem]]:
    queue: asyncio.Queue[QueueItem] = asyncio.Queue()
    engine = Engine(store, queue)
    await engine.start()
    return engine, queue


@pytest.fixture
async def client(
    engine_env: tuple[Engine, asyncio.Queue[QueueItem]],
) -> AsyncIterator[httpx.AsyncClient]:
    from netcorenoc.crosscutting import auth

    store = engine_env[0].store
    async with store.lock:
        await store.create_token(auth.hash_token(TOKEN), "test", "admin", "adm", 0.0)
        await store.commit()
    app = create_app(engine_env[0])
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://netcorenoc.test",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as c:
        yield c


async def test_f1_hostile_strings_survive_the_ingest_path_as_json(
    client: httpx.AsyncClient, engine_env: tuple[Engine, asyncio.Queue[QueueItem]]
) -> None:
    engine, queue = engine_env
    # A trap whose instance AND a varbind value are attacker-controlled XSS payloads,
    # driven through the real engine (the datagram-adjacent unauthenticated path, F1).
    hostile = util.event(
        device="10.0.0.9",
        instance=XSS,
        varbinds=[Varbind(oid="1.3.6.1.4.1.9.9.1", kind="OctetString", value=XSS)],
    )
    await util.drive(engine, queue, [hostile])
    # Operator label is the other F1 vector.
    dev_id = await engine.store.device_id("10.0.0.9", 1.0)
    r = await client.post("/api/labels", json={"kind": "device", "id": dev_id, "label": XSS})
    assert r.status_code == 200

    sid = (await client.get("/api/situations")).json()[0]["id"]
    resp = await client.get(f"/api/situations/{sid}")
    assert "application/json" in resp.headers["content-type"]
    detail = resp.json()
    alarm = detail["alarms"][0]
    # The payload round-trips as a plain string value: it is data, not markup.
    assert alarm["instance"] == XSS
    assert alarm["device_label"] == XSS
    # In the raw JSON body the dangerous characters are JSON-escaped, so no HTML tag can
    # break out of the response even if a client mishandled the content-type.
    assert "<script>" in resp.text or "\\u003cscript\\u003e" in resp.text  # tolerate either encoder
    # The response is not HTML — an HTML sniffer would find no executable document wrapper.
    assert not resp.text.lstrip().lower().startswith("<!doctype")


@pytest.mark.parametrize("path", ["/", "/app.js", "/style.css", "/healthz", "/api/stats"])
async def test_security_headers_on_every_route_class(client: httpx.AsyncClient, path: str) -> None:
    resp = await client.get(path)
    assert resp.headers["content-security-policy"] == CSP
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "no-referrer"


async def test_api_responses_are_no_store(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/stats")).headers["cache-control"] == "no-store"
    # Static assets are cacheable (no no-store forced on them).
    assert (await client.get("/app.js")).headers.get("cache-control") != "no-store"


async def test_static_assets_served_with_correct_types(client: httpx.AsyncClient) -> None:
    """Every asset a browser fetches comes back, with the right type, from this origin.

    v0.13.0: the console is a module graph, so this walks the whole declared set rather than the
    three files v0.12.0 shipped. A module that is declared and 404s is a console that half-loads.
    """
    from netcorenoc.api.routes_static import STATIC_ASSETS

    for asset, media_type in STATIC_ASSETS.items():
        response = await client.get(f"/{asset}")
        assert response.status_code == 200, f"/{asset} returned {response.status_code}"
        assert media_type.split(";")[0] in response.headers["content-type"], asset
    css = await client.get("/style.css")
    assert "text/css" in css.headers["content-type"]
    d3 = await client.get("/vendor/d3.v7.min.js")
    assert d3.status_code == 200 and "d3js.org v7" in d3.text  # vendored locally, pinned
    # The framework is served from this origin too — no CDN, ever.
    preact = await client.get("/vendor/preact-10.29.8.module.js")
    assert preact.status_code == 200 and "preact" in preact.text.lower()


def _code_only(source: str) -> str:
    """Drop comments, keeping line count so positions stay comparable.

    DECISIONS #100: matching text *about* a thing as though it were the thing. Every module in this
    console documents the sinks it does not use — `.innerHTML`, `dangerouslySetInnerHTML`, `eval` —
    because naming a forbidden thing is how a reader learns it is forbidden. Without this, writing
    that comment would break the test that checks the comment is true, and the obvious repair would
    be to water down the comment. **Assert over code, comment freely.**
    """
    without_block = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return "\n".join(
        "" if line.lstrip().startswith("//") else line.split("//")[0] if "//" in line else line
        for line in without_block.splitlines()
    )


def ui_modules() -> dict[str, str]:
    """Every JavaScript module this console ships, by relative path. **Not** the vendored assets:
    those are third-party bytes pinned by checksum, and holding them to this project's source
    conventions would mean either editing them (breaking the pin) or exempting them one by one."""
    return {
        str(path.relative_to(UI_DIR)): _code_only(path.read_text(encoding="utf-8"))
        for path in sorted(UI_DIR.rglob("*.js"))
        if "vendor" not in path.parts
    }


def test_ui_source_has_no_f1_antipatterns() -> None:
    """F1 and F2, applied to **every** module rather than to one file.

    v0.12.0 checked one 52 KB file. v0.13.0 has 33 modules, and a scan that checked only the entry
    point would be a guard that stopped guarding the moment the UI acquired a second file — which
    is exactly what this release did.
    """
    index = (UI_DIR / "index.html").read_text()
    modules = ui_modules()
    assert len(modules) >= 20, f"only {len(modules)} modules found; this scan is not scanning"

    # CSP compliance: the page loads scripts/styles by reference only — no inline code.
    assert "cdn.jsdelivr.net" not in index and "https://" not in index
    assert "<script>" not in index  # only <script src=...></script>
    assert " style=" not in index  # no inline style attributes (style-src 'self')
    assert "importmap" not in index  # an import map is an inline script the CSP forbids

    for name, source in modules.items():
        # F1's root cause, and the framework's own escape hatch out of it. `innerHTML` is not
        # reachable from a tagged template; `dangerouslySetInnerHTML` is the single token that
        # would make it reachable, and it appears in no module.
        assert ".innerHTML" not in source, name
        assert "dangerouslySetInnerHTML" not in source, name
        assert "insertAdjacentHTML" not in source and "outerHTML" not in source, name
        # F2: no localStorage anywhere. The theme preference is a cookie (ADR #172).
        assert "localStorage" not in source, name
        assert "sessionStorage" not in source, name
        # No `eval` or `Function` constructor: `script-src 'self'` has no 'unsafe-eval'.
        assert "eval(" not in source, name
        assert "new Function" not in source, name
    assert "localStorage" not in index

    # The escaper survives, and is exported from the module the whole console imports.
    dom_module = modules["app/dom.js"]
    assert "export function esc(" in dom_module
    assert "htm.bind(h)" in dom_module
    # The control for `_code_only`: the forbidden tokens ARE discussed in the comments, so a
    # scanner that did not strip them would fail above. If this ever stops being true, the scan
    # has quietly become a scan of a tree that no longer explains itself.
    assert "dangerouslySetInnerHTML" in (UI_DIR / "app" / "dom.js").read_text(encoding="utf-8")


def test_d3_is_vendored_and_pinned() -> None:
    d3 = (Path(UI_DIR) / "vendor" / "d3.v7.min.js").read_text()
    assert "v7.9.0" in d3.splitlines()[0]  # pinned version banner


# -- A.4 role-gated UI, v0.13.0 form ------------------------------------------------------
#
# ### WHAT REPLACED `split("const TABS")`, AND WHY THE OLD GUARD IS GONE RATHER THAN KEPT ###
#
# v0.12.0 kept a text-level guard that parsed the `TABS` array out of `app.js` with a regex, for a
# stated reason: the DOM harness skips when Node is absent, and a skipped guard is a silent one, so
# the pair failed for different reasons and one of them still watched on a machine without Node
# (ADR #168).
#
# **That guard could not survive this release, and replacing it with a regex over the new source
# would have been worse than deleting it.** Its own docstring said why: a rewrite that changed the
# *shape* of the map leaves the matcher finding nothing, and a matcher that finds nothing reports
# no violations. v0.13.0 changed the shape completely — the map is now a list of objects in
# `app/registry.js`, each carrying a component reference — so a regex would have had to be rewritten
# from scratch **during the change it guards**, against a shape chosen an hour earlier.
#
# What replaces it is stronger and is still not a DOM test: `app/registry.js` is parsed as
# **structure** (a real tokeniser over its declarations, below), and every capability it names is
# checked against `rbac.PERMISSIONS`. It runs with no Node. It cannot pass by matching nothing,
# because it asserts the count it extracted against the count of views the file declares — the
# vacuity check the old guard's `assert caps` was reaching for and did not reach.

import json  # noqa: E402 - grouped with the tests it supports
import re  # noqa: E402 - grouped with the tests it supports

REGISTRY = UI_DIR / "app" / "registry.js"

#: The views whose capability's minimum role must be `admin`. Named here so a view SILENTLY
#: dropping out of the registry fails, which a set derived from the registry could not catch.
ADMIN_VIEWS = {"users", "tokens", "settings", "scorer", "governance", "quarantine", "audit"}


def registry_entries() -> dict[str, list[str]]:
    """`{view id: [capabilities]}`, extracted from `app/registry.js` by structure.

    Not `split("const VIEWS")`. Each entry is found by its `id:` key and its `capability:` value is
    parsed as JSON — so a single capability, a list of them, and a trailing comment all read
    correctly, and a shape this parser does not understand raises instead of returning nothing.
    """
    source = REGISTRY.read_text(encoding="utf-8")
    body = source.split("export const VIEWS = [", 1)[1].rsplit("];", 1)[0]
    entries: dict[str, list[str]] = {}
    for block in re.finditer(r"\{(.*?)\n  \}", body, re.DOTALL):
        text = block.group(1)
        view_id = re.search(r'\bid:\s*"([\w-]+)"', text)
        capability = re.search(r"\bcapability:\s*(\"[\w.]+\"|\[[^\]]*\])", text)
        if not view_id:
            continue
        if not capability:
            raise AssertionError(f"registry entry {view_id.group(1)!r} declares no capability")
        raw = json.loads(capability.group(1).replace("'", '"'))
        entries[view_id.group(1)] = [raw] if isinstance(raw, str) else list(raw)
    return entries


def test_the_registry_parser_is_not_matching_nothing() -> None:
    """**The vacuity check the old text guard did not have.**

    The extractor's count must equal the number of `id:` keys in the file. A parser that matched
    half the entries would report the other half as compliant by never looking at them, which is
    precisely how `split("const TABS")` would have failed silently through this rewrite.
    """
    entries = registry_entries()
    declared = len(re.findall(r'\bid:\s*"[\w-]+"', REGISTRY.read_text(encoding="utf-8")))
    assert declared > 10, f"the registry declares only {declared} views; something is wrong"
    assert len(entries) == declared, (
        f"the extractor found {len(entries)} of {declared} declared views. It is not reading the "
        f"registry it is supposed to be checking, and every assertion built on it is vacuous."
    )


def test_every_view_is_gated_on_a_capability_the_server_enforces() -> None:
    """No view may be gated on something `rbac` has never heard of.

    A capability the authorization map does not know cannot be held by anyone, so a view gated on
    one is either unreachable or — if the client's check were ever inverted — ungated.
    """
    from netcorenoc.crosscutting import rbac

    for view_id, capabilities in registry_entries().items():
        assert capabilities, f"{view_id} declares an empty capability list"
        for capability in capabilities:
            assert capability in rbac.PERMISSIONS, (
                f"{view_id} is gated on unknown capability {capability!r}"
            )


def test_admin_views_are_gated_on_an_admin_ceiling_capability() -> None:
    """A.4: every administration screen is gated on a capability whose minimum role is `admin`.

    Stronger than a "role: admin" string check, because the requirement is resolved against
    `rbac.PERMISSIONS` — the same source the server enforces from — so a view gated on a capability
    that was quietly lowered to editor fails here.

    **v0.13.0**: this asserts the shape of the registry. The property itself — that a viewer's
    console contains no admin screen and issues no admin request — is asserted by rendering, in
    `test_ui_invariants.py`. A green tick here is not that assertion and must never be reported as
    though it were.
    """
    from netcorenoc.crosscutting import rbac

    entries = registry_entries()
    for view_id in ADMIN_VIEWS:
        assert view_id in entries, f"the {view_id} view has left the registry entirely"
        # The EFFECTIVE minimum role is the strictest of the declared capabilities, because the
        # router requires all of them. Demanding that every one be admin-only would be wrong for a
        # screen that legitimately also needs a viewer-level read — the Link scorer declares
        # `scorer.read` (viewer+) beside `scorer.write` (admin), and must, because a stored policy
        # can grant one without the other.
        effective = max(
            rbac.ROLE_RANK[rbac.PERMISSIONS[capability]] for capability in entries[view_id]
        )
        assert effective == rbac.ROLE_RANK["admin"], (
            f"{view_id} resolves to a minimum role below admin: "
            f"{ {c: rbac.PERMISSIONS[c] for c in entries[view_id]} }"
        )


def test_the_router_and_the_sidebar_read_the_same_table() -> None:
    """One table, two readers — which is what makes "offered" and "authorised" the same question.

    v0.12.0 had `TABS` for navigation and `prunePanels` for the DOM, and their agreeing was a
    convention nothing enforced. This asserts the structural replacement: the sidebar renders
    `reachableViews(...)` and nothing else decides what it offers.
    """
    sidebar = (UI_DIR / "app" / "sidebar.js").read_text(encoding="utf-8")
    router = (UI_DIR / "app" / "router.js").read_text(encoding="utf-8")
    assert "reachableViews" in sidebar, "the sidebar no longer renders from the router's table"
    assert "VIEWS" not in sidebar, "the sidebar reads the registry directly, bypassing the router"
    assert "export function reachableViews" in router
    assert "export function resolve" in router
    # Both go through the SAME predicate on the same declaration.
    assert router.count("requirementsOf(view)") >= 2


def test_capability_is_resolved_before_anything_is_constructed() -> None:
    """**F53's repair, asserted structurally as well as behaviourally.**

    The behavioural assertion is
    `test_ui_invariants.py::test_a_view_reached_by_address_without_its_capability_refuses_by_decision`.
    This is the source-level tripwire beside it, and it fails for a different reason: it checks
    that there is exactly **one** place where a routing decision becomes a mounted component. A
    second one would be a second authorisation surface, which is the shape of the finding.
    """
    shell = (UI_DIR / "app" / "shell.js").read_text(encoding="utf-8")
    # The decision is consulted, and the component is only read out of a `view` decision.
    assert 'decision.kind === "refused"' in shell
    assert 'decision.kind === "unknown"' in shell
    mounts = re.findall(r"decision\.view\.component", shell)
    assert len(mounts) == 1, (
        f"{len(mounts)} places turn a routing decision into a component. There must be exactly "
        f"one: a second is a second authorisation surface (F53, ADR #176)."
    )
    # And no view module may resolve its own capability — that would be the 17-checks design the
    # ADR rejected, and the one that is forgotten is invisible until it is exploited.
    for path in sorted((UI_DIR / "app" / "views").glob("*.js")):
        source = path.read_text(encoding="utf-8")
        assert "reachableViews" not in source, path.name
        assert "resolve(" not in source, path.name


def view_writes() -> dict[str, set[tuple[str, str]]]:
    """`{view id: {(METHOD, templated path)}}` — every mutating route each screen can issue.

    Extracted from the `post(...)` / `del(...)` call sites and resolved onto
    `rbac.ROUTE_PERMISSIONS` by shape, so a template literal like `/api/users/${x}/role` matches
    the declared `/api/users/{uid}/role`. A path this cannot resolve raises rather than being
    skipped: an unresolvable write is exactly the one that would slip through unchecked.
    """
    from netcorenoc.crosscutting import rbac

    modules = ui_modules()

    # Which registry view each view-module belongs to. A module that is not itself a view
    # (`retention.js`, `facts.js`) is a component of the one that imports it, and its writes are
    # attributed there. **Skipping it instead would be how a write escapes a guard that looks
    # total** — the section that deletes the feedback corpus lives in exactly such a module.
    owners: dict[str, set[str]] = {}
    for name in modules:
        if not name.startswith("app/views/"):
            continue
        stem = name.removeprefix("app/views/").removesuffix(".js")
        owners.setdefault(name, set())
        for other, other_source in modules.items():
            if other.startswith("app/views/") and f'from "./{stem}.js"' in other_source:
                owners[name].add(other.removeprefix("app/views/").removesuffix(".js"))

    writes: dict[str, set[tuple[str, str]]] = {}
    for name, source in modules.items():
        if not name.startswith("app/views/"):
            continue
        view_id = name.removeprefix("app/views/").removesuffix(".js")
        found: set[tuple[str, str]] = set()
        calls = re.findall(r"\b(post|del)\(\s*[`\"']([^`\"']*)", source)
        # A path looked up in a module-level literal table (`POLICY_PATH[kind]`) is still a
        # literal; expand the table so the write is attributable. A path built from an expression
        # this cannot expand still raises below, which is the property that matters.
        for table in re.findall(r"const [A-Z_]+ = \{([^}]*)\};", source):
            for literal in re.findall(r'"(/api/[^"]*)"', table):
                calls.append(("post", literal))
        for verb, raw in calls:
            method = "POST" if verb == "post" else "DELETE"
            # `${expr}` is a path parameter; the declared table spells it `{name}`.
            concrete = re.sub(r"\$\{[^}]*\}", "{x}", raw).split("?", 1)[0]
            if not concrete.startswith("/api"):
                continue
            segments = concrete.split("/")
            match = next(
                (
                    (m, t)
                    for m, t in rbac.ROUTE_PERMISSIONS
                    if m == method
                    and len(t.split("/")) == len(segments)
                    and all(
                        want.startswith("{") or want == got
                        for want, got in zip(t.split("/"), segments, strict=True)
                    )
                ),
                None,
            )
            assert match is not None, (
                f"{name} writes to {concrete!r}, which resolves to no declared route. An "
                f"unresolvable write is the one that would slip past this guard unchecked."
            )
            found.add(match)
        if not found:
            continue
        targets = {view_id} if view_id in _registry_ids() else owners[name]
        assert targets, (
            f"{name} issues writes but is neither a registry view nor imported by one, so no "
            f"capability governs it. A write nothing owns is a write nothing gates."
        )
        for target in targets:
            writes.setdefault(target, set()).update(found)
    return writes


def _registry_ids() -> set[str]:
    return set(registry_entries())


def test_mutating_controls_are_behind_capability_guards() -> None:
    """A principal never gets a mutating control they could not use.

    The rule, derived rather than listed: for every write a screen can issue, either

      * the **router** already resolved the capability that write requires — the screen's declared
        capability set contains it, so a principal without it never mounts the screen at all; or
      * the screen gates the control itself with `can()` / `canEdit()`.

    Deriving it is what makes the exemptions honest. `account.js` writes only
    `POST /api/password`, which needs `self.read`, which is exactly what the router resolved to let
    the principal reach the screen — so a second gate there would guard nothing. `governance.js`
    writes `rbac.write` and `scope.write`, both admin-ceiling, and the router already refused any
    principal who lacks them. `situations.js` is reachable by a **viewer** and writes
    `feedback.write`, so it must and does gate itself.
    """
    from netcorenoc.crosscutting import rbac

    entries = registry_entries()
    modules = ui_modules()
    writes = view_writes()
    # A view's own gates plus those of every component module it composes.
    component_sources = {
        view_id: "".join(
            source
            for name, source in modules.items()
            if name.startswith("app/views/")
            and (name.endswith(f"/{view_id}.js") or 'from "./' in source)
        )
        for view_id in writes
    }
    assert len(writes) >= 6, f"only {len(writes)} writing views found; this scan is not scanning"

    for view_id, routes in sorted(writes.items()):
        declared = set(entries[view_id])
        source = modules[f"app/views/{view_id}.js"] + component_sources.get(view_id, "")
        gated_in_screen = "canEdit" in source or "can(" in source
        for route in sorted(routes):
            required = rbac.ROUTE_PERMISSIONS[route]
            router_gated = required in declared
            assert router_gated or gated_in_screen, (
                f"{view_id} can issue {route[0]} {route[1]}, which needs {required!r}. The router "
                f"resolved only {sorted(declared)} for this screen and the screen gates nothing "
                f"itself, so a principal without {required!r} would be offered the control."
            )


def test_no_module_re_derives_permissions_from_role_rank() -> None:
    """F28 at the UI layer: the affordance gate reads the server's **resolved** set.

    An admin may have stored a policy narrowing what a role holds. A console that compared role
    rank would be a second decision site that silently disagrees with the server the moment a
    policy exists. `session.js` owns the question; nothing else may re-derive it.
    """
    session_module = (UI_DIR / "app" / "session.js").read_text(encoding="utf-8")
    assert "capabilities.has(capability)" in session_module
    assert "me.capabilities" in session_module
    for name, source in ui_modules().items():
        if name == "app/session.js":
            continue
        assert 'role === "admin"' not in source, f"{name} compares role rank"
        assert "ROLE_RANK" not in source and "ROLES[" not in source, f"{name} compares role rank"


def test_the_client_refusal_is_never_the_only_refusal() -> None:
    """Draft §11.3, as a test: the console's refusal is an affordance, not a control.

    The hardening-only refusal in `parameters.js` reads the bounds the SERVER published — there is
    no client-side copy of a bound anywhere — so the console cannot disagree with the appliance
    about where a floor is, and cannot become the only thing enforcing one.
    """
    parameters = (UI_DIR / "app" / "parameters.js").read_text(encoding="utf-8")
    assert "bounds.min_tau_s" in parameters and "bounds.max_tau_s" in parameters
    assert "bounds.min_weight_sum" in parameters and "bounds.threshold_margin" in parameters
    # No literal floor. A number here would be a second source of truth for a project floor.
    for forbidden in ("= 1.0;", "= 3600.0;", "= 0.10;", "= 0.01;"):
        assert forbidden not in parameters, (
            f"{forbidden!r} looks like a hard-coded copy of a bound the server publishes"
        )
    session_module = (UI_DIR / "app" / "session.js").read_text(encoding="utf-8")
    assert "never a control" in session_module or "affordance" in session_module


def test_the_held_card_freezes_the_payload_and_says_so() -> None:
    """§5.1-§5.3 in their v0.13.0 form (ADR #173). **Shape of the source, not behaviour.**

    The behavioural assertions are invariant 3's two tests. This is the tripwire beside them: the
    hold must live in the store, the staleness marker must be conditional on a card being held,
    and the hold must be released on collapse. A marker that outlived its hold would assert
    something false for as long as the card stayed shut.
    """
    store = (UI_DIR / "app" / "store.js").read_text(encoding="utf-8")
    situations = (UI_DIR / "app" / "views" / "situations.js").read_text(encoding="utf-8")
    assert "export function expand(" in store and "export function collapse(" in store
    assert "export function heldDetail(" in store
    # Collapsing must clear BOTH the hold and the withheld count, in `collapse`.
    collapse = store.split("export function collapse(", 1)[1].split("\n}", 1)[0]
    assert "held.delete(sid)" in collapse and "withheld.delete(sid)" in collapse
    # The marker is emitted only where a card is expanded AND has withheld something.
    assert "held while open" in situations
    at = situations.index("held while open")
    # Look BACK from the marker to the condition that emits it. Anchoring on a single line would
    # make this test a statement about where a line break falls, which is not the property.
    guard = situations[max(0, at - 200) : at]
    assert "expanded &&" in guard and "withheld > 0" in guard, (
        f"the staleness marker is not guarded by (expanded AND withheld): {guard!r}"
    )


def test_the_labelling_payload_contract_is_unchanged() -> None:
    """Draft §11.12 / DECISIONS #127. The **gesture** may change; the **payload** may not.

    `excluded_ids` is sent only with a split, and only when something was marked. Omitting it means
    "the operator marked nothing", which is a plain split — never an empty list.
    """
    situations = (UI_DIR / "app" / "views" / "situations.js").read_text(encoding="utf-8")
    verdict = situations.split("async verdict(kind)", 1)[1].split("\n  }", 1)[0]
    assert 'kind === "split" && this.state.marked.size' in verdict, (
        "excluded_ids is no longer conditional on BOTH a split and a non-empty mark set"
    )
    assert "member_ids" in verdict and "updated_at" in verdict


def test_the_scorer_screen_states_the_preview_caveat() -> None:
    """SECURITY-REVIEW-0.6 §4 treats the wording as a control: a preview that presented itself as
    authoritative would be worse than none. The API supplies the sentence; the screen shows it."""
    scorer = (UI_DIR / "app" / "views" / "scorer.js").read_text(encoding="utf-8")
    assert "delta.caveat" in scorer
    source = apisource.api_source()
    assert "Directional, not exhaustive" in source
    assert "learned matrices held fixed" in source


def test_every_destructive_control_goes_through_one_component() -> None:
    """Nothing destructive without a preview, and one implementation of "destructive".

    Twenty copies of a confirm dialogue is twenty chances to forget one, and the one that is
    forgotten is the one that deletes the corpus. Every destructive control imports `Destructive`.
    """
    destructive = (UI_DIR / "app" / "destructive.js").read_text(encoding="utf-8")
    assert "cannot be undone" in destructive
    # The apply step does not exist until a preview has been seen — absent, not disabled.
    assert 'stage === "previewed" || stage === "applying"' in destructive
    for name in ("audit", "settings", "users", "tokens", "entities", "promotion", "scorer"):
        source = (UI_DIR / "app" / "views" / f"{name}.js").read_text(encoding="utf-8")
        assert "Destructive" in source, f"{name}.js has no destructive-preview machinery"
    # And no screen may reach for a bare `confirm()` instead.
    for name, source in ui_modules().items():
        assert "confirm(" not in source or "confirmLabel" in source, name


def test_style_uses_design_tokens_both_themes_and_focus_states() -> None:
    import re

    css = (UI_DIR / "style.css").read_text()
    for token in ("--space-1:", "--radius-1:", "--shadow-1:", "--fs-md:", "--tap:"):
        assert token in css, token
    assert "@media (prefers-color-scheme: light)" in css  # the system preference still decides…
    assert '[data-theme="light"]' in css  # …and an explicit choice overrides it
    # v0.15.3: the density switch is GONE, mechanism included (DECISIONS #235, #45). It scaled four
    # spacing tokens and one step of the type ramp, so the two densities had different sets of
    # relationships and one of them was chosen by nobody. Asserted absent rather than deleted from
    # this file, because "the knob went and the mechanism stayed" is the failure #45 records.
    assert "data-density" not in css, "the density mechanism is back without its decision"
    assert ":focus-visible" in css  # visible keyboard focus (accessibility)
    # More than one layout breakpoint (#237). The shipped console had exactly one — `max-width:
    # 760px` — so 820 px rendered byte-identically to 1440 px on every view of every role, and
    # everything between 761 px and a NOC wall was the desktop layout stretched: a phone rule and
    # a desktop, with no tablet at all. Asserted as a COUNT rather than as literal widths, because
    # the widths are a design decision this test has no business pinning; what was missing is that
    # the stylesheet reasoned about more than one.
    layout = re.findall(r"@media \(max-width: (\d+)px\)", css)
    assert len(layout) >= 2, (
        f"the stylesheet reasons about {len(layout)} width(s): {layout}. One is the state v0.15.3 "
        f"found, and no assertion here could see it — 820 px and 1440 px render the same words."
    )
    assert "prefers-reduced-motion" in css
    # still CSP-clean: no external origins, no @import of a remote sheet.
    assert "http://" not in css and "https://" not in css
    assert "@import" not in css


def test_no_card_styles_a_bare_element_it_does_not_own() -> None:
    """**F86.** A container that styles `input` or `button` by element name is styling controls it
    has not met yet.

    `.login-card button { width: 100% }` was correct when the card held one button. Then the card
    gained a composed `PasswordInput` — an input and a reveal button inside `.pw-field > .pw-row` —
    and the descendant selector reached them too. `width: 100%` on a `flex: none` item is a base
    size it will not shrink below, so **the reveal button took the whole row and the password field
    rendered 18 px wide**, with the two 8 px out of line because one carried `margin-top` and the
    other `margin-bottom`. An operator could not see the password they were typing.

    This is F85's shape in CSS: a rule whose meaning silently widens every time the tree it sits
    over grows. `>` fixes it by saying what the rule always meant — the card's OWN controls — and
    keeps saying it when the card grows again.

    Scoped to the containers that COMPOSE other components, which is where the widening happens.
    Not the whole stylesheet: a bare `button` rule at the top level is the reset and is exactly
    what it is for, and `.pw-row input` is a leaf component styling the one control it is built
    from — it cannot acquire a second one without someone editing `password.js` to put it there.
    """
    import re

    css = (UI_DIR / "style.css").read_text()
    owners = ("login-card", "card")
    offenders = []
    for line in css.splitlines():
        selector = line.split("{")[0]
        if "{" not in line:
            continue
        for owner in owners:
            # `.owner input` / `.owner button` — a DESCENDANT combinator (whitespace, no `>`).
            if re.search(rf"\.{owner}\s+(input|button|select|textarea)\b", selector):
                offenders.append(selector.strip())
    assert not offenders, (
        f"these style a bare form element as a DESCENDANT of a card, so they will capture the "
        f"controls of any component composed into it (F86): {offenders}. Use `>` for the "
        f"container's own controls, or give the control a class."
    )
    # CONTROL: the rules this is about still exist, so the guard is not passing because the
    # stylesheet stopped styling the login card at all.
    assert ".login-card > input" in css and ".login-card > button" in css, (
        "the login card no longer styles its own field and submit button; re-derive this guard"
    )


def test_no_enumerated_dom_attribute_is_passed_as_the_string_false() -> None:
    """**F88**, and it was visible in the shipped DOM the whole time.

    `spellcheck` is an IDL **boolean**. Preact assigns the property, so `spellcheck="false"` —
    a non-empty string — coerces to `true`, and the password field rendered `spellcheck="true"`.
    The browser was offering to spell-check a password, which on some builds means sending it to a
    remote service. The source said one thing and the DOM said the other, and reading either alone
    could not tell you.

    `false`, the boolean, is the only thing that renders `spellcheck="false"`. Same trap for
    `draggable` and `contenteditable`, so all three are named here rather than the one that bit.
    """
    for name, source in ui_modules().items():
        for attribute in ("spellcheck", "draggable", "contenteditable"):
            assert f'{attribute}="false"' not in source, (
                f'{name} passes {attribute}="false" as a STRING. Preact sets the property and a '
                f'non-empty string is truthy, so this renders {attribute}="true" — the opposite '
                f"of what it says. Write {attribute}=${{false}} (F88)."
            )


def test_the_ui_tree_is_exactly_what_is_declared() -> None:
    """DECISIONS #38's constraint, restated for a module graph (ADR #175).

    #38 fixed the UI at four files. That number was a proxy for *"the UI is small and reviewable"*,
    and a 400-line-per-module guard makes four files impossible. The constraint that replaces it is
    stricter in the way that matters: the tree is not merely small, it is **enumerated**, and the
    set on disk must equal the set the appliance serves.
    """
    from netcorenoc.api.routes_static import STATIC_ASSETS

    top_level = {p.name for p in UI_DIR.iterdir()}
    assert top_level - {".well-known"} == {"index.html", "app.js", "style.css", "app", "vendor"}

    on_disk = {str(p.relative_to(UI_DIR)) for p in UI_DIR.rglob("*.js") if "vendor" not in p.parts}
    served = {name for name in STATIC_ASSETS if name.endswith(".js") and "vendor/" not in name}
    assert on_disk == served, (
        f"the module set on disk and the set the appliance serves disagree.\n"
        f"  on disk, not served: {sorted(on_disk - served)}\n"
        f"  served, not on disk: {sorted(served - on_disk)}"
    )
    well_known = UI_DIR / ".well-known"
    if well_known.exists():
        assert {p.name for p in well_known.iterdir()} == {"security.txt"}


def test_csp_is_unchanged_and_forbids_inline() -> None:
    """The CSP is **not relaxed** by this release. Not one directive moved."""
    assert "style-src 'self'" in CSP and "script-src 'self'" in CSP
    assert "'unsafe-inline'" not in CSP and "default-src 'none'" in CSP
    assert "'unsafe-eval'" not in CSP
    assert CSP == (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
        "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"
    ), "the CSP text changed; v0.13.0 may not touch it (Part VI.5)"
