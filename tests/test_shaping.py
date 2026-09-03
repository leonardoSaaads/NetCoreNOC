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
        # v0.16.0: the correlator creates `new` (DECISIONS #254).
        sid = (await client.get("/api/situations", params={"status": "new"})).json()[0]["id"]
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


# --- v0.16.0: the derived name is built from addresses, so it is shaped like one ----------------
#
# The two axes, each with a control. `test_sse_stream_graph_is_shaped_for_viewer` above is what
# caught the original defect — it asserts on the stream's raw text rather than on a field, which is
# why a leak inside a *composite string* could not hide from it — and the two tests below say the
# same thing where a reader of `naming.py` will look for them.


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        ("viewer", "127.0.0.0/24"),  # every address in the NAME collapses to its network
        ("editor", "127.0.0.2"),  # the control: an editor still sees the host
        ("admin", "127.0.0.2"),
    ],
)
async def test_the_derived_name_is_coarsened_for_a_role_below_editor(
    store: Store, role: str, expected: str
) -> None:
    """**The field axis.** `Storm -> 127.0.0.2` names an address, and a viewer is shown
    `127.0.0.0/24` for that same device two fields away.

    Parametrised over all three roles rather than asserted for a viewer alone: a coarsener applied
    to everyone would pass a viewer-only test and would silently take the address away from the
    role the name is most useful to.
    """
    app = await _seed_topology(store)
    client = await authutil.client_as(app, role)
    try:
        rows = (await client.get("/api/situations?limit=50")).json()
        names = [str(row["derived_name"] or "") for row in rows]
        assert any(expected in name for name in names), names
        if role == "viewer":
            assert not any("127.0.0.2" in name or "127.0.0.3" in name for name in names), names
    finally:
        await client.aclose()


async def test_a_scoped_reader_gets_no_name_built_from_members_they_cannot_see(
    store: Store,
) -> None:
    """**The scope axis**, which coarsening does not cover: a /24 the reader may not see at all is
    still a disclosure, and the list carries no membership to rebuild a true name from.

    So the list DROPS the name when anything is redacted (DECISIONS #59: redact to a count, never
    to a different identity) and the detail RECOMPUTES it from the members that are visible — the
    same function on a different input (#257).

    The control is the second half of each assertion: a row with nothing redacted keeps its name,
    and the recomputed detail name is a real name rather than an empty string.
    """
    import hashlib
    import json as _json

    _engine, _queue, app = await authutil.make_env(store)
    policy = _json.dumps({"version": 1, "roles": {"editor": ["10.1.0.0/16"]}})
    async with store.lock:
        pid = await store.insert_governance_policy(
            "scope", policy, hashlib.sha256(policy.encode()).hexdigest(), "adm", BASE, ""
        )
        await store.set_active_governance_policy("scope", pid, "adm", BASE)
        sid = await store.create_situation(BASE, None)
        # The hidden device holds the LOWEST address on purpose: the name leads with the lowest and
        # counts the rest, so a fixture whose hidden member sorts last would be green against a
        # name that never had a chance to carry it — an invariant that cannot fail.
        for index, block in enumerate(("10.0", "10.1", "10.1")):
            ip = f"{block}.0.{index + 1}"
            cur = await store.conn.execute(
                "INSERT INTO device (ip, first_seen, last_seen) VALUES (?, ?, ?) RETURNING id",
                (ip, BASE, BASE),
            )
            device = int((await cur.fetchone())[0])  # type: ignore[index]
            cur = await store.conn.execute(
                "INSERT INTO ne (ip, first_seen, last_seen) VALUES (?, ?, ?) RETURNING id",
                (ip, BASE, BASE),
            )
            ne = int((await cur.fetchone())[0])  # type: ignore[index]
            cur = await store.conn.execute(
                "INSERT INTO alarm_class (oid, first_seen, last_seen) VALUES (?, ?, ?) "
                "RETURNING id",
                (f"1.3.6.1.4.1.99.{index}", BASE, BASE),
            )
            klass = int((await cur.fetchone())[0])  # type: ignore[index]
            cur = await store.conn.execute(
                "INSERT INTO alarm (ne_id, device_id, class_id, instance, status, count, "
                "first_seen, last_seen) VALUES (?, ?, ?, '', 'active', 1, ?, ?) RETURNING id",
                (ne, device, klass, BASE, BASE),
            )
            await store.add_alarm_to_situation(sid, int((await cur.fetchone())[0]))  # type: ignore[index]
        await store.commit()

    unrestricted = await authutil.client_as(app, "admin")
    scoped = await authutil.client_as(app, "editor")
    try:
        full = (await unrestricted.get("/api/situations?limit=50")).json()
        assert any("10.0.0.1" in str(row["derived_name"] or "") for row in full), (
            "the control: the STORED name does name the out-of-scope device"
        )

        rows = (await scoped.get("/api/situations?limit=50")).json()
        listed = next(row for row in rows if int(row["id"]) == sid)
        assert listed["redacted_count"] == 1
        assert listed["derived_name"] is None, (
            "the list served a name built partly from a device outside the reader's scope"
        )

        detail = (await scoped.get(f"/api/situations/{sid}")).json()
        assert detail["derived_name"], "the detail dropped the name instead of recomputing it"
        assert "10.0.0.1" not in detail["derived_name"], detail["derived_name"]
        assert "10.1.0." in detail["derived_name"], detail["derived_name"]
    finally:
        await unrestricted.aclose()
        await scoped.aclose()
