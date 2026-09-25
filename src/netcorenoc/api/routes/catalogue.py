"""`/api/catalogue/*` — the trap catalogue: classes, the OID tree, rules, and imports (v0.22.0).

Items 16-18 (ADR #384, #385). **A rule names and grades; it changes nothing about correlation**,
nothing a maintenance window collects, and nothing in the dataset — it is resolved when a screen
reads, like the per-class declaration it generalises. Unscoped for `/api/classes`' reason: a trap
OID is a kind of trap, not a network element, and no row here names one.

The import is the one upload on this API, so its bounds are stated where they are enforced:
the declared length is refused before the body is read, the body is read in chunks and abandoned
past :data:`catalogue_import.MAX_BYTES` whatever the header said, and parsing has a row cap and a
deadline. The text is never executed or interpreted — cells are matched against patterns — and the
audit row carries its digest and counts, never its contents.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from netcorenoc.api import catalogue_import as importer
from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.crosscutting import auth, shaping

MAX_PAGE = 200


class RuleIn(BaseModel):
    """A declared rule: a name and/or a severity for an OID, or for everything beneath it."""

    oid: str = Field(min_length=3, max_length=600)
    subtree: bool = True
    name: str | None = Field(default=None, max_length=importer.MAX_NAME)
    severity: Literal["critical", "major", "minor", "warning", "indeterminate"] | None = None


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the catalogue routes on `app`."""
    store, security, write_txn = ctx.store, ctx.security, ctx.write_txn
    audit_row = ctx.perimeter.audit_row
    route = DeclaredRoutes(app)

    @route.get("/api/catalogue")
    async def catalogue(
        q: str | None = None,
        under: str | None = None,
        limit: int = 50,
        offset: int = 0,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """A page of classes, busiest first, each with the rule winning its name and severity."""
        branch = importer.normalise_oid(under) if under else None
        if branch and importer.valid_oid(branch):
            raise HTTPException(status_code=422, detail="under: " + str(importer.valid_oid(branch)))
        async with store.lock:
            page = await store.catalogue_classes(
                q=(q or "").strip()[:100] or None,
                under=branch,
                limit=min(max(limit, 1), MAX_PAGE),
                offset=max(offset, 0),
            )
        return shaping.shape(page, principal.role)  # a declared name may carry an address

    @route.get("/api/catalogue/tree")
    async def tree(
        node: str = "1.3.6.1.4.1", principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """The children of one OID node, by arc, and the rules on it."""
        at = importer.normalise_oid(node)
        broken = importer.valid_oid(at)
        if broken:
            raise HTTPException(status_code=422, detail=f"node: {broken}")
        async with store.lock:
            answer = await store.catalogue_tree(at)
        return shaping.shape(answer, principal.role)

    @route.get("/api/catalogue/rules")
    async def rules(
        source: Literal["declared", "imported"] | None = None,
        limit: int = 50,
        offset: int = 0,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        async with store.lock:
            page = await store.catalogue_rules(
                source=source, limit=min(max(limit, 1), MAX_PAGE), offset=max(offset, 0)
            )
        return shaping.shape(page, principal.role)

    @route.post("/api/catalogue/rules")
    async def put_rule(
        body: RuleIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """Declare a name and/or severity for an OID or a branch; replaces that node's rule."""
        oid = importer.normalise_oid(body.oid)
        broken = importer.valid_oid(oid)
        if broken:
            raise HTTPException(status_code=422, detail=f"oid: {broken}")
        name = " ".join((body.name or "").split()) or None
        if name is None and body.severity is None:
            raise HTTPException(status_code=422, detail="a rule must give a name or a severity")
        async with write_txn():
            rule_id, created = await store.put_class_rule(
                oid=oid,
                subtree=body.subtree,
                name=name,
                severity=body.severity,
                vendor=None,
                source="declared",
                origin=None,
                actor=principal.actor,
            )
            await audit_row(
                request,
                principal,
                "catalogue.rule.set",
                "ok",
                object_type="class_rule",
                object_id=str(rule_id),
                details={
                    "oid": oid,
                    "subtree": body.subtree,
                    "severity": body.severity,
                    "name_len": len(name or ""),
                    "created": created,
                },
            )
        return {"id": rule_id, "created": created}

    @route.delete("/api/catalogue/rules/{rule_id}")
    async def delete_rule(
        rule_id: int, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, str]:
        async with write_txn():
            gone = await store.delete_class_rule(rule_id)
            if gone is None:
                raise HTTPException(status_code=404, detail="no such rule")
            await audit_row(
                request,
                principal,
                "catalogue.rule.delete",
                "ok",
                object_type="class_rule",
                object_id=str(rule_id),
                details={"oid": gone.oid, "source": gone.source},
            )
        return {"status": "withdrawn"}

    @route.post("/api/catalogue/import")
    async def import_file(
        request: Request, dry_run: bool = False, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """Check, or import, a trap list. **All or nothing**: one bad row imports no row."""
        declared = request.headers.get("content-length")
        cap = importer.MAX_BYTES * 2 + 4096  # the JSON envelope escapes quotes and newlines
        if declared is not None and (not declared.isdigit() or int(declared) > cap):
            raise HTTPException(status_code=413, detail="the file is too large to import")
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > cap:
                raise HTTPException(status_code=413, detail="the file is too large to import")
        try:
            envelope = json.loads(raw.decode("utf-8"))
            text = str(envelope["text"])
            filename = " ".join(str(envelope.get("filename") or "file").split())[:120]
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            raise HTTPException(
                status_code=422, detail='send {"filename": …, "text": …} as JSON'
            ) from None
        parsed = importer.parse(text)
        report: dict[str, Any] = {
            "filename": filename,
            "rows": len(parsed.rows),
            "problems": len(parsed.problems),
            "listed": [p.__dict__ for p in parsed.problems[: importer.MAX_LISTED]],
            "refused": parsed.refused,
            "policy": "all-or-nothing: a file with any problem imports nothing",
        }
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if not parsed.ok:
            report["outcome"] = "refused"
            async with write_txn():
                await audit_row(
                    request,
                    principal,
                    "catalogue.import",
                    "denied",
                    object_type="file",
                    object_id=digest[:16],
                    details={
                        "rows": len(parsed.rows),
                        "problems": len(parsed.problems),
                        "refused": parsed.refused,
                        "dry_run": dry_run,
                    },
                )
            return report
        async with store.lock:
            existing = {(r.oid, r.subtree, r.source): r for r in (await store.catalogue()).rules}
        new = sum(1 for r in parsed.rows if (r.oid, False, "imported") not in existing)
        shadowed = [r.line for r in parsed.rows if (r.oid, False, "declared") in existing]
        report.update(new=new, updated=len(parsed.rows) - new, shadowed_by_declared=shadowed[:50])
        if dry_run:
            report["outcome"] = "checked"
            return report
        now = time.time()
        async with write_txn():
            for row in parsed.rows:
                await store.put_class_rule(
                    oid=row.oid,
                    subtree=False,
                    name=row.name,
                    severity=row.severity,
                    vendor=row.vendor,
                    source="imported",
                    origin=filename,
                    actor=principal.actor,
                    now=now,
                )
            await audit_row(
                request,
                principal,
                "catalogue.import",
                "ok",
                object_type="file",
                object_id=digest[:16],
                details={"rows": len(parsed.rows), "new": new, "filename_len": len(filename)},
            )
        report["outcome"] = "imported"
        return report

    @route.delete("/api/catalogue/imported")
    async def clear_imported(
        request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """Withdraw every imported row — the undo for an import. Declared rules are untouched."""
        async with write_txn():
            removed = await store.delete_imported_rules()
            await audit_row(
                request,
                principal,
                "catalogue.import",
                "ok",
                object_type="file",
                object_id="withdrawn",
                details={"removed": removed},
            )
        return {"removed": removed}
