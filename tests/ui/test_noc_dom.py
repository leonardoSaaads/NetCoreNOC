"""Broker | NOC and NOC → Secrets in the DOM harness (ADRs #454-#458, #459).

Driven against route tables captured from the real server, with one credential stored through the
real API, so what is asserted is what the console does with what the appliance really sends:

* the top bar offers Broker | NOC to a principal who can reach both, and nothing to one who cannot;
  the plane follows the route, and the sidebar shows one plane's groups at a time;
* the vault lists metadata and a status light — never a secret — and is closed, with the reason,
  to an editor who is not a custodian;
* a reveal sends the step-up in its own body and nowhere else, offers no authenticator field
  while two-factor sign-in does not exist, and shows a secret field masked.
"""

from __future__ import annotations

from typing import Any

from netcorenoc.store import Store

import authutil
import domdriver
import uifixtures
from domdriver import dom_test

SECRET = "only-the-vault-knows-this-1"


async def _routes(store: Store) -> tuple[dict[str, dict[str, Any]], int]:
    """Each role's captured table, with one credential stored and its detail captured."""
    _engine, _queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")
    try:
        made = await admin.post(
            "/api/noc/secrets",
            json={
                "name": "core-1 ssh",
                "kind": "ssh_password",
                "access_level": "root",
                "host": "10.0.0.1",
                "fields": {"username": "noc", "password": SECRET},
            },
        )
        sid = int(made.json()["id"])
        detail = (await admin.get(f"/api/noc/secrets/{sid}")).json()
    finally:
        await admin.aclose()
    tables = {role: await uifixtures.capture(app, role) for role in ("viewer", "editor", "admin")}
    tables["admin"][f"/api/noc/secrets/{sid}"] = {"status": 200, "json": detail}
    return tables, sid


def _render(routes: dict[str, Any], where: str) -> dict[str, Any]:
    return domdriver.run_scenario("render", {"routes": routes, "navigate": where})


@dom_test
async def test_the_top_bar_switches_planes_and_the_sidebar_follows(store: Store) -> None:
    tables, _sid = await _routes(store)
    broker = _render(tables["admin"], "#/overview")
    assert ".plane-switch" in broker["dump"] and ".plane-broker.plane-on" in broker["dump"]
    assert "Secrets" not in broker["navItems"] and "Situations" in broker["navItems"]
    assert broker["navGroups"] == ["Operations", "Evidence", "Administer"]
    noc = _render(tables["admin"], "#/secrets")
    assert ".plane-noc.plane-on" in noc["dump"]
    # ADR #468: Paperclip's company sidebar — the work, the agents one by one, the NOC itself;
    # ADR #470: the Overview first, where the switch lands.
    assert noc["navItems"] == [
        "Overview",
        "Cases",
        "KPIs",
        "Playbooks",
        "All agents",
        "Organization",
        "Secrets",
    ]
    assert noc["navGroups"] == ["Work", "Agents", "NOC"]
    assert "GET /api/noc/access" in noc["requestPaths"]
    # ADR #470: the KPIs and the playbooks are a lead's — an editor nobody named is not offered
    # them (and each route refuses them on its own).
    editor = _render(tables["editor"], "#/cases")
    assert "KPIs" not in editor["navItems"] and "Playbooks" not in editor["navItems"]
    assert editor["navItems"][:2] == ["Overview", "Cases"]
    # A viewer reads cases (the customer's side), so the switch is there — and the NOC plane
    # offers them the overview and the cases and nothing of the vault: never a greyed half.
    viewer = _render(tables["viewer"], "#/cases")
    assert ".plane-noc.plane-on" in viewer["dump"]
    assert viewer["navItems"] == ["Overview", "Cases"] and viewer["navGroups"] == ["Work"]
    refused = _render(tables["viewer"], "#/secrets")
    assert refused["refused"] is True
    assert not [p for p in refused["requestPaths"] if "/api/noc/" in p], "a refusal asks nothing"


@dom_test
async def test_the_vault_lists_metadata_and_a_light_and_never_a_secret(store: Store) -> None:
    tables, _sid = await _routes(store)
    admin = _render(tables["admin"], "#/secrets")
    assert '"core-1 ssh"' in admin["dump"] and '"10.0.0.1"' in admin["dump"]
    assert ".light" in admin["dump"] and '"Not checked"' in admin["dump"]
    assert SECRET not in admin["dump"]
    assert "GET /api/noc/secrets" in admin["requestPaths"]
    # An editor holds the capability but is no custodian: told why, offered nothing.
    editor = _render(tables["editor"], "#/secrets")
    assert "The vault is closed to you" in editor["dump"]
    assert "New credential" not in editor["dump"] and SECRET not in editor["dump"]


@dom_test
async def test_a_reveal_carries_its_step_up_in_its_own_body(store: Store) -> None:
    tables, sid = await _routes(store)
    routes = dict(tables["admin"])
    routes[f"POST /api/noc/secrets/{sid}/reveal"] = {
        "status": 200,
        "json": {
            "id": sid,
            "kind": "ssh_password",
            "shown_for_s": 30,
            "fields": {"username": "noc", "password": SECRET},
        },
    }
    result = domdriver.run_scenario(
        "reveal",
        {"routes": routes, "sid": sid, "username": "adm", "password": authutil.PW},
    )
    assert result["requestsBeforeSubmit"] == 0, "opening the step-up asks the server nothing"
    assert result["otpOffered"] is False, "no authenticator field while 2FA does not exist"
    assert result["posts"] == [
        {
            "path": f"/api/noc/secrets/{sid}/reveal",
            "body": {"stepup": {"username": "adm", "password": authutil.PW, "otp": None}},
        }
    ]
    assert result["revealedText"] is not None and "noc" in result["revealedText"]
    assert SECRET not in result["revealedText"], "a secret field is masked until asked"


async def _case_routes(store: Store) -> tuple[dict[str, dict[str, Any]], int]:
    """Each role's captured table with one case in the NOC queue, and that case's own reads."""
    import time

    from netcorenoc.engine.noc import case_loop

    import util

    _engine, _queue, app = await authutil.make_env(store)
    async with store.lock:
        sid = await store.create_situation(time.time(), None)
        alarm = await store.ingest(util.event(device="10.0.0.1", ts=time.time()))
        await store.add_alarm_to_situation(sid, alarm.alarm_id)
        await store.conn.execute("UPDATE situation SET severity='critical' WHERE id=?", (sid,))
        await store.promote_situation(sid, time.time(), "promote")
        await store.commit()
    await case_loop.tick(store)
    tables = {role: await uifixtures.capture(app, role) for role in ("viewer", "editor")}
    for role in tables:
        client = await authutil.client_as(app, role)
        try:
            for path in (f"/api/noc/cases/{sid}", f"/api/noc/cases/{sid}/feed"):
                tables[role][path] = {"status": 200, "json": (await client.get(path)).json()}
        finally:
            await client.aclose()
    return tables, sid


@dom_test
async def test_the_queue_shows_the_take_clock_and_only_the_noc_works_a_case(store: Store) -> None:
    tables, sid = await _case_routes(store)
    queue = _render(tables["editor"], "#/cases")
    assert ".case-open" in queue["dump"] and f'"{sid}"' in queue["dump"]
    assert '"Critical"' in queue["dump"] and '"25m left"' in queue["dump"], "the take clock runs"
    editor = _render(tables["editor"], f"#/cases/{sid}")
    viewer = _render(tables["viewer"], f"#/cases/{sid}")
    # "Take" labels a clock and a history column for everyone; it is a button only for the NOC.
    assert editor["dump"].count('"Take"') == viewer["dump"].count('"Take"') + 1
    assert ".case-path" in editor["dump"] and "Read-only for you." in viewer["dump"]
    assert '"Internal"' not in viewer["dump"], "a viewer is offered only what it may post"
