"""Upgrading a populated database: v0.1.0 -> v0.2.0 -> v0.3.0 (forward-only, data intact)."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest
from netcorenoc import audit, auth
from netcorenoc.store import MIGRATIONS_DIR, Store


def _build_v010_db(path: str) -> None:
    """A real v0.1.0 database: only migration 0001 applied, some data, user_version=1."""
    conn = sqlite3.connect(path)
    conn.executescript((MIGRATIONS_DIR / "0001_init.sql").read_text())
    conn.execute("PRAGMA user_version=1")
    conn.execute(
        "INSERT INTO device (ip,vendor,first_seen,last_seen) VALUES ('10.0.0.1','Ciena',1,2)"
    )
    conn.execute(
        "INSERT INTO alarm_class (oid,vendor,name,first_seen,last_seen) "
        "VALUES ('1.3.6.1.4.1.1271.1','Ciena',NULL,1,2)"
    )
    conn.execute("INSERT INTO alarm (device_id,class_id,first_seen,last_seen) VALUES (1,1,1,2)")
    conn.execute(
        "INSERT INTO quarantine (source,raw,reason,received_at) VALUES ('10.9.9.9',x'3005',?,3)",
        ("legacy-row",),
    )
    conn.commit()
    conn.close()


async def test_migrate_populated_v010_database(tmp_path: Path) -> None:
    db = str(tmp_path / "v010.db")
    _build_v010_db(db)

    async def scalar(sql: str) -> object:
        cur = await store.conn.execute(sql)
        row = await cur.fetchone()
        assert row is not None
        return row[0]

    store = Store(db)
    await store.open()  # applies 0002, 0003 and 0004 forward-only
    try:
        assert await scalar("PRAGMA user_version") == 4

        # v0.1.0 data intact.
        stats = await store.stats()
        assert stats["devices"] == 1 and stats["active_alarms"] == 1 and stats["quarantined"] == 1

        # v0.2.0 tables exist and are empty; v0.3.0 profiler/gap tables likewise.
        for table in ("user", "session", "api_token", "audit_log", "ingest_gap", "varbind_profile"):
            assert await scalar(f"SELECT COUNT(*) FROM {table}") == 0  # nosec B608

        # v0.3.0 entity model backfilled: one NE and one level-0 entity per device, and the
        # single alarm attributed to that level-0 entity with ne_id synced to device_id.
        assert await scalar("SELECT COUNT(*) FROM ne") == 1
        assert await scalar("SELECT COUNT(*) FROM entity WHERE level=0 AND key_source='self'") == 1
        assert await scalar("SELECT COUNT(*) FROM alarm WHERE entity_id IS NOT NULL") == 1
        assert await scalar("SELECT COUNT(*) FROM alarm WHERE ne_id = device_id") == 1

        # Append-only triggers are active on the freshly migrated DB.
        async with store.lock:
            await store.audit_insert(
                {
                    "id": 1,
                    "ts": 1.0,
                    "actor": "s",
                    "role": "admin",
                    "source_ip": "-",
                    "action": "login.ok",
                    "object_type": None,
                    "object_id": None,
                    "outcome": "ok",
                    "details": {},
                },
                prev_hash="0" * 64,
                entry_hash="deadbeef",
            )
            await store.commit()
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                await store.conn.execute("DELETE FROM audit_log WHERE id=1")

        # A legacy quarantine row (no F4 metadata columns) still reads back safely.
        rows = await store.list_quarantine(10)
        assert rows and rows[0]["sha256"] and rows[0]["length"] == 2

        # Bootstrap admin can be created on the migrated DB.
        async with store.lock:
            password = await auth.bootstrap_admin(store, 0.0)
            await store.commit()
        assert password is not None
        async with store.lock:
            admin = await store.get_user_by_name("admin")
        assert admin is not None and admin["role"] == "admin"
    finally:
        await store.close()


def test_migration_is_forward_only_and_idempotent(tmp_path: Path) -> None:
    """Opening the migrated DB again does not re-run migrations or lose data."""
    db = str(tmp_path / "again.db")
    _build_v010_db(db)

    async def open_twice() -> int:
        for _ in range(2):
            store = Store(db)
            await store.open()
            await store.close()
        conn = sqlite3.connect(db)
        count = conn.execute("SELECT COUNT(*) FROM device").fetchone()[0]
        conn.close()
        return int(count)

    assert asyncio.run(open_twice()) == 1  # device row survived two opens


def _build_v020_db(path: str) -> None:
    """A real v0.2.0 database: migrations 0001+0002 applied, some data, user_version=2."""
    conn = sqlite3.connect(path)
    for migration in ("0001_init.sql", "0002_auth_audit.sql"):
        conn.executescript((MIGRATIONS_DIR / migration).read_text())
    conn.execute("PRAGMA user_version=2")
    conn.execute(
        "INSERT INTO device (ip,vendor,first_seen,last_seen) VALUES ('10.0.0.1','Ciena',1,2)"
    )
    conn.execute(
        "INSERT INTO device (ip,vendor,first_seen,last_seen) VALUES ('10.0.0.2','Huawei',1,2)"
    )
    conn.execute(
        "INSERT INTO alarm_class (oid,vendor,name,first_seen,last_seen) "
        "VALUES ('1.3.6.1.4.1.1271.1','Ciena',NULL,1,2)"
    )
    conn.execute(
        "INSERT INTO alarm (device_id,class_id,instance,first_seen,last_seen,community_tag) "
        "VALUES (1,1,'port-1',1,2,'abc')"
    )
    conn.execute(
        "INSERT INTO alarm (device_id,class_id,instance,first_seen,last_seen) "
        "VALUES (2,1,'port-2',1,2)"
    )
    conn.execute("INSERT INTO situation (created_at,updated_at) VALUES (1,2)")
    conn.execute("INSERT INTO situation_alarm (situation_id,alarm_id) VALUES (1,1)")
    conn.execute("INSERT INTO situation_alarm (situation_id,alarm_id) VALUES (1,2)")
    conn.execute(
        "INSERT INTO link (situation_id,alarm_a,alarm_b,score,term_t,term_a,term_e,created_at) "
        "VALUES (1,1,2,0.7,0.3,0.2,0.2,2)"
    )
    conn.commit()
    conn.close()


async def test_migrate_populated_v020_database_with_audit_chain(tmp_path: Path) -> None:
    """Gate 2: a populated v0.2.0 DB (with a live audit chain) upgrades to v0.3.0 with data
    intact, the entity model backfilled, the append-only audit triggers still firing, and
    the hash chain still verifying."""
    db = str(tmp_path / "v020.db")
    _build_v020_db(db)

    # Write a real chained audit history before the upgrade.
    seed = Store(db)
    await seed.open()  # NOTE: this first open already applies 0003 forward-only
    async with seed.lock:
        await audit.write_event(
            seed,
            ts=1.0,
            actor="admin",
            role="admin",
            source_ip="-",
            action="login.ok",
            outcome="ok",
        )
        await audit.write_event(
            seed,
            ts=2.0,
            actor="admin",
            role="admin",
            source_ip="-",
            action="config.change",
            outcome="ok",
            object_type="config",
        )
        await seed.commit()
    await seed.close()

    async def scalar(store: Store, sql: str) -> object:
        cur = await store.conn.execute(sql)
        row = await cur.fetchone()
        assert row is not None
        return row[0]

    store = Store(db)
    await store.open()
    try:
        assert await scalar(store, "PRAGMA user_version") == 4

        # v0.2.0 data intact.
        stats = await store.stats()
        assert (
            stats["devices"] == 2 and stats["active_alarms"] == 2 and stats["open_situations"] == 1
        )

        # Entity model backfilled: one NE and one level-0 entity per device.
        assert await scalar(store, "SELECT COUNT(*) FROM ne") == 2
        assert await scalar(store, "SELECT COUNT(*) FROM entity WHERE level=0") == 2
        assert await scalar(store, "SELECT COUNT(*) FROM alarm WHERE entity_id IS NOT NULL") == 2
        assert await scalar(store, "SELECT COUNT(*) FROM alarm WHERE ne_id = device_id") == 2
        # Every level-0 entity is 'self' with full confidence and maps to its NE's IP.
        mismatched = await scalar(
            store,
            "SELECT COUNT(*) FROM entity e JOIN ne n ON n.id=e.ne_id "
            "WHERE e.level=0 AND (e.key <> n.ip OR e.key_source <> 'self' OR e.confidence <> 1.0)",
        )
        assert mismatched == 0

        # The new UNIQUE (entity_id, class_id, instance) index rejects a duplicate.
        async with store.lock:
            with pytest.raises(sqlite3.IntegrityError):
                await store.conn.execute(
                    "INSERT INTO alarm (device_id, ne_id, entity_id, class_id, instance, "
                    "first_seen, last_seen) SELECT device_id, ne_id, entity_id, class_id, "
                    "instance, first_seen, last_seen FROM alarm WHERE id=1"
                )
            await store.conn.rollback()

        # Append-only audit triggers still active after the upgrade.
        async with store.lock:
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                await store.conn.execute("DELETE FROM audit_log WHERE id=1")
            await store.conn.rollback()

        # The pre-existing audit hash chain still verifies across the schema change.
        result = await audit.verify_chain(store)
        assert result.ok and result.checked == 2
    finally:
        await store.close()
