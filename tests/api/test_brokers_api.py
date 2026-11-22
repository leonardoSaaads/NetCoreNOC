"""ADRs #494, #496 — the brokers through the API, as an admin, a viewer and a remote proxy see them.

* an admin adds a broker in each of the three intakes; a port, a name or a range another broker
  holds is refused, and so is the standard trap port; nobody else may add one;
* a proxy's token is shown once, never read back, compared at the proxy's door, and replaced by a
  rotation that stops the old one at once;
* the proxy's door spools a numbered batch durably, skips a batch it already took, and refuses
  anything that is not a plain trap record;
* a screen inside a broker reads that network alone — situations, map, Timeline, elements, stats;
* a visibility policy may name brokers (`broker:<id>`), and the list of brokers follows it;
* a merge across brokers is refused; a window for a broker covers that network's elements only;
* only an empty broker may be removed; an element moves unless its address is taken there.
"""

from __future__ import annotations

import base64
import time
from typing import Any

from netcorenoc.ingest import records
from netcorenoc.store import Store

import authutil
import util

BASE = 1_700_000_000.0
STREAM = "spool-0001"


def _record(i: int, source: str = "10.0.0.1") -> str:
    raw = records.raw(source, 40000 + i, BASE + i, b"\x30\x00" + bytes([i % 256]))
    return base64.b64encode(raw).decode()


async def _env(store: Store) -> tuple[Any, Any, Any, Any]:
    engine, queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")
    return engine, queue, app, admin


async def _add(admin: Any, **body: Any) -> dict[str, Any]:
    resp = await admin.post("/api/brokers", json={"organization_id": 1, **body})
    assert resp.status_code == 200, resp.text
    return dict(resp.json())


async def test_an_admin_adds_a_broker_in_each_intake_and_conflicts_are_refused(
    store: Store,
) -> None:
    _engine, _queue, app, admin = await _env(store)
    north = await _add(admin, name="Claro Nordeste", udp_port=1162, networks=["10.1.0.0/16"])
    assert north["intake"] == "port" and north["networks"] == ["10.1.0.0/16"]
    assert "token" not in north, "only a proxy broker has a token"
    corp = await _add(admin, name="Corp", intake="cidr", networks=["192.0.2.0/24"])
    assert corp["intake"] == "cidr"

    for body, status in (
        ({"name": "Claro Nordeste", "udp_port": 1170}, 409),  # the name
        ({"name": "B", "udp_port": 1162}, 409),  # the port
        ({"name": "C", "intake": "cidr", "networks": ["10.1.0.0/16"]}, 409),  # the same range
        ({"name": "D", "udp_port": 162}, 422),  # below 1024
        ({"name": "E", "intake": "cidr"}, 422),  # ranges-only with no range
        ({"name": "F", "intake": "proxy", "networks": ["10.9.0.0/16"]}, 422),
        ({"name": "G", "udp_port": 1180, "networks": ["10.0.0.1/8"]}, 422),  # host bits
        ({"name": "H"}, 422),  # own port is the default, and it needs one
    ):
        resp = await admin.post("/api/brokers", json={"organization_id": 1, **body})
        assert resp.status_code == status, (body, resp.status_code, resp.text)
    # A narrower range than another broker's is fine: the longest prefix wins.
    await _add(admin, name="Claro Recife", intake="cidr", networks=["10.1.2.0/24"])

    viewer = await authutil.client_as(app, "viewer")
    denied = await viewer.post("/api/brokers", json={"organization_id": 1, "name": "X"})
    assert denied.status_code == 403
    listed = (await viewer.get("/api/brokers")).json()["brokers"]
    assert [b["name"] for b in listed][:1] == ["Default"], "the Default first, then by name"
    assert all("networks" not in b for b in listed), "where a network is, is an admin's"
    full = (await admin.get("/api/brokers")).json()["brokers"]
    assert {b["name"] for b in full} == {"Default", "Claro Nordeste", "Corp", "Claro Recife"}


async def test_a_proxy_token_is_shown_once_and_rotation_stops_the_old_one(store: Store) -> None:
    engine, _queue, app, admin = await _env(store)
    proxy = await _add(admin, name="Vivo Sul", intake="proxy")
    token = proxy["token"]
    assert token.startswith("ncp_") and len(token) > 40
    read = (await admin.get(f"/api/brokers/{proxy['id']}")).json()
    assert token not in str(read) and read["proxy_token_hint"] == token[-4:]

    spooled: list[bytes] = []

    async def spool(batch: list[bytes]) -> bool:
        spooled.extend(batch)
        return True

    engine.proxy_spool = spool
    door = authutil.new_client(app)
    body = {
        "stream": STREAM,
        "first": 1,
        "last": 2,
        "version": "test",
        "records": [_record(1), _record(2)],
    }
    resp = await door.post(
        "/api/brokers/ingest", json=body, headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200, resp.text
    rotated = (await admin.post(f"/api/brokers/{proxy['id']}/token")).json()["token"]
    assert rotated != token
    old = await door.post(
        "/api/brokers/ingest",
        json={**body, "first": 3, "last": 4},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert old.status_code == 401, "the old token stops at once"
    port = await _add(admin, name="Porta", udp_port=1190)
    assert (await admin.post(f"/api/brokers/{port['id']}/token")).status_code == 409


async def test_the_proxys_door_spools_once_and_refuses_what_is_not_a_trap(store: Store) -> None:
    engine, _queue, app, admin = await _env(store)
    proxy = await _add(admin, name="Remote", intake="proxy")
    auth = {"Authorization": f"Bearer {proxy['token']}"}
    door = authutil.new_client(app)
    batch = {"stream": STREAM, "first": 1, "last": 3, "records": [_record(i) for i in (1, 2, 3)]}
    unavailable = await door.post("/api/brokers/ingest", json=batch, headers=auth)
    assert unavailable.status_code == 503, "no spool: the proxy keeps the batch and sends it again"

    spooled: list[bytes] = []

    async def spool(items: list[bytes]) -> bool:
        spooled.extend(items)
        return True

    engine.proxy_spool = spool
    first = await door.post("/api/brokers/ingest", json=batch, headers=auth)
    assert first.json() == {"accepted": 3, "spooled": 3}
    assert all(records.envelope(p)[1] == proxy["id"] for p in spooled), "spooled as the broker's"
    again = await door.post("/api/brokers/ingest", json=batch, headers=auth)
    assert again.json() == {"accepted": 3, "spooled": 0}, "a batch sent twice is spooled once"
    overlap = {
        "stream": STREAM,
        "first": 2,
        "last": 5,
        "records": [_record(i) for i in (2, 3, 4, 5)],
    }
    assert (await door.post("/api/brokers/ingest", json=overlap, headers=auth)).json() == {
        "accepted": 5,
        "spooled": 2,
    }
    assert len(spooled) == 5
    heartbeat = {"stream": STREAM, "first": 6, "last": 5, "records": []}
    assert (await door.post("/api/brokers/ingest", json=heartbeat, headers=auth)).json() == {
        "accepted": 5,
        "spooled": 0,
    }, "a quiet proxy's heartbeat moves nothing and is answered"

    for bad, status in (
        ({"stream": STREAM, "first": 6, "last": 6, "records": ["not base64!"]}, 422),
        (
            {
                "stream": STREAM,
                "first": 6,
                "last": 6,
                "records": [base64.b64encode(records.for_broker(1, b"R")).decode()],
            },
            422,
        ),
        (
            {"stream": STREAM, "first": 6, "last": 7, "records": [_record(6)]},
            422,
        ),  # numbers and records disagree
    ):
        resp = await door.post("/api/brokers/ingest", json=bad, headers=auth)
        assert resp.status_code == status, (bad, resp.text)
    unknown = await door.post(
        "/api/brokers/ingest",
        json={"stream": STREAM, "first": 9, "last": 9, "records": [_record(9)]},
        headers={"Authorization": "Bearer ncp_guess"},
    )
    assert unknown.status_code == 401
    off = await admin.post(
        f"/api/brokers/{proxy['id']}",
        json={"name": "Remote", "organization_id": 1, "enabled": False},
    )
    assert off.status_code == 200, off.text
    refused = await door.post(
        "/api/brokers/ingest",
        json={"stream": STREAM, "first": 6, "last": 6, "records": [_record(6)]},
        headers=auth,
    )
    assert refused.status_code == 403, "a broker switched off refuses its proxy"


async def _two_networks(store: Store) -> tuple[Any, Any, Any, int, int]:
    engine, queue, app, admin = await _env(store)
    north = (await _add(admin, name="North", udp_port=1162))["id"]
    south = (await _add(admin, name="South", intake="proxy"))["id"]
    events = util.fixture_events("fiber_cut.json", BASE)
    await util.drive(
        engine,
        queue,
        [e.model_copy(update={"port": 1162}) for e in events]
        + [e.model_copy(update={"broker": south}) for e in events],
    )
    return engine, app, admin, north, south


async def test_a_screen_inside_a_broker_reads_that_network_alone(store: Store) -> None:
    _engine, _app, admin, north, south = await _two_networks(store)
    async with store.reading():
        cur = await store.conn.execute("SELECT id, broker_id FROM ne")
        owner = {int(r[0]): int(r[1]) for r in await cur.fetchall()}
    for broker in (north, south):
        mine = {ne for ne, b in owner.items() if b == broker}
        graph = (await admin.get(f"/api/graph?broker_id={broker}")).json()
        assert {n["id"] for n in graph["nodes"]} == mine
        assert all(e["a_id"] in mine and e["b_id"] in mine for e in graph["edges"])
        situations = (await admin.get(f"/api/situations?broker_id={broker}")).json()
        assert situations
        async with store.reading():
            of = await store.situation_brokers([int(s["id"]) for s in situations])
        assert set(of.values()) == {broker}
        entities = (await admin.get(f"/api/entities?broker_id={broker}")).json()
        assert {e["id"] for e in entities} == mine
        marks = (await admin.get(f"/api/timeline?broker_id={broker}&limit=1000")).json()["marks"]
        assert marks and {m["ne_id"] for m in marks} <= mine
        stats = (await admin.get(f"/api/stats?broker_id={broker}")).json()
        assert stats["devices"] == len(mine)
        inventory = (await admin.get(f"/api/inventory?broker_id={broker}")).json()
        assert {e["ne_id"] for e in inventory["elements"]} == mine
    everything = (await admin.get("/api/graph")).json()
    assert len(everything["nodes"]) == len(owner), "All brokers: every network"


async def test_a_policy_may_name_brokers_and_a_merge_across_brokers_is_refused(
    store: Store,
) -> None:
    _engine, app, admin, north, south = await _two_networks(store)
    await authutil.set_scope(store, {"roles": {"viewer": [f"broker:{north}"]}})
    viewer = await authutil.client_as(app, "viewer")
    names = [b["name"] for b in (await viewer.get("/api/brokers")).json()["brokers"]]
    assert names == ["North"]
    assert (await viewer.get(f"/api/brokers/{south}")).status_code == 404
    seen = (await viewer.get("/api/situations")).json()
    async with store.reading():
        of = await store.situation_brokers([int(s["id"]) for s in seen])
    assert seen and set(of.values()) == {north}
    narrowed = (await viewer.get(f"/api/situations?broker_id={south}")).json()
    assert narrowed == [], "a broker filter only narrows what the person may see"

    a = (await admin.get(f"/api/situations?broker_id={north}")).json()[0]["id"]
    b = (await admin.get(f"/api/situations?broker_id={south}")).json()[0]["id"]
    merged = await admin.post(
        f"/api/situations/{a}/merge", json={"from_situation_id": b, "confidence": 1.0}
    )
    assert merged.status_code == 409 and "different brokers" in merged.text


async def test_a_window_for_a_broker_and_the_filtered_list(store: Store) -> None:
    _engine, _app, admin, north, south = await _two_networks(store)
    async with store.reading():
        cur = await store.conn.execute("SELECT id, broker_id FROM ne ORDER BY id")
        rows = [(int(r[0]), int(r[1])) for r in await cur.fetchall()]
    mine = [ne for ne, b in rows if b == north][:2]
    theirs = [ne for ne, b in rows if b == south][:1]
    now = time.time()
    window = {
        "name": "North fibre works",
        "starts_at": now + 7200,
        "ends_at": now + 10800,
        "tz": "UTC",
        "broker_id": north,
    }
    refused = await admin.post("/api/maintenance-windows", json={**window, "targets": theirs})
    assert refused.status_code == 422 and "not in this window's broker" in refused.text
    made = await admin.post("/api/maintenance-windows", json={**window, "targets": mine})
    assert made.status_code == 200, made.text
    assert made.json()["broker_id"] == north
    other = await admin.post(
        "/api/maintenance-windows",
        json={**window, "name": "South works", "broker_id": south, "targets": theirs},
    )
    assert other.status_code == 200, other.text
    listed = (await admin.get(f"/api/maintenance-windows?broker_id={north}")).json()
    assert [w["name"] for w in listed["windows"]] == ["North fibre works"]
    assert listed["total"] == 1
    assert (await admin.get("/api/maintenance-windows")).json()["total"] == 2


async def test_only_an_empty_broker_is_removed_and_an_element_moves(store: Store) -> None:
    _engine, _app, admin, north, south = await _two_networks(store)
    empty = (await _add(admin, name="Spare", udp_port=1199))["id"]
    assert (await admin.delete(f"/api/brokers/{north}")).status_code == 409
    assert (await admin.delete("/api/brokers/1")).status_code == 409, "never the Default"
    assert (await admin.delete(f"/api/brokers/{empty}")).status_code == 200
    async with store.reading():
        cur = await store.conn.execute(
            "SELECT id FROM ne WHERE broker_id=? ORDER BY id LIMIT 1", (north,)
        )
        row = await cur.fetchone()
    assert row is not None
    ne = int(row[0])
    clash = await admin.post(f"/api/elements/{ne}/broker", json={"broker_id": south})
    assert clash.status_code == 409, "the south already has an element at that address"
    spare = (await _add(admin, name="Spare 2", udp_port=1198))["id"]
    moved = await admin.post(f"/api/elements/{ne}/broker", json={"broker_id": spare})
    assert moved.status_code == 200, moved.text
    async with store.reading():
        assert await store.ne_broker(ne) == spare


async def test_each_broker_pins_its_own_model(store: Store, packaged_league: object) -> None:
    engine, _queue, _app, admin = await _env(store)
    north = (await _add(admin, name="North", udp_port=1162))["id"]
    decider = (await admin.get(f"/api/decider?broker_id={north}")).json()
    assert decider["broker_id"] == north and decider["pinned"] is None
    other = next(m["ref"] for m in decider["members"] if m["ref"] != decider["running"])
    pinned = await admin.post(
        "/api/decider", json={"pin": other, "reason": "trial on the north", "broker_id": north}
    )
    assert pinned.status_code == 200, pinned.text
    async with store.lock:
        await engine.load_scorer_config()  # the reload point
        await store.commit()
    assert engine.lanes.of(north).decider_ref == other, "model B decides in the north"
    assert engine.lanes.default.decider_ref != other, "model A still decides elsewhere"
    judge = (await admin.get(f"/api/judge?broker_id={north}&brief=true")).json()
    assert judge["pinned"] == other and judge["broker_id"] == north
    assert (await admin.get("/api/decider")).json()["pinned"] is None
    assert (await admin.get("/api/decider?broker_id=999")).status_code == 404


async def test_a_customer_change_moves_its_elements_but_not_the_defaults(store: Store) -> None:
    """A broker's elements follow its customer; the Default broker's never do — it holds every
    customer's unclaimed elements — and an edit that keeps the customer changes no element."""
    from netcorenoc.crosscutting import shaping

    _engine, _app, admin, north, _south = await _two_networks(store)
    made = await admin.post("/api/organizations", json={"name": "Claro", "slug": "claro"})
    claro = made.json()["id"]
    body = {"name": "North", "organization_id": claro, "udp_port": 1162, "enabled": True}
    changed = await admin.post(f"/api/brokers/{north}", json=body)
    assert changed.status_code == 200, changed.text
    async with store.reading():
        cur = await store.conn.execute(
            "SELECT DISTINCT organization_id FROM ne WHERE broker_id=?", (north,)
        )
        assert [r[0] for r in await cur.fetchall()] == [claro]
    async with store.lock:
        await store.conn.execute("UPDATE ne SET organization_id=? WHERE broker_id=1", (claro,))
        await store.commit()
    default = {"name": "Default", "organization_id": claro, "description": "renamed"}
    assert (await admin.post("/api/brokers/1", json=default)).status_code == 200
    refused = await admin.post("/api/brokers/1", json={**default, "udp_port": 1190})
    assert refused.status_code == 422, "the Default broker takes no port"
    policy = shaping.parse_scope_policy('{"roles": {"viewer": ["broker:abc", "broker:2"]}}')
    problems = shaping.scope_policy_errors(policy)
    assert len(problems) == 1 and "broker:abc" in problems[0]
