"""`POST /api/mcp` — the Model Context Protocol endpoint agents reach the NOC through (ADR #466).

Behind the same perimeter as every route: the caller's Bearer token (or session) is checked by
`perimeter.security` before anything here runs, and `mcp.use` is the door's own capability. Every
tool is then a route called in process with the caller's own credentials (`api/mcp.py`), so it
is authorized, scoped and audited as that route is. The protocol is `api/mcp.py`'s.

Two things are this endpoint's own:

* **the Origin check** the specification asks for (DNS rebinding): a browser request from another
  site is refused before it is read;
* **the run record**: each tool an agent calls is a step of its current run — the one the
  appliance woke it for, or, when it came on its own, a run of its own, ended after five quiet
  minutes — with what it asked (no lease, token or key) and how the route answered.
"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse

from netcorenoc.api import asgi_call, mcp, mcp_tools
from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.crosscutting import auth

#: The request headers a tool call carries on: who is asking, and what a cookie needs to pass.
FORWARD = ("authorization", "cookie", "origin", "host", "x-netcorenoc-client")
PROMPT = {
    "name": "noc_agent",
    "title": "Your instructions as a NOC agent",
    "description": "What your manager wrote for you, then how the NOC works here.",
    "arguments": [],
}


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the MCP endpoint on `app`."""
    store, security, write_txn = ctx.store, ctx.security, ctx.write_txn
    route = DeclaredRoutes(app)
    built: dict[str, mcp_tools.Tool] = {}

    def tools() -> dict[str, mcp_tools.Tool]:
        if not built:
            built.update(mcp_tools.build(app.openapi()))
        return built

    def forwarded(request: Request) -> list[tuple[str, str]]:
        return [(k, request.headers[k]) for k in FORWARD if k in request.headers]

    def client(request: Request) -> tuple[str, int]:
        return (request.client.host, request.client.port) if request.client else ("unknown", 0)

    async def record(
        principal: auth.Principal, tool: mcp_tools.Tool, arguments: dict[str, Any], answer: Any
    ) -> None:
        """The call as a step of the agent's run; the caller holds the write transaction."""
        if not principal.is_token or principal.token_id is None or tool.name == "noc_finish_run":
            return
        now = time.time()
        agent = await store.noc_agent_by_token(principal.token_id)
        if agent is None:
            return
        run = await store.noc_agent_run_active(int(agent["id"]))
        run_id = (
            int(run["id"]) if run else await store.noc_agent_run_external(int(agent["id"]), now)
        )
        outcome = str(answer.status) + ("" if answer.ok else f": {answer.detail()[:300]}")
        await store.noc_agent_run_step(
            run_id, "tool", tool.name, answer.ok, f"{mcp.redact(arguments)} → {outcome}", now
        )

    async def call_tool(
        request: Request, params: dict[str, Any]
    ) -> tuple[dict[str, Any], tuple[mcp_tools.Tool, dict[str, Any], Any] | None]:
        """The tool result, and the step to record (the handler records it)."""
        name, arguments = params.get("name"), params.get("arguments") or {}
        tool = tools().get(str(name))
        if tool is None or not isinstance(arguments, dict):
            raise ValueError(f"no tool {name!r}" if tool is None else "arguments: an object")
        if tool.capability not in request.state.capabilities:
            text = f"403: your key may not call {tool.name} ({tool.capability})"
            return {"content": [{"type": "text", "text": text}], "isError": True}, None
        answer, out = await mcp.run_tool(app, tool, arguments, forwarded(request), client(request))
        return out, (tool, arguments, answer)

    async def instructions(request: Request) -> str:
        me = await asgi_call.call(
            app, "GET", "/api/noc/agents/me", headers=forwarded(request), client=client(request)
        )
        own = (
            me.body["agent"].get("instructions", "") if me.ok and isinstance(me.body, dict) else ""
        )
        return (own.strip() + "\n\n" if own.strip() else "") + mcp.GUIDE

    async def answer(
        request: Request, method: str, rid: Any, params: dict[str, Any]
    ) -> tuple[dict[str, Any], tuple[mcp_tools.Tool, dict[str, Any], Any] | None]:
        if method == "tools/call":
            try:
                out, step = await call_tool(request, params)
            except ValueError as exc:
                return mcp.error(rid, mcp.INVALID_PARAMS, str(exc)), None
            return mcp.result(rid, out), step
        return await reply(request, method, rid, params), None

    async def reply(
        request: Request, method: str, rid: Any, params: dict[str, Any]
    ) -> dict[str, Any]:
        if method == "initialize":
            return mcp.result(rid, mcp.initialize(params))
        if method == "ping":
            return mcp.result(rid, {})
        if method == "tools/list":
            allowed = request.state.capabilities
            listed = [t.listing() for t in tools().values() if t.capability in allowed]
            return mcp.result(rid, {"tools": listed})
        if method == "prompts/list":
            return mcp.result(rid, {"prompts": [PROMPT]})
        if method == "prompts/get":
            if params.get("name") != PROMPT["name"]:
                return mcp.error(rid, mcp.INVALID_PARAMS, f"no prompt {params.get('name')!r}")
            text = await instructions(request)
            message = {"role": "user", "content": {"type": "text", "text": text}}
            return mcp.result(rid, {"description": PROMPT["description"], "messages": [message]})
        return mcp.error(rid, mcp.METHOD_NOT_FOUND, f"method not found: {method}")

    @route.post("/api/mcp", operation_id="mcp")
    async def mcp_endpoint(
        request: Request, principal: auth.Principal = Depends(security)
    ) -> Response:
        """The MCP endpoint: JSON-RPC 2.0 — initialize, ping, tools/list, tools/call, prompts —
        one message per POST, answered in JSON. Every tool is a route of this API, called with
        your own credentials."""
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "cross-origin MCP requests are refused"}, 403)
        try:
            payload = json.loads(await request.body())
        except (ValueError, UnicodeDecodeError):
            return JSONResponse(mcp.error(None, mcp.PARSE_ERROR, "not JSON"), 400)
        if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
            reason = (
                "batches are not supported" if isinstance(payload, list) else "not JSON-RPC 2.0"
            )
            return JSONResponse(mcp.error(None, mcp.INVALID_REQUEST, reason), 400)
        method = payload.get("method")
        if not isinstance(method, str) or "id" not in payload:
            return Response(status_code=202)  # a notification, or a response to us: noted
        params = payload.get("params") or {}
        if not isinstance(params, dict):
            return JSONResponse(mcp.error(payload["id"], mcp.INVALID_PARAMS, "params: an object"))
        version = request.headers.get("mcp-protocol-version")
        if (
            version
            and version not in mcp.SUPPORTED
            and method.split("/")[0] in ("tools", "prompts")
        ):
            supported = ", ".join(mcp.SUPPORTED)
            return JSONResponse({"detail": f"MCP-Protocol-Version: one of {supported}"}, 400)
        out, step = await answer(request, method, payload["id"], params)
        if step is not None:
            async with write_txn():  # the call, as a step of the agent's run
                await record(principal, *step)
        return JSONResponse(out)
