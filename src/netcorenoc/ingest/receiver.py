"""Asyncio SNMP trap listener — v1, v2c and v3 — with source allowlist and quarantine.

Defensive by construction: a malformed packet can never crash or block the process —
it is truncated, wrapped in a :class:`QuarantinedPacket`, and queued for storage so an
operator can inspect it later. Backpressure is a bounded queue; overflow is counted,
never awaited inside the datagram callback.

**What is accepted is an :class:`~netcorenoc.ingest.snmpconf.SnmpPolicy`** (v0.30.0): which
versions, which v1/v2c communities (any, by default) and which SNMPv3 users. A refusal is a
quarantine entry naming why — `community-not-accepted`, `v3-unknown-user`,
`v3-authentication-failed` — so an operator whose traps do not arrive can see the reason.
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

from netcorenoc.ingest import ber, known_oids, usm
from netcorenoc.ingest.events import QuarantinedPacket, TrapEvent, Varbind
from netcorenoc.ingest.snmpconf import DEFAULT_POLICY, SnmpPolicy

_PMOD = api.PROTOCOL_MODULES[api.SNMP_VERSION_2C]
_V1MOD = api.PROTOCOL_MODULES[api.SNMP_VERSION_1]
_SKIP_FOR_INSTANCE = (known_oids.SYS_UPTIME_OID, known_oids.SNMP_TRAP_OID)
MAX_QUARANTINE_BYTES = 4096
MAX_INSTANCE_CHARS = 120
COMMUNITY_TAG_HEX = 12  # first 12 hex chars of the HMAC (F4)
#: Distinct refusal reasons counted per process. The reasons are a closed vocabulary; the cap only
#: guarantees the counter cannot grow with hostile input (`not-a-trap-pdu:<type>`).
MAX_REASONS = 64

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
    """Comma-separated CIDRs/IPs; empty means allow all (zero-config default).

    A bad entry raises with **the entry named and an example of a good one** (F69). `ip_network`'s
    own message — *"'not-a-cidr' does not appear to be an IPv4 or IPv6 network"* — is correct and
    tells an operator who has never seen this code nothing about which setting produced it. The
    callers add the setting's name; this adds the shape it wanted.
    """
    entries = [e.strip() for e in spec.split(",") if e.strip()]
    out: list[Network] = []
    for entry in entries:
        try:
            out.append(ipaddress.ip_network(entry, strict=False))
        except ValueError as exc:
            raise ValueError(
                f"{entry!r} is not an IP address or CIDR network. The trap allowlist is a "
                f"comma-separated list of them, for example '10.20.0.0/16,192.0.2.10'; leave it "
                f"empty to accept every source."
            ) from exc
    return out or None


def _peek_version(data: bytes) -> int | None:
    """Read the SNMP version from the BER header: SEQUENCE, then INTEGER version."""
    if len(data) < 5 or data[0] != 0x30:
        return None
    offset = 2 + (data[1] & 0x7F if data[1] & 0x80 else 0)
    if len(data) < offset + 3 or data[offset] != 0x02 or data[offset + 1] != 0x01:
        return None
    return data[offset + 2]


def _decode_failure_reason(data: bytes) -> str:
    """Best-effort diagnosis for packets the v2c spec rejects (a version this receiver lacks)."""
    version = _peek_version(data)
    if version is None:
        return "ber-decode-failed"
    return f"unsupported-snmp-version-{version}" if version != 1 else "malformed-v2c-message"


def community_digest(community: bytes, key: bytes | None) -> str:
    """F4: the keyed hash of a community — the tag's source, and what an accepted list holds."""
    if key is None:
        return ""
    return hmac.new(key, community, hashlib.sha256).hexdigest()


def community_tag(community: bytes, key: bytes | None) -> str:
    """F4: opaque grouping tag for a community string; the community itself is discarded."""
    return community_digest(community, key)[:COMMUNITY_TAG_HEX]


def _accepted_tag(community: bytes, key: bytes | None, policy: SnmpPolicy) -> str:
    """The tag for a v1/v2c community, or a refusal when the policy names the ones it accepts."""
    digest = community_digest(community, key)
    if policy.communities and digest not in policy.digests:
        raise TrapParseError("community-not-accepted")
    return digest[:COMMUNITY_TAG_HEX]


def parse_trap(
    source: str,
    data: bytes,
    ts: float,
    community_key: bytes | None = None,
    policy: SnmpPolicy = DEFAULT_POLICY,
    state: usm.UsmState | None = None,
) -> TrapEvent:
    """Decode one SNMP trap datagram — v1 (via RFC 3584), v2c or v3 — or raise saying why not.

    The community string is hashed into ``community_tag`` (F4) and then dropped — it is never
    returned, stored, or logged, for either version. A v3 trap's tag is the hash of its user name.
    """
    version = _peek_version(data)
    if version == 0:
        if not policy.v1:
            raise TrapParseError("snmp-v1-not-accepted")
        return _parse_v1(source, data, ts, community_key, policy)
    if version == 3:
        if not policy.v3:
            raise TrapParseError("snmp-v3-not-accepted")
        return _parse_v3(source, data, ts, community_key, policy, state or usm.UsmState())
    if version == 1 and not policy.v2c:
        raise TrapParseError("snmp-v2c-not-accepted")
    return _parse_v2c(source, data, ts, community_key, policy)


def _parse_v3(
    source: str,
    data: bytes,
    ts: float,
    community_key: bytes | None,
    policy: SnmpPolicy,
    state: usm.UsmState,
) -> TrapEvent:
    try:
        pdu, user = usm.process(data, policy, state, ts)
    except usm.UsmError as exc:
        raise TrapParseError(exc.reason) from exc
    if not isinstance(pdu, _PMOD.TrapPDU):
        raise TrapParseError(f"not-a-trap-pdu:{type(pdu).__name__}")
    tag = community_tag(b"usm:" + user.name.encode("utf-8"), community_key)
    return _event_from_pdu(source, pdu, ts, tag)


def _parse_v2c(
    source: str, data: bytes, ts: float, community_key: bytes | None, policy: SnmpPolicy
) -> TrapEvent:
    try:
        message, _ = decoder.decode(data, asn1Spec=_PMOD.Message())
    except Exception as exc:
        raise TrapParseError(_decode_failure_reason(data)) from exc
    pdu = _PMOD.apiMessage.get_pdu(message)
    if not isinstance(pdu, _PMOD.TrapPDU):
        raise TrapParseError(f"not-a-trap-pdu:{type(pdu).__name__}")
    community = bytes(_PMOD.apiMessage.get_community(message).asOctets())
    tag = _accepted_tag(community, community_key, policy)
    del community  # never keep the plaintext community
    return _event_from_pdu(source, pdu, ts, tag)


def _event_from_pdu(source: str, pdu: Any, ts: float, tag: str) -> TrapEvent:
    """An SNMPv2-Trap-PDU's varbinds as a :class:`TrapEvent` — the v2c and v3 paths share it."""
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


def _parse_v1(
    source: str, data: bytes, ts: float, community_key: bytes | None, policy: SnmpPolicy
) -> TrapEvent:
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
    tag = _accepted_tag(community, community_key, policy)
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


def blank_community(data: bytes) -> bytes | None:
    """Zero the community octets in an SNMP message: SEQ{ INTEGER version, OCTETSTRING }.

    Best-effort BER walk; returns the packet with the community content zeroed, or None
    if the structure cannot be located (then the caller keeps metadata only).
    """
    try:
        if not data or data[0] != 0x30:
            return None
        seq = ber.read_length(data, 1)
        if seq is None:
            return None
        i = seq[1]
        if i >= len(data) or data[i] != 0x02:  # INTEGER version
            return None
        ver = ber.read_length(data, i + 1)
        if ver is None:
            return None
        i = ver[1] + ver[0]
        if i >= len(data) or data[i] != 0x04:  # OCTET STRING community
            return None
        com = ber.read_length(data, i + 1)
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
    """Instance heuristic: ifIndex when present, else the first payload varbind value that is not
    an X.733 severity word.

    **A severity is never an instance** (v0.28.0, ADR #432). A vendor that sends its severity
    first gave its raise the instance `major` and its clear the instance `cleared`: two alarm rows
    for one fault, and a clear that could never find its raise — not even after the state field was
    learned, because the learner's slot is keyed on the instance too. One dictionary lookup per
    varbind already iterated; nothing else changes on this path.
    """
    for vb in varbinds:
        if vb.oid.startswith(known_oids.IF_INDEX_PREFIX):
            return vb.value[:MAX_INSTANCE_CHARS]
    payload = [vb for vb in varbinds if vb.oid not in _SKIP_FOR_INSTANCE]
    for vb in payload:
        if known_oids.severity_rank(vb.value) is None:
            return vb.value[:MAX_INSTANCE_CHARS]
    return payload[0].value[:MAX_INSTANCE_CHARS] if payload else ""


class TrapReceiver(asyncio.DatagramProtocol):
    """UDP protocol: allowlist check, defensive parse, non-blocking enqueue."""

    def __init__(
        self,
        queue: asyncio.Queue[QueueItem],
        allowlist: str = "",
        community_key: bytes | None = None,
        policy: SnmpPolicy = DEFAULT_POLICY,
    ) -> None:
        self.queue = queue
        self.networks = parse_allowlist(allowlist)
        self.community_key = community_key
        # Replaced whole by the Settings screen (an attribute assignment the event loop sees on
        # the next datagram); never mutated in place, so no datagram reads half a policy.
        self.policy = policy
        self.usm = usm.UsmState()
        self.stats = ReceiverStats()
        self.reasons: dict[str, int] = {}

    def _refused(self, reason: str) -> None:
        """Count a quarantine by its reason, for the SNMP settings screen. Bounded."""
        if reason in self.reasons or len(self.reasons) < MAX_REASONS:
            self.reasons[reason] = self.reasons.get(reason, 0) + 1

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
            item = parse_trap(source, data, now, self.community_key, self.policy, self.usm)
            self.stats.accepted += 1
        except TrapParseError as exc:
            item = quarantine_packet(source, data, exc.reason, now)
            self.stats.quarantined += 1
            self._refused(exc.reason)
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
    policy: SnmpPolicy = DEFAULT_POLICY,
) -> tuple[asyncio.DatagramTransport, TrapReceiver]:
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: TrapReceiver(queue, allowlist, community_key, policy), local_addr=(host, port)
    )
    return transport, protocol
