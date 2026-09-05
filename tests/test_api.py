"""Gate 3 evidence: situations, explanations, labels, and feedback over the HTTP API."""

from __future__ import annotations

import asyncio
import contextlib
import socket
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from netcorenoc.api import create_app
from netcorenoc.crosscutting import administration
from netcorenoc.ingest.receiver import QueueItem
from netcorenoc.main import Engine, Settings, run
from netcorenoc.store import Store

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
    from netcorenoc.crosscutting import auth

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
        base_url="http://netcorenoc.test",
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
    # v0.13.0: the document is a mount point. What it must still do is name the script a browser
    # has to fetch, by a path that exists on disk — no bundle, no manifest, no import map.
    #
    # **v0.15.2 (DECISIONS #228): one script, not two.** d3 was the other, and it made every screen
    # pay 279 706 bytes for the two that draw with it; `app/vendor.js` appends the same same-origin
    # element when one of those mounts. That `/vendor/d3.v7.min.js` is still named *somewhere*, and
    # that every named `src` is same-origin and on disk, is asserted by
    # `test_build_step.py::test_the_ui_is_still_loaded_directly_by_the_browser` — which now checks
    # every console file rather than this one document, and is red when `vendor.js` points at a CDN.
    assert "NetCoreNOC" in response.text
    assert 'src="/vendor/d3.v7.min.js"' not in response.text
    assert 'type="module" src="/app.js"' in response.text
    assert "importmap" not in response.text


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
    # v0.16.0: a situation the correlator formed and nobody has triaged is `new` (DECISIONS #254).
    listed = (await client.get("/api/situations", params={"status": "new"})).json()
    assert len(listed) == 1 and listed[0]["alarm_count"] == 8
    detail = (await client.get(f"/api/situations/{listed[0]['id']}")).json()
    assert len(detail["alarms"]) == 8
    assert detail["root_alarm_id"] in {a["id"] for a in detail["alarms"]}
    assert detail["links"], "links with score terms are the explanation"
    for link in detail["links"]:
        assert {"score", "term_t", "term_a", "term_e"} <= link.keys()
    assert (await client.get("/api/situations/424242")).status_code == 404
    assert (await client.get("/api/situations", params={"status": "bogus"})).status_code == 422


async def test_a_situation_that_is_idle_and_still_burning_is_marked_stale(
    client: httpx.AsyncClient,
    engine_env: tuple[Engine, asyncio.Queue[QueueItem]],
    store: Store,
) -> None:
    """`stale` on the list row (v0.16.2, DECISIONS #274), with a control on each axis.

    The mark the console badges for the population the idle sweep used to resolve out of every live
    view. This suite replays at `BASE = 2_000_000.0`, so the situation the fibre cut forms is
    already an eternity old by the wall clock the route reads — which is the treatment arm, and
    also a check that the route reads a clock at all.

    Both controls are load-bearing and each moves one axis:

      * touch the same situation to **now** and the mark clears — so the field is not just
        "has an active alarm";
      * a situation just as old whose alarms have all **cleared** is never marked — so it is not
        just "is old".
    """
    await replay_fiber(engine_env)
    listed = (await client.get("/api/situations")).json()
    assert listed and all("stale" in row for row in listed), "the field is not served at all"
    burning = int(listed[0]["id"])
    assert listed[0]["stale"] is True, "an idle situation with a live alarm is not marked"

    async with store.lock:
        cleared_sid = await store.create_situation(BASE, None)
        cleared_alarm = await store.ingest(util.event(device="10.5.5.5", ts=BASE))
        await store.add_alarm_to_situation(cleared_sid, cleared_alarm.alarm_id)
        await store.conn.execute(
            "UPDATE alarm SET status='cleared', cleared_at=? WHERE id=?",
            (BASE + 1.0, cleared_alarm.alarm_id),
        )
        await store.touch_situation(cleared_sid, BASE)
        await store.commit()
    rows = {int(r["id"]): r for r in (await client.get("/api/situations")).json()}
    assert rows[cleared_sid]["stale"] is False, "a bag with nothing active was marked stale"

    async with store.lock:
        await store.touch_situation(burning, time.time())
        await store.commit()
    rows = {int(r["id"]): r for r in (await client.get("/api/situations")).json()}
    assert rows[burning]["stale"] is False, "a situation touched just now is still marked stale"


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
        base_url="http://netcorenoc.test",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as client:
        statuses = [(await client.get("/api/stats")).status_code for _ in range(8)]
    assert statuses[:5] == [200] * 5
    assert 429 in statuses[5:]


async def test_end_to_end_udp_replay_to_http(
    client: httpx.AsyncClient, engine_env: tuple[Engine, asyncio.Queue[QueueItem]]
) -> None:
    """The demo path: real trap PDUs over UDP, verified through the HTTP API."""
    from netcorenoc.ingest.receiver import start_receiver

    engine, queue = engine_env
    transport, _receiver = await start_receiver(queue, "127.0.0.1", 0)
    port = transport.get_extra_info("sockname")[1]
    engine_task = asyncio.create_task(engine.run())
    sender = trap_replay.Sender(("127.0.0.1", port))
    try:
        for entry in util.scenario("fiber_cut.json")["events"]:
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
    listed = (await client.get("/api/situations", params={"status": "new"})).json()
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
    from netcorenoc.crosscutting import auth

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
        await administration.bootstrap_admin(seed, 0.0)
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
            assert "NetCoreNOC" in page.text
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


# --- v0.7.1: the write perimeter ------------------------------------------------------------
#
# F36, F37 and F39 of `docs/security/SECURITY-REVIEW-0.7.1.md` — the write-discipline half of the
# release. F34/F35/F38 (scope and authorization) live in `tests/test_governance.py`.


@pytest.fixture
async def burst_client(
    engine_env: tuple[Engine, asyncio.Queue[QueueItem]],
) -> AsyncIterator[httpx.AsyncClient]:
    """Like `client`, but with the per-IP limiter effectively disabled.

    F36 is about what the *learning* path does under repetition, not about what the limiter does.
    The limiter (30 burst, 10/s) only paces the attack — ~600 posts a minute still reaches every
    learned mass — so a test that stopped at the 429 would be testing the wrong control.
    """
    await _seed_admin_token(engine_env[0].store)
    app = create_app(engine_env[0], rate_capacity=100000.0)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://netcorenoc.test",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as c:
        yield c


async def test_f36_repeated_feedback_is_idempotent_and_bounded(
    engine_env: tuple[Engine, asyncio.Queue[QueueItem]], burst_client: httpx.AsyncClient
) -> None:
    """Operator feedback must not be an unbounded lever on global learned state.

    `learn_epoch` ticks the global forgetting epoch on **both** matrices, every mass decays by
    `(1-LAMBDA)` per epoch, and `add_feedback` has no uniqueness, no dedupe and no bound. So N
    identical posts drive N epochs: at LAMBDA=0.05, ~600 epochs a minute takes every learned mass
    to ~1e-14. `split` compounds it by halving each pair mass as well. The role that can do it is
    `editor`, the least-privileged role that can write at all.
    """
    engine, _queue = engine_env
    await replay_fiber(engine_env)
    sid = (await burst_client.get("/api/situations")).json()[0]["id"]
    learner = engine.learner
    epoch_before = (learner.A.epoch, learner.E.epoch)
    pair = next(iter(learner.A.pairs), None)
    assert pair is not None, "the fixture must have taught the class matrix something"
    mass_before = learner.A.pair_mass(*pair)

    for _ in range(60):
        resp = await burst_client.post(
            f"/api/situations/{sid}/feedback", json={"verdict": "confirm"}
        )
        assert resp.status_code == 200, resp.text
    for _ in range(20):
        assert (
            await burst_client.post(f"/api/situations/{sid}/feedback", json={"verdict": "split"})
        ).status_code == 200

    assert (learner.A.epoch, learner.E.epoch) == epoch_before, (
        "operator feedback advanced the global forgetting epoch: "
        f"{epoch_before} -> {(learner.A.epoch, learner.E.epoch)}. An epoch is a closed situation."
    )
    mass_after = learner.A.pair_mass(*pair)
    assert mass_after > mass_before * 0.1, (
        f"80 posts drove pair {pair} from {mass_before:.6f} to {mass_after:.3e} — the effect of "
        "repeated feedback is unbounded"
    )
    async with engine.store.lock:
        cur = await engine.store.conn.execute("SELECT COUNT(*) FROM feedback")
        rows = int((await cur.fetchone())[0])  # type: ignore[index]
    assert rows == 2, f"80 posts of 2 distinct verdicts recorded {rows} feedback rows, not 2"


async def test_f36_a_changed_verdict_still_applies_once(
    engine_env: tuple[Engine, asyncio.Queue[QueueItem]], client: httpx.AsyncClient
) -> None:
    """Idempotence is per `(situation, verdict)`: a *correction* is legitimate and must land."""
    engine, _queue = engine_env
    await replay_fiber(engine_env)
    sid = (await client.get("/api/situations")).json()[0]["id"]
    learner = engine.learner
    pair = next(iter(learner.A.pairs), None)
    assert pair is not None
    before = learner.A.pair_mass(*pair)
    assert (
        await client.post(f"/api/situations/{sid}/feedback", json={"verdict": "split"})
    ).status_code == 200
    after_split = learner.A.pair_mass(*pair)
    assert after_split < before, "a split must actually penalize"
    # The same verdict again is a no-op…
    await client.post(f"/api/situations/{sid}/feedback", json={"verdict": "split"})
    assert learner.A.pair_mass(*pair) == after_split
    # …but the operator changing their mind is a correction and applies once.
    assert (
        await client.post(f"/api/situations/{sid}/feedback", json={"verdict": "confirm"})
    ).status_code == 200
    assert learner.A.pair_mass(*pair) > after_split


async def test_f36_closing_a_situation_still_ticks_the_epoch(
    engine_env: tuple[Engine, asyncio.Queue[QueueItem]], client: httpx.AsyncClient
) -> None:
    """The epoch belongs to a **closed situation** — which is what `learn.py` already says it is.

    Moving the tick off the feedback path must not remove it from the path that owns it.
    """
    engine, _queue = engine_env
    await replay_fiber(engine_env)
    sid = next(
        s["id"] for s in (await client.get("/api/situations")).json() if s["status"] == "new"
    )
    before = engine.learner.A.epoch
    async with engine.store.lock:
        await engine._close_situation(sid, BASE + 5000.0)
        await engine.store.commit()
    assert engine.learner.A.epoch == before + 1, (
        f"closing a situation must advance the epoch exactly once: {before} -> "
        f"{engine.learner.A.epoch}"
    )


async def test_f36_feedback_records_its_author(
    engine_env: tuple[Engine, asyncio.Queue[QueueItem]], client: httpx.AsyncClient
) -> None:
    """Without an author, "who degraded the matrices?" is unanswerable: the v0.7.0 audit chain
    records the API call but not the effect."""
    engine, _queue = engine_env
    await replay_fiber(engine_env)
    sid = (await client.get("/api/situations")).json()[0]["id"]
    assert (
        await client.post(f"/api/situations/{sid}/feedback", json={"verdict": "confirm"})
    ).status_code == 200
    async with engine.store.lock:
        cur = await engine.store.conn.execute(
            "SELECT principal_ref, role FROM feedback WHERE situation_id=?", (sid,)
        )
        row = await cur.fetchone()
    assert row is not None
    assert row["principal_ref"] == "token:1" and row["role"] == "admin", dict(row)


async def test_f37_a_label_write_to_a_nonexistent_target_is_rejected(
    engine_env: tuple[Engine, asyncio.Queue[QueueItem]], client: httpx.AsyncClient
) -> None:
    """`store.set_label` is an unconditional UPSERT into a table with no foreign key, and
    `store.prune()` never touches it — so any editor holds an unbounded, never-reclaimed write
    primitive against the database file."""
    await replay_fiber(engine_env)
    store = engine_env[0].store
    for target in range(900000, 900005):
        resp = await client.post(
            "/api/labels", json={"kind": "device", "id": target, "label": "ghost"}
        )
        assert resp.status_code == 404, (
            f"a label was written to nonexistent device {target}: {resp.status_code} {resp.text}"
        )
    missing_class = await client.post(
        "/api/labels", json={"kind": "class", "id": 900000, "label": "ghost"}
    )
    assert missing_class.status_code == 404, missing_class.text
    async with store.lock:
        cur = await store.conn.execute("SELECT COUNT(*) FROM label WHERE target_id >= 900000")
        assert int((await cur.fetchone())[0]) == 0  # type: ignore[index]


async def test_f37_label_writes_to_real_targets_still_work(
    engine_env: tuple[Engine, asyncio.Queue[QueueItem]], client: httpx.AsyncClient
) -> None:
    """The existence check must not break the feature it guards."""
    await replay_fiber(engine_env)
    device_id = (await client.get("/api/graph")).json()["nodes"][0]["id"]
    class_id = (await client.get("/api/classes")).json()[0]["id"]
    assert (
        await client.post("/api/labels", json={"kind": "device", "id": device_id, "label": "core"})
    ).status_code == 200
    assert (
        await client.post("/api/labels", json={"kind": "class", "id": class_id, "label": "LOS"})
    ).status_code == 200


async def test_f39_a_failed_write_leaves_nothing_to_commit(
    engine_env: tuple[Engine, asyncio.Queue[QueueItem]],
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One `aiosqlite` connection is shared by the engine and the API. `main.py` rolls back;
    `api.py` does not. A handler that mutates and then raises leaves the statement pending on
    that shared connection, and the **next commit from any other caller adopts it** — so the
    mutation lands, with no audit row, committed by someone else entirely.
    """
    from netcorenoc.crosscutting import audit as audit_module

    engine, _queue = engine_env
    store = engine.store
    async with store.lock:
        uid = await store.create_user("victim", "x", "viewer", False, BASE)
        await store.commit()

    real_write_event = audit_module.write_event

    async def exploding_write_event(*args: object, **kwargs: object) -> None:
        if kwargs.get("action") == "role.change":
            raise RuntimeError("audit backend down")
        await real_write_event(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(audit_module, "write_event", exploding_write_event)
    with contextlib.suppress(Exception):
        await client.post(f"/api/users/{uid}/role", json={"role": "admin"})
    monkeypatch.setattr(audit_module, "write_event", real_write_event)

    # Any other caller's commit must not adopt the abandoned mutation.
    async with store.lock:
        await store.commit()
        user = await store.get_user(uid)
    assert user is not None and user["role"] == "viewer", (
        "an uncommitted, unaudited role change survived a failed request and was committed by an "
        f"unrelated caller: role is now {user['role'] if user else None!r}"
    )


async def test_f39_feedback_commits_exactly_once(
    engine_env: tuple[Engine, asyncio.Queue[QueueItem]], client: httpx.AsyncClient
) -> None:
    """`Engine.apply_feedback` commits internally, then the handler commits again — one route,
    two transactions, mutation durable *before* it is attributable. The API owns the boundary.

    Counted as a **delta over a read**, because every authenticated request also commits once in
    `resolve_identity` (the session touch). The quantity under test is the commits the *write path*
    adds on top of that, and it must be exactly one: mutate → audit → commit.
    """
    engine, _queue = engine_env
    await replay_fiber(engine_env)
    sid = (await client.get("/api/situations")).json()[0]["id"]
    store = engine.store
    commits = 0
    real_commit = store.commit

    async def counting_commit() -> None:
        nonlocal commits
        commits += 1
        await real_commit()

    store.commit = counting_commit  # type: ignore[method-assign]
    try:
        await client.get("/api/situations")
        baseline = commits  # identity resolution only
        commits = 0
        resp = await client.post(f"/api/situations/{sid}/feedback", json={"verdict": "confirm"})
        assert resp.status_code == 200, resp.text
        write_commits = commits - baseline
    finally:
        store.commit = real_commit  # type: ignore[method-assign]
    assert write_commits == 1, (
        f"POST /feedback committed {write_commits} time(s) beyond identity resolution, not 1 — "
        "the mutation is durable before its audit row"
    )


# -- v0.13.0: the three-column precedence `GET /api/config` reports (ADR #179) --------------------


async def test_config_precedence_distinguishes_the_environment_from_the_override(
    client: httpx.AsyncClient,
) -> None:
    """`UI-0.13-DRAFT.md` §7.1: environment default, database override, effective — **three
    distinct things**, because an operator's real question during an incident is *"why is this
    value what it is?"* and two sources behind one number cannot answer it.

    **This test exists because a mutation survived without it.** Replacing `env.allowlist` with the
    saved override in the response left every test in the repository green: the settings screen
    would have rendered the same value in the "environment default" and "database override"
    columns, which reads as *"the environment was already set to this"* — the opposite of what
    happened — and the precedence table would have been decoration.

    Asserted in both states, before and after a save, because the interesting one is *after*: with
    no override the two agree by definition and the mutant is invisible.
    """
    before = (await client.get("/api/config")).json()
    assert before["precedence"]["allowlist"]["override"] is None
    assert before["precedence"]["retention_days"]["override"] is None
    environment_default = before["precedence"]["allowlist"]["env"]
    environment_retention = before["precedence"]["retention_days"]["env"]
    assert before["allowlist"] == environment_default
    assert before["retention_days"] == environment_retention

    saved = await client.post(
        "/api/config", json={"allowlist": "10.9.0.0/16", "retention_days": 3.5}
    )
    assert saved.status_code == 200

    after = (await client.get("/api/config")).json()
    precedence = after["precedence"]
    # The override is what was saved…
    assert precedence["allowlist"]["override"] == "10.9.0.0/16"
    assert precedence["retention_days"]["override"] == 3.5
    # …the effective value follows it…
    assert after["allowlist"] == "10.9.0.0/16"
    assert after["retention_days"] == 3.5
    # …and the environment column still reports the ENVIRONMENT, unmoved by the save. These are
    # the assertions the mutants escaped — **both fields**, because the first version of this test
    # checked only the allowlist and a second ledger run showed the retention half still living.
    assert precedence["allowlist"]["env"] == environment_default
    assert precedence["allowlist"]["env"] != precedence["allowlist"]["override"]
    assert precedence["retention_days"]["env"] == environment_retention
    assert precedence["retention_days"]["env"] != precedence["retention_days"]["override"]


async def test_config_reports_what_needs_a_restart_without_disclosing_tls_paths(
    client: httpx.AsyncClient,
) -> None:
    """`startup` exists so the console can say which changes need a restart **before** one is
    attempted (draft §7.3), and it is deliberately narrower than `Settings`.

    `config.read` is in `AUDITED_DENIED_PERMISSIONS` because reading the allowlist reveals
    network-security posture (F9). Adding filesystem paths to that response would widen what one
    audited capability discloses for no operator benefit, so TLS is one boolean and the removed
    shared token is absent entirely.
    """
    startup = (await client.get("/api/config")).json()["startup"]
    for key in ("trap_host", "trap_port", "http_host", "http_port", "audit_retention_days"):
        assert key in startup, key
    assert startup["tls_enabled"] in (True, False)
    assert "tls_cert" not in startup and "tls_key" not in startup
    assert "api_token" not in startup
    body = (await client.get("/api/config")).text
    assert "api_token" not in body
