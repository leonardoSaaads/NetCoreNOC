"""The NOC's agents through the HTTP API: the organization, hiring, waking, the chain a case climbs
(ADRs #465, #467).

* an admin hires an agent; an agent running elsewhere gets its key once, a `claude` one never — the
  appliance keeps it sealed — and an agent's key is a service token named as the agent, granted
  `agent`, so what it does is recorded under its name;
* the organization is a forest: a manager below the agent is a loop and refused; terminating an
  agent moves its reports up to its manager and revokes its key;
* an agent reads itself at `/me`, and wakes or pauses only the agents below it;
* a case routed to an agent climbs on escalation — to the manager at the next tier, then past the
  top to the NOC team — and an agent delegates only its own cases, only downward.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from netcorenoc.engine.noc import case_loop
from netcorenoc.store import Store

import authutil
import util

STEP = {"username": "adm", "password": authutil.PW}


async def _hire(admin: httpx.AsyncClient, name: str, **fields: Any) -> dict[str, Any]:
    body = {"name": name, "role": "engineer", "adapter": "external", **fields}
    made = await admin.post("/api/noc/agents", json=body)
    assert made.status_code == 200, made.text
    return dict(made.json())


def _as(app: Any, token: str) -> httpx.AsyncClient:
    client = authutil.new_client(app)
    client.headers["Authorization"] = f"Bearer {token}"
    return client


async def _open_case(store: Store) -> int:
    now = time.time()
    async with store.lock:
        sid = await store.create_situation(now, None)
        alarm = await store.ingest(util.event(device="10.0.0.1", ts=now))
        await store.add_alarm_to_situation(sid, alarm.alarm_id)
        await store.promote_situation(sid, now, "promote")
        await store.commit()
    await case_loop.tick(store)
    return sid


async def test_hiring_an_organization_and_its_keys(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")
    editor = await authutil.client_as(app, "editor")
    try:
        head = await _hire(admin, "Atlas", role="head", tier=4, icon="crown")
        assert head["token"] and head["shown_once"] and head["webhook_key"] is None
        lead = await _hire(
            admin,
            "Orion",
            role="supervisor",
            tier=2,
            reports_to=head["agent"]["id"],
            adapter="webhook",
            adapter_config={"url": "https://agents.example/wake"},
        )
        assert lead["webhook_key"] and lead["token"]
        l1 = await _hire(admin, "Vega", tier=1, entry=True, reports_to=lead["agent"]["id"])
        refused = await editor.post(
            "/api/noc/agents", json={"name": "x", "role": "engineer", "adapter": "external"}
        )
        assert refused.status_code == 403
        loop = await admin.post(
            f"/api/noc/agents/{head['agent']['id']}",
            json={"version": head["agent"]["version"], "reports_to": l1["agent"]["id"]},
        )
        assert loop.status_code == 400 and "loop" in loop.json()["detail"]
        org = (await editor.get("/api/noc/agents")).json()
        assert [n["name"] for n in org["org"]] == ["Atlas"]
        assert org["org"][0]["reports"][0]["reports"][0]["name"] == "Vega"
        assert "instructions" not in org["agents"][0], "a listing leaves the instructions out"
        page = (await editor.get(f"/api/noc/agents/{l1['agent']['id']}", params={"days": 7})).json()
        assert [c["name"] for c in page["agent"]["chain_of_command"]] == ["Orion", "Atlas"]
        claude = await admin.post(
            "/api/noc/agents",
            json={
                "name": "Lyra",
                "role": "engineer",
                "adapter": "claude",
                "adapter_config": {"secret_id": 99},
            },
        )
        assert claude.status_code == 400 and "Anthropic key" in claude.json()["detail"]
        async with store.reading():
            tokens = {t["name"]: t for t in await store.list_tokens()}
            granted = await store.noc_has_grant(f"token:{tokens['Vega']['id']}", "agent")
        assert tokens["Vega"]["role"] == "editor" and granted
        vega = _as(app, l1["token"])
        try:
            me = (await vega.get("/api/noc/agents/me")).json()
            assert me["agent"]["name"] == "Vega" and me["agent"]["entry"] is True
            up = await vega.post(f"/api/noc/agents/{lead['agent']['id']}/pause", json={})
            assert up.status_code == 403, "an agent pauses only the agents below it"
        finally:
            await vega.aclose()
        orion = _as(app, lead["token"])
        try:
            down = await orion.post(
                f"/api/noc/agents/{l1['agent']['id']}/pause", json={"reason": "drill"}
            )
            assert down.status_code == 200 and down.json()["agent"]["status"] == "paused"
        finally:
            await orion.aclose()
        rotated = await admin.post(f"/api/noc/agents/{l1['agent']['id']}/key")
        assert rotated.status_code == 200
        stale = _as(app, l1["token"])
        try:
            assert (await stale.get("/api/noc/agents/me")).status_code == 401
        finally:
            await stale.aclose()
        gone = await admin.post(f"/api/noc/agents/{lead['agent']['id']}/terminate")
        assert gone.status_code == 200
        after = (await editor.get(f"/api/noc/agents/{l1['agent']['id']}")).json()
        assert after["agent"]["reports_to"] == head["agent"]["id"], "its reports moved up"
    finally:
        await admin.aclose()
        await editor.aclose()


async def test_a_case_climbs_the_chain_to_the_people(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")
    try:
        head = await _hire(admin, "Atlas", role="head", tier=3)
        l1 = await _hire(admin, "Vega", entry=True, reports_to=head["agent"]["id"])
        sid = await _open_case(store)
        assigned = await admin.post(
            f"/api/noc/cases/{sid}/assign", json={"agent_id": l1["agent"]["id"]}
        )
        assert assigned.status_code == 200, assigned.text
        assert assigned.json()["agent"]["name"] == "Vega" and assigned.json()["tier"] == 1
        vega = _as(app, l1["token"])
        try:
            sideways = await vega.post(
                f"/api/noc/cases/{sid}/assign", json={"agent_id": head["agent"]["id"]}
            )
            assert sideways.status_code == 403, "an agent delegates only downward"
            assert (await vega.post(f"/api/noc/cases/{sid}/take")).status_code == 200
            up = await vega.post(f"/api/noc/cases/{sid}/escalate", json={"reason": "LOS persists"})
            assert up.status_code == 200, up.text
            body = up.json()
            assert body["agent"]["name"] == "Atlas" and body["tier"] == 3
            assert body["level_label"] == "With NOC Tier 3"
        finally:
            await vega.aclose()
        atlas = _as(app, head["token"])
        try:
            top = await atlas.post(
                f"/api/noc/cases/{sid}/escalate", json={"reason": "needs a person"}
            )
            assert top.status_code == 200, "routed to Atlas, it is Atlas's to pass on"
        finally:
            await atlas.aclose()
        done = top.json()
        assert done["with_people"] is True and done["agent"] is None
        assert done["level_label"] == "With the NOC team"
        assert done["status"] == "queue", "an agent had taken it: it is the people's to take"
        async with store.reading():
            cycles = await store.noc_case_cycles(sid)
            runs = await store.noc_agent_runs(int(head["agent"]["id"]))
        assert [c["reason"] for c in cycles][-1] == "escalated"
        assert runs == [], "an external agent is never woken"
    finally:
        await admin.aclose()
