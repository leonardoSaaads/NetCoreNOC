"""SQLite (WAL) persistence behind one small interface.

One connection, hand-written SQL, plain-SQL migrations applied at startup via
``PRAGMA user_version``. The engine batches writes and calls :meth:`Store.commit`
per batch; interleaved API writes simply join the current transaction — a throughput
choice, not an atomicity contract.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite

from netcorenoc import known_oids
from netcorenoc.events import QuarantinedPacket, TrapEvent

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


@dataclass(frozen=True)
class IngestResult:
    """Outcome of deduplicating one trap into the alarm table."""

    alarm_id: int
    device_id: int
    class_id: int
    activated: bool  # newly active (first ever, or re-raise after clear)
    count: int
    entity_id: int = 0  # the alarmed entity (§5.5); 0 falls back to the device at scoring


@dataclass(frozen=True)
class EdgeRow:
    """One learned pairwise statistic (affinity, precedence, or clear pair)."""

    kind: str
    a_id: int
    b_id: int
    weight: float
    n: float
    g: int


TOUCH_INTERVAL_S = 5.0  # cadence for cosmetic last_seen updates on cached rows


class Store:
    def __init__(self, path: str) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None
        self._device_ids: dict[str, int] = {}
        self._ne_ids: dict[str, int] = {}
        self._entity0_ids: dict[int, int] = {}  # ne id -> its level-0 entity id
        self._entity_ids: dict[tuple[int, str], int] = {}  # (ne id, key) -> promoted entity id
        self._class_ids: dict[str, int] = {}
        self._touched: dict[tuple[str, int], float] = {}
        # Non-fatal integrity findings from the startup PRAGMA checks (F11): surfaced through
        # operator_warnings(), never a crash — a NOC trap sink must keep ingesting even with a
        # partly-damaged history DB.
        self.integrity_warnings: list[str] = []
        # One connection, many tasks: holders of this lock get a consistent view and,
        # critically, commits can never interleave with another task's open cursor
        # (sqlite refuses to commit while statements are in progress). The engine takes
        # it per batch; API handlers take it per request.
        self.lock = asyncio.Lock()

    @property
    def conn(self) -> aiosqlite.Connection:
        assert self._conn is not None, "Store.open() not called"
        return self._conn

    async def open(self) -> None:
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._migrate()
        await self._check_integrity()

    async def _migrate(self) -> None:
        cur = await self.conn.execute("PRAGMA user_version")
        row = await cur.fetchone()
        current = int(row[0]) if row else 0
        for script in sorted(MIGRATIONS_DIR.glob("*.sql")):
            version = int(script.name.split("_", 1)[0])
            if version > current:
                await self.conn.executescript(script.read_text())
                await self.conn.execute(f"PRAGMA user_version={version}")
        await self.conn.commit()

    async def _check_integrity(self) -> None:
        """Startup integrity/FK check (F11). Records a warning on damage; never crashes."""
        cur = await self.conn.execute("PRAGMA integrity_check")
        row = await cur.fetchone()
        if row is not None and str(row[0]).lower() != "ok":
            self.integrity_warnings.append(
                "Database integrity_check reported damage. Restore from a backup and run "
                "`netcorenoc audit verify`; new traps are still being ingested."
            )
        cur = await self.conn.execute("PRAGMA foreign_key_check")
        orphans = list(await cur.fetchall())
        if orphans:
            self.integrity_warnings.append(
                f"Database foreign_key_check found {len(orphans)} orphaned row(s); "
                "history may be inconsistent. A backup/restore is recommended."
            )

    @staticmethod
    def latest_schema_version() -> int:
        """The highest migration number on disk (the schema version a healthy DB reaches)."""
        return max((int(p.name.split("_", 1)[0]) for p in MIGRATIONS_DIR.glob("*.sql")), default=0)

    async def schema_version(self) -> int:
        cur = await self.conn.execute("PRAGMA user_version")
        row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.commit()
            await self._conn.close()
            self._conn = None

    async def commit(self) -> None:
        await self.conn.commit()

    async def rollback(self) -> None:
        """Abandon the current transaction (F11). The audit chain only advances on commit, so a
        rolled-back batch never breaks it — the dropped traps are recorded as an ingest loss."""
        await self.conn.rollback()

    # -- devices and classes ---------------------------------------------------------

    def _should_touch(self, kind: str, row_id: int, ts: float) -> bool:
        """last_seen on device/class rows is cosmetic; throttle it under load."""
        key = (kind, row_id)
        if ts - self._touched.get(key, 0.0) < TOUCH_INTERVAL_S:
            return False
        self._touched[key] = ts
        return True

    async def device_id(self, ip: str, ts: float) -> int:
        cached = self._device_ids.get(ip)
        if cached is not None:
            if self._should_touch("d", cached, ts):
                await self.conn.execute("UPDATE device SET last_seen=? WHERE id=?", (ts, cached))
            return cached
        cur = await self.conn.execute(
            "INSERT INTO device (ip, vendor, first_seen, last_seen) VALUES (?, NULL, ?, ?) "
            "ON CONFLICT (ip) DO UPDATE SET last_seen=excluded.last_seen RETURNING id",
            (ip, ts, ts),
        )
        row = await cur.fetchone()
        assert row is not None
        self._device_ids[ip] = int(row[0])
        return self._device_ids[ip]

    async def ne_id(self, ip: str, ts: float) -> int:
        """The reporting element for a source IP. Parallel to :meth:`device_id` (one NE per
        device, 1:1); device_id is retained and kept in sync for one version (§5.2)."""
        cached = self._ne_ids.get(ip)
        if cached is not None:
            if self._should_touch("n", cached, ts):
                await self.conn.execute("UPDATE ne SET last_seen=? WHERE id=?", (ts, cached))
            return cached
        cur = await self.conn.execute(
            "INSERT INTO ne (ip, vendor, first_seen, last_seen) VALUES (?, NULL, ?, ?) "
            "ON CONFLICT (ip) DO UPDATE SET last_seen=excluded.last_seen RETURNING id",
            (ip, ts, ts),
        )
        row = await cur.fetchone()
        assert row is not None
        self._ne_ids[ip] = int(row[0])
        return self._ne_ids[ip]

    async def entity_level0(self, ne_id: int, ip: str, ts: float) -> int:
        """The level-0 entity (the NE itself). Insert-or-get; the store lock serialises this,
        and the UNIQUE (ne_id, parent_id, key) index does not fire on a NULL parent, so a
        SELECT-then-INSERT under the lock is the correct idempotent path."""
        cached = self._entity0_ids.get(ne_id)
        if cached is not None:
            return cached
        cur = await self.conn.execute(
            "SELECT id FROM entity WHERE ne_id=? AND level=0 AND parent_id IS NULL", (ne_id,)
        )
        row = await cur.fetchone()
        if row is None:
            cur = await self.conn.execute(
                "INSERT INTO entity (ne_id, parent_id, level, key, key_source, confidence, "
                "first_seen, last_seen) VALUES (?, NULL, 0, ?, 'self', 1.0, ?, ?) RETURNING id",
                (ne_id, ip, ts, ts),
            )
            row = await cur.fetchone()
        assert row is not None
        self._entity0_ids[ne_id] = int(row[0])
        return self._entity0_ids[ne_id]

    async def class_id(self, oid: str, ts: float) -> int:
        cached = self._class_ids.get(oid)
        if cached is not None:
            if self._should_touch("c", cached, ts):
                await self.conn.execute(
                    "UPDATE alarm_class SET last_seen=? WHERE id=?", (ts, cached)
                )
            return cached
        vendor = known_oids.vendor_of(oid)
        name = known_oids.trap_name(oid)
        cur = await self.conn.execute(
            "INSERT INTO alarm_class (oid, vendor, name, first_seen, last_seen) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (oid) DO UPDATE SET last_seen=excluded.last_seen RETURNING id",
            (oid, vendor, name, ts, ts),
        )
        row = await cur.fetchone()
        assert row is not None
        self._class_ids[oid] = int(row[0])
        return self._class_ids[oid]

    # -- alarms ----------------------------------------------------------------------

    async def ingest(
        self,
        event: TrapEvent,
        entity_id: int | None = None,
        instance: str | None = None,
        severity: str | None = None,
        severity_rank: int | None = None,
    ) -> IngestResult:
        """Dedup by fingerprint: a repeat bumps count/last_seen; a re-raise re-activates.

        The dedup key stays (device_id, class_id, instance) — at level 0 this is exactly
        the entity fingerprint (one entity per NE), which is what preserves cold-start parity
        (§5.2). ne_id and entity_id are recorded on every alarm so the entity model is
        populated from day one; the profiler (S4/S5) only ever adds deeper entities.
        """
        device_id = await self.device_id(event.device, event.ts)
        class_id = await self.class_id(event.trap_oid, event.ts)
        ne_id = await self.ne_id(event.device, event.ts)
        # The engine resolves the entity and dedup instance (promotion-aware, S5). Defaults —
        # the level-0 entity and the heuristic instance — reproduce v0.2.0 exactly (parity).
        if entity_id is None:
            entity_id = await self.entity_level0(ne_id, event.device, event.ts)
        inst = event.instance if instance is None else instance
        cur = await self.conn.execute(
            "SELECT id, status, entity_id FROM alarm "
            "WHERE device_id=? AND class_id=? AND instance=?",
            (device_id, class_id, inst),
        )
        existing = await cur.fetchone()
        varbinds = json.dumps([v.model_dump() for v in event.varbinds])
        if existing is None:
            cur = await self.conn.execute(
                "INSERT INTO alarm (device_id, ne_id, entity_id, class_id, instance, first_seen, "
                "last_seen, varbinds, community_tag, severity, severity_rank) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
                (
                    device_id,
                    ne_id,
                    entity_id,
                    class_id,
                    inst,
                    event.ts,
                    event.ts,
                    varbinds,
                    event.community_tag or None,
                    severity,
                    severity_rank,
                ),
            )
            row = await cur.fetchone()
            assert row is not None
            return IngestResult(int(row[0]), device_id, class_id, True, 1, entity_id)
        # A learned severity refreshes the active alarm's current severity; COALESCE keeps the
        # last known value when a later trap omits the field (never silently downgrades to NULL).
        cur = await self.conn.execute(
            "UPDATE alarm SET count=count+1, last_seen=?, varbinds=?, status='active', "
            "cleared_at=NULL, severity=COALESCE(?, severity), "
            "severity_rank=COALESCE(?, severity_rank) WHERE id=? RETURNING count",
            (event.ts, varbinds, severity, severity_rank, int(existing["id"])),
        )
        row = await cur.fetchone()
        assert row is not None
        activated = str(existing["status"]) != "active"
        # A re-raise keeps its original entity (forward-only): use the stored entity_id.
        kept = int(existing["entity_id"]) if existing["entity_id"] is not None else entity_id
        return IngestResult(int(existing["id"]), device_id, class_id, activated, int(row[0]), kept)

    async def clear_alarm(
        self, device_id: int, raise_class_id: int, instance: str, ts: float
    ) -> int | None:
        """Mark the matching active alarm cleared; returns its id, or None if absent."""
        cur = await self.conn.execute(
            "UPDATE alarm SET status='cleared', cleared_at=?, last_seen=? "
            "WHERE device_id=? AND class_id=? AND instance=? AND status='active' RETURNING id",
            (ts, ts, device_id, raise_class_id, instance),
        )
        row = await cur.fetchone()
        return int(row[0]) if row else None

    async def set_flapping(self, alarm_id: int, flapping: bool) -> None:
        await self.conn.execute(
            "UPDATE alarm SET is_flapping=? WHERE id=?", (int(flapping), alarm_id)
        )

    async def quarantine_packet(self, pkt: QuarantinedPacket) -> None:
        await self.conn.execute(
            "INSERT INTO quarantine (source, raw, reason, received_at, sha256, length, first8, "
            "sanitized) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                pkt.source,
                pkt.raw,
                pkt.reason,
                pkt.ts,
                pkt.sha256,
                pkt.length,
                pkt.first8,
                int(pkt.sanitized),
            ),
        )

    # -- learned state ---------------------------------------------------------------

    async def load_edges(self, kind: str) -> list[EdgeRow]:
        cur = await self.conn.execute(
            "SELECT kind, a_id, b_id, weight, n, g FROM edge WHERE kind=?", (kind,)
        )
        return [
            EdgeRow(r["kind"], r["a_id"], r["b_id"], r["weight"], r["n"], r["g"])
            for r in await cur.fetchall()
        ]

    async def upsert_edges(self, rows: list[EdgeRow], ts: float) -> None:
        await self.conn.executemany(
            "INSERT INTO edge (kind, a_id, b_id, weight, n, g, version, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 1, ?) ON CONFLICT (kind, a_id, b_id) DO UPDATE SET "
            "weight=excluded.weight, n=excluded.n, g=excluded.g, version=version+1, "
            "updated_at=excluded.updated_at",
            [(r.kind, r.a_id, r.b_id, r.weight, r.n, r.g, ts) for r in rows],
        )

    async def get_meta(self, key: str) -> str | None:
        cur = await self.conn.execute("SELECT value FROM meta WHERE key=?", (key,))
        row = await cur.fetchone()
        return str(row[0]) if row else None

    async def set_meta(self, key: str, value: str) -> None:
        await self.conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    async def del_meta(self, key: str) -> None:
        await self.conn.execute("DELETE FROM meta WHERE key=?", (key,))

    # -- situations ------------------------------------------------------------------

    async def create_situation(self, ts: float, scorer_config_id: int | None = None) -> int:
        """Open a situation, recording which scorer configuration formed it (v0.6.0 provenance).

        The engine passes the active `config_id`; it is written here, on the store side under the
        batch lock the engine already holds — never on the datagram path.

        ``None`` means "no configuration is in effect" — the fail-safe state where the engine is
        running on the coded defaults. The column is then left out of the statement entirely
        rather than written as NULL, so this call still succeeds against a schema that predates
        `0005_scorer_config.sql`. That is not hypothetical tidiness: it is what lets the same
        engine code run before and after the migration, which is how the upgrade test proves the
        migration changes no grouping."""
        if scorer_config_id is None:
            cur = await self.conn.execute(
                "INSERT INTO situation (created_at, updated_at) VALUES (?, ?) RETURNING id",
                (ts, ts),
            )
        else:
            cur = await self.conn.execute(
                "INSERT INTO situation (created_at, updated_at, scorer_config_id) "
                "VALUES (?, ?, ?) RETURNING id",
                (ts, ts, scorer_config_id),
            )
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    async def add_alarm_to_situation(self, situation_id: int, alarm_id: int) -> None:
        await self.conn.execute(
            "INSERT OR IGNORE INTO situation_alarm (situation_id, alarm_id) VALUES (?, ?)",
            (situation_id, alarm_id),
        )

    async def merge_situations(self, dst: int, src: int, ts: float) -> None:
        await self.conn.execute(
            "INSERT OR IGNORE INTO situation_alarm (situation_id, alarm_id) "
            "SELECT ?, alarm_id FROM situation_alarm WHERE situation_id=?",
            (dst, src),
        )
        await self.conn.execute("DELETE FROM situation_alarm WHERE situation_id=?", (src,))
        await self.conn.execute("UPDATE link SET situation_id=? WHERE situation_id=?", (dst, src))
        await self.conn.execute(
            "UPDATE situation SET status='merged', closed_at=?, updated_at=? WHERE id=?",
            (ts, ts, src),
        )
        await self.touch_situation(dst, ts)

    async def touch_situation(self, situation_id: int, ts: float) -> None:
        await self.conn.execute("UPDATE situation SET updated_at=? WHERE id=?", (ts, situation_id))

    async def close_situation(self, situation_id: int, ts: float) -> None:
        await self.conn.execute(
            "UPDATE situation SET status='closed', closed_at=?, updated_at=? "
            "WHERE id=? AND status='open'",
            (ts, ts, situation_id),
        )

    async def set_root(self, situation_id: int, alarm_id: int) -> None:
        await self.conn.execute(
            "UPDATE situation SET root_alarm_id=? WHERE id=?", (alarm_id, situation_id)
        )

    async def manual_close_situation(self, situation_id: int, ts: float) -> bool:
        """Operator ack: close an open situation. Returns False if it was not open."""
        cur = await self.conn.execute(
            "UPDATE situation SET status='closed', closed_at=?, updated_at=? "
            "WHERE id=? AND status='open' RETURNING id",
            (ts, ts, situation_id),
        )
        return await cur.fetchone() is not None

    async def add_link(
        self,
        situation_id: int,
        alarm_a: int,
        alarm_b: int,
        score: float,
        term_t: float,
        term_a: float,
        term_e: float,
        ts: float,
    ) -> None:
        await self.conn.execute(
            "INSERT INTO link (situation_id, alarm_a, alarm_b, score, term_t, term_a, term_e, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (situation_id, alarm_a, alarm_b, score, term_t, term_a, term_e, ts),
        )

    async def idle_open_situations(self, cutoff: float) -> list[int]:
        cur = await self.conn.execute(
            "SELECT id FROM situation WHERE status='open' AND updated_at < ?", (cutoff,)
        )
        return [int(r[0]) for r in await cur.fetchall()]

    async def all_cleared(self, situation_id: int) -> bool:
        cur = await self.conn.execute(
            "SELECT COUNT(*) FROM situation_alarm sa JOIN alarm a ON a.id=sa.alarm_id "
            "WHERE sa.situation_id=? AND a.status='active'",
            (situation_id,),
        )
        row = await cur.fetchone()
        assert row is not None
        return int(row[0]) == 0

    async def open_situation_members(self) -> list[dict[str, Any]]:
        """Members of all open situations, for rebuilding engine state at startup."""
        cur = await self.conn.execute(
            "SELECT sa.situation_id, a.id AS alarm_id, a.class_id, a.device_id, a.first_seen "
            "FROM situation s JOIN situation_alarm sa ON sa.situation_id=s.id "
            "JOIN alarm a ON a.id=sa.alarm_id WHERE s.status='open'"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def situation_members(self, situation_id: int) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT a.id, a.device_id, a.class_id, a.first_seen FROM situation_alarm sa "
            "JOIN alarm a ON a.id=sa.alarm_id WHERE sa.situation_id=?",
            (situation_id,),
        )
        return [dict(r) for r in await cur.fetchall()]

    # -- feedback and labels ---------------------------------------------------------

    async def add_feedback(self, situation_id: int, verdict: str, ts: float) -> bool:
        cur = await self.conn.execute("SELECT 1 FROM situation WHERE id=?", (situation_id,))
        if await cur.fetchone() is None:
            return False
        await self.conn.execute(
            "INSERT INTO feedback (situation_id, verdict, created_at) VALUES (?, ?, ?)",
            (situation_id, verdict, ts),
        )
        return True

    async def set_label(self, kind: str, target_id: int, label: str, ts: float) -> None:
        await self.conn.execute(
            "INSERT INTO label (kind, target_id, label, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (kind, target_id) DO UPDATE SET label=excluded.label, "
            "updated_at=excluded.updated_at",
            (kind, target_id, label, ts),
        )

    # -- read models for the API -----------------------------------------------------

    async def stats(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for name, sql in (
            ("devices", "SELECT COUNT(*) FROM device"),
            ("classes", "SELECT COUNT(*) FROM alarm_class"),
            ("active_alarms", "SELECT COUNT(*) FROM alarm WHERE status='active'"),
            ("open_situations", "SELECT COUNT(*) FROM situation WHERE status='open'"),
            ("quarantined", "SELECT COUNT(*) FROM quarantine"),
        ):
            cur = await self.conn.execute(sql)
            row = await cur.fetchone()
            assert row is not None
            out[name] = int(row[0])
        return out

    async def list_situations(self, status: str | None, limit: int) -> list[dict[str, Any]]:
        where, args = ("WHERE s.status=?", [status]) if status else ("", [])
        cur = await self.conn.execute(
            "SELECT s.id, s.status, s.created_at, s.updated_at, s.root_alarm_id, "
            "COUNT(sa.alarm_id) AS alarm_count FROM situation s "
            "LEFT JOIN situation_alarm sa ON sa.situation_id=s.id "
            # nosec B608 - `where` is a fixed literal, values are bound parameters
            f"{where} GROUP BY s.id ORDER BY s.updated_at DESC LIMIT ?",
            (*args, limit),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def situation_detail(self, situation_id: int) -> dict[str, Any] | None:
        cur = await self.conn.execute("SELECT * FROM situation WHERE id=?", (situation_id,))
        head = await cur.fetchone()
        if head is None:
            return None
        cur = await self.conn.execute(
            "SELECT a.id, a.instance, a.status, a.is_flapping, a.count, a.first_seen, "
            "a.last_seen, a.severity, a.severity_rank, d.ip AS device_ip, d.vendor AS "
            "device_vendor, dl.label AS device_label, c.oid AS class_oid, c.name AS "
            "class_name, cl.label AS class_label "
            "FROM situation_alarm sa JOIN alarm a ON a.id=sa.alarm_id "
            "JOIN device d ON d.id=a.device_id JOIN alarm_class c ON c.id=a.class_id "
            "LEFT JOIN label dl ON dl.kind='device' AND dl.target_id=d.id "
            "LEFT JOIN label cl ON cl.kind='class' AND cl.target_id=c.id "
            "WHERE sa.situation_id=? ORDER BY a.first_seen",
            (situation_id,),
        )
        alarms = [dict(r) for r in await cur.fetchall()]
        cur = await self.conn.execute(
            "SELECT alarm_a, alarm_b, score, term_t, term_a, term_e FROM link "
            "WHERE situation_id=? ORDER BY id",
            (situation_id,),
        )
        links = [dict(r) for r in await cur.fetchall()]
        return {**dict(head), "alarms": alarms, "links": links}

    async def graph_snapshot(self, min_edge_n: float) -> dict[str, Any]:
        cur = await self.conn.execute(
            "SELECT d.id, d.ip, d.vendor, l.label, "
            "(SELECT COUNT(*) FROM alarm a WHERE a.device_id=d.id AND a.status='active') "
            "AS active_alarms FROM device d "
            "LEFT JOIN label l ON l.kind='device' AND l.target_id=d.id"
        )
        nodes = [dict(r) for r in await cur.fetchall()]
        cur = await self.conn.execute(
            "SELECT a_id, b_id, weight, n FROM edge WHERE kind='device' AND n>=? AND weight>0",
            (min_edge_n,),
        )
        edges = [dict(r) for r in await cur.fetchall()]
        return {"nodes": nodes, "edges": edges}

    async def list_classes(self) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT c.id, c.oid, c.vendor, c.name, l.label FROM alarm_class c "
            "LEFT JOIN label l ON l.kind='class' AND l.target_id=c.id ORDER BY c.id"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def timeline_marks(self, limit: int) -> list[dict[str, Any]]:
        """Recent alarms as raise/clear marks over time (for the UI timeline view)."""
        cur = await self.conn.execute(
            "SELECT a.first_seen, a.cleared_at, COALESCE(dl.label, d.ip) AS device, "
            "COALESCE(cl.label, c.name, c.oid) AS class FROM alarm a "
            "JOIN device d ON d.id=a.device_id JOIN alarm_class c ON c.id=a.class_id "
            "LEFT JOIN label dl ON dl.kind='device' AND dl.target_id=d.id "
            "LEFT JOIN label cl ON cl.kind='class' AND cl.target_id=c.id "
            "ORDER BY a.last_seen DESC LIMIT ?",
            (limit,),
        )
        marks: list[dict[str, Any]] = []
        for r in await cur.fetchall():
            marks.append(
                {"ts": r["first_seen"], "device": r["device"], "class": r["class"], "kind": "raise"}
            )
            if r["cleared_at"] is not None:
                marks.append(
                    {
                        "ts": r["cleared_at"],
                        "device": r["device"],
                        "class": r["class"],
                        "kind": "clear",
                    }
                )
        marks.sort(key=lambda m: m["ts"])
        return marks

    async def list_quarantine(self, limit: int) -> list[dict[str, Any]]:
        """Quarantine metadata only — never the raw payload (F4)."""
        cur = await self.conn.execute(
            "SELECT id, source, reason, sha256, length, first8, sanitized, raw, received_at "
            "FROM quarantine ORDER BY received_at DESC LIMIT ?",
            (limit,),
        )
        out: list[dict[str, Any]] = []
        for r in await cur.fetchall():
            row = dict(r)
            raw = row.pop("raw") or b""
            # Fallback for rows written before the F4 columns existed (v0.1.0 upgrades).
            row["sha256"] = row["sha256"] or hashlib.sha256(raw).hexdigest()
            row["length"] = row["length"] if row["length"] is not None else len(raw)
            row["first8"] = row["first8"] or raw[:8].hex()
            out.append(row)
        return out

    # -- entity model + varbind profiler (§5.1/§5.2) ---------------------------------

    async def list_ne(self) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT id, ip, vendor, first_seen, last_seen FROM ne ORDER BY id"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def entities_for_ne(self, ne_id: int) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT id, ne_id, parent_id, level, key, key_source, confidence, first_seen, "
            "last_seen FROM entity WHERE ne_id=? ORDER BY level, id",
            (ne_id,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def entity_count_for_ne(self, ne_id: int) -> int:
        cur = await self.conn.execute("SELECT COUNT(*) FROM entity WHERE ne_id=?", (ne_id,))
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    async def get_or_create_entity(
        self,
        ne_id: int,
        parent_id: int,
        level: int,
        key: str,
        key_source: str,
        confidence: float,
        ts: float,
    ) -> int:
        """Insert-or-get a promoted (level ≥ 1) entity; serialised under the store lock."""
        cached = self._entity_ids.get((ne_id, key))
        if cached is not None:
            return cached
        cur = await self.conn.execute(
            "SELECT id FROM entity WHERE ne_id=? AND parent_id=? AND key=?",
            (ne_id, parent_id, key),
        )
        row = await cur.fetchone()
        if row is None:
            cur = await self.conn.execute(
                "INSERT INTO entity (ne_id, parent_id, level, key, key_source, confidence, "
                "first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
                (ne_id, parent_id, level, key, key_source, confidence, ts, ts),
            )
            row = await cur.fetchone()
        assert row is not None
        self._entity_ids[(ne_id, key)] = int(row[0])
        return int(row[0])

    async def entity_keys_for_ne(self, ne_id: int) -> list[str]:
        """Keys of the promoted (level ≥ 1) entities on an NE — seeds the engine's cap set."""
        cur = await self.conn.execute("SELECT key FROM entity WHERE ne_id=? AND level>=1", (ne_id,))
        return [str(r[0]) for r in await cur.fetchall()]

    async def promoted_discriminators(self) -> list[dict[str, Any]]:
        """The learned entity discriminator chain per NE, reconstructed coarsest→finest from
        the promoted entities' levels (for engine restart). One row per (ne, level)."""
        cur = await self.conn.execute(
            "SELECT ne_id, level, key_source AS varbind_oid, MAX(confidence) AS score "
            "FROM entity WHERE level >= 1 GROUP BY ne_id, level, key_source ORDER BY ne_id, level"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def promoted_severities(self) -> list[dict[str, Any]]:
        """The confirmed severity varbind per NE (role='severity'), for engine restart (S8)."""
        cur = await self.conn.execute(
            "SELECT DISTINCT ne_id, varbind_oid FROM varbind_profile WHERE role='severity'"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def closed_alarm_varbind_lifetimes(
        self, ne_id: int, varbind_oid: str, limit: int = 2000
    ) -> list[tuple[str, float]]:
        """(varbind value, lifetime in seconds) for recent closed alarms on the NE whose stored
        varbinds include ``varbind_oid`` — the evidence the severity ordinality test needs (S8).
        Reads the varbinds JSON already on the alarm: no new column, no trap-path cost."""
        cur = await self.conn.execute(
            "SELECT varbinds, first_seen, cleared_at FROM alarm "
            "WHERE ne_id=? AND status='cleared' AND cleared_at IS NOT NULL "
            "ORDER BY cleared_at DESC LIMIT ?",
            (ne_id, limit),
        )
        out: list[tuple[str, float]] = []
        for row in await cur.fetchall():
            lifetime = float(row["cleared_at"]) - float(row["first_seen"])
            for vb in json.loads(row["varbinds"]):
                if vb.get("oid") == varbind_oid:
                    out.append((str(vb.get("value")), lifetime))
                    break
        return out

    async def varbind_profiles_for_ne(self, ne_id: int) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT ne_id, class_id, varbind_oid, n_obs, n_repeat, n_monotonic, n_numeric, "
            "n_distinct, score, role, updated_at FROM varbind_profile WHERE ne_id=? "
            "ORDER BY score DESC, varbind_oid",
            (ne_id,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def upsert_varbind_profiles(self, rows: list[tuple[Any, ...]]) -> None:
        await self.conn.executemany(
            "INSERT INTO varbind_profile (ne_id, class_id, varbind_oid, n_obs, n_repeat, "
            "n_monotonic, n_numeric, n_distinct, score, role, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (ne_id, class_id, varbind_oid) DO UPDATE SET n_obs=excluded.n_obs, "
            "n_repeat=excluded.n_repeat, n_monotonic=excluded.n_monotonic, "
            "n_numeric=excluded.n_numeric, n_distinct=excluded.n_distinct, "
            "score=excluded.score, role=excluded.role, updated_at=excluded.updated_at",
            rows,
        )

    async def load_varbind_profiles(self) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT ne_id, class_id, varbind_oid, n_obs, n_repeat, n_monotonic, n_numeric, "
            "updated_at FROM varbind_profile"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def delete_stale_varbind_profiles(self, cutoff: float) -> int:
        cur = await self.conn.execute(
            "DELETE FROM varbind_profile WHERE updated_at < ? RETURNING ne_id", (cutoff,)
        )
        return len(list(await cur.fetchall()))

    async def clear_varbind_roles(self, ne_id: int) -> int:
        """Null out the learned roles for an NE (S11 reset), keeping the evidence counters."""
        cur = await self.conn.execute(
            "UPDATE varbind_profile SET role=NULL WHERE ne_id=? AND role IS NOT NULL "
            "RETURNING ne_id",
            (ne_id,),
        )
        return len(list(await cur.fetchall()))

    async def delete_varbind_profiles_for_ne(self, ne_id: int) -> None:
        await self.conn.execute("DELETE FROM varbind_profile WHERE ne_id=?", (ne_id,))

    async def reset_ne_ids(self) -> set[int]:
        """NEs whose learned entity discriminator has been reset (S11); the engine skips
        reloading their discriminator on restart until it is legitimately re-learned."""
        cur = await self.conn.execute("SELECT key FROM meta WHERE key LIKE 'entity_reset:%'")
        return {int(str(r[0]).split(":", 1)[1]) for r in await cur.fetchall()}

    # -- state-based clears (S9, §5.5) ------------------------------------------------

    async def upsert_state_clears(self, rows: list[tuple[Any, ...]], ts: float) -> None:
        """Persist learned (class, varbind) state fields. Forward-only: a learned field is
        stable, so an existing row is never overwritten (DO NOTHING on conflict)."""
        await self.conn.executemany(
            "INSERT INTO state_clear (class_id, varbind_oid, clear_value, raise_value, "
            "learned_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT (class_id, varbind_oid) DO NOTHING",
            [(class_id, oid, clear_v, raise_v, ts) for class_id, oid, clear_v, raise_v in rows],
        )

    async def load_state_clears(self) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT class_id, varbind_oid, clear_value, raise_value FROM state_clear"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def list_state_clears(self) -> list[dict[str, Any]]:
        """Learned state fields joined to their class, for inspection (which OID, which values)."""
        cur = await self.conn.execute(
            "SELECT s.class_id, s.varbind_oid, s.clear_value, s.raise_value, s.learned_at, "
            "COALESCE(cl.label, c.name, c.oid) AS class FROM state_clear s "
            "JOIN alarm_class c ON c.id=s.class_id "
            "LEFT JOIN label cl ON cl.kind='class' AND cl.target_id=c.id "
            "ORDER BY s.learned_at DESC"
        )
        return [dict(r) for r in await cur.fetchall()]

    # -- ingest gaps (§5.6) ----------------------------------------------------------

    async def record_ingest_gap(
        self, started_at: float, ended_at: float, dropped: int, reason: str
    ) -> None:
        """Durably record a window of dropped traps: 'events lost between t1 and t2'."""
        await self.conn.execute(
            "INSERT INTO ingest_gap (started_at, ended_at, dropped, reason) VALUES (?, ?, ?, ?)",
            (started_at, ended_at, dropped, reason),
        )

    async def list_ingest_gaps(self, limit: int) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT id, started_at, ended_at, dropped, reason FROM ingest_gap "
            "ORDER BY started_at DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in await cur.fetchall()]

    # -- scoring configuration (v0.6.0, §the scoring seam) ---------------------------

    async def active_scorer_config(self) -> dict[str, Any] | None:
        """The configuration the one-row pointer names, or None if the pointer is unset.

        Read at the engine's configuration reload point — never per packet, never per candidate
        pair, and never in ``receiver.datagram_received`` (prime directive 2)."""
        cur = await self.conn.execute(
            "SELECT c.* FROM scorer_active a JOIN scorer_config c ON c.id = a.config_id "
            "WHERE a.id = 1"
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def get_scorer_config(self, config_id: int) -> dict[str, Any] | None:
        cur = await self.conn.execute("SELECT * FROM scorer_config WHERE id=?", (config_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def list_scorer_configs(self, limit: int) -> list[dict[str, Any]]:
        """The immutable configuration history, newest first, with the active one flagged."""
        cur = await self.conn.execute(
            "SELECT c.*, (a.config_id IS NOT NULL) AS active FROM scorer_config c "
            "LEFT JOIN scorer_active a ON a.config_id = c.id AND a.id = 1 "
            "ORDER BY c.id DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def insert_scorer_config(
        self,
        scorer_id: str,
        contract_version: str,
        w_t: float,
        w_a: float,
        w_e: float,
        tau_s: float,
        threshold: float,
        params_hash: str,
        created_by: str | None,
        created_at: float,
        note: str,
    ) -> int:
        """Append one immutable configuration row. Never UPDATE, never DELETE (triggers abort)."""
        cur = await self.conn.execute(
            "INSERT INTO scorer_config (scorer_id, contract_version, w_t, w_a, w_e, tau_s, "
            "threshold, params_hash, created_by, created_at, note) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (
                scorer_id,
                contract_version,
                w_t,
                w_a,
                w_e,
                tau_s,
                threshold,
                params_hash,
                created_by,
                created_at,
                note,
            ),
        )
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    async def set_active_scorer_config(
        self, config_id: int, activated_by: str | None, ts: float
    ) -> bool:
        """Point the single active row at a configuration. Apply and rollback are this one call —
        history is immutable, so reverting is moving the pointer, never editing or deleting."""
        cur = await self.conn.execute("SELECT 1 FROM scorer_config WHERE id=?", (config_id,))
        if await cur.fetchone() is None:
            return False
        await self.conn.execute(
            "INSERT INTO scorer_active (id, config_id, activated_by, activated_at) "
            "VALUES (1, ?, ?, ?) ON CONFLICT (id) DO UPDATE SET config_id=excluded.config_id, "
            "activated_by=excluded.activated_by, activated_at=excluded.activated_at",
            (config_id, activated_by, ts),
        )
        return True

    async def recent_alarms_for_preview(self, limit: int) -> list[dict[str, Any]]:
        """A bounded, read-only snapshot of recent alarms for the scorer what-if (v0.6.0).

        Most-recent-first at the SQL level so the cap keeps the *newest* window, then returned in
        chronological order so the replay ordering — and therefore the preview result — is
        deterministic. Reads five columns and writes nothing."""
        cur = await self.conn.execute(
            "SELECT id, ne_id, entity_id, class_id, first_seen FROM alarm "
            "ORDER BY first_seen DESC, id DESC LIMIT ?",
            (limit,),
        )
        rows = [dict(r) for r in await cur.fetchall()]
        rows.reverse()
        return rows

    # -- governance policy (v0.7.0, §the perimeter) ----------------------------------
    #
    # Same discipline as `scorer_config`: an append-only history plus a pointer, so a change is
    # auditable and reversible and history is tamper-evident. One row holds the WHOLE policy for
    # its kind as canonical JSON, because a policy is read and applied as a unit on every request —
    # which makes "which policy was active" a single id and rollback a pointer move.
    #
    # Read HTTP-side, per request, and nowhere else. The trap path never touches these tables.

    async def active_governance_ids(self) -> dict[str, int]:
        """``{kind: policy_id}`` for the kinds that have an active policy (at most two rows).

        The per-request read. It is deliberately id-only: the API re-parses a document only when
        its id differs from the one it is holding, so a change lands on the very next request while
        the parse cost is paid once per policy version. An empty result means "no governance
        configured", which resolves to the compiled ceiling and full visibility — v0.6.0 exactly.
        """
        cur = await self.conn.execute("SELECT kind, policy_id FROM governance_active")
        return {str(r["kind"]): int(r["policy_id"]) for r in await cur.fetchall()}

    async def get_governance_policy(self, policy_id: int) -> dict[str, Any] | None:
        cur = await self.conn.execute("SELECT * FROM governance_policy WHERE id=?", (policy_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def list_governance_policies(self, kind: str, limit: int) -> list[dict[str, Any]]:
        """The immutable history for one kind, newest first, with the active one flagged."""
        cur = await self.conn.execute(
            "SELECT p.*, (a.policy_id IS NOT NULL) AS active FROM governance_policy p "
            "LEFT JOIN governance_active a ON a.policy_id = p.id AND a.kind = p.kind "
            "WHERE p.kind=? ORDER BY p.id DESC LIMIT ?",
            (kind, limit),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def insert_governance_policy(
        self,
        kind: str,
        document: str,
        doc_hash: str,
        created_by: str | None,
        created_at: float,
        note: str,
    ) -> int:
        """Append one immutable policy row. Never UPDATE, never DELETE (triggers abort)."""
        cur = await self.conn.execute(
            "INSERT INTO governance_policy (kind, document, doc_hash, created_by, created_at, "
            "note) VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
            (kind, document, doc_hash, created_by, created_at, note),
        )
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    async def set_active_governance_policy(
        self, kind: str, policy_id: int, activated_by: str | None, ts: float
    ) -> bool:
        """Point `kind`'s active row at a policy. Apply and rollback are this one call — history is
        immutable, so reverting is moving the pointer, never editing or deleting."""
        cur = await self.conn.execute(
            "SELECT 1 FROM governance_policy WHERE id=? AND kind=?", (policy_id, kind)
        )
        if await cur.fetchone() is None:
            return False
        await self.conn.execute(
            "INSERT INTO governance_active (kind, policy_id, activated_by, activated_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT (kind) DO UPDATE SET policy_id=excluded.policy_id, "
            "activated_by=excluded.activated_by, activated_at=excluded.activated_at",
            (kind, policy_id, activated_by, ts),
        )
        return True

    async def clear_active_governance_policy(self, kind: str) -> bool:
        """Remove `kind`'s pointer: back to the shipped baseline (ceiling / full visibility).

        This is the recovery path, and it is why the pointer table is not append-only. The history
        rows survive, so what was once active remains answerable.
        """
        cur = await self.conn.execute("DELETE FROM governance_active WHERE kind=?", (kind,))
        return bool(cur.rowcount)

    async def scoped_stats(self, ne_ids: frozenset[int], ips: frozenset[str]) -> dict[str, int]:
        """:meth:`stats` computed over one principal's visible NEs only.

        Every counter here is an enumeration of something an out-of-scope NE could contribute to,
        so leaving any of them global would hand a scoped viewer a volume oracle: a rising
        `active_alarms` with nothing visible to explain it says "a storm is happening somewhere you
        cannot see", and a rising `classes` says "a device you cannot see just emitted a trap type
        nobody has emitted before". `quarantined` is filtered by source address for the same
        reason. Situations count when they have at least one visible member, matching the listing
        rule exactly (DECISIONS #59).
        """
        if not ne_ids:
            return {
                "devices": 0,
                "classes": 0,
                "active_alarms": 0,
                "open_situations": 0,
                "quarantined": 0,
            }
        ne_marks = ",".join("?" * len(ne_ids))
        ne_args = tuple(sorted(ne_ids))
        out: dict[str, int] = {"devices": len(ne_ids)}
        for name, sql in (
            (
                "classes",
                f"SELECT COUNT(DISTINCT class_id) FROM alarm WHERE ne_id IN ({ne_marks})",  # nosec B608 - placeholders only
            ),
            (
                "active_alarms",
                f"SELECT COUNT(*) FROM alarm WHERE status='active' AND ne_id IN ({ne_marks})",  # nosec B608 - placeholders only
            ),
            (
                "open_situations",
                "SELECT COUNT(DISTINCT s.id) FROM situation s "
                "JOIN situation_alarm sa ON sa.situation_id=s.id "
                "JOIN alarm a ON a.id=sa.alarm_id "
                f"WHERE s.status='open' AND a.ne_id IN ({ne_marks})",  # nosec B608 - placeholders only
            ),
        ):
            cur = await self.conn.execute(sql, ne_args)
            row = await cur.fetchone()
            assert row is not None
            out[name] = int(row[0])
        if ips:
            ip_marks = ",".join("?" * len(ips))
            cur = await self.conn.execute(
                f"SELECT COUNT(*) FROM quarantine WHERE source IN ({ip_marks})",  # nosec B608 - placeholders only
                tuple(sorted(ips)),
            )
            row = await cur.fetchone()
            assert row is not None
            out["quarantined"] = int(row[0])
        else:
            out["quarantined"] = 0
        return out

    async def situation_member_nes(self, situation_ids: list[int]) -> dict[int, list[int | None]]:
        """``{situation_id: [ne_id per member alarm]}`` — the scope filter's input for a listing.

        One query for the whole page rather than one per situation. A **list**, not a set, because
        the caller needs both "is any member visible?" (whether to list it at all) and "how many
        members are visible versus redacted?" (the honest counts), and a set would lose the second.
        """
        if not situation_ids:
            return {}
        marks = ",".join("?" * len(situation_ids))
        cur = await self.conn.execute(
            "SELECT sa.situation_id, a.ne_id FROM situation_alarm sa "
            f"JOIN alarm a ON a.id=sa.alarm_id WHERE sa.situation_id IN ({marks})",  # nosec B608 - placeholders only
            tuple(situation_ids),
        )
        out: dict[int, list[int | None]] = {}
        for row in await cur.fetchall():
            ne_id = int(row["ne_id"]) if row["ne_id"] is not None else None
            out.setdefault(int(row["situation_id"]), []).append(ne_id)
        return out

    async def situation_member_ne(self, situation_id: int) -> dict[int, int | None]:
        """``{alarm_id: ne_id}`` for one situation's members — which members a reader may see."""
        cur = await self.conn.execute(
            "SELECT sa.alarm_id, a.ne_id FROM situation_alarm sa "
            "JOIN alarm a ON a.id=sa.alarm_id WHERE sa.situation_id=?",
            (situation_id,),
        )
        return {
            int(r["alarm_id"]): (int(r["ne_id"]) if r["ne_id"] is not None else None)
            for r in await cur.fetchall()
        }

    async def list_ne_for_scope(self) -> list[dict[str, Any]]:
        """Every NE as ``(id, ip, label)`` — the input to resolving scope selectors to NE ids.

        The label comes from the `device` row with the same address, because labels are stored
        against `device` while scoping is expressed over `ne`. The join is on **ip**, not on id:
        the two tables' ids coincide only because migration 0003 seeded `ne` from `device`, which
        is an artefact rather than a declared invariant (and the device_id → ne_id cutover is an
        open ROADMAP line).

        Called only when a scope policy is active and the caller is not an admin, so an
        un-configured appliance runs the v0.6.0 read paths untouched.
        """
        cur = await self.conn.execute(
            "SELECT n.id, n.ip, l.label FROM ne n "
            "LEFT JOIN device d ON d.ip = n.ip "
            "LEFT JOIN label l ON l.kind='device' AND l.target_id = d.id "
            "ORDER BY n.id"
        )
        return [dict(r) for r in await cur.fetchall()]

    # -- auth: users --------------------------------------------------------------------

    async def count_users(self) -> int:
        cur = await self.conn.execute("SELECT COUNT(*) FROM user")
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    async def create_user(
        self, username: str, password_hash: str, role: str, must_change_password: bool, now: float
    ) -> int:
        cur = await self.conn.execute(
            "INSERT INTO user (username, password_hash, role, must_change_password, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
            (username, password_hash, role, int(must_change_password), now, now),
        )
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    async def get_user_by_name(self, username: str) -> dict[str, Any] | None:
        cur = await self.conn.execute("SELECT * FROM user WHERE username=?", (username,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def get_user(self, user_id: int) -> dict[str, Any] | None:
        cur = await self.conn.execute("SELECT * FROM user WHERE id=?", (user_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def list_users(self) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT id, username, role, must_change_password, disabled, created_at "
            "FROM user ORDER BY username"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def update_user_password(self, user_id: int, password_hash: str, now: float) -> None:
        await self.conn.execute(
            "UPDATE user SET password_hash=?, must_change_password=0, updated_at=? WHERE id=?",
            (password_hash, now, user_id),
        )

    async def set_user_role(self, user_id: int, role: str, now: float) -> None:
        await self.conn.execute(
            "UPDATE user SET role=?, updated_at=? WHERE id=?", (role, now, user_id)
        )

    async def set_must_change_password(self, user_id: int, flag: bool, now: float) -> None:
        await self.conn.execute(
            "UPDATE user SET must_change_password=?, updated_at=? WHERE id=?",
            (int(flag), now, user_id),
        )

    async def delete_user(self, user_id: int) -> bool:
        cur = await self.conn.execute("DELETE FROM user WHERE id=? RETURNING id", (user_id,))
        return await cur.fetchone() is not None

    # -- auth: sessions -----------------------------------------------------------------

    async def create_session(
        self,
        session_hash: str,
        user_id: int,
        created_at: float,
        last_seen_at: float,
        idle_expiry: float,
        absolute_expiry: float,
        source_ip: str | None,
    ) -> None:
        await self.conn.execute(
            "INSERT INTO session (session_hash, user_id, created_at, last_seen_at, idle_expiry, "
            "absolute_expiry, source_ip) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                session_hash,
                user_id,
                created_at,
                last_seen_at,
                idle_expiry,
                absolute_expiry,
                source_ip,
            ),
        )

    async def get_session(self, session_hash: str) -> dict[str, Any] | None:
        cur = await self.conn.execute(
            "SELECT s.session_hash, s.user_id, s.created_at, s.idle_expiry, s.absolute_expiry, "
            "u.username, u.role, u.must_change_password, u.disabled "
            "FROM session s JOIN user u ON u.id=s.user_id WHERE s.session_hash=?",
            (session_hash,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def touch_session(
        self, session_hash: str, last_seen_at: float, idle_expiry: float
    ) -> None:
        await self.conn.execute(
            "UPDATE session SET last_seen_at=?, idle_expiry=? WHERE session_hash=?",
            (last_seen_at, idle_expiry, session_hash),
        )

    async def delete_session(self, session_hash: str) -> None:
        await self.conn.execute("DELETE FROM session WHERE session_hash=?", (session_hash,))

    async def revoke_user_sessions(self, user_id: int) -> int:
        cur = await self.conn.execute(
            "DELETE FROM session WHERE user_id=? RETURNING session_hash", (user_id,)
        )
        return len(list(await cur.fetchall()))

    async def purge_expired_sessions(self, now: float) -> int:
        cur = await self.conn.execute(
            "DELETE FROM session WHERE absolute_expiry < ? OR idle_expiry < ? "
            "RETURNING session_hash",
            (now, now),
        )
        return len(list(await cur.fetchall()))

    # -- auth: service tokens -----------------------------------------------------------

    async def create_token(
        self, token_hash: str, name: str, role: str, created_by: str | None, now: float
    ) -> int:
        cur = await self.conn.execute(
            "INSERT INTO api_token (token_hash, name, role, created_at, created_by) "
            "VALUES (?, ?, ?, ?, ?) RETURNING id",
            (token_hash, name, role, now, created_by),
        )
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    async def get_token(self, token_hash: str) -> dict[str, Any] | None:
        cur = await self.conn.execute(
            "SELECT id, token_hash, name, role, revoked FROM api_token WHERE token_hash=?",
            (token_hash,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def list_tokens(self) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT id, name, role, created_at, created_by, last_used_at, revoked "
            "FROM api_token ORDER BY name"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def touch_token_used(self, token_hash: str, now: float) -> None:
        await self.conn.execute(
            "UPDATE api_token SET last_used_at=? WHERE token_hash=?", (now, token_hash)
        )

    async def revoke_token(self, token_id: int, now: float) -> dict[str, Any] | None:
        cur = await self.conn.execute(
            "UPDATE api_token SET revoked=1, revoked_at=? WHERE id=? AND revoked=0 RETURNING name",
            (now, token_id),
        )
        row = await cur.fetchone()
        return {"name": str(row[0])} if row else None

    # -- audit log -------------------------------------------------------------------

    async def audit_last_hash(self) -> str:
        """entry_hash of the newest audit row, or the genesis hash for an empty chain."""
        cur = await self.conn.execute("SELECT entry_hash FROM audit_log ORDER BY id DESC LIMIT 1")
        row = await cur.fetchone()
        return str(row[0]) if row else "0" * 64

    async def audit_next_id(self) -> int:
        """The id the next appended row will take (reserved under the store lock)."""
        cur = await self.conn.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM audit_log")
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    async def audit_insert(self, entry: dict[str, Any], prev_hash: str, entry_hash: str) -> None:
        await self.conn.execute(
            "INSERT INTO audit_log (id, ts, actor, role, source_ip, action, object_type, "
            "object_id, outcome, details, prev_hash, entry_hash) "
            "VALUES (:id, :ts, :actor, :role, :source_ip, :action, :object_type, :object_id, "
            ":outcome, :details, :prev_hash, :entry_hash)",
            {
                "id": entry["id"],
                "ts": entry["ts"],
                "actor": entry["actor"],
                "role": entry["role"],
                "source_ip": entry["source_ip"],
                "action": entry["action"],
                "object_type": entry["object_type"],
                "object_id": entry["object_id"],
                "outcome": entry["outcome"],
                "details": json.dumps(entry["details"], sort_keys=True, separators=(",", ":")),
                "prev_hash": prev_hash,
                "entry_hash": entry_hash,
            },
        )

    async def audit_all(self) -> list[dict[str, Any]]:
        """Every audit row in id (chain) order — for verify and export."""
        cur = await self.conn.execute(
            "SELECT id, ts, actor, role, source_ip, action, object_type, object_id, outcome, "
            "details, prev_hash, entry_hash FROM audit_log ORDER BY id"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def list_audit(self, limit: int) -> list[dict[str, Any]]:
        """Most-recent audit rows for the viewer (no hashes; details left as JSON text)."""
        cur = await self.conn.execute(
            "SELECT id, ts, actor, role, source_ip, action, object_type, object_id, outcome, "
            "details FROM audit_log ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def prune_audit(self, cutoff: float) -> int:
        """Delete audit rows older than cutoff. The one sanctioned deleter (DECISIONS v0.2
        #3): drops the append-only triggers, deletes, and recreates them in this locked
        transaction. Pruning only the oldest rows keeps the surviving suffix verifiable."""
        await self.conn.execute("DROP TRIGGER IF EXISTS audit_log_no_update")
        await self.conn.execute("DROP TRIGGER IF EXISTS audit_log_no_delete")
        cur = await self.conn.execute("DELETE FROM audit_log WHERE ts < ? RETURNING id", (cutoff,))
        removed = len(list(await cur.fetchall()))
        await self.conn.execute(
            "CREATE TRIGGER audit_log_no_update BEFORE UPDATE ON audit_log "
            "BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END"
        )
        await self.conn.execute(
            "CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON audit_log "
            "BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END"
        )
        return removed

    # -- retention -------------------------------------------------------------------

    async def prune(self, now: float, retention_s: float) -> dict[str, int]:
        """Bounded growth: drop old cleared alarms, closed situations, and quarantine."""
        cutoff = now - retention_s
        counts: dict[str, int] = {}
        cur = await self.conn.execute(
            "SELECT id FROM situation WHERE status IN ('closed','merged') AND closed_at < ?",
            (cutoff,),
        )
        gone = [int(r[0]) for r in await cur.fetchall()]
        counts["situations"] = len(gone)
        if gone:
            # nosec B608 - `marks` is only "?" placeholders; ids are bound parameters
            marks = ",".join("?" * len(gone))
            await self.conn.execute(
                f"DELETE FROM situation_alarm WHERE situation_id IN ({marks})",  # nosec B608
                gone,
            )
            await self.conn.execute(
                f"DELETE FROM link WHERE situation_id IN ({marks})",  # nosec B608
                gone,
            )
            await self.conn.execute(
                f"DELETE FROM feedback WHERE situation_id IN ({marks})",  # nosec B608
                gone,
            )
            await self.conn.execute(
                f"DELETE FROM situation WHERE id IN ({marks})",  # nosec B608
                gone,
            )
        cur = await self.conn.execute(
            "DELETE FROM alarm WHERE status='cleared' AND last_seen < ? AND id NOT IN "
            "(SELECT alarm_id FROM situation_alarm) RETURNING id",
            (cutoff,),
        )
        counts["alarms"] = len(list(await cur.fetchall()))
        cur = await self.conn.execute(
            "DELETE FROM quarantine WHERE received_at < ? RETURNING id", (cutoff,)
        )
        counts["quarantine"] = len(list(await cur.fetchall()))
        return counts
