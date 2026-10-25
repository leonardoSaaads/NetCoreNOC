"""The status light of a credential, from its reachability checks (ADR #457).

Three colours and a grey, each with the sentence that earned it, so a light is never only a colour:

* **green, `up`** — the last check answered the protocol, and so did every check in the window;
* **yellow, `degraded`** — the last check answered, but one in the window did not (intermittent),
  or it answered slowly;
* **red, `down`** — the last check did not answer: refused, timed out, unresolvable, or something
  that is not the protocol answered;
* **grey, `unknown`** — never checked, checks switched off, or a kind that cannot be checked without
  using the credential (SNMP is UDP: a request with no community gets no answer either way).

**What green does not say**: that the credential is accepted. Checking a password means logging in
with it, which is the executor's job and does not exist yet; until it does, a check never opens
the seal, and the light describes the *endpoint*. The console and the API say so beside it.

Pure: a list of checks in, a dict out.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

__all__ = ["SLOW_MS", "WINDOW", "status"]

#: How many recent checks decide between green and yellow.
WINDOW = 5
#: An answer slower than this is yellow: an endpoint that takes two seconds to greet is struggling.
SLOW_MS = 2000.0

_UNCHECKABLE = (
    "Not checked: this kind runs over UDP, and an unauthenticated request gets no answer either "
    "way. The NOC executor will check it with the credential."
)


def status(
    checks: Sequence[Mapping[str, Any]], *, checkable: bool, monitored: bool
) -> dict[str, Any]:
    """``{"state", "reason", "last_checked_at", "latency_ms"}`` for checks newest first."""
    if not checkable:
        return _state("unknown", _UNCHECKABLE, None, None)
    if not checks:
        reason = "Not checked yet." if monitored else "Checks are off for this credential."
        return _state("unknown", reason, None, None)
    latest = checks[0]
    at, latency = latest["at"], latest.get("latency_ms")
    if not latest["ok"]:
        return _state("down", f"No answer: {latest.get('detail') or 'unreachable'}.", at, latency)
    recent = checks[:WINDOW]
    failed = sum(1 for check in recent if not check["ok"])
    if failed:
        return _state(
            "degraded",
            f"Intermittent: {failed} of the last {len(recent)} checks got no answer.",
            at,
            latency,
        )
    if latency is not None and float(latency) >= SLOW_MS:
        return _state("degraded", f"Slow: answered in {float(latency):.0f} ms.", at, latency)
    detail = latest.get("detail") or "answered"
    return _state("up", f"Reachable: {detail}.", at, latency)


def _state(state: str, reason: str, at: float | None, latency: float | None) -> dict[str, Any]:
    return {"state": state, "reason": reason, "last_checked_at": at, "latency_ms": latency}
