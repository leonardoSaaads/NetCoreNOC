"""The MCP endpoint, driven by the official MCP SDK over real HTTP (ADR #466).

* an agent's key opens `/api/mcp`; the SDK negotiates, lists the tools its key may call — derived
  from the routes, each with the route's own input schema — and calls them: every call is the route,
  authorized, scoped and audited as the route is, and a step of the agent's run;
* a refusal is a tool result the model can read (`isError`, the route's sentence), never a crash;
* the transport's edges: a notification is 202, a batch or a cross-site Origin is refused, an
  unknown method is "method not found" (which is what makes a newer client fall back), no key is
  401 — and a paused agent's key opens nothing.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
import httpx2
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from netcorenoc.engine.noc import case_loop
from netcorenoc.store import Store

import authutil
import util

INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "t", "version": "1"},
    },
}


@contextlib.asynccontextmanager
async def _serving(app: Any) -> AsyncIterator[str]:
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="off")
    )
    task = asyncio.create_task(server.serve())
    try:
        while not server.started:
            await asyncio.sleep(0.02)
        port = server.servers[0].sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}/api/mcp"
    finally:
        server.should_exit = True
        await task


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


async def _agent(app: Any) -> tuple[int, str]:
    admin = await authutil.client_as(app, "admin")
    try:
        made = await admin.post(
            "/api/noc/agents",
            json={
                "name": "Vega",
                "role": "engineer",
                "adapter": "external",
                "instructions": "Work LOS cases.",
            },
        )
        assert made.status_code == 200, made.text
        return int(made.json()["agent"]["id"]), str(made.json()["token"])
    finally:
        await admin.aclose()


async def test_an_agent_works_a_case_through_the_sdk(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    agent_id, token = await _agent(app)
    sid = await _open_case(store)
    async with _serving(app) as url:
        http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=30)
        async with Client(streamable_http_client(url, http_client=http), mode="legacy") as client:
            listed = await client.list_tools()
            names = {t.name for t in listed.tools}
            assert {"noc_me", "noc_take_case", "noc_exec", "noc_create_playbook"} <= names
            assert "noc_hire_agent" not in names, "hiring is an admin's"
            take = next(t for t in listed.tools if t.name == "noc_take_case")
            assert take.input_schema["required"] == ["sid"]
            assert take.annotations is not None and take.annotations.idempotent_hint
            me = await client.call_tool("noc_me", {})
            assert not me.is_error and me.structured_content["agent"]["name"] == "Vega"
            taken = await client.call_tool("noc_take_case", {"sid": sid})
            assert not taken.is_error and taken.structured_content["status"] == "working"
            posted = await client.call_tool(
                "noc_post_to_case", {"sid": sid, "body": "Looking at it.", "visibility": "internal"}
            )
            assert not posted.is_error, posted.content
            refused = await client.call_tool(
                "noc_resolve_case", {"sid": sid, "cause": "carrier_circuit", "solution": "spliced"}
            )
            assert refused.is_error and "complete the case first" in getattr(
                refused.content[0], "text", ""
            )
            prompt = await client.get_prompt("noc_agent")
            assert "Work LOS cases." in getattr(prompt.messages[0].content, "text", "")
        await http.aclose()
    async with store.reading():
        runs = await store.noc_agent_runs(agent_id)
        steps = await store.noc_agent_run_steps(int(runs[0]["id"]))
        actions = [r["action"] for r in await store.audit_all()]
    assert runs[0]["trigger"] == "external" and runs[0]["status"] == "running"
    assert [s["name"] for s in steps] == [
        "noc_me",
        "noc_take_case",
        "noc_post_to_case",
        "noc_resolve_case",
    ]
    assert steps[-1]["ok"] is False
    assert {"noc.case.take", "noc.case.post"} <= set(actions), "each call is the route, audited"


async def test_the_transport_s_edges(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    agent_id, token = await _agent(app)
    raw = authutil.new_client(app)
    raw.headers["Authorization"] = f"Bearer {token}"
    try:
        assert (await raw.post("/api/mcp", json=INIT)).json()["result"][
            "protocolVersion"
        ] == "2025-06-18"
        note = await raw.post(
            "/api/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}
        )
        assert note.status_code == 202
        assert (await raw.post("/api/mcp", json=[INIT])).status_code == 400
        unknown = await raw.post(
            "/api/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "server/discover"}
        )
        assert unknown.json()["error"]["code"] == -32601
        cross = await raw.post("/api/mcp", json=INIT, headers={"Origin": "https://evil.example"})
        assert cross.status_code == 403
        nobody = authutil.new_client(app)
        try:
            assert (await nobody.post("/api/mcp", json=INIT)).status_code == 401
        finally:
            await nobody.aclose()
        admin = await authutil.client_as(app, "admin")
        try:
            assert (
                await admin.post(f"/api/noc/agents/{agent_id}/pause", json={})
            ).status_code == 200
        finally:
            await admin.aclose()
        assert (await raw.post("/api/mcp", json=INIT)).status_code == 401, (
            "a paused agent's key is inert"
        )
    finally:
        await raw.aclose()
    assert isinstance(httpx.Response, type)


async def test_a_tool_call_is_one_request_to_the_rate_limit(store: Store) -> None:
    """The owner's test round (ADR #469): an agent working a case calls tools in bursts, and each
    call was counted twice — the MCP request, then the route it reached in process — so a 30-request
    bucket ran dry mid-case. Fifteen calls in a 24-request bucket: twice-counted they are 30 and the
    last ones are refused; counted once, every one is answered."""
    _engine, _queue, app = await authutil.make_env(store, rate_capacity=24.0, rate_refill=0.001)
    _agent_id, token = await _agent(app)
    raw = authutil.new_client(app)
    raw.headers["Authorization"] = f"Bearer {token}"
    try:
        assert (await raw.post("/api/mcp", json=INIT)).status_code == 200
        for n in range(15):
            call = {
                "jsonrpc": "2.0",
                "id": n + 2,
                "method": "tools/call",
                "params": {"name": "noc_me", "arguments": {}},
            }
            answer = await raw.post("/api/mcp", json=call)
            assert answer.status_code == 200, f"call {n + 1}: {answer.status_code}"
            assert not answer.json()["result"].get("isError"), answer.json()
    finally:
        await raw.aclose()
