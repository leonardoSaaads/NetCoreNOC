"""The sign-in's second step: an authenticator code, or enrolling an authenticator (ADR #482).

``POST /api/login`` checks the password and, when it is right, opens **no session**: it answers
with a short-lived challenge (:func:`begin`). ``POST /api/login/code`` answers the challenge with a
code and only then is a session opened (:func:`finish`).

* **An enrolled person** (``verify``) gives the six-digit code from the authenticator app, or one
  of their recovery codes when the phone is gone. A recovery code works once.
* **A person without an authenticator** (``enroll``) — every account the first time, and anyone an
  admin reset — is shown a new secret as a QR code and as text, plus ten recovery codes. The first
  code the app shows proves it reads the secret, and only then does the secret become theirs: a
  challenge abandoned half-way enrols nothing.

**Limits.** A challenge lives :data:`CHALLENGE_TTL_S` and takes :data:`MAX_ATTEMPTS` wrong codes,
then it is gone and the password is asked again. Wrong codes also count against a throttle of their
own (per username and per address, exponential to fifteen minutes), which **a right password does
not reset** — so knowing the password does not buy unlimited guesses at the code.

The handle the browser holds is random (256 bits); the database keeps only its SHA-256.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from netcorenoc.crosscutting import auth, qrcode, totp

if TYPE_CHECKING:
    from netcorenoc.store import Store

__all__ = ["CHALLENGE_TTL_S", "MAX_ATTEMPTS", "Begun", "Finished", "begin", "finish"]

#: How long the second step waits for its code: long enough to install an app and scan.
CHALLENGE_TTL_S = 10 * 60.0
#: Wrong codes one challenge takes before the password is asked again.
MAX_ATTEMPTS = 5


def _grouped(secret: str) -> str:
    """``ABCDEFGH…`` as ``ABCD EFGH …``: easier to type into an app by hand."""
    return " ".join(secret[i : i + 4] for i in range(0, len(secret), 4))


@dataclass(frozen=True)
class Begun:
    """The answer to a right password: what the second step needs."""

    purpose: str  # "verify" | "enroll"
    challenge: str
    account: str
    secret: str | None = None
    recovery_codes: list[str] = field(default_factory=list)

    def response(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "second_factor": self.purpose,
            "challenge": self.challenge,
            "expires_in": int(CHALLENGE_TTL_S),
            "digits": totp.DIGITS,
        }
        if self.secret is not None:
            uri = totp.provisioning_uri(self.secret, self.account)
            out["enrollment"] = {
                "issuer": totp.ISSUER,
                "account": self.account,
                "secret": _grouped(self.secret),
                "uri": uri,
                "qr": ["".join("1" if dark else "0" for dark in row) for row in qrcode.encode(uri)],
                "recovery_codes": self.recovery_codes,
                "period": totp.PERIOD_S,
            }
        return out


async def begin(
    store: Store, user_id: int, username: str, source_ip: str | None, now: float
) -> Begun:
    """The challenge after a right password. The caller holds the write lock and commits."""
    handle = auth.new_session_token()
    expires = now + CHALLENGE_TTL_S
    if await store.second_factor(user_id) is not None:
        await store.create_login_challenge(
            auth.hash_token(handle), user_id, "verify", now, expires, source_ip
        )
        return Begun("verify", handle, username)
    secret = totp.new_secret()
    codes = totp.new_recovery_codes()
    await store.create_login_challenge(
        auth.hash_token(handle),
        user_id,
        "enroll",
        now,
        expires,
        source_ip,
        secret=secret,
        recovery_digests=[totp.recovery_digest(c) for c in codes],
    )
    return Begun("enroll", handle, username, secret=secret, recovery_codes=codes)


@dataclass(frozen=True)
class Finished:
    """How a code went. ``status``: ``ok``, ``wrong``, ``expired`` or ``locked``."""

    status: str
    username: str | None = None
    user_id: int | None = None
    principal: auth.Principal | None = None
    session_token: str | None = None
    #: How the person proved it: ``totp``, ``recovery`` or ``enrolled``.
    method: str | None = None
    #: Wrong codes this challenge still takes (``wrong`` only).
    remaining: int = 0
    #: Unused recovery codes left after this sign-in (``recovery`` only).
    recovery_left: int | None = None


async def _accepts(store: Store, row: dict[str, Any], code: str, now: float) -> str | None:
    uid = int(row["user_id"])
    if row["purpose"] == "enroll":
        step = totp.matching_step(str(row["secret"]), code, now)
        if step is None:
            return None
        await store.enroll_second_factor(uid, str(row["secret"]), step, row["recovery"], now)
        return "enrolled"
    stored = await store.second_factor_secret(uid)
    if stored is None:
        return None
    secret, last_step = stored
    step = totp.matching_step(secret, code, now, last_step=last_step)
    if step is not None:
        used = await store.second_factor_use(uid, step, now)
        return "totp" if used or not totp.REPLAY_GUARD else None
    if totp.is_recovery_shape(code) and await store.take_recovery_code(
        uid, totp.recovery_digest(code), now
    ):
        return "recovery"
    return None


async def finish(
    store: Store,
    throttle: auth.LoginThrottle,
    *,
    challenge: str,
    code: str,
    source_ip: str,
    now: float,
) -> Finished:
    """Check ``code`` against the challenge and, when right, open the session. The caller holds
    the write lock, audits the outcome and commits."""
    handle = auth.hash_token(challenge)
    row = await store.login_challenge(handle, now)
    if row is None or row["disabled"]:
        return Finished("expired")
    username, uid = str(row["username"]), int(row["user_id"])
    if throttle.locked_for(username, source_ip, now) > 0:
        return Finished("locked", username, uid)
    method = await _accepts(store, row, code, now)
    if method is None:
        throttle.record_failure(username, source_ip, now)
        attempts = await store.login_challenge_failed(handle)
        if attempts >= MAX_ATTEMPTS:
            await store.delete_login_challenge(handle)
        return Finished("wrong", username, uid, remaining=max(0, MAX_ATTEMPTS - attempts))
    throttle.record_success(username, source_ip)
    await store.delete_login_challenge(handle)
    token = await auth.open_session(store, uid, source_ip, now)
    principal = auth.Principal(actor=username, role=str(row["role"]), kind="session", user_id=uid)
    left = None
    if method == "recovery":
        factor = await store.second_factor(uid)
        left = int(factor["recovery_left"]) if factor else 0
    return Finished("ok", username, uid, principal, token, method, recovery_left=left)
