"""Gate 3 evidence: situations, explanations, labels, and feedback over the HTTP API."""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from opticorr.api import create_app
from opticorr.main import Engine, Settings, run
from opticorr.receiver import QueueItem
from opticorr.store import Store

import trap_replay
import util

TOKEN = "test-token-123"
BASE = 2_000_000.0


@pytest.fixture
async def engine_env(store: Store) -> tuple[Engine, asyncio.Queue[QueueItem]]:
    queue: asyncio.Queue[QueueItem] = asyncio.Queue()
    engine = Engine(store, queue)
    await engine.start()
    return engine, queue


async def _seed_admin_token(store: Store, value: str = TOKEN) -> None:
    """The legacy shared token is gone (§5.8); the v0.2.0 API tests now authenticate with a
    real admin service token bound to the Bearer value."""
    from opticorr import auth

    async with store.lock:
        await store.create_token(auth.hash_token(value), "test", "admin", "adm", 0.0)
        await store.commit()


@pytest.fixture
async def client(
    engine_env: tuple[Engine, asyncio.Queue[QueueItem]],
) -> AsyncIterator[httpx.AsyncClient]:
    await _seed_admin_token(engine_env[0].store)
    app = create_app(engine_env[0], extra_stats=lambda: {"receiver": {"received": 7}})
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://opticorr.test",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as c:
        yield c


async def replay_fiber(engine_env: tuple[Engine, asyncio.Queue[QueueItem]]) -> None:
    engine, queue = engine_env
    await util.drive(engine, queue, util.fixture_events("fiber_cut.json", BASE))


async def test_healthz_needs_no_token(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz", headers={"Authorization": ""})
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_index_serves_the_single_file_ui(client: httpx.AsyncClient) -> None:
    response = await client.get("/", headers={"Authorization": ""})
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "d3" in response.text and "OptiCorr" in response.text


async def test_api_requires_token(client: httpx.AsyncClient) -> None:
    for headers in ({"Authorization": ""}, {"Authorization": "Bearer wrong"}):
        response = await client.get("/api/stats", headers=headers)
        assert response.status_code == 401


async def test_stats_reports_store_engine_and_receiver(
    client: httpx.AsyncClient, engine_env: tuple[Engine, asyncio.Queue[QueueItem]]
) -> None:
    await replay_fiber(engine_env)
    stats = (await client.get("/api/stats")).json()
    assert stats["devices"] == 2 and stats["active_alarms"] == 8
    assert stats["receiver"] == {"received": 7}
    assert "latency_p95_s" in stats and "queue_depth" in stats


async def test_situations_with_explanations_and_root(
    client: httpx.AsyncClient, engine_env: tuple[Engine, asyncio.Queue[QueueItem]]
) -> None:
    await replay_fiber(engine_env)
    listed = (await client.get("/api/situations", params={"status": "open"})).json()
    assert len(listed) == 1 and listed[0]["alarm_count"] == 8
    detail = (await client.get(f"/api/situations/{listed[0]['id']}")).json()
    assert len(detail["alarms"]) == 8
    assert detail["root_alarm_id"] in {a["id"] for a in detail["alarms"]}
    assert detail["links"], "links with score terms are the explanation"
    for link in detail["links"]:
        assert {"score", "term_t", "term_a", "term_e"} <= link.keys()
    assert (await client.get("/api/situations/424242")).status_code == 404
    assert (await client.get("/api/situations", params={"status": "bogus"})).status_code == 422


async def test_graph_exposes_learned_topology(
    client: httpx.AsyncClient, engine_env: tuple[Engine, asyncio.Queue[QueueItem]]
) -> None:
    engine, _ = engine_env
    await replay_fiber(engine_env)
    await engine.learner.save(engine.store, BASE)  # normally done by maintenance
    await engine.store.commit()
    graph = (await client.get("/api/graph")).json()
    assert {n["ip"] for n in graph["nodes"]} == {"127.0.0.2", "127.0.0.3"}
    assert len(graph["edges"]) == 1  # the learned NE-A — NE-B edge is already trusted
    edge = graph["edges"][0]
    assert edge["weight"] > 0.5 and edge["n"] >= 5


async def test_split_feedback_via_http_measurably_reduces_affinity(
    client: httpx.AsyncClient, engine_env: tuple[Engine, asyncio.Queue[QueueItem]]
) -> None:
    engine, _ = engine_env
    await replay_fiber(engine_env)
    sid = (await client.get("/api/situations")).json()[0]["id"]
    device_a = await engine.store.device_id("127.0.0.2", BASE)
    device_b = await engine.store.device_id("127.0.0.3", BASE)
    mass_before = engine.learner.E.pair_mass(device_a, device_b)
    assert mass_before > 0
    response = await client.post(f"/api/situations/{sid}/feedback", json={"verdict": "split"})
    assert response.status_code == 200
    assert engine.learner.E.pair_mass(device_a, device_b) == mass_before * 0.5
    cur = await engine.store.conn.execute("SELECT verdict FROM feedback")
    rows = [r["verdict"] for r in await cur.fetchall()]
    assert rows == ["split"]


async def test_confirm_feedback_and_validation(
    client: httpx.AsyncClient, engine_env: tuple[Engine, asyncio.Queue[QueueItem]]
) -> None:
    await replay_fiber(engine_env)
    sid = (await client.get("/api/situations")).json()[0]["id"]
    assert (
        await client.post(f"/api/situations/{sid}/feedback", json={"verdict": "confirm"})
    ).status_code == 200
    assert (
        await client.post(f"/api/situations/{sid}/feedback", json={"verdict": "merge"})
    ).status_code == 422
    assert (
        await client.post("/api/situations/424242/feedback", json={"verdict": "split"})
    ).status_code == 404


async def test_labels_are_cosmetic_and_persisted(
    client: httpx.AsyncClient, engine_env: tuple[Engine, asyncio.Queue[QueueItem]]
) -> None:
    engine, _ = engine_env
    await replay_fiber(engine_env)
    device_a = await engine.store.device_id("127.0.0.2", BASE)
    sid = (await client.get("/api/situations")).json()[0]["id"]
    first_class = (await client.get(f"/api/situations/{sid}")).json()["alarms"][0]
    response = await client.post(
        "/api/labels", json={"kind": "device", "id": device_a, "label": "dwdm-core-1"}
    )
    assert response.status_code == 200
    cur = await engine.store.conn.execute(
        "SELECT c.id FROM alarm_class c WHERE c.oid=?", (first_class["class_oid"],)
    )
    row = await cur.fetchone()
    assert row is not None
    await client.post("/api/labels", json={"kind": "class", "id": row["id"], "label": "LOS"})
    detail = (await client.get(f"/api/situations/{sid}")).json()
    labelled = next(
        a
        for a in detail["alarms"]
        if a["device_ip"] == "127.0.0.2" and a["class_oid"] == first_class["class_oid"]
    )
    assert labelled["device_label"] == "dwdm-core-1"
    assert labelled["class_label"] == "LOS"
    graph = (await client.get("/api/graph")).json()
    assert any(n["label"] == "dwdm-core-1" for n in graph["nodes"])
    assert (
        await client.post("/api/labels", json={"kind": "rack", "id": 1, "label": "x"})
    ).status_code == 422
    assert (
        await client.post("/api/labels", json={"kind": "device", "id": 1, "label": ""})
    ).status_code == 422


async def test_rate_limit_returns_429(
    engine_env: tuple[Engine, asyncio.Queue[QueueItem]],
) -> None:
    await _seed_admin_token(engine_env[0].store)
    app = create_app(engine_env[0], rate_capacity=5, rate_refill=0.0001)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://opticorr.test",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as client:
        statuses = [(await client.get("/api/stats")).status_code for _ in range(8)]
    assert statuses[:5] == [200] * 5
    assert 429 in statuses[5:]


async def test_end_to_end_udp_replay_to_http(
    client: httpx.AsyncClient, engine_env: tuple[Engine, asyncio.Queue[QueueItem]]
) -> None:
    """The demo path: real trap PDUs over UDP, verified through the HTTP API."""
    from opticorr.receiver import start_receiver

    engine, queue = engine_env
    transport, _receiver = await start_receiver(queue, "127.0.0.1", 0)
    port = transport.get_extra_info("sockname")[1]
    engine_task = asyncio.create_task(engine.run())
    sender = trap_replay.Sender(("127.0.0.1", port))
    try:
        for entry in json.loads((util.FIXTURES / "fiber_cut.json").read_text())["events"]:
            sender.send(
                entry["source"],
                trap_replay.encode_trap(entry["trap_oid"], entry["varbinds"], "public", 1),
            )

        async def settled() -> bool:
            stats = (await client.get("/api/stats")).json()
            return int(stats["active_alarms"]) == 8

        await util.eventually(settled)
    finally:
        sender.close()
        transport.close()
        engine_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await engine_task
    listed = (await client.get("/api/situations", params={"status": "open"})).json()
    assert sum(row["alarm_count"] for row in listed) == 8


async def test_concurrent_api_reads_during_ingest(
    client: httpx.AsyncClient, engine_env: tuple[Engine, asyncio.Queue[QueueItem]]
) -> None:
    """Regression: an API cursor interleaving with the engine's batch commit used to
    raise 'cannot commit transaction - SQL statements in progress' and kill the engine.
    The store lock serializes them; the engine must survive a busy stream while the
    API is polled."""
    engine, queue = engine_env
    events = [
        util.event(
            device=f"10.2.0.{i % 40}",
            trap_oid=f"1.3.6.1.4.1.9.9.55.{i % 25}",
            instance=str(i % 4),
            ts=3_000_000.0 + i * 0.01,
        )
        for i in range(2500)
    ]
    for e in events:
        queue.put_nowait(e)
    engine_task = asyncio.create_task(engine.run())
    try:
        while engine.processed < len(events) or not queue.empty():
            assert not engine_task.done(), engine_task.exception()
            assert (await client.get("/api/stats")).status_code == 200
            assert (await client.get("/api/situations")).status_code == 200
            await asyncio.sleep(0)
        assert engine.processed == len(events)
    finally:
        engine_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await engine_task


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def test_main_run_serves_real_sockets(tmp_path: Path) -> None:
    """Smoke test of the single-process wiring: UDP trap in, HTTP API out."""
    from opticorr import auth

    db = str(tmp_path / "run.db")
    settings = Settings(
        db_path=db,
        trap_host="127.0.0.1",
        trap_port=free_port(),
        http_host="127.0.0.1",
        http_port=free_port(),
    )
    # The legacy shared token was removed in v0.3.0; pre-seed a real service token instead.
    seed = Store(db)
    await seed.open()
    async with seed.lock:
        await auth.bootstrap_admin(seed, 0.0)
        await seed.create_token(auth.hash_token(TOKEN), "smoke", "admin", "admin", 0.0)
        await seed.commit()
    await seed.close()

    task = asyncio.create_task(run(settings))
    base = f"http://127.0.0.1:{settings.http_port}"
    try:
        async with httpx.AsyncClient(
            headers={"Authorization": f"Bearer {TOKEN}"}, timeout=2.0
        ) as client:

            async def up() -> bool:
                with contextlib.suppress(httpx.TransportError):
                    return (await client.get(f"{base}/healthz")).status_code == 200
                return False

            await util.eventually(up, timeout=10.0)
            sender = trap_replay.Sender(("127.0.0.1", settings.trap_port))
            sender.send("127.0.0.2", trap_replay.encode_trap(util.CIENA_TRAP, [], "public", 1))
            sender.close()

            async def ingested() -> bool:
                stats = (await client.get(f"{base}/api/stats")).json()
                return int(stats["active_alarms"]) == 1

            await util.eventually(ingested, timeout=10.0)
            page = await client.get(f"{base}/")
            assert "OptiCorr" in page.text
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
