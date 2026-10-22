"""The append-only, hash-chained audit log.

``prune_audit`` is the one sanctioned deleter (DECISIONS v0.2 #3): the schema's
`prune_audit_log` function (0032, ADR #453), which runs as the schema's owner, sets the
transaction-local `netcorenoc.prune` the `forbid_change()` trigger honours, deletes only rows older
than its cutoff — never less than a day old — and clears it again, so the surviving suffix stays
verifiable and no other statement can delete a row.

Each row carries ``mac`` since ADR #453, an HMAC of its ``entry_hash`` under the appliance's secret
key, and every append takes a transaction-scoped advisory lock first: the CLI is another process
appending to the same chain, which `store.lock` does not reach.
"""

from __future__ import annotations

import json
from typing import Any

from netcorenoc.store.base import StoreBase

#: The advisory lock every audit append takes: "ncaudit" as a 64-bit key.
AUDIT_LOCK_KEY = int.from_bytes(b"ncaudit", "big")


class AuditLogMixin(StoreBase):
    async def audit_serialize(self) -> None:
        """Hold the chain's tail until this transaction ends, across processes."""
        await self.conn.execute("SELECT pg_advisory_xact_lock(?)", (AUDIT_LOCK_KEY,))

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

    async def audit_insert(
        self, entry: dict[str, Any], prev_hash: str, entry_hash: str, mac: str | None = None
    ) -> None:
        await self.conn.execute(
            "INSERT INTO audit_log (id, ts, actor, role, source_ip, action, object_type, "
            "object_id, outcome, details, prev_hash, entry_hash, mac) "
            "VALUES (:id, :ts, :actor, :role, :source_ip, :action, :object_type, :object_id, "
            ":outcome, :details, :prev_hash, :entry_hash, :mac)",
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
                "mac": mac,
            },
        )

    async def audit_all(self) -> list[dict[str, Any]]:
        """Every audit row in id (chain) order — for verify and export."""
        cur = await self.conn.execute(
            "SELECT id, ts, actor, role, source_ip, action, object_type, object_id, outcome, "
            "details, prev_hash, entry_hash, mac FROM audit_log ORDER BY id"
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
        """Delete audit rows older than cutoff. The one sanctioned deleter (DECISIONS v0.2 #3),
        and since ADR #453 a function, `prune_audit_log`, that runs as the schema's owner: the
        runtime role may hold no DELETE on `audit_log` at all, and the function refuses a cutoff
        less than a day old. Pruning only the oldest rows keeps the surviving suffix verifiable;
        it runs in the caller's locked transaction."""
        cur = await self.conn.execute("SELECT prune_audit_log(?)", (cutoff,))
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])
