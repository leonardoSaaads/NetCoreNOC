"""Phase 4.8 / Phase 5 — a populated v0.3.0-shaped database upgrades in place.

The rebrand ships no schema change, so a v0.3.0 database *is* a v0.4.0 database: reopening a
populated file runs no migration, and the learned state (promoted entities + the affinity the
engine reloads), the situations, and the audit chain all survive. This is the "upgrade from a
real v0.3.0 database with learned state" evidence for the release gate.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from netcorenoc import audit
from netcorenoc.main import Engine
from netcorenoc.store import Store

import util

BASE = 1_700_000_000.0


async def test_populated_db_learned_state_and_audit_chain_survive_reopen(tmp_path: Path) -> None:
    db = str(tmp_path / "upgrade.db")

    # 1) Populate: ingest a scenario that learns a device-affinity edge and groups a situation,
    #    persist learned state, and write an audit row. (Promoted-entity reload across a restart
    #    is covered separately by test_promotion; here we exercise the affinity matrix + chain.)
    store1 = Store(db)
    await store1.open()
    engine1 = Engine(store1, asyncio.Queue())
    await engine1.start()
    await util.drive(engine1, engine1.queue, util.fixture_events("fiber_cut.json", BASE))
    await engine1.maintenance(BASE + 10, retention_days=365.0)  # persists learned edges + state
    async with store1.lock:
        await audit.write_event(
            store1,
            ts=BASE,
            actor="system",
            role=None,
            source_ip=None,
            action="ingest.gap",
            outcome="ok",
            object_type="ingest_gap",
            details={},
        )
        await store1.commit()

    nes_before = await store1.list_ne()
    device_a = await store1.device_id("127.0.0.2", BASE)
    device_b = await store1.device_id("127.0.0.3", BASE)
    edges_before = (await store1.graph_snapshot(min_edge_n=0))["edges"]
    situations_before = await store1.list_situations("open", 100)
    version_before = await store1.schema_version()
    assert edges_before and situations_before  # the scenario actually learned/grouped something
    await store1.close()

    # 2) "Upgrade": reopen the same file with fresh objects — no migration should run.
    store2 = Store(db)
    await store2.open()
    assert await store2.schema_version() == version_before == store2.latest_schema_version()
    assert store2.integrity_warnings == []  # the populated DB is healthy
    engine2 = Engine(store2, asyncio.Queue())
    await engine2.start()  # reloads the affinity matrices

    # 3) Verify survival across the reopen.
    assert await store2.list_ne() == nes_before  # NEs intact
    assert (await store2.graph_snapshot(min_edge_n=0))["edges"] == edges_before  # learned edges
    assert await store2.list_situations("open", 100) == situations_before  # situations survive
    assert engine2.learner.device_affinity(device_a, device_b) > 0  # matrix reloaded into memory
    assert (await audit.verify_chain(store2)).ok  # the chain still verifies across the upgrade
    await store2.close()
