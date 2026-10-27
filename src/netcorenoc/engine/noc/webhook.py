"""Waking an agent that runs elsewhere: one signed POST (ADR #467).

The `webhook` adapter's whole act. The agent — Claude Code behind a small receiver, Codex, OpenClaw,
a service of one's own — learns why it was woken (the run, the trigger, the case) and where to
work (the MCP endpoint), and does the work there with its own key. The request carries no
credential: it is **signed**, so the agent can tell the appliance's wake-ups from anyone else's —

    X-NetCoreNOC-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256 of "<t>.<body>">

with the signing key it was given once, when it was hired. A receiver should refuse a signature
older than five minutes (a replay) and compare in constant time.

Standard library only (`urllib`), in a worker thread, with a timeout, no redirects followed — the
address is an admin's, and a redirect is not where they pointed the appliance.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = ["HEADER", "Delivery", "deliver", "sign", "verify"]

HEADER = "X-NetCoreNOC-Signature"
#: A signature older than this is a replay.
TOLERANCE_S = 300


@dataclass(frozen=True)
class Delivery:
    ok: bool
    status: int | None
    detail: str


def sign(key: str, body: bytes, at: float) -> str:
    stamp = str(int(at))
    mac = hmac.new(key.encode(), stamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return f"t={stamp},v1={mac}"


def verify(key: str, body: bytes, header: str, now: float) -> bool:
    """What a receiver does (and what the tests do): the signature matches and is recent."""
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        stamp = int(parts["t"])
    except (KeyError, ValueError):
        return False
    if abs(now - stamp) > TOLERANCE_S:
        return False
    return hmac.compare_digest(sign(key, body, stamp), header)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _post(url: str, body: bytes, signature: str, timeout: float) -> Delivery:
    if not url.startswith(("http://", "https://")):  # checked when stored; checked again here
        return Delivery(False, None, "not an http(s) address")
    request = urllib.request.Request(  # nosec B310 - scheme checked above
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "NetCoreNOC-agent-wake/1",
            HEADER: signature,
        },
    )
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:  # nosec B310 - scheme checked
            return Delivery(
                200 <= response.status < 300, response.status, f"HTTP {response.status}"
            )
    except urllib.error.HTTPError as exc:
        return Delivery(False, exc.code, f"HTTP {exc.code}")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return Delivery(False, None, str(getattr(exc, "reason", exc))[:200])


async def deliver(url: str, key: str, payload: Mapping[str, Any], timeout: float) -> Delivery:
    """POST ``payload``, signed with ``key``; never raises."""
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    signature = sign(key, body, time.time())
    return await asyncio.to_thread(_post, url, body, signature, timeout)
