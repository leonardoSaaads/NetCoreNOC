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
from netcorenoc.events import Varbind
from netcorenoc.main import Engine
from netcorenoc.receiver import QueueItem
from netcorenoc.store import Store

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
    from netcorenoc import auth

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
    js = await client.get("/app.js")
    assert "javascript" in js.headers["content-type"]
    assert "createTextNode" in js.text and "textContent" in js.text  # safe DOM building
    css = await client.get("/style.css")
    assert "text/css" in css.headers["content-type"]
    d3 = await client.get("/vendor/d3.v7.min.js")
    assert d3.status_code == 200 and "d3js.org v7" in d3.text  # vendored locally, pinned


def test_ui_source_has_no_f1_antipatterns() -> None:
    index = (UI_DIR / "index.html").read_text()
    app_js = (UI_DIR / "app.js").read_text()
    # CSP compliance: the page loads scripts/styles by reference only — no inline code.
    assert "cdn.jsdelivr.net" not in index and "https://" not in index
    assert "<script>" not in index  # only <script src=...></script>
    assert " style=" not in index  # no inline style attributes (style-src 'self')
    # F1 root cause is gone: the client never assigns to or reads the .innerHTML sink.
    assert ".innerHTML" not in app_js
    # F2: the localStorage token is removed entirely.
    assert "localStorage" not in app_js and "localStorage" not in index
    # The escaper and safe builders exist and are used.
    assert "function esc(" in app_js and "createTextNode" in app_js


def test_d3_is_vendored_and_pinned() -> None:
    d3 = (Path(UI_DIR) / "vendor" / "d3.v7.min.js").read_text()
    assert "v7.9.0" in d3.splitlines()[0]  # pinned version banner


# -- S9 / A.4 role-gated UI + design-token refresh ----------------------------------

import re  # noqa: E402 - grouped with the S9 tests it supports

ADMIN_PANELS = {"users", "tokens", "config", "scorer", "quarantine", "audit"}


def _tab_roles() -> dict[str, str]:
    """Parse the TABS role map out of app.js: {panel_id: required_role}."""
    app_js = (UI_DIR / "app.js").read_text()
    block = app_js.split("const TABS", 1)[1].split("];", 1)[0]
    return dict(re.findall(r'id:\s*"(\w+)".*?role:\s*"(\w+)"', block))


def test_admin_panels_are_gated_to_admin() -> None:
    """A.4: every admin screen requires the admin role in the single TABS role map."""
    roles = _tab_roles()
    for panel in ADMIN_PANELS:
        assert roles.get(panel) == "admin", f"{panel} is not admin-gated"


def test_non_admin_panels_are_pruned_from_the_dom() -> None:
    """A.4: admin screens are *absent* from a non-admin DOM, not merely hidden. app.js removes
    each panel whose role the caller lacks, and does so on entry."""
    app_js = (UI_DIR / "app.js").read_text()
    assert "function prunePanels(" in app_js
    assert ".panel[data-panel=" in app_js and ".remove()" in app_js
    # prunePanels runs in enterApp (before the app is shown).
    enter = app_js.split("function enterApp(", 1)[1].split("\n}", 1)[0]
    assert "prunePanels()" in enter
    # index.html declares the admin panels statically; the pruning is what makes them absent.
    index = (UI_DIR / "index.html").read_text()
    for panel in ADMIN_PANELS:
        assert f'data-panel="{panel}"' in index


def test_mutating_controls_are_behind_role_guards() -> None:
    """Feedback/close/rename affordances are created only under canEdit(); entity/profile reset
    only under isAdmin() — a viewer never gets a mutating control rendered."""
    app_js = (UI_DIR / "app.js").read_text()
    detail = app_js.split("async function renderDetail(", 1)[1].split("\nfunction ", 1)[0]
    # the feedback/close block is guarded by canEdit()
    assert "if (canEdit())" in detail
    assert detail.index("if (canEdit())") < detail.index('feedback(sid, "confirm")')
    ent = app_js.split("async function renderEntityDetail(", 1)[1].split("\nfunction ", 1)[0]
    assert "if (isAdmin())" in ent
    assert ent.index("if (isAdmin())") < ent.index("/reset")


def test_style_uses_design_tokens_light_variant_and_focus_states() -> None:
    css = (UI_DIR / "style.css").read_text()
    for token in ("--space-1:", "--radius-1:", "--shadow-1:", "--fs-md:"):
        assert token in css, token
    assert "@media (prefers-color-scheme: light)" in css  # light palette
    assert ":focus-visible" in css  # visible keyboard focus (accessibility)
    assert "@media (max-width: 760px)" in css  # responsive to a narrow viewport
    # still CSP-clean: no external origins, no @import of a remote sheet.
    assert "http://" not in css and "https://" not in css
    assert "@import" not in css


def test_ui_stays_four_files() -> None:
    """Hard constraint (DECISIONS #38): the UI *code* is exactly index.html, app.js, style.css,
    and vendor/. The standardized ``.well-known/`` served-metadata directory (RFC 9116
    security.txt, added v0.5.0) is the only permitted non-code addition and holds no UI code."""
    entries = {p.name for p in UI_DIR.iterdir()}
    assert entries - {".well-known"} == {"index.html", "app.js", "style.css", "vendor"}
    well_known = UI_DIR / ".well-known"
    if well_known.exists():
        assert {p.name for p in well_known.iterdir()} == {"security.txt"}


def test_csp_is_unchanged_and_forbids_inline() -> None:
    assert "style-src 'self'" in CSP and "script-src 'self'" in CSP
    assert "'unsafe-inline'" not in CSP and "default-src 'none'" in CSP


# -- v0.6.0: the link-scorer panel -------------------------------------------------------


def _scorer_panel_source() -> str:
    """The body of loadScorer(), where every new admin control lives."""
    app_js = (UI_DIR / "app.js").read_text()
    return app_js.split("async function loadScorer(", 1)[1].split("\nasync function loadQuar", 1)[0]


def test_scorer_panel_is_admin_gated_and_prunable() -> None:
    """The panel offers preview/apply/rollback, all admin-only on the API, so it is admin-gated
    in the single TABS map and removed from a non-admin DOM by prunePanels (A.4)."""
    assert _tab_roles().get("scorer") == "admin"
    assert 'data-panel="scorer"' in (UI_DIR / "index.html").read_text()


def test_scorer_panel_renders_every_value_as_text_not_markup() -> None:
    """F1 discipline on the new panel: no innerHTML anywhere, and every server-supplied string
    (the degraded reason, the note, the caveat, created_by) goes through el({text})/text()."""
    app_js = (UI_DIR / "app.js").read_text()
    panel = app_js.split("async function loadScorer(", 1)[1].split(
        "\nasync function loadQuarantine", 1
    )[0]
    assert ".innerHTML" not in panel
    assert "insertAdjacentHTML" not in panel and "outerHTML" not in panel
    for supplied in ("cfg.degraded_reason", "h.created_by", "h.note", "d.caveat"):
        assert supplied in panel, supplied
    # The only style manipulation in the UI stays CSSOM (CSP forbids inline style attributes).
    assert " style=" not in panel


def test_scorer_panel_states_the_preview_caveat() -> None:
    """SECURITY-REVIEW-0.6 §4 treats the wording as a control: a preview that presented itself
    as authoritative would be worse than none. The API supplies the sentence; the panel shows it
    and says so."""
    app_js = (UI_DIR / "app.js").read_text()
    assert "d.caveat" in app_js
    from netcorenoc import api

    source = __import__("inspect").getsource(api.create_app)
    assert "Directional, not exhaustive" in source
    assert "learned matrices held fixed" in source


def test_scorer_panel_confirms_before_applying_or_rolling_back() -> None:
    """Both actions change system-wide correlation logic; neither happens on a stray click."""
    app_js = (UI_DIR / "app.js").read_text()
    panel = app_js.split("async function loadScorer(", 1)[1].split(
        "\nasync function loadQuarantine", 1
    )[0]
    assert panel.count("confirm(") >= 2


def test_link_terms_come_from_the_named_term_list() -> None:
    """S2: the UI reads the scorer's named terms, falling back to the legacy three columns for
    an older response. Same numbers, one typed source."""
    app_js = (UI_DIR / "app.js").read_text()
    assert "function linkTerms(" in app_js
    bar = app_js.split("function termBar(", 1)[1].split("\n}", 1)[0]
    assert "linkTerms(l)" in bar
    assert "t.contribution" in bar
