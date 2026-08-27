"""F7 / §A.3 — least-privilege response shaping (field-level authorization).

Route authorization is covered by ``test_rbac``; this suite proves that *within* a
viewer-readable response, sensitive network detail is coarsened or dropped for lower roles —
deny-by-default extended from routes to fields — while editors/admins keep full fidelity.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import pytest

from netcorenoc.crosscutting import auth, shaping
from netcorenoc.store import Store

import authutil
import util

BASE = 1_700_000_000.0


# -- unit: the serializer ------------------------------------------------------------


def test_coarsen_ipv4_to_slash24() -> None:
    assert shaping.coarsen_ip("10.1.2.3") == "10.1.2.0/24"


def test_coarsen_ipv6_to_slash48() -> None:
    assert shaping.coarsen_ip("2001:db8:abcd:1234::1") == "2001:db8:abcd::/48"


def test_coarsen_leaves_labels_and_empty_untouched() -> None:
    assert shaping.coarsen_ip("core-router-1") == "core-router-1"
    assert shaping.coarsen_ip("") == ""


def test_shape_viewer_coarsens_ip_and_drops_secrets() -> None:
    row = {"ip": "10.0.0.5", "source_ip": "10.9.9.9", "community_tag": "abc123", "name": "keep"}
    assert shaping.shape(row, "viewer") == {"ip": "10.0.0.0/24", "name": "keep"}


def test_shape_editor_sees_ip_and_community_but_not_source_ip() -> None:
    row = {"ip": "10.0.0.5", "source_ip": "10.9.9.9", "community_tag": "abc123", "name": "keep"}
    assert shaping.shape(row, "editor") == {
        "ip": "10.0.0.5",
        "community_tag": "abc123",
        "name": "keep",
    }


def test_shape_admin_sees_everything() -> None:
    row = {"ip": "10.0.0.5", "source_ip": "10.9.9.9", "community_tag": "abc123"}
    assert shaping.shape(row, "admin") == row


def test_shape_recurses_into_nested_lists_and_dicts() -> None:
    payload = {"nodes": [{"ip": "192.168.1.7", "active": 2}], "meta": {"source_ip": "8.8.8.8"}}
    shaped = shaping.shape(payload, "viewer")
    assert shaped == {"nodes": [{"ip": "192.168.1.0/24", "active": 2}], "meta": {}}


def test_unknown_role_gets_the_strictest_projection() -> None:
    row = {"ip": "10.0.0.5", "source_ip": "10.9.9.9"}
    assert shaping.shape(row, None) == {"ip": "10.0.0.0/24"}


# -- integration: role x endpoint ----------------------------------------------------


async def _seed_topology(store: Store) -> object:
    engine, queue, app = await authutil.make_env(store)
    await util.drive(engine, queue, util.fixture_events("fiber_cut.json", BASE))
    await engine.learner.save(store, BASE)  # normally the maintenance loop
    async with store.lock:
        await store.commit()
    return app


@pytest.mark.parametrize(
    ("role", "expected_ips"),
    [
        ("viewer", {"127.0.0.0/24"}),  # both hosts collapse to their /24
        ("editor", {"127.0.0.2", "127.0.0.3"}),
        ("admin", {"127.0.0.2", "127.0.0.3"}),
    ],
)
async def test_graph_ips_shaped_by_role(store: Store, role: str, expected_ips: set[str]) -> None:
    app = await _seed_topology(store)
    client = await authutil.client_as(app, role)
    try:
        graph = (await client.get("/api/graph")).json()
        assert {n["ip"] for n in graph["nodes"]} == expected_ips
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    ("role", "coarsened"),
    [("viewer", True), ("editor", False), ("admin", False)],
)
async def test_situation_detail_device_ip_shaped_by_role(
    store: Store, role: str, coarsened: bool
) -> None:
    app = await _seed_topology(store)
    client = await authutil.client_as(app, role)
    try:
        sid = (await client.get("/api/situations", params={"status": "open"})).json()[0]["id"]
        alarms = (await client.get(f"/api/situations/{sid}")).json()["alarms"]
        ips = {a["device_ip"] for a in alarms}
        if coarsened:
            assert ips == {"127.0.0.0/24"}
        else:
            assert ips == {"127.0.0.2", "127.0.0.3"}
    finally:
        await client.aclose()


async def test_entities_ne_ip_shaped_for_viewer(store: Store) -> None:
    app = await _seed_topology(store)
    viewer = await authutil.client_as(app, "viewer")
    admin = await authutil.client_as(app, "admin")
    try:
        v_ips = {ne["ip"] for ne in (await viewer.get("/api/entities")).json()}
        a_ips = {ne["ip"] for ne in (await admin.get("/api/entities")).json()}
        assert v_ips == {"127.0.0.0/24"}
        assert a_ips == {"127.0.0.2", "127.0.0.3"}
    finally:
        await viewer.aclose()
        await admin.aclose()


class _StopSSEError(Exception):
    """Break out of the infinite SSE generator once the first update event is captured."""


async def test_sse_stream_graph_is_shaped_for_viewer(store: Store) -> None:
    """The live SSE path shapes exactly like the polled endpoints (no leak via the stream).

    httpx's ASGITransport cannot deliver partial bodies of an infinite response, so the app is
    driven directly: we capture body chunks until the first `event: update`, then stop.
    """
    app = await _seed_topology(store)
    viewer = await authutil.client_as(app, "viewer")
    cookie = viewer.cookies.get(auth.COOKIE_NAME)
    await viewer.aclose()
    assert cookie is not None

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "path": "/api/events",
        "raw_path": b"/api/events",
        "query_string": b"",
        "headers": [
            (b"cookie", f"{auth.COOKIE_NAME}={cookie}".encode()),
            (b"host", b"testserver"),
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "scheme": "http",
    }
    bodies: list[bytes] = []
    sent_request = False
    never = asyncio.Event()

    async def receive() -> dict[str, Any]:
        nonlocal sent_request
        if not sent_request:
            sent_request = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await never.wait()  # a real client keeps the connection open (never disconnects)
        return {"type": "http.disconnect"}  # pragma: no cover

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.body":
            bodies.append(message.get("body", b""))
            if b"event: update" in b"".join(bodies):
                raise _StopSSEError

    with contextlib.suppress(_StopSSEError, TimeoutError):
        await asyncio.wait_for(app(scope, receive, send), timeout=5.0)  # type: ignore[operator]
    payload = b"".join(bodies).decode()
    assert "event: update" in payload, "no SSE update captured"
    assert "127.0.0.2" not in payload and "127.0.0.3" not in payload  # raw host IPs hidden
    assert "127.0.0.0/24" in payload  # coarsened form present
