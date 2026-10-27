"""The agent loop end to end: routing, the climb, a signed wake-up, and the appliance running an
agent through the Anthropic API (ADR #467).

* a queued case goes to the entry agent with the fewest cases, at Tier 1; one not concluded within
  the agent's escalation time climbs to its manager, and past the top to the NOC team;
* a `webhook` agent is woken by a POST signed with the key it was given once, and its run stays
  open until it reports it finished;
* a `claude` agent is run here: its tools are the MCP tools its key may call, each a route called
  with its own key — it takes the case, and the run records each step, its summary and its cost.
  The model is a scripted stand-in for the SDK client: no network, no key spent.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace
from typing import Any, ClassVar

from netcorenoc.api import agent_runtime
from netcorenoc.engine.noc import agent_loop, case_loop, webhook
from netcorenoc.store import Store

import authutil
import util

STEP = {"username": "adm", "password": authutil.PW}


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


async def _hire(app: Any, **body: Any) -> dict[str, Any]:
    admin = await authutil.client_as(app, "admin")
    try:
        made = await admin.post("/api/noc/agents", json={"role": "engineer", **body})
        assert made.status_code == 200, made.text
        return dict(made.json())
    finally:
        await admin.aclose()


async def test_routing_and_the_climb(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    head = await _hire(app, name="Atlas", role="head", tier=3, adapter="external")
    await _hire(
        app,
        name="Vega",
        entry=True,
        adapter="external",
        escalate_after_s=60,
        reports_to=head["agent"]["id"],
    )
    sid = await _open_case(store)
    assert (await agent_loop.tick(store))["routed"] == 1
    async with store.reading():
        case = await store.noc_case(sid)
    assert case is not None and case["agent_name"] == "Vega" and case["tier"] == 1
    async with store.lock:
        await store.conn.execute(
            "UPDATE noc_case SET assigned_at = assigned_at - interval '2 minutes' "
            "WHERE situation_id = ?",
            (sid,),
        )
        await store.commit()
    assert (await agent_loop.tick(store))["escalated"] == 1
    async with store.reading():
        case = await store.noc_case(sid)
    assert case is not None and case["agent_name"] == "Atlas" and case["tier"] == 3
    async with store.lock:
        await store.conn.execute(
            "UPDATE noc_case SET assigned_at = assigned_at - interval '3 hours' "
            "WHERE situation_id = ?",
            (sid,),
        )
        await store.commit()
    await agent_loop.tick(store)
    async with store.reading():
        case = await store.noc_case(sid)
        posts = [p["body"] for p in await store.noc_posts(sid)]
    assert case is not None and case["with_people"] and case["agent_id"] is None
    assert any("Escalated to the NOC team" in p for p in posts)


class _Receiver(BaseHTTPRequestHandler):
    seen: ClassVar[list[tuple[str, bytes]]] = []

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers["Content-Length"]))
        _Receiver.seen.append((str(self.headers.get(webhook.HEADER)), body))  # any case
        self.send_response(202)
        self.end_headers()

    def log_message(self, *_args: Any) -> None:
        return


async def test_a_webhook_agent_is_woken_with_a_signed_post(store: Store) -> None:
    server = HTTPServer(("127.0.0.1", 0), _Receiver)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        _engine, _queue, app = await authutil.make_env(store)
        url = f"http://127.0.0.1:{server.server_address[1]}/wake"
        hired = await _hire(
            app, name="Orion", entry=True, adapter="webhook", adapter_config={"url": url}
        )
        sid = await _open_case(store)
        await agent_loop.tick(store)
        running: dict[int, asyncio.Task[None]] = {}
        assert await agent_loop.start_ready(store, running, None) == 1
        await asyncio.gather(*running.values())
        signature, body = _Receiver.seen[-1]
        assert webhook.verify(hired["webhook_key"], body, signature, time.time())
        payload = json.loads(body)
        assert payload["case"]["id"] == sid and payload["trigger"] == "assignment"
        assert payload["mcp_path"] == "/api/mcp" and "token" not in json.dumps(payload)
        agent = authutil.new_client(app)
        agent.headers["Authorization"] = f"Bearer {hired['token']}"
        try:
            done = await agent.post(
                f"/api/noc/runs/{payload['run_id']}/finish", json={"summary": "took a look"}
            )
        finally:
            await agent.aclose()
        assert done.status_code == 200 and done.json()["run"]["status"] == "succeeded"
    finally:
        server.shutdown()


class _Model:
    """A scripted stand-in for `AsyncAnthropic`: takes the case, then ends its turn."""

    def __init__(self, sid: int) -> None:
        self.calls: list[dict[str, Any]] = []
        self.sid = sid
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        usage = SimpleNamespace(input_tokens=1000, output_tokens=200, cache_creation_input_tokens=0)
        if len(self.calls) == 1:
            use = SimpleNamespace(
                type="tool_use", id="t1", name="noc_take_case", input={"sid": self.sid}
            )
            return SimpleNamespace(content=[use], stop_reason="tool_use", usage=usage)
        text = SimpleNamespace(type="text", text="Took the case; LOS confirmed, provider engaged.")
        return SimpleNamespace(content=[text], stop_reason="end_turn", usage=usage)


async def test_the_appliance_runs_a_claude_agent(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")
    try:
        made = await admin.post(
            "/api/noc/secrets",
            json={
                "name": "anthropic",
                "kind": "api_token",
                "access_level": "view_only",
                "host": "api.anthropic.com",
                "monitor": False,
                "fields": {"token": "sk-ant-test"},
            },
        )
        secret_id = int(made.json()["id"])
        on = await admin.post(
            f"/api/noc/secrets/{secret_id}/agents", json={"enabled": True, "stepup": STEP}
        )
        assert on.status_code == 200, on.text
    finally:
        await admin.aclose()
    hired = await _hire(
        app,
        name="Lyra",
        entry=True,
        adapter="claude",
        adapter_config={"secret_id": secret_id, "effort": "high"},
        budget_month_cents=500,
    )
    assert hired["token"] is None, "the appliance keeps a claude agent's key sealed"
    sid = await _open_case(store)
    await agent_loop.tick(store)
    model = _Model(sid)
    keys: list[str] = []

    def client(key: str) -> _Model:
        keys.append(key)
        return model

    runner = agent_runtime.claude_runner(app, store, client=client)
    running: dict[int, asyncio.Task[None]] = {}
    assert await agent_loop.start_ready(store, running, runner) == 1
    await asyncio.gather(*running.values())
    first = model.calls[0]
    assert keys == ["sk-ant-test"] and first["model"] == "claude-opus-5-5"
    assert first["fallbacks"] == "default" and first["thinking"] == {"type": "adaptive"}
    assert first["output_config"] == {"effort": "high"}
    assert "noc_take_case" in {t["name"] for t in first["tools"]}
    assert "noc_hire_agent" not in {t["name"] for t in first["tools"]}
    async with store.reading():
        case = await store.noc_case(sid)
        runs = await store.noc_agent_runs(int(hired["agent"]["id"]))
        steps = await store.noc_agent_run_steps(int(runs[0]["id"]))
        agent = await store.noc_agent(int(hired["agent"]["id"]))
    assert case is not None and case["status"] == "working" and case["owner_name"] == "Lyra"
    assert runs[0]["status"] == "succeeded" and "provider engaged" in runs[0]["summary"]
    assert float(runs[0]["cost_cents"]) > 0 and runs[0]["output_tokens"] == 400
    assert [s["name"] for s in steps] == ["assignment", "noc_take_case", "summary"]
    assert agent is not None and agent["status"] == "idle"
