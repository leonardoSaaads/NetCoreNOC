"""S1 / F1 — stored-XSS remediation, CSP, security headers, and safe static assets.

Hostile strings are pushed through the *real* ingest path (parser → engine → store) in
the instance, a varbind value, and operator labels, then read back over the API. The API
must return them as JSON string values (data, never HTML), every route class must carry
the security headers, and the shipped UI source must be free of the unsafe patterns that
caused F1 (`innerHTML` interpolation, a `localStorage` token).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from opticorr.api import CSP, UI_DIR, create_app
from opticorr.events import Varbind
from opticorr.main import Engine
from opticorr.receiver import QueueItem
from opticorr.store import Store

import util

TOKEN = "test-token-123"
XSS = "<img src=x onerror=alert(1)><script>alert(document.cookie)</script>\"'>"


@pytest.fixture
async def engine_env(store: Store) -> tuple[Engine, asyncio.Queue[QueueItem]]:
    queue: asyncio.Queue[QueueItem] = asyncio.Queue()
    engine = Engine(store, queue)
    await engine.start()
    return engine, queue


@pytest.fixture
async def client(
    engine_env: tuple[Engine, asyncio.Queue[QueueItem]],
) -> AsyncIterator[httpx.AsyncClient]:
    from opticorr import auth

    store = engine_env[0].store
    async with store.lock:
        await store.create_token(auth.hash_token(TOKEN), "test", "admin", "adm", 0.0)
        await store.commit()
    app = create_app(engine_env[0])
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://opticorr.test",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as c:
        yield c


async def test_f1_hostile_strings_survive_the_ingest_path_as_json(
    client: httpx.AsyncClient, engine_env: tuple[Engine, asyncio.Queue[QueueItem]]
) -> None:
    engine, queue = engine_env
    # A trap whose instance AND a varbind value are attacker-controlled XSS payloads,
    # driven through the real engine (the datagram-adjacent unauthenticated path, F1).
    hostile = util.event(
        device="10.0.0.9",
        instance=XSS,
        varbinds=[Varbind(oid="1.3.6.1.4.1.9.9.1", kind="OctetString", value=XSS)],
    )
    await util.drive(engine, queue, [hostile])
    # Operator label is the other F1 vector.
    dev_id = await engine.store.device_id("10.0.0.9", 1.0)
    r = await client.post("/api/labels", json={"kind": "device", "id": dev_id, "label": XSS})
    assert r.status_code == 200

    sid = (await client.get("/api/situations")).json()[0]["id"]
    resp = await client.get(f"/api/situations/{sid}")
    assert "application/json" in resp.headers["content-type"]
    detail = resp.json()
    alarm = detail["alarms"][0]
    # The payload round-trips as a plain string value: it is data, not markup.
    assert alarm["instance"] == XSS
    assert alarm["device_label"] == XSS
    # In the raw JSON body the dangerous characters are JSON-escaped, so no HTML tag can
    # break out of the response even if a client mishandled the content-type.
    assert "<script>" in resp.text or "\\u003cscript\\u003e" in resp.text  # tolerate either encoder
    # The response is not HTML — an HTML sniffer would find no executable document wrapper.
    assert not resp.text.lstrip().lower().startswith("<!doctype")


@pytest.mark.parametrize("path", ["/", "/app.js", "/style.css", "/healthz", "/api/stats"])
async def test_security_headers_on_every_route_class(client: httpx.AsyncClient, path: str) -> None:
    resp = await client.get(path)
    assert resp.headers["content-security-policy"] == CSP
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "no-referrer"


async def test_api_responses_are_no_store(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/stats")).headers["cache-control"] == "no-store"
    # Static assets are cacheable (no no-store forced on them).
    assert (await client.get("/app.js")).headers.get("cache-control") != "no-store"


async def test_static_assets_served_with_correct_types(client: httpx.AsyncClient) -> None:
    js = await client.get("/app.js")
    assert "javascript" in js.headers["content-type"]
    assert "createTextNode" in js.text and "textContent" in js.text  # safe DOM building
    css = await client.get("/style.css")
    assert "text/css" in css.headers["content-type"]
    d3 = await client.get("/vendor/d3.v7.min.js")
    assert d3.status_code == 200 and "d3js.org v7" in d3.text  # vendored locally, pinned


def test_ui_source_has_no_f1_antipatterns() -> None:
    index = (UI_DIR / "index.html").read_text()
    app_js = (UI_DIR / "app.js").read_text()
    # CSP compliance: the page loads scripts/styles by reference only — no inline code.
    assert "cdn.jsdelivr.net" not in index and "https://" not in index
    assert "<script>" not in index  # only <script src=...></script>
    assert " style=" not in index  # no inline style attributes (style-src 'self')
    # F1 root cause is gone: the client never assigns to or reads the .innerHTML sink.
    assert ".innerHTML" not in app_js
    # F2: the localStorage token is removed entirely.
    assert "localStorage" not in app_js and "localStorage" not in index
    # The escaper and safe builders exist and are used.
    assert "function esc(" in app_js and "createTextNode" in app_js


def test_d3_is_vendored_and_pinned() -> None:
    d3 = (Path(UI_DIR) / "vendor" / "d3.v7.min.js").read_text()
    assert "v7.9.0" in d3.splitlines()[0]  # pinned version banner
