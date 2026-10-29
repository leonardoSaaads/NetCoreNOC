"""NOC → Secrets: who may hold the vault, and the leases agents use instead (ADRs #456, #458).

* **Custodians, leads and agents** are named by an admin (`secrets.grants`), stepped up to name one
  and never to withdraw one. A custodian (the vault) and a lead (the NOC's KPIs and playbooks, ADR
  #470) are enabled editors — every admin already is both; an agent is a live service token.
  Withdrawing an agent ends its leases at once.
* **A lease** is asked for by an agent, for one credential a custodian switched agent access on
  for, and is shown once. It carries no secret (`engine/noc/lease.py`).
* **Introspection** answers RFC 7662's question — is this lease active, and for what? — to the
  agent it was issued to or to a custodian, and nobody else. An inactive lease is ``200
  {"active": false}`` with a reason, as the RFC has it, never an error that tells a guesser more.
  Every active introspection is recorded as a **use**: whatever acts on a device asks first.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request

from netcorenoc.api import noc_gate as gate
from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.api.models_noc import GrantIn, IntrospectIn, LeaseIn
from netcorenoc.crosscutting import auth
from netcorenoc.engine.noc import lease

#: The live leases one agent may hold at once: enough for a diagnosis across a dozen devices.
MAX_LIVE_LEASES = 20
#: The grants a person holds; `agent` is a token's.
PERSON_ROLES = frozenset({"custodian", "lead"})


def _summary(row: Mapping[str, Any]) -> dict[str, Any]:
    """What an agent learns about a credential it may lease: where and how much, never what."""
    return {
        k: row[k]
        for k in (
            "id",
            "name",
            "kind",
            "access_level",
            "host",
            "port",
            "protocol_version",
            "version",
        )
    }


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the vault's access routes on `app`."""
    store, security = ctx.store, ctx.security
    audit_row, write_txn = ctx.perimeter.audit_row, ctx.write_txn
    route = DeclaredRoutes(app)

    @route.get("/api/noc/grants", dependencies=ctx.guarded)
    async def list_grants() -> dict[str, Any]:
        """Every enabled editor and admin with whether they hold the vault, and every live token
        with whether it is an agent."""
        async with store.reading():
            users = await store.list_users()
            tokens = await store.list_tokens()
            grants = {(g["subject_ref"], g["role"]): g for g in await store.noc_grants()}
        people = [
            {
                "ref": f"user:{u['id']}",
                "username": u["username"],
                "display_name": u.get("display_name"),
                "role": u["role"],
                "custodian": u["role"] == "admin" or (f"user:{u['id']}", "custodian") in grants,
                "lead": u["role"] == "admin" or (f"user:{u['id']}", "lead") in grants,
                "by_role": u["role"] == "admin",
            }
            for u in users
            if not u["disabled"] and u["role"] in ("admin", "editor")
        ]
        agents = [
            {
                "ref": f"token:{t['id']}",
                "name": t["name"],
                "role": t["role"],
                "purpose": t.get("purpose"),
                "agent": (f"token:{t['id']}", "agent") in grants,
            }
            for t in tokens
            if not t["revoked"]
        ]
        return {"people": people, "tokens": agents, "second_factor": "not-enrolled"}

    @route.post("/api/noc/grants")
    async def set_grant(
        body: GrantIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        kind, _, raw_id = body.subject.partition(":")
        if (kind == "user") != (body.role in PERSON_ROLES):
            raise HTTPException(
                status_code=400,
                detail="a custodian or a lead is a person (user:<id>); an agent is a token",
            )
        async with store.reading():
            if kind == "user":
                user = await store.get_user(int(raw_id))
                fit = user is not None and not user["disabled"] and user["role"] == "editor"
                name = str(user["username"]) if user else ""
            else:
                tokens = {int(t["id"]): t for t in await store.list_tokens()}
                token = tokens.get(int(raw_id))
                fit = token is not None and not token["revoked"]
                name = str(token["name"]) if token else ""
        if not fit:
            raise HTTPException(
                status_code=400,
                detail="a custodian or a lead is an enabled editor (admins already are); an "
                "agent is a live service token",
            )
        detail: dict[str, Any] = {"role": body.role, "enabled": body.enabled, "name": name}
        if body.enabled:
            outcome = await gate.confirm(
                ctx,
                request,
                principal,
                body.stepup,
                action="noc.grant.change",
                object_type="noc_grant",
                object_id=body.subject,
            )
            detail.update(outcome.audit())
        now = time.time()
        async with write_txn():
            changed = await store.noc_set_grant(
                body.subject, body.role, body.enabled, now, principal.actor
            )
            ended = 0
            if body.role == "agent" and not body.enabled:
                ended = await store.noc_revoke_subject_leases(body.subject, now, principal.actor)
            if changed or ended:
                await audit_row(
                    request,
                    principal,
                    "noc.grant.change",
                    "ok",
                    object_type="noc_grant",
                    object_id=body.subject,
                    details={**detail, "leases_ended": ended},
                )
        return {
            "subject": body.subject,
            "role": body.role,
            "enabled": body.enabled,
            "changed": changed,
            "leases_ended": ended,
        }

    @route.get("/api/noc/access", operation_id="noc_my_access")
    async def my_access(
        request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """What you hold in the NOC beyond your role (ADRs #456, #470): the vault (custodian), its
        measures and playbooks (lead), credential leases (agent). The console hides what you
        lack; every route still decides on its own."""
        return {
            "custodian": await gate.is_custodian(ctx, request, principal),
            "lead": await gate.is_lead(ctx, request, principal),
            "agent": await gate.is_agent(ctx, principal),
            "explanations": {"custodian": gate.NOT_CUSTODIAN, "lead": gate.NOT_LEAD},
        }

    @route.get("/api/noc/agent")
    async def agent_view(principal: auth.Principal = Depends(security)) -> dict[str, Any]:
        """For an agent: the credentials it may lease and its leases. For anyone else, empty."""
        if not await gate.is_agent(ctx, principal):
            return {"agent": False, "explanation": gate.NOT_AGENT, "secrets": [], "leases": []}
        assert principal.ref is not None
        async with store.reading():
            rows = [r for r in await store.noc_list_secrets() if r["agent_access"]]
            leases = await store.noc_leases(subject_ref=principal.ref, limit=50)
        return {
            "agent": True,
            "secrets": [_summary(r) for r in rows],
            "leases": leases,
            "lease_ttl_s": {"default": lease.DEFAULT_TTL_S, "max": lease.MAX_TTL_S},
        }

    @route.post("/api/noc/secrets/{secret_id}/lease")
    async def issue_lease(
        secret_id: int,
        body: LeaseIn,
        request: Request,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        if not await gate.is_agent(ctx, principal):
            raise HTTPException(status_code=403, detail=gate.NOT_AGENT)
        assert principal.ref is not None
        now = time.time()
        async with store.reading():
            row = await store.noc_get_secret(secret_id)
            live = await store.noc_live_leases(principal.ref, now)
        if row is None or not row["agent_access"]:
            # A credential agents may not use is indistinguishable from one that does not exist.
            raise HTTPException(status_code=404, detail="no such credential for agents")
        if live >= MAX_LIVE_LEASES:
            raise HTTPException(
                status_code=409,
                detail=f"at most {MAX_LIVE_LEASES} live leases; revoke one you no longer need",
            )
        jti, issued = lease.new_id(), int(now)
        claims = lease.Claims(
            jti=jti,
            sub=principal.ref,
            sid=secret_id,
            ref=str(row.get("ref") or ""),
            ver=int(row["version"]),
            kind=str(row["kind"]),
            host=str(row["host"]),
            port=int(row["port"]),
            scope=str(row["access_level"]),
            iat=issued,
            nbf=issued,
            exp=issued + body.ttl_s,
        )
        async with write_txn():
            lease_id = await store.noc_create_lease(
                lease.digest(jti), secret_id, principal.ref, principal.actor, now, claims.exp
            )
            await audit_row(
                request,
                principal,
                "noc.lease.issue",
                "ok",
                object_type="noc_secret",
                object_id=str(secret_id),
                details={
                    "lease_id": lease_id,
                    "agent": principal.actor,
                    "ttl_s": body.ttl_s,
                    "purpose": body.purpose.strip()[:200],
                },
            )
        return {
            "lease": lease.issue(claims),
            "lease_id": lease_id,
            "token_type": "noc-lease",  # nosec B105 - the kind of token, not a credential
            "expires_at": claims.exp,
            "secret": _summary(row),
        }

    @route.post("/api/noc/leases/introspect")
    async def introspect(
        body: IntrospectIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        now = time.time()
        state = await gate.lease_state(ctx, request, principal, body.lease, now)
        if state.reason is not None:
            return {"active": False, "reason": state.reason}
        claims, row, secret = state.claims, state.row, state.secret
        assert claims is not None and row is not None and secret is not None
        async with write_txn():
            await store.noc_use_lease(int(row["id"]), now)
            await audit_row(
                request,
                principal,
                "noc.lease.use",
                "ok",
                object_type="noc_secret",
                object_id=str(claims.sid),
                details={"lease_id": row["id"], "agent": row["subject_name"]},
            )
        return {
            "active": True,
            "lease_id": row["id"],
            "sub": claims.sub,
            "scope": claims.scope,
            "exp": claims.exp,
            "secret": _summary(secret),
        }

    @route.post("/api/noc/leases/{lease_id}/revoke")
    async def revoke_lease(
        lease_id: int, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        async with store.reading():
            row = await store.noc_lease(lease_id)
        mine = row is not None and principal.ref == row["subject_ref"]
        if row is None or not (mine or await gate.is_custodian(ctx, request, principal)):
            raise HTTPException(status_code=404, detail="no such lease")
        async with write_txn():
            ended = await store.noc_revoke_lease(lease_id, time.time(), principal.actor)
            if ended:
                await audit_row(
                    request,
                    principal,
                    "noc.lease.revoke",
                    "ok",
                    object_type="noc_secret",
                    object_id=str(row["secret_id"]),
                    details={"lease_id": lease_id, "agent": row["subject_name"]},
                )
        return {"lease_id": lease_id, "revoked": True, "changed": ended}
