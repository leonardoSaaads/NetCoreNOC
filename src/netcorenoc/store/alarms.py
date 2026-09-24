"""The alarm table: dedup by fingerprint, clear, flap flag, and quarantine.

Inherits :class:`DeviceMixin` because :meth:`ingest` resolves the device, NE, class and level-0
entity before it can write a row. That is one of exactly two sibling-inheritance edges in this
package (DECISIONS #88); the alternative — restating four signatures on :class:`StoreBase` — would
have needed stub bodies, and a stub that resolves instead of the real method is a silent no-op
write.
"""

from __future__ import annotations

import json

from netcorenoc.ingest.events import QuarantinedPacket, TrapEvent
from netcorenoc.store.devices import DeviceMixin
from netcorenoc.store.types import IngestResult


class AlarmMixin(DeviceMixin):
    async def ingest(
        self,
        event: TrapEvent,
        entity_id: int | None = None,
        instance: str | None = None,
        severity: str | None = None,
        severity_rank: int | None = None,
        severity_source: str | None = None,
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
        # v0.21.0: the provenance column exists only from migration `0019`, and the schema probe
        # answers that once at `open()` rather than per trap. On an older schema the severity is
        # still placed and still written — only the column that would say where it came from is
        # absent, which is the state that database was already in.
        source_column = ", severity_source" if self._has_severity_source else ""
        source_value = (severity_source,) if self._has_severity_source else ()
        if existing is None:
            marks = ", ".join("?" * (11 + len(source_value)))
            cur = await self.conn.execute(
                # nosec B608 - `source_column` and `marks` are literals chosen by a schema probe;
                # every value is bound.
                "INSERT INTO alarm (device_id, ne_id, entity_id, class_id, instance, first_seen, "  # nosec B608
                f"last_seen, varbinds, community_tag, severity, severity_rank{source_column}) "
                f"VALUES ({marks}) RETURNING id",
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
                    *source_value,
                ),
            )
            row = await cur.fetchone()
            assert row is not None
            return IngestResult(int(row[0]), device_id, class_id, True, 1, entity_id)
        # A placed severity refreshes the active alarm's current severity; COALESCE keeps the last
        # known value when a later trap omits the field (never silently downgrades to NULL).
        #
        # **The provenance moves with the value, in the same statement** (v0.21.0). Two COALESCEs
        # over one source would let a trap that carries no severity leave the previous value beside
        # a provenance that had been overwritten, and a row claiming `standard` for a value no trap
        # ever stated is exactly the fabrication prime directive 2 forbids. They are written from
        # the same `severity` parameter, so the pair is always one trap's statement.
        source_set = (
            ", severity_source=CASE WHEN ? IS NULL THEN severity_source ELSE ? END "
            if self._has_severity_source
            else " "
        )
        source_args = (severity, severity_source) if self._has_severity_source else ()
        cur = await self.conn.execute(
            # nosec B608 - `source_set` is one of two literals chosen by a schema probe.
            "UPDATE alarm SET count=count+1, last_seen=?, varbinds=?, status='active', "  # nosec B608
            "cleared_at=NULL, severity=COALESCE(?, severity), "
            f"severity_rank=COALESCE(?, severity_rank){source_set}"
            "WHERE id=? RETURNING count",
            (event.ts, varbinds, severity, severity_rank, *source_args, int(existing["id"])),
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
