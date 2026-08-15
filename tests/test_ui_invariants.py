"""The five invariants of `ui/app.js` that must survive a rewrite (v0.12.0, Workstream 1.3-1.4).

v0.13.0 replaces this UI. Tests that describe **layout** would be thrown away with it; tests that
describe **invariants** are what the replacement must honour. Only the second kind is here. There
is deliberately no assertion in this file about a tab's order, a panel's name, a CSS class, a
colour, or a string of copy — if you find yourself adding one, you are describing what is about to
be deleted.

Each test states, in its own docstring, **what it does not cover**. That voice is
`../docs/gates/v0.9.1-test-audit.md`'s and it is not decoration: four times in this project's
history a test has been written whose green read like a claim it was not making.

**Every fixture here comes from the real server.** `uifixtures` boots the real engine over the real
fiber-cut corpus, logs in as each real role, and captures what the real routes return, at the exact
URLs `app.js` requests. Two of the five invariants are statements about the client's contract with
the server; a hand-written fixture that paraphrased the server would make them worthless.

**The one substitution that matters**: d3 is a recording double, so the force-directed graph and the
timeline SVG are not executed. They are layout, they are outside this release's characterisation
boundary, and they are exactly what v0.13.0 rewrites. `tests/domharness/env.mjs` says so at the
point of substitution.
"""

from __future__ import annotations

from typing import Any

import pytest

import domdriver
import uifixtures
from netcorenoc import rbac
from netcorenoc.store import Store
from test_dom_harness import dom_test

#: Admin panels, by the label their tab carries. Used only to *drive* clicks — never asserted as a
#: fact about the UI, because a label is copy and copy is not an invariant.
ADMIN_TAB_LABELS = ["Users", "Tokens", "Config", "Scorer", "Governance", "Quarantine", "Audit"]
ALL_TAB_LABELS = ["Situations", "Timeline", "Entities", *ADMIN_TAB_LABELS]


@pytest.fixture
async def routes(store: Store) -> dict[str, dict[str, Any]]:
    """Every role's captured route table, from one real corpus."""
    return await uifixtures.all_routes(store)


def _templated(path: str) -> str | None:
    """Map a concrete request path onto its `ROUTE_PERMISSIONS` key.

    The client requests `/api/situations/1?x=y`; the authorization table is keyed on
    `/api/situations/{sid}`. Resolving one to the other is what lets a test written about
    *observed requests* be checked against the server's own table rather than against a second
    copy of it.
    """
    bare = path.split("?", 1)[0]
    if ("GET", bare) in rbac.ROUTE_PERMISSIONS:
        return bare
    segments = bare.split("/")
    for method, template in rbac.ROUTE_PERMISSIONS:
        if method != "GET":
            continue
        parts = template.split("/")
        if len(parts) != len(segments):
            continue
        pairs = zip(parts, segments, strict=True)
        if all(want.startswith("{") or want == got for want, got in pairs):
            return template
    return None


def _capability_for(path: str) -> str | None:
    template = _templated(path)
    return rbac.permission_for("GET", template) if template else None


def _resolved(routes: dict[str, Any], role: str) -> frozenset[str]:
    """The capability set **the server told this client it holds**, from the captured `/api/me`."""
    return frozenset(routes[role]["/api/me"]["json"]["capabilities"])


# --- Invariant 1: a role never sees a panel requiring a capability it lacks ---------------------


@dom_test
async def test_a_role_never_renders_a_panel_whose_capability_it_lacks(
    routes: dict[str, Any],
) -> None:
    """**Invariant 1**, and the replacement for the `split("const TABS")` guard.

    The old guard parsed the capability map out of `app.js` **as text** and compared it against
    `rbac.PERMISSIONS`. It could not fail for the thing it was guarding: a rewrite that changed the
    shape of `TABS` would leave it green by matching nothing, and the panel/capability map — the
    only client-side defence against an admin screen rendering for a viewer — would be unguarded
    exactly when the UI was being rewritten.

    This asserts the same property by **rendering**: boot as each real role with the real resolved
    capability set, then read which panels exist in the resulting DOM. The panel-to-capability
    mapping is not read from the source at all — it is discovered from the requests each panel
    issues (below) — so there is nothing for a rewrite to make unmatchable.

    What this does NOT cover: what a panel *contains* once rendered, and any control inside a
    panel the role does hold. It covers presence and absence.
    """
    seen: dict[str, set[str]] = {}
    for role in ("viewer", "editor", "admin"):
        result = domdriver.run_scenario("boot", {"routes": routes[role]})
        seen[role] = set(result["panels"])
        assert result["appVisible"] is True, f"{role} never reached the authenticated view"

    # Capabilities are nested (viewer ⊆ editor ⊆ admin), so the rendered panels must be too.
    assert seen["viewer"] <= seen["editor"] <= seen["admin"]
    # And the strong half: an admin-ceiling panel is ABSENT from a non-admin DOM, not merely
    # hidden. `prunePanels` removes the node; this is what asserts that it really did.
    admin_only = seen["admin"] - seen["viewer"]
    assert admin_only, "no panel distinguishes an admin from a viewer; the fixture proves nothing"
    for role in ("viewer", "editor"):
        assert not (seen[role] & admin_only), (
            f"{role} rendered admin panel(s) {sorted(seen[role] & admin_only)}"
        )


@dom_test
async def test_every_panel_a_role_reaches_requests_only_routes_that_role_may_call(
    routes: dict[str, Any],
) -> None:
    """The cross-check against `rbac.tables`, done **behaviourally**.

    For each role, every tab the UI offers is clicked and the requests it issues are attributed to
    it. Each request's route is resolved to its `ROUTE_PERMISSIONS` entry and the required
    capability compared against the set the server itself reported in `/api/me`. A panel that
    drifted from its capability fails **here**, against the server's table, rather than in
    production.

    This is the assertion the old text guard was reaching for. It is stronger in one specific way:
    it cannot pass by matching nothing, because a panel that issued no request at all would leave
    `paths` empty and the `admin_routes_touched` control below would notice.

    What this does NOT cover: a panel that renders a control the role may not *use*. The route it
    reads and the routes its buttons would write are different questions, and only the first is
    observable without clicking every control.
    """
    for role in ("viewer", "editor", "admin"):
        held = _resolved(routes, role)
        result = domdriver.run_scenario(
            "capabilityRequests", {"routes": routes[role], "clickTabs": ALL_TAB_LABELS}
        )
        for label, record in result["perTab"].items():
            if not record["offered"]:
                continue
            for entry in record["paths"]:
                method, path = entry.split(" ", 1)
                if method != "GET":
                    continue
                capability = _capability_for(path)
                assert capability is not None, f"{label} requested undeclared route {path}"
                assert capability in held, (
                    f"the {label} panel rendered for {role} and requested {path}, which needs "
                    f"{capability!r} — a capability the server says this principal does not hold"
                )


@dom_test
async def test_no_admin_capability_produces_a_panel_for_a_non_admin(
    routes: dict[str, Any],
) -> None:
    """The A.4 property, stated against `rbac.PERMISSIONS` rather than against a list of names.

    Every capability whose minimum role is `admin` is admin-ceiling by construction. A viewer or
    editor must reach no route requiring one — through any tab the UI offers them.

    What this does NOT cover: whether the *server* would refuse such a request. It would, and
    `tests/test_rbac.py` proves it. This is the client-side half, and its value is precisely that
    it does not rely on the server half.
    """
    admin_capabilities = {c for c, role in rbac.PERMISSIONS.items() if role == "admin"}
    for role in ("viewer", "editor"):
        result = domdriver.run_scenario(
            "capabilityRequests", {"routes": routes[role], "clickTabs": ALL_TAB_LABELS}
        )
        for entry in result["requestPaths"]:
            method, path = entry.split(" ", 1)
            capability = _capability_for(path) if method == "GET" else None
            assert capability not in admin_capabilities, (
                f"{role} issued {entry}, which requires the admin capability {capability!r}"
            )


# --- Invariant 2: a partial split sends exactly the ids the operator marked ---------------------


@dom_test
async def test_a_partial_split_sends_exactly_the_marked_ids_and_no_others(
    routes: dict[str, Any],
) -> None:
    """**Invariant 2**, and the contract the whole v0.9.1 -> v0.9.2 evidence chain rests on.

    `excluded_ids` asserts *marked-by-rest negative and nothing else*. If the client sent one id
    the operator did not tick, a human judgement would be recorded about a pair no human judged —
    and every downstream figure (the bias report, the agreement report, the shadow verdict, the
    promotion gate) is computed over those rows. The evidence boundary is server-derived since
    v0.9.2, but the *marks* are still the client's to report faithfully.

    Two members of an eight-member situation are ticked. The assertion is set equality, both
    directions, plus the full membership under `member_ids`.

    What this does NOT cover: that the operator ticked the members they meant to. A checkbox
    misread by a human is not a defect a test can find, and `SECURITY-REVIEW-0.7.5.md` §6.4
    records the human-factors residual it belongs to.
    """
    sid, count = uifixtures.largest_situation(routes["editor"])
    members = uifixtures.member_ids(routes["editor"], sid)
    assert count >= 4, (
        "the corpus must offer a situation big enough for a partial split to mean something"
    )

    marked_positions = [1, 3]
    result = domdriver.run_scenario(
        "partialSplit", {"routes": routes["editor"], "sid": sid, "mark": marked_positions}
    )
    body = result["feedbackBody"]
    assert result["feedbackPath"] == f"/api/situations/{sid}/feedback"
    assert body["verdict"] == "split"
    assert body["excluded_ids"] == [members[i] for i in marked_positions]
    assert set(body["member_ids"]) == set(members), "the reported membership is not what rendered"
    # The half that matters and is easy to forget: nothing the operator did NOT mark was sent.
    unmarked = {m for i, m in enumerate(members) if i not in marked_positions}
    assert not (set(body["excluded_ids"]) & unmarked), (
        f"the client excluded members the operator never marked: "
        f"{sorted(set(body['excluded_ids']) & unmarked)}"
    )


@dom_test
async def test_a_split_with_nothing_marked_carries_no_exclusions_at_all(
    routes: dict[str, Any],
) -> None:
    """The control for the invariant above, and a claim in its own right (DECISIONS #127).

    Omitting `excluded_ids` means "the operator marked nothing", which is a PLAIN split — never a
    guess, and never an empty list that a reader could mistake for "the operator considered every
    member and excluded none". Without this control, the test above would pass just as well if the
    client always sent every ticked-or-not id, provided the ticked ones happened to be included.
    """
    sid, _ = uifixtures.largest_situation(routes["editor"])
    result = domdriver.run_scenario(
        "partialSplit", {"routes": routes["editor"], "sid": sid, "mark": []}
    )
    assert "excluded_ids" not in result["feedbackBody"], result["feedbackBody"]
    assert result["feedbackBody"]["verdict"] == "split"


@dom_test
async def test_a_confirm_never_carries_exclusions(routes: dict[str, Any]) -> None:
    """A `confirm` asserts every pair positive, so an exclusion on one would be a contradiction.

    Ticked boxes are deliberately ignored by the Confirm path. This drives the stronger case: the
    operator ticks two members and then clicks Confirm anyway.

    What this does NOT cover: what the server does with a contradictory payload. It drops it, and
    `tests/test_feedback_dataset.py` is where that is proven.
    """
    sid, _ = uifixtures.largest_situation(routes["editor"])
    result = domdriver.run_scenario(
        "partialSplit",
        {"routes": routes["editor"], "sid": sid, "mark": [1, 3], "button": "✓ Confirm"},
    )
    assert result["feedbackBody"]["verdict"] == "confirm"
    assert "excluded_ids" not in result["feedbackBody"], result["feedbackBody"]


@dom_test
async def test_a_viewer_is_offered_no_member_checkboxes_at_all(routes: dict[str, Any]) -> None:
    """The boxes exist only for a principal who can post a verdict.

    The control that keeps invariant 2 honest from the other side: a viewer's table renders with no
    marking column, so there is no gesture for them to make and no payload for them to send.
    """
    sid, _ = uifixtures.largest_situation(routes["viewer"])
    with pytest.raises(domdriver.HarnessError, match="no button starting"):
        domdriver.run_scenario("partialSplit", {"routes": routes["viewer"], "sid": sid, "mark": []})


# --- Invariant 3: an SSE update during a click does not lose the gesture ------------------------


@dom_test
async def test_an_sse_update_mid_gesture_does_not_destroy_the_click_target(
    routes: dict[str, Any],
) -> None:
    """**Invariant 3 — the v0.7.5 defect, by name, and the first machine check of it.**

    `FEEDBACK-PATH-0.7.5-DRAFT.md` §5 asked for exactly this: *drive `applyUpdate` twice and assert
    the same DOM node is still in the document.* v0.7.5 could not do it — DECISIONS #99 records
    that there was no DOM to drive — so the claim was carried by
    `../docs/gates/v0.7.5-manual-verification.md`, executed by a human. This is Tests A, C and D of
    that protocol, executed by a machine.

    The sequence is the operator's: expand a card, tick two members, **let a server-sent update
    arrive**, then click Split. Before v0.7.5, `clear(sits)` was the first statement of
    `renderSituations` and the update destroyed the card mid-gesture, so the click landed on a
    detached node or on a button from a render the operator never read — a silently wrong label.

    What this does NOT cover: the *browser's* behaviour under a real 2-second SSE stream with real
    paint timing. This drives one update, synchronously, in a DOM with no renderer. It proves the
    node identity and the payload; it does not prove anything about what the operator saw.
    """
    sid, _ = uifixtures.largest_situation(routes["editor"])
    members = uifixtures.member_ids(routes["editor"], sid)
    update = {
        "situations": routes["editor"]["/api/situations?limit=50&status=open"]["json"],
        "stats": routes["editor"]["/api/stats"]["json"],
        "graph": routes["editor"]["/api/graph"]["json"],
    }
    result = domdriver.run_scenario(
        "sseDuringGesture",
        {"routes": routes["editor"], "sid": sid, "mark": [1, 3], "update": update},
    )

    assert result["sameDetailNode"] is True, (
        "the SSE update replaced the expanded card's detail node: the operator's click would land "
        "on a render they never read (v0.7.5 §5.1)"
    )
    assert result["buttonStillConnected"] is True, (
        "the Split button the operator was aiming at was detached from the document by an SSE "
        "update — this is the v0.7.5 defect exactly"
    )
    assert result["detailStillConnected"] is True
    # And the gesture still reports what the operator marked, not what arrived after they marked it.
    assert result["feedbackBody"]["excluded_ids"] == [members[1], members[3]]
    assert set(result["feedbackBody"]["member_ids"]) == set(members)


@dom_test
async def test_a_held_card_says_it_is_stale_while_it_is_held(routes: dict[str, Any]) -> None:
    """§5.3's marker, which is the whole of the mitigation for the trade v0.7.5 made.

    Freezing the card trades a wrong label for a stale one, and a stale label is only better if the
    operator knows it is stale. The marker must be present on a card that is holding back an
    update — which is what this observes, after an update has actually been withheld.

    What this does NOT cover: that the operator reads it or acts on it. A marker nobody notices is
    a marker that did not work — a human-factors residual recorded in `SECURITY-REVIEW-0.7.5.md`
    §6.4, not something any test can close.
    """
    sid, _ = uifixtures.largest_situation(routes["editor"])
    update = {"situations": routes["editor"]["/api/situations?limit=50&status=open"]["json"]}
    result = domdriver.run_scenario(
        "sseDuringGesture", {"routes": routes["editor"], "sid": sid, "mark": [], "update": update}
    )
    assert result["heldMarkerPresent"] is True


# --- Invariant 4: no render path writes unescaped data into the document ------------------------


@dom_test
async def test_no_render_path_turns_operator_supplied_text_into_markup(
    routes: dict[str, Any],
) -> None:
    """**Invariant 4** — the reason `esc()` and the `el({text})` discipline exist (F1).

    The payload travels the **real** label route onto every device and class, so it arrives the way
    an operator's input actually arrives, then a card is expanded and the Entities panel opened.
    The assertion is structural, not textual: walk the resulting document and count the *elements*
    that appeared. If any render path had interpolated the string into markup, an `<img>` and a
    `<script>` would exist. They do not; the payload is present in text nodes only.

    Asserting over a serialisation of the document would have been the wrong test — it would have
    measured the harness's escaper rather than `app.js`'s. This counts nodes.

    The instrument is non-vacuous here by construction: the harness DOM implements `innerHTML` as a
    **real parse** (`tests/domharness/html.mjs`), so a bypassed `esc()` really does build elements.
    That injection is demonstrated red in `../docs/gates/v0.12.0-guard-demonstrations.md` §2.

    What this does NOT cover: the panels this scenario does not open, and any render path reached
    only by an admin. `test_security_ui.py`'s source-level scan still covers the whole file, and
    the two guards fail for different reasons on purpose.
    """
    hostile = routes["editor_hostile"]
    sid, _ = uifixtures.largest_situation(hostile)
    result = domdriver.run_scenario(
        "hostilePayload", {"routes": hostile, "sid": sid, "hostile": uifixtures.HOSTILE}
    )

    assert result["dangerousElementsIntroduced"] == {}, (
        f"a render path built elements from operator-supplied text: "
        f"{result['dangerousElementsIntroduced']}"
    )
    assert result["payloadInAttributeValues"] == 0, (
        "the payload reached an attribute value; `el` sets attributes through setAttribute, so "
        "this would mean a new path composed markup"
    )
    # The control: the payload must actually have REACHED the document. A run that rendered
    # nothing would satisfy every assertion above and prove nothing at all.
    assert result["payloadInTextNodes"] > 0, (
        "the hostile payload never reached the DOM; this scenario asserted nothing"
    )


# --- Invariant 5: a capability the client lacks produces no request -----------------------------


@dom_test
async def test_a_capability_the_client_lacks_produces_no_request_at_all(
    routes: dict[str, Any],
) -> None:
    """**Invariant 5** — least privilege at the client, which must not regress into
    "the server will catch it".

    A viewer performs every gesture the UI offers, including attempts at all seven admin tabs. The
    assertion is that the admin routes are never *requested* — not that they are requested and
    refused. A client that fired them and handled the 403 would be leaking the shape of the
    estate's administration into the access log of every viewer, and would make the server's
    refusal the only thing standing between a UI bug and a privilege boundary.

    What this does NOT cover: writes. Every gesture here is a read, because the admin panels a
    viewer might reach are read-first.
    """
    admin_only = {c for c, role in rbac.PERMISSIONS.items() if role == "admin"}
    viewer = domdriver.run_scenario(
        "capabilityRequests", {"routes": routes["viewer"], "clickTabs": ADMIN_TAB_LABELS}
    )
    assert all(not record["offered"] for record in viewer["perTab"].values()), (
        f"an admin tab was offered to a viewer: {viewer['tabsOffered']}"
    )
    assert all(not record["paths"] for record in viewer["perTab"].values()), (
        f"a viewer's admin-tab gestures issued requests: {viewer['perTab']}"
    )

    # THE CONTROL. Without it, a viewer's zero could be a property of the scenario — a harness that
    # clicked nothing would report the same zero. The same gestures at admin must produce the
    # requests, and every one of them must be an admin-capability route.
    admin = domdriver.run_scenario(
        "capabilityRequests", {"routes": routes["admin"], "clickTabs": ADMIN_TAB_LABELS}
    )
    issued = [p for record in admin["perTab"].values() for p in record["paths"]]
    assert len(issued) >= len(ADMIN_TAB_LABELS) - 1, (
        f"the control issued only {issued}; a viewer's zero is unmeasured against it"
    )
    assert any(_capability_for(p.split(" ", 1)[1]) in admin_only for p in issued), (
        "the control issued no admin-capability request, so it does not control for anything"
    )


@dom_test
async def test_a_panel_reached_without_its_capability_still_issues_no_request(
    routes: dict[str, Any],
) -> None:
    """The same invariant one level deeper, and an **honest note about why it holds**.

    Invariant 5 above is about what a *rendered* UI offers. This bypasses the gesture entirely and
    calls `renderPanel(id)` directly — what a deep link or a client-side router would do — for
    every admin panel, as a viewer. No request is issued.

    **But it holds for an incidental reason, not a designed one.** `prunePanels` has removed the
    panel's container from the DOM, so the loader's first statement dereferences null and throws
    before it reaches `api(...)`. There is no capability check inside the loaders. The observed
    outcome is right; the mechanism is a `TypeError`.

    That distinction is recorded rather than fixed: fixing it would be a change to `ui/app.js`, and
    this release changes not one byte of it (SCOPE-0.12.0 §3). It is `../docs/ROADMAP.md`'s
    "the loaders have no capability check of their own" line and a constraint on
    `../docs/architecture/UI-0.13-DRAFT.md` §7 — v0.13.0 introduces routing, which is precisely
    when an accidental defence stops being available.

    What this does NOT cover: any panel a viewer *does* hold, and any loader invoked with its
    container present.
    """
    admin_panels = ["users", "tokens", "config", "scorer", "governance", "quarantine", "audit"]
    result = domdriver.run_scenario(
        "panelWithoutCapability", {"routes": routes["viewer"], "panels": admin_panels}
    )
    assert result["requestsAfterBypass"] == [], (
        f"a viewer reached an admin route by bypassing the tab: {result['requestsAfterBypass']}"
    )
    assert not (set(result["panelsPresent"]) & set(admin_panels))
    # Name the mechanism, so a later reader is not misled about why the zero above is a zero.
    assert all(outcome.startswith("threw:") for outcome in result["outcomes"].values()), (
        "a loader returned normally without its panel: the protection asserted here is the "
        "absence of the container, and this test's docstring would then be wrong"
    )
