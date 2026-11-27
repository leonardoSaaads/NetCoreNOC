"""Microsoft sign-ins in progress, held by the server (ADR #510).

A sign-in is started by one person and belongs to them: the flow's key is what the browser holds,
and a poll or a save by anyone else answers as if it did not exist. The device code and, once the
owner approves, the tokens stay here — in memory, for at most the code's own lifetime plus a few
minutes to save — and are never sent to the browser. A restart forgets them all, which only means
signing in again.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from fastapi import HTTPException

from netcorenoc.crosscutting import mailauth

#: How many sign-ins may be in progress at once, appliance-wide.
MAX_FLOWS = 20
#: How long approved tokens wait to be saved.
KEEP_S = 900.0


@dataclass
class Held:
    owner: str
    flow: mailauth.DeviceFlow
    tokens: mailauth.Tokens | None = None
    done_at: float | None = None


_FLOWS: dict[str, Held] = {}


def _sweep(now: float) -> None:
    for key in [
        k
        for k, h in _FLOWS.items()
        if (h.done_at or h.flow.expires_at) + (KEEP_S if h.done_at else 0.0) < now
    ]:
        del _FLOWS[key]


def hold(owner: str, flow: mailauth.DeviceFlow, now: float | None = None) -> None:
    now = time.time() if now is None else now
    _sweep(now)
    if len(_FLOWS) >= MAX_FLOWS:
        raise HTTPException(status_code=429, detail="too many sign-ins in progress; try shortly")
    _FLOWS[flow.key] = Held(owner, flow)


def mine(owner: str, key: str) -> Held:
    """The caller's own sign-in, or the 404 every other key gets."""
    held = _FLOWS.get(key)
    if held is None or held.owner != owner:
        raise HTTPException(status_code=404, detail="no such sign-in: start it again")
    return held


def take(owner: str, key: str) -> mailauth.Tokens:
    """The approved sign-in's tokens, removed from memory as they are handed to storage."""
    held = mine(owner, key)
    if held.tokens is None:
        raise HTTPException(status_code=409, detail="the sign-in has not been approved yet")
    del _FLOWS[key]
    return held.tokens
