"""What the trap receiver accepts: SNMP versions, v1/v2c communities and SNMPv3 users (v0.30.0).

**The default accepts every v1 and v2c trap and every v3 trap from a configured user** — the
zero-configuration promise this appliance makes, unchanged: point equipment at it and traps arrive.
An admin narrows it from Settings → SNMP, and the policy is one immutable :class:`SnmpPolicy` the
receiver swaps by attribute assignment, so a change reaches the datagram path without a lock.

Nothing secret is kept in the clear (F4's rule, extended):

* a **community** is stored as the HMAC the receiver already computes for its tag, under the
  per-install key, with a hint (its first character and its length) so an admin can tell entries
  apart. The receiver compares HMACs; the community itself is never written anywhere;
* an **SNMPv3 passphrase** is stored as its RFC 3414 master key — the 1 MiB hash the protocol
  derives every localized key from. That is what Net-SNMP persists too: it authenticates SNMP, but
  it does not give back the passphrase, which people reuse.

Parsing a stored document never raises; :func:`policy_problems` says what is wrong with one, and the
runner refuses to start on a stored document that has problems rather than guessing (the
allowlist's rule, F69).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from netcorenoc.crosscutting.settings import SettingsError

#: The meta key the policy is stored under. One JSON document, versioned.
META_KEY = "config.snmp"
#: What an operator runs when a stored policy keeps the appliance from starting.
CLEAR_COMMAND = "sqlite3 <NETCORENOC_DB> \"DELETE FROM meta WHERE key='config.snmp'\""
DOCUMENT_VERSION = 1

#: RFC 3414 §11.2: a passphrase shorter than eight characters is refused.
MIN_PASSPHRASE = 8
MAX_PASSPHRASE = 128
#: SnmpAdminString (SIZE (1..32)) for a user name, and RFC 3411's 5 to 32 octets for an engine ID.
MAX_USER_NAME = 32
ENGINE_ID_BYTES = (5, 32)
#: Bounds an admin cannot exceed, so a stored policy cannot grow the receiver's memory.
MAX_COMMUNITIES = 64
MAX_USERS = 64
MAX_COMMUNITY_CHARS = 64

Hasher = Callable[..., Any]

#: Authentication protocol → (hash, truncated MAC length). RFC 3414 (MD5, SHA-1), RFC 7860 (SHA-2).
AUTH_PROTOCOLS: dict[str, tuple[Hasher, int]] = {
    "MD5": (hashlib.md5, 12),
    "SHA": (hashlib.sha1, 12),
    "SHA-224": (hashlib.sha224, 16),
    "SHA-256": (hashlib.sha256, 24),
    "SHA-384": (hashlib.sha384, 32),
    "SHA-512": (hashlib.sha512, 48),
}
#: Privacy protocols. `-C` is the Reeder key extension Cisco uses for AES-192/256; the plain names
#: are the Blumenthal extension Net-SNMP uses. The names match what equipment configuration says.
PRIV_PROTOCOLS: tuple[str, ...] = (
    "DES",
    "3DES",
    "AES-128",
    "AES-192",
    "AES-256",
    "AES-192-C",
    "AES-256-C",
)
NONE = "none"
#: Said beside a protocol the console offers but discourages.
WEAK = frozenset({"MD5", "DES"})

_USER_NAME = re.compile(r"^[A-Za-z0-9._@-]{1,32}$")
_HEX = re.compile(r"^(?:0x)?([0-9A-Fa-f]{10,64})$")


@dataclass(frozen=True)
class UsmUser:
    """One SNMPv3 user the receiver accepts traps from. Keys are RFC 3414 master keys (Ku)."""

    name: str
    auth: str = NONE
    priv: str = NONE
    auth_key: bytes = b""
    priv_key: bytes = b""
    engine_id: bytes | None = None

    @property
    def level(self) -> int:
        """0 noAuthNoPriv, 1 authNoPriv, 2 authPriv — comparable with a message's flags."""
        return 0 if self.auth == NONE else 1 if self.priv == NONE else 2


@dataclass(frozen=True)
class Community:
    """An accepted community: its keyed hash and a hint an admin can recognise it by."""

    digest: str
    hint: str


@dataclass(frozen=True)
class SnmpPolicy:
    """Everything the receiver needs to decide what to accept. Immutable; replaced, never edited."""

    v1: bool = True
    v2c: bool = True
    v3: bool = True
    #: Empty means *any community* — the zero-configuration default.
    communities: tuple[Community, ...] = ()
    users: Mapping[bytes, UsmUser] = field(default_factory=dict)
    #: RFC 3414 §3.2.7 replay protection: refuse a v3 trap older than the sender's clock by 150 s.
    time_window: bool = True

    @property
    def digests(self) -> frozenset[str]:
        return frozenset(c.digest for c in self.communities)


DEFAULT_POLICY = SnmpPolicy()


def master_key(secret: bytes, auth: str) -> bytes:
    """RFC 3414 A.2: Ku, the hash of `secret` repeated to 1 MiB with the user's auth hash."""
    hasher = AUTH_PROTOCOLS[auth][0](b"")
    hasher.update((secret * (1_048_576 // len(secret) + 1))[:1_048_576])
    digest: bytes = hasher.digest()
    return digest


def hash_passphrase(passphrase: str, auth: str) -> bytes:
    """The master key of a passphrase as an admin typed it."""
    return master_key(passphrase.encode("utf-8"), auth)


def community_hint(community: str) -> str:
    """First character and length: enough to tell two entries apart, not enough to read one."""
    return f"{community[:1]}{'•' * min(len(community) - 1, 7)} ({len(community)})"


def parse_engine_id(text: str | None) -> bytes | None:
    """Hex, with or without `0x`, 5 to 32 octets; None when empty. ValueError names the rule."""
    if text is None or not text.strip():
        return None
    match = _HEX.match(text.strip().replace(":", ""))
    if match is None or len(match.group(1)) % 2:
        raise ValueError(
            "an engine ID is 5 to 32 octets written in hex, for example 80001f8880e9630000d61ff449"
        )
    return bytes.fromhex(match.group(1))


def user_problems(user: UsmUser) -> list[str]:
    """What is wrong with one user, in words an admin can act on."""
    problems: list[str] = []
    if not _USER_NAME.match(user.name):
        problems.append(
            f"user name {user.name!r} must be 1 to {MAX_USER_NAME} letters, digits or . _ @ -"
        )
    if user.auth != NONE and user.auth not in AUTH_PROTOCOLS:
        problems.append(f"unknown authentication protocol {user.auth!r}")
    if user.priv != NONE and user.priv not in PRIV_PROTOCOLS:
        problems.append(f"unknown privacy protocol {user.priv!r}")
    if user.priv != NONE and user.auth == NONE:
        problems.append(f"user {user.name!r}: privacy needs authentication (authPriv)")
    if user.auth != NONE and not user.auth_key:
        problems.append(f"user {user.name!r}: an authentication passphrase is required")
    if user.priv != NONE and not user.priv_key:
        problems.append(f"user {user.name!r}: a privacy passphrase is required")
    if user.engine_id is not None and not (
        ENGINE_ID_BYTES[0] <= len(user.engine_id) <= ENGINE_ID_BYTES[1]
    ):
        problems.append(f"user {user.name!r}: an engine ID is 5 to 32 octets")
    return problems


def policy_problems(policy: SnmpPolicy) -> list[str]:
    """Usability and safety at write time: a policy that would accept nothing is refused."""
    problems: list[str] = []
    if not (policy.v1 or policy.v2c or policy.v3):
        problems.append("at least one SNMP version must be accepted, or no trap would arrive")
    if len(policy.communities) > MAX_COMMUNITIES:
        problems.append(f"at most {MAX_COMMUNITIES} communities")
    if len(policy.users) > MAX_USERS:
        problems.append(f"at most {MAX_USERS} SNMPv3 users")
    for user in policy.users.values():
        problems.extend(user_problems(user))
    return problems


def to_document(policy: SnmpPolicy) -> str:
    """The stored form: canonical JSON, keys as hex."""
    return json.dumps(
        {
            "version": DOCUMENT_VERSION,
            "v1": policy.v1,
            "v2c": policy.v2c,
            "v3": policy.v3,
            "time_window": policy.time_window,
            "communities": [{"digest": c.digest, "hint": c.hint} for c in policy.communities],
            "users": [
                {
                    "name": u.name,
                    "auth": u.auth,
                    "priv": u.priv,
                    "auth_key": u.auth_key.hex(),
                    "priv_key": u.priv_key.hex(),
                    "engine_id": u.engine_id.hex() if u.engine_id else None,
                }
                for u in sorted(policy.users.values(), key=lambda u: u.name)
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def from_document(text: str) -> tuple[SnmpPolicy, list[str]]:
    """Parse a stored document. **Never raises**: a bad one returns the default and its problems."""
    try:
        raw = json.loads(text)
        if not isinstance(raw, dict) or raw.get("version") != DOCUMENT_VERSION:
            raise ValueError("not a version-1 SNMP policy document")
        users = {}
        for item in raw.get("users") or []:
            user = UsmUser(
                name=str(item["name"]),
                auth=str(item.get("auth", NONE)),
                priv=str(item.get("priv", NONE)),
                auth_key=bytes.fromhex(item.get("auth_key") or ""),
                priv_key=bytes.fromhex(item.get("priv_key") or ""),
                engine_id=bytes.fromhex(item["engine_id"]) if item.get("engine_id") else None,
            )
            users[user.name.encode("utf-8")] = user
        policy = SnmpPolicy(
            v1=bool(raw.get("v1", True)),
            v2c=bool(raw.get("v2c", True)),
            v3=bool(raw.get("v3", True)),
            time_window=bool(raw.get("time_window", True)),
            communities=tuple(
                Community(digest=str(c["digest"]), hint=str(c.get("hint", "")))
                for c in raw.get("communities") or []
            ),
            users=users,
        )
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return DEFAULT_POLICY, [f"the stored SNMP policy could not be read ({exc})"]
    return policy, policy_problems(policy)


def stored_policy(stored: str | None) -> SnmpPolicy:
    """The policy the receiver starts with: the stored one, or the default when none is stored.

    A stored document with problems **refuses to start**, naming the way out, for the allowlist's
    reason (F69): the screen that would fix it is served by the appliance that will not start.
    """
    if stored is None:
        return DEFAULT_POLICY
    policy, problems = from_document(stored)
    if problems:
        raise SettingsError(
            f"the stored SNMP receiver policy is not usable: {'; '.join(problems)}. Clear it and "
            f"the default (every v1/v2c trap, no SNMPv3 user) applies again:  {CLEAR_COMMAND}"
        )
    return policy


def describe(policy: SnmpPolicy) -> str:
    """One line for the start-up log: what the receiver accepts, never a secret or a hint."""
    versions = "/".join(
        name for name, on in (("v1", policy.v1), ("v2c", policy.v2c), ("v3", policy.v3)) if on
    )
    communities = f"{len(policy.communities)} accepted" if policy.communities else "any"
    return f"{versions}; communities: {communities}; {len(policy.users)} SNMPv3 user(s)"
