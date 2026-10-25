"""The lease an agent presents instead of a credential (ADR #458).

An agent never receives a password, a key or a community. It asks for a **lease** on one credential
and gets a compact JWS — a JWT signed HS256 under a subkey of the secrets key
(`crosscutting/secretbox.py`) — that **carries no secret material at all**: which credential, which
host and port, what access level, who it was issued to, when it expires, and a random id. Whatever
acts on the device for the agent (the executor, next) checks the lease, opens the seal itself and
keeps the secret on its side; the agent only ever holds the lease.

Why each property is there:

* **Signed, and only HS256.** The header is compared whole: ``alg: none``, an RS/HS confusion, or
  any algorithm but the one this appliance signs with is refused before the signature is read.
* **Short-lived**: 15 minutes by default, one hour at most, with 30 s of clock leeway.
* **Bound to the agent** (``sub``, the token's row identity): a lease copied out of an agent's
  context is useless to anyone not also holding that agent's bearer token.
* **Bound to one credential** (``sid`` and its ``ref``) and to its ``ver``: changing the
  credential — a new password, a new host — or switching agent access off ends its leases.
* **Revocable**: the store keeps a SHA-256 of each lease's id, and an introspection refuses a lease
  whose row is revoked, expired or gone.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from dataclasses import asdict, dataclass
from typing import Any

from netcorenoc.crosscutting import secretbox

__all__ = [
    "AUDIENCE",
    "DEFAULT_TTL_S",
    "ISSUER",
    "MAX_TTL_S",
    "MIN_TTL_S",
    "Claims",
    "LeaseError",
    "decode",
    "digest",
    "issue",
    "new_id",
]

ISSUER = "netcorenoc"
AUDIENCE = "netcorenoc-noc-executor"
DEFAULT_TTL_S = 900
MIN_TTL_S = 60
MAX_TTL_S = 3600
LEEWAY_S = 30
MAX_LEASE_CHARS = 4096
_HEADER = {"alg": "HS256", "typ": "JWT"}


class LeaseError(ValueError):
    """A lease that is not valid. The message says why in a word an agent can act on."""


@dataclass(frozen=True)
class Claims:
    jti: str
    sub: str  # the agent: "token:<id>"
    sid: int  # the credential's row id
    ref: str  # the credential's reference, bound into its seal
    ver: int  # the credential's version when the lease was issued
    kind: str
    host: str
    port: int
    scope: str  # the access level
    iat: int
    nbf: int
    exp: int
    iss: str = ISSUER
    aud: str = AUDIENCE


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def new_id() -> str:
    """192 random bits."""
    return secrets.token_urlsafe(24)


def digest(jti: str) -> str:
    """What the store keeps of a lease id."""
    return hashlib.sha256(jti.encode("ascii")).hexdigest()


def issue(claims: Claims) -> str:
    """The signed lease. Its value is shown to the agent once and kept nowhere."""
    header = _b64(json.dumps(_HEADER, separators=(",", ":"), sort_keys=True).encode("ascii"))
    payload = _b64(json.dumps(asdict(claims), separators=(",", ":"), sort_keys=True).encode())
    signing_input = f"{header}.{payload}".encode("ascii")
    return f"{header}.{payload}.{_b64(secretbox.current().sign(signing_input))}"


def decode(lease: str, now: float) -> Claims:
    """The claims of a valid lease, or :class:`LeaseError`. Checks the signature, the algorithm,
    the issuer, the audience and the clock — **not** revocation, which is the store's row."""
    if not lease or len(lease) > MAX_LEASE_CHARS or lease.count(".") != 2:
        raise LeaseError("malformed")
    header_b64, payload_b64, signature_b64 = lease.split(".")
    try:
        header = json.loads(_unb64(header_b64))
        signature = _unb64(signature_b64)
    except (ValueError, UnicodeDecodeError) as exc:
        raise LeaseError("malformed") from exc
    if header != _HEADER:
        raise LeaseError("unsupported algorithm")
    if not secretbox.current().verify(f"{header_b64}.{payload_b64}".encode("ascii"), signature):
        raise LeaseError("bad signature")
    try:
        body: dict[str, Any] = json.loads(_unb64(payload_b64))
        claims = Claims(**body)
    except (ValueError, TypeError, UnicodeDecodeError) as exc:
        raise LeaseError("malformed") from exc
    if claims.iss != ISSUER or claims.aud != AUDIENCE:
        raise LeaseError("wrong issuer or audience")
    if now > claims.exp + LEEWAY_S:
        raise LeaseError("expired")
    if now + LEEWAY_S < claims.nbf:
        raise LeaseError("not yet valid")
    return claims
