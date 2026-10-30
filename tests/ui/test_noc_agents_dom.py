"""NOC → Agents, Organization, and a case's milestones in the DOM harness (ADR #468).

Driven against route tables captured from the real server, with two agents hired through the real
API and a case routed to one of them by the real agent loop:

* the sidebar lists each agent under *Agents*, in the order of the organization, and asks for the
  roster only from a principal who may read it;
* the agents page and one agent's page render what the server sent; hiring is offered to an admin
  and to nobody else;
* the organization chart puts the NOC team on top of the agents, and tells an admin — only an
  admin — that an agent is moved by dragging it onto its new manager; the drop rules refuse a loop
  and a move to where it already reports, and propose the tier one below the new manager's;
* a routed case shows its milestones — the climb to the next tier among them — and who holds it.
"""

from __future__ import annotations

import time
from typing import Any

from netcorenoc.engine.noc import agent_loop, case_loop
from netcorenoc.store import Store

import authutil
import domdriver
import uifixtures
import util
from domdriver import dom_test


async def _routes(store: Store) -> tuple[dict[str, dict[str, Any]], dict[str, int], int]:
    _engine, _queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")
    ids: dict[str, int] = {}
    try:
        for body in (
            {"name": "Atlas", "role": "head", "tier": 3, "icon": "crown", "adapter": "external"},
            {"name": "Vega", "role": "engineer", "entry": True, "adapter": "external"},
        ):
            if body["name"] == "Vega":
                body["reports_to"] = ids["Atlas"]
            made = await admin.post("/api/noc/agents", json=body)
            assert made.status_code == 200, made.text
            ids[str(body["name"])] = int(made.json()["agent"]["id"])
    finally:
        await admin.aclose()
    async with store.lock:
        sid = await store.create_situation(time.time(), None)
        alarm = await store.ingest(util.event(device="10.0.0.1", ts=time.time()))
        await store.add_alarm_to_situation(sid, alarm.alarm_id)
        await store.promote_situation(sid, time.time(), "promote")
        await store.commit()
    await case_loop.tick(store)
    assert (await agent_loop.tick(store))["routed"] == 1
    tables = {role: await uifixtures.capture(app, role) for role in ("viewer", "editor", "admin")}
    for role in ("editor", "admin"):
        client = await authutil.client_as(app, role)
        try:
            for path in (
                f"/api/noc/agents/{ids['Vega']}?days=14",
                f"/api/noc/agents/{ids['Atlas']}?days=1",
                f"/api/noc/cases/{sid}",
                f"/api/noc/cases/{sid}/feed",
            ):
                tables[role][path] = {"status": 200, "json": (await client.get(path)).json()}
        finally:
            await client.aclose()
    return tables, ids, sid


def _render(routes: dict[str, Any], where: str) -> dict[str, Any]:
    return domdriver.run_scenario("render", {"routes": routes, "navigate": where})


@dom_test
async def test_the_sidebar_lists_each_agent_and_the_pages_render_them(store: Store) -> None:
    tables, ids, _sid = await _routes(store)
    admin = _render(tables["admin"], "#/agents")
    items = admin["navItems"]
    at = items.index("All agents")
    assert items[at + 1].startswith("Atlas") and items[at + 2].startswith("Vega"), (
        "each agent under All agents, its manager first"
    )
    # ADR #473: the customers' group sits between the work and the agents.
    assert admin["navGroups"] == ["Work", "Customers", "Agents", "NOC"]
    assert ".agent-row" in admin["dump"] and '"Hire an agent"' in admin["dump"]
    editor = _render(tables["editor"], "#/agents")
    assert ".agent-row" in editor["dump"] and '"Hire an agent"' not in editor["dump"], (
        "hiring is an admin's"
    )
    page = _render(tables["admin"], f"#/agents/{ids['Vega']}")
    assert ".agent-head" in page["dump"] and '"Chain of command"' in page["dump"]
    assert f"GET /api/noc/agents/{ids['Vega']}?days=14" in page["requestPaths"]
    org = _render(tables["admin"], "#/org")
    assert '"The NOC team"' in org["dump"] and org["dump"].count(".org-card") >= 3
    # A viewer holds no `agents.read`: the plane offers no agents, and nothing asks for them.
    viewer = _render(tables["viewer"], "#/cases")
    assert "All agents" not in viewer["navItems"]
    assert not [p for p in viewer["requestPaths"] if "/api/noc/agents" in p]
    assert _render(tables["viewer"], "#/agents")["refused"] is True


@dom_test
async def test_a_routed_case_shows_its_milestones_and_who_holds_it(store: Store) -> None:
    tables, _ids, sid = await _routes(store)
    editor = _render(tables["editor"], f"#/cases/{sid}")
    dump = editor["dump"]
    assert ".milestones" in dump and ".case-path" in dump and ".highlights" in dump
    for words in ('"Take"', '"Customer update"', '"Restore service"', '"Escalates to Tier 2"'):
        assert words in dump, f"the milestones do not show {words}"
    assert ".assignment" in dump and '"Vega"' in dump and '"With NOC Tier 1"' in dump
    queue = _render(tables["editor"], "#/cases")
    assert ".holder" in queue["dump"] and '"Vega"' in queue["dump"], "the queue says who holds it"


@dom_test
async def test_an_admin_moves_an_agent_by_dragging_it_and_no_loop_is_offered(store: Store) -> None:
    tables, ids, _sid = await _routes(store)
    hint = "Drag an agent onto its new manager to move it."
    assert hint in _render(tables["admin"], "#/org")["dump"]
    assert hint not in _render(tables["editor"], "#/org")["dump"], "moving agents is an admin's"
    atlas, vega = ids["Atlas"], ids["Vega"]
    agents = [
        {"id": atlas, "reports_to": None, "tier": 3},
        {"id": vega, "reports_to": atlas, "tier": 1},
        {"id": 99, "reports_to": vega, "tier": 1},
        {"id": 7, "reports_to": None, "tier": 1},
    ]
    calls = {
        "below_atlas": {"fn": "below", "args": [agents, atlas]},
        "atlas_onto_its_report": {"fn": "droppable", "args": [agents, atlas, 99]},
        "vega_onto_atlas": {"fn": "droppable", "args": [agents, vega, atlas]},
        "vega_onto_the_team": {"fn": "droppable", "args": [agents, vega, 0]},
        "seven_onto_vega": {"fn": "droppable", "args": [agents, 7, vega]},
        "seven_onto_the_team": {"fn": "droppable", "args": [agents, 7, 0]},
        "tier_under_atlas": {"fn": "proposedTier", "args": [agents[3], agents[0]]},
        "tier_under_vega": {"fn": "proposedTier", "args": [agents[3], agents[1]]},
        "tier_at_the_top": {"fn": "proposedTier", "args": [agents[1], None]},
    }
    out = domdriver.run_scenario(
        "moduleCalls",
        {"routes": tables["admin"], "module": "app/views/parts/orgmove.js", "calls": calls},
    )["out"]
    assert out["below_atlas"] == sorted([vega, 99])
    assert out["atlas_onto_its_report"] is False, "a manager onto its own report is a loop"
    assert out["vega_onto_atlas"] is False, "onto where it already reports changes nothing"
    assert out["vega_onto_the_team"] is True and out["seven_onto_vega"] is True
    assert out["seven_onto_the_team"] is False
    assert (out["tier_under_atlas"], out["tier_under_vega"], out["tier_at_the_top"]) == (2, 1, 1)
