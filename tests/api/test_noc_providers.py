"""AI providers' keys in the vault, and agents the appliance runs on them (ADR #471).

* each provider is a credential kind of the `ai` group whose one field is the API key; a cloud
  provider's key is refused anywhere but its own API, a local Ollama's goes where its owner says
  and needs no key;
* the models a key may use are asked of the provider by a custodian, with the key, and the asking
  is a `noc.secret.use` audit row;
* an `openai` agent is run here like a `claude` one: its key sealed, its tools the MCP tools its key
  may call, each a step of its run — and opening its provider's key is an audit row under the
  agent's token. The provider is a scripted stand-in: no network, no key spent;
* the HTTP client follows no redirect, speaks only http(s), and sends each provider its own header.
"""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, ClassVar

import pytest

from netcorenoc.api import agent_openai
from netcorenoc.engine.noc import agent_loop, agents, kinds, llm_http, providers
from netcorenoc.engine.noc.llm_http import ProviderCallError
from netcorenoc.store import Store

import authutil

STEP = {"username": "adm", "password": authutil.PW}


def _key(kind: str, host: str, *, port: int | None = None, **fields: str) -> dict[str, Any]:
    return {
        "name": kind,
        "kind": kind,
        "access_level": "view_only",
        "host": host,
        "port": port,
        "monitor": False,
        "fields": fields,
    }


async def _audit(store: Store, action: str) -> list[dict[str, Any]]:
    async with store.reading():
        cur = await store.conn.execute(
            "SELECT actor, object_id, details FROM audit_log WHERE action = ? ORDER BY id",
            (action,),
        )
        return [dict(r) for r in await cur.fetchall()]


def test_each_provider_is_one_ai_kind_whose_field_is_the_key() -> None:
    ai = {k.id: k for k in kinds.KINDS.values() if k.group == "ai"}
    assert set(ai) == {p.kind for p in providers.PROVIDERS.values()}
    assert [f.name for f in ai["ai_openai"].fields] == ["api_key"]
    assert ai["ai_openai"].fields[0].secret and ai["ai_openai"].fields[0].required
    assert not ai["ai_ollama"].fields[0].required, "a local Ollama needs no key"
    described = providers.describe()
    assert {p["id"] for p in described} == set(providers.PROVIDERS)
    assert kinds.describe()["providers"] == described
    google = providers.PROVIDERS["google"]
    assert providers.base_url(google, google.host, 443, "HTTPS") == (
        "https://generativelanguage.googleapis.com/v1beta/openai"
    )
    ollama = providers.PROVIDERS["ollama"]
    assert providers.base_url(ollama, "gpu-1", 11434, "HTTP") == "http://gpu-1:11434/v1"
    assert providers.base_url(ollama, "::1", 11434, "HTTP") == "http://[::1]:11434/v1"


def test_the_openai_adapter_names_its_provider_and_model() -> None:
    ok = agents.validate_config("openai", {"provider": "xai", "model": "grok-4", "secret_id": 3})
    assert ok["provider"] == "xai" and ok["max_turns"] == 20
    for bad in (
        {"provider": "anthropic", "model": "x", "secret_id": 1},
        {"provider": "openai", "model": "gpt 4; rm", "secret_id": 1},
        {"provider": "openai", "model": "gpt-4.1"},
    ):
        with pytest.raises(agents.AgentError):
            agents.validate_config("openai", bad)


async def test_a_cloud_key_works_only_at_its_providers_api(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")
    try:
        moved = await admin.post(
            "/api/noc/secrets", json=_key("ai_openai", "evil.example.net", api_key="sk-x")
        )
        assert moved.status_code == 400 and "api.openai.com" in moved.json()["detail"]
        home = await admin.post(
            "/api/noc/secrets", json=_key("ai_openai", "api.openai.com", api_key="sk-x")
        )
        assert home.status_code == 200, home.text
        keyless = await admin.post(
            "/api/noc/secrets", json=_key("ai_anthropic", "api.anthropic.com")
        )
        assert keyless.status_code == 400 and "api_key" in keyless.json()["detail"]
        local = await admin.post("/api/noc/secrets", json=_key("ai_ollama", "10.1.2.3", port=11434))
        assert local.status_code == 200 and local.json()["host"] == "10.1.2.3"
        assert "sk-x" not in json.dumps((await admin.get("/api/noc/secrets")).json())
    finally:
        await admin.aclose()


async def test_a_custodian_asks_the_provider_for_its_models(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[tuple[str, str, str]] = []

    async def fake(provider: providers.Provider, base: str, key: str, **_: Any) -> list[str]:
        asked.append((provider.id, base, key))
        return ["gpt-4.1", "gpt-4.1-mini"]

    monkeypatch.setattr(llm_http, "list_models", fake)
    _engine, _queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")
    editor = await authutil.client_as(app, "editor")
    try:
        made = await admin.post(
            "/api/noc/secrets", json=_key("ai_openai", "api.openai.com", api_key="sk-live")
        )
        sid = int(made.json()["id"])
        answer = await admin.post(f"/api/noc/secrets/{sid}/models")
        assert answer.status_code == 200 and answer.json()["models"] == ["gpt-4.1", "gpt-4.1-mini"]
        assert asked == [("openai", "https://api.openai.com/v1", "sk-live")]
        assert "sk-live" not in answer.text
        assert (await editor.post(f"/api/noc/secrets/{sid}/models")).status_code == 403
        device = await admin.post(
            "/api/noc/secrets",
            json={**_key("ssh_password", "10.0.0.1"), "fields": {"username": "u", "password": "p"}},
        )
        refused = await admin.post(f"/api/noc/secrets/{device.json()['id']}/models")
        assert refused.status_code == 400
    finally:
        await admin.aclose()
        await editor.aclose()
    rows = await _audit(store, "noc.secret.use")
    assert len(rows) == 1 and rows[0]["object_id"] == str(sid)


class _Provider:
    """A scripted OpenAI-compatible provider: asks for one tool, then answers in words."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self, provider: providers.Provider, base: str, key: str, payload: dict[str, Any], **_: Any
    ) -> dict[str, Any]:
        self.calls.append({"provider": provider.id, "base": base, "key": key, **payload})
        usage = {"prompt_tokens": 500, "completion_tokens": 50}
        if len(self.calls) == 1:
            call = {
                "id": "c1",
                "type": "function",
                "function": {"name": "noc_me", "arguments": "{}"},
            }
            message: dict[str, Any] = {"role": "assistant", "content": None, "tool_calls": [call]}
        else:
            message = {"role": "assistant", "content": "Nothing routed to me; the queue is quiet."}
        return {"choices": [{"message": message, "finish_reason": "stop"}], "usage": usage}


async def test_the_appliance_runs_an_agent_on_a_local_ollama(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")
    try:
        made = await admin.post("/api/noc/secrets", json=_key("ai_ollama", "127.0.0.1"))
        sid = int(made.json()["id"])
        on = await admin.post(
            f"/api/noc/secrets/{sid}/agents", json={"enabled": True, "stepup": STEP}
        )
        assert on.status_code == 200, on.text
        wrong = await admin.post(
            "/api/noc/agents",
            json={
                "name": "Gem",
                "role": "engineer",
                "adapter": "openai",
                "adapter_config": {"provider": "google", "model": "gemini-x", "secret_id": sid},
            },
        )
        assert wrong.status_code == 400, "a Google agent cannot run on an Ollama key"
        hired = await admin.post(
            "/api/noc/agents",
            json={
                "name": "Ollie",
                "role": "engineer",
                "adapter": "openai",
                "adapter_config": {"provider": "ollama", "model": "llama3.1", "secret_id": sid},
            },
        )
        assert hired.status_code == 200, hired.text
        assert hired.json()["token"] is None, "the appliance keeps its key sealed"
        aid = int(hired.json()["agent"]["id"])
        assert hired.json()["agent"]["provider"] == "ollama"
        woken = await admin.post(f"/api/noc/agents/{aid}/invoke", json={})
        assert woken.status_code == 200, woken.text
    finally:
        await admin.aclose()
    model = _Provider()
    runner = agent_openai.openai_runner(app, store, chat=model)
    running: dict[int, asyncio.Task[None]] = {}
    assert await agent_loop.start_ready(store, running, None, runner) == 1
    await asyncio.gather(*running.values())
    first = model.calls[0]
    assert first["base"] == "http://127.0.0.1:11434/v1" and first["key"] == ""
    assert first["model"] == "llama3.1" and first["messages"][0]["role"] == "system"
    assert "max_tokens" in first and "max_completion_tokens" not in first
    names = {t["function"]["name"] for t in first["tools"]}
    assert "noc_me" in names and "noc_hire_agent" not in names and "noc_finish_run" not in names
    assert model.calls[1]["messages"][-1]["role"] == "tool"
    async with store.reading():
        runs = await store.noc_agent_runs(aid)
        steps = await store.noc_agent_run_steps(int(runs[0]["id"]))
        agent = await store.noc_agent(aid)
    assert runs[0]["status"] == "succeeded" and "quiet" in runs[0]["summary"]
    assert runs[0]["input_tokens"] == 1000 and float(runs[0]["cost_cents"]) == 0
    assert [s["name"] for s in steps] == ["on_demand", "noc_me", "summary"]
    used = await _audit(store, "noc.secret.use")
    assert agent is not None and used[-1]["actor"] == f"token:{agent['token_id']}"
    assert used[-1]["object_id"] == str(sid)


async def test_without_its_runner_an_openai_run_fails_with_the_reason(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")
    try:
        made = await admin.post("/api/noc/secrets", json=_key("ai_ollama", "127.0.0.1"))
        sid = int(made.json()["id"])
        await admin.post(f"/api/noc/secrets/{sid}/agents", json={"enabled": True, "stepup": STEP})
        hired = await admin.post(
            "/api/noc/agents",
            json={
                "name": "Ollie",
                "role": "engineer",
                "adapter": "openai",
                "adapter_config": {"provider": "ollama", "model": "llama3.1", "secret_id": sid},
            },
        )
        aid = int(hired.json()["agent"]["id"])
        await admin.post(f"/api/noc/agents/{aid}/invoke", json={})
    finally:
        await admin.aclose()
    assert await agent_loop.start_ready(store, {}, None, None) == 0
    async with store.reading():
        runs = await store.noc_agent_runs(aid)
    assert runs[0]["status"] == "failed" and "OpenAI-compatible" in runs[0]["error"]


class _Redirect(BaseHTTPRequestHandler):
    seen: ClassVar[list[str]] = []

    def do_GET(self) -> None:
        type(self).seen.append(f"{self.path} {self.headers.get('Authorization')}")
        if self.path.startswith("/v1"):
            self.send_response(302)
            port = self.headers.get("Host", "").rpartition(":")[2]
            self.send_header("Location", f"http://127.0.0.1:{port}/stolen")
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()

    def log_message(self, *_: Any) -> None:
        pass


async def test_a_key_is_never_followed_to_another_place() -> None:
    server = HTTPServer(("127.0.0.1", 0), _Redirect)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{server.server_port}/v1"
        with pytest.raises(ProviderCallError, match="redirect"):
            await llm_http.list_models(providers.PROVIDERS["openai"], base, "sk-secret")
        assert _Redirect.seen == ["/v1/models Bearer sk-secret"], "the redirect was not followed"
        with pytest.raises(ProviderCallError, match="http"):
            await llm_http.list_models(providers.PROVIDERS["openai"], "file:///etc", "k")
    finally:
        server.shutdown()
    anthropic = llm_http.headers_for(providers.PROVIDERS["anthropic"], "sk-ant-1")
    assert anthropic == {"x-api-key": "sk-ant-1", "anthropic-version": "2023-06-01"}
    assert llm_http.headers_for(providers.PROVIDERS["ollama"], "") == {}
