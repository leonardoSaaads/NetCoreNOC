"""Two-factor sign-in: time-based one-time codes (RFC 6238) and recovery codes (ADR #482).

Every person signs in with a password **and** a six-digit code from an authenticator app (Google
Authenticator, Authy, 1Password, Microsoft Authenticator, any app that reads an ``otpauth://``
QR code). The appliance and the app share a 160-bit secret; each derives the same code from it
and the current 30-second step (HOTP, RFC 4226, over the step number).

* **The secret** is made here (:func:`new_secret`), shown once while the person enrols — as a QR
  code and as text — and stored sealed under the appliance's key (``vault.seal``), never in clear.
* **A code is accepted once.** The step a code was accepted for is recorded, and a code for that
  step or an earlier one is refused (:data:`REPLAY_GUARD`): a code read over a shoulder or from a
  log cannot be replayed within its 30 seconds. One step either side of now is accepted, for a
  phone whose clock is a little off.
* **Recovery codes** (:func:`new_recovery_codes`) are ten single-use codes the person writes down
  at enrolment, for the day the phone is lost. They are stored as keyed digests
  (:func:`recovery_digest`, an HMAC under the appliance's key), so a copy of the database does not
  hold them. Each works once.

Pure apart from the randomness and the vault's key.
"""

from __future__ import annotations

import base64
import hmac
import secrets
from urllib.parse import quote, urlencode

from netcorenoc.crosscutting import vault

__all__ = [
    "DIGITS",
    "ISSUER",
    "PERIOD_S",
    "RECOVERY_CODES",
    "REPLAY_GUARD",
    "WINDOW",
    "code_at",
    "is_recovery_shape",
    "matching_step",
    "new_recovery_codes",
    "new_secret",
    "normalise_recovery",
    "provisioning_uri",
    "recovery_digest",
    "step_at",
]

#: A code is six digits, renewed every 30 seconds: what every authenticator app shows by default.
DIGITS = 6
PERIOD_S = 30
#: Steps either side of now that are still accepted (a phone clock up to 30 s off).
WINDOW = 1
#: The name the authenticator app shows above the code.
ISSUER = "NetCoreNOC"
#: How many recovery codes an enrolment hands out.
RECOVERY_CODES = 10
#: A code is refused for a step at or before the last one accepted. **On in production**, asserted
#: by a policy test; the test suite turns it off so a role can sign in twice in one 30-second step.
REPLAY_GUARD = True

_SECRET_BYTES = 20  # 160 bits, RFC 4226's recommendation
_RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o, 1/l/i
_RECOVERY_HALF = 5
_RECOVERY_PLACE = "app_user.recovery_code"


def new_secret() -> str:
    """A fresh shared secret, base32 without padding (what an ``otpauth://`` link carries)."""
    return base64.b32encode(secrets.token_bytes(_SECRET_BYTES)).decode("ascii").rstrip("=")


def _key(secret: str) -> bytes:
    text = secret.strip().replace(" ", "").upper()
    return base64.b32decode(text + "=" * (-len(text) % 8))


def step_at(now: float) -> int:
    """The 30-second step ``now`` falls in."""
    return int(now // PERIOD_S)


def code_at(secret: str, step: int) -> str:
    """The code for ``step`` (HOTP over the step, RFC 4226 §5.3)."""
    mac = hmac.new(_key(secret), step.to_bytes(8, "big"), "sha1").digest()
    offset = mac[-1] & 0x0F
    value = int.from_bytes(mac[offset : offset + 4], "big") & 0x7FFF_FFFF
    return str(value % 10**DIGITS).zfill(DIGITS)


def _digits(code: str) -> str | None:
    cleaned = "".join(code.split()).replace("-", "")
    return cleaned if len(cleaned) == DIGITS and cleaned.isdigit() else None


def matching_step(secret: str, code: str, now: float, *, last_step: int = -1) -> int | None:
    """The step ``code`` is valid for, within :data:`WINDOW` of now and after ``last_step``, or
    ``None``. Each candidate is compared in constant time."""
    given = _digits(code)
    if given is None:
        return None
    current = step_at(now)
    found: int | None = None
    for step in range(current - WINDOW, current + WINDOW + 1):
        if hmac.compare_digest(code_at(secret, step), given) and found is None:
            found = step
    if found is None:
        return None
    if REPLAY_GUARD and found <= last_step:
        return None
    return found


def provisioning_uri(secret: str, account: str) -> str:
    """The ``otpauth://`` link an authenticator app reads from the QR code."""
    label = quote(f"{ISSUER}:{account}", safe=":@")
    return f"otpauth://totp/{label}?" + urlencode({"secret": secret, "issuer": ISSUER})


def new_recovery_codes(count: int = RECOVERY_CODES) -> list[str]:
    """``count`` single-use codes like ``k7m3p-x9qrt`` (about 49 bits each)."""

    def half() -> str:
        return "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(_RECOVERY_HALF))

    return [f"{half()}-{half()}" for _ in range(count)]


def normalise_recovery(code: str) -> str:
    """A recovery code as typed (any case, spaces, with or without the dash) in its one form."""
    cleaned = "".join(code.split()).replace("-", "").lower()
    return f"{cleaned[:_RECOVERY_HALF]}-{cleaned[_RECOVERY_HALF:]}" if cleaned else ""


def is_recovery_shape(code: str) -> bool:
    """Does ``code`` look like a recovery code rather than a six-digit one?"""
    cleaned = normalise_recovery(code).replace("-", "")
    return len(cleaned) == 2 * _RECOVERY_HALF and all(c in _RECOVERY_ALPHABET for c in cleaned)


def recovery_digest(code: str) -> str:
    """The keyed digest a recovery code is stored and looked up by."""
    return vault.current().index(_RECOVERY_PLACE, normalise_recovery(code))
