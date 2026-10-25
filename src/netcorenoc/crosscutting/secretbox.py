"""Device credentials at rest: two keys, two ciphers, and no single file that opens one (ADR #455).

The NOC plane keeps what opens equipment — SSH passwords and private keys, SNMP communities and USM
keys, API tokens, SMTP passwords. A sealed personal datum (`vault.py`) answers *"what does a stolen
database give away?"*; a credential must also survive the theft of the key that sealed it. So each
one is sealed **twice, under independent keys, by two different ciphers**:

1. **Inner layer.** The payload under a fresh 256-bit data key, ChaCha20-Poly1305. The data key is
   wrapped with AES-256-GCM under a subkey of the **secrets key** — a second key file,
   ``NETCORENOC_SECRETS_KEY_FILE`` (default ``secrets.key`` in the data directory), created
   owner-only on the first start, like ``secret.key`` and never in the database.
2. **Outer layer.** The whole envelope — wrapped data key and inner ciphertext — under AES-256-GCM
   with a subkey of the appliance's **secret key** (:meth:`vault.Vault.wrap`).

Both layers authenticate the credential's **binding** as associated data: its reference, kind, host,
port, protocol version and access level (`engine/noc/kinds.py::binding`). A row whose host was
rewritten to point a credential at a machine an attacker controls no longer opens, rather than
handing the password over. Opening one takes the row, ``secret.key`` **and** ``secrets.key``; any
two of the three open nothing. The two key files only add up to two walls when they are kept
apart, so :func:`warnings` says so when both sit in one directory.

A third subkey of the secrets key signs the leases agents present instead of a credential
(:meth:`SecretBox.sign`, `engine/noc/lease.py`): a lease never carries secret material, and a forged
one fails here.

Memory: Python cannot promise to erase a string, so a decrypted value lives only for the request
that asked for it (a custodian's reveal), the data key in a ``bytearray`` overwritten after use. It
is never logged, audited or returned to an agent.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM, ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from netcorenoc.crosscutting import vault
from netcorenoc.crosscutting.vault import VaultError

__all__ = [
    "PREFIX",
    "SecretBox",
    "configure",
    "current",
    "install",
    "is_sealed",
    "warnings",
]

log = logging.getLogger("netcorenoc")

PREFIX = "ncs1:"
_KID = 4
_NONCE = 12
_TAG = 16
_DEK = 32
#: The most a sealed payload may hold: a 16 KiB private key and its fields, with room to spare.
MAX_PAYLOAD_BYTES = 64 * 1024


def _subkey(key: bytes, purpose: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=purpose).derive(key)


@dataclass(frozen=True, repr=False)
class SecretBox:
    """The keys derived from the secrets key. ``repr`` is withheld: it would print key material."""

    _wrap_key: bytes
    _lease_key: bytes
    kid: bytes

    @classmethod
    def from_key(cls, key: bytes) -> SecretBox:
        if len(key) != vault.KEY_BYTES:
            raise VaultError(f"a secrets key is {vault.KEY_BYTES} bytes; this one is {len(key)}")
        return cls(
            _wrap_key=_subkey(key, b"netcorenoc credential kek v1"),
            _lease_key=_subkey(key, b"netcorenoc lease v1"),
            kid=_subkey(key, b"netcorenoc secrets key id v1")[:_KID],
        )

    def seal(self, binding: str, payload: dict[str, str]) -> str:
        """``payload`` sealed for ``binding`` under both keys, as ``ncs1:…`` text."""
        data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(data) > MAX_PAYLOAD_BYTES:
            raise VaultError(f"a credential is at most {MAX_PAYLOAD_BYTES} bytes")
        aad = binding.encode("utf-8")
        dek = bytearray(secrets.token_bytes(_DEK))
        try:
            inner_nonce, wrap_nonce = secrets.token_bytes(_NONCE), secrets.token_bytes(_NONCE)
            inner = ChaCha20Poly1305(bytes(dek)).encrypt(inner_nonce, data, b"inner|" + aad)
            wrapped = AESGCM(self._wrap_key).encrypt(wrap_nonce, bytes(dek), b"dek|" + aad)
        finally:
            dek[:] = bytes(_DEK)
        envelope = wrap_nonce + wrapped + inner_nonce + inner
        outer = vault.current().wrap(f"noc_secret|{binding}", envelope)
        return PREFIX + base64.urlsafe_b64encode(self.kid + outer).decode("ascii")

    def open(self, binding: str, token: str) -> dict[str, str]:
        """The payload ``token`` sealed for ``binding``. Raises :class:`VaultError` otherwise —
        never naming the value, and never saying which layer refused beyond the key it needs."""
        if not is_sealed(token):
            raise VaultError("not a sealed credential")
        try:
            raw = base64.urlsafe_b64decode(token[len(PREFIX) :].encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise VaultError("a sealed credential is malformed") from exc
        if not hmac.compare_digest(raw[:_KID], self.kid):
            raise VaultError(
                "a credential sealed with another secrets key — NETCORENOC_SECRETS_KEY_FILE does "
                "not hold the key this vault was written with"
            )
        envelope = vault.current().unwrap(f"noc_secret|{binding}", raw[_KID:])
        wrapped_end = _NONCE + _DEK + _TAG
        if len(envelope) < wrapped_end + _NONCE + _TAG:
            raise VaultError("a sealed credential is truncated")
        aad = binding.encode("utf-8")
        try:
            dek = bytearray(
                AESGCM(self._wrap_key).decrypt(
                    envelope[:_NONCE], envelope[_NONCE:wrapped_end], b"dek|" + aad
                )
            )
            try:
                data = ChaCha20Poly1305(bytes(dek)).decrypt(
                    envelope[wrapped_end : wrapped_end + _NONCE],
                    envelope[wrapped_end + _NONCE :],
                    b"inner|" + aad,
                )
            finally:
                dek[:] = bytes(len(dek))
        except InvalidTag as exc:
            raise VaultError("a sealed credential failed authentication") from exc
        payload = json.loads(data)
        if not isinstance(payload, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in payload.items()
        ):
            raise VaultError("a sealed credential does not hold named text fields")
        return payload

    def sign(self, message: bytes) -> bytes:
        """HMAC-SHA256 of ``message`` under the lease subkey."""
        return hmac.new(self._lease_key, message, hashlib.sha256).digest()

    def verify(self, message: bytes, signature: bytes) -> bool:
        return hmac.compare_digest(self.sign(message), signature)


def is_sealed(value: object) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


_current: SecretBox | None = None
_warnings: list[str] = []


def configure(box: SecretBox | None) -> None:
    """Make ``box`` the process's secrets key (``None`` forgets it: tests)."""
    global _current
    _current = box


def current() -> SecretBox:
    if _current is None:
        raise VaultError(
            "no secrets key is loaded: the appliance loads NETCORENOC_SECRETS_KEY_FILE (or "
            "<data dir>/secrets.key) before it reads or writes a device credential"
        )
    return _current


def warnings() -> list[str]:
    """What an operator should know about where the two keys live. Empty when they are apart."""
    return list(_warnings)


def install(path: Path, *, secret_key: Path) -> SecretBox:
    """Load the secrets key at ``path`` — created on the first start — as the process's.

    ``secret_key`` is where the appliance's other key lives: both in one directory is allowed, and
    is what a copy of that directory needs to open every credential, so it is said (principle 1).
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        key, created = vault.read_key(path)
        box = SecretBox.from_key(key)
    except (OSError, VaultError) as exc:
        raise VaultError(
            f"NETCORENOC_SECRETS_KEY_FILE ({path}): {exc}. See docs/configure.md."
        ) from exc
    configure(box)
    _warnings.clear()
    if path.resolve().parent == secret_key.resolve().parent:
        _warnings.append(
            "The secrets key and the secret key are in the same directory: a copy of it opens "
            "every device credential. Mount NETCORENOC_SECRETS_KEY_FILE from somewhere else "
            "(docs/security.md, the Secrets vault)."
        )
    if created:
        log.warning(
            "created the secrets key %s (key %s): back it up apart from the database and from "
            "secret.key — without it every device credential is unreadable (ADR #455)",
            path,
            box.kid.hex(),
        )
    else:
        log.info("secrets key %s (key %s)", path, box.kid.hex())
    return box
