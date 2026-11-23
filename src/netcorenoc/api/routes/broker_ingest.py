"""A remote proxy's door: `POST /api/brokers/ingest` (ADR #494).

A broker in **proxy** mode is a small NetCoreNOC proxy in the customer's network
(`python -m netcorenoc proxy`). It receives the traps there, spools them on its own disk so a link
that drops loses nothing, and forwards them here over HTTPS in numbered batches. This route is in
`rbac.PUBLIC_ROUTES` because no person or service account calls it: **the proxy's token is its
identity**, and it names exactly one broker. What keeps that safe:

* **The token is never stored.** Only its SHA-256, compared against every proxy broker's in
  constant time and without stopping at the first match, so the time an answer takes says nothing
  about how close a guess came. A broker switched off refuses its proxy with 403.
* **Bounded.** A token bucket per client address — and a much smaller one for failed tokens, so a
  guessing client is refused before it reaches the database. The body limit (1 MiB) and
  `MAX_PROXY_RECORDS` bound one batch; every record must be a plain trap record (``R``) that
  parses, or the whole batch is refused: a proxy cannot claim a port or another broker.
* **Durable before it is acknowledged.** The records are written to the spool through the ingest
  process — the spool's one writer — and fsynced before this answers 200. A batch the proxy sends
  again, because our answer did not reach it, is recognised by its numbers and not spooled twice.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import logging
import time
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.api.models_brokers import ProxyBatchIn
from netcorenoc.api.perimeter import RateLimiter, _client_ip
from netcorenoc.ingest import records

log = logging.getLogger("netcorenoc")

#: Per client address: a proxy sends a batch every second or so while its traps flow.
BATCH_CAPACITY, BATCH_REFILL = 60.0, 20.0
#: Per client address, failed tokens only: three, then one a minute.
FAILURE_CAPACITY, FAILURE_REFILL = 3.0, 1.0 / 60.0


def _digest(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer ") or len(header) > 200:
        return None
    return hashlib.sha256(header[7:].strip().encode()).hexdigest()


def _decoded(body: ProxyBatchIn) -> list[bytes]:
    """The batch as spool records, or 422: every one a plain trap record that parses."""
    out: list[bytes] = []
    for text in body.records:
        try:
            payload = base64.b64decode(text, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(status_code=422, detail="a record is not base64") from exc
        if payload[:1] != b"R":
            raise HTTPException(status_code=422, detail="a record is not a trap record (R)")
        try:
            records.unraw(payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"a record does not parse: {exc}") from exc
        out.append(payload)
    return out


def _blocked(limiter: RateLimiter, key: str, now: float) -> bool:
    """Whether this address has spent its failures — read without spending one."""
    tokens, last = limiter.buckets.get(key, (limiter.capacity, now))
    return min(limiter.capacity, tokens + (now - last) * limiter.refill) < 1.0


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the proxy's door on `app`."""
    store, engine, write_txn = ctx.store, ctx.engine, ctx.write_txn
    route = DeclaredRoutes(app)
    batches = RateLimiter(BATCH_CAPACITY, BATCH_REFILL)
    failures = RateLimiter(FAILURE_CAPACITY, FAILURE_REFILL)
    # One batch at a time per broker, so two copies of a batch cannot both be judged new.
    locks: dict[int, asyncio.Lock] = {}

    async def _broker(request: Request, client: str) -> int:
        digest = _digest(request)
        async with store.reading():
            known = await store.broker_token_hashes()
        found: tuple[int, bool] | None = None
        for broker_id, stored, enabled in known:  # every one, whatever matched: constant time
            if hmac.compare_digest(stored, digest or "") and found is None:
                found = (broker_id, enabled)
        if found is None:
            failures.allow(client, time.monotonic())
            log.warning("proxy ingest: unknown token from %s", client)
            raise HTTPException(status_code=401, detail="unknown proxy token")
        if not found[1]:
            raise HTTPException(status_code=403, detail="this broker is switched off")
        return found[0]

    @route.post("/api/brokers/ingest")
    async def ingest(body: ProxyBatchIn, request: Request) -> dict[str, Any]:
        """Accept a numbered batch of a proxy's traps. See the module docstring."""
        client = _client_ip(request)
        now = time.monotonic()
        if _blocked(failures, client, now) or not batches.allow(client, now):
            raise HTTPException(status_code=429, detail="rate limit exceeded")
        broker_id = await _broker(request, client)
        spool = engine.proxy_spool
        if spool is None:
            raise HTTPException(
                status_code=503, detail="this appliance has no trap spool; send the batch again"
            )
        payloads = _decoded(body)
        async with locks.setdefault(broker_id, asyncio.Lock()):
            async with store.reading():
                seen = await store.proxy_seq(broker_id, body.stream)
            fresh = max(0, body.last - max(seen, body.first - 1))
            new = payloads[len(payloads) - fresh :] if fresh else []
            if new and not await spool([records.for_broker(broker_id, p) for p in new]):
                raise HTTPException(
                    status_code=503, detail="the spool did not take the batch; send it again"
                )
            async with write_txn():
                await store.proxy_accept(
                    broker_id, body.first, body.last, time.time(), client, body.version, body.stream
                )
        return {"accepted": body.last, "spooled": len(new)}
