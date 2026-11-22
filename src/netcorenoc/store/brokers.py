"""Brokers: the networks this appliance monitors, and how their traps reach it (ADR #494).

A broker is one monitored network owned by one customer (`migrations/0047_brokers.sql`). Its rows
are written by admins only (the routes are `brokers.write`, admin), so every input of the
visibility selector ``broker:<id>`` stays admin- or engine-written (F35). A proxy's token is kept
as its SHA-256 alone, compared in constant time by the caller, and shown to the admin once.
"""

from __future__ import annotations

from typing import Any

from netcorenoc.store.base import StoreBase

__all__ = ["BrokerMixin"]

_FIELDS = (
    "b.id, b.name, b.slug, b.organization_id, o.name AS customer, "
    "o.is_default AS customer_default, "
    "b.description, b.intake, b.udp_port, b.networks, b.proxy_token_hint, b.proxy_seq, "
    "b.proxy_seen_at, b.proxy_address, b.proxy_version, b.is_default, b.enabled, b.created_at, "
    "b.created_by, b.updated_at, b.updated_by"
)


#: What a broker holds and when it last heard from its network: the list's and the page's figures.
_COUNTS = (
    ", (SELECT COUNT(*) FROM ne n WHERE n.broker_id = b.id) AS elements, "
    "(SELECT COUNT(*) FROM situation s WHERE s.broker_id = b.id "
    "AND s.status IN ('new', 'pending', 'open') AND s.merged_into IS NULL) AS live, "
    "(SELECT MAX(n.last_seen) FROM ne n WHERE n.broker_id = b.id) AS last_trap"
)


class BrokerMixin(StoreBase):
    async def brokers(self, *, counts: bool = False) -> list[dict[str, Any]]:
        """Every broker, the Default first and then by name — the order the sidebar keeps."""
        extra = _COUNTS if counts else ""
        cur = await self.conn.execute(
            f"SELECT {_FIELDS}{extra} FROM broker b "  # nosec B608 - literal fragments
            "JOIN organization o ON o.id = b.organization_id "
            "ORDER BY b.is_default DESC, b.name, b.id"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def broker(self, broker_id: int) -> dict[str, Any] | None:
        """One broker, with what it holds (`_COUNTS`)."""
        cur = await self.conn.execute(
            f"SELECT {_FIELDS}{_COUNTS} FROM broker b "  # nosec B608 - literal fragments
            "JOIN organization o ON o.id = b.organization_id WHERE b.id = ?",
            (broker_id,),
        )
        row = await cur.fetchone()
        return None if row is None else dict(row)

    async def broker_routes(self) -> list[dict[str, Any]]:
        """What the routing snapshot is built from (`engine/operate/routing.build`)."""
        cur = await self.conn.execute(
            "SELECT id, name, intake, udp_port, networks, enabled FROM broker ORDER BY id"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def create_broker(
        self,
        *,
        name: str,
        slug: str,
        organization_id: int,
        description: str,
        intake: str,
        udp_port: int | None,
        networks: str,
        token_hash: str | None,
        token_hint: str | None,
        actor: str,
        now: float,
    ) -> int:
        cur = await self.conn.execute(
            "INSERT INTO broker (name, slug, organization_id, description, intake, udp_port, "
            "networks, proxy_token_hash, proxy_token_hint, created_at, created_by, updated_at, "
            "updated_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (
                name,
                slug,
                organization_id,
                description,
                intake,
                udp_port,
                networks,
                token_hash,
                token_hint,
                now,
                actor,
                now,
                actor,
            ),
        )
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    async def update_broker(
        self,
        broker_id: int,
        *,
        name: str,
        organization_id: int,
        description: str,
        udp_port: int | None,
        networks: str,
        enabled: bool,
        actor: str,
        now: float,
    ) -> None:
        """Change what an admin may change. The intake is fixed when the broker is created: a
        network that moves from a port to a proxy is a new broker, so its elements' identity —
        (broker, address) — never changes under them."""
        cur = await self.conn.execute(
            "SELECT organization_id, is_default FROM broker WHERE id=?", (broker_id,)
        )
        before = await cur.fetchone()
        await self.conn.execute(
            "UPDATE broker SET name=?, organization_id=?, description=?, udp_port=?, networks=?, "
            "enabled=?, updated_at=?, updated_by=? WHERE id=?",
            (
                name,
                organization_id,
                description,
                udp_port,
                networks,
                enabled,
                now,
                actor,
                broker_id,
            ),
        )
        if before is None or before[1] or int(before[0]) == organization_id:
            return
        # A broker's elements belong to its customer: when the customer changes, theirs does.
        # Never the Default broker's, which holds every customer's unclaimed elements, and never
        # on an edit that leaves the customer as it was — an element an admin put in another
        # organization by hand keeps it.
        await self.conn.execute(
            "UPDATE ne SET organization_id = (SELECT CASE WHEN o.is_default THEN NULL "
            "ELSE o.id END FROM organization o WHERE o.id = ?) WHERE broker_id = ?",
            (organization_id, broker_id),
        )

    async def set_broker_token(
        self, broker_id: int, token_hash: str, hint: str, actor: str, now: float
    ) -> None:
        """Replace a proxy broker's token: the old one stops working at once."""
        await self.conn.execute(
            "UPDATE broker SET proxy_token_hash=?, proxy_token_hint=?, updated_at=?, updated_by=? "
            "WHERE id=? AND intake='proxy'",
            (token_hash, hint, now, actor, broker_id),
        )

    async def broker_token_hashes(self) -> list[tuple[int, str, bool]]:
        """(broker, token hash, enabled) for every proxy broker — compared in constant time by the
        caller, never matched in SQL, so a timing difference never tells which hash came close."""
        cur = await self.conn.execute(
            "SELECT id, proxy_token_hash, enabled FROM broker WHERE intake='proxy' ORDER BY id"
        )
        return [(int(r[0]), str(r[1]), bool(r[2])) for r in await cur.fetchall()]

    async def proxy_seq(self, broker_id: int, stream: str) -> int:
        """The last record of this stream a proxy broker's batches reached; 0 before the first,
        and 0 for a stream it has not sent before (a reinstalled proxy numbers from 1 again)."""
        cur = await self.conn.execute(
            "SELECT proxy_seq, proxy_stream FROM broker WHERE id=?", (broker_id,)
        )
        row = await cur.fetchone()
        return 0 if row is None or row[1] != stream else int(row[0])

    async def proxy_accept(
        self,
        broker_id: int,
        first: int,
        last: int,
        now: float,
        address: str,
        version: str,
        stream: str = "",
    ) -> int:
        """Record a proxy's batch ``first..last`` and return how many of its records are **new**.

        A proxy numbers every record it forwards. It sends a batch again when our answer did not
        reach it (its link dropped), so the records at or below `proxy_seq` were already spooled
        and are skipped; ``0`` means the whole batch was a repeat.
        """
        cur = await self.conn.execute(
            "SELECT proxy_seq, proxy_stream FROM broker WHERE id=? AND intake='proxy' FOR UPDATE",
            (broker_id,),
        )
        row = await cur.fetchone()
        if row is None:
            return 0
        same = row[1] == stream
        seen = int(row[0]) if same else 0
        fresh = max(0, last - max(seen, first - 1))
        await self.conn.execute(
            "UPDATE broker SET proxy_seq=?, proxy_stream=?, proxy_seen_at=?, proxy_address=?, "
            "proxy_version=? WHERE id=?",
            (max(seen, last), stream[:64], now, address[:64], version[:40], broker_id),
        )
        return fresh

    async def delete_broker(self, broker_id: int) -> bool:
        """Remove a broker that never received anything: no element, no situation, no window. A
        broker with history is switched off instead, so its map and its situations stay readable."""
        cur = await self.conn.execute(
            "DELETE FROM broker WHERE id=? AND NOT is_default "
            "AND NOT EXISTS (SELECT 1 FROM ne WHERE broker_id=?) "
            "AND NOT EXISTS (SELECT 1 FROM situation WHERE broker_id=?) "
            "AND NOT EXISTS (SELECT 1 FROM maintenance_window WHERE broker_id=?) RETURNING id",
            (broker_id, broker_id, broker_id, broker_id),
        )
        if await cur.fetchone() is None:
            return False
        for table in ("decider_setting", "league_decision"):
            sql = f"DELETE FROM {table} WHERE broker_id=?"  # nosec B608 - a literal table name
            await self.conn.execute(sql, (broker_id,))
        await self.conn.execute("DELETE FROM edge WHERE broker_id=?", (broker_id,))
        await self.conn.execute("DELETE FROM episode_pair WHERE broker_id=?", (broker_id,))
        return True

    async def ne_brokers(self, ne_ids: list[int]) -> dict[int, int]:
        """``{element: its broker}`` for these elements — one query for a whole target list."""
        if not ne_ids:
            return {}
        cur = await self.conn.execute(
            "SELECT id, broker_id FROM ne WHERE id = ANY(?::bigint[])", (list(ne_ids),)
        )
        return {int(r[0]): int(r[1]) for r in await cur.fetchall()}

    async def move_element(self, ne_id: int, broker_id: int) -> str | None:
        """Move an element to another broker; ``None``, or why it cannot be moved.

        Its history moves with it (alarms belong to the element). Refused when the target broker
        already has an element at the same address: identity is (broker, address).
        """
        cur = await self.conn.execute("SELECT ip, broker_id FROM ne WHERE id=?", (ne_id,))
        row = await cur.fetchone()
        if row is None:
            return "no such element"
        if int(row[1]) == broker_id:
            return None
        clash = await self.conn.execute(
            "SELECT 1 FROM ne WHERE broker_id=? AND ip=?", (broker_id, row[0])
        )
        if await clash.fetchone() is not None:
            return "that broker already has an element at this address"
        await self.conn.execute(
            "UPDATE ne SET broker_id=?, organization_id=(SELECT CASE WHEN o.is_default THEN NULL "
            "ELSE o.id END FROM broker b JOIN organization o ON o.id = b.organization_id "
            "WHERE b.id = ?) WHERE id=?",
            (broker_id, broker_id, ne_id),
        )
        self._ne_ids = {k: v for k, v in self._ne_ids.items() if v != ne_id}
        return None
