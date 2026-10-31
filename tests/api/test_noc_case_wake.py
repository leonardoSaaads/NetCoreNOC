"""A case that waits wakes its agent again, its restore clock paused meanwhile (ADR #478).

What a new install needs of its agents with nothing typed, each driven through the real routes:

* a case back in the queue — a person posted while it was pending — goes back to its agent, woken,
  its escalation clock started over; it does not climb at once for the time it waited;
* an agent with no credential for the device sets the case `pending_connectivity`; the moment a
  custodian lets the agents use a credential for that device, the case is back with its agent
  (`access`) — an AI provider's key wakes nothing, and is never offered to an agent as a device's;
* the restore clock pauses while the case is pending and its due time moves on by the wait;
* the customer update is sent to the customer: refused while incomplete, then a post everyone
  who may read the case reads, an event, and an audit row;
* the agent's first message names the case's devices, whether it may reach each, and why it is back.
"""

from __future__ import annotations

import json
import time
from typing import Any

from netcorenoc.api import agent_tools, mcp, mcp_tools
from netcorenoc.engine.noc import agent_loop, case_loop, kpi, overview, providers
from netcorenoc.store import Store

import authutil
import util

STEP = {"username": "adm", "password": authutil.PW}
DEVICE = "10.0.0.7"
COMPLETE = {
    "title": "LOS on ETH10G-3-1-1",
    "description": "Loss of signal on the client side of port 3 at 10.0.0.7.",
    "executive_summary": "One client port down at the Sao Paulo hub",
    "current_analysis": "The device is not reachable by the NOC yet; the alarms alone say LOS.",
    "noc_next_step": "Read the port's optics once access is granted.",
    "customer_next_step": "Grant the NOC access to the device.",
}


async def _open_case(store: Store) -> int:
    now = time.time()
    async with store.lock:
        sid = await store.create_situation(now, None)
        alarm = await store.ingest(util.event(device=DEVICE, ts=now))
        await store.add_alarm_to_situation(sid, alarm.alarm_id)
        await store.promote_situation(sid, now, "promote")
        await store.commit()
    await case_loop.tick(store)
    return sid


async def _hire_entry(app: Any) -> dict[str, Any]:
    admin = await authutil.client_as(app, "admin")
    try:
        made = await admin.post(
            "/api/noc/agents",
            json={
                "role": "engineer",
                "name": "Vega",
                "entry": True,
                "adapter": "webhook",
                "adapter_config": {"url": "http://127.0.0.1:9/wake"},
            },
        )
        assert made.status_code == 200, made.text
        return dict(made.json())
    finally:
        await admin.aclose()


async def _queued_runs(store: Store, agent_id: int) -> list[dict[str, Any]]:
    async with store.reading():
        cur = await store.conn.execute(
            "SELECT trigger, situation_id FROM noc_agent_run WHERE agent_id = ? "
            "AND status = 'queued' ORDER BY id",
            (agent_id,),
        )
        return [dict(r) for r in await cur.fetchall()]


async def _drop_runs(store: Store, agent_id: int) -> None:
    async with store.lock:
        await store.conn.execute(
            "UPDATE noc_agent_run SET status = 'cancelled', ended_at = now() "
            "WHERE agent_id = ? AND status = 'queued'",
            (agent_id,),
        )
        await store.commit()


async def test_access_granted_wakes_the_case_that_waited_for_it(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    hired = await _hire_entry(app)
    aid = int(hired["agent"]["id"])
    sid = await _open_case(store)
    assert (await agent_loop.tick(store))["routed"] == 1
    agent = authutil.new_client(app)
    agent.headers["Authorization"] = f"Bearer {hired['token']}"
    admin = await authutil.client_as(app, "admin")
    try:
        # With nothing in the vault, the agent may lease nothing: it says so and waits.
        assert (await agent.get("/api/noc/agent")).json()["secrets"] == []
        assert (await agent.post(f"/api/noc/cases/{sid}/take")).status_code == 200
        assert (await agent.post(f"/api/noc/cases/{sid}", json=COMPLETE)).status_code == 200
        waits = await agent.post(
            f"/api/noc/cases/{sid}/status", json={"status": "pending_connectivity"}
        )
        assert waits.status_code == 200, waits.text
        assert waits.json()["clocks"]["restore"]["state"] == "paused"
        await _drop_runs(store, aid)
        async with store.lock:
            # The wait has lasted an hour: the escalation time is long past.
            await store.conn.execute(
                "UPDATE noc_case SET assigned_at = assigned_at - interval '1 hour', "
                "paused_at = paused_at - interval '1 hour' WHERE situation_id = ?",
                (sid,),
            )
            await store.commit()

        # An AI provider's key, switched on for agents, is no device's: nothing wakes.
        ollama = await admin.post(
            "/api/noc/secrets",
            json={
                "name": "Ollama",
                "kind": providers.PROVIDERS["ollama"].kind,
                "access_level": "view_only",
                "host": DEVICE,
                "port": 11434,
                "protocol_version": "HTTP",
                "fields": {},
            },
        )
        assert ollama.status_code == 200, ollama.text
        on = await admin.post(
            f"/api/noc/secrets/{ollama.json()['id']}/agents",
            json={"enabled": True, "stepup": STEP},
        )
        assert on.status_code == 200 and on.json()["cases_woken"] == []
        assert (await agent.get("/api/noc/agent")).json()["secrets"] == [], "a key is no device's"
        refused = await agent.post(f"/api/noc/secrets/{ollama.json()['id']}/lease", json={})
        assert refused.status_code == 404

        # The device's credential, switched on for agents: the case is back with Vega, woken.
        ssh = await admin.post(
            "/api/noc/secrets",
            json={
                "name": "hub ssh",
                "kind": "ssh_password",
                "access_level": "diagnostic",
                "host": DEVICE,
                "fields": {"username": "noc", "password": "Hub-Passw0rd-2026"},
            },
        )
        on = await admin.post(
            f"/api/noc/secrets/{ssh.json()['id']}/agents", json={"enabled": True, "stepup": STEP}
        )
        assert on.status_code == 200 and on.json()["cases_woken"] == [sid]
        offered = (await agent.get("/api/noc/agent")).json()["secrets"]
        assert [s["host"] for s in offered] == [DEVICE]
        async with store.reading():
            case = await store.noc_case(sid)
            cycles = await store.noc_case_cycles(sid)
            posts = [p["body"] for p in await store.noc_posts(sid)]
        assert case is not None and case["status"] == "queue" and case["agent_name"] == "Vega"
        assert time.time() - float(case["assigned_at"]) < 60, "its escalation clock starts over"
        assert case["paused_at"] is None and float(case["paused_s"]) >= 3600 - 5
        assert cycles[-1]["reason"] == "access"
        assert any("Vega is woken" in p and DEVICE in p for p in posts)
        assert await _queued_runs(store, aid) == [{"trigger": "assignment", "situation_id": sid}]
        assert (await agent_loop.tick(store))["escalated"] == 0, "no climb for the wait"

        # Its restore clock does not charge the hour it waited.
        restore = kpi.restore(case, time.time())
        assert restore["state"] == "running" and restore["paused_s"] >= 3600 - 5
        assert restore["elapsed_s"] < 600

        # What Vega is told first: the device, that it may now reach it, and why it is back.
        run = {"id": 1, "trigger": "assignment", "situation_id": sid}
        brief = await agent_tools.briefing(store, run)
        assert brief is not None
        assert brief["devices"][0]["address"] == DEVICE and brief["devices"][0]["credential"]
        text = agent_tools.first_message(run, brief)
        assert "Why it is back: The agents were given access" in text
        assert f"{DEVICE} (Ciena)" in text, "the vendor, from its traps' enterprise arc"
        assert "a credential you may lease covers it" in text
        assert f"get_situation {sid}" in text
    finally:
        await agent.aclose()
        await admin.aclose()


async def test_a_person_posting_on_a_pending_case_wakes_its_agent(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    hired = await _hire_entry(app)
    aid = int(hired["agent"]["id"])
    sid = await _open_case(store)
    await agent_loop.tick(store)
    agent = authutil.new_client(app)
    agent.headers["Authorization"] = f"Bearer {hired['token']}"
    viewer = await authutil.client_as(app, "viewer")
    try:
        await agent.post(f"/api/noc/cases/{sid}/take")
        await agent.post(f"/api/noc/cases/{sid}", json=COMPLETE)
        pend = await agent.post(f"/api/noc/cases/{sid}/status", json={"status": "pending_customer"})
        assert pend.status_code == 200, pend.text
        await _drop_runs(store, aid)
        said = await viewer.post(
            f"/api/noc/cases/{sid}/feed", json={"body": "Access granted.", "visibility": "all"}
        )
        assert said.status_code == 200 and said.json()["requeued"] is True
        assert await _queued_runs(store, aid) == [{"trigger": "assignment", "situation_id": sid}]
        brief = await agent_tools.briefing(
            store, {"id": 2, "trigger": "assignment", "situation_id": sid}
        )
        assert brief is not None and brief["reason"] == "customer_post"
        assert not brief["devices"][0]["credential"]
        text = agent_tools.first_message({"id": 2, "trigger": "assignment"}, brief)
        assert "A person posted on the feed" in text and "no credential you may lease" in text
    finally:
        await agent.aclose()
        await viewer.aclose()


async def test_the_customer_update_is_sent_to_the_customer(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    sid = await _open_case(store)
    editor = await authutil.client_as(app, "editor")
    viewer = await authutil.client_as(app, "viewer")
    send = f"/api/noc/cases/{sid}/customer-update"
    try:
        assert (await editor.post(f"/api/noc/cases/{sid}/take")).status_code == 200
        incomplete = await editor.post(send)
        assert incomplete.status_code == 409
        assert "complete the customer update first" in incomplete.json()["detail"]
        assert (await viewer.post(send)).status_code == 403, "sending is the NOC's"
        saved = await editor.post(
            f"/api/noc/cases/{sid}", json={**COMPLETE, "next_update_at": time.time() + 3600}
        )
        stale = await editor.post(send, json={"version": saved.json()["version"] - 1})
        assert stale.status_code == 409 and "changed since you read it" in stale.json()["detail"]
        sent = await editor.post(send, json={"version": saved.json()["version"]})
        assert sent.status_code == 200, sent.text
        assert sent.json() == {"id": sent.json()["id"], "sent": True, "email": False}
        feed = (await viewer.get(f"/api/noc/cases/{sid}/feed")).json()["posts"]
        update = next(p for p in feed if p["id"] == sent.json()["id"])
        assert update["visibility"] == "all"
        assert update["body"].startswith("Customer update — LOS on ETH10G-3-1-1")
        assert "Customer next step:\nGrant the NOC access to the device." in update["body"]
        assert update["body"].rstrip().endswith(" UTC") and "\nNext update: " in update["body"]
        async with store.reading():
            posts = [p["body"] for p in await store.noc_posts(sid)]
            cur = await store.conn.execute(
                "SELECT details FROM audit_log WHERE action = 'noc.case.send_update'"
            )
            rows = [json.loads(r[0]) for r in await cur.fetchall()]
        assert any("sent the customer update to the customer" in p for p in posts)
        assert rows == [{"post": sent.json()["id"], "version": saved.json()["version"]}]
    finally:
        await editor.aclose()
        await viewer.aclose()
    names = {tool.name for tool in mcp_tools.TOOLS}
    assert "noc_send_customer_update" in names


def test_the_agents_are_told_what_to_do_with_nothing_typed() -> None:
    for step in (
        "get_situation",
        "pending_connectivity",
        "noc_send_customer_update",
        "pending_customer",
        "show xcvr status port",
    ):
        assert step in mcp.GUIDE, step
    assert mcp.GUIDE_END[0] in mcp.GUIDE
    assert "Pending connectivity" in mcp.DEFAULT_INSTRUCTIONS
    prompt = agent_tools.system_prompt({"instructions": ""})
    assert prompt.endswith("13. End your turn: what you did and whether it worked.\n")


def test_the_overview_says_what_the_noc_still_lacks() -> None:
    agent = {"status": "idle", "entry": False}
    ai = {"agent_access": True, "kind": providers.PROVIDERS["ollama"].kind}
    ssh = {"agent_access": False, "kind": "ssh_password"}
    steps = overview.setup([agent], [ai, ssh])
    assert steps == {
        "ai_provider": True,
        "agent": True,
        "entry": False,
        "device_access": False,
        "ready": False,
    }
    ready = overview.setup([{**agent, "entry": True}], [ai, {**ssh, "agent_access": True}])
    assert ready["ready"] is True


def test_a_local_model_server_at_loopback_in_a_container_is_explained(monkeypatch: Any) -> None:
    monkeypatch.setattr(providers, "in_container", lambda: True)
    assert "host.docker.internal" in providers.loopback_hint("127.0.0.1")
    assert providers.loopback_hint("10.0.0.9") == ""
    monkeypatch.setattr(providers, "in_container", lambda: False)
    assert providers.loopback_hint("127.0.0.1") == ""
