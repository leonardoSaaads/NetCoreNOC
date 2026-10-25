"""Step-up: the person at the keyboard proves again that they are the account (ADR #456).

A session is a bearer credential — whoever holds the browser holds it. Reading a root password,
changing or deleting one, letting agents use one, and naming who may do any of that are each worth
more than a session, so each asks again for the **username, the password and a second factor**:

* **Only a person steps up.** A service token has no password to give, so a token is refused
  whatever its role: no program reads, writes or grants a device credential (agents get leases).
* **The username must be the session's own.** A colleague's password typed into an unlocked
  console does not borrow their custody.
* **A wrong password counts against sign-in's own throttle** (per username and per address), so a
  stolen session buys no guesses that signing in would not have allowed.
* **The second factor is a placeholder, and says so.** Two-factor sign-in (TOTP, RFC 6238 —
  Google Authenticator, Authy, any authenticator app) is on the roadmap; until an account can enrol
  one, :func:`second_factor` answers ``"not-enrolled"`` and every audit row records that answer.
  A code sent today is ignored rather than "checked": accepting any six digits would be a control
  that controls nothing. Enrolment turns the answer into ``"verified"`` or ``"failed"`` here, and
  the routes need no change.
"""

from __future__ import annotations

import asyncio
import hmac
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from netcorenoc.crosscutting import auth

if TYPE_CHECKING:
    from netcorenoc.store import Store

__all__ = ["SECOND_FACTOR_STATES", "StepUpOutcome", "confirm", "second_factor"]

#: What the second-factor check may answer. ``not-enrolled`` is the only answer today.
SECOND_FACTOR_STATES = frozenset({"verified", "not-enrolled", "failed"})

REFUSED = "confirm your username and password to do this"
TOKEN_REFUSED = "a service token cannot confirm an identity; a person must do this"  # nosec B105
LOCKED = "too many attempts; try again later"


@dataclass(frozen=True)
class StepUpOutcome:
    ok: bool
    second_factor: str
    reason: str | None = None

    def audit(self) -> dict[str, Any]:
        """What an audit row records about the confirmation: never the values given."""
        return {
            "step_up": "ok" if self.ok else (self.reason or "refused"),
            "second_factor": self.second_factor,
        }


def second_factor(_user: dict[str, Any], _code: str | None) -> str:
    """``"verified"``, ``"failed"`` or ``"not-enrolled"``. No account can enrol yet (ADR #456)."""
    return "not-enrolled"


async def confirm(
    store: Store,
    throttle: auth.LoginThrottle,
    principal: auth.Principal,
    *,
    username: str,
    password: str,
    code: str | None,
    source_ip: str,
    now: float,
) -> StepUpOutcome:
    """Has the session's own person just proved who they are? Reads; writes nothing.

    The caller audits the outcome and refuses the request when ``ok`` is false. scrypt runs off the
    event loop, so a confirmation never stalls the engine's batch or another request.
    """
    if principal.is_token or principal.user_id is None:
        return StepUpOutcome(False, "not-enrolled", "token")
    if throttle.locked_for(principal.actor, source_ip, now) > 0:
        return StepUpOutcome(False, "not-enrolled", "locked")
    async with store.reading():
        user = await store.get_user(principal.user_id)
    same = user is not None and hmac.compare_digest(
        str(user["username"]).encode("utf-8"), username.encode("utf-8")
    )
    if user is None or user["disabled"] or not same:
        await asyncio.to_thread(auth.dummy_verify, password)
        throttle.record_failure(principal.actor, source_ip, now)
        return StepUpOutcome(False, "not-enrolled", "mismatch")
    if not await asyncio.to_thread(auth.verify_password, password, str(user["password_hash"])):
        throttle.record_failure(principal.actor, source_ip, now)
        return StepUpOutcome(False, "not-enrolled", "password")
    factor = second_factor(user, code)
    if factor == "failed":
        throttle.record_failure(principal.actor, source_ip, now)
        return StepUpOutcome(False, factor, "second-factor")
    throttle.record_success(principal.actor, source_ip)
    return StepUpOutcome(True, factor)


def refusal(outcome: StepUpOutcome) -> str:
    """The sentence a refused confirmation answers with: the same for every wrong credential."""
    if outcome.reason == "token":
        return TOKEN_REFUSED
    if outcome.reason == "locked":
        return LOCKED
    return REFUSED
