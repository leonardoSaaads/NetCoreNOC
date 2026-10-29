"""NOC agents and their runs (ADRs #465, #467). Storage only.

Who an agent is and what a run may be is `engine/noc/agents.py`; who may hire, change or wake one
is `api/routes/agents.py`; what wakes them and routes cases to them is `engine/noc/agent_loop.py`.
The sealed columns (`token_sealed`, `webhook_key_sealed`) are read by one method,
`noc_agent_sealed`, and never by a listing.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from netcorenoc.store.base import StoreBase

#: The agent row as every read returns it — never a sealed column.
_AGENT = (
    "a.id, a.name, a.role, a.title, a.icon, a.tier, a.reports_to, a.capabilities, a.instructions, "
    "a.adapter, a.adapter_config, a.token_id, a.entry, a.wake_on_assignment, a.heartbeat_s, "
    "a.escalate_after_s, a.run_timeout_s, a.budget_month_cents, a.spent_cents, a.spend_month, "
    "a.status, a.pause_reason, a.paused_at, a.error_reason, a.last_run_at, a.version, "
    "a.created_at, a.created_by, a.updated_at, a.updated_by, "
    "(a.token_sealed IS NOT NULL) AS acts_in_process, "
    "(SELECT COUNT(*) FROM noc_case c WHERE c.agent_id = a.id "
    "AND c.status NOT IN ('resolved', 'closed')) AS open_cases"
)
_RUN = (
    "r.id, r.agent_id, a.name AS agent_name, r.trigger, r.situation_id, r.status, r.queued_at, "
    "r.started_at, r.ended_at, r.requested_by, r.summary, r.error, r.input_tokens, "
    "r.output_tokens, r.cost_cents, r.tool_calls"
)
#: Fields an admin edits directly; the status, identity and spend move only through the methods.
EDITABLE = (
    "name",
    "role",
    "title",
    "icon",
    "tier",
    "reports_to",
    "capabilities",
    "instructions",
    "adapter",
    "adapter_config",
    "entry",
    "wake_on_assignment",
    "heartbeat_s",
    "escalate_after_s",
    "run_timeout_s",
    "budget_month_cents",
)


class NocAgentMixin(StoreBase):
    # -- agents ---------------------------------------------------------------------------------

    async def noc_agents(self, *, include_terminated: bool = False) -> list[dict[str, Any]]:
        where = "" if include_terminated else "WHERE a.status <> 'terminated' "
        cur = await self.conn.execute(
            f"SELECT {_AGENT} FROM noc_agent a {where}ORDER BY a.tier DESC, a.name"  # nosec B608
        )
        return [dict(r) for r in await cur.fetchall()]

    async def noc_agent(self, agent_id: int) -> dict[str, Any] | None:
        cur = await self.conn.execute(
            f"SELECT {_AGENT} FROM noc_agent a WHERE a.id = ?",  # nosec B608 - literal
            (agent_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def noc_agent_by_token(self, token_id: int) -> dict[str, Any] | None:
        """The agent a service token is the identity of, if it is one."""
        cur = await self.conn.execute(
            f"SELECT {_AGENT} FROM noc_agent a WHERE a.token_id = ?",  # nosec B608 - literal
            (token_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def noc_agent_sealed(self, agent_id: int) -> dict[str, Any] | None:
        """The sealed token and signing key — for the agent loop alone, which opens them in this
        process to act as the agent or to sign its wake-ups."""
        cur = await self.conn.execute(
            "SELECT token_sealed, webhook_key_sealed FROM noc_agent WHERE id = ?", (agent_id,)
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def noc_create_agent(self, fields: Mapping[str, Any], now: float, actor: str) -> int:
        """The agent's row; its identity follows (`noc_set_agent_identity`)."""
        names = [n for n in EDITABLE if n in fields]
        cur = await self.conn.execute(
            f"INSERT INTO noc_agent ({', '.join(names)}, created_at, created_by, "  # nosec B608
            f"updated_at, updated_by) VALUES ({', '.join('?' for _ in names)}, ?, ?, ?, ?) "
            "RETURNING id",
            (*(fields[n] for n in names), now, actor, now, actor),
        )
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    async def noc_update_agent(
        self, agent_id: int, fields: Mapping[str, Any], version: int, now: float, actor: str
    ) -> bool:
        """Optimistic: False when ``version`` is no longer the agent's."""
        names = [n for n in EDITABLE if n in fields]
        sets = "".join(f"{n} = ?, " for n in names)
        cur = await self.conn.execute(
            f"UPDATE noc_agent SET {sets}version = version + 1, updated_at = ?, "  # nosec B608
            "updated_by = ? WHERE id = ? AND version = ? RETURNING id",
            (*(fields[n] for n in names), now, actor, agent_id, version),
        )
        return await cur.fetchone() is not None

    async def noc_set_agent_identity(
        self,
        agent_id: int,
        *,
        token_id: int,
        token_sealed: str | None,
        webhook_key_sealed: str | None,
        now: float,
        actor: str,
    ) -> None:
        """A new token (and key) for the agent: rotation, or an adapter that changed."""
        await self.conn.execute(
            "UPDATE noc_agent SET token_id = ?, token_sealed = ?, webhook_key_sealed = ?, "
            "version = version + 1, updated_at = ?, updated_by = ? WHERE id = ?",
            (token_id, token_sealed, webhook_key_sealed, now, actor, agent_id),
        )

    async def noc_set_agent_status(
        self, agent_id: int, status: str, now: float, actor: str, *, reason: str | None = None
    ) -> None:
        """`paused` records why and when; `error` records why; anything else clears both."""
        paused = (reason, now) if status == "paused" else (None, None)
        error = reason if status == "error" else None
        await self.conn.execute(
            "UPDATE noc_agent SET status = ?, pause_reason = ?, paused_at = ?, error_reason = ?, "
            "version = version + 1, updated_at = ?, updated_by = ? WHERE id = ?",
            (status, *paused, error, now, actor, agent_id),
        )

    async def noc_agent_spend(self, agent_id: int, cents: float, month: str) -> float:
        """Add to the month's spend (a new month starts from zero); returns the month's total."""
        cur = await self.conn.execute(
            "UPDATE noc_agent SET spent_cents = CASE WHEN spend_month = ? THEN spent_cents + ? "
            "ELSE ? END, spend_month = ? WHERE id = ? RETURNING spent_cents",
            (month, cents, cents, month, agent_id),
        )
        row = await cur.fetchone()
        return float(row[0]) if row else 0.0

    async def noc_agents_heartbeat_due(self, now: float) -> list[int]:
        """Idle agents whose heartbeat has come."""
        cur = await self.conn.execute(
            "SELECT id FROM noc_agent WHERE heartbeat_s > 0 AND status = 'idle' "
            "AND adapter <> 'external' AND (last_run_at IS NULL "
            "OR last_run_at + make_interval(secs => heartbeat_s) <= to_timestamp(?)) ORDER BY id",
            (now,),
        )
        return [int(r[0]) for r in await cur.fetchall()]

    # -- runs -----------------------------------------------------------------------------------

    async def noc_wake(
        self, agent_id: int, trigger: str, situation_id: int | None, by: str, now: float
    ) -> int:
        """Queue a run — or merge into the one already queued for the agent (Paperclip coalesces
        wake-ups): one queued run per agent, which keeps the first case named."""
        cur = await self.conn.execute(
            "UPDATE noc_agent_run SET situation_id = COALESCE(situation_id, ?) "
            "WHERE agent_id = ? AND status = 'queued' RETURNING id",
            (situation_id, agent_id),
        )
        row = await cur.fetchone()
        if row is not None:
            return int(row[0])
        cur = await self.conn.execute(
            "INSERT INTO noc_agent_run (agent_id, trigger, situation_id, status, queued_at, "
            "requested_by) VALUES (?, ?, ?, 'queued', ?, ?) RETURNING id",
            (agent_id, trigger, situation_id, now, by),
        )
        created = await cur.fetchone()
        assert created is not None
        return int(created[0])

    async def noc_agent_run_external(self, agent_id: int, now: float) -> int:
        """A run for an agent acting over MCP on its own: running from its first call, ended
        after five quiet minutes (`noc_agent_runs_overdue`)."""
        cur = await self.conn.execute(
            "INSERT INTO noc_agent_run (agent_id, trigger, status, queued_at, started_at, "
            "requested_by) VALUES (?, 'external', 'running', ?, ?, 'mcp') RETURNING id",
            (agent_id, now, now),
        )
        row = await cur.fetchone()
        assert row is not None
        await self.conn.execute(
            "UPDATE noc_agent SET status = 'running', last_run_at = ? "
            "WHERE id = ? AND status IN ('idle', 'error')",
            (now, agent_id),
        )
        return int(row[0])

    async def noc_agent_runs_ready(self, limit: int = 20) -> list[dict[str, Any]]:
        """Queued runs whose agent may start one now: not paused, terminated or already running."""
        cur = await self.conn.execute(
            f"SELECT {_RUN} FROM noc_agent_run r JOIN noc_agent a ON a.id = r.agent_id "  # nosec B608
            "WHERE r.status = 'queued' AND a.status IN ('idle', 'error') "
            "AND NOT EXISTS (SELECT 1 FROM noc_agent_run x WHERE x.agent_id = r.agent_id "
            "AND x.status = 'running') ORDER BY r.id LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def noc_agent_run_start(self, run_id: int, now: float) -> None:
        await self.conn.execute(
            "UPDATE noc_agent_run SET status = 'running', started_at = ? WHERE id = ?",
            (now, run_id),
        )
        await self.conn.execute(
            "UPDATE noc_agent SET status = 'running', last_run_at = ? "
            "WHERE id = (SELECT agent_id FROM noc_agent_run WHERE id = ?) "
            "AND status IN ('idle', 'error')",
            (now, run_id),
        )

    async def noc_agent_run_finish(
        self,
        run_id: int,
        status: str,
        now: float,
        *,
        summary: str = "",
        error: str = "",
        usage: tuple[int, int, float] = (0, 0, 0.0),
    ) -> bool:
        """End a queued or running run; the agent is idle again unless a person paused it.
        False when it had already ended."""
        cur = await self.conn.execute(
            "UPDATE noc_agent_run SET status = ?, ended_at = ?, summary = ?, error = ?, "
            "input_tokens = input_tokens + ?, output_tokens = output_tokens + ?, "
            "cost_cents = cost_cents + ? WHERE id = ? AND status IN ('queued', 'running') "
            "RETURNING agent_id",
            (status, now, summary[:8000], error[:2000], *usage, run_id),
        )
        row = await cur.fetchone()
        if row is None:
            return False
        await self.conn.execute(
            "UPDATE noc_agent SET status = 'idle' WHERE id = ? AND status = 'running'",
            (int(row[0]),),
        )
        return True

    async def noc_agent_run(self, run_id: int) -> dict[str, Any] | None:
        cur = await self.conn.execute(
            f"SELECT {_RUN} FROM noc_agent_run r JOIN noc_agent a ON a.id = r.agent_id "  # nosec B608
            "WHERE r.id = ?",
            (run_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def noc_agent_run_active(self, agent_id: int) -> dict[str, Any] | None:
        """The agent's running run, if it has one."""
        cur = await self.conn.execute(
            f"SELECT {_RUN} FROM noc_agent_run r JOIN noc_agent a ON a.id = r.agent_id "  # nosec B608
            "WHERE r.agent_id = ? AND r.status = 'running' ORDER BY r.id DESC LIMIT 1",
            (agent_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def noc_agent_runs_overdue(self, now: float) -> list[dict[str, Any]]:
        """Running runs past their agent's time limit; `external` ones after five quiet minutes."""
        cur = await self.conn.execute(
            f"SELECT {_RUN} FROM noc_agent_run r JOIN noc_agent a ON a.id = r.agent_id "  # nosec B608
            "WHERE r.status = 'running' AND to_timestamp(?) > CASE WHEN r.trigger = 'external' "
            "THEN COALESCE((SELECT max(s.at) FROM noc_agent_run_step s WHERE s.run_id = r.id), "
            "r.started_at) + interval '5 minutes' "
            "ELSE r.started_at + make_interval(secs => a.run_timeout_s) END",
            (now,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def noc_agent_runs(self, agent_id: int, limit: int = 30) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            f"SELECT {_RUN} FROM noc_agent_run r JOIN noc_agent a ON a.id = r.agent_id "  # nosec B608
            "WHERE r.agent_id = ? ORDER BY r.id DESC LIMIT ?",
            (agent_id, limit),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def noc_agent_run_step(
        self, run_id: int, kind: str, name: str, ok: bool, detail: str, now: float
    ) -> None:
        await self.conn.execute(
            "INSERT INTO noc_agent_run_step (run_id, at, kind, name, ok, detail) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, now, kind, name[:120], ok, detail[:4000]),
        )
        if kind == "tool":
            await self.conn.execute(
                "UPDATE noc_agent_run SET tool_calls = tool_calls + 1 WHERE id = ?", (run_id,)
            )

    async def noc_agent_run_steps(self, run_id: int, limit: int = 500) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT id, at, kind, name, ok, detail FROM noc_agent_run_step WHERE run_id = ? "
            "ORDER BY id LIMIT ?",
            (run_id, limit),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def noc_agent_activity(self, agent_id: int, since: float) -> dict[str, Any]:
        """The agent page's charts: runs per day by status, with what they cost and the tokens
        they used (ADR #472), and its cases by priority and status."""
        cur = await self.conn.execute(
            "SELECT to_char(queued_at AT TIME ZONE 'UTC', 'YYYY-MM-DD') AS day, status, "
            "COUNT(*) AS n, COALESCE(SUM(cost_cents), 0) AS cost_cents, "
            "COALESCE(SUM(input_tokens + output_tokens), 0) AS tokens FROM noc_agent_run "
            "WHERE agent_id = ? AND queued_at >= to_timestamp(?) GROUP BY 1, 2 ORDER BY 1",
            (agent_id, since),
        )
        runs = [dict(r) for r in await cur.fetchall()]
        cur = await self.conn.execute(
            "SELECT c.priority, c.status, COUNT(*) AS n FROM noc_case c WHERE c.situation_id IN "
            "(SELECT DISTINCT situation_id FROM noc_agent_run WHERE agent_id = ? "
            "AND situation_id IS NOT NULL AND queued_at >= to_timestamp(?)) OR (c.agent_id = ?) "
            "GROUP BY 1, 2",
            (agent_id, since, agent_id),
        )
        return {"runs": runs, "cases": [dict(r) for r in await cur.fetchall()]}

    # -- the agent's identity -------------------------------------------------------------------

    async def noc_token_name_taken(self, name: str) -> bool:
        cur = await self.conn.execute("SELECT 1 FROM api_token WHERE name = ?", (name,))
        return await cur.fetchone() is not None

    async def noc_retire_token(self, token_id: int, now: float) -> None:
        """Revoke an agent's old token and free its name for the new one (the agent's name is the
        actor its every act is recorded under). The audit rows keep the name they were written
        with."""
        await self.conn.execute(
            "UPDATE api_token SET revoked = true, revoked_at = COALESCE(revoked_at, ?), "
            "name = name || ' · retired #' || id::text "
            "WHERE id = ? AND name NOT LIKE '% · retired #%'",
            (now, token_id),
        )

    async def noc_rename_token(self, token_id: int, name: str) -> None:
        await self.conn.execute("UPDATE api_token SET name = ? WHERE id = ?", (name, token_id))
