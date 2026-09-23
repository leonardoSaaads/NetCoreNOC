"""The maintenance console, **executed** (Part IV, §10).

Three properties that only a driven DOM can assert, and each is one a screenshot would show and a
unit test would not:

* **a viewer sees a redacted row rather than no row** — prime directive 4, and the failure mode
  this whole feature would otherwise create;
* **the form opens one card at a time** — D8, which is a claim about what is in the DOM;
* **the timeline bar draws three bands whose widths are the window's own proportions** — which is
  why it is hand-written SVG rather than d3: the harness has no layout engine, and every band
  carries a `data-band` so its width is a number rather than a picture somebody looks at.
"""

from __future__ import annotations

import time
from typing import Any

import domdriver
import uifixtures
from netcorenoc.store import Store
from test_dom_harness import dom_test

#: A captured upcoming-list payload with one window per status an operator acts on differently.
#: The fixture is doctored for the reason `severityBands` doctors severities: a fresh corpus has
#: no planned work in it, so every status but "none at all" would be unreachable by any test.
_NOW = 1_800_000_000.0


def _window(wid: int, status: str, **over: Any) -> dict[str, Any]:
    row = {
        "id": wid,
        "status": status,
        "starts_at": _NOW + 8100,
        "ends_at": _NOW + 15300,
        "patch_s": 600.0,
        "starts_in_s": 8100.0,
        "ends_in_s": 15300.0,
        "target_count": 2,
        "tz": "America/Sao_Paulo",
        "site_time": "2027-01-15T10:00:00-03:00",
        "site_offset": "UTC-03:00",
        "redacted": False,
        "name": "span splice, south tower",
        "description": "",
        "organization_id": 1,
        "organization_name": "Default organization",
        "all_day": False,
        "ledger_enabled": True,
        "visibility": "editors",
        "owner_ref": "user:2",
        "owner_role": "editor",
        "created_by_agent": False,
        "needs_confirmation": False,
        "confirmed_at": None,
        "confirmed_by": None,
        "created_at": _NOW,
        "updated_at": _NOW,
        "cancelled_at": None,
        "ended_at": None,
    }
    row.update(over)
    return row


def _redacted(wid: int, status: str, **over: Any) -> dict[str, Any]:
    """What the server hands a caller who may not see the details — a different set of keys."""
    row = {
        "id": wid,
        "status": status,
        "starts_at": _NOW + 8100,
        "ends_at": _NOW + 15300,
        "patch_s": 600.0,
        "starts_in_s": 8100.0,
        "ends_in_s": 15300.0,
        "target_count": 2,
        "tz": "America/Sao_Paulo",
        "site_time": "2027-01-15T10:00:00-03:00",
        "site_offset": "UTC-03:00",
        "redacted": True,
    }
    row.update(over)
    return row


def _with_windows(routes: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    doctored = dict(routes)
    doctored["/api/maintenance-windows?limit=5"] = {
        "status": 200,
        "json": {"windows": rows, "total": len(rows), "limit": 5, "offset": 0, "now": _NOW},
    }
    return doctored


@dom_test
async def test_a_viewer_sees_that_planned_work_exists_without_seeing_what_it_is(
    store: Store,
) -> None:
    """**Prime directive 4 at the DOM**, and the injection *"a viewer seeing no marker"*.

    A viewer who cannot see that planned work exists reads a quiet estate as a healthy one. So the
    row is there, it carries its status and its countdown, and it does **not** carry the name.

    Both arms run against the same screen and differ only in whether the payload is redacted, so
    this measures the redaction rather than the rendering: without the control arm, a list that had
    simply stopped rendering names would pass.
    """
    routes = await uifixtures.all_routes(store)

    hidden = domdriver.run_scenario(
        "maintenance",
        {"routes": _with_windows(routes["viewer"], [_redacted(7, "active")])},
    )
    assert len(hidden["rows"]) == 1, "a viewer cannot see that planned work exists at all"
    row = hidden["rows"][0]
    assert row["status"] == "active"
    assert row["redacted"] is True, "the row does not mark itself as withholding its details"
    assert "span splice" not in row["text"], f"an editors-only window leaked its name: {row}"
    assert row["countdown"], "the row renders no time, so it says nothing an operator can act on"

    shown = domdriver.run_scenario(
        "maintenance",
        {"routes": _with_windows(routes["editor"], [_window(7, "active")])},
    )
    assert shown["rows"][0]["redacted"] is False
    assert "span splice" in shown["rows"][0]["text"], (
        "the control arm does not render the name either, so the assertion above proves nothing"
    )


@dom_test
async def test_the_confirm_action_is_on_the_row_an_editor_is_looking_at(store: Store) -> None:
    """D7: *"for Pending confirmation, the confirm action where an editor will look for it."*

    On the row, not two clicks inside it. And **only** on that row: an `active` window has nothing
    to confirm, and offering the control anyway would teach an operator that it means nothing.
    """
    routes = await uifixtures.all_routes(store)
    result = domdriver.run_scenario(
        "maintenance",
        {
            "routes": _with_windows(
                routes["editor"],
                [
                    _window(1, "pending_confirmation", needs_confirmation=True),
                    _window(2, "active"),
                    _window(3, "scheduled"),
                ],
            )
        },
    )
    by_status = {row["status"]: row for row in result["rows"]}
    assert by_status["pending_confirmation"]["confirm"] is True
    assert by_status["active"]["confirm"] is False, "an active window offered a confirm"
    assert by_status["scheduled"]["confirm"] is False
    # "End now" is the mirror: only where there is something to end.
    assert by_status["active"]["endNow"] is True
    assert by_status["scheduled"]["endNow"] is False


@dom_test
async def test_an_agent_created_window_is_marked_on_screen(store: Store) -> None:
    """Part III: *"agent-created MWs are marked in the API **and on screen**."*

    An operator reading a list of planned work is entitled to know which entries a human wrote.
    """
    routes = await uifixtures.all_routes(store)
    result = domdriver.run_scenario(
        "maintenance",
        {
            "routes": _with_windows(
                routes["editor"],
                [_window(1, "scheduled", created_by_agent=True), _window(2, "scheduled")],
            )
        },
    )
    marked, human = result["rows"][0]["text"], result["rows"][1]["text"]
    assert "agent" in marked.lower(), f"an agent-created window is not marked: {marked}"
    assert "agent" not in human.lower(), (
        f"every window is marked as an agent's, so the mark says nothing: {human}"
    )


@dom_test
async def test_the_form_opens_one_card_at_a_time(store: Store) -> None:
    """**D8 at the DOM.** Never 200 fields at once.

    Four cards exist; exactly one is open. The other three are present — they are the summaries an
    operator reads to see where they are — and they are shut, which is the whole difference between
    this and a wall of fields.
    """
    routes = await uifixtures.all_routes(store)
    result = domdriver.run_scenario(
        "maintenance",
        {"routes": _with_windows(routes["editor"], []), "openForm": True},
    )
    assert result["canCreate"] is True, "an editor is not offered the form at all"
    cards = result["cards"]
    assert len(cards) == 4, f"the form rendered {len(cards)} cards, not four: {cards}"
    open_cards = [c for c in cards if c["open"]]
    assert len(open_cards) == 1, f"{len(open_cards)} cards are open at once: {cards}"
    assert open_cards[0]["index"] == 0, "the form did not open on the first card"
    assert all(c["heading"] for c in cards), "a card rendered with no heading to identify it"


@dom_test
async def test_a_viewer_is_not_offered_the_form(store: Store) -> None:
    """The control for the test above, and the capability rule: `mw.write` is `editor`.

    A viewer reads planned work and does not declare it. The control is **absent**, not disabled —
    a greyed-out button promising a power the server will refuse is worse than no button.
    """
    routes = await uifixtures.all_routes(store)
    result = domdriver.run_scenario("maintenance", {"routes": _with_windows(routes["viewer"], [])})
    assert result["canCreate"] is False, "a viewer is offered a control the server would refuse"


@dom_test
async def test_the_timeline_bar_draws_the_window_and_both_patch_bands(store: Store) -> None:
    """**The composed filter drawn in 40 pixels instead of written in a paragraph** (IV.1).

    Hand-written SVG precisely so this can read it: the harness has no layout engine, so every
    band carries a `data-band` and its width is a number. Three bands, the window widest, and both
    patch bands present — a bar that drew only the window would look right and would be hiding the
    ten minutes either side that are also in force.
    """
    routes = await uifixtures.all_routes(store)
    # Card 2 is *When*, which is where the bar lives. Reached by tapping its shut summary — the
    # way an operator moves backwards through the stepper — because `Next` is disabled until the
    # card it is on is complete, and an empty form has completed none.
    result = domdriver.run_scenario(
        "maintenance",
        {"routes": _with_windows(routes["editor"], []), "openForm": True, "card": 1},
    )
    assert [c["index"] for c in result["cards"] if c["open"]] == [1], (
        f"the When card did not open: {result['cards']}"
    )
    bands = {b["band"]: b["width"] for b in result["bands"] if b["band"] != "now"}
    assert set(bands) == {"lead", "window", "trail"}, f"the bar drew {sorted(bands)}"
    assert bands["window"] > bands["lead"] > 0, (
        f"the leading patch band is missing or not narrower than the window: {bands}"
    )
    assert abs(bands["lead"] - bands["trail"]) < 1.0, (
        f"the two patch bands differ in width; `patch_s` applies equally at both ends: {bands}"
    )


def test_the_screen_is_reachable_and_names_a_capability_the_server_enforces() -> None:
    """The registry entry, checked against `rbac` rather than against itself.

    `tests/test_ui_invariants.py` already asserts this for every view; this says which capability
    v0.21.0 is claiming, so the screen quietly becoming admin-only would fail here by name.
    """
    from pathlib import Path

    import netcorenoc
    from netcorenoc.crosscutting import rbac

    registry = (
        Path(netcorenoc.__file__).resolve().parent / "ui" / "app" / "registry.js"
    ).read_text(encoding="utf-8")
    assert 'id: "maintenance"' in registry, "the Maintenance screen is not in the registry"
    assert 'capability: "mw.read"' in registry
    assert rbac.PERMISSIONS["mw.read"] == "viewer", (
        "the screen is gated above viewer, so an operator who may not declare planned work can no "
        "longer see that it exists — which is the failure prime directive 4 names"
    )
    assert time.time() > 0
