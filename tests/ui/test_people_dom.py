"""People & access, and Your account, in the DOM harness (v0.25.0, ADR #401-#404).

Driven against route tables captured from the real server (`uifixtures`), so what is asserted is
what the screens do with the payloads the appliance really sends:

* the menu has one "People & access" entry where Users, Service tokens and Governance were, and
  the old addresses still open it;
* each person is a row with a face (a photo or initials) and a name, never a bare username;
* the capability grid is grouped by category and names what a capability lets a person do — an
  identifier is a tooltip, not the text — and a capability above the role is locked, not offered;
* Your account shows the person, the password form, and access as counts per category, with the
  wall of identifiers the old page printed gone.
"""

from __future__ import annotations

import re
from typing import Any

from netcorenoc.store import Store

import domdriver
import uifixtures
from domdriver import dom_test


async def _render(store: Store, role: str, where: str, **extra: Any) -> dict[str, Any]:
    routes = await uifixtures.all_routes(store)
    return domdriver.run_scenario("render", {"routes": routes[role], "navigate": where, **extra})


@dom_test
async def test_one_menu_entry_replaces_three_and_the_old_addresses_still_land(
    store: Store,
) -> None:
    result = await _render(store, "admin", "#/access")
    assert "People & access" in result["navItems"]
    for gone in ("Users", "Service tokens", "Governance"):
        assert gone not in result["navItems"], gone
    for old in ("#/users", "#/tokens", "#/governance"):
        landed = await _render(store, "admin", old)
        assert landed["refused"] is False and landed["unknown"] is False, old
        assert "access-tabs" in landed["dump"], f"{old} did not open People & access"


@dom_test
async def test_each_person_has_a_face_and_a_name(store: Store) -> None:
    dump = (await _render(store, "admin", "#/access"))["dump"]
    rows = re.findall(r"<button \.person-row", dump)
    assert len(rows) >= 3, dump[-2000:]
    assert dump.count(".avatar") >= len(rows), "a person row without an avatar"


@dom_test
async def test_the_grid_is_grouped_named_and_locks_what_the_role_cannot_hold(
    store: Store,
) -> None:
    dump = (await _render(store, "admin", "#/access?tab=roles"))["dump"]
    for group in ("Situations & alarms", "Monitoring", "Administration"):
        assert group in dump, group
    assert "Clear alarms" in dump and "Manage users" in dump
    # The Roles tab opens on Editor: every admin-only capability is drawn locked, not offered.
    assert dump.count(".cap-above") >= 10, "admin-only capabilities are offered to the editor role"


@dom_test
async def test_your_account_is_a_person_a_password_and_counts_not_a_wall(store: Store) -> None:
    dump = (await _render(store, "editor", "#/account"))["dump"]
    assert ".acct-profile" in dump and ".avatar" in dump
    assert "Change password" in dump
    assert ".acct-cats" in dump
    # The old page printed every identifier as text; now they are one disclosure away, as labels.
    visible = dump.split("<details")[0]
    assert "alarm.acknowledge" not in visible and "events.stream" not in visible
