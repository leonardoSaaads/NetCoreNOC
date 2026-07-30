"""Transitional holder for the `Store` methods not yet moved into their domain module.

Deleted at the end of Phase 3, when the last section leaves and `Store` moves to `__init__.py`.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import aiosqlite

from netcorenoc.store.alarms import AlarmMixin
from netcorenoc.store.base import StoreBase
from netcorenoc.store.devices import DeviceMixin
from netcorenoc.store.feedback import FeedbackMixin
from netcorenoc.store.governance import GovernanceMixin
from netcorenoc.store.learned import LearnedMixin
from netcorenoc.store.lifecycle import LifecycleMixin
from netcorenoc.store.read_models import ReadModelsMixin
from netcorenoc.store.situations import SituationMixin


class Store(
    ReadModelsMixin,
    GovernanceMixin,
    FeedbackMixin,
    SituationMixin,
    LearnedMixin,
    AlarmMixin,
    DeviceMixin,
    LifecycleMixin,
    StoreBase,
):
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

    # -- devices and classes ---------------------------------------------------------

    # -- alarms ----------------------------------------------------------------------

    # -- learned state ---------------------------------------------------------------

    # -- situations ------------------------------------------------------------------

    # -- feedback and labels ---------------------------------------------------------

    # -- read models for the API -----------------------------------------------------

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
