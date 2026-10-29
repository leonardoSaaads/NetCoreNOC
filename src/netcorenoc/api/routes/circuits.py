"""NOC → Customers → Circuits: each customer's circuits and the path each one takes (ADR #473).

An agent with an alarm on a port asks which circuit runs through it, and whose: **finding** a
circuit searches every field, attribute and hop of every circuit of every customer the caller may
see (`noc_find_circuits`). Reading is `cases.read`, scoped by the customer as the rest of its book
is. **Writing a circuit is a person's** (`cases.work`, not an agent: the circuit list is the
customer's), one at a time or a whole sheet (`import`, the customer's own spreadsheet with its
multi-row header, read by `engine/noc/circuits.py`); **a circuit's notes are anyone's who works
cases**, agents first — what they learned about it while working it.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request

from netcorenoc.api.context import AppContext
from netcorenoc.api.customer_view import circuit_view, editor_of, visible_customer
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.api.models_customers import CircuitImportIn, CircuitIn, CircuitNotesIn
from netcorenoc.crosscutting import auth
from netcorenoc.engine.noc import circuits, customers, office
from netcorenoc.engine.noc.circuits import CircuitError
from netcorenoc.engine.noc.customers import CustomerError

NO_CIRCUIT = "no such circuit"
MAX_LIST = 200
#: How many parsed circuits a preview of an import shows.
PREVIEW = 25


def _bad(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the circuit routes on `app`."""
    store, security, scope_for = ctx.store, ctx.security, ctx.scope_for
    audit_row, write_txn = ctx.perimeter.audit_row, ctx.write_txn
    route = DeclaredRoutes(app)

    async def audited(
        request: Request, principal: auth.Principal, action: str, customer_id: int, **details: Any
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

    async def own_circuit(customer_id: int, circuit_id: int) -> dict[str, Any]:
        circuit = await store.noc_circuit(circuit_id)
        if circuit is None or circuit["organization_id"] != customer_id:
            raise HTTPException(status_code=404, detail=NO_CIRCUIT)
        return circuit

    def cleaned(body: CircuitIn) -> dict[str, Any]:
        try:
            return circuits.clean(body.model_dump(exclude={"version"}))
        except CircuitError as exc:
            raise _bad(exc) from exc

    async def sheet(customer_id: int, body: CircuitImportIn) -> list[list[str]]:
        """The rows of the sheet the import names: pasted text, or an uploaded document."""
        if (body.text is None) == (body.doc_id is None):
            raise HTTPException(status_code=400, detail="text or doc_id: exactly one")
        try:
            if body.text is not None:
                return circuits.read_text_table(body.text)
            async with store.reading():
                doc = await store.noc_doc(int(body.doc_id or 0), content=True)
            if doc is None or doc["organization_id"] != customer_id:
                raise HTTPException(status_code=404, detail="no such document")
            data = bytes(doc["content"])
            if doc["ext"] == "xlsx":
                return office.xlsx_rows(data, body.sheet)
            if doc["ext"] in ("csv", "tsv", "txt"):
                return circuits.read_text_table(data.decode("utf-8-sig", errors="replace"))
        except (CircuitError, office.OfficeError) as exc:
            raise _bad(exc) from exc
        raise HTTPException(
            status_code=400, detail="doc_id: an Excel workbook (.xlsx), CSV or TSV document"
        )

    @route.get("/api/noc/circuits", operation_id="noc_find_circuits")
    async def find_circuits(
        q: str = "",
        limit: int = 50,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """Find circuits across every customer you may see: each word of ``q`` must appear in a
        circuit's designation, ends, provider, attributes, notes or any hop of its path — a port
        (``1/3/5``), an ODF position, a site, a provider's reference. Read one whole with
        `noc_get_circuit`."""
        if len(q) > 200:
            raise HTTPException(status_code=400, detail="q: at most 200 characters")
        scope = await scope_for(principal)
        async with store.reading():
            ids = None
            if not scope.unrestricted:
                ids = [int(c["id"]) for c in await store.noc_customers(scope.ne_ids)]
            rows, total = await store.noc_circuits(ids, query=q, limit=min(max(limit, 1), MAX_LIST))
        return {
            "circuits": [circuit_view(r, principal.role, whole=False) for r in rows],
            "total": total,
        }

    @route.get("/api/noc/customers/{customer_id}/circuits", operation_id="noc_list_circuits")
    async def list_circuits(
        customer_id: int,
        q: str = "",
        status: str = "",
        limit: int = 100,
        offset: int = 0,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """The customer's circuits, by designation: ``q`` narrows to those whose fields or path
        hold every word of it, ``status`` to one status; a page of ``limit`` from ``offset``."""
        await visible_customer(ctx, principal, customer_id)
        if len(q) > 200 or len(status) > 40:
            raise HTTPException(status_code=400, detail="q or status: too long")
        async with store.reading():
            rows, total = await store.noc_circuits(
                [customer_id],
                query=q,
                status=status,
                limit=min(max(limit, 1), MAX_LIST),
                offset=max(offset, 0),
            )
        return {
            "circuits": [circuit_view(r, principal.role, whole=False) for r in rows],
            "total": total,
            "offset": max(offset, 0),
        }

    @route.post(
        "/api/noc/customers/{customer_id}/circuits/import", operation_id="noc_import_circuits"
    )
    async def import_circuits(
        customer_id: int,
        body: CircuitImportIn,
        request: Request,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """Read a sheet of circuits — the customer's own, with its header rows of sites and
        segments over Shelf/Slot/Port columns. Without ``apply`` it only says what it found;
        with it, every circuit is stored (one with the same designation is replaced, its notes
        kept)."""
        await visible_customer(ctx, principal, customer_id)
        (await editor_of(ctx, request, principal)).require_book()
        try:
            parsed = circuits.parse_table(await sheet(customer_id, body))
        except CircuitError as exc:
            raise _bad(exc) from exc
        async with store.reading():
            known = {
                str(c["designation"])
                for c in (await store.noc_circuits([customer_id], limit=circuits.MAX_CIRCUITS))[0]
            }
        fresh = [c for c in parsed if c["designation"] not in known]
        out: dict[str, Any] = {
            "found": len(parsed),
            "new": len(fresh),
            "replaced": len(parsed) - len(fresh),
            "applied": False,
            "preview": [
                {
                    **{n: c[n] for n in circuits.FIELDS},
                    "path": [circuits.hop_text(h) for h in c["path"]],
                }
                for c in parsed[:PREVIEW]
            ],
        }
        if not body.apply:
            return out
        if len(known) + len(fresh) > circuits.MAX_CIRCUITS:
            raise HTTPException(
                status_code=409,
                detail=f"a customer keeps at most {circuits.MAX_CIRCUITS} circuits",
            )
        now = time.time()
        async with write_txn():
            for circuit in parsed:
                existing = await store.noc_circuit_by_designation(
                    customer_id, circuit["designation"]
                )
                notes = str(existing["notes"]) if existing else ""
                await store.noc_put_circuit(
                    customer_id,
                    circuit,
                    circuits.search_text({**circuit, "notes": notes}),
                    "import",
                    now,
                    principal.actor,
                    circuit_id=int(existing["id"]) if existing else None,
                )
            await audited(
                request,
                principal,
                "noc.circuit.import",
                customer_id,
                found=len(parsed),
                new=len(fresh),
                doc=body.doc_id,
            )
        return {**out, "applied": True}

    @route.post("/api/noc/customers/{customer_id}/circuits", operation_id="noc_create_circuit")
    async def create_circuit(
        customer_id: int,
        body: CircuitIn,
        request: Request,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """Add one circuit to the customer's list: its designation (unique for the customer), its
        ends, service, owner, provider, and its path hop by hop."""
        await visible_customer(ctx, principal, customer_id)
        (await editor_of(ctx, request, principal)).require_book()
        circuit = cleaned(body)
        async with write_txn():
            if await store.noc_circuit_by_designation(customer_id, circuit["designation"]):
                raise HTTPException(
                    status_code=409, detail="the customer already has a circuit with that name"
                )
            if await store.noc_count_circuits(customer_id) >= circuits.MAX_CIRCUITS:
                raise HTTPException(
                    status_code=409,
                    detail=f"a customer keeps at most {circuits.MAX_CIRCUITS} circuits",
                )
            circuit_id = await store.noc_put_circuit(
                customer_id,
                circuit,
                circuits.search_text(circuit),
                "manual",
                time.time(),
                principal.actor,
            )
            await audited(
                request,
                principal,
                "noc.circuit.create",
                customer_id,
                circuit=circuit_id,
                designation=circuit["designation"],
            )
            row = await store.noc_circuit(circuit_id)
        assert row is not None
        return circuit_view(row, principal.role, whole=True)

    @route.get(
        "/api/noc/customers/{customer_id}/circuits/{circuit_id}", operation_id="noc_get_circuit"
    )
    async def get_circuit(
        customer_id: int, circuit_id: int, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """One circuit, whole: its fields, every attribute the customer's sheet had, its path
        hop by hop (site, segment, shelf/slot/port, ODF positions…) and the NOC's notes on it."""
        await visible_customer(ctx, principal, customer_id)
        async with store.reading():
            row = await own_circuit(customer_id, circuit_id)
        return circuit_view(row, principal.role, whole=True)

    @route.post(
        "/api/noc/customers/{customer_id}/circuits/{circuit_id}",
        operation_id="noc_update_circuit",
    )
    async def update_circuit(
        customer_id: int,
        circuit_id: int,
        body: CircuitIn,
        request: Request,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """Replace one circuit, whole — its notes included."""
        await visible_customer(ctx, principal, customer_id)
        (await editor_of(ctx, request, principal)).require_book()
        circuit = cleaned(body)
        now = time.time()
        async with write_txn():
            current = await own_circuit(customer_id, circuit_id)
            if body.version is not None and body.version != current["version"]:
                raise HTTPException(
                    status_code=409,
                    detail=f"it changed since you read it (now version {current['version']})",
                )
            other = await store.noc_circuit_by_designation(customer_id, circuit["designation"])
            if other is not None and other["id"] != circuit_id:
                raise HTTPException(
                    status_code=409, detail="the customer already has a circuit with that name"
                )
            search = circuits.search_text(circuit)
            await store.noc_put_circuit(
                customer_id, circuit, search, "manual", now, principal.actor, circuit_id=circuit_id
            )
            if circuit["notes"] != current["notes"]:
                await store.noc_circuit_notes(
                    circuit_id, circuit["notes"], search, now, principal.actor
                )
            await audited(
                request,
                principal,
                "noc.circuit.update",
                customer_id,
                circuit=circuit_id,
                designation=circuit["designation"],
            )
            row = await store.noc_circuit(circuit_id)
        assert row is not None
        return circuit_view(row, principal.role, whole=True)

    @route.post(
        "/api/noc/customers/{customer_id}/circuits/{circuit_id}/notes",
        operation_id="noc_note_circuit",
    )
    async def note_circuit(
        customer_id: int,
        circuit_id: int,
        body: CircuitNotesIn,
        request: Request,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """Keep the NOC's notes on one circuit: ``append`` one dated line of what you found
        (a dirty patch cord, a provider ticket, a port that flaps), or ``replace`` them whole to
        condense them."""
        await visible_customer(ctx, principal, customer_id)
        (await editor_of(ctx, request, principal)).require_work()
        now = time.time()
        async with write_txn():
            current = await own_circuit(customer_id, circuit_id)
            if body.version is not None and body.version != current["version"]:
                raise HTTPException(
                    status_code=409,
                    detail=f"it changed since you read it (now version {current['version']})",
                )
            try:
                notes = (
                    customers.appended(str(current["notes"]), body.notes, principal.actor, now)
                    if body.mode == "append"
                    else body.notes.strip()
                )
            except CustomerError as exc:
                raise _bad(exc) from exc
            if len(notes) > 8000:
                raise HTTPException(
                    status_code=400, detail="notes: full (8000 characters) — condense them"
                )
            search = circuits.search_text({**current, "notes": notes})
            await store.noc_circuit_notes(circuit_id, notes, search, now, principal.actor)
            await audited(
                request,
                principal,
                "noc.circuit.notes",
                customer_id,
                circuit=circuit_id,
                mode=body.mode,
            )
            row = await store.noc_circuit(circuit_id)
        assert row is not None
        return circuit_view(row, principal.role, whole=True)

    @route.post(
        "/api/noc/customers/{customer_id}/circuits/{circuit_id}/delete",
        operation_id="noc_delete_circuit",
    )
    async def delete_circuit(
        customer_id: int,
        circuit_id: int,
        request: Request,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """Remove one circuit from the customer's list."""
        await visible_customer(ctx, principal, customer_id)
        (await editor_of(ctx, request, principal)).require_book()
        async with write_txn():
            current = await own_circuit(customer_id, circuit_id)
            await store.noc_delete_circuit(circuit_id)
            await audited(
                request,
                principal,
                "noc.circuit.delete",
                customer_id,
                circuit=circuit_id,
                designation=current["designation"],
            )
        return {"deleted": True, "id": circuit_id}
