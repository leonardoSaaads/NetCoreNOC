"""Playbooks, their runs, and what the executor needs from the vault and the broker (ADRs #462,
#463).

Storage only. What a playbook may say is `engine/noc/playbook.py`; how one runs is
`engine/noc/runbook.py`. The one read here that returns a sealed credential is
:meth:`NocPlaybookMixin.noc_secret_for`, for the executor's caller to open in process.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from netcorenoc.store.base import StoreBase

_PLAYBOOK = (
    "id, name, document, version, enabled, source_case, created_at, created_by, updated_at, "
    "updated_by, organization_id, tier, "
    # ADR #492: the customer a routine is for, by name; NULL for every customer.
    "(SELECT o.name FROM organization o WHERE o.id = organization_id) AS customer"
)


class NocPlaybookMixin(StoreBase):
    async def noc_playbooks(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        where = "WHERE enabled " if enabled_only else ""
        cur = await self.conn.execute(
            f"SELECT {_PLAYBOOK}, (SELECT COUNT(*) FROM noc_playbook_run r "  # nosec B608
            "WHERE r.playbook_id = p.id) AS runs, (SELECT MAX(started_at) FROM noc_playbook_run r "
            f"WHERE r.playbook_id = p.id) AS last_run_at FROM noc_playbook p {where}"
            # ADR #492: a customer's own routines before every customer's, then oldest first.
            "ORDER BY organization_id IS NULL, organization_id, id"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def noc_playbook(self, playbook_id: int) -> dict[str, Any] | None:
        cur = await self.conn.execute(
            f"SELECT {_PLAYBOOK} FROM noc_playbook WHERE id = ?",  # nosec B608 - a literal
            (playbook_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def noc_create_playbook(
        self,
        name: str,
        document: str,
        source_case: int | None,
        now: float,
        actor: str,
        *,
        organization_id: int | None = None,
        tier: int = 1,
    ) -> int:
        cur = await self.conn.execute(
            "INSERT INTO noc_playbook (name, document, source_case, created_at, created_by, "
            "updated_at, updated_by, organization_id, tier) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "RETURNING id",
            (name, document, source_case, now, actor, now, actor, organization_id, tier),
        )
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    async def noc_update_playbook(
        self,
        playbook_id: int,
        name: str,
        document: str,
        now: float,
        actor: str,
        *,
        organization_id: int | None = None,
        tier: int = 1,
    ) -> None:
        await self.conn.execute(
            "UPDATE noc_playbook SET name = ?, document = ?, version = version + 1, "
            "updated_at = ?, updated_by = ?, organization_id = ?, tier = ? WHERE id = ?",
            (name, document, now, actor, organization_id, tier, playbook_id),
        )

    async def noc_enable_playbook(
        self, playbook_id: int, enabled: bool, now: float, actor: str
    ) -> None:
        await self.conn.execute(
            "UPDATE noc_playbook SET enabled = ?, updated_at = ?, updated_by = ? WHERE id = ?",
            (enabled, now, actor, playbook_id),
        )

    # ADR #501: each version of a routine, kept whole, with who wrote it and why.

    async def noc_playbook_keep_version(
        self, playbook_id: int, now: float, actor: str, note: str = ""
    ) -> None:
        """Keep the routine as it now stands as its version — in the transaction that wrote it."""
        await self.conn.execute(
            "INSERT INTO noc_playbook_version (playbook_id, version, name, document, "
            "organization_id, tier, written_at, written_by, note) SELECT id, version, name, "
            "document, organization_id, tier, ?, ?, ? FROM noc_playbook WHERE id = ?",
            (now, actor, note, playbook_id),
        )

    async def noc_playbook_versions(
        self, playbook_id: int, limit: int = 30
    ) -> list[dict[str, Any]]:
        """A routine's versions, newest first, each with how its runs ended (``outcomes``)."""
        cur = await self.conn.execute(
            "SELECT version, name, organization_id, tier, written_at, written_by, note "
            "FROM noc_playbook_version WHERE playbook_id = ? ORDER BY version DESC LIMIT ?",
            (playbook_id, limit),
        )
        versions = [dict(r) for r in await cur.fetchall()]
        cur = await self.conn.execute(
            "SELECT version, COALESCE(outcome, 'running') AS outcome, COUNT(*) AS n "
            "FROM noc_playbook_run WHERE playbook_id = ? GROUP BY version, outcome",
            (playbook_id,),
        )
        tally: dict[int, dict[str, int]] = {}
        for row in await cur.fetchall():
            tally.setdefault(int(row["version"]), {})[str(row["outcome"])] = int(row["n"])
        for v in versions:
            v["outcomes"] = tally.get(int(v["version"]), {})
        return versions

    async def noc_playbook_version(self, playbook_id: int, version: int) -> dict[str, Any] | None:
        cur = await self.conn.execute(
            "SELECT playbook_id, version, name, document, organization_id, tier, written_at, "
            "written_by, note FROM noc_playbook_version WHERE playbook_id = ? AND version = ?",
            (playbook_id, version),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def noc_runs(
        self, *, playbook_id: int | None = None, situation_id: int | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT r.id, r.playbook_id, p.name, r.version, r.situation_id, r.started_at, "
            "r.ended_at, r.outcome, r.trace, r.started_by FROM noc_playbook_run r "
            "JOIN noc_playbook p ON p.id = r.playbook_id "
            "WHERE (?::bigint IS NULL OR r.playbook_id = ?) "
            "AND (?::bigint IS NULL OR r.situation_id = ?) ORDER BY r.id DESC LIMIT ?",
            (playbook_id, playbook_id, situation_id, situation_id, limit),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def noc_run_start(
        self, playbook_id: int, version: int, sid: int, now: float, started_by: str | None = None
    ) -> int:
        cur = await self.conn.execute(
            "INSERT INTO noc_playbook_run (playbook_id, version, situation_id, started_at, "
            "started_by) VALUES (?, ?, ?, ?, ?) RETURNING id",
            (playbook_id, version, sid, now, started_by),
        )
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    async def noc_run_end(self, run_id: int, now: float, outcome: str, trace: str) -> None:
        await self.conn.execute(
            "UPDATE noc_playbook_run SET ended_at = ?, outcome = ?, trace = ? WHERE id = ?",
            (now, outcome, trace, run_id),
        )

    async def noc_runs_abandoned(self, now: float) -> list[int]:
        """Close every run a restart interrupted; returns their cases still in `auto`."""
        cur = await self.conn.execute(
            "UPDATE noc_playbook_run SET ended_at = ?, outcome = 'error' WHERE ended_at IS NULL "
            "RETURNING situation_id",
            (now,),
        )
        sids = sorted({int(r[0]) for r in await cur.fetchall()})
        if not sids:
            return []
        cur = await self.conn.execute(
            "SELECT situation_id FROM noc_case WHERE status = 'auto' AND situation_id = ANY(?) "
            "ORDER BY situation_id",
            (sids,),
        )
        return [int(r[0]) for r in await cur.fetchall()]

    async def noc_situation_facts(self, situation_id: int) -> dict[str, Any]:
        """What a playbook matches on and fills in: the members' vendors and trap OIDs, the
        severity, and the root alarm's element address, vendor and entity key."""
        cur = await self.conn.execute(
            "SELECT DISTINCT n.vendor, c.oid FROM situation_alarm sa "
            "JOIN alarm a ON a.id = sa.alarm_id JOIN ne n ON n.id = a.ne_id "
            "JOIN alarm_class c ON c.id = a.class_id WHERE sa.situation_id = ? "
            "ORDER BY n.vendor, c.oid",
            (situation_id,),
        )
        rows = [dict(r) for r in await cur.fetchall()]
        cur = await self.conn.execute(
            "SELECT s.severity, host(n.ip) AS ip, n.vendor, e.key AS entity, "
            # ADR #492: the customer the root element is in, which a routine for one matches on.
            "COALESCE(n.organization_id, (SELECT id FROM organization WHERE is_default "
            "ORDER BY id LIMIT 1)) AS organization_id FROM situation s "
            "LEFT JOIN alarm a ON a.id = COALESCE(s.root_alarm_id, (SELECT MIN(alarm_id) "
            "FROM situation_alarm WHERE situation_id = s.id)) LEFT JOIN ne n ON n.id = a.ne_id "
            "LEFT JOIN entity e ON e.id = a.entity_id WHERE s.id = ?",
            (situation_id,),
        )
        head = await cur.fetchone()
        return {
            "vendors": sorted({str(r["vendor"]) for r in rows if r["vendor"]}),
            "trap_oids": sorted({str(r["oid"]) for r in rows}),
            "severity": head["severity"] if head else None,
            "ip": head["ip"] if head else None,
            "vendor": head["vendor"] if head else None,
            "entity": head["entity"] if head else None,
            "organization_id": head["organization_id"] if head else None,
        }

    async def noc_secret_for(
        self, host: str, kinds: Sequence[str], least: str = "view_only"
    ) -> dict[str, Any] | None:
        """The credential agents may use for ``host``, of one of ``kinds``, granting at least
        ``least`` — sealed, for the executor's caller to open in process. The **least** access
        level that suffices comes first: a read-only command is never sent with root."""
        rank = (
            "CASE access_level WHEN 'view_only' THEN 0 WHEN 'diagnostic' THEN 1 "
            "WHEN 'operator' THEN 2 ELSE 3 END"
        )
        floor = {"view_only": 0, "diagnostic": 1, "operator": 2, "root": 3}[least]
        cur = await self.conn.execute(
            "SELECT id, ref, name, kind, access_level, host, port, protocol_version, version, "
            "sealed, host_key FROM noc_secret WHERE host = ? AND agent_access AND kind = ANY(?) "
            f"AND {rank} >= ? ORDER BY {rank}, id LIMIT 1",  # nosec B608 - a literal
            (host, sorted(kinds), floor),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def noc_set_host_key(self, secret_id: int, key: str | None, now: float) -> None:
        """Pin (or, with ``None``, forget) the SSH host key a credential's device presented."""
        await self.conn.execute(
            "UPDATE noc_secret SET host_key = ?, host_key_at = ? WHERE id = ?",
            (key, now if key else None, secret_id),
        )
