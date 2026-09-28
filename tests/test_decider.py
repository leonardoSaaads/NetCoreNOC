"""The decider (ADR #405): what decides links, through the real engine and the real routes.

A small hand-written model stands in for the shipped one, so these tests check the *mechanics* —
which family runs, what candidates it sees, how it groups, what the routes permit — and never the
shipped model's quality, which `make train` measures and `tests/test_shipped.py` pins.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Iterator

import pytest

from netcorenoc.engine.model import gam, shipped
from netcorenoc.engine.operate.engine import CLEAR_HOLD_S, Engine
from netcorenoc.ingest.events import TrapEvent, Varbind
from netcorenoc.store import Store

import authutil

T = 1_800_000_000.0

TEST_MODEL = json.dumps(
    {
        "features": ["dt", "same_ne", "same_class", "oid_arcs"],
        "format": gam.FORMAT,
        "grouping": {"join_bias": 0.0, "merge_bias": 1.0, "merge_min_pairs": 3.0},
        "interactions": [],
        "intercept": -1.0,
        "shapes": [
            {"edges": [5.0, 60.0, 600.0], "feature": "dt", "scores": [2.0, 1.0, 0.0, -1.0]},
            {"edges": [0.5], "feature": "same_ne", "scores": [-1.0, 2.5]},
            {"edges": [0.5], "feature": "same_class", "scores": [-0.5, 1.5]},
            # 1271.2.1.x vs 1271.2.1.y share 9 arcs; incident A (1271.2.1) vs B (1271.2.9) share 8.
            {"edges": [8.5], "feature": "oid_arcs", "scores": [-1.5, 1.0]},
        ],
        "threshold": 0.0,
    },
    sort_keys=True,
    separators=(",", ":"),
)


def _manifest(document: str) -> str:
    return json.dumps({"artifact": {"sha256": hashlib.sha256(document.encode()).hexdigest()}})


@pytest.fixture
def test_model(monkeypatch: pytest.MonkeyPatch) -> Iterator[shipped.Shipped]:
    model = shipped.load_from(TEST_MODEL, _manifest(TEST_MODEL))
    monkeypatch.setattr(shipped, "load", lambda: model)
    yield model


def _trap(device: str, oid: str, instance: str, ts: float) -> TrapEvent:
    return TrapEvent(device=device, trap_oid=oid, instance=instance, ts=ts,
                     varbinds=[Varbind(oid="1.3.6.1.4.1.1271.9.1", kind="str", value=instance)])


async def _ingest(engine: Engine, store: Store, traps: list[TrapEvent]) -> None:
    async with store.lock:
        for trap in traps:
            await engine._process(trap)
        await store.commit()


async def _situations(store: Store) -> list[set[int]]:
    cur = await store.conn.execute(
        "SELECT sa.situation_id, sa.alarm_id FROM situation_alarm sa JOIN situation s "
        "ON s.id = sa.situation_id WHERE s.status IN ('new','open') ORDER BY 1, 2"
    )
    out: dict[int, set[int]] = {}
    for sid, aid in await cur.fetchall():
        out.setdefault(int(sid), set()).add(int(aid))
    return list(out.values())


async def test_a_fresh_appliance_runs_the_shipped_model(store: Store, test_model: shipped.Shipped) -> None:
    assert await store.decider_mode() == "shipped"
    engine = Engine(store, asyncio.Queue())
    await engine.start()
    assert engine.decider_ref == test_model.ref
    assert engine.correlator.two_stage, "a trained model gets two-stage recall"
    assert engine.correlator.grouper.mode == "cluster"


async def test_a_refused_shipped_model_falls_back_to_the_formula_and_says_why(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse() -> shipped.Shipped:
        raise shipped.ShippedModelError("the model document was refused: test")

    monkeypatch.setattr(shipped, "load", refuse)
    engine = Engine(store, asyncio.Queue())
    await engine.start()
    assert engine.decider_ref == "additive:default"
    assert not engine.correlator.two_stage
    assert any("shipped model could not be used" in w for w in engine.scorer_warning_list())


def test_a_manifest_that_does_not_match_its_model_is_refused() -> None:
    with pytest.raises(shipped.ShippedModelError, match="SHA-256"):
        shipped.load_from(TEST_MODEL, _manifest(TEST_MODEL + " "))
    with pytest.raises(shipped.ShippedModelError, match="refused"):
        bad = TEST_MODEL.replace('"intercept":-1.0', '"intercept":-1.0,"__class__":"os"')
        shipped.load_from(bad, _manifest(bad))


async def test_the_additive_formula_is_opt_in_and_still_reachable(
    store: Store, test_model: shipped.Shipped
) -> None:
    """Part VIII: *the additive formula unreachable after the default changed* must be red."""
    async with store.lock:
        await store.set_decider_mode("additive", "admin", T, "the operator prefers the formula")
        await store.commit()
    engine = Engine(store, asyncio.Queue())
    await engine.start()
    assert engine.decider_ref.startswith("additive:")
    assert engine.correlator.grouper.mode == "components"
    assert not engine.correlator.two_stage


async def test_recall_joins_a_slow_fault_beyond_the_window(store: Store, test_model: shipped.Shipped) -> None:
    """Ten minutes apart on one element: outside the 120 s window, inside recall's hour."""
    engine = Engine(store, asyncio.Queue())
    await engine.start()
    oid = "1.3.6.1.4.1.1271.2.1.1"
    await _ingest(engine, store, [_trap("10.0.0.1", oid, "a", T), _trap("10.0.0.1", oid, "b", T + 590.0)])
    assert await _situations(store) == [{1, 2}]


async def test_two_concurrent_incidents_on_one_vendor_stay_apart(
    store: Store, test_model: shipped.Shipped
) -> None:
    """`dual_incident` with incident B moved onto incident A's vendor: v0.25.0 merged all
    sixteen alarms (`over_merge_rate 1.000`). Correlation clustering keeps two situations."""
    engine = Engine(store, asyncio.Queue())
    await engine.start()
    traps = []
    for k in range(1, 5):
        base = T + (k - 1) * 1.2
        traps += [
            _trap("203.0.113.1", f"1.3.6.1.4.1.1271.2.1.{k}", "x", base + 0.3),
            _trap("203.0.113.2", f"1.3.6.1.4.1.1271.2.1.{k}", "x", base + 0.6),
            _trap("203.0.113.51", f"1.3.6.1.4.1.1271.2.9.{k}", "y", base + 0.9),
            _trap("203.0.113.52", f"1.3.6.1.4.1.1271.2.9.{k}", "y", base + 1.2),
        ]
    await _ingest(engine, store, traps)
    groups = await _situations(store)
    cur = await store.conn.execute("SELECT a.id, d.ip FROM alarm a JOIN device d ON d.id=a.device_id")
    ip = {int(r[0]): str(r[1]) for r in await cur.fetchall()}
    incidents = [{ip[a].rsplit(".", 1)[1] in ("1", "2") for a in g} for g in groups]
    assert all(len(kinds) == 1 for kinds in incidents), f"a situation mixes incidents: {groups}"
    assert len(groups) == 2


async def test_a_bounce_inside_the_clear_hold_rejoins_its_situation(
    store: Store, test_model: shipped.Shipped
) -> None:
    engine = Engine(store, asyncio.Queue())
    await engine.start()
    down, up = "1.3.6.1.6.3.1.1.5.3", "1.3.6.1.6.3.1.1.5.4"
    port = [Varbind(oid="1.3.6.1.2.1.2.2.1.1.7", kind="int", value="7")]
    ev = lambda oid, ts: TrapEvent(device="10.0.0.9", trap_oid=oid, instance="7", ts=ts, varbinds=port)  # noqa: E731
    await _ingest(engine, store, [ev(down, T), ev(up, T + 5.0), ev(down, T + 60.0)])
    groups = await _situations(store)
    assert len(groups) == 1, "the re-raise inside the hold joined the same situation"
    await _ingest(engine, store, [ev(up, T + 65.0)])
    await engine.maintenance(now=T + 65.0 + CLEAR_HOLD_S / 2, retention_days=365.0)
    assert len(await _situations(store)) == 1, "still held"
    await engine.maintenance(now=T + 66.0 + CLEAR_HOLD_S, retention_days=365.0)
    assert await _situations(store) == [], "resolved once the hold passed"
    cur = await store.conn.execute("SELECT resolution FROM situation")
    assert [r[0] for r in await cur.fetchall()] == ["self_cleared"]


async def test_the_decider_routes_are_admin_and_a_site_switch_needs_a_verdict(
    store: Store, test_model: shipped.Shipped
) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    viewer = await authutil.client_as(app, "viewer")
    editor = await authutil.client_as(app, "editor")
    admin = await authutil.client_as(app, "admin")
    got = (await viewer.get("/api/decider")).json()
    assert got["mode"] == "shipped" and got["shipped"]["ref"] == test_model.ref
    body = {"mode": "additive", "reason": "testing the switch"}
    assert (await editor.post("/api/decider", json=body)).status_code == 403
    assert (await admin.post("/api/decider", json=body)).status_code == 200
    assert (await viewer.get("/api/decider")).json()["mode"] == "additive"
    site = await admin.post("/api/decider", json={"mode": "site", "reason": "no model yet"})
    assert site.status_code == 409 and "no site model" in site.text
    assert (await admin.post("/api/decider", json={"mode": "shipped", "reason": ""})).status_code == 422


async def test_the_kill_switch_is_an_editor_gesture_and_enabling_is_admin(
    store: Store, test_model: shipped.Shipped
) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    editor = await authutil.client_as(app, "editor")
    admin = await authutil.client_as(app, "admin")
    on = {"grouping": True, "naming": True, "reason": "trying it on the lab"}
    assert (await editor.post("/api/autonomy", json=on)).status_code == 403
    assert (await admin.post("/api/autonomy", json=on)).status_code == 200
    status = (await editor.get("/api/autonomy")).json()
    assert status["active"] and status["grades"] == ["grouping", "naming"]
    assert (await editor.post("/api/autonomy/stop")).status_code == 200
    status = (await editor.get("/api/autonomy")).json()
    assert not status["active"] and not status["suspended"], "stopped by a person, not suspended"
    assert status["history"][0]["reason"] == "stopped from the console"
