"""What the API answers about the NOC's customers, and who may change what (ADR #473).

Built for both readers, as every NOC answer is: the console draws a customer's book from it, and
an agent reading it over MCP gets the same facts with explicit labels. Two things are shaped by the
reader's role, by the rules the rest of the console already follows: **a contact's email and phone
are shown to whoever may see people** (`shaping.sees_people`), and **an element's address to
whoever may see addresses** (`shaping.sees_raw_addresses`); the rest of the book is the NOC's
working reference, for everyone who reads cases.

Who may write is :class:`Editor`'s: the book is worked by whoever works cases; what the customer
itself said (instructions, workbook, documents, circuits) is kept by people and by tokens that are
not the NOC's agents, and an agent keeps what it learns — the memory and a circuit's notes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, Request

from netcorenoc.api.noc_gate import is_agent
from netcorenoc.crosscutting import auth, shaping
from netcorenoc.engine.noc import circuits, customers, doctext, feed

__all__ = [
    "AGENTS_KEEP_MEMORY",
    "Editor",
    "circuit_view",
    "contact_view",
    "customer_view",
    "doc_view",
    "editor_of",
    "visible_customer",
]

AGENTS_KEEP_MEMORY = (
    "what the customer told the NOC — its instructions, workbook, documents and circuits — is kept "
    "by people; an agent keeps what it learns: the customer's memory and a circuit's notes"
)
_HIDDEN = "hidden for your role"
NO_SUCH = "no such customer"


def _iso(value: Any) -> str | None:
    return None if value is None else datetime.fromtimestamp(float(value), UTC).isoformat()


@dataclass(frozen=True)
class Editor:
    """What one caller may do to a customer's book."""

    actor: str
    role: str | None
    works: bool
    agent: bool

    @property
    def keeps_book(self) -> bool:
        return self.works and not self.agent

    def require_book(self) -> None:
        if not self.works:
            raise HTTPException(status_code=403, detail="working cases is needed to change this")
        if self.agent:
            raise HTTPException(status_code=403, detail=AGENTS_KEEP_MEMORY)

    def require_work(self) -> None:
        if not self.works:
            raise HTTPException(status_code=403, detail="working cases is needed to change this")

    def actions(self) -> list[str]:
        out = ["edit_memory", "note_circuits"] if self.works else []
        if self.keeps_book:
            out += ["edit_instructions", "edit_workbook", "upload_docs", "edit_circuits"]
        return out


async def editor_of(ctx: Any, request: Request, principal: auth.Principal) -> Editor:
    return Editor(
        actor=principal.actor,
        role=principal.role,
        works="cases.work" in request.state.capabilities,
        agent=await is_agent(ctx, principal),
    )


async def visible_customer(ctx: Any, principal: auth.Principal, customer_id: int) -> dict[str, Any]:
    """The customer, or the 404 that also answers "none of its elements is in your scope"."""
    scope = await ctx.scope_for(principal)
    ne_ids = None if scope.unrestricted else scope.ne_ids
    async with ctx.store.reading():
        row = await ctx.store.noc_customer(customer_id)
        seen = row is not None and await ctx.store.noc_customer_visible(customer_id, ne_ids)
    if row is None or not seen:
        raise HTTPException(status_code=404, detail=NO_SUCH)
    return dict(row)


def _by(value: Any, role: str | None) -> Any:
    return value if value is None or shaping.sees_people(role) else "someone"


def contact_view(row: Mapping[str, Any], role: str | None) -> dict[str, Any]:
    people = shaping.sees_people(role)
    return {
        "id": row["id"],
        "party": row["party"],
        "party_kind": row["party_kind"],
        "party_kind_label": customers.PARTY_KINDS.get(str(row["party_kind"]), row["party_kind"]),
        "escalation_level": row["escalation_level"],
        "name": row["name"],
        "role": row["role"],
        "email": row["email"] if people else (_HIDDEN if row["email"] else ""),
        "phone": row["phone"] if people else (_HIDDEN if row["phone"] else ""),
        "hours": row["hours"],
        "notes": row["notes"],
        "version": row["version"],
        "updated_at": row["updated_at"],
        "updated_by": _by(row["updated_by"], role),
    }


def workbook(contacts: Sequence[Mapping[str, Any]], role: str | None) -> list[dict[str, Any]]:
    """The contacts company by company, each company's in the order they are called."""
    parties: dict[str, dict[str, Any]] = {}
    for row in contacts:
        party = parties.setdefault(
            str(row["party"]),
            {"party": row["party"], "party_kind": row["party_kind"], "contacts": []},
        )
        party["contacts"].append(contact_view(row, role))
    return list(parties.values())


def doc_view(row: Mapping[str, Any], role: str | None) -> dict[str, Any]:
    kind = doctext.TYPES.get(str(row["ext"]))
    return {
        "id": row["id"],
        "name": row["name"],
        "title": row["title"] or row["name"],
        "category": row["category"],
        "category_label": customers.DOC_CATEGORIES.get(str(row["category"]), row["category"]),
        "type": row["ext"],
        "type_label": kind.label if kind else row["ext"],
        "icon": kind.icon if kind else "doc",
        "mime": row["mime"],
        "size": row["size"],
        "sha256": row["sha256"],
        "text_chars": row["text_chars"],
        "text_note": row["text_note"],
        "created_at": row["created_at"],
        "created_at_iso": _iso(row["created_at"]),
        "created_by": _by(row["created_by"], role),
    }


def circuit_view(row: Mapping[str, Any], role: str | None, *, whole: bool) -> dict[str, Any]:
    out = {
        "id": row["id"],
        "customer_id": row["organization_id"],
        "customer": row["customer"],
        **{name: row[name] for name in circuits.FIELDS},
        "source": row["source"],
        "version": row["version"],
        "updated_at": row["updated_at"],
        "updated_by": _by(row["updated_by"], role),
        "notes_at": row["notes_at"],
        "notes_by": _by(row["notes_by"], role),
        "has_notes": bool(row["notes_chars"]),
    }
    if whole:
        out["attributes"] = row["attributes"]
        out["path"] = row["path"]
        out["path_text"] = [circuits.hop_text(h) for h in row["path"]]
        out["notes"] = row["notes"]
        out["created_at"] = row["created_at"]
    return out


def customer_view(
    row: Mapping[str, Any],
    editor: Editor,
    *,
    contacts: Sequence[Mapping[str, Any]],
    docs: Sequence[Mapping[str, Any]],
    elements: Sequence[Mapping[str, Any]],
    statuses: Sequence[str],
) -> dict[str, Any]:
    text = (lambda s: s) if shaping.sees_raw_addresses(editor.role) else feed.mask_addresses
    return {
        "id": row["id"],
        "name": row["name"],
        "slug": row["slug"],
        "instructions": row["instructions"],
        "memory": row["memory"],
        "version": row["version"],
        "updated_at": row["updated_at"],
        "updated_by": _by(row["updated_by"], editor.role),
        "memory_at": row["memory_at"],
        "memory_at_iso": _iso(row["memory_at"]),
        "memory_by": _by(row["memory_by"], editor.role),
        "open_cases": row["open_cases"],
        "workbook": workbook(contacts, editor.role),
        "docs": [doc_view(d, editor.role) for d in docs],
        "circuits": row["circuits"],
        "circuit_statuses": list(statuses),
        "elements": [
            {"id": e["id"], "ip": text(str(e["ip"])), "label": e["label"], "vendor": e["vendor"]}
            for e in elements
        ],
        "element_count": row["elements"],
        "actions": editor.actions(),
        "limits": {
            "instructions": customers.MAX_INSTRUCTIONS,
            "memory": customers.MAX_MEMORY,
            "doc_bytes": doctext.MAX_BYTES,
            "doc_types": sorted(doctext.TYPES),
        },
    }
