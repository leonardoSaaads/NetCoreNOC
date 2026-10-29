"""The NOC's customers in the DOM harness (ADR #473).

Driven against route tables captured from the real server, so what is asserted is what the console
does with what the appliance really sends:

* the sidebar has a *Customers* group listing each customer, and under the one open the four parts
  of its book — Instructions, Workbook, Additional docs, Circuits;
* the instructions and the NOC's memory are shown side by side, and saving the instructions sends
  them with the version they were read at;
* the workbook groups contacts by company in the order they are called, and a viewer is shown that
  a contact exists, not its email or phone;
* a circuit's page draws its path site by site, with each hop's coordinates.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from netcorenoc.store import Store

import authutil
import domdriver
import uifixtures
from domdriver import dom_test

SHEET = (Path(__file__).parents[1] / "fixtures" / "noc_circuits.tsv").read_text()


async def _tables(store: Store) -> tuple[dict[str, dict[str, Any]], int, int]:
    """Akamai and TIM Brasil, Akamai's book written; each role's captured table and its reads."""
    _engine, _queue, app = await authutil.make_env(store)
    async with store.lock:
        ids = {}
        for name, slug, ip in (("Akamai", "akamai", "10.0.0.1"), ("TIM Brasil", "tim", "10.0.0.2")):
            ne = await store.ne_id(ip, 1.0)
            ids[slug] = await store.create_organization(name, slug, 1.0)
            await store.set_ne_organization(ne, ids[slug])
        await store.commit()
    akamai = ids["akamai"]
    base = f"/api/noc/customers/{akamai}"
    editor = await authutil.client_as(app, "editor")
    try:
        said = "Name every case AKAM-<site>-<fault>. Call Zayo first for a fibre cut."
        assert (await editor.post(base, json={"instructions": said})).status_code == 200
        assert (
            await editor.post(f"{base}/memory", json={"memory": "Zayo answers by phone at night."})
        ).status_code == 200
        for level, name in ((1, "Zayo NOC"), (2, "Zayo duty manager")):
            contact = {
                "party": "Zayo",
                "party_kind": "provider",
                "escalation_level": level,
                "name": name,
                "email": "noc@zayo.example",
                "phone": "+1 800 555 0100",
            }
            assert (await editor.post(f"{base}/contacts", json=contact)).status_code == 200
        stored = await editor.post(f"{base}/circuits/import", json={"text": SHEET, "apply": True})
        assert stored.status_code == 200, stored.text
        circuits = (await editor.get(f"{base}/circuits")).json()["circuits"]
    finally:
        await editor.aclose()
    circuit = next(c["id"] for c in circuits if c["designation"].startswith("SILLA-TO-ALZUR 2"))
    tables = {role: await uifixtures.capture(app, role) for role in ("viewer", "editor")}
    for role in tables:
        client = await authutil.client_as(app, role)
        try:
            for path in (
                base,
                f"{base}/circuits?q=&status=&limit=100&offset=0",
                f"{base}/circuits/{circuit}",
            ):
                tables[role][path] = {"status": 200, "json": (await client.get(path)).json()}
        finally:
            await client.aclose()
    return tables, akamai, circuit


def _render(routes: dict[str, Any], where: str) -> dict[str, Any]:
    return domdriver.run_scenario("render", {"routes": routes, "navigate": where})


@dom_test
async def test_each_customer_is_in_the_sidebar_with_the_four_parts_of_its_book(
    store: Store,
) -> None:
    tables, akamai, _circuit = await _tables(store)
    listed = _render(tables["editor"], "#/customers")
    assert "GET /api/noc/customers" in listed["requestPaths"]
    assert '"Akamai"' in listed["dump"] and '"TIM Brasil"' in listed["dump"]
    opened = _render(tables["editor"], f"#/customers/{akamai}/instructions")
    nav = opened["nav"] if "nav" in opened else opened["dump"]
    for word in (
        '"Customers"',
        '"All customers"',
        '"Instructions"',
        '"Workbook"',
        '"Additional docs"',
        '"Circuits"',
    ):
        assert word in opened["dump"] or word in str(nav), word
    assert "Name every case AKAM-<site>-<fault>." in opened["dump"]
    assert '"NOC memory"' in opened["dump"] and "Zayo answers by phone at night." in opened["dump"]


@dom_test
async def test_saving_the_instructions_sends_them_with_their_version(store: Store) -> None:
    tables, akamai, _circuit = await _tables(store)
    result = domdriver.run_scenario(
        "submitForm",
        {
            "routes": tables["editor"],
            "navigate": f"#/customers/{akamai}/instructions",
            "click": ["Edit", "Save the instructions"],
        },
    )
    posts = [r for r in result["sent"] if r["method"] == "POST"]
    assert [p["path"] for p in posts] == [f"/api/noc/customers/{akamai}"]
    body = posts[0]["body"]
    assert body["version"] == 1 and "AKAM-<site>-<fault>" in body["instructions"]


@dom_test
async def test_the_workbook_is_by_company_in_call_order_and_a_viewer_sees_no_details(
    store: Store,
) -> None:
    tables, akamai, _circuit = await _tables(store)
    shown = _render(tables["editor"], f"#/customers/{akamai}/workbook")
    dump = shown["dump"]
    assert '"Zayo"' in dump and '"Provider"' in dump
    assert dump.index('"Zayo NOC"') < dump.index('"Zayo duty manager"')
    assert '"1st"' in dump and '"2nd"' in dump and "noc@zayo.example" in dump
    hidden = _render(tables["viewer"], f"#/customers/{akamai}/workbook")
    assert "noc@zayo.example" not in hidden["dump"] and "hidden for your role" in hidden["dump"]
    assert '"Add a contact"' not in hidden["dump"], "a viewer is offered no change"


@dom_test
async def test_a_circuit_draws_its_path_site_by_site(store: Store) -> None:
    tables, akamai, circuit = await _tables(store)
    listed = _render(tables["editor"], f"#/customers/{akamai}/circuits")
    assert (
        f"GET /api/noc/customers/{akamai}/circuits?q=&status=&limit=100&offset=0"
        in listed["requestPaths"]
    )
    assert "SILLA-TO-ALZUR 2- ETH 10G-P02-002" in listed["dump"]
    shown = _render(tables["editor"], f"#/customers/{akamai}/circuits/{circuit}")
    dump = shown["dump"]
    for word in ("SILA PoP", "Cient Port", "MEETS ODF", "AL ZOUR PoP", "DTM-00066"):
        assert word in dump, word
    assert dump.index("SILA PoP") < dump.index("AL ZOUR PoP"), "the path runs from A to Z"
