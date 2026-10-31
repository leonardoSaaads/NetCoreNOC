"""The NOC's overview, its case list's filters, the customer update's send, and the vault's AI
providers in the DOM harness (ADRs #470, #471).

Driven against route tables captured from the real server, so what is asserted is what the console
does with what the appliance really sends:

* the Overview counts Queue, Working and Pending from `/api/noc/overview`, and each tile opens the
  cases it counts;
* the case list's severity chips ask the server for exactly those cases, in the address;
* the customer update ends in **Send customer update** (ADR #478), which posts the saved update
  with the version it was read at, and says it went to the case feed — email not connected yet;
  an incomplete update is refused with what it lacks;
* the vault offers a card per AI provider, each opening its own short form; a new device
  credential starts from the kinds' cards.
"""

from __future__ import annotations

import time
from typing import Any

from netcorenoc.engine.noc import case_loop
from netcorenoc.store import Store

import authutil
import domdriver
import uifixtures
import util
from domdriver import dom_test


async def _tables(store: Store) -> tuple[dict[str, dict[str, Any]], int]:
    """Each role's captured table with one critical case taken by the editor, and its reads."""
    _engine, _queue, app = await authutil.make_env(store)
    async with store.lock:
        sid = await store.create_situation(time.time(), None)
        alarm = await store.ingest(util.event(device="10.0.0.1", ts=time.time()))
        await store.add_alarm_to_situation(sid, alarm.alarm_id)
        await store.conn.execute("UPDATE situation SET severity='critical' WHERE id=?", (sid,))
        await store.promote_situation(sid, time.time(), "promote")
        await store.commit()
    await case_loop.tick(store)
    editor = await authutil.client_as(app, "editor")
    try:
        assert (await editor.post(f"/api/noc/cases/{sid}/take")).status_code == 200
    finally:
        await editor.aclose()
    tables = {role: await uifixtures.capture(app, role) for role in ("editor", "admin")}
    for role in tables:
        client = await authutil.client_as(app, role)
        try:
            for path in (
                f"/api/noc/cases/{sid}",
                f"/api/noc/cases/{sid}/feed",
                "/api/noc/cases?view=working",
                "/api/noc/cases?view=queue&priority=critical",
            ):
                tables[role][path] = {"status": 200, "json": (await client.get(path)).json()}
        finally:
            await client.aclose()
    return tables, sid


def _render(routes: dict[str, Any], where: str) -> dict[str, Any]:
    return domdriver.run_scenario("render", {"routes": routes, "navigate": where})


def _act(routes: dict[str, Any], where: str, **params: Any) -> dict[str, Any]:
    return domdriver.run_scenario("submitForm", {"routes": routes, "navigate": where, **params})


@dom_test
async def test_the_overview_counts_the_three_states_and_opens_them(store: Store) -> None:
    tables, _sid = await _tables(store)
    seen = _render(tables["editor"], "#/noc")
    assert "GET /api/noc/overview" in seen["requestPaths"]
    for word in ('"Queue"', '"Working"', '"Pending"', '"By tier"', '"NOC team"', '"By priority"'):
        assert word in seen["dump"], word
    assert (
        ".nov-tile.nov-tile-working" in seen["dump"]
        and 'aria-label="Working: 1 case. Open them."' in seen["dump"]
    ), "a tile opens the cases it counts"
    assert '"Who holds what"' in seen["dump"]


@dom_test
async def test_a_severity_filter_in_the_address_asks_the_server_for_those_cases(
    store: Store,
) -> None:
    tables, _sid = await _tables(store)
    seen = _render(tables["editor"], "#/cases?view=queue&priority=critical")
    assert "GET /api/noc/cases?view=queue&priority=critical" in seen["requestPaths"]
    assert '"Severity"' in seen["dump"] and '"Level"' in seen["dump"]
    assert ".cf-chip.cf-critical.on" in seen["dump"], "the chosen chip says it is on"
    assert '"Clear filters"' in seen["dump"]


@dom_test
async def test_send_customer_update_posts_the_saved_update_and_says_where_it_went(
    store: Store,
) -> None:
    tables, sid = await _tables(store)
    routes = dict(tables["editor"])
    version = routes[f"/api/noc/cases/{sid}"]["json"]["version"]
    routes[f"POST /api/noc/cases/{sid}/customer-update"] = {
        "status": 200,
        "json": {"id": 9, "sent": True, "email": False},
    }
    result = _act(routes, f"#/cases/{sid}", click=["Customer update", "Send customer update"])
    posts = [r for r in result["sent"] if r["method"] == "POST"]
    assert [r["path"] for r in posts] == [f"/api/noc/cases/{sid}/customer-update"]
    assert posts[0]["body"] == {"version": version}, "what is sent is what was read"
    assert ".send-note" in result["dump"] and "posted to the case feed" in result["dump"]
    # The real server refuses an incomplete update, and the form shows its sentence.
    refused = dict(tables["editor"])
    refused[f"POST /api/noc/cases/{sid}/customer-update"] = {
        "status": 409,
        "json": {"detail": "complete the customer update first: title, description"},
    }
    result = _act(refused, f"#/cases/{sid}", click=["Customer update", "Send customer update"])
    assert "complete the customer update first" in result["dump"]


@dom_test
async def test_the_vault_offers_each_ai_provider_and_a_card_per_device_kind(
    store: Store,
) -> None:
    tables, _sid = await _tables(store)
    shown = _render(tables["admin"], "#/secrets?tab=ai")
    for name in ("Anthropic", "OpenAI", "Google AI Studio", "xAI", "Meta", "Ollama"):
        assert f'"{name}"' in shown["dump"], name
    assert ".brand-tile" in shown["dump"]
    opened = _act(tables["admin"], "#/secrets?tab=ai", click=["Connect"])
    assert '"Connect Anthropic"' in opened["dump"] and "Get a key" in opened["dump"]
    assert not [r for r in opened["sent"] if r["method"] == "POST"], "opening it stores nothing"
    picker = _act(tables["admin"], "#/secrets", click=["New credential"])
    assert "What does this credential open?" in picker["dump"]
    for kind in ("SFTP", "SMTP", "API token"):
        assert f'"{kind}"' in picker["dump"], kind


@dom_test
async def test_the_overview_says_what_the_agents_still_need(store: Store) -> None:
    """ADR #478: with no AI provider, agent or device credential, the overview lists each step,
    in words and with a link to where it is done."""
    tables, _sid = await _tables(store)
    seen = _render(tables["admin"], "#/noc")
    assert '"Get the NOC\'s agents working"' in seen["dump"]
    for step in ("Connect an AI provider", "Hire an agent", "Make one agent the entry point"):
        assert f'"{step}"' in seen["dump"], step
    assert '"Secrets → AI providers"' in seen["dump"], "each step links to where it is done"


@dom_test
async def test_in_a_container_a_local_ollama_is_proposed_on_the_docker_host(store: Store) -> None:
    """ADR #478: inside a container 127.0.0.1 is the container itself, so the Ollama card proposes
    host.docker.internal (which docker-compose.yml maps to the host) and says why."""
    tables, _sid = await _tables(store)
    routes = dict(tables["admin"])
    vault = routes["/api/noc/secrets"]["json"]
    ollama = [p for p in vault["catalogue"]["providers"] if p["id"] == "ollama"]
    catalogue = {**vault["catalogue"], "providers": ollama, "in_container": True}
    routes["/api/noc/secrets"] = {"status": 200, "json": {**vault, "catalogue": catalogue}}
    seen = _act(routes, "#/secrets?tab=ai", click=["Connect"])
    assert '"Connect Ollama"' in seen["dump"] and '"Where it runs"' in seen["dump"]
    assert "host.docker.internal" in seen["dump"], "the Docker host, not the container"
    assert "127.0.0.1 is the container itself" in seen["dump"]
    assert not [r for r in seen["sent"] if r["method"] == "POST"], "opening it stores nothing"
