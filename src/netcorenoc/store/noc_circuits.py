"""A customer's documents and circuits (ADR #473).

Storage only. A document is kept as it was handed over (`content`) beside the text an agent reads
from it (`body_text`); the list never carries either. A circuit's path and attributes are JSON
text (`engine/noc/circuits.py` shapes them), and ``search`` is what it is found by: every term of a
query must appear in it, so ``"sila 1/3/5"`` finds the circuit whose client port is there.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from netcorenoc.store.base import StoreBase

_DOC = (
    "id, organization_id, name, title, category, ext, mime, size, sha256, text_note, "
    "length(body_text) AS text_chars, created_at, created_by"
)
_CIRCUIT_LIST = (
    "k.id, k.organization_id, o.name AS customer, k.designation, k.a_end, k.z_end, k.bandwidth, "
    "k.service, k.status, k.owner, k.provider, k.rfs_date, k.cease_date, k.source, k.version, "
    "k.updated_at, k.updated_by, k.notes_at, k.notes_by, length(k.notes) AS notes_chars"
)
_CIRCUIT = f"{_CIRCUIT_LIST}, k.attributes, k.path, k.notes, k.created_at, k.created_by"
#: The circuit fields a row stores, in its column order.
CIRCUIT_COLUMNS = ("designation", "a_end", "z_end", "bandwidth", "service", "status", "owner",
                   "provider", "rfs_date", "cease_date")  # fmt: skip


def _like(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _circuit(row: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for name, empty in (("attributes", "{}"), ("path", "[]")):
        if name in out:
            out[name] = json.loads(out[name] or empty)
    return out


class NocCircuitMixin(StoreBase):
    # -- documents -----------------------------------------------------------------------------

    async def noc_docs(self, organization_id: int) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            f"SELECT {_DOC} FROM noc_customer_doc WHERE organization_id = ? "  # nosec B608
            "ORDER BY category, title, name, id",
            (organization_id,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def noc_doc(self, doc_id: int, *, content: bool = False) -> dict[str, Any] | None:
        """One document's metadata and text; ``content`` adds the file's bytes."""
        extra = ", content" if content else ""
        cur = await self.conn.execute(
            f"SELECT {_DOC}, body_text{extra} FROM noc_customer_doc WHERE id = ?",  # nosec B608
            (doc_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def noc_docs_usage(self, organization_id: int) -> tuple[int, int]:
        """How many documents the customer holds, and their bytes."""
        cur = await self.conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(size), 0) FROM noc_customer_doc "
            "WHERE organization_id = ?",
            (organization_id,),
        )
        row = await cur.fetchone()
        return (int(row[0]), int(row[1])) if row else (0, 0)

    async def noc_create_doc(
        self, organization_id: int, doc: Mapping[str, Any], now: float, actor: str
    ) -> int:
        cur = await self.conn.execute(
            "INSERT INTO noc_customer_doc (organization_id, name, title, category, ext, mime, "
            "size, sha256, content, body_text, text_note, created_at, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (
                organization_id,
                doc["name"],
                doc["title"],
                doc["category"],
                doc["ext"],
                doc["mime"],
                doc["size"],
                doc["sha256"],
                doc["content"],
                doc["body_text"],
                doc["text_note"],
                now,
                actor,
            ),
        )
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    async def noc_delete_doc(self, doc_id: int) -> None:
        await self.conn.execute("DELETE FROM noc_customer_doc WHERE id = ?", (doc_id,))

    # -- circuits ------------------------------------------------------------------------------

    async def noc_circuits(
        self,
        organization_ids: Sequence[int] | None,
        *,
        query: str = "",
        status: str = "",
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """The circuits of the customers named (None: every customer), each term of ``query`` in
        its search text, and how many match in all."""
        where = ["TRUE"]
        args: list[Any] = []
        if organization_ids is not None:
            where.append("k.organization_id = ANY(?)")
            args.append(sorted(organization_ids))
        for term in query.lower().split()[:8]:
            where.append("k.search LIKE ?")
            args.append(_like(term))
        if status:
            where.append("lower(k.status) = ?")
            args.append(status.lower())
        clause = " AND ".join(where)
        cur = await self.conn.execute(
            f"SELECT COUNT(*) FROM noc_circuit k WHERE {clause}",  # nosec B608 - fragments fixed
            tuple(args),
        )
        row = await cur.fetchone()
        total = int(row[0]) if row else 0
        cur = await self.conn.execute(
            f"SELECT {_CIRCUIT_LIST} FROM noc_circuit k "  # nosec B608 - fragments fixed
            f"JOIN organization o ON o.id = k.organization_id WHERE {clause} "
            "ORDER BY o.name, k.designation, k.id LIMIT ? OFFSET ?",
            (*args, limit, offset),
        )
        return [dict(r) for r in await cur.fetchall()], total

    async def noc_circuit_statuses(self, organization_id: int) -> list[str]:
        cur = await self.conn.execute(
            "SELECT DISTINCT status FROM noc_circuit WHERE organization_id = ? AND status <> '' "
            "ORDER BY status",
            (organization_id,),
        )
        return [str(r[0]) for r in await cur.fetchall()]

    async def noc_circuit(self, circuit_id: int) -> dict[str, Any] | None:
        cur = await self.conn.execute(
            f"SELECT {_CIRCUIT} FROM noc_circuit k "  # nosec B608 - a literal
            "JOIN organization o ON o.id = k.organization_id WHERE k.id = ?",
            (circuit_id,),
        )
        row = await cur.fetchone()
        return _circuit(row) if row else None

    async def noc_circuit_by_designation(
        self, organization_id: int, designation: str
    ) -> dict[str, Any] | None:
        cur = await self.conn.execute(
            f"SELECT {_CIRCUIT} FROM noc_circuit k "  # nosec B608 - a literal
            "JOIN organization o ON o.id = k.organization_id "
            "WHERE k.organization_id = ? AND k.designation = ?",
            (organization_id, designation),
        )
        row = await cur.fetchone()
        return _circuit(row) if row else None

    async def noc_count_circuits(self, organization_id: int) -> int:
        cur = await self.conn.execute(
            "SELECT COUNT(*) FROM noc_circuit WHERE organization_id = ?", (organization_id,)
        )
        row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def noc_put_circuit(
        self,
        organization_id: int,
        circuit: Mapping[str, Any],
        search: str,
        source: str,
        now: float,
        actor: str,
        *,
        circuit_id: int | None = None,
    ) -> int:
        """Store a circuit: a new one, or (``circuit_id``) a replacement of one's every field
        but its notes, which are their own write (:meth:`noc_circuit_notes`)."""
        values = (
            *(circuit[name] for name in CIRCUIT_COLUMNS),
            json.dumps(circuit["attributes"], ensure_ascii=False),
            json.dumps(circuit["path"], ensure_ascii=False),
            search,
        )
        if circuit_id is not None:
            sets = ", ".join(f"{name} = ?" for name in CIRCUIT_COLUMNS)
            await self.conn.execute(
                f"UPDATE noc_circuit SET {sets}, attributes = ?, path = ?, "  # nosec B608
                "search = ?, version = version + 1, updated_at = ?, updated_by = ? WHERE id = ?",
                (*values, now, actor, circuit_id),
            )
            return circuit_id
        cur = await self.conn.execute(
            f"INSERT INTO noc_circuit (organization_id, {', '.join(CIRCUIT_COLUMNS)}, "  # nosec B608
            "attributes, path, search, notes, source, created_at, created_by, updated_at, "
            f"updated_by) VALUES (?, {', '.join('?' * len(CIRCUIT_COLUMNS))}, ?, ?, ?, ?, ?, "
            "?, ?, ?, ?) RETURNING id",
            (organization_id, *values, circuit.get("notes", ""), source, now, actor, now, actor),
        )
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    async def noc_circuit_notes(
        self, circuit_id: int, notes: str, search: str, now: float, actor: str
    ) -> None:
        await self.conn.execute(
            "UPDATE noc_circuit SET notes = ?, search = ?, version = version + 1, notes_at = ?, "
            "notes_by = ? WHERE id = ?",
            (notes, search, now, actor, circuit_id),
        )

    async def noc_delete_circuit(self, circuit_id: int) -> None:
        await self.conn.execute("DELETE FROM noc_circuit WHERE id = ?", (circuit_id,))
