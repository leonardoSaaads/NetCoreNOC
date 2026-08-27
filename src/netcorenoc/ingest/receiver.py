"""Asyncio SNMPv2c trap listener with source allowlist and quarantine.

Defensive by construction: a malformed packet can never crash or block the process —
it is truncated, wrapped in a :class:`QuarantinedPacket`, and queued for storage so an
operator can inspect it later. Backpressure is a bounded queue; overflow is counted,
never awaited inside the datagram callback.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import time
from dataclasses import dataclass
from typing import Any

from pyasn1.codec.ber import decoder
from pysnmp.proto import api

from netcorenoc.ingest import known_oids
from netcorenoc.ingest.events import QuarantinedPacket, TrapEvent, Varbind

_PMOD = api.PROTOCOL_MODULES[api.SNMP_VERSION_2C]
_V1MOD = api.PROTOCOL_MODULES[api.SNMP_VERSION_1]
_SKIP_FOR_INSTANCE = (known_oids.SYS_UPTIME_OID, known_oids.SNMP_TRAP_OID)
MAX_QUARANTINE_BYTES = 4096
MAX_INSTANCE_CHARS = 120
COMMUNITY_TAG_HEX = 12  # first 12 hex chars of the HMAC (F4)

# RFC 3584 §3.1: an SNMPv1 generic trap 0-5 maps to a standard snmpTraps.(generic+1) OID; a
# generic 6 (enterpriseSpecific) maps to <enterprise>.0.<specific-trap>.
SNMP_TRAPS_PREFIX = "1.3.6.1.6.3.1.1.5"
_GENERIC_TRAP_OIDS = {n: f"{SNMP_TRAPS_PREFIX}.{n + 1}" for n in range(6)}
SNMP_TRAP_ADDRESS_OID = "1.3.6.1.6.3.18.1.3.0"  # snmpTrapAddress.0 (the v1 agent-address)
SNMP_TRAP_ENTERPRISE_OID = "1.3.6.1.6.3.1.1.4.3.0"  # snmpTrapEnterprise.0

QueueItem = TrapEvent | QuarantinedPacket
Network = ipaddress.IPv4Network | ipaddress.IPv6Network


class TrapParseError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class ReceiverStats:
    received: int = 0
    accepted: int = 0
    denied: int = 0
    quarantined: int = 0
    dropped: int = 0


def parse_allowlist(spec: str) -> list[Network] | None:
    """Comma-separated CIDRs/IPs; empty means allow all (zero-config default)."""
    entries = [e.strip() for e in spec.split(",") if e.strip()]
    return [ipaddress.ip_network(e, strict=False) for e in entries] or None


def _peek_version(data: bytes) -> int | None:
    """Read the SNMP version from the BER header: SEQUENCE, then INTEGER version."""
    if len(data) < 5 or data[0] != 0x30:
        return None
    offset = 2 + (data[1] & 0x7F if data[1] & 0x80 else 0)
    if len(data) < offset + 3 or data[offset] != 0x02 or data[offset + 1] != 0x01:
        return None
    return data[offset + 2]


def _decode_failure_reason(data: bytes) -> str:
    """Best-effort diagnosis for packets the v2c spec rejects (e.g. SNMPv1)."""
    version = _peek_version(data)
    if version is None:
        return "ber-decode-failed"
    return f"unsupported-snmp-version-{version}" if version != 1 else "malformed-v2c-message"


def community_tag(community: bytes, key: bytes | None) -> str:
    """F4: opaque grouping tag for a community string; the community itself is discarded."""
    if key is None:
        return ""
    return hmac.new(key, community, hashlib.sha256).hexdigest()[:COMMUNITY_TAG_HEX]


def parse_trap(
    source: str, data: bytes, ts: float, community_key: bytes | None = None
) -> TrapEvent:
    """Decode one SNMP trap datagram (v2c or, via RFC 3584, v1); raise on anything else.

    The community string is hashed into ``community_tag`` (F4) and then dropped — it is never
    returned, stored, or logged, for either version.
    """
    if _peek_version(data) == 0:
        return _parse_v1(source, data, ts, community_key)
    return _parse_v2c(source, data, ts, community_key)


def _parse_v2c(source: str, data: bytes, ts: float, community_key: bytes | None) -> TrapEvent:
    try:
        message, _ = decoder.decode(data, asn1Spec=_PMOD.Message())
    except Exception as exc:
        raise TrapParseError(_decode_failure_reason(data)) from exc
    pdu = _PMOD.apiMessage.get_pdu(message)
    if not isinstance(pdu, _PMOD.TrapPDU):
        raise TrapParseError(f"not-a-trap-pdu:{type(pdu).__name__}")
    community = bytes(_PMOD.apiMessage.get_community(message).asOctets())
    tag = community_tag(community, community_key)
    del community  # never keep the plaintext community
    varbinds: list[Varbind] = []
    trap_oid = ""
    for oid_obj, value_obj in _PMOD.apiPDU.get_varbinds(pdu):
        oid = oid_obj.prettyPrint()
        varbinds.append(
            Varbind(oid=oid, kind=type(value_obj).__name__, value=value_obj.prettyPrint())
        )
        if oid == known_oids.SNMP_TRAP_OID:
            trap_oid = varbinds[-1].value
    if not trap_oid:
        raise TrapParseError("missing-trap-oid")
    if not all(part.isdigit() for part in trap_oid.split(".")):
        raise TrapParseError("bad-trap-oid")
    return TrapEvent(
        device=source,
        trap_oid=trap_oid,
        instance=_instance_of(varbinds),
        ts=ts,
        varbinds=varbinds,
        community_tag=tag,
    )


def _v1_trap_oid(enterprise: str, generic: int, specific: int) -> str:
    """RFC 3584 §3.1: snmpTrapOID.0 for a v1 trap."""
    if generic in _GENERIC_TRAP_OIDS:
        return _GENERIC_TRAP_OIDS[generic]
    # enterpriseSpecific (generic 6, or any non-standard value): <enterprise>.0.<specific>
    return f"{enterprise}.0.{specific}"


def _parse_v1(source: str, data: bytes, ts: float, community_key: bytes | None) -> TrapEvent:
    """Map an SNMPv1 trap into the v2c pipeline per RFC 3584 §3.1.

    ``sysUpTime.0`` and ``snmpTrapOID.0`` are prepended; the original varbinds follow so the
    instance heuristic still picks a real payload varbind (not the agent address); the agent
    address and enterprise are appended as ``snmpTrapAddress``/``snmpTrapEnterprise``. The NE
    is the UDP source, never the spoofable in-PDU agent address (DECISIONS v0.3 #21). The
    community is never carried as a varbind (F4 overrides the RFC's snmpTrapCommunity append).
    """
    try:
        message, _ = decoder.decode(data, asn1Spec=_V1MOD.Message())
    except Exception as exc:
        raise TrapParseError("malformed-v1-message") from exc
    pdu = _V1MOD.apiMessage.get_pdu(message)
    if not isinstance(pdu, _V1MOD.TrapPDU):
        raise TrapParseError(f"not-a-trap-pdu:{type(pdu).__name__}")
    community = bytes(_V1MOD.apiMessage.get_community(message).asOctets())
    tag = community_tag(community, community_key)
    del community  # never keep the plaintext community
    enterprise = _V1MOD.apiTrapPDU.get_enterprise(pdu).prettyPrint()
    generic = int(_V1MOD.apiTrapPDU.get_generic_trap(pdu))
    specific = int(_V1MOD.apiTrapPDU.get_specific_trap(pdu))
    trap_oid = _v1_trap_oid(enterprise, generic, specific)
    if not all(part.isdigit() for part in trap_oid.split(".")):
        raise TrapParseError("bad-trap-oid")
    uptime = int(_V1MOD.apiTrapPDU.get_timestamp(pdu))
    agent = _V1MOD.apiTrapPDU.get_agent_address(pdu).prettyPrint()
    varbinds: list[Varbind] = [
        Varbind(oid=known_oids.SYS_UPTIME_OID, kind="TimeTicks", value=str(uptime)),
        Varbind(oid=known_oids.SNMP_TRAP_OID, kind="ObjectIdentifier", value=trap_oid),
    ]
    for oid_obj, value_obj in _V1MOD.apiTrapPDU.get_varbinds(pdu):
        varbinds.append(
            Varbind(
                oid=oid_obj.prettyPrint(),
                kind=type(value_obj).__name__,
                value=value_obj.prettyPrint(),
            )
        )
    varbinds.append(Varbind(oid=SNMP_TRAP_ADDRESS_OID, kind="IpAddress", value=agent))
    varbinds.append(
        Varbind(oid=SNMP_TRAP_ENTERPRISE_OID, kind="ObjectIdentifier", value=enterprise)
    )
    return TrapEvent(
        device=source,
        trap_oid=trap_oid,
        instance=_instance_of(varbinds),
        ts=ts,
        varbinds=varbinds,
        community_tag=tag,
    )


def _read_ber_len(data: bytes, i: int) -> tuple[int, int] | None:
    """Read a BER length at offset i; return (length, content_offset) or None."""
    if i >= len(data):
        return None
    first = data[i]
    i += 1
    if first < 0x80:
        return first, i
    count = first & 0x7F
    if count == 0 or i + count > len(data):
        return None
    return int.from_bytes(data[i : i + count], "big"), i + count


def blank_community(data: bytes) -> bytes | None:
    """Zero the community octets in an SNMP message: SEQ{ INTEGER version, OCTETSTRING }.

    Best-effort BER walk; returns the packet with the community content zeroed, or None
    if the structure cannot be located (then the caller keeps metadata only).
    """
    try:
        if not data or data[0] != 0x30:
            return None
        seq = _read_ber_len(data, 1)
        if seq is None:
            return None
        i = seq[1]
        if i >= len(data) or data[i] != 0x02:  # INTEGER version
            return None
        ver = _read_ber_len(data, i + 1)
        if ver is None:
            return None
        i = ver[1] + ver[0]
        if i >= len(data) or data[i] != 0x04:  # OCTET STRING community
            return None
        com = _read_ber_len(data, i + 1)
        if com is None:
            return None
        start, length = com[1], com[0]
        if start + length > len(data):
            return None
        blanked = bytearray(data)
        for pos in range(start, start + length):
            blanked[pos] = 0
        return bytes(blanked)
    except Exception:  # pragma: no cover - defensive; never raise from the datagram path
        return None


def quarantine_packet(source: str, data: bytes, reason: str, ts: float) -> QuarantinedPacket:
    """Build a quarantine record with the community blanked, or metadata only (F4)."""
    blanked = blank_community(data)
    if blanked is not None:
        raw, sanitized = blanked[:MAX_QUARANTINE_BYTES], True
    else:
        raw, sanitized = b"", False  # cannot locate the community: never store the payload
    return QuarantinedPacket(
        source=source,
        raw=raw,
        reason=reason,
        ts=ts,
        sha256=hashlib.sha256(data).hexdigest(),
        length=len(data),
        first8=data[:8].hex(),
        sanitized=sanitized,
    )


def _instance_of(varbinds: list[Varbind]) -> str:
    """Instance heuristic: ifIndex when present, else the first payload varbind value."""
    for vb in varbinds:
        if vb.oid.startswith(known_oids.IF_INDEX_PREFIX):
            return vb.value[:MAX_INSTANCE_CHARS]
    for vb in varbinds:
        if vb.oid not in _SKIP_FOR_INSTANCE:
            return vb.value[:MAX_INSTANCE_CHARS]
    return ""


class TrapReceiver(asyncio.DatagramProtocol):
    """UDP protocol: allowlist check, defensive parse, non-blocking enqueue."""

    def __init__(
        self,
        queue: asyncio.Queue[QueueItem],
        allowlist: str = "",
        community_key: bytes | None = None,
    ) -> None:
        self.queue = queue
        self.networks = parse_allowlist(allowlist)
        self.community_key = community_key
        self.stats = ReceiverStats()

    def _allowed(self, source: str) -> bool:
        if self.networks is None:
            return True
        try:
            address = ipaddress.ip_address(source)
        except ValueError:
            return False
        return any(address in net for net in self.networks)

    def datagram_received(self, data: bytes, addr: tuple[str | Any, ...]) -> None:
        source, now = str(addr[0]), time.time()
        self.stats.received += 1
        if not self._allowed(source):
            self.stats.denied += 1
            return
        item: QueueItem
        try:
            item = parse_trap(source, data, now, self.community_key)
            self.stats.accepted += 1
        except TrapParseError as exc:
            item = quarantine_packet(source, data, exc.reason, now)
            self.stats.quarantined += 1
        try:
            self.queue.put_nowait(item)
        except asyncio.QueueFull:
            self.stats.dropped += 1


async def start_receiver(
    queue: asyncio.Queue[QueueItem],
    host: str,
    port: int,
    allowlist: str = "",
    community_key: bytes | None = None,
) -> tuple[asyncio.DatagramTransport, TrapReceiver]:
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: TrapReceiver(queue, allowlist, community_key), local_addr=(host, port)
    )
    return transport, protocol
