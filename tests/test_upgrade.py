"""Phase 4.8 / Phase 5 — a populated v0.3.0-shaped database upgrades in place.

The rebrand ships no schema change, so a v0.3.0 database *is* a v0.4.0 database: reopening a
populated file runs no migration, and the learned state (promoted entities + the affinity the
engine reloads), the situations, and the audit chain all survive. This is the "upgrade from a
real v0.3.0 database with learned state" evidence for the release gate.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from netcorenoc import audit, shaping
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


async def test_v060_upgrade_preserves_grouping_and_seeds_provenance(tmp_path: Path) -> None:
    """Phase 5 gate: a live v0.5.0-shaped database upgrades in place with **identical grouping**.

    The same fixture is replayed twice: once against a database whose schema is frozen at v4 (a
    v0.5.0 install), once against the same file after migration 0005 has been applied. The
    resulting situation partitions must match member-for-member — the seeded configuration is the
    coded defaults, so v0.6.0 scores exactly as v0.5.0 did. Provenance is seeded and backfilled,
    learned state and the audit chain survive, and the schema advances by exactly one.
    """
    import netcorenoc.store as store_mod
    from netcorenoc.scoring import AdditiveScorer

    real_dir = store_mod.MIGRATIONS_DIR

    class _FrozenAtV4:
        """The migration directory as a v0.5.0 install saw it: nothing past 0004 exists yet.

        Filtered by migration *number*, not by name, so a later release adding 0006/0007 does not
        silently un-freeze this fixture and turn "a genuine v0.5.0 database" into a current one."""

        def glob(self, pattern: str) -> list[Path]:
            return [p for p in real_dir.glob(pattern) if int(p.name.split("_", 1)[0]) <= 4]

    async def partition_of(store: Store) -> set[frozenset[int]]:
        cur = await store.conn.execute("SELECT situation_id, alarm_id FROM situation_alarm")
        groups: dict[int, set[int]] = {}
        for row in await cur.fetchall():
            groups.setdefault(int(row[0]), set()).add(int(row[1]))
        return {frozenset(members) for members in groups.values()}

    db = str(tmp_path / "v050-live.db")
    store_mod.MIGRATIONS_DIR = _FrozenAtV4()  # type: ignore[assignment]
    try:
        old = Store(db)
        await old.open()
        assert await old.schema_version() == 4  # a genuine v0.5.0 database
        engine_old = Engine(old, asyncio.Queue())
        await engine_old.start()
        assert engine_old.scorer_config_id is None  # no scorer_config table yet
        await util.drive(engine_old, engine_old.queue, util.fixture_events("fiber_cut.json", BASE))
        await engine_old.maintenance(BASE + 10, retention_days=365.0)
        async with old.lock:
            await audit.write_event(
                old,
                ts=BASE,
                actor="admin",
                role="admin",
                source_ip="-",
                action="login.ok",
                outcome="ok",
            )
            await old.commit()
        before_partition = await partition_of(old)
        before_edges = (await old.graph_snapshot(min_edge_n=0))["edges"]
        before_alarms = (await old.stats())["active_alarms"]
        assert before_partition and before_edges
        await old.close()
    finally:
        store_mod.MIGRATIONS_DIR = real_dir

    # The upgrade: same file, migration 0005 now present.
    new = Store(db)
    await new.open()
    try:
        assert await new.schema_version() == Store.latest_schema_version()
        assert new.integrity_warnings == []
        engine_new = Engine(new, asyncio.Queue())
        await engine_new.start()

        # Learned state, grouping and the audit chain all survive.
        assert await partition_of(new) == before_partition
        assert (await new.graph_snapshot(min_edge_n=0))["edges"] == before_edges
        assert (await new.stats())["active_alarms"] == before_alarms
        assert (await audit.verify_chain(new)).ok

        # Provenance is seeded, active, and backfilled onto the pre-existing situations.
        assert engine_new.scorer_config_id == 1
        active = engine_new.correlator.scorer.active
        assert active.params_fingerprint() == AdditiveScorer().params_fingerprint()
        cur = await new.conn.execute(
            "SELECT COUNT(*) FROM situation WHERE scorer_config_id IS NULL"
        )
        row = await cur.fetchone()
        assert row is not None and row[0] == 0

        # And the upgraded engine keeps grouping the same way: replaying the same fixture into a
        # fresh v0.6.0 database reproduces the v0.5.0 partition exactly.
        fresh = Store(str(tmp_path / "fresh-v060.db"))
        await fresh.open()
        engine_fresh = Engine(fresh, asyncio.Queue())
        await engine_fresh.start()
        await util.drive(
            engine_fresh, engine_fresh.queue, util.fixture_events("fiber_cut.json", BASE)
        )
        assert await partition_of(fresh) == before_partition
        await fresh.close()
    finally:
        await new.close()


async def test_v070_upgrade_changes_no_behaviour(tmp_path: Path) -> None:
    """Phase 5 gate: a live v0.6.0 database upgrades in place and **nothing changes**.

    The same fixture is replayed against a database whose schema is frozen at v5 (a v0.6.0
    install), then the file is reopened with migration 0006 present. Grouping, learned state,
    scorer provenance and the audit chain must all survive, and — the point of this release — the
    upgraded appliance must carry **no governance policy**, so every route answers exactly as it
    did before. Governance changes nothing until an admin writes a policy.
    """
    import netcorenoc.store as store_mod
    from netcorenoc import rbac

    real_dir = store_mod.MIGRATIONS_DIR

    class _FrozenAtV5:
        """The migration directory as a v0.6.0 install saw it: nothing past 0005 exists yet."""

        def glob(self, pattern: str) -> list[Path]:
            return [p for p in real_dir.glob(pattern) if int(p.name.split("_", 1)[0]) <= 5]

    async def partition_of(store: Store) -> set[frozenset[int]]:
        cur = await store.conn.execute("SELECT situation_id, alarm_id FROM situation_alarm")
        groups: dict[int, set[int]] = {}
        for row in await cur.fetchall():
            groups.setdefault(int(row[0]), set()).add(int(row[1]))
        return {frozenset(members) for members in groups.values()}

    db = str(tmp_path / "v060-live.db")
    store_mod.MIGRATIONS_DIR = _FrozenAtV5()  # type: ignore[assignment]
    try:
        old = Store(db)
        await old.open()
        assert await old.schema_version() == 5  # a genuine v0.6.0 database
        engine_old = Engine(old, asyncio.Queue())
        await engine_old.start()
        assert engine_old.scorer_config_id == 1  # v0.6.0 seeded its scorer config
        await util.drive(engine_old, engine_old.queue, util.fixture_events("fiber_cut.json", BASE))
        await engine_old.maintenance(BASE + 10, retention_days=365.0)
        async with old.lock:
            await audit.write_event(
                old,
                ts=BASE,
                actor="admin",
                role="admin",
                source_ip="-",
                action="login.ok",
                outcome="ok",
            )
            await old.commit()
        before_partition = await partition_of(old)
        before_edges = (await old.graph_snapshot(min_edge_n=0))["edges"]
        before_stats = await old.stats()
        assert before_partition and before_edges
        await old.close()
    finally:
        store_mod.MIGRATIONS_DIR = real_dir

    # The upgrade: same file, migration 0006 now present.
    new = Store(db)
    await new.open()
    try:
        assert await new.schema_version() == Store.latest_schema_version()
        assert new.integrity_warnings == []
        engine_new = Engine(new, asyncio.Queue())
        await engine_new.start()

        # Learned state, grouping, provenance and the audit chain all survive untouched.
        assert await partition_of(new) == before_partition
        assert (await new.graph_snapshot(min_edge_n=0))["edges"] == before_edges
        assert await new.stats() == before_stats
        assert engine_new.scorer_config_id == 1
        assert (await audit.verify_chain(new)).ok

        # THE v0.7.0 gate: no governance policy exists, so the perimeter is byte-identically
        # v0.6.0 — the resolver returns each role's compiled ceiling for every capability.
        async with new.lock:
            assert await new.active_governance_ids() == {}
        for role in rbac.ROLE_RANK:
            resolved = rbac.resolve_capabilities(role, None, None)
            for capability in rbac.PERMISSIONS:
                assert (capability in resolved) == rbac.role_allows(role, capability)
        # And scoping is inactive, so every principal still sees every NE.
        assert shaping.visible_nes("viewer", "user:1", None, []).unrestricted
    finally:
        await new.close()
