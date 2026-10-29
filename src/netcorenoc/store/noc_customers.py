"""The NOC's customers: their instructions, their memory and their workbook (ADR #473).

Storage only; what may be written is `api/routes/customers.py`'s, what a circuit or a document is
`engine/noc/circuits.py` and `engine/noc/doctext.py`'s. A customer is an organization other than
the default one, and **who may see it is decided by its elements**: a caller whose scope reaches
none of a customer's elements is answered as if it did not exist (:data:`VISIBLE`). That narrows,
and never widens: the organization column decides which customer's book a scoped caller reads,
never which element (ADR #473 records why this is not the isolation ADR #366 refuses).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from netcorenoc.store.base import StoreBase

#: A customer the caller's elements reach; the caller binds its visible element ids.
VISIBLE = "EXISTS (SELECT 1 FROM ne WHERE ne.organization_id = o.id AND ne.id = ANY(?))"
#: Its open cases: one of its elements is a member of a case not yet resolved.
_OPEN_CASES = (
    "(SELECT COUNT(DISTINCT c.situation_id) FROM noc_case c "
    "JOIN situation_alarm sa ON sa.situation_id = c.situation_id "
    "JOIN alarm a ON a.id = sa.alarm_id JOIN ne n ON n.id = a.ne_id "
    "WHERE n.organization_id = o.id AND c.status NOT IN ('resolved', 'closed'))"
)
_CONTACT = (
    "id, organization_id, party, party_kind, escalation_level, name, role, email, phone, hours, "
    "notes, version, created_at, created_by, updated_at, updated_by"
)
CONTACT_FIELDS = ("party", "party_kind", "escalation_level", "name", "role", "email", "phone",
                  "hours", "notes")  # fmt: skip


class NocCustomerMixin(StoreBase):
    # -- customers -----------------------------------------------------------------------------

    async def noc_customers(self, ne_ids: frozenset[int] | None = None) -> list[dict[str, Any]]:
        """Every customer the caller may see, with what its book holds."""
        where, args = (f" AND {VISIBLE}", (sorted(ne_ids),)) if ne_ids is not None else ("", ())
        cur = await self.conn.execute(
            "SELECT o.id, o.name, o.slug, "  # nosec B608 - fixed fragments, values bound
            "(SELECT COUNT(*) FROM ne WHERE ne.organization_id = o.id) AS elements, "
            "(SELECT COUNT(*) FROM noc_contact x WHERE x.organization_id = o.id) AS contacts, "
            "(SELECT COUNT(*) FROM noc_customer_doc d WHERE d.organization_id = o.id) AS docs, "
            "(SELECT COUNT(*) FROM noc_circuit k WHERE k.organization_id = o.id) AS circuits, "
            f"{_OPEN_CASES} AS open_cases, "
            "COALESCE(length(m.instructions), 0) AS instructions_chars, "
            "COALESCE(length(m.memory), 0) AS memory_chars, m.updated_at, m.memory_at "
            "FROM organization o LEFT JOIN noc_customer m ON m.organization_id = o.id "
            f"WHERE NOT o.is_default{where} ORDER BY o.name, o.id",
            args,
        )
        return [dict(r) for r in await cur.fetchall()]

    async def noc_customer(self, organization_id: int) -> dict[str, Any] | None:
        """One customer and its book's own fields, or None when it is not a customer."""
        cur = await self.conn.execute(
            "SELECT o.id, o.name, o.slug, "  # nosec B608 - fixed fragments, values bound
            "(SELECT COUNT(*) FROM ne WHERE ne.organization_id = o.id) AS elements, "
            "(SELECT COUNT(*) FROM noc_circuit k WHERE k.organization_id = o.id) AS circuits, "
            f"{_OPEN_CASES} AS open_cases, "
            "COALESCE(m.instructions, '') AS instructions, COALESCE(m.memory, '') AS memory, "
            "COALESCE(m.version, 0) AS version, m.updated_at, m.updated_by, m.memory_at, "
            "m.memory_by FROM organization o "
            "LEFT JOIN noc_customer m ON m.organization_id = o.id "
            "WHERE o.id = ? AND NOT o.is_default",
            (organization_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def noc_customer_visible(
        self, organization_id: int, ne_ids: frozenset[int] | None
    ) -> bool:
        if ne_ids is None:
            return True
        cur = await self.conn.execute(
            f"SELECT 1 FROM organization o WHERE o.id = ? AND {VISIBLE}",  # nosec B608
            (organization_id, sorted(ne_ids)),
        )
        return await cur.fetchone() is not None

    async def noc_customer_elements(
        self, organization_id: int, limit: int = 200
    ) -> list[dict[str, Any]]:
        """The customer's elements, by address: what its circuits and cases run over."""
        cur = await self.conn.execute(
            "SELECT n.id, host(n.ip) AS ip, n.vendor, n.last_seen, l.label FROM ne n "
            "LEFT JOIN label l ON l.kind = 'ne' AND l.target_id = n.id AND l.qualifier = '' "
            "WHERE n.organization_id = ? ORDER BY n.ip LIMIT ?",
            (organization_id, limit),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def noc_set_instructions(
        self, organization_id: int, instructions: str, now: float, actor: str
    ) -> None:
        await self.conn.execute(
            "INSERT INTO noc_customer (organization_id, instructions, updated_at, updated_by) "
            "VALUES (?, ?, ?, ?) ON CONFLICT (organization_id) DO UPDATE SET "
            "instructions = EXCLUDED.instructions, version = noc_customer.version + 1, "
            "updated_at = EXCLUDED.updated_at, updated_by = EXCLUDED.updated_by",
            (organization_id, instructions, now, actor),
        )

    async def noc_set_memory(
        self, organization_id: int, memory: str, now: float, actor: str
    ) -> None:
        await self.conn.execute(
            "INSERT INTO noc_customer (organization_id, memory, updated_at, updated_by, memory_at, "
            "memory_by) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (organization_id) DO UPDATE SET "
            "memory = EXCLUDED.memory, version = noc_customer.version + 1, "
            "memory_at = EXCLUDED.memory_at, memory_by = EXCLUDED.memory_by",
            (organization_id, memory, now, actor, now, actor),
        )

    # -- the workbook --------------------------------------------------------------------------

    async def noc_contacts(self, organization_id: int) -> list[dict[str, Any]]:
        """Its contacts, company by company, each company's in the order they are called."""
        cur = await self.conn.execute(
            f"SELECT {_CONTACT} FROM noc_contact WHERE organization_id = ? "  # nosec B608
            "ORDER BY CASE party_kind WHEN 'customer' THEN 0 WHEN 'provider' THEN 1 "
            "WHEN 'vendor' THEN 2 ELSE 3 END, party, escalation_level, name, id",
            (organization_id,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def noc_contact(self, contact_id: int) -> dict[str, Any] | None:
        cur = await self.conn.execute(
            f"SELECT {_CONTACT} FROM noc_contact WHERE id = ?",  # nosec B608 - a literal
            (contact_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def noc_create_contact(
        self, organization_id: int, fields: Mapping[str, Any], now: float, actor: str
    ) -> int:
        values = tuple(fields[name] for name in CONTACT_FIELDS)
        cur = await self.conn.execute(
            f"INSERT INTO noc_contact (organization_id, {', '.join(CONTACT_FIELDS)}, "  # nosec B608
            "created_at, created_by, updated_at, updated_by) "
            f"VALUES (?, {', '.join('?' * len(CONTACT_FIELDS))}, ?, ?, ?, ?) RETURNING id",
            (organization_id, *values, now, actor, now, actor),
        )
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    async def noc_update_contact(
        self, contact_id: int, fields: Mapping[str, Any], now: float, actor: str
    ) -> None:
        sets = ", ".join(f"{name} = ?" for name in CONTACT_FIELDS)
        await self.conn.execute(
            f"UPDATE noc_contact SET {sets}, version = version + 1, "  # nosec B608 - names fixed
            "updated_at = ?, updated_by = ? WHERE id = ?",
            (*(fields[name] for name in CONTACT_FIELDS), now, actor, contact_id),
        )

    async def noc_delete_contact(self, contact_id: int) -> None:
        await self.conn.execute("DELETE FROM noc_contact WHERE id = ?", (contact_id,))

    async def noc_count_contacts(self, organization_id: int) -> int:
        cur = await self.conn.execute(
            "SELECT COUNT(*) FROM noc_contact WHERE organization_id = ?", (organization_id,)
        )
        row = await cur.fetchone()
        return int(row[0]) if row else 0
