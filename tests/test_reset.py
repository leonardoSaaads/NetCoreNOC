"""Admin reset of a learned decision (S11): the recourse for a poisoned entity/severity. The
decision reset forgets the discriminator (history untouched, durable across restart); the
profile reset also wipes the evidence so it re-measures from scratch. Both are admin-only and
audited."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from netcorenoc.events import TrapEvent, Varbind
from netcorenoc.main import Engine
from netcorenoc.store import Store

import authutil

NE = "10.60.0.1"
ENTITY_OID = "1.3.6.1.4.1.2011.6.128.1.1.2.43.1"
CLASS_A = "1.3.6.1.4.1.2011.6.128.1.1.2.1"
CLASS_B = "1.3.6.1.4.1.2011.6.128.1.1.2.2"
BASE = 8_000_000.0


def _onu_event(cls: str, onu: str, ts: float) -> TrapEvent:
    return TrapEvent(
        device=NE,
        trap_oid=cls,
        instance=onu,
        ts=ts,
        varbinds=[Varbind(oid=ENTITY_OID, kind="str", value=onu)],
    )


async def _promote(engine: Engine, store: Store) -> int:
    events = [
        _onu_event(cls, f"onu-{n}", BASE + i * 0.01)
        for i, (n, cls) in enumerate((n, c) for n in range(130) for c in (CLASS_A, CLASS_B))
    ]
    ts = BASE
    for start in range(0, len(events), 60):
        async with store.lock:
            for ev in events[start : start + 60]:
                await engine._process(ev)
                ts = max(ts, ev.ts)
            await store.commit()
        await engine.maintenance(now=ts + 0.001, retention_days=365.0)
    ne_id = next(n["id"] for n in await store.list_ne())
    assert ne_id in engine.ne_discriminator  # promoted
    return int(ne_id)


async def _scalar(store: Store, sql: str, *params: Any) -> int:
    cur = await store.conn.execute(sql, params)
    row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def test_entity_reset_forgets_the_discriminator_and_is_audited(store: Store) -> None:
    engine, _queue, app = await authutil.make_env(store)
    ne_id = await _promote(engine, store)

    client = await authutil.client_as(app, "admin")
    resp = await client.post(f"/api/entities/{ne_id}/reset", json={})
    assert resp.status_code == 200

    assert ne_id not in engine.ne_discriminator  # in-memory decision forgotten
    assert ne_id in await store.reset_ne_ids()  # durable marker set
    async with store.lock:
        cleared = await _scalar(
            store, "SELECT COUNT(*) FROM varbind_profile WHERE ne_id=? AND role IS NOT NULL", ne_id
        )
        assert cleared == 0  # roles cleared
        cur = await store.conn.execute(
            "SELECT outcome FROM audit_log WHERE action='entity.reset' AND object_id=?",
            (str(ne_id),),
        )
        rows = [dict(r) for r in await cur.fetchall()]
    assert rows and rows[0]["outcome"] == "ok"
    # History is untouched: the promoted entities and their alarms still exist.
    assert await store.entity_count_for_ne(ne_id) > 1


async def test_reset_survives_restart(store: Store) -> None:
    engine, _queue, _app = await authutil.make_env(store)
    ne_id = await _promote(engine, store)
    await engine.reset_entity(ne_id, BASE + 5000)
    async with store.lock:
        await store.commit()

    fresh = Engine(store, asyncio.Queue())
    await fresh.start()
    assert ne_id not in fresh.ne_discriminator  # the reset discriminator is not resurrected


async def test_profile_reset_wipes_the_evidence(store: Store) -> None:
    engine, _queue, app = await authutil.make_env(store)
    ne_id = await _promote(engine, store)
    assert any(k[0] == ne_id for k in engine.profiler.profiles)  # evidence present

    client = await authutil.client_as(app, "admin")
    resp = await client.post(f"/api/profiles/{ne_id}/reset", json={})
    assert resp.status_code == 200

    assert not any(k[0] == ne_id for k in engine.profiler.profiles)  # accumulators dropped
    assert ne_id not in engine.ne_discriminator
    async with store.lock:
        remaining = await _scalar(
            store, "SELECT COUNT(*) FROM varbind_profile WHERE ne_id=?", ne_id
        )
        assert remaining == 0  # persisted rows gone


@pytest.mark.parametrize("role", ["viewer", "editor"])
async def test_reset_is_admin_only(store: Store, role: str) -> None:
    engine, _queue, app = await authutil.make_env(store)
    ne_id = await _promote(engine, store)
    client = await authutil.client_as(app, role)
    resp = await client.post(f"/api/entities/{ne_id}/reset", json={})
    assert resp.status_code == 403
    assert ne_id in engine.ne_discriminator  # unchanged
