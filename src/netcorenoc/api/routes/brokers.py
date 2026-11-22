"""Brokers: the networks this appliance monitors (ADR #494) — list, add, change, remove, move.

A broker is **one monitored network owned by one customer**; how its traps arrive is chosen when it
is created and never changes (`models_brokers.py`). What an admin may do here, and why each is safe:

* **Add or change** — `brokers.write`, admin. A broker's rows feed the visibility selector
  ``broker:<id>``, so every input of the scope resolver stays admin-written (F35). The engine
  rebuilds its routing in the same transaction, so a new port is listened on within seconds and a
  new broker has its own lane — its own correlator and decider — before its first trap.
* **A proxy's token** is 256 random bits shown **once**, in the response that issues it; only its
  SHA-256 is kept, compared in constant time at the proxy's door (`broker_ingest.py`). Issuing one
  is a `token.create` audit row with the last four characters, never the token.
* **Remove** only an empty broker. One that received anything is switched off instead, so its map,
  its situations and its labels stay readable.
* **Move an element** to another broker, refused when that broker already has the address:
  identity is (broker, address).

The list is **scoped**: a person limited to some brokers (``broker:<id>``) is shown those, with
counts over what they can see — never another network's volume (F32).
"""

from __future__ import annotations

import hashlib
import re
import secrets
import time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request

from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.api.models_brokers import BrokerIn, BrokerUpdateIn, ElementBrokerIn, check_update
from netcorenoc.crosscutting import auth, shaping
from netcorenoc.crosscutting.settings import Settings
from netcorenoc.engine.operate import routing

#: Marks a proxy token in a configuration file or a leaked log line, as `ncn_` marks an API token.
TOKEN_PREFIX = "ncp_"
#: A proxy that sent nothing for this long is reported silent: it forwards while traps flow and says
#: it is alive every minute when they do not, keeping them on its own disk while its link is down.
PROXY_SILENT_S = 180.0  # three of its heartbeats (`ingest/forwarder.HEARTBEAT_S`)
#: Columns an admin reads and nobody else needs: where a network is, and where its proxy calls from.
_ADMIN_ONLY = ("networks", "proxy_address", "proxy_token_hint", "created_by", "updated_by")


def issue_token() -> tuple[str, str, str]:
    """``(token, its SHA-256, its last four characters)``. The token leaves in one response."""
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    return token, hashlib.sha256(token.encode()).hexdigest(), token[-4:]


def _slug(name: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "broker"
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}-{n}", n + 1
    return slug


def _row(broker: dict[str, Any], role: str) -> dict[str, Any]:
    out = {**broker, "networks": [n for n in str(broker["networks"]).split(",") if n]}
    if role != "admin":
        for key in _ADMIN_ONLY:
            out.pop(key, None)
    return out


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the broker routes on `app`."""
    store, engine, security, scope_for = ctx.store, ctx.engine, ctx.security, ctx.scope_for
    audit_row, write_txn = ctx.perimeter.audit_row, ctx.write_txn
    route = DeclaredRoutes(app)

    def _status(broker: dict[str, Any]) -> str:
        """How its traps are arriving now, as far as this appliance can tell."""
        if not broker["enabled"]:
            return "off"
        if broker["intake"] == "port":
            if engine.proxy_spool is None:
                return "not-listening"  # no ingest process: one socket, no broker ports
            error = engine.broker_port_errors.get(int(broker["udp_port"]))
            return f"error: {error}" if error else "listening"
        if broker["intake"] == "proxy":
            seen = broker["proxy_seen_at"]
            if seen is None:
                return "waiting"
            return "receiving" if time.time() - float(seen) < PROXY_SILENT_S else "silent"
        return "ready"

    async def _visible(principal: auth.Principal) -> dict[int, int] | None:
        """Per broker, how many of its elements this principal sees; None when unrestricted."""
        scope = await scope_for(principal)
        if scope.unrestricted:
            return None
        async with store.reading():
            owners = await store.ne_brokers(sorted(scope.ne_ids))
        seen: dict[int, int] = {}
        for broker_id in owners.values():
            seen[broker_id] = seen.get(broker_id, 0) + 1
        return seen

    @route.get("/api/brokers")
    async def list_brokers(principal: auth.Principal = Depends(security)) -> dict[str, Any]:
        """Every broker this principal can see, the Default first and then by name."""
        visible = await _visible(principal)
        async with store.reading():
            rows = await store.brokers(counts=True)
        out = []
        for row in rows:
            if visible is not None:
                if int(row["id"]) not in visible:
                    continue
                # Counts over what they can see; the broker's own volume is not theirs (F32).
                row = {**row, "elements": visible[int(row["id"])], "live": None, "last_trap": None}
            out.append({**_row(row, principal.role), "status": _status(row)})
        return {"brokers": shaping.shape(out, principal.role), "intakes": list(routing.INTAKES)}

    async def _load(broker_id: int) -> dict[str, Any]:
        async with store.reading():
            broker = await store.broker(broker_id)
        if broker is None:
            raise HTTPException(status_code=404, detail="no such broker")
        return broker

    @route.get("/api/brokers/{broker_id}")
    async def read_broker(
        broker_id: int, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """One broker. Out of scope and nonexistent are the same 404 (DECISIONS #60)."""
        visible = await _visible(principal)
        if visible is not None and broker_id not in visible:
            raise HTTPException(status_code=404, detail="no such broker")
        broker = await _load(broker_id)
        if visible is not None:
            broker = {**broker, "elements": visible[broker_id]}
        out = {**_row(broker, principal.role), "status": _status(broker)}
        return shaping.shape(out, principal.role)

    async def _conflicts(
        broker_id: int | None, name: str, udp_port: int | None, networks: list[str]
    ) -> None:
        """409 for what another broker already holds: its name, its port, one of its ranges."""
        others = [b for b in await store.brokers() if int(b["id"]) != broker_id]
        if any(str(b["name"]).lower() == name.lower() for b in others):
            raise HTTPException(status_code=409, detail=f"a broker named {name!r} exists")
        if udp_port is not None:
            if udp_port == Settings.from_env().trap_port:
                raise HTTPException(
                    status_code=409,
                    detail=f"port {udp_port} is the standard trap port; choose another",
                )
            holder = next((b for b in others if b["udp_port"] == udp_port), None)
            if holder is not None:
                raise HTTPException(
                    status_code=409, detail=f"port {udp_port} is broker {holder['name']!r}'s"
                )
        claimed = {int(b["id"]): str(b["networks"]).split(",") for b in others if b["networks"]}
        clash = routing.overlaps(networks, claimed)
        if clash:
            network, holder_id = clash[0]
            holder_name = next(str(b["name"]) for b in others if int(b["id"]) == holder_id)
            raise HTTPException(
                status_code=409,
                detail=f"{network} is already broker {holder_name!r}'s range; a narrower or wider "
                "range is fine (the longest prefix wins), the same one twice is not",
            )

    async def _customer(organization_id: int) -> None:
        if not await store.organization_exists(organization_id):
            raise HTTPException(
                status_code=422,
                detail=f"organization_id {organization_id} does not exist; "
                "GET /api/organizations lists the customers",
            )

    @route.post("/api/brokers")
    async def create_broker(
        body: BrokerIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """Add a broker. A remote proxy's token is in this response and in no other."""
        now = time.time()
        token: tuple[str, str, str] | None = issue_token() if body.intake == "proxy" else None
        async with write_txn():
            await _customer(body.organization_id)
            await _conflicts(None, body.name, body.udp_port, body.networks)
            taken = {str(b["slug"]) for b in await store.brokers()}
            broker_id = await store.create_broker(
                name=body.name,
                slug=_slug(body.name, taken),
                organization_id=body.organization_id,
                description=body.description,
                intake=body.intake,
                udp_port=body.udp_port,
                networks=",".join(body.networks),
                token_hash=token[1] if token else None,
                token_hint=token[2] if token else None,
                actor=principal.actor,
                now=now,
            )
            details = {"name": body.name, "intake": body.intake, "port": body.udp_port}
            await audit_row(
                request,
                principal,
                "config.change",
                "ok",
                object_type="broker",
                object_id=str(broker_id),
                details={**details, "networks": len(body.networks), "created": True},
            )
            if token is not None:
                await audit_row(
                    request,
                    principal,
                    "token.create",
                    "ok",
                    object_type="broker_proxy",
                    object_id=str(broker_id),
                    details={"hint": token[2]},
                )
            await engine.load_scorer_config()  # its lane and its port, before its first trap
        broker = await _load(broker_id)
        out: dict[str, Any] = {"created": True, **_row(broker, principal.role)}
        if token is not None:
            out["token"] = token[0]
        return out

    @route.post("/api/brokers/{broker_id}")
    async def update_broker(
        broker_id: int,
        body: BrokerUpdateIn,
        request: Request,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """Change a broker. Its intake is fixed; the Default broker keeps everything but its name
        and description, because it takes every trap no other broker claims."""
        current = await _load(broker_id)
        if current["is_default"] and (body.udp_port or body.networks or not body.enabled):
            raise HTTPException(
                status_code=422,
                detail="the Default broker takes every trap no other broker claims: only its "
                "name, customer and description change",
            )
        try:
            if not current["is_default"]:  # it takes what no range claims: it has none of its own
                check_update(str(current["intake"]), body)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        async with write_txn():
            await _customer(body.organization_id)
            await _conflicts(broker_id, body.name, body.udp_port, body.networks)
            await store.update_broker(
                broker_id,
                name=body.name,
                organization_id=body.organization_id,
                description=body.description,
                udp_port=body.udp_port,
                networks=",".join(body.networks),
                enabled=body.enabled,
                actor=principal.actor,
                now=time.time(),
            )
            await audit_row(
                request,
                principal,
                "config.change",
                "ok",
                object_type="broker",
                object_id=str(broker_id),
                details={
                    "name": body.name,
                    "port": body.udp_port,
                    "networks": len(body.networks),
                    "enabled": body.enabled,
                },
            )
            await engine.load_scorer_config()
        return {"updated": True, **_row(await _load(broker_id), principal.role)}

    @route.post("/api/brokers/{broker_id}/token")
    async def rotate_token(
        broker_id: int, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """A new token for a proxy broker. The old one stops working at once."""
        broker = await _load(broker_id)
        if broker["intake"] != "proxy":
            raise HTTPException(status_code=409, detail="only a proxy broker has a token")
        token, digest, hint = issue_token()
        async with write_txn():
            await store.set_broker_token(broker_id, digest, hint, principal.actor, time.time())
            await audit_row(
                request,
                principal,
                "token.create",
                "ok",
                object_type="broker_proxy",
                object_id=str(broker_id),
                details={"hint": hint, "rotated": True},
            )
        return {"rotated": True, "token": token, "proxy_token_hint": hint}

    @route.delete("/api/brokers/{broker_id}")
    async def delete_broker(
        broker_id: int, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """Remove a broker that never received anything; one with history is switched off."""
        await _load(broker_id)
        async with write_txn():
            if not await store.delete_broker(broker_id):
                raise HTTPException(
                    status_code=409,
                    detail="this broker has elements, situations or windows (or is the Default): "
                    "switch it off instead, so its map and its situations stay readable",
                )
            await audit_row(
                request,
                principal,
                "config.change",
                "ok",
                object_type="broker",
                object_id=str(broker_id),
                details={"deleted": True},
            )
            await engine.load_scorer_config()
        return {"deleted": True, "id": broker_id}

    @route.post("/api/elements/{ne_id}/broker")
    async def move_element(
        ne_id: int,
        body: ElementBrokerIn,
        request: Request,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, Any]:
        """Put one element in another broker. Its history moves with it; situations it is in
        stay where they formed."""
        await _load(body.broker_id)
        async with write_txn():
            refused = await store.move_element(ne_id, body.broker_id)
            if refused == "no such element":
                raise HTTPException(status_code=404, detail="no such NE")
            if refused is not None:
                raise HTTPException(status_code=409, detail=refused)
            await audit_row(
                request,
                principal,
                "config.change",
                "ok",
                object_type="ne",
                object_id=str(ne_id),
                details={"broker_id": body.broker_id},
            )
        return {"moved": True, "ne_id": ne_id, "broker_id": body.broker_id}
