"""The SNMP receiver's policy: versions, communities and SNMPv3 users (v0.30.0, ADR #444).

`config.read` to see it and `config.write` to change it — the allowlist's two capabilities, because
this is the same kind of fact: what the appliance agrees to hear. A change is audited (names and
protocols, never a secret), stored in `meta`, and swapped into the running receiver at once.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request

from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.api.models import SnmpIn, SnmpUserIn
from netcorenoc.crosscutting import auth
from netcorenoc.ingest import snmpconf, usm
from netcorenoc.ingest.receiver import community_digest


def _user(given: SnmpUserIn, previous: snmpconf.UsmUser | None) -> snmpconf.UsmUser:
    """A user with master keys: hashed from a passphrase given now, or kept from `previous` when
    the protocol that key was derived under is unchanged (a privacy key hashes with the auth one).
    """
    same_auth = previous is not None and previous.auth == given.auth
    same_priv = same_auth and previous is not None and previous.priv == given.priv
    auth_key = (
        snmpconf.hash_passphrase(given.auth_passphrase, given.auth)
        if given.auth_passphrase and given.auth in snmpconf.AUTH_PROTOCOLS
        else (previous.auth_key if same_auth and previous is not None else b"")
    )
    priv_key = (
        snmpconf.hash_passphrase(given.priv_passphrase, given.auth)
        if given.priv_passphrase and given.auth in snmpconf.AUTH_PROTOCOLS
        else (previous.priv_key if same_priv and previous is not None else b"")
    )
    try:
        engine_id = snmpconf.parse_engine_id(given.engine_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"user {given.name!r}: {exc}") from exc
    return snmpconf.UsmUser(
        name=given.name,
        auth=given.auth,
        priv=given.priv if given.auth != snmpconf.NONE else snmpconf.NONE,
        auth_key=auth_key if given.auth != snmpconf.NONE else b"",
        priv_key=priv_key if given.priv != snmpconf.NONE else b"",
        engine_id=engine_id,
    )


def view(policy: snmpconf.SnmpPolicy) -> dict[str, Any]:
    """The policy as the console sees it: every choice, no key, and a hint per community."""
    return {
        "v1": policy.v1,
        "v2c": policy.v2c,
        "v3": policy.v3,
        "time_window": policy.time_window,
        "communities": {
            "any": not policy.communities,
            "entries": [{"id": c.digest, "hint": c.hint} for c in policy.communities],
        },
        "users": [
            {
                "name": u.name,
                "auth": u.auth,
                "priv": u.priv,
                "level": ("noAuthNoPriv", "authNoPriv", "authPriv")[u.level],
                "engine_id": u.engine_id.hex() if u.engine_id else None,
            }
            for u in sorted(policy.users.values(), key=lambda u: u.name)
        ],
    }


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the SNMP policy routes on `app`."""
    store, security, guarded, runtime = ctx.store, ctx.security, ctx.guarded, ctx.runtime
    audit_row, write_txn = ctx.perimeter.audit_row, ctx.write_txn
    route = DeclaredRoutes(app)

    async def _current() -> snmpconf.SnmpPolicy:
        if runtime is not None and runtime.snmp is not None:
            return runtime.snmp
        stored = await store.get_meta(snmpconf.META_KEY)
        return snmpconf.from_document(stored)[0] if stored else snmpconf.DEFAULT_POLICY

    @route.get("/api/snmp", dependencies=guarded)
    async def get_snmp() -> dict[str, Any]:
        """What the receiver accepts, the protocols on offer, and what it refused since start."""
        async with store.lock:
            policy = await _current()
        return {
            **view(policy),
            "protocols": {
                "auth": list(snmpconf.AUTH_PROTOCOLS),
                "priv": list(snmpconf.PRIV_PROTOCOLS),
                "weak": sorted(snmpconf.WEAK),
            },
            "privacy_available": usm.privacy_available(),
            "refusals": runtime.refusals() if runtime is not None else {},
        }

    @route.post("/api/snmp")
    async def set_snmp(
        body: SnmpIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        names = [u.name for u in body.users]
        if len(set(names)) != len(names):
            raise HTTPException(status_code=400, detail="each SNMPv3 user name must be unique")
        limit = snmpconf.MAX_COMMUNITY_CHARS
        if any(not (1 <= len(c) <= limit and c.isprintable()) for c in body.communities.add):
            raise HTTPException(
                status_code=400, detail=f"a community is 1 to {limit} printable characters"
            )
        async with store.lock:
            current = await _current()

        def hashed() -> dict[bytes, snmpconf.UsmUser]:
            known = current.users
            return {u.name.encode(): _user(u, known.get(u.name.encode())) for u in body.users}

        # Each passphrase costs a 1 MiB hash: off the event loop, so traps keep flowing meanwhile.
        users = await asyncio.to_thread(hashed)
        async with write_txn():
            key = await store.community_hmac_key()
            communities: tuple[snmpconf.Community, ...] = ()
            if not body.communities.any:
                kept = [c for c in current.communities if c.digest in set(body.communities.keep)]
                added = [
                    snmpconf.Community(
                        community_digest(c.encode(), key), snmpconf.community_hint(c)
                    )
                    for c in body.communities.add
                ]
                unique = {c.digest: c for c in [*kept, *added]}
                communities = tuple(unique.values())
                if not communities:
                    raise HTTPException(
                        status_code=400,
                        detail="name at least one community, or accept any community",
                    )
            policy = snmpconf.SnmpPolicy(
                v1=body.v1,
                v2c=body.v2c,
                v3=body.v3,
                time_window=body.time_window,
                communities=communities,
                users=users,
            )
            problems = snmpconf.policy_problems(policy)
            if problems:
                raise HTTPException(status_code=400, detail="; ".join(problems))
            await store.set_meta(snmpconf.META_KEY, snmpconf.to_document(policy))
            await audit_row(
                request,
                principal,
                "snmp.config.change",
                "ok",
                object_type="config",
                details={
                    "versions": [v for v in ("v1", "v2c", "v3") if getattr(policy, v)],
                    "communities": len(policy.communities) or "any",
                    "users": [f"{u.name}:{u.auth}/{u.priv}" for u in policy.users.values()],
                    "time_window": policy.time_window,
                },
            )
        if runtime is not None:
            runtime.apply_snmp(policy)
        return {"status": "saved", **view(policy)}
