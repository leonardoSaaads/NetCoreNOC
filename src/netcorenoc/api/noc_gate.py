"""The vault's two gates, shared by its route modules: custody and step-up (ADR #456).

Route modules never import one another (`routes/__init__.py`), so what `routes/secrets.py` and
`routes/noc_access.py` both need lives here, beside the rest of the machinery:

* **Custody.** A capability is necessary and never sufficient: the caller must also be a person
  (a session, never a token) who is an admin or an editor an admin named a custodian. An admin is
  one by role — the admin role is never narrowed (#443), so holding `secrets.grants` says so.
* **Step-up.** `crosscutting/stepup.py` decides whether the person just proved who they are; this
  writes the refusal to the audit log **before** refusing — a failed confirmation is an attempt on a
  credential, and the log must have it even though the request fails.
* **What a credential looks like from outside**: its metadata and its status light, never its seal.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, Request

from netcorenoc.api.models_noc import StepUpIn
from netcorenoc.api.perimeter import _client_ip
from netcorenoc.crosscutting import auth, stepup
from netcorenoc.engine.noc import health
from netcorenoc.engine.noc.kinds import ACCESS_LEVELS, KINDS

if TYPE_CHECKING:
    from netcorenoc.api.context import AppContext

NOT_CUSTODIAN = (
    "Only an admin, or an editor an admin has named a custodian, may open the vault — signed in "
    "as a person. Ask an admin (NOC → Secrets → Custodians & agents)."
)
NOT_AGENT = (
    "Only a service token an admin has named an agent may lease a credential, and only one a "
    "custodian has switched agent access on for."
)
STEP_UP_REQUIRED = "stepup: confirm your username and password to do this"


async def is_custodian(ctx: AppContext, request: Request, principal: auth.Principal) -> bool:
    if principal.is_token or principal.ref is None:
        return False
    if "secrets.grants" in request.state.capabilities:
        return True
    async with ctx.store.reading():
        return await ctx.store.noc_has_grant(principal.ref, "custodian")


async def require_custodian(ctx: AppContext, request: Request, principal: auth.Principal) -> None:
    if not await is_custodian(ctx, request, principal):
        raise HTTPException(status_code=403, detail=NOT_CUSTODIAN)


async def is_agent(ctx: AppContext, principal: auth.Principal) -> bool:
    if not principal.is_token or principal.ref is None:
        return False
    async with ctx.store.reading():
        return await ctx.store.noc_has_grant(principal.ref, "agent")


async def confirm(
    ctx: AppContext,
    request: Request,
    principal: auth.Principal,
    given: StepUpIn | None,
    *,
    action: str,
    object_type: str,
    object_id: str,
) -> stepup.StepUpOutcome:
    """The step-up, or a 403 after its refusal is in the audit log under ``action``."""
    if given is None:
        raise HTTPException(status_code=400, detail=STEP_UP_REQUIRED)
    outcome = await stepup.confirm(
        ctx.store,
        ctx.throttle,
        principal,
        username=given.username,
        password=given.password,
        code=given.otp,
        source_ip=_client_ip(request),
        now=time.time(),
    )
    if not outcome.ok:
        async with ctx.write_txn():
            await ctx.perimeter.audit_row(
                request,
                principal,
                action,
                "denied",
                object_type=object_type,
                object_id=object_id,
                details=outcome.audit(),
            )
            await ctx.store.commit()  # durable although the request is refused
        raise HTTPException(status_code=403, detail=stepup.refusal(outcome))
    return outcome


def view(row: Mapping[str, Any], checks: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """A credential as every response shows it: metadata and status, never the seal."""
    kind = KINDS.get(str(row["kind"]))
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "kind": row["kind"],
        "kind_label": kind.label if kind else row["kind"],
        "access_level": row["access_level"],
        "access_label": ACCESS_LEVELS.get(str(row["access_level"]), row["access_level"]),
        "host": row["host"],
        "port": row["port"],
        "protocol_version": row["protocol_version"],
        "version": row["version"],
        "agent_access": row["agent_access"],
        "monitor": row["monitor"],
        "created_at": row["created_at"],
        "created_by": row["created_by"],
        "updated_at": row["updated_at"],
        "updated_by": row["updated_by"],
        "revealed_at": row["revealed_at"],
        "revealed_by": row["revealed_by"],
        "status": health.status(
            checks,
            checkable=kind is not None and kind.check is not None,
            monitored=bool(row["monitor"]),
        ),
    }
