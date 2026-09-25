"""Persisting the state ledger, and surfacing what outlived a window (II.2).

`engine/mw/ledger.py` holds the in-memory ledger and explains at length what it refuses to record.
This module is the two things that need the database: flushing it, and — when a window closes —
turning every raise that never cleared into an alarm.

⚠ **Nothing here writes a payload.** The ledger carries six scalars per fingerprint and no
varbinds, because storing them would be collecting the trap the operator said not to collect. The
alarm this module surfaces therefore says *"raised during maintenance, still active"* and says
**nothing about how serious it is** — `severity` is NULL and `severity_source` is NULL, because the
appliance genuinely does not know. Inventing one would be the fabrication prime directive 2 forbids,
and an operator reading an unplaced alarm with that marker on it is being told the truth.
"""

from __future__ import annotations

from typing import Any

from netcorenoc.store.devices import DeviceMixin

#: One ledger row as this layer speaks it: `(window_id, ne_id, device_id, class_id, instance,
#: raised_at, cleared_at, surfaced_at)`.
#:
#: **Scalars, not the engine's `LedgerKey`/`LedgerEntry`** — `store` is the data layer and
#: `engine` is above it, so importing those types here would be an upward import and
#: `tests/test_layers.py` refuses one. The engine converts at its own boundary, which is also
#: where the meaning of the six scalars is documented (`engine/mw/ledger.py`).
LedgerRow = tuple[int, int, int, int, str, float | None, float | None, float | None]


class MaintenanceLedgerMixin(DeviceMixin):
    """Inherits :class:`DeviceMixin` because :meth:`surface_ledger_entry` resolves the NE's
    level-0 entity before it can write an alarm row.

    The **third** sibling-inheritance edge in this package, and it is taken for exactly the reason
    `AlarmMixin(DeviceMixin)` was (DECISIONS #88): restating `entity_level0` on `StoreBase` would
    need a stub body, and a stub that resolves instead of the real method is a silent no-op write —
    here, an alarm attributed to no entity at all.
    """

    async def flush_ledger(self, entries: list[LedgerRow]) -> None:
        """Write the entries the sweep took as dirty. Upsert on the fingerprint.

        `raised_at` uses `COALESCE(excluded.raised_at, maintenance_ledger.raised_at)` so a clear
        arriving after a raise cannot erase when the fault started — the ledger's whole value is
        the pair, and the raise is the half that cannot be re-observed.
        """
        for row in entries:
            await self.conn.execute(
                "INSERT INTO maintenance_ledger (window_id, ne_id, device_id, class_id, instance, "
                "raised_at, cleared_at, surfaced_at) VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT (window_id, device_id, class_id, instance) DO UPDATE SET "
                "raised_at=COALESCE(excluded.raised_at, maintenance_ledger.raised_at), "
                "cleared_at=excluded.cleared_at, "
                "surfaced_at=COALESCE(excluded.surfaced_at, maintenance_ledger.surfaced_at)",
                row,
            )

    async def surface_ledger_entry(self, row: LedgerRow, now: float) -> int | None:
        """Turn one unresolved raise into an alarm. **The fault that outlived the window.**

        Returns the alarm id, or None when the fingerprint already has an active alarm — which
        happens when the element re-sent the trap after the window closed, and is the good case:
        the real trap carries its varbinds and its severity, and this must not overwrite it with a
        row that has neither.

        The alarm lands on the same `(device_id, class_id, instance)` the collected trap would have
        used, so a later real trap bumps this row rather than opening a second one beside it.

        `first_seen` is the instant the fault was **raised**, not the instant the window closed.
        An operator triaging at 12:00 needs to know it started at 11:40, and recording the
        surfacing time would make every such alarm look twenty minutes younger than it is.
        """
        window_id, ne_id, device_id, class_id, instance, raised_at, _cleared, _surfaced = row
        cur = await self.conn.execute(
            "SELECT id, status FROM alarm WHERE device_id=? AND class_id=? AND instance=?",
            (device_id, class_id, instance),
        )
        existing = await cur.fetchone()
        if existing is not None and str(existing["status"]) == "active":
            return None
        raised = raised_at if raised_at is not None else now
        # v0.22.0 (#388): the instant of surfacing and a fresh acknowledgement, so the marker's
        # lifetime can be read — active, not seen since, not acknowledged.
        stamp = (
            ", surfaced_at=?, surfaced_ack_at=NULL, surfaced_ack_by=NULL"
            if self._has_surfaced_ack
            else ""
        )
        stamp_args = (now,) if self._has_surfaced_ack else ()
        if existing is not None:
            await self.conn.execute(
                "UPDATE alarm SET status='active', cleared_at=NULL, last_seen=?, "  # nosec B608
                f"surfaced_from_window_id=?{stamp} WHERE id=?",
                (now, window_id, *stamp_args, int(existing["id"])),
            )
            return int(existing["id"])
        entity_id = await self.entity_level0_by_id(device_id, ne_id, raised)
        cur = await self.conn.execute(
            # `varbinds` is `'[]'` and both severity columns are NULL, deliberately — see the
            # module docstring. The appliance saw a raise and nothing else, and says so.
            "INSERT INTO alarm (device_id, ne_id, entity_id, class_id, instance, first_seen, "
            "last_seen, varbinds, surfaced_from_window_id"  # nosec B608 - probe-chosen literal
            + (
                ", surfaced_at) VALUES (?,?,?,?,?,?,?,'[]',?,?) "
                if self._has_surfaced_ack
                else ") VALUES (?,?,?,?,?,?,?,'[]',?) "
            )
            + "RETURNING id",
            (device_id, ne_id, entity_id, class_id, instance, raised, now, window_id, *stamp_args),
        )
        created = await cur.fetchone()
        return int(created[0]) if created is not None else None

    async def entity_level0_by_id(self, device_id: int, ne_id: int, ts: float) -> int | None:
        """The NE's own level-0 entity, looked up by id rather than by address.

        `entity_level0` takes the address because every other caller has a `TrapEvent` in hand.
        The ledger has neither — it holds ids, because holding the address would be holding part
        of a trap it did not collect — so this resolves the address from `device_id` first. One
        query per surfaced alarm, on a sweep that runs when a window ends.
        """
        cur = await self.conn.execute("SELECT ip FROM device WHERE id=?", (device_id,))
        row = await cur.fetchone()
        if row is None:
            return None
        return int(await self.entity_level0(ne_id, str(row["ip"]), ts))

    async def mark_ledger_surfaced(self, row: LedgerRow, now: float) -> None:
        window_id, _ne, device_id, class_id, instance = row[0], row[1], row[2], row[3], row[4]
        await self.conn.execute(
            "UPDATE maintenance_ledger SET surfaced_at=? WHERE window_id=? AND device_id=? "
            "AND class_id=? AND instance=?",
            (now, window_id, device_id, class_id, instance),
        )

    async def unresolved_ledger(self, window_id: int) -> list[LedgerRow]:
        """Raised inside the window, never cleared inside it, never yet surfaced.

        Read from the table rather than from memory, so a window that spans a restart still
        surfaces what it saw. The sweep reconciles the two: the in-memory ledger is flushed first,
        then this is read, so a raise observed in the same tick the window ended is included.
        """
        cur = await self.conn.execute(
            "SELECT ne_id, device_id, class_id, instance, raised_at, cleared_at, surfaced_at "
            "FROM maintenance_ledger WHERE window_id=? AND raised_at IS NOT NULL "
            "AND cleared_at IS NULL AND surfaced_at IS NULL ORDER BY device_id, class_id, instance",
            (window_id,),
        )
        return [
            (
                window_id,
                int(row["ne_id"]),
                int(row["device_id"]),
                int(row["class_id"]),
                str(row["instance"]),
                row["raised_at"],
                row["cleared_at"],
                row["surfaced_at"],
            )
            for row in await cur.fetchall()
        ]

    async def ledger_summary(self, window_id: int) -> dict[str, Any]:
        """Counts only, for the window's detail card. **Admin-only, and it names nothing.**

        Three integers: how many fingerprints the window suppressed a raise for, how many of those
        cleared inside it, and how many surfaced when it closed. No device, no class, no instance —
        the ledger is not a view of the network and this is not a way to make it one.
        """
        cur = await self.conn.execute(
            "SELECT COUNT(*) AS seen, "
            "SUM(CASE WHEN cleared_at IS NOT NULL THEN 1 ELSE 0 END) AS cleared, "
            "SUM(CASE WHEN surfaced_at IS NOT NULL THEN 1 ELSE 0 END) AS surfaced "
            "FROM maintenance_ledger WHERE window_id=?",
            (window_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return {"seen": 0, "cleared": 0, "surfaced": 0}
        return {
            "seen": int(row["seen"] or 0),
            "cleared": int(row["cleared"] or 0),
            "surfaced": int(row["surfaced"] or 0),
        }
