"""SNMPv3 traps: the User-based Security Model on the receiving side (v0.30.0).

RFC 3412 (message format), RFC 3414 (USM, HMAC-MD5/SHA, DES), RFC 3826 (AES-128), RFC 7860
(HMAC-SHA-2), and the AES-192/256 and 3DES drafts equipment ships (Blumenthal, Reeder).

**A trap's sender is the authoritative engine**, so the keys are localized to the engine ID the
message carries. That is what makes this plug-and-play where `snmptrapd` is not: an admin enters a
user's passphrases once and every device configured with that user is accepted, whatever its
engine ID — unless the user is pinned to one. Localized keys are cached per (user, engine),
bounded.

**Cost on the datagram path**: one HMAC over the message (C, in `hashlib`), one AES or DES
decryption when the message is private (C, in `cryptography`), and the BER decode every trap
already pays. No lock, no I/O, no await — `receiver.datagram_received` calls this exactly as it
calls the v2c parser.

**Privacy needs `cryptography`**, the one optional package this appliance can use (DECISIONS #444):
the core stays five runtime dependencies, the Docker image includes it, and without it an encrypted
trap is quarantined as `v3-privacy-unavailable` — never silently dropped.
"""

from __future__ import annotations

import hmac
import importlib
import math
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from pyasn1.codec.ber import decoder
from pysnmp.proto.mpmod.rfc3412 import ScopedPDU

from netcorenoc.ingest import ber
from netcorenoc.ingest.snmpconf import AUTH_PROTOCOLS, NONE, SnmpPolicy, UsmUser, master_key

USM_SECURITY_MODEL = 3
FLAG_AUTH, FLAG_PRIV = 0x01, 0x02
#: RFC 3414 §2.2.3: a message more than 150 s behind the sender's clock is a replay.
TIME_WINDOW_S = 150
MAX_BOOTS = 2_147_483_647
#: Per-engine clocks and per-(user, engine) localized keys kept; least recently used go first.
CACHE_ENTRIES = 4096
_KEY_SIZE = {
    "DES": 16,
    "3DES": 32,
    "AES-128": 16,
    "AES-192": 24,
    "AES-256": 32,
    "AES-192-C": 24,
    "AES-256-C": 32,
}
_REEDER = frozenset({"3DES", "AES-192-C", "AES-256-C"})


class UsmError(Exception):
    """A v3 message refused, with the quarantine reason an operator reads."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Header:
    """The parts of an SNMPv3 message USM reads, with where the MAC sits in the bytes."""

    flags: int
    engine_id: bytes
    boots: int
    engine_time: int
    user: bytes
    auth_params: bytes
    auth_offset: int
    priv_params: bytes
    data_tag: int
    data: bytes


def parse_header(data: bytes) -> Header:
    """Walk the message's BER by hand, because the MAC check needs byte offsets, not values."""
    try:
        outer = ber.tlv(data, 0, 0x30)
        version = ber.tlv(data, outer.start, 0x02)
        if ber.integer(data, version) != 3:
            raise UsmError("v3-malformed")
        globl = ber.tlv(data, version.end, 0x30)
        msg_id = ber.tlv(data, globl.start, 0x02)
        max_size = ber.tlv(data, msg_id.end, 0x02)
        flags = ber.tlv(data, max_size.end, 0x04)
        model = ber.tlv(data, flags.end, 0x02)
        if ber.integer(data, model) != USM_SECURITY_MODEL:
            raise UsmError("v3-unsupported-security-model")
        params = ber.tlv(data, globl.end, 0x04)
        usm = ber.tlv(data, params.start, 0x30)
        engine = ber.tlv(data, usm.start, 0x04)
        boots = ber.tlv(data, engine.end, 0x02)
        clock = ber.tlv(data, boots.end, 0x02)
        user = ber.tlv(data, clock.end, 0x04)
        auth = ber.tlv(data, user.end, 0x04)
        priv = ber.tlv(data, auth.end, 0x04)
        body = ber.tlv(data, params.end)
        boots_value, time_value = ber.integer(data, boots), ber.integer(data, clock)
    except ber.BerError as exc:
        raise UsmError("v3-malformed") from exc
    if body.tag not in (0x30, 0x04) or flags.end - flags.start != 1:
        raise UsmError("v3-malformed")
    if not (0 <= boots_value <= MAX_BOOTS and 0 <= time_value <= MAX_BOOTS):
        raise UsmError("v3-malformed")
    return Header(
        flags=data[flags.start],
        engine_id=data[engine.start : engine.end],
        boots=boots_value,
        engine_time=time_value,
        user=data[user.start : user.end],
        auth_params=data[auth.start : auth.end],
        auth_offset=auth.start,
        priv_params=data[priv.start : priv.end],
        data_tag=body.tag,
        data=data[body.start : body.end] if body.tag == 0x04 else data[body.offset : body.end],
    )


def localize(master: bytes, engine_id: bytes, auth: str) -> bytes:
    """RFC 3414 A.2: Kul = H(Ku || engineID || Ku)."""
    digest: bytes = AUTH_PROTOCOLS[auth][0](master + engine_id + master).digest()
    return digest


def privacy_key(user: UsmUser, engine_id: bytes) -> bytes:
    """The localized privacy key, extended to the cipher's size by the scheme its name implies."""
    hash_name = user.auth
    key = localize(user.priv_key, engine_id, hash_name)
    size = _KEY_SIZE[user.priv]
    if user.priv in _REEDER:
        while len(key) < size:  # Reeder: repeat password-to-key over the key so far
            key += localize(master_key(key, hash_name), engine_id, hash_name)
    else:
        for _ in range(1, math.ceil(size / len(key))):  # Blumenthal: append H(key so far)
            key += AUTH_PROTOCOLS[hash_name][0](key).digest()
    return key[:size]


class UsmState:
    """What the receiver remembers between v3 messages: keys and each sender's clock. Bounded."""

    def __init__(self) -> None:
        self._keys: OrderedDict[tuple[str, bytes, bytes, bytes], tuple[bytes, bytes]] = (
            OrderedDict()
        )
        self._clocks: OrderedDict[bytes, tuple[int, int, float]] = OrderedDict()

    def keys(self, user: UsmUser, engine_id: bytes) -> tuple[bytes, bytes]:
        """(authentication key, privacy key), localized to `engine_id` once and then cached."""
        slot = (user.name, user.auth_key, user.priv_key, engine_id)
        found = self._keys.get(slot)
        if found is None:
            auth = localize(user.auth_key, engine_id, user.auth) if user.auth != NONE else b""
            priv = privacy_key(user, engine_id) if user.priv != NONE else b""
            found = (auth, priv)
            self._keys[slot] = found
            if len(self._keys) > CACHE_ENTRIES:
                self._keys.popitem(last=False)
        else:
            self._keys.move_to_end(slot)
        return found

    def in_window(self, engine_id: bytes, boots: int, engine_time: int, now: float) -> bool:
        """RFC 3414 §3.2.7 for a non-authoritative receiver: learn the sender's clock, and refuse
        a message from before a reboot it already announced or more than 150 s behind it."""
        if boots >= MAX_BOOTS:
            return False
        known = self._clocks.get(engine_id)
        if known is not None:
            known_boots, known_time, seen_at = known
            estimate = known_time + int(now - seen_at)
            behind = boots == known_boots and engine_time < estimate - TIME_WINDOW_S
            if boots < known_boots or behind:
                return False
            if boots == known_boots and engine_time <= estimate:
                self._clocks.move_to_end(engine_id)
                return True
        self._clocks[engine_id] = (boots, engine_time, now)
        self._clocks.move_to_end(engine_id)
        if len(self._clocks) > CACHE_ENTRIES:
            self._clocks.popitem(last=False)
        return True


def process(data: bytes, policy: SnmpPolicy, state: UsmState, now: float | None = None) -> Any:
    """Authenticate and decrypt one v3 message; return its PDU and the user it came from.

    Raises :class:`UsmError` with the reason the quarantine shows. The order follows RFC 3414
    §3.2: the user, the security level, the MAC, the clock, and only then the decryption — so an
    unauthenticated message never costs a decryption.
    """
    header = parse_header(data)
    user = policy.users.get(header.user)
    if user is None:
        raise UsmError("v3-unknown-user")
    if user.engine_id is not None and user.engine_id != header.engine_id:
        raise UsmError("v3-unknown-engine-id")
    level = 2 if header.flags & FLAG_PRIV else 1 if header.flags & FLAG_AUTH else 0
    if header.flags & FLAG_PRIV and not header.flags & FLAG_AUTH:
        raise UsmError("v3-malformed")
    if level < user.level:
        raise UsmError("v3-security-level-too-low")
    if level > user.level:
        raise UsmError("v3-unsupported-security-level")
    auth_key, priv_key = state.keys(user, header.engine_id)
    if level >= 1:
        hasher, length = AUTH_PROTOCOLS[user.auth]
        if len(header.auth_params) != length:
            raise UsmError("v3-authentication-failed")
        zeroed = data[: header.auth_offset] + bytes(length) + data[header.auth_offset + length :]
        mac = hmac.new(auth_key, zeroed, hasher).digest()[:length]
        if not hmac.compare_digest(mac, header.auth_params):
            raise UsmError("v3-authentication-failed")
        clock = time.time() if now is None else now
        if policy.time_window and not state.in_window(
            header.engine_id, header.boots, header.engine_time, clock
        ):
            raise UsmError("v3-not-in-time-window")
    if level == 2:
        if header.data_tag != 0x04:
            raise UsmError("v3-malformed")
        scoped = decrypt(user.priv, priv_key, header)
    else:
        if header.data_tag != 0x30:
            raise UsmError("v3-malformed")
        scoped = header.data
    try:
        message, _rest = decoder.decode(scoped, asn1Spec=ScopedPDU())
        pdu = message["data"].getComponent()
    except Exception as exc:
        raise UsmError("v3-decryption-failed" if level == 2 else "v3-malformed") from exc
    return pdu, user


# -- privacy ---------------------------------------------------------------------------------


def privacy_available() -> bool:
    """Whether the optional `cryptography` package is importable (DECISIONS #444)."""
    return _ciphers() is not None


_CIPHERS: list[Any] = []


def _ciphers() -> Any:
    """`(Cipher, AES, TripleDES, CBC, CFB)` from `cryptography`, imported once, or None."""
    if _CIPHERS:
        return _CIPHERS[0]
    try:
        primitives = importlib.import_module("cryptography.hazmat.primitives.ciphers")
    except ImportError:
        _CIPHERS.append(None)
        return None
    algorithms, modes = primitives.algorithms, primitives.modes
    triple = _first_attr(
        ("cryptography.hazmat.decrepit.ciphers.algorithms", "TripleDES"), algorithms
    )
    cfb = _first_attr(("cryptography.hazmat.decrepit.ciphers.modes", "CFB"), modes)
    _CIPHERS.append((primitives.Cipher, algorithms.AES, triple, modes.CBC, cfb))
    return _CIPHERS[0]


def _first_attr(preferred: tuple[str, str], fallback: Any) -> Any:
    """The algorithm from `decrepit` where this version of `cryptography` moved it, else where it
    used to live. DES and CFB are legacy, and the protocol fixes them, not this appliance."""
    try:
        return getattr(importlib.import_module(preferred[0]), preferred[1])
    except (ImportError, AttributeError):
        return getattr(fallback, preferred[1])


def decrypt(priv: str, key: bytes, header: Header) -> bytes:
    """RFC 3414 §8 (DES-CBC), the Reeder draft (3DES-EDE-CBC), RFC 3826 (AES-CFB128)."""
    ciphers = _ciphers()
    if ciphers is None:
        raise UsmError("v3-privacy-unavailable")
    cipher, aes, triple_des, cbc, cfb = ciphers
    salt = header.priv_params
    if len(salt) != 8:
        raise UsmError("v3-decryption-failed")
    if priv in ("DES", "3DES"):
        size = 8 if priv == "DES" else 24
        if len(header.data) % 8:
            raise UsmError("v3-decryption-failed")
        iv = bytes(a ^ b for a, b in zip(key[size : size + 8], salt, strict=True))
        # A legacy cipher, decrypting what the equipment sends; the protocol chose it.
        des_key = key[:8] * 3 if priv == "DES" else key[:24]  # DES is 3DES with K1=K2=K3
        engine = cipher(triple_des(des_key), cbc(iv)).decryptor()  # nosec B304
    else:
        iv = header.boots.to_bytes(4, "big") + header.engine_time.to_bytes(4, "big") + salt
        engine = cipher(aes(key), cfb(iv)).decryptor()
    plain: bytes = engine.update(header.data) + engine.finalize()
    return plain
