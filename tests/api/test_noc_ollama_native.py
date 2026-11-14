"""A local Ollama, the way AnythingLLM and Paperclip's adapters reach one (ADR #487).

A fake Ollama on a real socket answers `/api/version`, `/api/tags` and `/api/chat`, so the appliance
is tested over HTTP the way it reaches a real one: the agent's turn goes to the native API with a
context window, "Detect" finds the server among the usual addresses, and "Try" says whether a
model answers and calls a tool.
"""

from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, ClassVar

import pytest

from netcorenoc.engine.noc import agents, llm_http, ollama_chat, providers
from netcorenoc.store import Store

import authutil

STEP = {"username": "adm", "password": authutil.PW, "otp": authutil.OTP}


class _Ollama(BaseHTTPRequestHandler):
    chats: ClassVar[list[dict[str, Any]]] = []
    calls_tools: ClassVar[bool] = True

    def _send(self, body: dict[str, Any]) -> None:
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/api/version":
            self._send({"version": "0.9.1"})
        elif self.path == "/api/tags":
            self._send({"models": [{"model": "qwen3:8b"}, {"model": "llama3.2:3b"}]})
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path != "/api/chat":
            self.send_response(404)
            self.end_headers()
            return
        type(self).chats.append(body)
        message: dict[str, Any] = {"role": "assistant", "content": "Case 42 is open."}
        if type(self).calls_tools and body.get("tools") and len(body["messages"]) == 1:
            message = {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": "get_case_status", "arguments": {"case": 42}}}
                ],
            }
        self._send({"message": message, "prompt_eval_count": 120, "eval_count": 9, "done": True})

    def log_message(self, *_: Any) -> None:
        pass


@pytest.fixture
def fake_ollama() -> Any:
    _Ollama.chats = []
    _Ollama.calls_tools = True
    server = HTTPServer(("127.0.0.1", 0), _Ollama)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_a_turn_becomes_ollamas_own_request_and_back() -> None:
    payload = {
        "model": "qwen3:8b",
        "num_ctx": 32768,
        "max_tokens": 4000,
        "tools": [{"type": "function", "function": {"name": "noc_me", "parameters": {}}}],
        "messages": [
            {"role": "system", "content": "You work NOC cases."},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "c1", "function": {"name": "noc_me", "arguments": '{"x": 1}'}}
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "you are Ollie"},
        ],
    }
    native = ollama_chat.to_native(payload)
    assert native["options"] == {"num_ctx": 32768, "num_predict": 4000}
    assert native["stream"] is False and native["keep_alive"] == ollama_chat.KEEP_ALIVE
    assert native["messages"][1]["tool_calls"] == [
        {"function": {"name": "noc_me", "arguments": {"x": 1}}}
    ]
    assert native["messages"][2] == {
        "role": "tool",
        "content": "you are Ollie",
        "tool_name": "noc_me",
    }
    assert native["tools"] == payload["tools"]
    assert ollama_chat.to_native({"model": "m", "messages": []})["options"]["num_ctx"] == 16384
    back = ollama_chat.from_native(
        {
            "message": {
                "content": "",
                "tool_calls": [{"function": {"name": "noc_me", "arguments": {}}}],
            },
            "prompt_eval_count": 7,
            "eval_count": 3,
        }
    )
    call = back["choices"][0]["message"]["tool_calls"][0]
    assert call["function"] == {"name": "noc_me", "arguments": "{}"} and call["id"]
    assert back["usage"] == {"prompt_tokens": 7, "completion_tokens": 3}


async def test_an_ollama_turn_goes_to_the_native_api_with_its_window(fake_ollama: Any) -> None:
    base = f"http://127.0.0.1:{fake_ollama.server_port}/v1"
    answer = await llm_http.chat(
        providers.PROVIDERS["ollama"],
        base,
        "",
        {"model": "qwen3:8b", "num_ctx": 8192, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert _Ollama.chats[-1]["options"]["num_ctx"] == 8192, "the window travels"
    assert answer["choices"][0]["message"]["content"] == "Case 42 is open."


async def test_detect_finds_ollama_among_the_usual_addresses(
    store: Store, fake_ollama: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidates = [("127.0.0.1", _closed_port()), ("127.0.0.1", fake_ollama.server_port)]
    monkeypatch.setattr(providers, "ollama_candidates", lambda: candidates)
    _engine, _queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")
    viewer = await authutil.client_as(app, "viewer")
    try:
        found = (await admin.get("/api/noc/providers/ollama/detect")).json()
        assert found["custodian"] is True
        assert found["found"] == [
            {
                "host": "127.0.0.1",
                "port": fake_ollama.server_port,
                "version": "0.9.1",
                "models": ["qwen3:8b", "llama3.2:3b"],
            }
        ]
        assert len(found["tried"]) == 2
        assert (await viewer.get("/api/noc/providers/ollama/detect")).status_code == 403
        editor = await authutil.client_as(app, "editor")
        try:
            refused = (await editor.get("/api/noc/providers/ollama/detect")).json()
            assert refused["custodian"] is False and refused["found"] == [], "nothing is tried"
        finally:
            await editor.aclose()
    finally:
        await admin.aclose()
        await viewer.aclose()


def test_the_usual_addresses_are_fixed_and_include_the_containers_host() -> None:
    hosts = [h for h, _p in providers.ollama_candidates()]
    assert hosts[:4] == ["127.0.0.1", "host.docker.internal", "ollama", "172.17.0.1"]
    assert {p for _h, p in providers.ollama_candidates()} == {11434}


async def test_try_says_whether_a_model_answers_and_calls_a_tool(
    store: Store, fake_ollama: Any
) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")
    try:
        made = await admin.post(
            "/api/noc/secrets",
            json={
                "name": "Local Ollama",
                "kind": "ai_ollama",
                "host": "127.0.0.1",
                "port": fake_ollama.server_port,
                "protocol_version": "HTTP",
                "access_level": "view_only",
                "fields": {},
            },
        )
        assert made.status_code == 200, made.text
        sid = made.json()["id"]
        tried = (await admin.post(f"/api/noc/secrets/{sid}/try", json={"model": "qwen3:8b"})).json()
        assert tried["ok"] and tried["called_tool"] and tried["prompt_tokens"] == 120
        assert _Ollama.chats[-1]["options"]["num_ctx"] == ollama_chat.DEFAULT_NUM_CTX
        _Ollama.calls_tools = False
        chatty = (
            await admin.post(f"/api/noc/secrets/{sid}/try", json={"model": "llama3.2:3b"})
        ).json()
        assert chatty["ok"] and not chatty["called_tool"] and "42" in chatty["reply"]
        cur = await store.conn.execute(
            "SELECT details FROM audit_log WHERE action='noc.secret.use' ORDER BY id DESC LIMIT 1"
        )
        row = await cur.fetchone()
        assert row is not None and json.loads(row[0])["for"] == "try"
    finally:
        await admin.aclose()


def test_an_ollama_agent_carries_a_context_window_and_others_do_not() -> None:
    base = {"provider": "ollama", "model": "qwen3:8b", "secret_id": 1}
    assert agents.validate_config("openai", base)["num_ctx"] == 16384
    assert agents.validate_config("openai", {**base, "num_ctx": 32768})["num_ctx"] == 32768
    with pytest.raises(agents.AgentError, match="num_ctx"):
        agents.validate_config("openai", {**base, "num_ctx": 512})
    gpt = agents.validate_config("openai", {"provider": "openai", "model": "gpt-5", "secret_id": 1})
    assert "num_ctx" not in gpt
