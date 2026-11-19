"""Routines (ADR #492): a playbook is a customer's and a tier's, and an agent runs one on demand.

Against the same in-process SSH device as `test_noc_automation.py`:

* **tier** — a tier-1 agent never learns a tier-2 routine exists (the list leaves it out, the read
  answers 404 as for one that does not exist, the run likewise) and writes routines at its own tier
  or below; a tier-2 agent sees and runs both; a token holding the agent grant that is no hired
  agent is held to the least tier;
* **customer** — a routine for one customer applies only to that customer's cases, and asking with
  `situation_id` lists only the ones that apply; an unknown customer is a 400;
* **on demand** — the routine's checks run on the case's element and come back with the device's
  words marked untrusted; the conclusion is advice: the case is exactly as it was, the run ends
  `advised` under the one who asked, and every check is a `noc.exec` row under that agent;
* **viewers** never reach a routine at all.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import test_noc_automation as automation

from netcorenoc.store import Store

import authutil
import util

PASSWORD, PLAYBOOK, STEP = automation.PASSWORD, automation.PLAYBOOK, automation.STEP
#: The in-process SSH device of `test_noc_automation.py`, answering as its `ANSWER` says.
device_port = automation.device_port


async def _estate(store: Store, app: Any, port: int) -> dict[str, Any]:
    """A customer, a credential agents may use for the device, and two hired agents (tiers 1, 2)."""
    async with store.lock:
        acme = await store.create_organization("Acme Telecom", "acme", time.time())
        other = await store.create_organization("Globex", "globex", time.time())
        await store.commit()
    admin = await authutil.client_as(app, "admin")
    try:
        made = await admin.post(
            "/api/noc/secrets",
            json={
                "name": "device ssh",
                "kind": "ssh_password",
                "access_level": "view_only",
                "host": "127.0.0.1",
                "port": port,
                "monitor": False,
                "fields": {"username": "noc", "password": PASSWORD},
            },
        )
        assert made.status_code == 200, made.text
        on = await admin.post(
            f"/api/noc/secrets/{made.json()['id']}/agents",
            json={"enabled": True, "stepup": STEP},
        )
        assert on.status_code == 200, on.text
        keys = {}
        for name, tier in (("Iris", 1), ("Orion", 2)):
            hired = await admin.post(
                "/api/noc/agents",
                json={"name": name, "role": "engineer", "adapter": "external", "tier": tier},
            )
            assert hired.status_code == 200, hired.text
            keys[tier] = str(hired.json()["token"])
    finally:
        await admin.aclose()
    return {"acme": acme, "other": other, "keys": keys}


def _as(app: Any, key: str) -> httpx.AsyncClient:
    client = authutil.new_client(app)
    client.headers["Authorization"] = f"Bearer {key}"
    return client


async def _case_of(store: Store, organization_id: int) -> int:
    """A case on the device, whose element belongs to `organization_id`."""
    now = time.time()
    async with store.lock:
        sid = await store.create_situation(now, None)
        alarm = await store.ingest(util.event(device="127.0.0.1", ts=now))
        await store.conn.execute(
            "UPDATE ne SET organization_id = ? WHERE host(ip) = '127.0.0.1'", (organization_id,)
        )
        await store.add_alarm_to_situation(sid, alarm.alarm_id)
        await store.promote_situation(sid, now, "promote")
        await store.noc_case_open(sid, now)
        await store.commit()
    return sid


def _routine(name: str) -> dict[str, Any]:
    return {**PLAYBOOK, "name": name}


async def test_a_routine_above_an_agents_tier_does_not_exist_for_it(
    store: Store, device_port: int
) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    estate = await _estate(store, app, device_port)
    lead = await authutil.client_as(app, "admin")
    tier1, tier2 = _as(app, estate["keys"][1]), _as(app, estate["keys"][2])
    viewer = await authutil.client_as(app, "viewer")
    try:
        low = await lead.post("/api/noc/playbooks", json={"document": _routine("Any agent")})
        high = await lead.post(
            "/api/noc/playbooks",
            json={"document": _routine("Core routers"), "customer_id": estate["acme"], "tier": 2},
        )
        assert low.status_code == 200 and high.status_code == 200, high.text
        assert high.json()["customer"] == "Acme Telecom" and high.json()["tier"] == 2
        hid = high.json()["id"]

        seen = (await tier1.get("/api/noc/playbooks")).json()
        assert [p["name"] for p in seen["playbooks"]] == ["Any agent"]
        assert seen["tiers"] == [1], "a tier-1 agent is offered only its own tier"
        assert (await tier1.get(f"/api/noc/playbooks/{hid}")).status_code == 404
        hidden = await tier1.post(f"/api/noc/playbooks/{hid}/run", json={"situation_id": 1})
        assert hidden.status_code == 404 and hidden.json()["detail"] == "no such routine"
        above = await tier1.post(
            "/api/noc/playbooks", json={"document": _routine("Mine"), "tier": 2}
        )
        assert above.status_code == 403 and "tier 1 or below" in above.json()["detail"]

        both = (await tier2.get("/api/noc/playbooks")).json()
        assert {p["name"] for p in both["playbooks"]} == {"Any agent", "Core routers"}
        assert (await tier2.get(f"/api/noc/playbooks/{hid}")).status_code == 200

        unknown = await lead.post(
            "/api/noc/playbooks", json={"document": _routine("Nobody's"), "customer_id": 999999}
        )
        assert unknown.status_code == 400 and "no such customer" in unknown.json()["detail"]
        # ADR #493: a number no column holds is a request that cannot be served, never a 500.
        huge = await lead.post(
            "/api/noc/playbooks", json={"document": _routine("Huge"), "customer_id": 2**40}
        )
        assert huge.status_code == 422 and "out of range" in huge.json()["detail"]
        assert (await lead.get(f"/api/noc/customers/{2**40}")).status_code == 422

        # A viewer is not a lead: no routine, no list, no run.
        assert (await viewer.get("/api/noc/playbooks")).status_code == 403
        assert (await viewer.get(f"/api/noc/playbooks/{hid}")).status_code == 403
        ran = await viewer.post(f"/api/noc/playbooks/{hid}/run", json={"situation_id": 1})
        assert ran.status_code == 403
    finally:
        for client in (lead, tier1, tier2, viewer):
            await client.aclose()


async def test_a_token_that_is_no_hired_agent_is_held_to_the_least_tier(
    store: Store, device_port: int
) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    await _estate(store, app, device_port)
    value = await authutil.make_token(store, "outside", "editor")
    async with store.lock:
        token_id = next(int(t["id"]) for t in await store.list_tokens() if t["name"] == "outside")
        await store.noc_set_grant(f"token:{token_id}", "agent", True, 0.0, "test")
        await store.commit()
    lead = await authutil.client_as(app, "admin")
    outside = _as(app, value)
    try:
        made = await lead.post(
            "/api/noc/playbooks", json={"document": _routine("Tier three"), "tier": 3}
        )
        assert made.status_code == 200, made.text
        listed = (await outside.get("/api/noc/playbooks")).json()
        assert listed["playbooks"] == [] and listed["tiers"] == [1]
        wrote = await outside.post(
            "/api/noc/playbooks", json={"document": _routine("Mine"), "tier": 4}
        )
        assert wrote.status_code == 403
    finally:
        await lead.aclose()
        await outside.aclose()


async def test_an_agent_runs_its_customers_routine_and_gets_advice_not_an_action(
    store: Store, device_port: int
) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    estate = await _estate(store, app, device_port)
    lead = await authutil.client_as(app, "admin")
    agent = _as(app, estate["keys"][1])
    try:
        mine = await lead.post(
            "/api/noc/playbooks",
            json={"document": _routine("Acme: alarm gone?"), "customer_id": estate["acme"]},
        )
        theirs = await lead.post(
            "/api/noc/playbooks",
            json={"document": _routine("Globex only"), "customer_id": estate["other"]},
        )
        assert mine.status_code == 200 and theirs.status_code == 200
        sid = await _case_of(store, estate["acme"])
        async with store.reading():
            before = await store.noc_case(sid)
        assert before is not None

        applies = (await agent.get(f"/api/noc/playbooks?situation_id={sid}")).json()
        assert [p["name"] for p in applies["playbooks"]] == ["Acme: alarm gone?"]
        refused = await agent.post(
            f"/api/noc/playbooks/{theirs.json()['id']}/run", json={"situation_id": sid}
        )
        assert refused.status_code == 409 and "another customer" in refused.json()["detail"]
        missing = await agent.post(
            f"/api/noc/playbooks/{mine.json()['id']}/run", json={"situation_id": 999999}
        )
        assert missing.status_code == 404

        ran = await agent.post(
            f"/api/noc/playbooks/{mine.json()['id']}/run", json={"situation_id": sid}
        )
        assert ran.status_code == 200, ran.text
        body = ran.json()
        assert body["advice"]["end"] == "resolve" and body["advice"]["clear_alarms"]
        assert body["routine"]["name"] == "Acme: alarm gone?"
        assert body["steps"][0]["output"] == {"untrusted": True, "text": "No active alarms\n"}
        assert PASSWORD not in ran.text, "a run reports the device's words, never the credential"

        async with store.reading():
            after = await store.noc_case(sid)
            runs = await store.noc_runs(situation_id=sid)
            audit = [(r["action"], r["actor"]) for r in await store.audit_all()]
        assert after is not None and after["status"] == before["status"]
        assert after["resolution"] is None, "advice is never applied"
        assert [(r["outcome"], r["started_by"]) for r in runs] == [("advised", "Iris")]
        acted = {who for action, who in audit if action in ("noc.exec", "noc.playbook.run")}
        assert len(acted) == 1 and acted.pop().startswith("token:"), "under the agent who asked"

        detail = (await lead.get(f"/api/noc/playbooks/{mine.json()['id']}")).json()
        assert detail["recent_runs"][0]["outcome"] == "advised"
        assert detail["recent_runs"][0]["started_by"] == "Iris"
    finally:
        await lead.aclose()
        await agent.aclose()


async def test_a_routine_that_is_off_or_a_case_that_is_over_is_not_run(
    store: Store, device_port: int
) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    estate = await _estate(store, app, device_port)
    lead = await authutil.client_as(app, "admin")
    agent = _as(app, estate["keys"][2])
    try:
        made = await lead.post("/api/noc/playbooks", json={"document": _routine("Gone")})
        rid = made.json()["id"]
        sid = await _case_of(store, estate["acme"])
        off = await lead.post(f"/api/noc/playbooks/{rid}/enabled", json={"enabled": False})
        assert off.status_code == 200
        assert (await agent.get(f"/api/noc/playbooks?situation_id={sid}")).json()["playbooks"] == []
        stopped = await agent.post(f"/api/noc/playbooks/{rid}/run", json={"situation_id": sid})
        assert stopped.status_code == 409 and "switched off" in stopped.json()["detail"]
        await lead.post(f"/api/noc/playbooks/{rid}/enabled", json={"enabled": True})
        async with store.lock:
            await store.conn.execute(
                "UPDATE noc_case SET status = 'resolved' WHERE situation_id = ?", (sid,)
            )
            await store.commit()
        over = await agent.post(f"/api/noc/playbooks/{rid}/run", json={"situation_id": sid})
        assert over.status_code == 409 and "over" in over.json()["detail"]
    finally:
        await lead.aclose()
        await agent.aclose()
