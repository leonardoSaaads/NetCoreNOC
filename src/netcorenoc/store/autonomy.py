"""Autonomy's settings and decisions, and the search's trials (v0.26.0, `0026`). SQL only.

**No method here takes ``store.lock``**, which is this package's contract for callers.
"""

from __future__ import annotations

import json
from typing import Any

from netcorenoc.store.base import StoreBase
from netcorenoc.store.situations import LIVE

__all__ = ["AutonomyMixin"]

_SETTING_COLUMNS = (
    "grouping",
    "naming",
    "closing",
    "severity",
    "agreement_floor",
    "window",
    "min_judged",
    "confidence_floor",
)


class AutonomyMixin(StoreBase):
    async def _has(self, table: str) -> bool:
        cur = await self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        )
        return await cur.fetchone() is not None

    # -- settings --------------------------------------------------------------------------

    async def autonomy_setting(self) -> dict[str, Any] | None:
        if not await self._has("autonomy_setting"):
            return None
        cur = await self.conn.execute("SELECT * FROM autonomy_setting ORDER BY id DESC LIMIT 1")
        row = await cur.fetchone()
        return dict(row) if row else None

    async def autonomy_history(self, limit: int = 20) -> list[dict[str, Any]]:
        if not await self._has("autonomy_setting"):
            return []
        cur = await self.conn.execute(
            "SELECT * FROM autonomy_setting ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in await cur.fetchall()]

    async def set_autonomy(
        self, values: dict[str, Any], set_by: str, ts: float, reason: str
    ) -> int:
        cols = [c for c in _SETTING_COLUMNS if c in values]
        marks = ", ".join("?" * (len(cols) + 3))
        cur = await self.conn.execute(
            # nosec B608 - column names come from the fixed tuple above; every value is bound.
            f"INSERT INTO autonomy_setting ({', '.join(cols)}, set_by, set_at, reason) "  # nosec B608
            f"VALUES ({marks}) RETURNING id",
            (*[values[c] for c in cols], set_by, ts, reason),
        )
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    # -- decisions -------------------------------------------------------------------------

    async def add_autonomy_decision(
        self,
        *,
        situation_id: int,
        grade: str,
        action: str,
        decider: str,
        confidence: float,
        explanation: dict[str, Any],
        at: float,
    ) -> int:
        cur = await self.conn.execute(
            "INSERT INTO autonomy_decision (situation_id, grade, action, decider, confidence, "
            "explanation, at) VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (
                situation_id,
                grade,
                action,
                decider,
                confidence,
                json.dumps(explanation, sort_keys=True, separators=(",", ":")),
                at,
            ),
        )
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    async def autonomy_decisions(
        self, *, limit: int = 50, situation_id: int | None = None, pending: bool = False
    ) -> list[dict[str, Any]]:
        if not await self._has("autonomy_decision"):
            return []
        where, args = [], []
        if situation_id is not None:
            where.append("situation_id = ?")
            args.append(situation_id)
        if pending:
            where.append("verdict IS NULL")
        clause = f"WHERE {' AND '.join(where)} " if where else ""
        cur = await self.conn.execute(
            # nosec B608 - the clause is assembled from two fixed fragments; values are bound.
            f"SELECT * FROM autonomy_decision {clause}ORDER BY id DESC LIMIT ?",  # nosec B608
            (*args, limit),
        )
        rows = [dict(r) for r in await cur.fetchall()]
        for row in rows:
            row["explanation"] = json.loads(row["explanation"])
        return rows

    async def judge_autonomy_decision(
        self, decision_id: int, verdict: str, at: float, by: str, why: str
    ) -> None:
        await self.conn.execute(
            "UPDATE autonomy_decision SET verdict=?, verdict_at=?, verdict_by=?, verdict_why=? "
            "WHERE id=? AND verdict IS NULL",
            (verdict, at, by, why, decision_id),
        )

    async def recent_verdicts(self, window: int) -> list[str]:
        """The last ``window`` judged decisions' verdicts, newest first."""
        if not await self._has("autonomy_decision"):
            return []
        cur = await self.conn.execute(
            "SELECT verdict FROM autonomy_decision WHERE verdict IS NOT NULL "
            "ORDER BY verdict_at DESC, id DESC LIMIT ?",
            (window,),
        )
        return [str(r[0]) for r in await cur.fetchall()]

    async def situation_gestures_after(
        self, situation_id: int, after: float
    ) -> list[dict[str, Any]]:
        """Operator gestures on a situation after ``after`` — never the model's own. A verdict
        event carries its feedback's `confirm`/`split` as ``verdict``."""
        cur = await self.conn.execute(
            "SELECT e.kind, e.actor, e.at, f.verdict FROM situation_event e "
            "LEFT JOIN feedback f ON f.id = e.feedback_id "
            "WHERE e.situation_id=? AND e.at>? AND e.actor IS NOT NULL "
            "AND e.actor NOT LIKE 'model:%' ORDER BY e.at",
            (situation_id, after),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def autonomy_candidates(self, settled_before: float, limit: int) -> list[dict[str, Any]]:
        """Live situations formed at least a settling period ago, newest activity first."""
        cur = await self.conn.execute(
            # nosec B608 - `LIVE` is the store's one module literal; every value is bound.
            "SELECT id, status, created_at, updated_at, decider, operator_name, model_name, "
            f"severity, severity_by FROM situation WHERE {LIVE} AND created_at <= ? "  # nosec B608
            "ORDER BY updated_at DESC LIMIT ?",
            (settled_before, limit),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def situation_link_scores(self, situation_id: int) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT alarm_a, alarm_b, score, terms FROM link WHERE situation_id=?", (situation_id,)
        )
        rows = [dict(r) for r in await cur.fetchall()]
        for row in rows:
            row["terms"] = json.loads(row["terms"]) if row.get("terms") else None
        return rows

    async def situation_profile(self, situation_id: int) -> list[dict[str, Any]]:
        """Each member's class, element, severity and status — what naming and closing read."""
        cur = await self.conn.execute(
            "SELECT a.id AS alarm_id, a.class_id, c.oid AS class_oid, d.ip AS device_ip, "
            "a.severity_rank, a.status, (s.root_alarm_id = a.id) AS is_root "
            "FROM situation_alarm sa JOIN alarm a ON a.id = sa.alarm_id "
            "JOIN alarm_class c ON c.id = a.class_id JOIN device d ON d.id = a.device_id "
            "JOIN situation s ON s.id = sa.situation_id WHERE sa.situation_id=? ORDER BY a.id",
            (situation_id,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def any_reactivated(self, alarm_ids: list[int], after: float) -> bool:
        if not alarm_ids:
            return False
        marks = ",".join("?" * len(alarm_ids))
        cur = await self.conn.execute(
            # nosec B608 - only placeholders are interpolated.
            f"SELECT 1 FROM alarm WHERE id IN ({marks}) AND status='active' AND last_seen > ? "  # nosec B608
            "LIMIT 1",
            (*alarm_ids, after),
        )
        return await cur.fetchone() is not None

    async def situation_severity(self, situation_id: int) -> tuple[str | None, str | None] | None:
        cur = await self.conn.execute(
            "SELECT severity, severity_by FROM situation WHERE id=?", (situation_id,)
        )
        row = await cur.fetchone()
        return None if row is None else (row[0], row[1])

    async def set_situation_severity(self, situation_id: int, severity: str, by: str) -> None:
        await self.conn.execute(
            "UPDATE situation SET severity=?, severity_by=? WHERE id=?",
            (severity, by, situation_id),
        )

    async def set_situation_name_by_model(self, situation_id: int, name: str) -> bool:
        """Name a situation nobody has named, in `model_name` — never `operator_name`, whose one
        writer is the rename route. Refuses (False) once an operator or the model has named it."""
        cur = await self.conn.execute(
            "UPDATE situation SET model_name=? "
            "WHERE id=? AND operator_name IS NULL AND model_name IS NULL RETURNING id",
            (name, situation_id),
        )
        return await cur.fetchone() is not None

    # -- the search ------------------------------------------------------------------------

    async def open_search_run(
        self, *, by: str, seed: int, budget: dict[str, Any], at: float
    ) -> int:
        cur = await self.conn.execute(
            "INSERT INTO search_run (started_at, started_by, seed, budget, status) "
            "VALUES (?, ?, ?, ?, 'running') RETURNING id",
            (at, by, seed, json.dumps(budget, sort_keys=True, separators=(",", ":"))),
        )
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    async def finish_search_run(
        self,
        run_id: int,
        status: str,
        at: float,
        note: str = "",
        *,
        model_version_id: int | None = None,
        rows: int | None = None,
        judgement: dict[str, Any] | None = None,
    ) -> None:
        await self.conn.execute(
            "UPDATE search_run SET status=?, finished_at=?, note=?, model_version_id=?, rows=?, "
            "judgement=? WHERE id=?",
            (
                status,
                at,
                note,
                model_version_id,
                rows,
                None if judgement is None else json.dumps(judgement, sort_keys=True),
                run_id,
            ),
        )

    async def add_search_trial(self, run_id: int, trial: dict[str, Any]) -> None:
        await self.conn.execute(
            "INSERT OR IGNORE INTO search_trial (run_id, trial, rung, params, rounds, best_round, "
            "train_loss, valid_loss, seconds, status, trace) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                trial["index"],
                trial["rung"],
                json.dumps(trial["params"], sort_keys=True, separators=(",", ":")),
                trial["rounds"],
                trial["best_round"],
                _finite(trial["train_loss"]),
                _finite(trial["valid_loss"]),
                trial["seconds"],
                trial["status"],
                json.dumps(trial["trace"]),
            ),
        )

    async def search_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        if not await self._has("search_run"):
            return []
        cur = await self.conn.execute("SELECT * FROM search_run ORDER BY id DESC LIMIT ?", (limit,))
        rows = [dict(r) for r in await cur.fetchall()]
        for row in rows:
            row["budget"] = json.loads(row["budget"])
            row["judgement"] = json.loads(row["judgement"]) if row["judgement"] else None
        return rows

    async def pair_features(self, pair_ids: list[int]) -> dict[int, str]:
        """The captured v2 vector of each pair that has one (`0026`), by pair id."""
        if not pair_ids or not self._has_pair_features:
            return {}
        out: dict[int, str] = {}
        for start in range(0, len(pair_ids), 500):
            chunk = pair_ids[start : start + 500]
            marks = ",".join("?" * len(chunk))
            cur = await self.conn.execute(
                # nosec B608 - only placeholders are interpolated.
                f"SELECT id, features FROM dataset_pair WHERE id IN ({marks}) "  # nosec B608
                "AND features IS NOT NULL",
                chunk,
            )
            out.update({int(r[0]): str(r[1]) for r in await cur.fetchall()})
        return out

    async def search_trials(self, run_id: int) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT * FROM search_trial WHERE run_id=? ORDER BY rung, trial", (run_id,)
        )
        rows = [dict(r) for r in await cur.fetchall()]
        for row in rows:
            row["params"] = json.loads(row["params"])
            row["trace"] = json.loads(row["trace"])
        return rows


def _finite(value: float) -> float | None:
    return value if value == value and value not in (float("inf"), float("-inf")) else None
