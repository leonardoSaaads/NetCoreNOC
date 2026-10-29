"""NOC → Secrets: the credential vault (ADRs #454-#457).

Every route here is custodian-gated (`api/noc_gate.py`) on top of its capability, and every route
that reads, changes or deletes a credential — or lets agents use one — takes a step-up in its own
body. What leaves this module:

* **Lists and details carry metadata and a status light, never the seal.** The store's list query
  does not select the sealed column, so no response built from it can carry one by accident.
* **A reveal is the one response holding a credential**: to a custodian who just stepped up, once,
  with `Cache-Control: no-store` (the perimeter's, for every `/api` answer), and an audit row naming
  who read it. The console hides it again after thirty seconds.
* **Changing what a credential is bound to — kind, host, port, version, access level — re-seals it**
  under the new binding and ends every lease on it, so a lease issued for one host never opens
  another.
"""

from __future__ import annotations

import time
from secrets import token_hex
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request

from netcorenoc.api import noc_gate as gate
from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.api.models_noc import AgentAccessIn, SecretIn, StepUpOnly
from netcorenoc.crosscutting import auth, secretbox, stepup
from netcorenoc.crosscutting.vault import VaultError
from netcorenoc.engine.noc import kinds, probe, providers
from netcorenoc.engine.noc.kinds import CredentialError

#: How long the console keeps a revealed credential on screen (it is a statement, not a control).
SHOWN_FOR_S = 30
NO_SUCH = "no such credential"


def _bound(
    body: SecretIn, fields: dict[str, str] | None
) -> tuple[dict[str, Any], dict[str, str] | None]:
    """The request's metadata, validated, and its fields cleaned (``None`` when not sent)."""
    try:
        host = kinds.clean_host(body.host)
        if fields is None:
            kind = kinds.KINDS.get(body.kind)
            if kind is None:
                raise CredentialError(f"kind: one of {', '.join(kinds.KINDS)}")
            version = body.protocol_version.strip() or (kind.versions[0] if kind.versions else "")
            if kind.versions and version not in kind.versions:
                raise CredentialError(f"protocol_version: one of {', '.join(kind.versions)}")
            cleaned = None
        else:
            kind, cleaned, version = kinds.clean_fields(
                body.kind, fields, version=body.protocol_version
            )
    except CredentialError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    port = body.port or kind.port
    try:
        providers.check_endpoint(kind.id, host, port)
    except providers.ProviderError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    meta = {
        "name": body.name.strip(),
        "description": body.description.strip(),
        "kind": kind.id,
        "access_level": body.access_level,
        "host": host,
        "port": port,
        "protocol_version": version,
        "monitor": body.monitor,
    }
    if not meta["name"]:
        raise HTTPException(status_code=400, detail="name: required")
    return meta, cleaned


def _binding(ref: str, meta: dict[str, Any]) -> str:
    return kinds.binding(
        ref,
        meta["kind"],
        meta["host"],
        meta["port"],
        meta["protocol_version"],
        meta["access_level"],
    )


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the vault's routes on `app`."""
    store, security = ctx.store, ctx.security
    audit_row, write_txn = ctx.perimeter.audit_row, ctx.write_txn
    route = DeclaredRoutes(app)

    async def secret_or_404(secret_id: int, *, sealed: bool = False) -> dict[str, Any]:
        async with store.reading():
            row = await store.noc_get_secret(secret_id, sealed=sealed)
        if row is None:
            raise HTTPException(status_code=404, detail=NO_SUCH)
        return row

    @route.get("/api/noc/secrets")
    async def list_secrets(
        request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """The vault for a custodian; for anyone else, an empty list and the sentence why."""
        catalogue = kinds.describe()
        if not await gate.is_custodian(ctx, request, principal):
            return {
                "custodian": False,
                "explanation": gate.NOT_CUSTODIAN,
                "secrets": [],
                "catalogue": catalogue,
                "warnings": [],
                "second_factor": "not-enrolled",
            }
        async with store.reading():
            rows = await store.noc_list_secrets()
            checks = await store.noc_checks([int(r["id"]) for r in rows])
        return {
            "custodian": True,
            "secrets": [gate.view(r, checks[int(r["id"])]) for r in rows],
            "catalogue": catalogue,
            "warnings": secretbox.warnings(),
            "second_factor": stepup.second_factor({}, None),
        }

    @route.post("/api/noc/secrets")
    async def create_secret(
        body: SecretIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        await gate.require_custodian(ctx, request, principal)
        if body.fields is None:
            raise HTTPException(status_code=400, detail="fields: required")
        meta, fields = _bound(body, body.fields)
        assert fields is not None
        ref = token_hex(16)
        sealed = secretbox.current().seal(_binding(ref, meta), fields)
        now = time.time()
        async with write_txn():
            secret_id = await store.noc_create_secret(
                {**meta, "ref": ref, "sealed": sealed}, now, principal.actor
            )
            await audit_row(
                request,
                principal,
                "noc.secret.create",
                "ok",
                object_type="noc_secret",
                object_id=str(secret_id),
                details={**_described(meta), "fields": sorted(fields)},
            )
            row = await store.noc_get_secret(secret_id)
        assert row is not None
        return gate.view(row, [])

    @route.get("/api/noc/secrets/{secret_id}")
    async def secret_detail(
        secret_id: int, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """One credential: metadata, status, recent checks, leases, and its own audit history."""
        await gate.require_custodian(ctx, request, principal)
        async with store.reading():
            row = await store.noc_get_secret(secret_id)
            if row is None:
                raise HTTPException(status_code=404, detail=NO_SUCH)
            checks = (await store.noc_checks([secret_id], per=10))[secret_id]
            leases = await store.noc_leases(secret_id=secret_id, limit=20)
            history = await store.noc_history(secret_id)
        return {**gate.view(row, checks), "checks": checks, "leases": leases, "history": history}

    @route.post("/api/noc/secrets/{secret_id}")
    async def update_secret(
        secret_id: int,
        body: SecretIn,
        request: Request,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        await gate.require_custodian(ctx, request, principal)
        current = await secret_or_404(secret_id, sealed=True)
        meta, fields = _bound(body, body.fields)
        outcome = await gate.confirm(
            ctx,
            request,
            principal,
            body.stepup,
            action="noc.secret.update",
            object_type="noc_secret",
            object_id=str(secret_id),
        )
        before = {k: current[k] for k in meta}
        rebound = any(
            before[k] != meta[k]
            for k in ("kind", "host", "port", "protocol_version", "access_level")
        )
        if fields is None and before["kind"] != meta["kind"]:
            raise HTTPException(status_code=400, detail="fields: required when the kind changes")
        sealed = str(current["sealed"])
        if fields is not None or rebound:
            try:
                payload = (
                    fields
                    if fields is not None
                    else secretbox.current().open(_binding(str(current["ref"]), before), sealed)
                )
            except VaultError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            sealed = secretbox.current().seal(_binding(str(current["ref"]), meta), payload)
        resealed = fields is not None or rebound
        now = time.time()
        async with write_txn():
            await store.noc_update_secret(
                secret_id, {**meta, "sealed": sealed}, now, principal.actor, resealed=resealed
            )
            ended = (
                await store.noc_revoke_secret_leases(secret_id, now, principal.actor)
                if resealed
                else 0
            )
            if before["host"] != meta["host"] or before["port"] != meta["port"]:
                # ADR #462: a key pinned for one device says nothing about another.
                await store.noc_set_host_key(secret_id, None, now)
            await audit_row(
                request,
                principal,
                "noc.secret.update",
                "ok",
                object_type="noc_secret",
                object_id=str(secret_id),
                details={
                    "changed": sorted(k for k in meta if before[k] != meta[k]),
                    "fields_replaced": sorted(fields) if fields is not None else [],
                    "resealed": resealed,
                    "leases_ended": ended,
                    **outcome.audit(),
                },
            )
            row = await store.noc_get_secret(secret_id)
        assert row is not None
        return gate.view(row, [])

    @route.post("/api/noc/secrets/{secret_id}/delete")
    async def delete_secret(
        secret_id: int,
        body: StepUpOnly,
        request: Request,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, str]:
        await gate.require_custodian(ctx, request, principal)
        row = await secret_or_404(secret_id)
        outcome = await gate.confirm(
            ctx,
            request,
            principal,
            body.stepup,
            action="noc.secret.delete",
            object_type="noc_secret",
            object_id=str(secret_id),
        )
        async with write_txn():
            if not await store.noc_delete_secret(secret_id):
                raise HTTPException(status_code=404, detail=NO_SUCH)
            await audit_row(
                request,
                principal,
                "noc.secret.delete",
                "ok",
                object_type="noc_secret",
                object_id=str(secret_id),
                details={"name": row["name"], "host": row["host"], **outcome.audit()},
            )
        return {"status": "deleted"}

    @route.post("/api/noc/secrets/{secret_id}/reveal")
    async def reveal_secret(
        secret_id: int,
        body: StepUpOnly,
        request: Request,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """The credential itself, to a custodian who just stepped up — the only response that
        carries one, and every one of them is in the audit log with the person who asked."""
        await gate.require_custodian(ctx, request, principal)
        row = await secret_or_404(secret_id, sealed=True)
        outcome = await gate.confirm(
            ctx,
            request,
            principal,
            body.stepup,
            action="noc.secret.reveal",
            object_type="noc_secret",
            object_id=str(secret_id),
        )
        try:
            payload = secretbox.current().open(_binding(str(row["ref"]), row), str(row["sealed"]))
        except VaultError as exc:
            async with write_txn():
                await audit_row(
                    request,
                    principal,
                    "noc.secret.reveal",
                    "error",
                    object_type="noc_secret",
                    object_id=str(secret_id),
                    details={"reason": "the seal did not open"},
                )
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        now = time.time()
        async with write_txn():
            await store.noc_mark_revealed(secret_id, now, principal.actor)
            await audit_row(
                request,
                principal,
                "noc.secret.reveal",
                "ok",
                object_type="noc_secret",
                object_id=str(secret_id),
                details={"fields": sorted(payload), **outcome.audit()},
            )
        return {"id": secret_id, "kind": row["kind"], "fields": payload, "shown_for_s": SHOWN_FOR_S}

    @route.post("/api/noc/secrets/{secret_id}/agents")
    async def agent_access(
        secret_id: int,
        body: AgentAccessIn,
        request: Request,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """Agents on (stepped up) or off (never stepped up: the off switch must always be
        reachable). Off ends every lease on the credential at once."""
        await gate.require_custodian(ctx, request, principal)
        await secret_or_404(secret_id)
        detail: dict[str, Any] = {"enabled": body.enabled}
        if body.enabled:
            outcome = await gate.confirm(
                ctx,
                request,
                principal,
                body.stepup,
                action="noc.secret.agents",
                object_type="noc_secret",
                object_id=str(secret_id),
            )
            detail.update(outcome.audit())
        now = time.time()
        async with write_txn():
            moved = await store.noc_set_agent_access(secret_id, body.enabled, now, principal.actor)
            ended = (
                0
                if body.enabled
                else await store.noc_revoke_secret_leases(secret_id, now, principal.actor)
            )
            if moved or ended:
                await audit_row(
                    request,
                    principal,
                    "noc.secret.agents",
                    "ok",
                    object_type="noc_secret",
                    object_id=str(secret_id),
                    details={**detail, "leases_ended": ended},
                )
        return {"id": secret_id, "agent_access": body.enabled, "leases_ended": ended}

    @route.post("/api/noc/secrets/{secret_id}/check")
    async def check_now(
        secret_id: int, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """One reachability check now, with nothing held while it waits on the network."""
        await gate.require_custodian(ctx, request, principal)
        row = await secret_or_404(secret_id)
        result = await probe.check_secret(row)
        if result is not None:
            async with write_txn():
                await store.noc_record_check(
                    secret_id, time.time(), result.ok, result.latency_ms, result.detail, "manual"
                )
        async with store.reading():
            fresh = await store.noc_get_secret(secret_id)
            checks = (await store.noc_checks([secret_id]))[secret_id]
        if fresh is None:
            raise HTTPException(status_code=404, detail=NO_SUCH)
        return gate.view(fresh, checks)


def _described(meta: dict[str, Any]) -> dict[str, Any]:
    """What an audit row says about a credential: its metadata, never a field's value."""
    return {k: meta[k] for k in ("name", "kind", "host", "port", "access_level")}
