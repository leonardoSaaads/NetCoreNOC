"""`netcorenoc db import-sqlite` — a v0.30.0 SQLite history into PostgreSQL, once (ADR #452).

Until v0.30.0 `tests/store/test_upgrade.py` and `tests/store/test_migration.py` proved that a
populated database *upgrades in place*: reopening a file from an older release ran the migrations
and the learned state, the situations and the audit chain survived. The SQLite migrations are gone
with the SQLite store, so the same promise is now made — and tested — at the one place an old
database still enters the product: the import.

The source is built from `tests/fixtures/sqlite-v030.sql`, the exact schema v0.30.0's migrations
produced (with their seed rows), and populated here with one of everything that carries a type
the conversion has to get right: epoch instants (including digits below a microsecond), 0/1 flags,
addresses, JSON varbinds, a blob, the `device` duplicate of `ne`, and an audit chain hashed over
the floats exactly as `audit.write_event` hashes them.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from netcorenoc.crosscutting import audit
from netcorenoc.main import Engine
from netcorenoc.store import Store, importer

import paths

SCHEMA = paths.FIXTURES / "sqlite-v030.sql"
T0 = 1_760_000_000.0
#: An instant as `time.time()` produces it: digits below the microsecond a `timestamptz` keeps.
NOW = 1_760_000_123.123456789


def _chain(rows: list[dict[str, object]]) -> list[tuple[object, ...]]:
    """Audit rows hashed exactly as `audit.write_event` hashes them, in `audit_log`'s order."""
    prev = audit.GENESIS_HASH
    out: list[tuple[object, ...]] = []
    for entry in rows:
        entry_hash = audit.compute_hash(prev, audit.canonical(entry))
        details = json.dumps(entry["details"], sort_keys=True, separators=(",", ":"))
        out.append(
            (
                entry["id"],
                entry["ts"],
                entry["actor"],
                entry["role"],
                entry["source_ip"],
                entry["action"],
                entry["object_type"],
                entry["object_id"],
                entry["outcome"],
                details,
                prev,
                entry_hash,
            )
        )
        prev = entry_hash
    return out


def build_v030(path: Path, *, bad_address: bool = False) -> str:
    """A populated v0.30.0 database at ``path``; returns its final audit hash."""
    db = sqlite3.connect(path)
    db.executescript(SCHEMA.read_text())
    ips = ["10.0.0.2", "10.0.0.10", "not-an-address" if bad_address else "10.0.0.1"]
    for i, ip in enumerate(ips, start=1):
        db.execute(
            "INSERT INTO device (id, ip, first_seen, last_seen) VALUES (?, ?, ?, ?)",
            (i, ip, T0, NOW),
        )
        db.execute(
            "INSERT INTO ne (id, ip, first_seen, last_seen, organization_id) "
            "VALUES (?, ?, ?, ?, 1)",
            (i, ip, T0, NOW),
        )
        db.execute(
            "INSERT INTO entity (id, ne_id, parent_id, level, key, key_source, confidence, "
            "first_seen, last_seen) VALUES (?, ?, NULL, 0, ?, 'self', 1.0, ?, ?)",
            (i, i, ip, T0, NOW),
        )
    db.execute(
        "INSERT INTO alarm_class (id, oid, first_seen, last_seen) VALUES "
        "(1, '1.3.6.1.6.3.1.1.5.3', ?, ?), (2, '1.3.6.1.4.1.2011.5.25.1.1', ?, ?)",
        (T0, NOW, T0, NOW),
    )
    varbinds = json.dumps([{"oid": "1.3.6.1.2.1.2.2.1.1.7", "kind": "int", "value": "7"}])
    for aid, ne, cls, status, flapping in ((1, 1, 1, "active", 1), (2, 2, 2, "cleared", 0)):
        db.execute(
            "INSERT INTO alarm (id, device_id, ne_id, entity_id, class_id, instance, status, "
            "is_flapping, count, first_seen, last_seen, cleared_at, varbinds, severity, "
            "severity_rank, severity_source) VALUES (?, ?, ?, ?, ?, 'if7', ?, ?, 3, ?, ?, ?, ?, "
            "'major', 1, 'standard')",
            (
                aid,
                ne,
                ne,
                ne,
                cls,
                status,
                flapping,
                T0 + aid,
                NOW,
                NOW if aid == 2 else None,
                varbinds,
            ),
        )
    db.execute(
        "INSERT INTO situation (id, status, created_at, updated_at, root_alarm_id, "
        "scorer_config_id, derived_name) VALUES (1, 'open', ?, ?, 1, 1, 'linkDown on 10.0.0.2')",
        (T0, NOW),
    )
    db.execute("INSERT INTO situation_alarm VALUES (1, 1), (1, 2)")
    db.execute(
        "INSERT INTO link (situation_id, alarm_a, alarm_b, score, term_t, term_a, term_e, "
        "created_at) VALUES (1, 1, 2, 0.7123456789012345, 0.3, 0.2, 0.2123456789, ?)",
        (NOW,),
    )
    db.execute(
        "INSERT INTO capture_run (id, started_at, netcorenoc_version, window_s, max_candidates, "
        "max_links_per_alarm, link_threshold, retention_sink_days, retention_sink_rows, "
        "retention_training_days, retention_audit_days, a_epoch, e_epoch) "
        "VALUES (1, ?, '0.30.0', 300.0, 100, 5, 0.5, 7.0, 200000, 365.0, 365.0, 3, 4)",
        (T0,),
    )
    # `ne_id` NULL beside a `device_id`: the import fills the element from the device it was.
    db.execute(
        "INSERT INTO dataset_observation (capture_run_id, alarm_id, ne_id, device_id, class_id, "
        "observed_at, alarm_count, trap_oid, varbinds) VALUES (1, 2, NULL, 2, 2, ?, 1, "
        "'1.3.6.1.4.1.2011.5.25.1.1', ?)",
        (NOW, json.dumps([["1.3", "7"]])),
    )
    db.execute(
        "INSERT INTO user (id, username, password_hash, role, must_change_password, disabled, "
        "created_at, updated_at) VALUES (1, 'adm', 'scrypt$x', 'admin', 0, 0, ?, ?)",
        (T0, NOW),
    )
    db.execute(
        "INSERT INTO api_token (id, token_hash, name, role, created_at, revoked, revoked_at) "
        "VALUES (1, ?, 'agent', 'editor', ?, 1, ?)",
        ("a" * 64, T0, NOW),
    )
    db.execute(
        "INSERT INTO quarantine (source, raw, reason, received_at, sanitized) "
        "VALUES ('10.0.0.9', X'DEAD00BEEF', 'ber-decode-failed', ?, 1)",
        (NOW,),
    )
    db.execute("INSERT INTO meta (key, value) VALUES ('spool.cursor', '41')")
    db.execute(
        "INSERT INTO edge (kind, a_id, b_id, weight, n, g, version, updated_at) "
        "VALUES ('device', 1, 2, 0.8, 6.0, 3, 2, ?)",
        (NOW,),
    )
    entries = [
        {
            "id": n,
            "ts": NOW + n * 0.000123456789,
            "actor": "adm",
            "role": "admin",
            "source_ip": "10.0.0.9",
            "action": "config.change",
            "outcome": "ok",
            "object_type": "config",
            "object_id": str(n),
            "details": {"n": n, "ratio": 1 / 3},
        }
        for n in range(1, 6)
    ]
    chain = _chain(entries)
    db.executemany(f"INSERT INTO audit_log VALUES ({', '.join('?' * 12)})", chain)
    db.commit()
    db.close()
    return str(chain[-1][-1])


async def _open(url: str) -> Store:
    store = Store(url, exclusive=False)
    await store.open()
    return store


async def test_a_v030_history_imports_whole_typed_and_verified(tmp_path: Path, db_url: str) -> None:
    final_hash = build_v030(tmp_path / "v030.db")
    store = await _open(db_url)
    try:
        report = await importer.import_sqlite(store, str(tmp_path / "v030.db"))
        assert report.ok, report.render()
        counts = {name: (expected, got) for name, expected, got in report.tables}
        assert counts["alarm"] == (2, 2) and counts["ne"] == (3, 3)
        assert counts["audit_log"] == (5, 5) and counts["app_user"] == (1, 1)
        assert all(expected == got for expected, got in counts.values())

        # The chain verifies link for link, ending where the SQLite one ended — and the import
        # itself is the next link (`db.import`).
        rows = await store.audit_all()
        assert audit.verify_rows(rows[:5]).final_hash == final_hash
        result = await audit.verify_chain(store)
        assert result.ok and result.checked == 6, result.render()
        assert rows[-1]["action"] == "db.import"

        cur = await store.conn.execute(
            "SELECT a.is_flapping, a.first_seen, a.varbinds, n.ip FROM alarm a "
            "JOIN ne n ON n.id = a.ne_id ORDER BY a.id"
        )
        alarms = await cur.fetchall()
        assert [r["is_flapping"] for r in alarms] == [True, False]
        assert alarms[0]["first_seen"] == T0 + 1  # whole and six-decimal instants are exact
        assert json.loads(alarms[0]["varbinds"]) == [
            {"oid": "1.3.6.1.2.1.2.2.1.1.7", "kind": "int", "value": "7"}
        ]
        assert alarms[0]["ip"] == "10.0.0.2"

        cur = await store.conn.execute("SELECT last_seen FROM ne WHERE id = 1")
        row = await cur.fetchone()
        assert row is not None and abs(row["last_seen"] - NOW) <= 0.5e-6  # microseconds kept

        cur = await store.conn.execute("SELECT host(ip) FROM ne ORDER BY ip")
        assert [r[0] for r in await cur.fetchall()] == ["10.0.0.1", "10.0.0.2", "10.0.0.10"]

        cur = await store.conn.execute("SELECT ne_id FROM dataset_observation")
        assert [r[0] for r in await cur.fetchall()] == [2]  # the device it was, as the element

        token = await store.conn.execute("SELECT revoked FROM api_token")
        assert [r[0] for r in await token.fetchall()] == [True]
        cur = await store.conn.execute("SELECT raw, sanitized FROM quarantine")
        packet = await cur.fetchone()
        assert packet is not None and packet["raw"] == b"\xde\xad\x00\xbe\xef"
        assert packet["sanitized"] is True
        cur = await store.conn.execute("SELECT score FROM link")
        link = await cur.fetchone()
        assert link is not None and link["score"] == 0.7123456789012345  # a double, bit for bit

        # The identity sequences moved past the copied ids: the next row is not a collision.
        async with store.lock:
            sid = await store.create_situation(NOW)
            await store.commit()
        assert sid == 2
        assert await store.get_meta("spool.cursor") == "41"
    finally:
        await store.close()


async def test_the_engine_resumes_on_an_imported_history(tmp_path: Path, db_url: str) -> None:
    """What `test_upgrade.py` asserted of a reopened file: the learned state reloads and the
    situations are there, now of an imported database."""
    build_v030(tmp_path / "v030.db")
    store = await _open(db_url)
    try:
        assert (await importer.import_sqlite(store, str(tmp_path / "v030.db"))).ok
    finally:
        await store.close()
    store = Store(db_url)
    await store.open()
    try:
        engine = Engine(store, __import__("asyncio").Queue())
        await engine.start()
        listed = await store.list_situations(None, 100, None)
        assert [s["id"] for s in listed] == [1]
        assert engine.members  # the open situation's members are back in memory
    finally:
        await store.close()


async def test_a_source_older_than_v030_is_refused(tmp_path: Path, db_url: str) -> None:
    path = tmp_path / "old.db"
    build_v030(path)
    db = sqlite3.connect(path)
    db.execute("PRAGMA user_version = 28")
    db.commit()
    db.close()
    store = await _open(db_url)
    try:
        with pytest.raises(importer.ImportRefusedError, match="schema 28"):
            await importer.import_sqlite(store, str(path))
    finally:
        await store.close()


async def test_a_v030_as_released_schema_29_imports_with_the_0030_columns_unmeasured(
    tmp_path: Path, db_url: str
) -> None:
    """v0.30.x shipped at schema 29; `0030` only added nullable `host_sample` columns. A released
    appliance's file imports, and the columns it never had read as "not measured" (NULL)."""
    path = tmp_path / "released.db"
    build_v030(path)
    db = sqlite3.connect(path)
    db.execute(
        "INSERT INTO host_sample (at, cpu_pct, mem_pct, disk_pct, db_mb, queue_depth, "
        "traps_per_s, latency_ms) VALUES (?, 1.5, 2.5, 3.5, 4.5, 6, 7.5, 8.5)",
        (NOW,),
    )
    db.commit()
    db.execute(
        "CREATE TABLE hs AS SELECT at, cpu_pct, mem_pct, disk_pct, db_mb, queue_depth, "
        "traps_per_s, latency_ms FROM host_sample"
    )
    db.execute("DROP TABLE host_sample")
    db.execute("ALTER TABLE hs RENAME TO host_sample")
    db.execute("PRAGMA user_version = 29")
    db.commit()
    db.close()
    store = await _open(db_url)
    try:
        report = await importer.import_sqlite(store, str(path))
        assert report.ok, report.render()
        cur = await store.conn.execute(
            "SELECT queue_depth, latency_ms, classified, spool_mb FROM host_sample"
        )
        assert [tuple(r) for r in await cur.fetchall()] == [(6, 8.5, None, None)]
    finally:
        await store.close()


async def test_a_target_that_holds_history_is_refused(
    tmp_path: Path, new_database: Callable[[], str]
) -> None:
    build_v030(tmp_path / "v030.db")
    url = new_database()
    store = await _open(url)
    try:
        assert (await importer.import_sqlite(store, str(tmp_path / "v030.db"))).ok
        with pytest.raises(importer.ImportRefusedError, match="already holds history"):
            await importer.import_sqlite(store, str(tmp_path / "v030.db"))
    finally:
        await store.close()


async def test_a_failed_import_leaves_the_schema_as_it_was(tmp_path: Path, db_url: str) -> None:
    """One transaction: a value that cannot be converted (an address that is not one) rolls back
    everything already copied, so the schema is empty and the import can simply be re-run."""
    build_v030(tmp_path / "bad.db", bad_address=True)
    store = await _open(db_url)
    try:
        with pytest.raises(importer.ImportRefusedError, match=r"ne, row 3.*not-an-address"):
            await importer.import_sqlite(store, str(tmp_path / "bad.db"))
        cur = await store.conn.execute(
            "SELECT (SELECT COUNT(*) FROM ne), (SELECT COUNT(*) FROM alarm_class), "
            "(SELECT COUNT(*) FROM organization), (SELECT COUNT(*) FROM decider_setting)"
        )
        assert tuple(await cur.fetchone() or ()) == (0, 0, 1, 1)  # only the four seeds
    finally:
        await store.close()
