"""Upgrading a populated database: v0.1.0 -> v0.2.0 -> v0.3.0 -> v0.6.0 (forward-only,
data intact)."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from netcorenoc.crosscutting import audit, auth
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
    await store.open()  # applies 0002-0005 forward-only
    try:
        assert await scalar("PRAGMA user_version") == Store.latest_schema_version()

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
        assert await scalar(store, "PRAGMA user_version") == Store.latest_schema_version()

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


def _build_v050_db(path: str) -> None:
    """A real v0.5.0 database: migrations 0001-0004 applied, some data, user_version=4."""
    conn = sqlite3.connect(path)
    for migration in (
        "0001_init.sql",
        "0002_auth_audit.sql",
        "0003_entity.sql",
        "0004_state_clear.sql",
    ):
        conn.executescript((MIGRATIONS_DIR / migration).read_text())
    conn.execute("PRAGMA user_version=4")
    conn.execute(
        "INSERT INTO device (ip,vendor,first_seen,last_seen) VALUES ('10.0.0.1','Ciena',1,2)"
    )
    conn.execute("INSERT INTO ne (ip,vendor,first_seen,last_seen) VALUES ('10.0.0.1','Ciena',1,2)")
    conn.execute(
        "INSERT INTO entity (ne_id,parent_id,level,key,key_source,confidence,first_seen,last_seen)"
        " VALUES (1,NULL,0,'10.0.0.1','self',1.0,1,2)"
    )
    conn.execute(
        "INSERT INTO alarm_class (oid,vendor,name,first_seen,last_seen) "
        "VALUES ('1.3.6.1.4.1.1271.1','Ciena',NULL,1,2)"
    )
    for instance in ("port-1", "port-2"):
        conn.execute(
            "INSERT INTO alarm (device_id,ne_id,entity_id,class_id,instance,first_seen,last_seen) "
            "VALUES (1,1,1,1,?,1,2)",
            (instance,),
        )
    conn.execute("INSERT INTO situation (created_at,updated_at) VALUES (1,2)")
    conn.execute("INSERT INTO situation (created_at,updated_at) VALUES (3,4)")
    conn.execute("INSERT INTO situation_alarm (situation_id,alarm_id) VALUES (1,1)")
    conn.execute("INSERT INTO situation_alarm (situation_id,alarm_id) VALUES (2,2)")
    conn.execute(
        "INSERT INTO link (situation_id,alarm_a,alarm_b,score,term_t,term_a,term_e,created_at) "
        "VALUES (1,1,2,0.7,0.3,0.2,0.2,2)"
    )
    conn.commit()
    conn.close()


async def test_migrate_populated_v050_database_seeds_the_scorer_config(tmp_path: Path) -> None:
    """Gate 2/4: a populated v0.5.0 DB upgrades to v0.6.0 with data intact, the audit chain still
    verifying, the append-only triggers live on both tables, the seeded configuration equal to
    the coded defaults, and every existing situation truthfully backfilled to it — because those
    situations *were* formed by those parameters. The seed is what makes the upgrade
    grouping-neutral."""
    from netcorenoc.engine.correlate.scoring import AdditiveScorer

    db = str(tmp_path / "v050.db")
    _build_v050_db(db)

    seed = Store(db)
    await seed.open()
    async with seed.lock:
        for ts, action in ((1.0, "login.ok"), (2.0, "config.change")):
            await audit.write_event(
                seed, ts=ts, actor="admin", role="admin", source_ip="-", action=action, outcome="ok"
            )
        await seed.commit()
    await seed.close()

    store = Store(db)
    await store.open()
    try:

        async def scalar(sql: str) -> object:
            cur = await store.conn.execute(sql)
            row = await cur.fetchone()
            assert row is not None
            return row[0]

        assert await scalar("PRAGMA user_version") == Store.latest_schema_version()
        assert store.integrity_warnings == []

        # v0.5.0 data intact.
        stats = await store.stats()
        assert stats["devices"] == 1 and stats["active_alarms"] == 2
        assert await scalar("SELECT COUNT(*) FROM situation") == 2
        assert await scalar("SELECT COUNT(*) FROM link") == 1
        assert await scalar("SELECT score FROM link WHERE id=1") == 0.7

        # The seed is exactly the coded defaults, and it is active.
        config = await store.active_scorer_config()
        assert config is not None
        assert int(config["id"]) == 1 and config["scorer_id"] == "additive"
        assert config["contract_version"] == "1.0"
        assert (
            config["w_t"],
            config["w_a"],
            config["w_e"],
            config["tau_s"],
            config["threshold"],
        ) == (0.3, 0.35, 0.35, 30.0, 0.5)
        assert config["params_hash"] == AdditiveScorer().params_fingerprint()

        # Every pre-existing situation is backfilled to it — a truthful provenance, not a guess.
        assert await scalar("SELECT COUNT(*) FROM situation WHERE scorer_config_id IS NULL") == 0
        assert await scalar("SELECT COUNT(*) FROM situation WHERE scorer_config_id=1") == 2

        async with store.lock:
            for sql in (
                "UPDATE scorer_config SET w_t=0.9 WHERE id=1",
                "DELETE FROM scorer_config WHERE id=1",
                "DELETE FROM audit_log WHERE id=1",
            ):
                with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                    await store.conn.execute(sql)
                await store.conn.rollback()
            # The pointer table holds exactly one row, by CHECK.
            with pytest.raises(sqlite3.IntegrityError):
                await store.conn.execute(
                    "INSERT INTO scorer_active (id,config_id,activated_at) VALUES (2,1,0.0)"
                )
            await store.conn.rollback()

        result = await audit.verify_chain(store)
        assert result.ok and result.checked == 2
    finally:
        await store.close()

    # Idempotent: reopening neither re-runs the migration nor duplicates the seed.
    again = Store(db)
    await again.open()
    try:
        cur = await again.conn.execute("SELECT COUNT(*) FROM scorer_config")
        row = await cur.fetchone()
        assert row is not None and row[0] == 1
    finally:
        await again.close()


def _build_v060_db(path: str) -> None:
    """A real v0.6.0 database: migrations 0001-0005 applied, some data, user_version=5."""
    conn = sqlite3.connect(path)
    for migration in (
        "0001_init.sql",
        "0002_auth_audit.sql",
        "0003_entity.sql",
        "0004_state_clear.sql",
        "0005_scorer_config.sql",
    ):
        conn.executescript((MIGRATIONS_DIR / migration).read_text())
    conn.execute("PRAGMA user_version=5")
    for ip in ("10.0.0.1", "10.0.0.2"):
        conn.execute(
            "INSERT INTO device (ip,vendor,first_seen,last_seen) VALUES (?,'Ciena',1,2)", (ip,)
        )
        conn.execute(
            "INSERT INTO ne (ip,vendor,first_seen,last_seen) VALUES (?,'Ciena',1,2)", (ip,)
        )
    for ne_id, ip in ((1, "10.0.0.1"), (2, "10.0.0.2")):
        conn.execute(
            "INSERT INTO entity (ne_id,parent_id,level,key,key_source,confidence,first_seen,"
            "last_seen) VALUES (?,NULL,0,?,'self',1.0,1,2)",
            (ne_id, ip),
        )
    conn.execute(
        "INSERT INTO alarm_class (oid,vendor,name,first_seen,last_seen) "
        "VALUES ('1.3.6.1.4.1.1271.1','Ciena',NULL,1,2)"
    )
    for device_id, instance in ((1, "port-1"), (1, "port-2"), (2, "port-1")):
        conn.execute(
            "INSERT INTO alarm (device_id,ne_id,entity_id,class_id,instance,first_seen,last_seen) "
            "VALUES (?,?,?,1,?,1,2)",
            (device_id, device_id, device_id, instance),
        )
    conn.execute("INSERT INTO situation (created_at,updated_at,scorer_config_id) VALUES (1,2,1)")
    conn.execute("INSERT INTO situation_alarm (situation_id,alarm_id) VALUES (1,1)")
    conn.execute("INSERT INTO situation_alarm (situation_id,alarm_id) VALUES (1,2)")
    conn.execute(
        "INSERT INTO link (situation_id,alarm_a,alarm_b,score,term_t,term_a,term_e,created_at) "
        "VALUES (1,1,2,0.7,0.3,0.2,0.2,2)"
    )
    conn.commit()
    conn.close()


async def test_migrate_populated_v060_database_seeds_no_governance_rows(tmp_path: Path) -> None:
    """Gate 2 (v0.7.0): a populated v0.6.0 DB upgrades to v0.7.0 with data intact, the audit chain
    still verifying, the append-only triggers live on the new table — and **no governance rows**.

    The absence of a seed is the point and is the release gate. `0005` had to seed a parameter row
    because the engine needs parameters; governance has a compiled default (`PERMISSIONS` and full
    visibility), so seeding would be the only way to make an upgrade change behaviour. No rows
    means no policy means the ceiling and full visibility means byte-identically v0.6.0
    (DECISIONS #54)."""
    db = str(tmp_path / "v060.db")
    _build_v060_db(db)

    seed = Store(db)
    await seed.open()
    async with seed.lock:
        for ts, action in ((1.0, "login.ok"), (2.0, "scorer.config.update")):
            await audit.write_event(
                seed, ts=ts, actor="admin", role="admin", source_ip="-", action=action, outcome="ok"
            )
        await seed.commit()
    await seed.close()

    store = Store(db)
    await store.open()
    try:

        async def scalar(sql: str) -> object:
            cur = await store.conn.execute(sql)
            row = await cur.fetchone()
            assert row is not None
            return row[0]

        assert await scalar("PRAGMA user_version") == Store.latest_schema_version()
        assert store.integrity_warnings == []

        # v0.6.0 data intact, including the scoring provenance the previous migration wrote.
        stats = await store.stats()
        assert stats["devices"] == 2 and stats["active_alarms"] == 3
        assert await scalar("SELECT COUNT(*) FROM situation") == 1
        assert await scalar("SELECT score FROM link WHERE id=1") == 0.7
        assert await scalar("SELECT COUNT(*) FROM situation WHERE scorer_config_id=1") == 1
        config = await store.active_scorer_config()
        assert config is not None and int(config["id"]) == 1

        # THE gate: the migration seeded nothing, so there is no policy and nothing changes.
        assert await scalar("SELECT COUNT(*) FROM governance_policy") == 0
        assert await scalar("SELECT COUNT(*) FROM governance_active") == 0

        async with store.lock:
            # The new history table is append-only at the storage layer, like audit_log and
            # scorer_config. Insert one row first so UPDATE/DELETE have a target.
            await store.conn.execute(
                "INSERT INTO governance_policy (kind,document,doc_hash,created_by,created_at,note)"
                " VALUES ('rbac','{}','h','admin',1.0,'')"
            )
            for sql in (
                "UPDATE governance_policy SET document='{\"x\":1}' WHERE id=1",
                "DELETE FROM governance_policy WHERE id=1",
                "DELETE FROM audit_log WHERE id=1",
            ):
                with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                    await store.conn.execute(sql)
                    await store.conn.commit()
            # `kind` is constrained to the two governed axes.
            with pytest.raises(sqlite3.IntegrityError):
                await store.conn.execute(
                    "INSERT INTO governance_policy (kind,document,doc_hash,created_at) "
                    "VALUES ('other','{}','h',1.0)"
                )
            await store.conn.rollback()

        result = await audit.verify_chain(store)
        assert result.ok and result.checked == 2
    finally:
        await store.close()

    # Idempotent: reopening neither re-runs the migration nor invents a policy.
    again = Store(db)
    await again.open()
    try:
        cur = await again.conn.execute("SELECT COUNT(*) FROM governance_active")
        row = await cur.fetchone()
        assert row is not None and row[0] == 0
    finally:
        await again.close()


def _build_v070_db(path: str) -> None:
    """A populated v0.7.0 database (schema 6), carrying exactly the states v0.7.1 must clean up.

    Built by running migrations `0001`…`0006` only, so it is genuinely a pre-`0007` database
    rather than a current one with rows deleted: duplicate feedback (what v0.7.0's unbounded
    `add_feedback` wrote, F36) and orphan labels (what its missing existence check allowed, F37).
    """
    conn = sqlite3.connect(path)
    for migration in (
        "0001_init.sql",
        "0002_auth_audit.sql",
        "0003_entity.sql",
        "0004_state_clear.sql",
        "0005_scorer_config.sql",
        "0006_governance.sql",
    ):
        conn.executescript((MIGRATIONS_DIR / migration).read_text())
    conn.execute("PRAGMA user_version=6")
    for ip in ("10.0.0.1", "10.0.0.2"):
        conn.execute(
            "INSERT INTO device (ip,vendor,first_seen,last_seen) VALUES (?,'Ciena',1,2)", (ip,)
        )
        conn.execute(
            "INSERT INTO ne (ip,vendor,first_seen,last_seen) VALUES (?,'Ciena',1,2)", (ip,)
        )
    for ne_id, ip in ((1, "10.0.0.1"), (2, "10.0.0.2")):
        conn.execute(
            "INSERT INTO entity (ne_id,parent_id,level,key,key_source,confidence,first_seen,"
            "last_seen) VALUES (?,NULL,0,?,'self',1.0,1,2)",
            (ne_id, ip),
        )
    conn.execute(
        "INSERT INTO alarm_class (oid,vendor,name,first_seen,last_seen) "
        "VALUES ('1.3.6.1.4.1.1271.1','Ciena',NULL,1,2)"
    )
    for device_id, instance in ((1, "port-1"), (2, "port-1")):
        conn.execute(
            "INSERT INTO alarm (device_id,ne_id,entity_id,class_id,instance,first_seen,last_seen) "
            "VALUES (?,?,?,1,?,1,2)",
            (device_id, device_id, device_id, instance),
        )
    conn.execute("INSERT INTO situation (created_at,updated_at) VALUES (1,2)")
    conn.execute("INSERT INTO situation_alarm (situation_id,alarm_id) VALUES (1,1)")
    conn.execute("INSERT INTO situation_alarm (situation_id,alarm_id) VALUES (1,2)")
    # F36: v0.7.0 recorded one row per post with no uniqueness at all. Out of order on purpose,
    # so "keep the EARLIEST by created_at" is actually tested rather than "keep the lowest id".
    for created_at, verdict in (
        (300.0, "confirm"),
        (100.0, "confirm"),  # the earliest confirm — this one must survive
        (200.0, "confirm"),
        (500.0, "split"),
        (400.0, "split"),  # the earliest split — this one must survive
    ):
        conn.execute(
            "INSERT INTO feedback (situation_id,verdict,created_at) VALUES (1,?,?)",
            (verdict, created_at),
        )
    # F37: labels naming targets that do not exist, plus two real ones that must survive.
    for kind, target_id, label in (
        ("device", 1, "core-1"),  # real
        ("class", 1, "LOS"),  # real
        ("device", 900001, "ghost"),
        ("device", 900002, "ghost"),
        ("class", 900003, "ghost"),
    ):
        conn.execute(
            "INSERT INTO label (kind,target_id,label,updated_at) VALUES (?,?,?,1)",
            (kind, target_id, label),
        )
    conn.commit()
    conn.close()


async def test_migrate_populated_v070_database_dedupes_feedback_and_reaps_orphan_labels(
    tmp_path: Path,
) -> None:
    """Gate 2 (v0.7.1): a populated v0.7.0 DB upgrades with data intact and the audit chain
    verifying, the F36 duplicates de-duplicated to the **earliest** row per (situation, verdict),
    and the F37 orphan labels gone — while every real row survives.

    `0007` seeds nothing and changes no behaviour by itself; the three deliberate behaviour changes
    are application-side and enumerated in `docs/scope/SCOPE-0.7.1.md` §2.
    """
    db = str(tmp_path / "v070.db")
    _build_v070_db(db)

    seed = Store(db)
    await seed.open()  # note: this already applies 0007
    async with seed.lock:
        for ts, action in ((1.0, "login.ok"), (2.0, "label.set"), (3.0, "feedback")):
            await audit.write_event(
                seed, ts=ts, actor="admin", role="admin", source_ip="-", action=action, outcome="ok"
            )
        await seed.commit()
    await seed.close()

    store = Store(db)
    await store.open()
    try:

        async def scalar(sql: str) -> object:
            cur = await store.conn.execute(sql)
            row = await cur.fetchone()
            assert row is not None
            return row[0]

        assert await scalar("PRAGMA user_version") == Store.latest_schema_version()
        assert store.integrity_warnings == []

        # v0.7.0 data intact.
        stats = await store.stats()
        assert stats["devices"] == 2 and stats["active_alarms"] == 2
        assert await scalar("SELECT COUNT(*) FROM situation") == 1
        assert await scalar("SELECT COUNT(*) FROM situation_alarm") == 2

        # F36: 6 rows collapse to 2 — one per (situation, verdict), the EARLIEST of each.
        assert await scalar("SELECT COUNT(*) FROM feedback") == 2
        cur = await store.conn.execute("SELECT verdict, created_at FROM feedback ORDER BY verdict")
        assert [(r[0], r[1]) for r in await cur.fetchall()] == [
            ("confirm", 100.0),
            ("split", 400.0),
        ]
        # …and the constraint that keeps it that way is live.
        async with store.lock:
            with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
                await store.conn.execute(
                    "INSERT INTO feedback (situation_id,verdict,created_at) VALUES (1,'confirm',9)"
                )
            await store.conn.rollback()

        # F36: the attribution columns exist, are nullable, and are NULL for pre-0007 rows —
        # deliberately not backfilled, because the author is unknown and a guess is not an audit.
        cur = await store.conn.execute("SELECT principal_ref, role FROM feedback")
        assert all(r[0] is None and r[1] is None for r in await cur.fetchall())

        # F37: the three orphans are gone; both real labels survive.
        cur = await store.conn.execute("SELECT kind, target_id FROM label ORDER BY kind, target_id")
        assert [(r[0], r[1]) for r in await cur.fetchall()] == [("class", 1), ("device", 1)]

        cur = await store.conn.execute("PRAGMA foreign_key_check")
        assert list(await cur.fetchall()) == []
        # No seed of any kind: the migration cannot change behaviour on its own.
        assert await scalar("SELECT COUNT(*) FROM governance_policy") == 0
        assert await scalar("SELECT COUNT(*) FROM governance_active") == 0

        result = await audit.verify_chain(store)
        assert result.ok and result.checked == 3
    finally:
        await store.close()

    # Idempotent: reopening neither re-runs the cleanup nor loses a row.
    again = Store(db)
    await again.open()
    try:
        cur = await again.conn.execute("SELECT COUNT(*) FROM feedback")
        row = await cur.fetchone()
        assert row is not None and row[0] == 2
    finally:
        await again.close()
