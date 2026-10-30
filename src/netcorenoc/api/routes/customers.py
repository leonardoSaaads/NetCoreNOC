"""NOC → Customers: each customer's book — instructions, memory and workbook (ADR #473).

A NOC serves several customers, and an agent about to act on one's network reads how that customer
wants to be served first. **Reading is `cases.read`, scoped**: a customer is visible when one of
its elements is in the caller's scope, and one that is not answers the same 404 as one that does
not exist. **Writing is `cases.work`**, split by who knows what (`api/customer_view.py`): the
instructions and the workbook are the customer's words, kept by people; the memory is what the NOC
learned, kept by agents as much as people. Every write is one transaction and one audit row.

A customer is an organization (`POST /api/organizations`, an admin's): this module adds what the
NOC knows about it, not the customer itself — and **ends it** (ADR #477) when the NOC stops serving
it: an admin's act, its name typed again and a step-up, its book deleted and its elements returned
to the default organization in one transaction, one audit row. No agent tool reaches it.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request

from netcorenoc.api import noc_gate as gate
from netcorenoc.api.context import AppContext
from netcorenoc.api.customer_view import (
    contact_view,
    customer_view,
    editor_of,
    visible_customer,
)
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.api.models_customers import ContactIn, CustomerEndIn, InstructionsIn, MemoryIn
from netcorenoc.crosscutting import auth
from netcorenoc.engine.noc import customers
from netcorenoc.engine.noc.customers import CustomerError

NO_CONTACT = "no such contact"


def _current(version: int | None, now: int) -> None:
    if version is not None and version != now:
        raise HTTPException(
            status_code=409, detail=f"it changed since you read it (now version {now})"
        )


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the customer routes on `app`."""
    store, security, scope_for = ctx.store, ctx.security, ctx.scope_for
    audit_row, write_txn = ctx.perimeter.audit_row, ctx.write_txn
    route = DeclaredRoutes(app)

    async def audited(
        request: Request,
        principal: auth.Principal,
        action: str,
        customer_id: int,
        **details: Any,
    ) -> None:
        await audit_row(
            request,
            principal,
            action,
            "ok",
            object_type="organization",
            object_id=str(customer_id),
            details=details or None,
        )

    async def book(customer_id: int, request: Request, principal: auth.Principal) -> dict[str, Any]:
        row = await visible_customer(ctx, principal, customer_id)
        editor = await editor_of(ctx, request, principal)
        async with store.reading():
            contacts = await store.noc_contacts(customer_id)
            docs = await store.noc_docs(customer_id)
            elements = await store.noc_customer_elements(customer_id, limit=50)
            statuses = await store.noc_circuit_statuses(customer_id)
        return customer_view(
            row, editor, contacts=contacts, docs=docs, elements=elements, statuses=statuses
        )

    async def own_contact(customer_id: int, contact_id: int) -> dict[str, Any]:
        contact = await store.noc_contact(contact_id)
        if contact is None or contact["organization_id"] != customer_id:
            raise HTTPException(status_code=404, detail=NO_CONTACT)
        return contact

    def checked(body: ContactIn) -> dict[str, Any]:
        try:
            return customers.contact(body.model_dump(exclude={"version"}))
        except CustomerError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @route.get("/api/noc/customers", operation_id="noc_list_customers")
    async def list_customers(
        request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """Every customer the NOC serves that you may see: what its book holds (contacts,
        documents, circuits), its open cases, and whether it has instructions and a memory.
        Read a customer's book (`noc_get_customer`) before acting on its network."""
        scope = await scope_for(principal)
        async with store.reading():
            rows = await store.noc_customers(None if scope.unrestricted else scope.ne_ids)
        editor = await editor_of(ctx, request, principal)
        return {
            "customers": [
                {
                    "id": r["id"],
                    "name": r["name"],
                    "slug": r["slug"],
                    "elements": r["elements"],
                    "contacts": r["contacts"],
                    "docs": r["docs"],
                    "circuits": r["circuits"],
                    "open_cases": r["open_cases"],
                    "has_instructions": bool(r["instructions_chars"]),
                    "has_memory": bool(r["memory_chars"]),
                    "updated_at": r["updated_at"],
                    "memory_at": r["memory_at"],
                }
                for r in rows
            ],
            "party_kinds": customers.PARTY_KINDS,
            "doc_categories": customers.DOC_CATEGORIES,
            "actions": editor.actions(),
        }

    @route.get("/api/noc/customers/{customer_id}", operation_id="noc_get_customer")
    async def get_customer(
        customer_id: int, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """One customer's book: its instructions (how it wants the NOC to act and to name its
        cases), the NOC's memory of its network, its workbook (who to call, company by company,
        in escalation order), its documents (read one with `noc_read_customer_doc`), how many
        circuits it has (`noc_list_circuits`), its elements and its open cases."""
        return await book(customer_id, request, principal)

    @route.post("/api/noc/customers/{customer_id}", operation_id="noc_set_customer_instructions")
    async def set_instructions(
        customer_id: int,
        body: InstructionsIn,
        request: Request,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """Replace the customer's instructions: how it wants the NOC to act. Kept by people —
        an agent records what it learns with `noc_update_customer_memory` instead."""
        await visible_customer(ctx, principal, customer_id)
        (await editor_of(ctx, request, principal)).require_book()
        async with write_txn():
            row = await store.noc_customer(customer_id)
            assert row is not None
            _current(body.version, int(row["version"]))
            await store.noc_set_instructions(
                customer_id, body.instructions.strip(), time.time(), principal.actor
            )
            await audited(
                request,
                principal,
                "noc.customer.update",
                customer_id,
                chars=len(body.instructions.strip()),
            )
        return await book(customer_id, request, principal)

    @route.post(
        "/api/noc/customers/{customer_id}/memory", operation_id="noc_update_customer_memory"
    )
    async def set_memory(
        customer_id: int,
        body: MemoryIn,
        request: Request,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """Keep the NOC's memory of this customer's network current: ``append`` one dated line
        of what you learned (a recurring fault, a provider's habit, a workaround that worked), or
        ``replace`` the whole memory to condense it — keep it short, true and useful to the next
        engineer. Not for what the customer asked: that is its instructions."""
        await visible_customer(ctx, principal, customer_id)
        (await editor_of(ctx, request, principal)).require_work()
        async with write_txn():
            row = await store.noc_customer(customer_id)
            assert row is not None
            _current(body.version, int(row["memory_version"]))
            try:
                memory = (
                    customers.appended(str(row["memory"]), body.memory, principal.actor)
                    if body.mode == "append"
                    else body.memory.strip()
                )
            except CustomerError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            await store.noc_set_memory(customer_id, memory, time.time(), principal.actor)
            await audited(
                request,
                principal,
                "noc.customer.memory",
                customer_id,
                mode=body.mode,
                chars=len(memory),
            )
        return await book(customer_id, request, principal)

    @route.post("/api/noc/customers/{customer_id}/contacts", operation_id="noc_create_contact")
    async def create_contact(
        customer_id: int,
        body: ContactIn,
        request: Request,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """Add a person (or a desk) to the customer's workbook: their company, its kind, the
        order they are called in (1 first), how to reach them and when."""
        await visible_customer(ctx, principal, customer_id)
        (await editor_of(ctx, request, principal)).require_book()
        fields = checked(body)
        async with write_txn():
            if await store.noc_count_contacts(customer_id) >= customers.MAX_CONTACTS:
                raise HTTPException(
                    status_code=409,
                    detail=f"a workbook holds at most {customers.MAX_CONTACTS} contacts",
                )
            contact_id = await store.noc_create_contact(
                customer_id, fields, time.time(), principal.actor
            )
            await audited(
                request,
                principal,
                "noc.contact.create",
                customer_id,
                contact=contact_id,
                party=fields["party"],
            )
            row = await store.noc_contact(contact_id)
        assert row is not None
        return contact_view(row, principal.role)

    @route.post(
        "/api/noc/customers/{customer_id}/contacts/{contact_id}",
        operation_id="noc_update_contact",
    )
    async def update_contact(
        customer_id: int,
        contact_id: int,
        body: ContactIn,
        request: Request,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """Replace one workbook entry, whole."""
        await visible_customer(ctx, principal, customer_id)
        (await editor_of(ctx, request, principal)).require_book()
        fields = checked(body)
        async with write_txn():
            contact = await own_contact(customer_id, contact_id)
            _current(body.version, int(contact["version"]))
            await store.noc_update_contact(contact_id, fields, time.time(), principal.actor)
            await audited(
                request,
                principal,
                "noc.contact.update",
                customer_id,
                contact=contact_id,
                party=fields["party"],
            )
            row = await store.noc_contact(contact_id)
        assert row is not None
        return contact_view(row, principal.role)

    @route.post(
        "/api/noc/customers/{customer_id}/contacts/{contact_id}/delete",
        operation_id="noc_delete_contact",
    )
    async def delete_contact(
        customer_id: int,
        contact_id: int,
        request: Request,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """Remove one workbook entry."""
        await visible_customer(ctx, principal, customer_id)
        (await editor_of(ctx, request, principal)).require_book()
        async with write_txn():
            contact = await own_contact(customer_id, contact_id)
            await store.noc_delete_contact(contact_id)
            await audited(
                request,
                principal,
                "noc.contact.delete",
                customer_id,
                contact=contact_id,
                party=contact["party"],
            )
        return {"deleted": True, "id": contact_id}

    @route.post("/api/noc/customers/{customer_id}/end")
    async def end_customer(
        customer_id: int,
        body: CustomerEndIn,
        request: Request,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """Stop serving a customer: its book goes, its elements return to the default."""
        async with store.reading():
            row = await store.noc_customer(customer_id)
        if row is None:
            raise HTTPException(status_code=404, detail="no such customer")
        if body.name.strip().casefold() != str(row["name"]).strip().casefold():
            raise HTTPException(status_code=400, detail="name: type the customer's name to confirm")
        outcome = await gate.confirm(
            ctx,
            request,
            principal,
            body.stepup,
            action="noc.customer.end",
            object_type="organization",
            object_id=str(customer_id),
        )
        async with write_txn():
            gone = await store.end_customer(customer_id, time.time(), principal.actor)
            await audited(
                request,
                principal,
                "noc.customer.end",
                customer_id,
                name=row["name"],
                slug=row["slug"],
                **gone,
                **outcome.audit(),
            )
        return {"ended": True, "id": customer_id, **gone}
