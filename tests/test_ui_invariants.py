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
        {"routes": routes["editor"], "sid": sid, "mark": [1, 3], "button": "✓ Confirm"},
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
    from netcorenoc import scoring
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
