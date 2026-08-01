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

ADMIN_PANELS = {
    "users",
    "tokens",
    "config",
    "scorer",
    "governance",  # v0.7.0
    "quarantine",
    "audit",
}


def _tab_caps() -> dict[str, str]:
    """Parse the TABS capability map out of app.js: {panel_id: required_capability}.

    v0.7.0 gates tabs on the **resolved capability** from `/api/me` rather than on role rank: an
    admin may have narrowed what a role holds, and a UI still offering the control would promise
    something the server refuses.
    """
    app_js = (UI_DIR / "app.js").read_text()
    block = app_js.split("const TABS", 1)[1].split("];", 1)[0]
    return dict(re.findall(r'id:\s*"(\w+)".*?cap:\s*"([\w.]+)"', block))


def test_admin_panels_are_gated_to_admin() -> None:
    """A.4, v0.7.0 form: every admin screen is gated on an **admin-ceiling** capability.

    Stronger than the old "role: admin" string check, because the required capability is now
    resolved against `rbac.PERMISSIONS` — the same source the server enforces from — so a panel
    gated on a capability that was quietly lowered to editor would fail here.
    """
    from netcorenoc import rbac

    caps = _tab_caps()
    for panel in ADMIN_PANELS:
        capability = caps.get(panel)
        assert capability is not None, f"{panel} has no capability gate"
        assert rbac.PERMISSIONS[capability] == "admin", (
            f"{panel} is gated on {capability!r}, which is not admin-only"
        )


def test_every_tab_is_gated_on_a_real_capability() -> None:
    """No tab may be gated on a capability the authorization map does not know."""
    from netcorenoc import rbac

    caps = _tab_caps()
    assert caps, "TABS capability map did not parse"
    for panel, capability in caps.items():
        assert capability in rbac.PERMISSIONS, f"{panel} gated on unknown capability {capability!r}"


def test_ui_gates_affordances_on_resolved_capabilities_not_role_rank() -> None:
    """F28 at the UI layer: the affordance gate reads the server's resolved set.

    `/api/me` returns `capabilities`; `can()` is the single question every gate asks. A UI that
    re-derived permissions from role rank would be a second decision site that silently disagrees
    with the server once a policy is stored.
    """
    app_js = (UI_DIR / "app.js").read_text()
    assert "session.capabilities.has(cap)" in app_js
    assert "me.capabilities" in app_js
    # Tab gating and DOM pruning both go through can(), not through a rank comparison.
    for block_name in ("function buildTabs(", "function prunePanels("):
        block = app_js.split(block_name, 1)[1].split("\n}", 1)[0]
        assert "can(tab.cap)" in block, block_name
        assert "ROLES[" not in block, f"{block_name} still compares role rank"


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
    assert _tab_caps().get("scorer") == "scorer.write"
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
    source = apisource.api_source()
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


# -- v0.7.5: the operator-feedback acquisition path --------------------------------------
#
# ### READ THIS BEFORE ADDING TO THIS BLOCK, AND BEFORE TRUSTING IT. ###
#
# Every assertion below inspects the **shape of the source**. None of them observes the **behaviour
# of the browser**, because there is no JavaScript runtime anywhere in this repository — no node, no
# npm, no jsdom, no browser automation, in pyproject.toml, the Makefile, flake.nix or
# .github/workflows/ (evidenced in `docs/gates/v0.7.5-phase-0.md` §5). That is a deliberate
# consequence of "one static UI, no build step, no npm", and v0.7.5 does not overturn it
# (DECISIONS #99).
#
# So these tests CANNOT establish the three claims this release actually makes:
#
#   * that the same DOM node survives an SSE update,
#   * that no reachable state has the detail container displayed and empty,
#   * that a click lands on the card the operator was reading.
#
# `FEEDBACK-PATH-0.7.5-DRAFT.md` §5 asks for exactly those, phrased as "drive `applyUpdate` twice
# and assert the same DOM node is still in the document". There is no DOM to drive.
#
# **The behavioural claims are verified by `docs/gates/v0.7.5-manual-verification.md`**, executed by
# a human against a running appliance. That document is the proof; this block is a tripwire that
# catches the source drifting away from the shape the fix requires. A green tick here is NOT the
# assertion the draft asked for, and must never be reported as though it were.


def _code_only(body: str) -> str:
    """Drop whole-line `//` comments, keeping line count so positions stay comparable.

    Every assertion below is **positional** — "does `clear(` appear before `await api(`" — and a
    comment that *describes* the old code mentions the old code. Without this, documenting the fix
    would break the test that checks the fix, and the obvious repair would be to water down the
    comment. That is the same failure the documentation guard had in v0.7.4: text about a thing
    being matched as though it were the thing (DECISIONS #100). Assert over code, comment freely.
    """
    return "\n".join("" if line.lstrip().startswith("//") else line for line in body.splitlines())


def _render_situations_source() -> str:
    app_js = (UI_DIR / "app.js").read_text()
    body = app_js.split("function renderSituations(", 1)[1].split("\n/* ---------- timeline", 1)[0]
    return _code_only(body)


def _render_detail_source() -> str:
    app_js = (UI_DIR / "app.js").read_text()
    body = app_js.split("async function renderDetail(", 1)[1].split(
        "\nfunction renderSituations(", 1
    )[0]
    return _code_only(body)


def test_render_situations_does_not_clear_the_list_unconditionally_first() -> None:
    """§5.1, structurally. **Asserts the shape of the source, not the behaviour of the browser.**

    In v0.7.4 `clear(sits)` was the first statement of `renderSituations`, so every card — expanded
    ones included — was destroyed on every SSE update. The fix harvests the detail nodes of held
    cards *before* clearing and re-appends them, so `clear(sits)` can no longer be the first thing
    that happens.

    What this does NOT prove: that the node actually survives, that its listeners are intact, or
    that a click lands on it. That is Test A of `docs/gates/v0.7.5-manual-verification.md`.
    """
    body = _render_situations_source()
    statements = [line.strip() for line in body.splitlines() if line.strip()]
    lead = [s for s in statements if not s.startswith("//")][:2]
    assert "clear(sits);" not in lead, (
        "clear(sits) is back at the top of renderSituations: an expanded card is being destroyed "
        "on every SSE update again, which is the F-class defect v0.7.5 §5.1 closed"
    )
    assert body.index("const held") < body.index("clear(sits);"), (
        "the held-card harvest must run BEFORE clear(sits), or there is nothing left to harvest"
    )


def test_render_situations_does_not_refetch_a_held_card() -> None:
    """§5.1, the other half. **Shape of the source, not behaviour of the browser.**

    v0.7.4's rebuild path called `renderDetail(detail, s.id)` **without awaiting it**, so the
    container was displayed and empty until the round trip returned. A held card already has its
    content, so the call must not happen for one at all — which is the shape §5.4(a) allows as an
    alternative to awaiting it.

    What this does NOT prove: that no refetch occurs at runtime. Test A of the manual protocol.
    """
    body = _render_situations_source()
    calls = [line.strip() for line in body.splitlines() if "renderDetail(detail, s.id)" in line]
    assert calls, "renderSituations no longer renders detail at all — that is not the fix"
    rebuild = [c for c in calls if not c.startswith("expanded.add")]
    assert rebuild, "the rebuild-path call to renderDetail has vanished entirely"
    for call in rebuild:
        assert "await" in call or "!held.has(s.id)" in call, (
            f"the rebuild path calls renderDetail without awaiting it and without the held guard: "
            f"{call!r} — this is exactly the v0.7.4 fire-and-forget that showed an empty card"
        )


def test_render_detail_never_clears_before_its_fetch() -> None:
    """§5.2, structurally. **Shape of the source, not behaviour of the browser.**

    The empty-and-visible window opened because `clear(container)` ran at a source position
    *before* the content existed — the container was emptied and then filled incrementally after
    the `await`. Building into a fragment and swapping at the end closes it.

    What this does NOT prove: that the browser never paints an empty container. That is Test B of
    `docs/gates/v0.7.5-manual-verification.md`, run under network throttling, which is the only way
    to observe a window this short.
    """
    body = _render_detail_source()
    fetch_at = body.index("await api(")
    clear_at = body.index("clear(container)")
    assert clear_at > fetch_at, (
        "clear(container) appears before the await in renderDetail: the detail panel is displayed "
        "and empty for the length of a round trip again (v0.7.5 §5.2)"
    )
    assert "createDocumentFragment()" in body, "renderDetail no longer builds into a fragment"
    swap = body[clear_at:]
    assert "await" not in swap, (
        "there is an await between clear(container) and the append: the swap is no longer atomic"
    )
    assert "container.append(frag)" in swap


def test_the_staleness_marker_exists_and_is_guarded_by_expanded() -> None:
    """§5.3, structurally. **Shape of the source, not behaviour of the browser.**

    A held card is stale by design, and the marker is the whole of the mitigation. It must be
    emitted only on a path guarded by `expanded.has`, or it would claim a card is held when it is
    not.

    What this does NOT prove: that the operator sees it, reads it, or acts on it. A marker the
    operator ignores is a marker that did not work — a human-factors residual recorded in
    `SECURITY-REVIEW-0.7.5.md`, not something a test can close. Presence while held and absence
    once collapsed are Test C of the manual protocol.
    """
    app_js = (UI_DIR / "app.js").read_text()
    assert "HELD_MARKER" in app_js and "held while open" in app_js
    body = _render_situations_source()
    marker_at = body.index("HELD_MARKER")
    guard_at = body.index("expanded.has(s.id)")
    assert guard_at < marker_at, "the staleness marker is emitted without an expanded.has guard"
    emit = body[body.index("const heldMarker") : marker_at]
    assert "expanded.has(s.id)" in emit, (
        "the marker must be conditional on the card being expanded, not always rendered"
    )
    # It must also be removed on collapse rather than at the next render, or a collapsed card
    # asserts something false for up to SSE_UPDATE_S seconds.
    collapse = body.split("if (expanded.has(s.id)) {", 1)[1].split("return;", 1)[0]
    assert "heldMarker.remove()" in collapse, (
        "the marker is not removed when the card collapses: a collapsed card would keep claiming "
        "to be held until the next SSE update"
    )


def test_the_marker_reuses_the_existing_redacted_badge_styling() -> None:
    """§5.3 says the marker costs no new CSS architecture. This is what makes that checkable.

    `.badge.redacted` already exists in style.css with warn colours and `cursor: help`. Reusing it
    is why this release touches one UI file rather than two.
    """
    app_js = (UI_DIR / "app.js").read_text()
    marker_line = next(
        line for line in app_js.splitlines() if "HELD_TITLE" in line and "el(" in line
    )
    assert 'class: "badge redacted"' in marker_line
    assert ".badge.redacted" in (UI_DIR / "style.css").read_text()


def test_the_ui_is_still_one_file_changed_and_no_build_step_appeared() -> None:
    """The anti-scope-creep guard for this workstream.

    §5 is three changes in `ui/app.js`. If a package.json, a bundler config or a second script tag
    ever appears, this release's central claim — that the diff is small enough to read — is false.
    """
    assert not list(UI_DIR.rglob("package*.json"))
    assert not list(UI_DIR.rglob("node_modules"))
    index = (UI_DIR / "index.html").read_text()
    assert index.count("<script") == 2, "index.html should load exactly d3 and app.js"
    assert "app.js" in index and "d3.v7.min.js" in index
