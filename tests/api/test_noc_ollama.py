"""A local Ollama without a key, and what it has (ADR #477).

* a model suits an agent only if it calls tools; the list is ranked so the first is the one to try;
* a local Ollama is stored with no key and lists the models pulled on its machine — what each can
  do, which are loaded — asked of its native API with no `Authorization` header at all;
* Ollama Cloud is a cloud provider: ollama.com only, its key required, sent as a bearer token;
* an Ollama endpoint that speaks only the OpenAI API (a proxy) still lists its models.

The Ollama here is a stand-in on the loopback, shaped like Ollama's own answers.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, ClassVar

from netcorenoc.engine.noc import llm_http, ollama, providers
from netcorenoc.store import Store

import authutil

#: name -> (family, parameter size, capabilities or None for an Ollama that does not say)
_MODELS: dict[str, tuple[str, str, list[str] | None]] = {
    "gemma3:4b": ("gemma3", "4.3B", ["completion", "vision"]),
    "qwen3:8b": ("qwen3", "8.2B", ["completion", "tools", "thinking"]),
    "gpt-oss:20b": ("gptoss", "20.9B", ["completion", "tools", "thinking"]),
    "llama3.1:8b": ("llama", "8.0B", ["completion", "tools"]),
    "nomic-embed-text:latest": ("nomic-bert", "137M", ["embedding"]),
}


class _Ollama(BaseHTTPRequestHandler):
    native: ClassVar[bool] = True
    seen: ClassVar[list[str]] = []

    def _send(self, body: Any, code: int = 200) -> None:
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        type(self).seen.append(f"GET {self.path} {self.headers.get('Authorization')}")
        if self.path == "/api/tags" and self.native:
            self._send(
                {
                    "models": [
                        {
                            "name": n,
                            "model": n,
                            "size": 5_000_000_000,
                            "details": {
                                "family": f,
                                "parameter_size": p,
                                "quantization_level": "Q4",
                            },
                        }
                        for n, (f, p, _caps) in _MODELS.items()
                    ]
                }
            )
        elif self.path == "/api/ps" and self.native:
            self._send({"models": [{"name": "llama3.1:8b", "model": "llama3.1:8b"}]})
        elif self.path == "/v1/models":
            self._send({"data": [{"id": n} for n in _MODELS]})
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        type(self).seen.append(f"POST {self.path} {self.headers.get('Authorization')}")
        found = _MODELS.get(body.get("model", ""))
        if self.path != "/api/show" or not self.native or found is None:
            self._send({"error": "not found"}, 404)
            return
        shown: dict[str, Any] = {
            "license": "x" * 5000,
            "model_info": {f"{found[0]}.context_length": 40960},
        }
        if found[2] is not None:
            shown["capabilities"] = found[2]
        self._send(shown)

    def log_message(self, *_: Any) -> None:
        pass


def _serve(native: bool) -> HTTPServer:
    _Ollama.native = native
    _Ollama.seen = []
    server = HTTPServer(("127.0.0.1", 0), _Ollama)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_a_model_suits_an_agent_only_if_it_calls_tools() -> None:
    def tag(name: str, size: str) -> dict[str, Any]:
        return {"model": name, "details": {"parameter_size": size}}

    rows = [
        ollama.row(tag("small-tools", "3B"), {"capabilities": ["completion", "tools"]}, False),
        ollama.row(tag("big-tools", "70B"), {"capabilities": ["completion", "tools"]}, False),
        ollama.row(tag("loaded-tools", "8B"), {"capabilities": ["tools", "completion"]}, True),
        ollama.row(tag("chatty", "30B"), {"capabilities": ["completion"]}, True),
        ollama.row(tag("embed", "137M"), {"capabilities": ["embedding"]}, False),
        ollama.row(tag("old", "7B"), {"model_info": {}}, False),
        ollama.row(tag("unshown", "7B"), None, False),
    ]
    ranked = [r["name"] for r in ollama.rank(rows)]
    assert ranked == [
        "loaded-tools",
        "big-tools",
        "small-tools",
        "old",
        "unshown",
        "chatty",
        "embed",
    ], "tools first, then loaded, then the larger; what cannot act last"
    by = {r["name"]: r for r in rows}
    assert by["chatty"]["fit"] == "chat" and "cannot act" in by["chatty"]["why"]
    assert by["embed"]["fit"] == "embedding" and by["old"]["capabilities"] is None
    assert ollama.billions("567M") == 0.567 and ollama.billions("8.0B") == 8.0
    assert ollama.billions("") is None and ollama.billions("lots") is None
    hostile = ollama.row(
        {"model": "x" * 900, "details": "not a mapping"}, {"capabilities": "tools"}, False
    )
    assert len(hostile["name"]) == 200 and hostile["capabilities"] == []
    assert hostile["fit"] == "unknown", "capabilities that are not a list say nothing"


async def test_a_local_ollama_needs_no_key_and_lists_what_it_has(store: Store) -> None:
    server = _serve(native=True)
    _engine, _queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")
    try:
        made = await admin.post(
            "/api/noc/secrets",
            json={
                "name": "GPU-1",
                "kind": "ai_ollama",
                "access_level": "view_only",
                "host": "127.0.0.1",
                "port": server.server_port,
                "protocol_version": "HTTP",
                "monitor": False,
                "fields": {},
            },
        )
        assert made.status_code == 200, made.text
        answer = await admin.post(f"/api/noc/secrets/{made.json()['id']}/models")
        assert answer.status_code == 200, answer.text
        body = answer.json()
        assert body["recommended"] == "llama3.1:8b", "calls tools and is loaded now"
        names = [d["name"] for d in body["details"]]
        assert names[:3] == ["llama3.1:8b", "gpt-oss:20b", "qwen3:8b"]
        assert names[-2:] == ["gemma3:4b", "nomic-embed-text:latest"]
        assert body["models"] == names and body["count"] == 5
        llama = body["details"][0]
        assert llama["loaded"] and llama["context_length"] == 40960 and llama["fit"] == "agents"
        assert "license" not in json.dumps(body), "only what one chooses by is kept"
        assert all(line.endswith(" None") for line in _Ollama.seen), "no key, no Authorization"
        assert any(line.startswith("POST /api/show") for line in _Ollama.seen)
    finally:
        await admin.aclose()
        server.shutdown()


async def test_ollama_cloud_is_a_cloud_provider_with_a_key(store: Store) -> None:
    cloud = providers.PROVIDERS["ollama_cloud"]
    assert (cloud.host, cloud.port, cloud.key_required, cloud.own_host) == (
        "ollama.com",
        443,
        True,
        False,
    )
    _engine, _queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")

    def key(host: str, **fields: str) -> dict[str, Any]:
        return {
            "name": "cloud",
            "kind": "ai_ollama_cloud",
            "access_level": "view_only",
            "host": host,
            "port": 443,
            "monitor": False,
            "fields": fields,
        }

    try:
        assert (await admin.post("/api/noc/secrets", json=key("ollama.com"))).status_code == 400
        moved = await admin.post("/api/noc/secrets", json=key("10.0.0.9", api_key="ok-1"))
        assert moved.status_code == 400 and "ollama.com" in moved.json()["detail"]
        stored = await admin.post("/api/noc/secrets", json=key("ollama.com", api_key="ok-1"))
        assert stored.status_code == 200, stored.text
    finally:
        await admin.aclose()
    server = _serve(native=True)
    try:
        root = f"http://127.0.0.1:{server.server_port}"
        listed = await llm_http.ollama_models(cloud, root, "ok-secret")
        assert next(m["name"] for m in listed) == "gpt-oss:20b", "nothing is loaded in the cloud"
        assert all(line.endswith(" Bearer ok-secret") for line in _Ollama.seen)
        assert not any("/api/ps" in line for line in _Ollama.seen)
    finally:
        server.shutdown()


async def test_an_ollama_behind_a_proxy_that_speaks_only_v1_still_lists(store: Store) -> None:
    server = _serve(native=False)
    _engine, _queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")
    try:
        made = await admin.post(
            "/api/noc/secrets",
            json={
                "name": "proxied",
                "kind": "ai_ollama",
                "access_level": "view_only",
                "host": "127.0.0.1",
                "port": server.server_port,
                "protocol_version": "HTTP",
                "monitor": False,
                "fields": {"api_key": "proxy-token"},
            },
        )
        body = (await admin.post(f"/api/noc/secrets/{made.json()['id']}/models")).json()
        assert body["models"] == sorted(_MODELS) and body["details"] == []
        assert body["recommended"] is None
        assert "GET /v1/models Bearer proxy-token" in _Ollama.seen
    finally:
        await admin.aclose()
        server.shutdown()
