"""The five invariants of the console that had to survive the rewrite (v0.12.0 → v0.13.0).

v0.12.0 captured these **specifically so they would survive this release**. v0.13.0 replaced every
byte of the UI, so every selector below changed — and `UI-0.13-DRAFT.md` §1.1 sets the rule that
governs a rewrite like that:

> When a selector changes, the assertion count in `tests/test_ui_invariants.py` may not go down. A
> guard rewritten during the change it guards is at its least trustworthy, and the count is the
> cheapest thing to check.

**It went up.** `test_the_assertion_count_did_not_go_down` at the bottom of this file measures it
rather than asserting it in prose, and the v0.12.0 figure it compares against is a constant here so
the comparison is a diff a reviewer can read.

There is deliberately no assertion in this file about a nav item's order, a heading's text, a CSS
class, a colour, or a string of copy — if you find yourself adding one, you are describing
something that will be deleted.

**Every fixture here comes from the real server.** `uifixtures` boots the real engine over the real
fiber-cut corpus, logs in as each real role, and captures what the real routes return, at the exact
URLs the console requests.

**The one substitution that matters**: d3 is a recording double, so the force-directed graph and
the timeline SVG are not executed. `src/netcorenoc/ui/app/views/graph.js` says so at the top and
`docs/security/SECURITY-REVIEW-0.13.0.md` records it as this release's largest uncovered surface.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

import domdriver
import uifixtures
from netcorenoc.crosscutting import rbac
from netcorenoc.store import Store
from test_dom_harness import dom_test

#: Every view in the console's registry, by id. Used to *drive* — never asserted as a fact about
#: the UI, because a registry is data and this file describes behaviour.
ALL_VIEWS = [
    "overview",
    "situations",
    "graph",
    "timeline",
    "entities",
    "classes",
    "labelling",
    "corpus",
    "promotion",
    "users",
    "tokens",
    "settings",
    "scorer",
    "governance",
    "quarantine",
    "audit",
    "account",
]

#: The views whose capability's minimum role is `admin`, discovered below rather than listed.
ADMIN_VIEWS = ["users", "tokens", "settings", "scorer", "governance", "quarantine", "audit"]


def tap_floor_tags(stylesheet: Path) -> set[str]:
    """Which element types the stylesheet's tap-target floor actually reaches.

    **This is where F103 hid, and the reason it is a function now.** The v0.16.2 version of this
    read the floor's selector and normalised each part with
    ``part.strip().split(":")[0].split("[")[0]`` — which turns
    ``input:not([type="checkbox"]):not([type="radio"])`` into ``input`` and reports that inputs are
    covered. The exclusion that *was the defect* is exactly the substring the normaliser threw
    away, so the guard came back green over 72 controls measuring 13x13 px in the same tree. A
    guard that passes over the control it exists to protect is worse than no guard.

    So a part carrying a negation is **not** counted as covering its tag. ``input:not([disabled])``
    would be refused too, and that is deliberate: the question this answers is *"does the floor
    reach every control of this kind"*, and any negation means the honest answer is no. A floor
    that genuinely needs to exclude something should say so where a reader sees it, not inside a
    selector a normaliser flattens.
    """
    css = stylesheet.read_text(encoding="utf-8")
    floor = re.search(r"^([^\n{]*)\{\s*min-height: var\(--tap\);", css, re.M)
    assert floor is not None, "the stylesheet no longer states a tap-target floor at all"
    covered: set[str] = set()
    for part in floor.group(1).split(","):
        selector = part.strip()
        if ":not(" in selector:
            continue  # a negation is an exclusion, and an excluded control is F103
        covered.add(selector.split(":")[0].split("[")[0])
    return covered


@pytest.fixture
async def routes(store: Store) -> dict[str, dict[str, Any]]:
    """Every role's captured route table, from one real corpus."""
    return await uifixtures.all_routes(store)


def _templated(path: str) -> str | None:
    """Map a concrete request path onto its `ROUTE_PERMISSIONS` key."""
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


# --- Invariant 1: a role never sees a view requiring a capability it lacks ----------------------


@dom_test
async def test_a_role_never_renders_a_view_whose_capability_it_lacks(
    routes: dict[str, Any],
) -> None:
    """**Invariant 1**, restated against a UI where absence is the default.

    v0.12.0 declared ten panels in `index.html` and *deleted* the ones a role could not hold, so
    the property was about a successful deletion. It is now about a decision that was never taken:
    the document is a mount point, the registry is filtered by `reachableViews(capabilities)`, and
    a view a role lacks is never rendered because nothing ever asked for it.

    The view-to-capability mapping is **not read from the source**. It is discovered from the
    requests each view issues, exactly as v0.12.0 discovered it, so there is nothing for a rewrite
    to make unmatchable.

    **Why the admin set comes from `rbac.PERMISSIONS` and not from a set difference.** The v0.12.0
    demonstration showed a difference-based assertion is unfalsifiable by the defect it exists to
    catch: lower a view's required capability so a viewer *does* see it, and the view simply leaves
    the difference set. The expected set must come from an authority the injection does not touch.

    What this does NOT cover: what a view *contains* once rendered, and any control inside a view
    the role does hold.
    """
    offered: dict[str, set[str]] = {}
    for role in ("viewer", "editor", "admin"):
        result = domdriver.run_scenario("boot", {"routes": routes[role]})
        offered[role] = {href.removeprefix("#/") for href in result["navHrefs"]}
        assert result["appVisible"] is True, f"{role} never reached the authenticated view"
        assert result["loginVisible"] is False, f"{role} was left on the sign-in card"

    # Capabilities are nested (viewer ⊆ editor ⊆ admin), so the offered views must be too.
    assert offered["viewer"] <= offered["editor"] <= offered["admin"]

    admin_only = _admin_ceiling_views(routes)
    assert admin_only, "no view resolved to an admin capability; the fixture proves nothing"
    for role in ("viewer", "editor"):
        assert not (offered[role] & admin_only), (
            f"{role} was offered admin view(s) {sorted(offered[role] & admin_only)}"
        )


def _admin_ceiling_views(routes: dict[str, Any]) -> set[str]:
    """Views that read an admin-only route, discovered by execution at admin."""
    admin_capabilities = {c for c, role in rbac.PERMISSIONS.items() if role == "admin"}
    result = domdriver.run_scenario(
        "capabilityRequests", {"routes": routes["admin"], "visit": ALL_VIEWS}
    )
    found: set[str] = set()
    for view_id, record in result["perView"].items():
        for entry in record["paths"]:
            method, path = entry.split(" ", 1)
            if method == "GET" and _capability_for(path) in admin_capabilities:
                found.add(view_id)
    return found


@dom_test
async def test_every_view_a_role_reaches_requests_only_routes_that_role_may_call(
    routes: dict[str, Any],
) -> None:
    """The cross-check against `rbac.tables`, done **behaviourally**.

    For each role, every view is visited **by address** — not by clicking a nav item, which is the
    important change from v0.12.0. A test that only clicks what is offered can never reach the
    case a router creates, and the case a router creates is F53.

    What this does NOT cover: a view that renders a control the role may not *use*. The route it
    reads and the routes its buttons would write are different questions.
    """
    for role in ("viewer", "editor", "admin"):
        held = _resolved(routes, role)
        result = domdriver.run_scenario(
            "capabilityRequests", {"routes": routes[role], "visit": ALL_VIEWS}
        )
        for view_id, record in result["perView"].items():
            for entry in record["paths"]:
                method, path = entry.split(" ", 1)
                if method != "GET":
                    continue
                capability = _capability_for(path)
                assert capability is not None, f"{view_id} requested undeclared route {path}"
                assert capability in held, (
                    f"the {view_id} view rendered for {role} and requested {path}, which needs "
                    f"{capability!r} — a capability the server says this principal does not hold"
                )


@dom_test
async def test_no_admin_capability_produces_a_view_for_a_non_admin(
    routes: dict[str, Any],
) -> None:
    """The A.4 property, stated against `rbac.PERMISSIONS` rather than against a list of names.

    What this does NOT cover: whether the *server* would refuse such a request. It would, and
    `tests/test_rbac.py` proves it. This is the client-side half, and its value is precisely that
    it does not rely on the server half.
    """
    admin_capabilities = {c for c, role in rbac.PERMISSIONS.items() if role == "admin"}
    for role in ("viewer", "editor"):
        result = domdriver.run_scenario(
            "capabilityRequests", {"routes": routes[role], "visit": ALL_VIEWS}
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
    """**Invariant 2**, and the contract the whole v0.9.1 → v0.9.2 evidence chain rests on.

    `excluded_ids` asserts *marked-by-rest negative and nothing else*. If the client sent one id
    the operator did not tick, a human judgement would be recorded about a pair no human judged —
    and every downstream figure is computed over those rows.

    The **gesture** changed completely (a Preact component with its own marked-set state replaced
    a closure over a `Set`); the **payload** did not, which is what draft §11.12 requires.

    What this does NOT cover: that the operator ticked the members they meant to.
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
    guess, and never an empty list a reader could mistake for "the operator considered every
    member and excluded none".
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

    Drives the stronger case: the operator ticks two members and then clicks Confirm anyway.
    """
    sid, _ = uifixtures.largest_situation(routes["editor"])
    result = domdriver.run_scenario(
        "partialSplit",
        {"routes": routes["editor"], "sid": sid, "mark": [1, 3], "button": "Confirm"},
    )
    assert result["feedbackBody"]["verdict"] == "confirm"
    assert "excluded_ids" not in result["feedbackBody"], result["feedbackBody"]


@dom_test
async def test_a_viewer_is_offered_no_member_checkboxes_at_all(routes: dict[str, Any]) -> None:
    """The boxes exist only for a principal who can post a verdict."""
    sid, _ = uifixtures.largest_situation(routes["viewer"])
    with pytest.raises(domdriver.HarnessError, match="no button starting"):
        domdriver.run_scenario("partialSplit", {"routes": routes["viewer"], "sid": sid, "mark": []})


# --- Invariant 3: an SSE update during a click does not lose the gesture ------------------------


@dom_test
async def test_an_sse_update_mid_gesture_does_not_destroy_the_click_target(
    routes: dict[str, Any],
) -> None:
    """**Invariant 3 — the v0.7.5 defect, by name.**

    The sequence is the operator's: expand a card, tick two members, **let a server-sent update
    arrive**, then click Split.

    **The mechanism changed and the property did not, which is the point.** In v0.12.0 the detail
    node survived because `renderSituations` harvested and re-appended it before `clear(sits)`. It
    now survives because the reconciler diffs rather than rebuilds, *and* because the payload
    behind an open card is held (ADR #173). Both halves are needed: node identity alone would
    leave an operator's ticks pointing at a membership that changed underneath them.

    What this does NOT cover: the *browser's* behaviour under a real SSE stream with real paint
    timing. This drives one update, synchronously, in a DOM with no renderer.
    """
    sid, _ = uifixtures.largest_situation(routes["editor"])
    members = uifixtures.member_ids(routes["editor"], sid)
    update = {
        "situations": routes["editor"]["/api/situations?limit=50"]["json"],
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
    assert result["feedbackBody"]["excluded_ids"] == [members[1], members[3]]
    assert set(result["feedbackBody"]["member_ids"]) == set(members)


@dom_test
async def test_a_held_card_says_it_is_stale_while_it_is_held(routes: dict[str, Any]) -> None:
    """§5.3's marker, which is the whole of the mitigation for the trade v0.7.5 made.

    Freezing the card trades a wrong label for a stale one, and a stale label is only better if the
    operator knows it is stale.

    What this does NOT cover: that the operator reads it or acts on it.
    """
    sid, _ = uifixtures.largest_situation(routes["editor"])
    update = {"situations": routes["editor"]["/api/situations?limit=50"]["json"]}
    result = domdriver.run_scenario(
        "sseDuringGesture", {"routes": routes["editor"], "sid": sid, "mark": [], "update": update}
    )
    assert result["heldMarkerPresent"] is True


# --- Invariant 4: no render path writes unescaped data into the document ------------------------


@dom_test
async def test_no_render_path_turns_operator_supplied_text_into_markup(
    routes: dict[str, Any],
) -> None:
    """**Invariant 4** — the reason `esc()` and the text-node discipline exist (F1).

    The payload travels the **real** label route onto every device and class, then a card is
    expanded and the Entities screen opened. The assertion is structural: walk the resulting
    document and count the *elements* that appeared.

    **The property is now structural rather than conventional.** v0.12.0 kept it by discipline
    across 55 functions; an interpolated value in a tagged template becomes a text node because
    that is what the diff does with it. The only way back is `dangerouslySetInnerHTML`, which no
    module uses and `test_security_ui.py` refuses.

    The instrument is non-vacuous here by construction: the harness DOM implements `innerHTML` as a
    **real parse**, so a bypassed escape really does build elements.

    What this does NOT cover: the screens this scenario does not open.
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
        "the payload reached an attribute value, which would mean a path composed markup"
    )
    # The control: the payload must actually have REACHED the document.
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

    A viewer visits every admin view **by address**. The assertion is that the admin routes are
    never *requested* — not that they are requested and refused.

    What this does NOT cover: writes. Every gesture here is a read.
    """
    admin_only = {c for c, role in rbac.PERMISSIONS.items() if role == "admin"}
    viewer = domdriver.run_scenario(
        "capabilityRequests", {"routes": routes["viewer"], "visit": ADMIN_VIEWS}
    )
    assert all(not record["offered"] for record in viewer["perView"].values()), (
        f"an admin view was offered to a viewer: {viewer['viewsOffered']}"
    )
    assert all(record["refused"] for record in viewer["perView"].values()), (
        f"a viewer reaching an admin view by address was not refused: {viewer['perView']}"
    )
    assert all(not record["paths"] for record in viewer["perView"].values()), (
        f"a viewer's admin-view visits issued requests: {viewer['perView']}"
    )

    # THE CONTROL. Without it, a viewer's zero could be a property of the scenario — a harness that
    # navigated nowhere would report the same zero.
    admin = domdriver.run_scenario(
        "capabilityRequests", {"routes": routes["admin"], "visit": ADMIN_VIEWS}
    )
    issued = [p for record in admin["perView"].values() for p in record["paths"]]
    assert len(issued) >= len(ADMIN_VIEWS) - 1, (
        f"the control issued only {issued}; a viewer's zero is unmeasured against it"
    )
    assert any(_capability_for(p.split(" ", 1)[1]) in admin_only for p in issued), (
        "the control issued no admin-capability request, so it does not control for anything"
    )


@dom_test
async def test_a_view_reached_by_address_without_its_capability_refuses_by_decision(
    routes: dict[str, Any],
) -> None:
    """**F53, repaired — and this test is the one that changed meaning.**

    v0.12.0's version of this asserted the absent request **and the `TypeError`**, and its
    docstring said plainly that the zero held *"for an incidental reason, not a designed one"*:
    `prunePanels` had removed the container, so `clear(null)` threw before `api(...)` was reached.
    It ended with *"when the mechanism becomes deliberate the test fails and has to be updated
    deliberately"*. This is that deliberate update.

    Now: a viewer resolving every admin address gets a **refusal**, no request, **and no
    exception**. The three assertions are:

      * ``paths == []``   — the property, unchanged;
      * ``refused``       — the mechanism, now visible on screen for the operator affected;
      * ``threw is None`` — **the assertion that inverts v0.12.0's.** A `TypeError` here would mean
        the repair had regressed to an accident that happens to produce the same zero.

    What this does NOT cover: whether the server would refuse the same request. It would, and
    `tests/test_rbac.py` proves it.
    """
    result = domdriver.run_scenario(
        "navigateTo", {"routes": routes["viewer"], "fragments": [f"#/{v}" for v in ADMIN_VIEWS]}
    )
    for fragment, outcome in result["outcomes"].items():
        assert outcome["paths"] == [], f"{fragment} issued {outcome['paths']} for a viewer"
        assert outcome["refused"] is True, f"{fragment} did not render a refusal: {outcome}"
        assert outcome["threw"] is None, (
            f"{fragment} terminated in an exception ({outcome['threw']}). The zero above is then "
            f"a dereference rather than a decision, which is F53 exactly."
        )
        assert outcome["activeView"] is None, (
            f"{fragment} mounted a view component despite refusing: {outcome}"
        )


@dom_test
async def test_an_unknown_address_renders_nothing_and_requests_nothing(
    routes: dict[str, Any],
) -> None:
    """The other half of routing's new surface: a fragment that names no view.

    A router that fell through to a default view would silently send an operator somewhere they
    did not ask for; one that threw would be F53's shape again with a different trigger.
    """
    result = domdriver.run_scenario(
        "navigateTo", {"routes": routes["viewer"], "fragments": ["#/nope", "#/../etc/passwd"]}
    )
    for fragment, outcome in result["outcomes"].items():
        assert outcome["unknown"] is True, f"{fragment} did not report an unknown view: {outcome}"
        assert outcome["paths"] == [], f"{fragment} issued {outcome['paths']}"
        assert outcome["threw"] is None, f"{fragment} threw: {outcome['threw']}"


# --- v0.13.0's own invariants -------------------------------------------------------------------


@dom_test
async def test_a_hardening_only_value_cannot_be_lowered_through_this_surface(
    routes: dict[str, Any],
) -> None:
    """**Principle 9 at the client**, and the assertion §V.3 requires.

    > `resolved = max(project floor, deployment policy)` — harden always, soften never.

    An admin submits `tau_s = 0.1` against a project floor the **server published** (there is no
    client-side copy of a bound anywhere in the console). The console must refuse it, show the
    four things draft §6.2 requires, **and issue no request** — a client that fired the request
    and rendered the 400 would teach the operator that the console does not know its own rules.

    What this does NOT cover: the server's refusal, which is `scoring.validate_params` and is
    proven in `tests/test_scorer_api.py`. The console's refusal is an affordance; the appliance's
    is the control; neither may be the only one.
    """
    result = domdriver.run_scenario(
        "submitForm",
        {
            "routes": routes["admin"],
            "navigate": "#/scorer",
            "fields": {"#sc-tau_s": "0.1"},
            "click": ["Preview effect"],
        },
    )
    assert result["sent"] == [], (
        f"a value below the project floor was sent to the appliance: {result['sent']}"
    )
    assert result["refusalShown"] is True, "no refusal was rendered"
    text = result["refusalText"]
    # Draft §6.2's four things, each checked for rather than assumed present.
    assert "0.1" in text, "the refusal does not say what the operator submitted"
    assert "not applied" in text, "the refusal does not say the value was not applied"
    assert "nothing was sent" in text.lower(), "the refusal does not say no request was issued"
    assert "step function" in text or "discriminat" in text, (
        "the refusal does not say WHY the floor exists; a bare rejection teaches nothing and "
        "invites a workaround (SECURITY-REVIEW-0.6 §4 treats wording as a control)"
    )
    assert "accepted" in text, "the refusal does not say the stricter direction is available"


@dom_test
async def test_the_control_a_value_inside_the_bounds_is_sent(routes: dict[str, Any]) -> None:
    """**The control for the refusal above.** Without it, the zero could be a console that never
    sends anything at all — which would pass the assertion and mean nothing."""
    result = domdriver.run_scenario(
        "submitForm",
        {
            "routes": routes["admin"],
            "navigate": "#/scorer",
            "fields": {"#sc-tau_s": "120"},
            "click": ["Preview effect"],
        },
    )
    assert [entry["path"] for entry in result["sent"]] == ["/api/scorer/preview"], result["sent"]
    assert result["refusalShown"] is False, "an in-bounds value was refused"


@dom_test
async def test_the_sidebar_is_one_tab_stop_and_is_operable_by_keyboard(
    routes: dict[str, Any],
) -> None:
    """The accessibility floor's navigation half (draft §12.6, ADR #180), measured.

    Roving `tabindex`: exactly one item is in the tab order at a time, so an operator tabs **once**
    to reach navigation and **once more** to leave it. Sixteen tab stops would be the failure that
    makes people stop using the keyboard.

    What this does NOT cover: what a screen reader announces. There is no assistive technology in
    this environment and the harness has no accessibility tree, so this is a claim about markup.
    """
    result = domdriver.run_scenario(
        "keyboard",
        {
            "routes": routes["admin"],
            "keys": ["ArrowDown", "ArrowDown", "End", "Home", "Enter"],
        },
    )
    assert result["tabStops"] == 1, (
        f"the sidebar has {result['tabStops']} tab stops across {result['itemCount']} items"
    )
    assert result["itemCount"] > 5, "too few items for this to be measuring anything"
    assert result["navLabel"], "the navigation landmark has no accessible name"
    assert result["headingFocusable"] == "-1", (
        "the work-area heading is not programmatically focusable, so focus cannot be moved to it "
        "on a route change — a keyboard operator would stay in the navigation"
    )
    # Positional, not keyed on the step name: two ArrowDowns are two distinct observations and a
    # dict keyed on "ArrowDown" silently keeps only the second — which is how the first version of
    # this assertion came to expect 1 and measure 2.
    steps = [step for step in result["trace"] if "at" in step]
    assert [step["step"] for step in steps] == ["ArrowDown", "ArrowDown", "End", "Home", "Enter"]
    assert steps[0]["at"] == 1, steps[0]
    assert steps[1]["at"] == 2, steps[1]
    assert steps[2]["at"] == result["itemCount"] - 1, steps[2]
    assert steps[3]["at"] == 0, steps[3]
    # Enter on the first item activates it, so the route follows the keyboard.
    assert steps[4]["activeView"] == "overview", steps[4]


@dom_test
async def test_the_theme_persists_without_localstorage_and_a_hostile_cookie_cannot_widen_it(
    routes: dict[str, Any],
) -> None:
    """ADR #172's cookie, and the closed set that bounds what it can do.

    Three facts: the default stamps **no attribute** (so `prefers-color-scheme` still decides), the
    toggle writes a cookie carrying a theme name **and nothing else**, and a cookie value outside
    the closed set is discarded rather than trusted — so a hostile cookie can at worst select a
    supported theme.
    """
    default = domdriver.run_scenario("theme", {"routes": routes["admin"]})
    assert default["after"]["theme"] is None, (
        "a fresh client stamped an explicit theme, which would override prefers-color-scheme"
    )

    toggled = domdriver.run_scenario("theme", {"routes": routes["admin"], "click": ["Theme:"]})
    assert toggled["after"]["theme"] in {"dark", "light", None}
    assert list(toggled["cookiePairs"]) == ["ncn_theme"], (
        f"the theme control wrote more than a theme name: {toggled['cookiePairs']}"
    )
    assert toggled["after"]["cookie"].startswith("ncn_theme=")

    honoured = domdriver.run_scenario(
        "theme", {"routes": routes["admin"], "cookies": {"ncn_theme": "light"}}
    )
    assert honoured["after"]["theme"] == "light", "a valid stored preference was ignored"

    hostile = domdriver.run_scenario(
        "theme", {"routes": routes["admin"], "cookies": {"ncn_theme": "<script>alert(1)</script>"}}
    )
    assert hostile["after"]["theme"] is None, (
        f"a cookie value outside the closed set reached the document: {hostile['after']}"
    )


@dom_test
async def test_every_click_of_the_theme_control_changes_what_is_on_the_screen(
    routes: dict[str, Any],
) -> None:
    """**F87.** One click, one visible change — and the control has to say which state it is in.

    The control cycled `dark -> light -> system -> dark`: three states through something that can
    only ever show two appearances, so whichever of the two `system` resolved to, one click in
    three changed nothing. Measured in Chromium at `prefers-color-scheme: light`, click 3 left the
    page exactly as click 2 did, and an operator reasonably reports that switching takes two
    clicks.

    The second half is what made it unreadable. `TopBar` read the theme from a cookie, which Preact
    cannot observe, and leaned on a `forceRepaint()` that called
    `store.setConnection(store.get().connection)` — a setter that returns early when the value is
    unchanged. It published nothing. Through six clicks the label read `Theme: system.` while the
    page went dark, light, dark, light: the only thing moving was `data-theme`, written straight to
    the document root, never through the framework.

    So this asserts on the **trail**, not the endpoint. An assertion that only reads the final
    state cannot see either defect, which is why the one above did not.
    """

    # No `matchMedia` in the harness, so "system" resolves the way the stylesheet resolves it when
    # no `prefers-color-scheme` matches: light. Absent attribute therefore means light.
    def appearance(step: dict[str, Any]) -> str:
        return step["theme"] or "light"

    run = domdriver.run_scenario("theme", {"routes": routes["admin"], "click": ["Theme:"] * 4})
    trail = run["trail"]
    assert len(trail) == 5, trail

    seen = [appearance(step) for step in trail]
    dead = [i for i in range(1, len(seen)) if seen[i] == seen[i - 1]]
    assert not dead, (
        f"click(s) {dead} changed nothing on screen — the appearance trail was {seen}. A control "
        f"with more states than appearances always has a dead click somewhere in its ring."
    )

    # The control must name the state it is in, and it must move. Frozen is the F87 half that a
    # colour assertion cannot see: the page can be flipping while the button lies about it.
    labels = [step["control"] for step in trail]
    assert all(labels), f"the theme control has no accessible label somewhere in {labels}"
    assert len(set(labels)) > 1, (
        f"the theme control's label never changed across four clicks: {labels[0]!r}. It is being "
        f"rendered from state the framework cannot observe (F87)."
    )
    for step in trail[1:]:
        assert step["theme"] and step["theme"] in step["control"], (
            f"the label {step['control']!r} does not name the state {step['theme']!r} it is in"
        )


@dom_test
async def test_every_destructive_control_states_its_consequence_before_it_can_be_used(
    routes: dict[str, Any],
) -> None:
    """§IV.1: nothing destructive without a preview, and the consequence stated in words first.

    Driven at the two screens whose controls really destroy: the audit prune and the dataset
    retention tiers. The assertion is that the consequence is on screen **before** any preview is
    requested, and that no request has been issued merely by arriving.
    """
    for view in ("audit", "settings"):
        result = domdriver.run_scenario(
            "submitForm",
            {
                "routes": routes["admin"],
                "navigate": f"#/{view}",
                "fields": {},
                "click": [],
            },
        )
        assert result["consequenceShown"] is True, (
            f"the {view} screen offers a destructive control with no consequence stated"
        )
        assert "cannot be undone" in result["dump"], (
            f"the {view} screen does not say the action cannot be undone"
        )
        deletes = [entry for entry in result["sent"] if entry["method"] in {"POST", "DELETE"}]
        assert deletes == [], f"arriving at {view} issued a mutation: {deletes}"


# --- v0.14.0: the console tells the truth about what is deciding (F60) --------------------------


async def _routes_with_a_promoted_tree(store: Store) -> dict[str, Any]:
    """Capture the admin's route table with a **`tree` model version active**.

    Not a hand-written fixture: the artefact is fitted by `tree.fit_document`, registered through
    `store.insert_model_version`, activated through `store.set_active_model_version`, and loaded by
    `engine.load_scorer_config` — the same four steps a promotion takes. What the console then reads
    from `GET /api/scorer` is what an operator with a promoted tree reads.
    """
    import modelfixtures
    from netcorenoc.engine.correlate import scoring
    from netcorenoc.engine.model import model_version, tree

    engine, app = await uifixtures.corpus(store)
    document = model_version.canonical_object(
        await tree.fit_document(
            modelfixtures.training_rows(),
            max_depth=3,
            min_samples_leaf=20,
            criterion="gini",
            threshold=0.5,
        )
    )
    async with store.lock:
        version = await store.insert_model_version(
            kind=model_version.KIND_TREE,
            contract_version=scoring.CONTRACT_VERSION,
            params_document=document,
            params_hash=model_version.params_hash(
                model_version.KIND_TREE, scoring.CONTRACT_VERSION, document
            ),
            challenger_run_id=None,
            created_by="admin",
            created_at=1_700_000_000.0,
            note="v0.14.0 console fixture",
        )
        await store.set_active_model_version(version, "admin", 1_700_000_000.0)
        await store.commit()
    await engine.load_scorer_config()
    assert engine.scorer_model_version_id == version, "the tree did not become the champion"
    return await uifixtures.capture(app, "admin")


@dom_test
async def test_the_console_says_a_tree_is_deciding_and_not_five_additive_weights(
    store: Store,
) -> None:
    """**F60.** With a model version active there is no scorer configuration, and the route used to
    answer with the **coded additive defaults** — which this screen rendered under the heading
    *"Active configuration"*.

    An operator whose champion was a promoted tree would have read five weights that decided
    nothing, with nothing on the screen to say so. It is a display defect and it predates the tree
    kinds: a promoted `logistic` champion produced it too. It took a release about the model family
    to be noticed, which is the argument for driving a console rather than reading it.

    Driven, not read: the artefact is fitted, registered, activated and loaded exactly as a
    promotion does it, and the assertions are against the rendered document.
    """
    captured = await _routes_with_a_promoted_tree(store)
    result = domdriver.run_scenario("render", {"routes": captured, "navigate": "#/scorer"})
    dump = result["dump"]

    assert "tree" in dump, "the screen never names the kind that is running"
    assert "model version" in dump.lower(), "the screen never names the artefact behind the model"
    assert "not what is deciding" in dump.lower(), (
        "the screen shows five additive weights and does not say they decide nothing while a "
        "model version is active -- which is exactly F60"
    )
    # The five weights are still rendered, and that is right: an admin may retune the stored
    # configuration and roll the model version back. What must not happen is presenting them as
    # what is running.
    assert "Configured parameters" in dump, "the tunable table vanished instead of being labelled"
    assert "Active configuration" not in dump, (
        "the heading that made the defect a lie is still on the screen"
    )


@dom_test
async def test_the_control_an_additive_champion_is_not_warned_about(
    routes: dict[str, Any],
) -> None:
    """**The control.** Without it the assertion above could be satisfied by a console that warns
    unconditionally, which would be a different defect wearing the same words.

    The default fixture runs the additive scorer, so the warning must be **absent** and the section
    must still be there.
    """
    result = domdriver.run_scenario("render", {"routes": routes["admin"], "navigate": "#/scorer"})
    dump = result["dump"]
    assert "Configured parameters" in dump, "the tunable table is missing on an additive champion"
    assert "not what is deciding" not in dump.lower(), (
        "an additive champion was warned that its own parameters decide nothing"
    )
    assert "What is deciding" in dump, (
        "the running-scorer banner is shown only in the unusual case, which teaches an operator "
        "that the usual case needs no checking"
    )


async def _routes_with_an_applied_promotion(store: Store) -> dict[str, Any]:
    """Capture the admin's table with a **`BETTER` / applied** decision on record.

    The `promotion` row is written directly, and that is deliberate rather than a shortcut: this
    test is about **rendering a decision**, and the row is the contract between the gate and the
    screen. Forcing the real gate to return `BETTER` needs the floors, the power calculation and
    the seal all moved, which `tests/test_promotion_api.py` does — there, where the subject is the
    gate. Doing it again here would be testing the gate twice and the screen not at all.

    The metrics document is `promotion.Metrics.as_document()`'s own shape, built from real
    `Interval`s, so a change to that shape breaks this fixture rather than leaving it green against
    a format nothing produces.
    """
    from netcorenoc.engine.evaluation import promotion as promotion_module
    from netcorenoc.engine.evaluation.shadow_cv import Interval

    _engine, app = await uifixtures.corpus(store)
    metrics = promotion_module.Metrics(
        quantities=tuple(
            promotion_module.Quantity(
                name,
                Interval(0.12, 0.09, 0.15, 42),
                Interval(0.31, 0.27, 0.35, 42),
            )
            for name in promotion_module.QUANTITY_NAMES
        )
    )
    async with store.lock:
        version = await store.insert_model_version(
            kind="tree",
            contract_version="1.0",
            params_document='{"nodes":[[-1,0.0,-1,-1,0.5]],"base_value":0.25,"threshold":0.5,'
            '"criterion":"gini","max_depth":3,"min_samples_leaf":20}',
            params_hash="a" * 64,
            challenger_run_id=None,
            created_by="admin",
            created_at=1_700_000_000.0,
        )
        await store.insert_promotion(
            model_version_id=version,
            verdict="BETTER",
            triggers="[]",
            metrics=metrics.as_document(),
            evaluation_run_id="e" * 32,
            plan_sha256=promotion_module.RATIFIED_PLAN_SHA256,
            query_count=1,
            approved_by="adm",
            decided_at=1_700_000_100.0,
            outcome="applied",
            refusal_reason=None,
            unavailable="[]",
        )
        await store.commit()
    # The engine is not reloaded: this fixture is about a decision **on record**, not about a
    # pointer that moved. `GET /api/promotion` reads the table, and the table is what the screen
    # renders.
    return await uifixtures.capture(app, "admin")


@dom_test
async def test_a_decided_promotion_shows_both_arms_of_all_four_quantities(
    store: Store,
) -> None:
    """**The non-`INSUFFICIENT_EVIDENCE` branch, rendered.**

    v0.13.0's screen showed a decision as a table row: verdict, outcome, reason, triggers. That is
    complete for `INSUFFICIENT_EVIDENCE` — the only verdict this project had ever produced — and
    thin for the other two, because a `BETTER` is a **comparison** and a comparison shown without
    the numbers it compared is an assertion.

    `PREREGISTRATION-0.11.0.md` §2 item 4: both arms come from one code path, because a challenger
    number with no champion number beside it is not a comparison. This asserts the screen keeps
    that property — all four quantities, both arms — and that the one branch with a consequence
    says what the consequence was.
    """
    captured = await _routes_with_an_applied_promotion(store)
    result = domdriver.run_scenario("render", {"routes": captured, "navigate": "#/promotion"})
    dump = result["dump"]

    for label in (
        "over-merge rate",
        "under-merge rate",
        "split-bag intact rate",
        "asserted-negative respected rate",
    ):
        assert label in dump, f"the decision does not show {label!r}"
    assert "challenger" in dump and "champion" in dump, (
        "only one arm is rendered; a challenger number with no champion beside it is not a "
        "comparison (PREREGISTRATION-0.11.0.md §2 item 4)"
    )
    assert "BETTER" in dump, "the verdict is not shown"
    assert "The champion changed" in dump, (
        "the one branch with a consequence does not say what the consequence was"
    )
    # The interval, not just the point estimate: a rate with no interval beside it invites being
    # read as exact, and every one of these is a cluster bootstrap over incidents.
    assert "0.0900" in dump and "0.1500" in dump, "the challenger's interval bounds are missing"


# --- the rule draft §1.1 sets on a rewrite -------------------------------------------------------

#: `assert` statements in this file at v0.12.0, **counted from the shipped tree** rather than
#: remembered: `git show v0.12.0:tests/test_ui_invariants.py | python -c "…ast.walk…"` returns 33.
#: `UI-0.13-DRAFT.md` §1.1: *"when a selector changes, the assertion count may not go down"*.
V0_12_0_ASSERTION_COUNT = 33


def test_the_assertion_count_did_not_go_down() -> None:
    """Draft §1.1's rule, measured rather than promised.

    Every selector this file reaches for changed in v0.13.0 — `#sits`, `#tabs`, `#login`, `#app`,
    `#fltStatus`, `#fltText` and `.panel[data-panel=…]` are all gone. **A guard rewritten during
    the change it guards is at its least trustworthy**, and the cheapest check available is that
    the rewrite did not quietly drop assertions along the way.

    It is a floor, not a target: the count is expected to rise, and it did — this release adds the
    F53 repair, the unknown-address case, the hardening refusal and its control, the keyboard
    floor, the theme cookie and the destructive-preview rule.

    What this does NOT prove: that the assertions are *good*. A file of `assert True` would pass
    it. It is one cheap check beside the demonstrations in
    `../docs/gates/v0.13.0-guard-demonstrations.md`, not a substitute for them.
    """
    import ast
    from pathlib import Path

    source = Path(__file__).read_text(encoding="utf-8")
    count = sum(isinstance(node, ast.Assert) for node in ast.walk(ast.parse(source)))
    assert count >= V0_12_0_ASSERTION_COUNT, (
        f"this file now makes {count} assertions, down from {V0_12_0_ASSERTION_COUNT} at v0.12.0. "
        f"A selector rename may rewrite an assertion; it may not delete one."
    )


# --- v0.15.2: the appliance measures itself and the console shows it (F68, DECISIONS #222) -------


@dom_test
async def test_the_health_tiles_render_what_api_stats_already_served(
    routes: dict[str, Any],
) -> None:
    """`queue_depth` and the five receiver counters, on the screen an operator opens first.

    Every one of these was served on every poll and rendered nowhere, which is the whole finding:
    `receiver.denied` is the only evidence an operator has that their allowlist is refusing their
    own equipment, and it had never been on a screen.
    """
    result = domdriver.run_scenario(
        "health",
        {
            "routes": routes["admin"],
            "samples": [
                {
                    "devices": 2,
                    "classes": 3,
                    "active_alarms": 4,
                    "open_situations": 1,
                    "quarantined": 0,
                    "ingest_gaps": [],
                    "open_ingest_gaps": [],
                    "latency_p95_s": 0.01,
                    "queue_depth": 7,
                    "warnings": [],
                    "receiver": {
                        "received": 100,
                        "accepted": 90,
                        "denied": 6,
                        "quarantined": 3,
                        "dropped": 1,
                    },
                }
            ],
        },
    )
    tiles = result["samples"][0]["tiles"]
    assert tiles["queue depth"]["value"] == "7", tiles
    assert tiles["received"]["value"] == "100", tiles
    assert tiles["accepted"]["value"] == "90", tiles
    assert tiles["denied"]["value"] == "6", tiles
    assert tiles["quarantined"]["value"] == "3", tiles
    assert tiles["dropped"]["value"] == "1", tiles


@dom_test
async def test_the_trap_rate_is_derived_from_two_samples_and_names_its_window(
    routes: dict[str, Any],
) -> None:
    """**A rate with no window is a number nobody can act on**, and a rate from one sample is zero.

    The first update can only say it is waiting; the second carries a figure and the seconds it
    covers. The harness's clock is a double, so the window is whatever it advances — the assertion
    is that a window is *stated* and that the rate is derived from the difference, not that the
    number is any particular one.
    """

    def stats(received: int) -> dict[str, Any]:
        return {
            "devices": 2,
            "classes": 3,
            "active_alarms": 4,
            "open_situations": 1,
            "quarantined": 0,
            "ingest_gaps": [],
            "open_ingest_gaps": [],
            "latency_p95_s": 0.01,
            "queue_depth": 0,
            "warnings": [],
            "receiver": {
                "received": received,
                "accepted": received,
                "denied": 0,
                "quarantined": 0,
                "dropped": 0,
            },
        }

    result = domdriver.run_scenario(
        "health",
        {"routes": routes["admin"], "samples": [stats(100), stats(160), stats(20)]},
    )
    first, second, third = result["samples"]

    # One sample is not a rate, and the tile says so rather than showing a zero.
    assert first["rate"] is None, first
    assert first["tiles"]["trap rate"]["value"] == "—", first["tiles"]
    assert "waiting" in first["tiles"]["trap rate"]["note"], first["tiles"]

    # Two samples are. The window is stated on screen beside the figure.
    assert second["rate"] is not None, second
    assert second["rate"]["windowS"] > 0, second["rate"]
    expected = 60 / second["rate"]["windowS"]
    assert abs(second["rate"]["perSecond"] - expected) < 1e-9, second["rate"]
    assert "over the last" in second["tiles"]["trap rate"]["note"], second["tiles"]
    assert second["tiles"]["trap rate"]["value"].endswith("/s"), second["tiles"]

    # A counter that went BACKWARDS is an appliance that restarted, not a negative rate.
    assert third["rate"] is None, third
    assert third["tiles"]["trap rate"]["value"] == "—", third["tiles"]


# --- v0.15.3: "why these were grouped" answers the storm question (V.6, DECISIONS #245) ---------

#: The cap the old implementation applied: `links.slice(0, 30)`.
OLD_LINK_CAP = 30


def _routes_with_links_past_the_old_cap(
    table: dict[str, Any], sid: int
) -> tuple[dict[str, Any], int]:
    """The captured detail for `sid`, with its link list grown past `OLD_LINK_CAP`.

    **Why this exists, and it is the whole point of the test below.** The fiber-cut corpus produces
    a situation with **16** links, and the first version of the completeness assertion drove that:
    it passed, and it passed *with the thirty-row cap deliberately reinstated*, because 16 < 30
    made `slice(0, 30)` a no-op. A guard that cannot fail for the defect it names is F53's shape
    and F62's — a property holding by accident of the fixture — and it had to be found by injecting
    the defect rather than by reading the test.

    Only the **count** is synthetic. Every link is a copy of a real link from the real route, with
    the real term shape, so what is under test is exactly the quantity the cap acted on. Growing
    the corpus instead would move `make eval`'s frozen hash, which is a trap-path change in a
    console release.
    """
    import copy

    padded = dict(table)
    detail = copy.deepcopy(table[f"/api/situations/{sid}"]["json"])
    real = detail["links"]
    assert real, "the captured situation has no links at all"
    while len(detail["links"]) <= OLD_LINK_CAP:
        detail["links"] = detail["links"] + copy.deepcopy(real)
    padded[f"/api/situations/{sid}"] = {"status": 200, "json": detail}
    return padded, len(detail["links"])


@dom_test
async def test_the_grouping_summary_is_computed_from_every_link_not_from_thirty(
    routes: dict[str, Any],
) -> None:
    """**The redesign's first half.** The summary must aggregate the whole link set.

    The old section rendered `links.slice(0, 30)` and nothing else, so an operator in a storm read
    thirty rows chosen by insertion order and drew a conclusion from a sample nobody selected. What
    they actually need first is *"is this grouping sound?"*, and that is a property of every link:
    the weakest one, its margin over the threshold, and which of the three named terms is carrying
    the grouping.

    What this does NOT cover: whether the arithmetic is the right arithmetic. It is min, max and a
    mean per term, and it is checked by reading `views/parts/why.js` — a test that recomputed it
    here would be a second implementation of the thing under test.
    """
    sid, count = uifixtures.largest_situation(routes["editor"])
    assert count >= 4, "the corpus must offer a situation with several links"
    result = domdriver.run_scenario("whyGrouped", {"routes": routes["editor"], "sid": sid})

    closed = result["closed"]
    assert closed["summaryText"], "no summary rendered at all"
    for label in ("weakest link", "strongest link", "above the threshold"):
        assert label in closed["summaryText"], (
            f"the summary does not report {label!r}: {closed['summaryText']!r}"
        )
    # All three named terms are reported as means, so "which term is carrying this" is answerable
    # without opening anything.
    assert len(closed["means"]) == 3, closed["means"]
    assert closed["band"] in {
        "soundness-thin",
        "soundness-fair",
        "soundness-wide",
        "soundness-unknown",
    }, closed["band"]


@dom_test
async def test_the_per_term_decomposition_is_one_interaction_away_and_complete(
    routes: dict[str, Any],
) -> None:
    """**Principle 2, restated against the redesign — and the assertion that matters.**

    > The operator must be able to answer *"why did the system group these alarms?"* without
    > leaving the screen.

    Two properties, and the second is the one a redesign could quietly lose:

      * the decomposition is **behind one interaction** — closed by default, so a storm does not
        open thousands of rows nobody asked for;
      * it is **complete when opened**. The old version capped at thirty and printed a line saying
        how many it had hidden, which means the per-term contributions of link thirty-one onwards
        were unreachable on any device at all. A cap is how this screen stops carrying the
        product's central claim while still looking like it does.

    The fixture is grown past thirty on purpose — see `_routes_with_links_past_the_old_cap`.
    With the corpus's own sixteen this assertion passed **with the cap reinstated**, which is
    the exact failure mode Appendix B calls a property that holds by accident.
    """
    sid, _ = uifixtures.largest_situation(routes["editor"])
    padded, total = _routes_with_links_past_the_old_cap(routes["editor"], sid)
    result = domdriver.run_scenario("whyGrouped", {"routes": padded, "sid": sid})

    assert result["closed"]["rowCount"] == 0, (
        f"{result['closed']['rowCount']} link rows rendered before the operator asked for them"
    )
    assert result["expandedAttr"] == "true", "the toggle does not report its state"
    assert result["opened"]["rowCount"] == total, (
        f"opened the decomposition and got {result['opened']['rowCount']} of {total} links. "
        f"A truncated decomposition is principle 2 stopping being true without saying so."
    )
    # Every row carries all three terms as NUMBERS, not only as bars.
    assert len(result["opened"]["termNumbers"]) == total * 3, (
        f"expected {total * 3} term figures, got {len(result['opened']['termNumbers'])}"
    )
    assert all(any(ch.isdigit() for ch in figure) for figure in result["opened"]["termNumbers"]), (
        result["opened"]["termNumbers"][:6]
    )


# --- v0.16.1: the graph's text half, which IS executed -------------------------------------------


def _with_edges(captured: dict[str, Any]) -> dict[str, Any]:
    """The captured graph payload with one learned edge between two real nodes.

    The corpus's own graph has **no** edges: `MIN_EDGE_N = 5.0` and the fixture's traffic never
    clears it. Asserting the affinity table against that fixture would assert nothing — the same
    accident `_routes_with_links_past_the_old_cap` exists to avoid one screen over — so the edge
    is supplied, with the two numbers the table reads and nothing else.
    """
    graph = captured["/api/graph"]["json"]
    nodes = graph["nodes"]
    assert len(nodes) >= 2, "the fixture must offer two elements to draw an edge between"
    edges = [{"a_id": nodes[0]["id"], "b_id": nodes[1]["id"], "weight": 0.91, "n": 240.0}]
    return {**captured, "/api/graph": {"status": 200, "json": {**graph, "edges": edges}}}


@dom_test
async def test_the_graph_screen_answers_its_two_questions_in_ordinary_dom(
    routes: dict[str, Any],
) -> None:
    """**Part of the graph's coverage gap, closed** (§V.7).

    `tests/domharness/env.mjs` substitutes d3 with a recording double, so nothing about node
    placement, edge rendering, zoom, drag or the simulation is asserted anywhere in this
    repository — and `views/graph.js` has said so at the top since v0.13.0. That does not change
    here and the file still says it.

    What changes is that the screen's two answers are no longer *only* in the drawing. "Which
    elements alarm most" and "which relationships are strongest" are rendered as tables, from
    `active_alarms`, `weight` and `n` — three numbers `/api/graph` has served since v0.13.0 and
    the console encoded as a radius and an opacity and then discarded. **Tables the harness can
    execute**, which is why this test exists at all and why no route was added to produce them.

    What this does NOT cover: the drawing, still. And the numbers' correctness against a live
    appliance — this asserts the screen renders what the payload carries, not that the payload is
    right.
    """
    result = domdriver.run_scenario(
        "render", {"routes": _with_edges(routes["editor"]), "navigate": "#/graph"}
    )
    dump = result["dump"]
    assert "Elements alarming most" in dump, "the busiest-element table is not on the screen"
    assert "Strongest learned relationships" in dump, "the affinity table is not on the screen"
    # `n` beside the weight, because a strong-looking edge over six observations is a weaker claim
    # than the same score over hundreds (F61) and a table that hid it would say they were equal.
    assert "evidence (n)" in dump, "the affinity is shown without the evidence behind it"
    # **v0.16.3: the rename is gone from this screen, and the assertion is inverted rather than
    # deleted.** It lived here because there was nowhere else, and it wrote
    # `label(kind='device', target_id=node.id)` while Entities read the `ne` table — so the name an
    # operator gave a host was invisible on the screen built to describe that host. An element is
    # named from the row in a situation's member table now, with its class and its severity
    # (DECISIONS #281). A screen that offers a control cannot un-offer it silently, so this says so.
    assert "rename" not in dump.lower(), (
        "the graph still offers a rename. The declaration moved to the situation's member row, "
        "where the operator already is and where all three declarations are made together."
    )
    # …and it still links to where the element's situations are, which is where the naming went.
    assert "situations" in dump, "the graph no longer points at where an element is worked on"


@dom_test
async def test_the_graph_tables_are_absent_when_there_is_nothing_to_rank(
    routes: dict[str, Any],
) -> None:
    """**The control.** A table that renders unconditionally would say "nothing alarms most" on a
    quiet appliance, which is a sentence, not a fact.

    Driven with an empty graph payload rather than by hoping the fixture is quiet: the assertion
    above would otherwise be satisfiable by a screen that always prints both headings.
    """
    quiet = {**routes["editor"], "/api/graph": {"status": 200, "json": {"nodes": [], "edges": []}}}
    result = domdriver.run_scenario("render", {"routes": quiet, "navigate": "#/graph"})
    dump = result["dump"]
    assert "Elements alarming most" not in dump
    assert "Strongest learned relationships" not in dump
    assert "No network elements yet" in dump, "the empty state is what should be there instead"


# --- v0.16.3: the three declarations, and the one interruption they may cause -------------------


def _declaring(
    routes: dict[str, Any], sid: int, *, learned: tuple[str, int] | None
) -> dict[str, Any]:
    """The captured editor payload with a learned severity on every member, and none declared.

    A real corpus resolves no severity at all — 0 of 2 252 alarms — which is a fact about the
    corpus and about the floors `severity.py` refuses below. It is not a reason to leave the
    interruption rule undriven by any test.
    """
    import copy

    doctored = copy.deepcopy(routes)
    for alarm in doctored[f"/api/situations/{sid}"]["json"]["alarms"]:
        alarm["severity"], alarm["severity_rank"] = learned or (None, None)
        alarm["severity_ranks"] = [] if learned is None else [learned[1]]
        alarm["declared_severity"] = alarm["declared_severity_rank"] = None
    return doctored


@dom_test
async def test_each_declaration_sends_exactly_the_kind_and_target_it_names(
    routes: dict[str, Any],
) -> None:
    """**One mechanism, three times**, driven end to end from the row the operator is looking at.

    Every declaration goes through `POST /api/labels`, and each names the id of the thing it is
    about: the alarm's NE, the alarm's class, the alarm's class again for the severity. A control
    that sent the wrong id would still be a 200, which is why the id is asserted against the
    payload the row actually rendered from rather than against a constant.
    """
    sid, _ = uifixtures.largest_situation(routes["editor"])
    alarm = routes["editor"][f"/api/situations/{sid}"]["json"]["alarms"][0]

    named = domdriver.run_scenario(
        "declare",
        {"routes": routes["editor"], "sid": sid, "control": "ne", "row": 0, "value": "CORE-SW-01"},
    )
    assert named["posts"] == [{"kind": "ne", "id": alarm["ne_id"], "label": "CORE-SW-01"}]
    assert not named["warned"], "naming an element raised a severity disagreement"

    klass = domdriver.run_scenario(
        "declare",
        {"routes": routes["editor"], "sid": sid, "control": "class", "row": 0, "value": "LOS"},
    )
    assert klass["posts"] == [{"kind": "class", "id": alarm["class_id"], "label": "LOS"}]

    sev = domdriver.run_scenario(
        "declare",
        {
            "routes": routes["editor"],
            "sid": sid,
            "control": "severity",
            "row": 0,
            "value": "critical",
        },
    )
    assert sev["posts"] == [{"kind": "severity", "id": alarm["class_id"], "label": "critical"}]


@dom_test
async def test_a_viewer_is_offered_no_declaration_control_at_all(routes: dict[str, Any]) -> None:
    """The controls exist only for a principal who holds `label.write`.

    The same shape as the member checkbox: a viewer is not shown a control whose request would be
    refused, so the console never invites an action it knows will fail.
    """
    sid, _ = uifixtures.largest_situation(routes["viewer"])
    with pytest.raises(domdriver.HarnessError, match="no declaration control"):
        domdriver.run_scenario(
            "declare",
            {"routes": routes["viewer"], "sid": sid, "control": "ne", "row": 0, "value": "x"},
        )


@dom_test
async def test_the_disagreement_prompt_fires_on_two_steps_and_not_on_one(
    routes: dict[str, Any],
) -> None:
    """**§I.4's rule, with the control that makes it a rule rather than a dialog** (#285).

    The prompt appears only when the appliance's severity is **confirmed** — writing
    `alarm.severity` at all means it passed both of `severity.py`'s gates — and the declared rank
    is two or more steps away on the 0-4 vocabulary scale.

    Three arms, and the middle one is the whole point: *the same control, the same click, a
    one-step difference, and no interruption*. A prompt that fired on every declaration would be
    dismissed unread, and then it would be worth nothing on the one occasion it matters.
    """
    sid, _ = uifixtures.largest_situation(routes["editor"])
    learned_minor = _declaring(routes["editor"], sid, learned=("minor", 2))

    two_steps = domdriver.run_scenario(
        "declare",
        {
            "routes": learned_minor,
            "sid": sid,
            "control": "severity",
            "row": 0,
            "value": "critical",
            "anyway": True,
        },
    )
    assert two_steps["warned"], "a 2-step disagreement raised no confirmation"
    assert "minor" in two_steps["warnText"], two_steps["warnText"]

    one_step = domdriver.run_scenario(
        "declare",
        {"routes": learned_minor, "sid": sid, "control": "severity", "row": 0, "value": "major"},
    )
    assert not one_step["warned"], "a 1-step disagreement interrupted the operator"
    assert one_step["posts"] == [
        {"kind": "severity", "id": one_step["posts"][0]["id"], "label": "major"}
    ]

    # Nothing learned at all: the appliance holds no opinion, so it can contradict none.
    silent = domdriver.run_scenario(
        "declare",
        {
            "routes": _declaring(routes["editor"], sid, learned=None),
            "sid": sid,
            "control": "severity",
            "row": 0,
            "value": "critical",
        },
    )
    assert not silent["warned"], "an unlearned severity was treated as a contradiction"
    assert len(silent["posts"]) == 1


@dom_test
async def test_cancelling_the_disagreement_writes_nothing_and_confirming_writes_once(
    routes: dict[str, Any],
) -> None:
    """**A cancel does not write** (#285), and its control is the confirm beside it.

    The brief reads a declined disagreement as *"kept, with the disagreement recorded"*. A
    confirmation that saved regardless is not a confirmation — it is a notification wearing a
    dialog's clothes, and the second one an operator meets is dismissed unread. What is recorded
    is recorded server-side, on the declarations that land.
    """
    sid, _ = uifixtures.largest_situation(routes["editor"])
    learned_minor = _declaring(routes["editor"], sid, learned=("minor", 2))
    params = {
        "routes": learned_minor,
        "sid": sid,
        "control": "severity",
        "row": 0,
        "value": "critical",
    }

    cancelled = domdriver.run_scenario("declare", {**params, "anyway": False})
    assert cancelled["warned"]
    assert cancelled["posts"] == [], f"a cancelled declaration was written: {cancelled['posts']}"

    confirmed = domdriver.run_scenario("declare", {**params, "anyway": True})
    assert len(confirmed["posts"]) == 1, confirmed["posts"]
    assert confirmed["posts"][0]["label"] == "critical"


@dom_test
async def test_an_integer_learned_rank_never_raises_the_disagreement_prompt(
    routes: dict[str, Any],
) -> None:
    """F99's scale is not the declaration's scale, so it holds no comparable opinion (#285).

    A vendor numbering severity 10, 20, 30 would otherwise be "10 steps" from every declaration an
    operator could make, and the prompt would fire on all of them — which is precisely the failure
    mode §I.4 exists to avoid. The control is the vocabulary arm in the test above, where the same
    two-step comparison does interrupt.
    """
    sid, _ = uifixtures.largest_situation(routes["editor"])
    result = domdriver.run_scenario(
        "declare",
        {
            "routes": _declaring(routes["editor"], sid, learned=("30", 30)),
            "sid": sid,
            "control": "severity",
            "row": 0,
            "value": "critical",
        },
    )
    assert not result["warned"], "an out-of-scale learned rank was compared against a token"
    assert len(result["posts"]) == 1


@dom_test
async def test_a_declaration_can_be_withdrawn_from_the_row_that_made_it(
    routes: dict[str, Any],
) -> None:
    """**A declaration that cannot be undone is a declaration nobody will make** (#284).

    The revert is driven rather than described: the control offers `Clear` only when a declaration
    is in force, and it sends a DELETE naming the same kind and target the POST named. The control
    is the opener's own label — `Edit` when something is declared, `Declare` when nothing is — so
    a screen that offered `Clear` unconditionally would be visible here.
    """
    import copy

    sid, _ = uifixtures.largest_situation(routes["editor"])
    doctored = copy.deepcopy(routes["editor"])
    alarms = doctored[f"/api/situations/{sid}"]["json"]["alarms"]
    for alarm in alarms:
        alarm["device_label"] = "CORE-SW-01"
    ne_id = alarms[0]["ne_id"]

    result = domdriver.run_scenario(
        "withdraw", {"routes": doctored, "sid": sid, "control": "ne", "row": 0}
    )
    assert result["openerLabel"] == "Edit", result["openerLabel"]
    assert result["deletePaths"] == [f"/api/labels/ne/{ne_id}"], result["deletePaths"]
    assert result["posts"] == [], "withdrawing a declaration also wrote one"

    plain = domdriver.run_scenario(
        "declare",
        {"routes": routes["editor"], "sid": sid, "control": "ne", "row": 0, "value": "x"},
    )
    assert plain["openerLabel"] == "Declare", plain["openerLabel"]
    assert plain["deletePaths"] == []


@dom_test
async def test_every_declaration_control_clears_the_tap_floor_at_all_three_widths(
    routes: dict[str, Any],
) -> None:
    """**F103's lesson, applied to what this release added.**

    The tap floor's selector was `button, select, input:not([type="checkbox"]):not([type="radio"])`
    and the one control an operator ticks most was excluded from it, at 13x13 px. So the question
    for four new controls is not *"is there a floor"* but *"does the floor's selector cover what I
    added"* — which is checked against the stylesheet's own rule rather than against a number
    copied out of it.

    Each declaration control is a `button`, a `select` or an `input[type=text]`, and every one of
    those three is inside that selector. A control introduced as a `div` with a click handler, or
    as a checkbox, would fail here.

    **v0.16.4: the reading of that rule moved into `tap_floor_tags`, and it changed answer.** This
    test passed in v0.16.2 and v0.16.3 while the checkbox measured 13x13, because the normaliser
    flattened away the very `:not([type="checkbox"])` that was the defect. The floor no longer
    carries an exclusion and the reader no longer tolerates one; see that function.
    """
    import netcorenoc

    ui = Path(netcorenoc.__file__).resolve().parent / "ui"
    covered = tap_floor_tags(ui / "style.css")
    assert {"button", "select", "input"} <= covered, (
        f"the tap floor covers {sorted(covered)}; the declaration controls are buttons, a select "
        f"and text inputs, and a control the floor's selector excludes is exactly F103"
    )

    # Every element in `declare.js` that carries a handler — which is what "interactive" means at
    # the DOM, and what a `<div onClick>` would fail. Derived from the source rather than listed,
    # so a control added later is measured rather than assumed.
    source = (ui / "app" / "views" / "parts" / "declare.js").read_text(encoding="utf-8")
    #
    # `onSubmit` is deliberately not in the set: a form is a container, and the event is raised by
    # a control inside it that this same scan already covers. Every other handler here is one a
    # finger lands on directly, which is what a touch-target floor is about.
    interactive = {
        tag
        for tag, body in re.findall(r"<(\w+)((?:[^<>]|\$\{[^}]*\})*?)>", source, re.S)
        if re.search(r"\bon(Click|Change|Input|KeyDown)=", body)
    }
    assert interactive, "no interactive control was found in declare.js; the scan matched nothing"
    assert interactive <= covered, (
        f"declare.js makes {sorted(interactive - covered)} interactive, and the tap floor's "
        f"selector covers {sorted(covered)}. A control outside it is below the touch target at "
        f"every width, which is exactly F103."
    )
    assert 'type="checkbox"' not in source, (
        "a declaration control is a checkbox, which is the one shape the tap floor excludes (F103)"
    )


def _flex_containers_of(css: str) -> set[str]:
    """Every selector this stylesheet gives a flex or grid formatting context."""
    out: set[str] = set()
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if not re.search(r"display:\s*(inline-)?(flex|grid)\b", body):
            continue
        for selector in selectors.split(","):
            cleaned = selector.strip().split("/*")[0].strip()
            if cleaned:
                out.add(re.sub(r"\s+", " ", cleaned))
    return out


def _ancestor_chain_of(source: str, needle: str) -> list[list[str]]:
    """For each element carrying `needle` in its class list, the open-element stack above it.

    A small stack scanner over the `htm` templates rather than a regex per call site: the question
    *"what is this element's parent"* is a nesting question and a regex cannot answer it. Each
    entry is a list of `tag.class.class` strings from the outermost open element inwards, ending
    with the element's **direct parent**.
    """
    stack: list[str] = []
    found: list[list[str]] = []
    void = {"br", "hr", "img", "input", "meta", "link", "source"}
    for match in re.finditer(r"<(/?)(\w+)((?:[^<>]|\$\{[^{}]*\})*?)(/?)>", source, re.S):
        closing, tag, body, selfclose = match.groups()
        classes = re.search(r'\bclass="([^"$]*)"', body)
        names = classes.group(1).split() if classes else []
        if closing:
            if stack:
                stack.pop()
            continue
        if needle in names:
            found.append(list(stack))
        if selfclose or tag.lower() in void:
            continue
        stack.append(tag + "".join(f".{name}" for name in names))
    return found


def test_every_age_badge_sits_in_a_flex_context_or_its_auto_margin_does_nothing() -> None:
    """**Bug 2's layout half, derived from the templates rather than from a list.**

    `.age` right-aligns with `margin-left: auto`, which is inert outside a flex or grid formatting
    context. `style.css` had **no `.history-list` rule at all**, so the gesture history's `<li>` was
    `display: list-item`, the margin did nothing, and `by admin` abutted `2m` — read by a
    maintainer as a counter incrementing from `admin2` to `admin3`. Measured in Chromium at 390 /
    820 / 1440 px, with 192 / 622 / 1002 px of empty row to the age's right; the control was the
    same `.age` on `.sit-head`, whose parent IS flex, correct at 12 px from the edge at every width.

    So the rule was never wrong — it was placed in a context that did not exist. This walks every
    template that renders `class="age"`, takes each one's ancestor stack, and requires the
    stylesheet to give **some** ancestor a flex or grid context. A third `.age` added tomorrow
    inside a plain `<div>` fails here rather than in a browser nine releases later.

    What this does NOT cover: whether the age ends up on the right. That is layout, this suite has
    no layout engine, and the browser measurement above is the evidence for it.
    """
    import netcorenoc

    ui = Path(netcorenoc.__file__).resolve().parent / "ui"
    css = (ui / "style.css").read_text(encoding="utf-8")
    assert re.search(r"\.age\s*\{[^}]*margin-left:\s*auto", css), (
        "`.age` no longer right-aligns with an auto margin; this guard is about that rule"
    )
    flex = _flex_containers_of(css)
    sites = 0
    for js in sorted(ui.rglob("*.js")):
        for chain in _ancestor_chain_of(js.read_text(encoding="utf-8"), "age"):
            sites += 1
            # Every selector an ancestor could match: its own classes, its tag, and the
            # descendant form a stylesheet actually writes for an unclassed child (`.list li`).
            candidates: set[str] = set()
            for depth, node in enumerate(chain):
                tag, *classes = node.split(".")
                candidates.update(f".{name}" for name in classes)
                for outer in chain[:depth]:
                    for name in outer.split(".")[1:]:
                        candidates.add(f".{name} {tag}")
            assert candidates & flex, (
                f"{js.name} renders an age badge inside {chain}, and this stylesheet gives none "
                f"of {sorted(candidates)} a flex or grid context — so `margin-left: auto` does "
                f"nothing there and the age abuts the text before it. That is Bug 2."
            )
    assert sites >= 2, f"only {sites} `.age` call sites were found; the scanner matched too little"


def _with_history(captured: dict[str, Any], sid: int) -> dict[str, Any]:
    """The captured detail payload with a gesture history on it.

    A fresh corpus replay makes no gestures, so `events` is empty and the history panel is
    unreachable on real data — the same reason `severityBands` doctors the severities it needs.
    The shape is `store/situation_events.py::situation_events`'s five columns and nothing else,
    because a scoped reader is served five and a sixth here would be testing a payload the server
    does not send.
    """
    detail = captured[f"/api/situations/{sid}"]["json"]
    events = [
        {
            "kind": "rename",
            "at": 1_700_000_100.0,
            "actor": "user:2",
            "actor_name": "admin",
            "confidence": None,
        },
        {
            "kind": "operator_split",
            "at": 1_700_000_200.0,
            "actor": "user:2",
            "actor_name": "admin",
            "confidence": 0.8,
        },
    ]
    return {
        **captured,
        f"/api/situations/{sid}": {"status": 200, "json": {**detail, "events": events}},
    }


@dom_test
async def test_the_gesture_history_separates_the_actor_from_the_age_as_text(
    routes: dict[str, Any],
) -> None:
    """**Bug 2's text half, and the half a stylesheet cannot fix.**

    The row renders `" by "`, the actor, then the age. With no separator between the two runs,
    `admin` followed by `2m` is the single string `admin2m` — which is what a maintainer read as
    `admin2` becoming `admin3`, and what a screen reader announces, and what a copy-paste yields.

    Giving the row a flex context repairs where the age is **drawn** and changes none of that: a
    gap is not a character. So the template carries an explicit space, and this is the guard for
    it — `textContent`, deliberately not whitespace-collapsed, because collapsing is precisely the
    operation that erases the difference.

    Appendix B names this blind spot outright: *the DOM harness cannot see whitespace.* It can see
    whether whitespace is **there**, which is a different question and the one that matters here.
    """
    sid, _count = uifixtures.largest_situation(routes["editor"])
    result = domdriver.run_scenario(
        "history", {"routes": _with_history(routes["editor"], sid), "sid": sid}
    )
    assert result["present"], "the history panel did not render for a situation carrying events"
    assert result["lines"], "the history rendered no rows"
    for line, age in zip(result["lines"], result["ages"], strict=True):
        assert age, "a history row rendered no age at all"
        assert not line.endswith(f"admin{age}"), (
            f"the actor and the age are one word: {line!r}. That is Bug 2 — `by admin` + `2m` "
            f"reads as `admin2m`, and a minute later as `admin3m`."
        )
        # Derived rather than asserted against a literal: whatever precedes the age must end in
        # whitespace, so the two runs are separate words however the row is composed.
        before = line[: line.rindex(age)]
        assert before != before.rstrip(), (
            f"nothing separates {before!r} from the age {age!r} in {line!r}"
        )


@dom_test
async def test_a_permalink_followed_from_inside_situations_opens_the_card_it_names(
    routes: dict[str, Any],
) -> None:
    """**F108.** The permalink is the sharing mechanism — `card.js` calls it *"a link to this
    situation alone, shareable during the incident"* — and the case that failed is the one that
    happens during an incident: an operator already on Situations pastes a colleague's link.

    A hash change inside one view does not remount the component, so `componentDidMount`'s deep
    link was read once and never again. Measured in a browser: the address bar read
    `#/situations/41` while the card for 38 was the one still open, with no error and no empty
    state to say so.

    Driven here as three steps, and the **first two are the control**: a first permalink from
    outside the screen, then a second from inside it. If only the second worked the guard would be
    asserting a coincidence; if only the first worked, that is the defect.
    """
    listing = routes["editor"]["/api/situations?limit=50"]["json"]
    assert len(listing) >= 2, "the corpus must offer two situations for a permalink to move between"
    first, second = listing[0]["id"], listing[1]["id"]

    result = domdriver.run_scenario(
        "permalink",
        {
            "routes": routes["editor"],
            "fragments": [f"#/situations/{first}", f"#/situations/{second}", "#/overview"],
        },
    )
    steps = result["steps"]
    assert steps[0]["expanded"] == [str(first)], (
        f"a permalink reached from outside the screen did not open #{first}: {steps[0]}"
    )
    assert str(second) in steps[1]["expanded"], (
        f"the address moved to {steps[1]['hash']} and the card for #{second} did not open — the "
        f"cards expanded are {steps[1]['expanded']}. That is F108."
    )
    assert steps[2]["expanded"] == [], "leaving the screen did not take the cards with it"


def _in_state(
    captured: dict[str, Any], sid: int, *, status: str, events: list[Any]
) -> dict[str, Any]:
    """The captured detail payload put into one of the four states DECISIONS #291 names.

    A corpus replay makes no gestures and closes nothing, so three of the four are unreachable on
    real data — the same reason `severityBands` doctors severities and `_with_history` doctors
    events. Only the two fields the decision turns on are moved; everything else is the server's.
    """
    detail = captured[f"/api/situations/{sid}"]["json"]
    listing = [
        {**row, "status": status} if row["id"] == sid else row
        for row in captured["/api/situations?limit=50"]["json"]
    ]
    return {
        **captured,
        "/api/situations?limit=50": {"status": 200, "json": listing},
        f"/api/situations/{sid}": {
            "status": 200,
            "json": {**detail, "status": status, "events": events},
        },
    }


JUDGEMENT = [
    {
        "kind": "verdict",
        "at": 1_700_000_300.0,
        "actor": "user:2",
        "actor_name": "admin",
        "confidence": None,
    }
]
NOT_A_JUDGEMENT = [
    # Every one of these PROMOTES a situation to `open` and none of them says anything about
    # whether the alarms belong together. If the card keyed on the status it would treat this as
    # judged, which is the reading DECISIONS #291 rejects.
    {
        "kind": "rename",
        "at": 1_700_000_100.0,
        "actor": "user:2",
        "actor_name": "admin",
        "confidence": None,
    },
    {
        "kind": "manual_clear",
        "at": 1_700_000_200.0,
        "actor": "user:2",
        "actor_name": "admin",
        "confidence": None,
    },
]


def test_the_console_and_the_store_agree_on_which_gestures_assert() -> None:
    """**The mirror, checked** (DECISIONS #291).

    `format.js::ASSERTING_KINDS` decides whether a card treats a situation as judged, and the
    literal it mirrors lives in `store/situation_events.py`, where the prohibition is enforced.
    Two copies of a set is how one of them comes to be wrong; this reads both files, so the day
    they diverge is the day this fails rather than the day an operator is offered a gesture the
    appliance no longer counts.
    """
    import netcorenoc
    from netcorenoc.store.situation_events import ASSERTING_KINDS

    ui = Path(netcorenoc.__file__).resolve().parent / "ui"
    source = (ui / "app" / "format.js").read_text(encoding="utf-8")
    match = re.search(r"ASSERTING_KINDS = new Set\(\[([^\]]*)\]\)", source, re.S)
    assert match, "format.js no longer mirrors ASSERTING_KINDS in a form this can read"
    mirrored = set(re.findall(r'"([a-z_]+)"', match.group(1)))
    assert mirrored == set(ASSERTING_KINDS), (
        f"the console counts {sorted(mirrored)} as asserting and the store counts "
        f"{sorted(ASSERTING_KINDS)}. One of them is wrong about what a gesture means."
    )


@dom_test
async def test_every_gesture_stays_reachable_in_every_status_the_server_accepts_it_in(
    routes: dict[str, Any],
) -> None:
    """**DECISIONS #291, and the property it must not break: nothing is removed.**

    The maintainer's ask was that a judged situation stop offering Confirm and Split as though
    nothing had happened. The answer is a disclosure, not a deletion — and the difference is what
    this asserts. Four states:

      * `new`, unjudged — the full triage surface, unchanged;
      * `open` promoted or renamed but **not judged** — the same surface, because `open` means an
        operator is working it and says nothing about the grouping (v0.16.2's split);
      * `open` **judged** — the same controls, one click behind `Adjust the grouping`, with what
        was already recorded stated above it;
      * `resolved` — verdicts stay (the server answers 200), restructuring is absent (**409**).

    What this does NOT cover: that the disclosure is discoverable. That is a browser question and
    the live pass is where it is answered.
    """
    sid, _count = uifixtures.largest_situation(routes["editor"])
    editor = routes["editor"]

    # "Start working this" is the promote and belongs to `new` alone; every other control in the
    # `.fb` row is a statement about the GROUPING, and those are what the disclosure folds.
    def judging(state: dict[str, Any]) -> list[str]:
        return [b for b in state["grouping"] if b != "Start working this"]

    fresh = domdriver.run_scenario(
        "actionSurface", {"routes": _in_state(editor, sid, status="new", events=[]), "sid": sid}
    )["before"]
    assert fresh["judged"] is None and fresh["adjust"] is False
    assert "Confirm grouping" in fresh["grouping"], fresh["grouping"]
    assert "Start working this" in fresh["grouping"], "a new situation offers no promote"
    assert fresh["restructure"] and fresh["nameField"] and fresh["selectAll"]
    assert fresh["marks"] > 0 and fresh["declares"] >= 3 * fresh["marks"]

    # `open`, promoted or renamed, nothing judged. Identical to `new` but for the promote button,
    # and THAT is the reading of `open` the decision turns on.
    promoted = domdriver.run_scenario(
        "actionSurface",
        {"routes": _in_state(editor, sid, status="open", events=NOT_A_JUDGEMENT), "sid": sid},
    )["before"]
    assert promoted["judged"] is None, (
        f"a rename and a hand-clear were read as a judgement: {promoted['judged']!r}. Neither "
        f"asserts anything about the grouping, which is why the surface keys on the gesture and "
        f"not on the status."
    )
    assert judging(promoted) == judging(fresh), (promoted["grouping"], fresh["grouping"])
    assert "Start working this" not in promoted["grouping"], "an open situation offers a promote"
    assert promoted["restructure"] is True

    # `open`, judged: folded, and then unfolded to exactly the same controls.
    judged = domdriver.run_scenario(
        "actionSurface",
        {
            "routes": _in_state(editor, sid, status="open", events=JUDGEMENT),
            "sid": sid,
            "adjust": True,
        },
    )
    assert judged["before"]["judged"], "a judged situation does not say what was recorded"
    assert "admin" in judged["before"]["judged"], judged["before"]["judged"]
    assert judged["before"]["adjust"] is True
    assert judged["before"]["grouping"] == [], (
        f"the grouping controls are still open on a judged situation: "
        f"{judged['before']['grouping']}"
    )
    assert judging(judged["after"]) == judging(fresh), (
        f"Adjust does not restore the same controls: {judged['after']['grouping']} vs "
        f"{fresh['grouping']}. Nothing may be REMOVED by this decision, only folded."
    )
    assert judged["after"]["restructure"] is True
    # The marks and the declarations are never folded: they are how a split is composed, and a
    # declaration is not a judgement about the grouping at all.
    for state in (fresh, promoted, judged["before"]):
        assert state["marks"] == fresh["marks"] and state["selectAll"]
        assert state["declares"] == fresh["declares"]
        assert state["nameField"]

    # `resolved`: the verdicts the server accepts, and none of the three it refuses with 409.
    resolved = domdriver.run_scenario(
        "actionSurface",
        {
            "routes": _in_state(editor, sid, status="resolved", events=JUDGEMENT),
            "sid": sid,
            "adjust": True,
        },
    )
    assert resolved["after"]["restructure"] is False, (
        "the restructure block is offered on a resolved situation; the server answers 409 to all "
        "three of its gestures, so the console would be offering what will be refused"
    )
    assert "Confirm grouping" in resolved["after"]["grouping"], resolved["after"]["grouping"]
    assert not any("Close" in b for b in resolved["after"]["grouping"]), (
        f"Close is offered on a situation that has already closed: {resolved['after']['grouping']}"
    )


@dom_test
async def test_the_mark_column_header_marks_and_clears_every_row(routes: dict[str, Any]) -> None:
    """**"A way to clear every row at once"**, and it is invariant 2's contract through a new door.

    Measured: one corpus situation holds **1 051** members. Ticking them one at a time is not a
    gesture anybody completes, so the mark column's header ticks and clears the lot — and the
    thing that must stay true is that the ids the split then sends are **exactly** the membership
    and nothing else, which is what
    `test_a_partial_split_sends_exactly_the_marked_ids_and_no_others` protects for the manual path.
    """
    sid, count = uifixtures.largest_situation(routes["editor"])
    members = uifixtures.member_ids(routes["editor"], sid)
    result = domdriver.run_scenario("markAll", {"routes": routes["editor"], "sid": sid})
    assert result["ticked"] == count, f"{result['ticked']} of {count} rows were marked"
    assert result["feedbackBody"]["excluded_ids"] == members, (
        "select-all sent something other than the membership, in the order the card renders it"
    )
    # CONTROL: the same control the other way. A select-all that cannot be undone leaves an
    # operator who mis-clicked with 1 051 checkboxes to untick.
    assert result["afterUntick"] == 0, f"{result['afterUntick']} rows are still marked after untick"
