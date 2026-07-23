from __future__ import annotations

import asyncio

import pytest
from hypothesis import given
from hypothesis import strategies as st
from opticorr.events import QuarantinedPacket, TrapEvent
from opticorr.receiver import (
    QueueItem,
    TrapParseError,
    TrapReceiver,
    parse_allowlist,
    parse_trap,
    start_receiver,
)

import trap_replay

CIENA = "1.3.6.1.4.1.1271.2.1.1"


def encode(trap_oid: str = CIENA, varbinds: list[dict[str, str]] | None = None) -> bytes:
    return trap_replay.encode_trap(trap_oid, varbinds or [], "public", 123)


def test_parse_trap_roundtrip() -> None:
    wire = encode(varbinds=[{"oid": "1.3.6.1.4.1.1271.9.9", "kind": "str", "value": "port-1"}])
    event = parse_trap("10.0.0.1", wire, ts=42.0)
    assert event.device == "10.0.0.1"
    assert event.trap_oid == CIENA
    assert event.instance == "port-1"
    assert event.ts == 42.0
    kinds = [vb.kind for vb in event.varbinds]
    assert kinds == ["TimeTicks", "ObjectIdentifier", "OctetString"]


def test_instance_prefers_ifindex() -> None:
    wire = encode(
        varbinds=[
            {"oid": "1.3.6.1.4.1.1271.9.9", "kind": "str", "value": "other"},
            {"oid": "1.3.6.1.2.1.2.2.1.1.7", "kind": "int", "value": "7"},
        ]
    )
    assert parse_trap("10.0.0.1", wire, 1.0).instance == "7"


def test_instance_empty_without_payload() -> None:
    assert parse_trap("10.0.0.1", encode(), 1.0).instance == ""


def test_garbage_is_ber_decode_failed() -> None:
    with pytest.raises(TrapParseError, match="ber-decode-failed"):
        parse_trap("10.0.0.1", b"\x99\x01\x02not-snmp", 1.0)


def encode_v1(
    enterprise: str,
    generic: int,
    specific: int,
    agent: str = "10.9.9.9",
    varbinds: list[tuple[str, str]] | None = None,
    community: str = "public",
) -> bytes:
    from pyasn1.codec.ber import encoder
    from pysnmp.proto import api

    v1 = api.PROTOCOL_MODULES[api.SNMP_VERSION_1]
    pdu = v1.TrapPDU()
    v1.apiTrapPDU.set_defaults(pdu)
    v1.apiTrapPDU.set_enterprise(pdu, enterprise)
    v1.apiTrapPDU.set_generic_trap(pdu, generic)
    v1.apiTrapPDU.set_specific_trap(pdu, specific)
    v1.apiTrapPDU.set_agent_address(pdu, agent)
    v1.apiTrapPDU.set_varbinds(pdu, [(o, v1.OctetString(val)) for o, val in (varbinds or [])])
    message = v1.Message()
    v1.apiMessage.set_defaults(message)
    v1.apiMessage.set_community(message, community)
    v1.apiMessage.set_pdu(message, pdu)
    return bytes(encoder.encode(message))


def test_snmpv1_enterprise_specific_mapped_via_rfc3584() -> None:
    # S2: v0.2.0 quarantined all v1 traps; v0.3.0 maps them per RFC 3584 §3.1 into the
    # pipeline. The NE is the UDP source, not the (spoofable) in-PDU agent address.
    wire = encode_v1(
        "1.3.6.1.4.1.368.4",
        generic=6,
        specific=2,
        agent="203.0.113.9",
        varbinds=[("1.3.6.1.4.1.368.1.1.1", "cam-7")],
    )
    event = parse_trap("198.51.100.5", wire, ts=5.0)
    assert event.device == "198.51.100.5"  # UDP source, NOT the agent address
    assert event.trap_oid == "1.3.6.1.4.1.368.4.0.2"  # <enterprise>.0.<specific>
    assert event.instance == "cam-7"  # a real payload varbind, not the agent address
    oids = [vb.oid for vb in event.varbinds]
    assert oids[0] == "1.3.6.1.2.1.1.3.0"  # sysUpTime.0 prepended
    assert oids[1] == "1.3.6.1.6.3.1.1.4.1.0"  # snmpTrapOID.0 prepended
    assert "1.3.6.1.6.3.18.1.3.0" in oids  # snmpTrapAddress.0 exposes the agent address
    agent_vb = next(vb for vb in event.varbinds if vb.oid == "1.3.6.1.6.3.18.1.3.0")
    assert agent_vb.value == "203.0.113.9"


def test_snmpv1_generic_trap_maps_to_standard_oid() -> None:
    wire = encode_v1("1.3.6.1.4.1.9", generic=2, specific=0)  # generic 2 = linkDown
    assert parse_trap("10.0.0.1", wire, 1.0).trap_oid == "1.3.6.1.6.3.1.1.5.3"


def test_snmpv1_community_never_leaks_into_varbinds() -> None:
    # F4 overrides the RFC's snmpTrapCommunity append: the community is tagged and dropped.
    wire = encode_v1("1.3.6.1.4.1.9", generic=6, specific=1, community="s3cr3t-community")
    event = parse_trap("10.0.0.1", wire, 1.0)
    assert all("s3cr3t-community" not in vb.value for vb in event.varbinds)
    assert all(vb.oid != "1.3.6.1.6.3.18.1.4.0" for vb in event.varbinds)  # no snmpTrapCommunity


@given(
    generic=st.integers(min_value=0, max_value=6),
    specific=st.integers(min_value=0, max_value=999),
    ent_leaf=st.integers(min_value=1, max_value=99999),
)
def test_snmpv1_mapping_is_total_and_well_formed(
    generic: int, specific: int, ent_leaf: int
) -> None:
    """Property: every v1 trap maps to a numeric snmpTrapOID and a source-IP device."""
    enterprise = f"1.3.6.1.4.1.{ent_leaf}"
    wire = encode_v1(enterprise, generic=generic, specific=specific)
    event = parse_trap("192.0.2.1", wire, 1.0)
    assert event.device == "192.0.2.1"
    assert all(part.isdigit() for part in event.trap_oid.split("."))
    if generic in range(6):
        assert event.trap_oid == f"1.3.6.1.6.3.1.1.5.{generic + 1}"  # standard trap
    else:
        assert event.trap_oid == f"{enterprise}.0.{specific}"  # enterprise-specific


def test_non_trap_pdu_is_quarantined() -> None:
    from pyasn1.codec.ber import encoder
    from pysnmp.proto import api

    pmod = api.PROTOCOL_MODULES[api.SNMP_VERSION_2C]
    pdu = pmod.GetRequestPDU()
    pmod.apiPDU.set_defaults(pdu)
    message = pmod.Message()
    pmod.apiMessage.set_defaults(message)
    pmod.apiMessage.set_pdu(message, pdu)
    with pytest.raises(TrapParseError, match="not-a-trap-pdu"):
        parse_trap("10.0.0.1", bytes(encoder.encode(message)), 1.0)


def test_missing_trap_oid() -> None:
    from pyasn1.codec.ber import encoder
    from pysnmp.proto import api

    pmod = api.PROTOCOL_MODULES[api.SNMP_VERSION_2C]
    pdu = pmod.TrapPDU()
    pmod.apiTrapPDU.set_defaults(pdu)
    pmod.apiPDU.set_varbinds(pdu, [("1.3.6.1.2.1.1.3.0", pmod.TimeTicks(1))])
    message = pmod.Message()
    pmod.apiMessage.set_defaults(message)
    pmod.apiMessage.set_pdu(message, pdu)
    with pytest.raises(TrapParseError, match="missing-trap-oid"):
        parse_trap("10.0.0.1", bytes(encoder.encode(message)), 1.0)


def test_parse_allowlist() -> None:
    assert parse_allowlist("") is None
    assert parse_allowlist(" , ") is None
    nets = parse_allowlist("10.0.0.0/24, 192.168.1.5")
    assert nets is not None and len(nets) == 2
    with pytest.raises(ValueError, match="does not appear"):
        parse_allowlist("not-an-ip")


def test_allowlist_denies_and_counts() -> None:
    queue: asyncio.Queue[QueueItem] = asyncio.Queue()
    receiver = TrapReceiver(queue, allowlist="10.0.0.0/24")
    receiver.datagram_received(encode(), ("10.0.0.7", 12345))
    receiver.datagram_received(encode(), ("192.168.9.9", 12345))
    assert receiver.stats.accepted == 1
    assert receiver.stats.denied == 1
    assert queue.qsize() == 1


def test_queue_overflow_is_counted_not_fatal() -> None:
    queue: asyncio.Queue[QueueItem] = asyncio.Queue(maxsize=1)
    receiver = TrapReceiver(queue)
    receiver.datagram_received(encode(), ("10.0.0.7", 1))
    receiver.datagram_received(encode(), ("10.0.0.7", 1))
    assert receiver.stats.dropped == 1


async def test_end_to_end_udp_two_vendors() -> None:
    queue: asyncio.Queue[QueueItem] = asyncio.Queue()
    transport, receiver = await start_receiver(queue, "127.0.0.1", 0)
    port = transport.get_extra_info("sockname")[1]
    sender = trap_replay.Sender(("127.0.0.1", port))
    try:
        sender.send("127.0.0.2", encode(CIENA))
        sender.send("127.0.0.3", encode("1.3.6.1.4.1.2011.5.104.1"))
        sender.send("127.0.0.2", b"\x00garbage")
        items = [await asyncio.wait_for(queue.get(), 2) for _ in range(3)]
    finally:
        sender.close()
        transport.close()
    events = [i for i in items if isinstance(i, TrapEvent)]
    bad = [i for i in items if isinstance(i, QuarantinedPacket)]
    assert {e.device for e in events} == {"127.0.0.2", "127.0.0.3"}
    assert {e.trap_oid for e in events} == {CIENA, "1.3.6.1.4.1.2011.5.104.1"}
    assert len(bad) == 1 and bad[0].source == "127.0.0.2"
    assert receiver.stats.accepted == 2 and receiver.stats.quarantined == 1


@given(st.binary(min_size=0, max_size=300))
def test_fuzz_parse_never_raises_unexpectedly(data: bytes) -> None:
    try:
        event = parse_trap("10.0.0.1", data, 1.0)
    except TrapParseError:
        return
    assert isinstance(event, TrapEvent)


@given(st.binary(min_size=0, max_size=300))
def test_fuzz_datagram_received_never_raises(data: bytes) -> None:
    queue: asyncio.Queue[QueueItem] = asyncio.Queue()
    receiver = TrapReceiver(queue)
    receiver.datagram_received(data, ("10.0.0.1", 1))
    assert receiver.stats.received == 1
