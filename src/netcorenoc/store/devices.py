"""Devices, network elements, their level-0 entities, and alarm classes.

Every method here is an insert-or-get keyed by a natural identifier from the trap stream, cached
in-memory so the hot path does not re-query. The caches (``_device_ids``, ``_ne_ids``,
``_entity0_ids``, ``_class_ids``, ``_touched``) are declared on :class:`StoreBase`, assigned in
``Store.__init__``, and read and written only here.
"""

from __future__ import annotations

from netcorenoc import known_oids
from netcorenoc.store.base import StoreBase
from netcorenoc.store.types import TOUCH_INTERVAL_S


class DeviceMixin(StoreBase):
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
