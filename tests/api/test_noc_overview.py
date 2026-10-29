"""The NOC overview, its leads, and a customer update anyone working cases keeps (ADR #470).

* the overview counts the live cases as Queue, Working and Pending — each by tier (or the NOC team),
  by priority and by holder — with the takes running late, and names who holds what only to a
  reader who may see people;
* the KPIs and the playbooks are an admin's, a lead's (an editor an admin named, stepped up) or an
  agent's; `/api/noc/access` says which the caller holds; a lead is a person, never a token;
* setting a case pending with no next update ahead takes the handbook's cadence for its priority;
* the case list narrows by priority and by tier, and carries the holder's photo digest.
"""

from __future__ import annotations

import time
from typing import Any

from netcorenoc.engine.noc import case_loop, kpi, overview
from netcorenoc.store import Store

import authutil
import util

STEP = {"username": "adm", "password": authutil.PW}


async def _open_case(store: Store, *, device: str = "10.0.0.1", severity: str = "major") -> int:
    now = time.time()
    async with store.lock:
        sid = await store.create_situation(now, None)
        alarm = await store.ingest(util.event(device=device, ts=now))
        await store.add_alarm_to_situation(sid, alarm.alarm_id)
        await store.conn.execute("UPDATE situation SET severity = ? WHERE id = ?", (severity, sid))
        await store.promote_situation(sid, now, "promote")
        await store.commit()
    await case_loop.tick(store)
    return sid


async def _clients(store: Store) -> tuple[Any, dict[str, Any]]:
    _engine, _queue, app = await authutil.make_env(store)
    return app, {r: await authutil.client_as(app, r) for r in ("viewer", "editor", "admin")}


async def _close(clients: dict[str, Any]) -> None:
    for client in clients.values():
        await client.aclose()


def test_the_summary_splits_each_state_by_level_priority_and_holder() -> None:
    census = [
        {
            "status": "queue",
            "priority": "critical",
            "tier": 1,
            "with_people": False,
            "holder": "none",
            "agent_id": None,
            "agent_name": None,
            "person_ref": None,
            "person_name": None,
            "person_avatar": None,
            "n": 2,
        },
        {
            "status": "working",
            "priority": "major",
            "tier": 2,
            "with_people": False,
            "holder": "agent",
            "agent_id": 4,
            "agent_name": "Vega",
            "person_ref": None,
            "person_name": None,
            "person_avatar": None,
            "n": 1,
        },
        {
            "status": "pending_customer",
            "priority": "minor",
            "tier": 1,
            "with_people": True,
            "holder": "person",
            "agent_id": None,
            "agent_name": None,
            "person_ref": "user:3",
            "person_name": "ana",
            "person_avatar": "abc",
            "n": 1,
        },
    ]
    now = 10_000.0
    takes = [
        {"priority": "critical", "entered_at": now - 30 * 60},  # past 25 minutes: late
        {"priority": "critical", "entered_at": now - 20 * 60},  # 80%: near
        {"priority": "minor", "entered_at": now - 60},
    ]
    out = overview.summary(census, takes, {"overdue": 1, "resolved": 3}, now)
    assert out["states"]["queue"]["total"] == 2
    assert out["states"]["queue"]["by_priority"]["critical"] == 2
    assert out["states"]["working"]["by_level"]["2"] == 1
    assert out["states"]["pending"]["by_level"]["team"] == 1
    assert out["states"]["pending"]["by_holder"]["person"] == 1
    assert out["takes"] == {"running": 1, "near": 1, "late": 1}
    assert out["updates"]["overdue"] == 1 and out["resolved"]["total"] == 3
    assert out["agents"] == [{"id": 4, "name": "Vega", "queue": 0, "working": 1, "pending": 0}]
    assert out["people"][0]["user_id"] == 3 and out["people"][0]["avatar"] == "abc"


async def test_the_overview_counts_the_live_cases_and_names_holders_to_editors(
    store: Store,
) -> None:
    critical = await _open_case(store, severity="critical")
    await _open_case(store, device="10.0.0.2", severity="minor")
    app, clients = await _clients(store)
    try:
        taken = await clients["editor"].post(f"/api/noc/cases/{critical}/take")
        assert taken.status_code == 200, taken.text
        seen = (await clients["editor"].get("/api/noc/overview")).json()
        assert seen["states"]["queue"]["total"] == 1
        assert seen["states"]["working"]["total"] == 1
        assert seen["states"]["working"]["by_priority"]["critical"] == 1
        assert seen["states"]["queue"]["by_level"]["1"] == 1
        assert seen["named"] is True and [p["name"] for p in seen["people"]] == [
            authutil.ROLE_USER["editor"]
        ]
        # A viewer reads the counts and never who holds the cases (`case_view`'s rule).
        blind = (await clients["viewer"].get("/api/noc/overview")).json()
        assert blind["states"] == seen["states"]
        assert blind["named"] is False and blind["people"] == [] and blind["agents"] == []
        # The list narrows by priority and by tier, and carries the holder's photo digest.
        rows = (
            await clients["editor"].get(
                "/api/noc/cases", params={"view": "live", "priority": "critical"}
            )
        ).json()["cases"]
        assert [c["id"] for c in rows] == [critical]
        assert rows[0]["owner"]["user_id"] is not None and "avatar" in rows[0]["owner"]
        team = await clients["editor"].get("/api/noc/cases", params={"view": "live", "tier": 0})
        assert team.status_code == 200 and team.json()["cases"] == []
        wrong = await clients["editor"].get("/api/noc/cases", params={"priority": "urgent"})
        assert wrong.status_code == 400
    finally:
        await _close(clients)
    del app


async def test_the_kpis_and_playbooks_are_a_leads(store: Store) -> None:
    app, clients = await _clients(store)
    try:
        mine = (await clients["editor"].get("/api/noc/access")).json()
        assert mine["lead"] is False and mine["custodian"] is False
        assert (await clients["admin"].get("/api/noc/access")).json()["lead"] is True
        for path in ("/api/noc/cases/kpis", "/api/noc/playbooks"):
            refused = await clients["editor"].get(path)
            assert refused.status_code == 403 and "NOC lead" in refused.json()["detail"]
        editor = await store.get_user_by_name(authutil.ROLE_USER["editor"])
        assert editor is not None
        subject = f"user:{editor['id']}"
        # Naming a lead is stepped up; a token is never one.
        bare = await clients["admin"].post(
            "/api/noc/grants", json={"subject": subject, "role": "lead", "enabled": True}
        )
        assert bare.status_code in (400, 403)
        token = await clients["admin"].post(
            "/api/noc/grants",
            json={"subject": "token:1", "role": "lead", "enabled": True, "stepup": STEP},
        )
        assert token.status_code == 400
        named = await clients["admin"].post(
            "/api/noc/grants",
            json={"subject": subject, "role": "lead", "enabled": True, "stepup": STEP},
        )
        assert named.status_code == 200, named.text
        people = (await clients["admin"].get("/api/noc/grants")).json()["people"]
        assert next(p for p in people if p["ref"] == subject)["lead"] is True
        assert (await clients["editor"].get("/api/noc/access")).json()["lead"] is True
        for path in ("/api/noc/cases/kpis", "/api/noc/playbooks"):
            assert (await clients["editor"].get(path)).status_code == 200
        # Withdrawing is never stepped up.
        off = await clients["admin"].post(
            "/api/noc/grants", json={"subject": subject, "role": "lead", "enabled": False}
        )
        assert off.status_code == 200
        assert (await clients["editor"].get("/api/noc/cases/kpis")).status_code == 403
    finally:
        await _close(clients)
    del app


async def test_setting_pending_takes_the_cadence_when_no_next_update_is_ahead(
    store: Store,
) -> None:
    sid = await _open_case(store, severity="critical")
    app, clients = await _clients(store)
    try:
        editor = clients["editor"]
        assert (await editor.post(f"/api/noc/cases/{sid}/take")).status_code == 200
        filled = await editor.post(
            f"/api/noc/cases/{sid}",
            json={
                "title": "Fibre cut",
                "description": "LOS on the uplink",
                "executive_summary": "Link down",
                "current_analysis": "Optical power -40 dBm",
                "noc_next_step": "Carrier ticket",
                "customer_next_step": "None",
            },
        )
        assert filled.status_code == 200, filled.text
        before = time.time()
        pended = await editor.post(
            f"/api/noc/cases/{sid}/status", json={"status": "pending_customer"}
        )
        assert pended.status_code == 200, pended.text
        when = float(pended.json()["next_update_at"])
        cadence = kpi.UPDATE_CADENCE_S["pending"]["critical"]
        assert before + cadence - 5 <= when <= time.time() + cadence + 5
    finally:
        await _close(clients)
    del app
