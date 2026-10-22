"""Secrets at rest: one key the database never sees, and what it protects (ADR #453).

A copy of the database — a stolen backup, a leaked managed-database snapshot, a read-only SQL
injection — must not give an attacker what lets them act elsewhere or tells them who someone is.
Three uses of one key:

* **Sealed values** (:meth:`Vault.seal`, :meth:`Vault.open`): AES-256-GCM, a random 96-bit nonce
  per value, and the value's place (``"app_user.email"``, ``"meta:config.email"``) as associated
  data, so a sealed value copied into another column or row kind does not open. A token reads
  ``nc1:<base64url(key id ‖ nonce ‖ ciphertext ‖ tag)>``; the key id says "wrong key" instead of
  "corrupt data" when the key file is not the one the value was sealed with.
* **Blind indexes** (:meth:`Vault.index`): HMAC-SHA256 of a normalised value, for the lookups a
  sealed value still needs — a recovery address, matched case-insensitively and kept unique.
* **The password pepper** (:meth:`Vault.pepper`): HMAC-SHA256 of a password before scrypt, so the
  hashes in a dump cannot be attacked offline without the key.

**The key** is 32 random bytes, base64, in a file: ``NETCORENOC_SECRET_KEY_FILE``, else
``secret.key`` in the data directory, created owner-only on the first start. It lives beside the
spool, never in the database, so the two are stolen separately or not at all. Each use has its own
subkey (HKDF-SHA256). **Losing the key loses every sealed value and every password**: back it up
apart from the database; ``python -m netcorenoc admin reset-password`` issues new passwords.

The process configures one vault (:func:`configure`) before it touches a sealed value; using one
unconfigured raises rather than inventing a key that the next start would not have.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from netcorenoc.crosscutting.settings import SettingsError

__all__ = [
    "KEY_BYTES",
    "PREFIX",
    "Vault",
    "VaultError",
    "configure",
    "current",
    "install",
    "is_sealed",
    "load_or_create",
]

log = logging.getLogger("netcorenoc")

KEY_BYTES = 32
PREFIX = "nc1:"
_NONCE = 12
_KID = 4


class VaultError(SettingsError):
    """No key, the wrong key, or a sealed value that does not open. Never names the value.

    A :class:`SettingsError`: what fixes it is the key file the process was given."""


def _subkey(key: bytes, purpose: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=purpose).derive(key)


@dataclass(frozen=True, repr=False)
class Vault:
    """The keys derived from one master key. ``repr`` is withheld: it would print key material."""

    _seal_key: bytes
    _index_key: bytes
    _pepper_key: bytes
    kid: bytes

    @classmethod
    def from_key(cls, key: bytes) -> Vault:
        if len(key) != KEY_BYTES:
            raise VaultError(f"a secret key is {KEY_BYTES} bytes; this one is {len(key)}")
        return cls(
            _seal_key=_subkey(key, b"netcorenoc seal v1"),
            _index_key=_subkey(key, b"netcorenoc index v1"),
            _pepper_key=_subkey(key, b"netcorenoc pepper v1"),
            kid=_subkey(key, b"netcorenoc key id v1")[:_KID],
        )

    def seal(self, place: str, value: str | bytes) -> str:
        """``value`` encrypted and authenticated for ``place``, as ``nc1:…`` text."""
        data = value.encode("utf-8") if isinstance(value, str) else bytes(value)
        nonce = secrets.token_bytes(_NONCE)
        sealed = AESGCM(self._seal_key).encrypt(nonce, data, place.encode("utf-8"))
        return PREFIX + base64.urlsafe_b64encode(self.kid + nonce + sealed).decode("ascii")

    def open(self, place: str, token: str) -> bytes:
        """The bytes ``token`` sealed for ``place``. Raises :class:`VaultError` otherwise."""
        if not token.startswith(PREFIX):
            raise VaultError(f"{place}: not a sealed value")
        try:
            raw = base64.urlsafe_b64decode(token[len(PREFIX) :].encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise VaultError(f"{place}: a sealed value is malformed") from exc
        if len(raw) < _KID + _NONCE + 16:
            raise VaultError(f"{place}: a sealed value is truncated")
        if not hmac.compare_digest(raw[:_KID], self.kid):
            raise VaultError(
                f"{place}: sealed with another secret key — NETCORENOC_SECRET_KEY_FILE does not "
                "hold the key this database was written with"
            )
        nonce, sealed = raw[_KID : _KID + _NONCE], raw[_KID + _NONCE :]
        try:
            return AESGCM(self._seal_key).decrypt(nonce, sealed, place.encode("utf-8"))
        except InvalidTag as exc:
            raise VaultError(f"{place}: a sealed value failed authentication") from exc

    def open_text(self, place: str, token: str) -> str:
        return self.open(place, token).decode("utf-8")

    def index(self, place: str, value: str) -> str:
        """A keyed, deterministic digest of ``value`` for ``place``: equal values, equal digests."""
        message = place.encode("utf-8") + b"\x00" + value.encode("utf-8")
        return hmac.new(self._index_key, message, hashlib.sha256).hexdigest()

    def pepper(self, password: str) -> bytes:
        """The password as scrypt receives it: keyed, so a dump alone cannot test guesses."""
        return hmac.new(self._pepper_key, password.encode("utf-8"), hashlib.sha256).digest()


def is_sealed(value: object) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


_current: Vault | None = None


def configure(vault: Vault | None) -> None:
    """Make ``vault`` the process's vault (``None`` forgets it: tests)."""
    global _current
    _current = vault


def current() -> Vault:
    if _current is None:
        raise VaultError(
            "no secret key is loaded: the appliance and its CLI load NETCORENOC_SECRET_KEY_FILE "
            "(or <data dir>/secret.key) before they read or write a sealed value"
        )
    return _current


def load_or_create(path: Path) -> tuple[Vault, bool]:
    """The vault for the key in ``path``, creating the file (owner-only) when there is none.

    Returns ``(vault, created)``. A key file any user may read is refused: whoever reads it reads
    every sealed value. A group bit is allowed, for a secret mounted with a group (``0440``).
    """
    created = False
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            handle.write(base64.b64encode(secrets.token_bytes(KEY_BYTES)).decode("ascii") + "\n")
        created = True
    mode = path.stat().st_mode
    if mode & stat.S_IRWXO:
        raise VaultError(
            f"{path} may be read by other users (mode {stat.S_IMODE(mode):o}); "
            f"chmod 600 (or 440) it — whoever reads it reads every sealed value"
        )
    text = path.read_text(encoding="ascii").strip()
    try:
        key = base64.b64decode(text, validate=True)
    except ValueError as exc:
        raise VaultError(f"{path} does not hold a base64 secret key") from exc
    return Vault.from_key(key), created


def install(path: Path) -> Vault:
    """Load the key file at ``path`` — created on the first start — as the process's vault.

    Logs the key's identifier, and on creation a warning to back the file up apart from the
    database. Every failure, the file system's included, is a :class:`VaultError` naming the file.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        keyring, created = load_or_create(path)
    except (OSError, VaultError) as exc:
        raise VaultError(
            f"NETCORENOC_SECRET_KEY_FILE ({path}): {exc}. See docs/configure.md."
        ) from exc
    configure(keyring)
    if created:
        log.warning(
            "created the secret key %s (key %s): back it up apart from the database — without "
            "it, sealed personal data and every password are unreadable (ADR #453)",
            path,
            keyring.kid.hex(),
        )
    else:
        log.info("secret key %s (key %s)", path, keyring.kid.hex())
    return keyring
