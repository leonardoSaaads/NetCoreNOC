"""The SQL behind an alarm's occurrences: when a repeat is a new one, when one ends (v0.28.0).

`engine/operate/occurrence.py` decides; this module only reads and writes. Three questions:

    sole_active_member(sid, alarm)   is this alarm the only thing still burning in its situation?
    end_occurrences(oids, before)    which occurrence-only notifications have gone quiet? (ADR #433)
    member_fingerprints(sids)        the (device, OID, instance) of each member, for the flap memory

**No method here takes ``store.lock``.** The engine calls them inside its batch or its maintenance
sweep, both of which already hold it (`tests/test_store_concurrency.py` walks the MRO).
"""

from __future__ import annotations

from collections.abc import Iterable

from netcorenoc.store.base import StoreBase

__all__ = ["OccurrenceMixin"]


class OccurrenceMixin(StoreBase):
    """Three statements and no state."""

    async def sole_active_member(self, situation_id: int, alarm_id: int) -> bool:
        """True when `alarm_id` is a member of `situation_id` and **no other member is active**.

        The guard that keeps ADR #431 from repeating v0.16.2's defect (DECISIONS #274): a situation
        may only be concluded while one of its alarms is on if that alarm is the one starting a new
        occurrence elsewhere. A second member still burning keeps the situation live.
        """
        cur = await self.conn.execute(
            "SELECT COUNT(*) FROM situation_alarm sa JOIN alarm a ON a.id=sa.alarm_id "
            "WHERE sa.situation_id=? AND a.status='active' AND a.id<>?",
            (situation_id, alarm_id),
        )
        row = await cur.fetchone()
        assert row is not None
        if int(row[0]):
            return False
        cur = await self.conn.execute(
            "SELECT 1 FROM situation_alarm WHERE situation_id=? AND alarm_id=?",
            (situation_id, alarm_id),
        )
        return await cur.fetchone() is not None

    async def end_occurrences(self, oids: Iterable[str], before: float, ts: float) -> list[int]:
        """Clear every **active** alarm of an occurrence-only class silent since `before`.

        `known_oids.OCCURRENCE_NOTIFICATIONS` names the classes — a cold start, an authentication
        failure, a UPS on battery that stops repeating — whose standard defines no clear. Without
        this they stayed active for ever, and v0.16.2's rule (an active alarm keeps its situation
        live) left every reboot a `new` situation nobody could age out. Returns the cleared ids so
        the engine can drop them from the correlator's window, as a received clear does.
        """
        wanted = sorted(set(oids))
        if not wanted:
            return []
        marks = ", ".join("?" * len(wanted))
        cur = await self.conn.execute(
            "UPDATE alarm SET status='cleared', cleared_at=? "  # nosec B608 - marks are literals
            f"WHERE status='active' AND last_seen<=? AND class_id IN "
            f"(SELECT id FROM alarm_class WHERE oid IN ({marks})) RETURNING id",
            (ts, before, *wanted),
        )
        return [int(row[0]) for row in await cur.fetchall()]

    async def member_fingerprints(
        self, situation_ids: Iterable[int]
    ) -> dict[int, list[tuple[str, str, str]]]:
        """`{situation: [(device address, trap OID, instance), ...]}` — the flap detector's key."""
        sids = sorted(set(situation_ids))
        if not sids:
            return {}
        marks = ", ".join("?" * len(sids))
        cur = await self.conn.execute(
            "SELECT sa.situation_id, d.ip, c.oid, a.instance FROM situation_alarm sa "  # nosec B608
            "JOIN alarm a ON a.id=sa.alarm_id JOIN device d ON d.id=a.device_id "
            f"JOIN alarm_class c ON c.id=a.class_id WHERE sa.situation_id IN ({marks})",
            tuple(sids),
        )
        out: dict[int, list[tuple[str, str, str]]] = {}
        for row in await cur.fetchall():
            out.setdefault(int(row[0]), []).append((str(row[1]), str(row[2]), str(row[3])))
        return out
