"""Entity promotion (S5): the engine acts on the profiler's judgement — forward-only, with
the MAX_ENTITIES_PER_NE cap that warns and audits but never fails ingestion."""

from __future__ import annotations

import asyncio

import pytest

from netcorenoc import varbind_profile
from netcorenoc.events import TrapEvent, Varbind
from netcorenoc.main import Engine
from netcorenoc.store import Store

import authutil

NE = "10.20.0.1"
ENTITY_OID = "1.3.6.1.4.1.2011.6.128.1.1.2.43.1"  # the per-ONU identifier varbind
CLASS_A = "1.3.6.1.4.1.2011.6.128.1.1.2.1"
CLASS_B = "1.3.6.1.4.1.2011.6.128.1.1.2.2"
BASE = 5_000_000.0


def _onu_event(cls: str, onu: str, ts: float, device: str = NE) -> TrapEvent:
    return TrapEvent(
        device=device,
        trap_oid=cls,
        instance=onu,  # the heuristic already picks the onu here; promotion makes it explicit
        ts=ts,
        varbinds=[Varbind(oid=ENTITY_OID, kind="str", value=onu)],
    )


async def _drive_with_maintenance(engine: Engine, events: list[TrapEvent], every: int = 60) -> None:
    ts = BASE
    for start in range(0, len(events), every):
        async with engine.store.lock:
            for ev in events[start : start + every]:
                await engine._process(ev)
                ts = max(ts, ev.ts)
            await engine.store.commit()
        await engine.maintenance(now=ts + 0.001, retention_days=365.0)


async def test_promotion_subdivides_the_ne_and_is_audited(store: Store) -> None:
    engine, _queue, _app = await authutil.make_env(store)
    # 130 ONUs across two classes on one OLT: the id recurs across classes -> promotable.
    events = [
        _onu_event(cls, f"onu-{n}", BASE + i * 0.01)
        for i, (n, cls) in enumerate((n, c) for n in range(130) for c in (CLASS_A, CLASS_B))
    ]
    await _drive_with_maintenance(engine, events)

    ne_id = next(n["id"] for n in await store.list_ne())
    assert ne_id in engine.ne_discriminator
    assert engine.ne_discriminator[ne_id][-1][0] == ENTITY_OID  # finest in the chain

    entities = await store.entities_for_ne(ne_id)
    levels = {e["level"] for e in entities}
    assert 0 in levels and 1 in levels  # the NE plus promoted ONU entities
    onu = next(e for e in entities if e["level"] == 1)
    assert onu["key_source"] == ENTITY_OID and onu["key"].startswith("onu-")
    assert onu["confidence"] >= varbind_profile.ENTITY_PROMOTE_SCORE

    async with store.lock:
        cur = await store.conn.execute(
            "SELECT actor, object_id, outcome FROM audit_log WHERE action='entity.promote'"
        )
        rows = [dict(r) for r in await cur.fetchall()]
    assert rows and rows[0]["actor"] == "system" and rows[0]["outcome"] == "ok"


async def test_promotion_is_forward_only(store: Store) -> None:
    """Alarms ingested before promotion keep their level-0 entity; only later alarms get the
    fine entity. History is never reinterpreted (prime directive 4)."""
    engine, _queue, _app = await authutil.make_env(store)
    events = [
        _onu_event(cls, f"onu-{n}", BASE + i * 0.01)
        for i, (n, cls) in enumerate((n, c) for n in range(130) for c in (CLASS_A, CLASS_B))
    ]
    await _drive_with_maintenance(engine, events)

    async with store.lock:
        cur = await store.conn.execute(
            "SELECT e.level, COUNT(*) FROM alarm a JOIN entity e ON e.id=a.entity_id "
            "GROUP BY e.level"
        )
        by_level = {int(r[0]): int(r[1]) for r in await cur.fetchall()}
        cur = await store.conn.execute("SELECT COUNT(*) FROM alarm")
        row = await cur.fetchone()
        assert row is not None
        total = int(row[0])
    assert by_level.get(0, 0) > 0 and by_level.get(1, 0) > 0  # early level-0, later promoted
    assert sum(by_level.values()) == total  # every alarm attributed to exactly one entity


async def test_max_entities_per_ne_caps_warns_and_audits_without_failing_ingest(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, _queue, _app = await authutil.make_env(store)
    # Promote first (small legitimate set), then flood with unique ids past a tiny cap.
    monkeypatch.setattr("netcorenoc.engine.operate.engine.MAX_ENTITIES_PER_NE", 5)
    # The cap is enforced in `_resolve_entity` (engine side) and reported in `_promotion_sweep`'s
    # audit detail (maintenance side). Both bindings are patched so the fixture cap is the one
    # every reader sees — a single patch would leave the audit row claiming the real cap.
    monkeypatch.setattr("netcorenoc.engine.operate.maintenance.MAX_ENTITIES_PER_NE", 5)
    train = [
        _onu_event(cls, f"onu-{n}", BASE + i * 0.01)
        for i, (n, cls) in enumerate((n, c) for n in range(130) for c in (CLASS_A, CLASS_B))
    ]
    await _drive_with_maintenance(engine, train)
    ne_id = next(n["id"] for n in await store.list_ne())
    assert ne_id in engine.ne_discriminator

    # A forged per-trap unique id: far more than the cap of 5.
    flood = [_onu_event(CLASS_A, f"forged-{n}", BASE + 100 + n * 0.01) for n in range(50)]
    async with store.lock:
        for ev in flood:
            await engine._process(ev)  # must never raise
        await store.commit()
    await engine.maintenance(now=BASE + 200, retention_days=365.0)

    assert ne_id in engine._entity_cap_hit  # the cap bit
    assert engine.entity_cap_warnings()  # a persistent operator warning is surfaced
    assert (await store.entity_count_for_ne(ne_id)) <= 5 + 1  # cap + the level-0 entity

    async def _count(sql: str) -> int:
        cur = await store.conn.execute(sql)
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    async with store.lock:
        denied = await _count(
            "SELECT COUNT(*) FROM audit_log WHERE action='entity.promote' AND outcome='denied'"
        )
        alarms = await _count("SELECT COUNT(*) FROM alarm")
    assert denied >= 1  # the breach was audited
    assert alarms > 0  # ingestion continued


CARD_OID = "1.3.6.1.4.1.9.9.117.1.1.1"
PORT_OID = "1.3.6.1.4.1.9.9.276.1.1.9"
CH_DOWN = "1.3.6.1.4.1.9.9.276.1.1.1"
CH_FAULT = "1.3.6.1.4.1.9.9.276.1.1.2"


def _port_event(cls: str, card: str, port: str, ts: float, device: str = "192.0.2.9") -> TrapEvent:
    return TrapEvent(
        device=device,
        trap_oid=cls,
        instance=port,
        ts=ts,
        varbinds=[
            Varbind(oid=PORT_OID, kind="str", value=port),
            Varbind(oid=CARD_OID, kind="str", value=card),
        ],
    )


async def test_hierarchy_recovers_card_to_port_containment(store: Store) -> None:
    """S6: the functional-dependency test recovers the card->port containment (each port
    maps to one card) and promotes the finest as the entity, the card as its parent."""
    engine, _queue, _app = await authutil.make_env(store)
    events: list[TrapEvent] = []
    i = 0
    for _repeat in range(4):
        for cls in (CH_DOWN, CH_FAULT):
            for p in range(120):
                events.append(_port_event(cls, f"card-{p // 40}", f"eth-{p}", BASE + i * 0.01))
                i += 1
    await _drive_with_maintenance(engine, events)

    ne_id = next(n["id"] for n in await store.list_ne())
    chain = engine.ne_discriminator.get(ne_id, [])
    assert [oid for oid, _ in chain] == [CARD_OID, PORT_OID]  # coarse card -> fine port

    entities = await store.entities_for_ne(ne_id)
    cards = [e for e in entities if e["level"] == 1]
    ports = [e for e in entities if e["level"] == 2]
    assert cards and ports
    assert all(c["key_source"] == CARD_OID and c["key"].startswith("card-") for c in cards)
    assert all(p["key_source"] == PORT_OID and p["key"].startswith("eth-") for p in ports)
    card_ids = {c["id"] for c in cards}
    assert all(p["parent_id"] in card_ids for p in ports)  # every port contained by a card


async def test_promoted_discriminator_survives_restart(store: Store) -> None:
    engine, _queue, _app = await authutil.make_env(store)
    events = [
        _onu_event(cls, f"onu-{n}", BASE + i * 0.01)
        for i, (n, cls) in enumerate((n, c) for n in range(130) for c in (CLASS_A, CLASS_B))
    ]
    await _drive_with_maintenance(engine, events)
    ne_id = next(n["id"] for n in await store.list_ne())
    assert ne_id in engine.ne_discriminator

    fresh = Engine(store, asyncio.Queue())
    await fresh.start()
    chain = fresh.ne_discriminator.get(ne_id, [])
    assert chain and chain[-1][0] == ENTITY_OID  # reloaded from the promoted entities' levels
