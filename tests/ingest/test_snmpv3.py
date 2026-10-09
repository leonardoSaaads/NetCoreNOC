"""SNMPv3 traps and the receiver's SNMP policy (v0.30.0, ADR #444).

The v3 messages are produced by `pysnmp`'s own notification originator and captured off a local
socket, so the parser is checked against an independent encoder — the same messages a device using
`pysnmp`'s USM would send — rather than against bytes this suite built by hand.
"""

from __future__ import annotations

import asyncio
import socket
from typing import Any

import pytest
from pysnmp.hlapi.v3arch import asyncio as hlapi

from netcorenoc.ingest import receiver, snmpconf, usm
from netcorenoc.ingest.events import TrapEvent

KEY = b"k" * 32
AUTH, PRIV = "auth-passphrase", "priv-passphrase"
LINK_DOWN = "1.3.6.1.6.3.1.1.5.3"

_AUTH_OIDS = {
    "MD5": hlapi.usmHMACMD5AuthProtocol,
    "SHA": hlapi.usmHMACSHAAuthProtocol,
    "SHA-256": hlapi.usmHMAC192SHA256AuthProtocol,
    "SHA-512": hlapi.usmHMAC384SHA512AuthProtocol,
}
_PRIV_OIDS = {
    "DES": hlapi.usmDESPrivProtocol,
    "3DES": hlapi.usm3DESEDEPrivProtocol,
    "AES-128": hlapi.usmAesCfb128Protocol,
    "AES-192": hlapi.usmAesBlumenthalCfb192Protocol,
    "AES-256": hlapi.usmAesBlumenthalCfb256Protocol,
    "AES-192-C": hlapi.usmAesCfb192Protocol,
    "AES-256-C": hlapi.usmAesCfb256Protocol,
}

needs_privacy = pytest.mark.skipif(
    not usm.privacy_available(), reason="SNMPv3 privacy needs the optional `cryptography` package"
)


async def _v3_trap(auth: str, priv: str, user: str = "noc", auth_pw: str = AUTH) -> bytes:
    """One SNMPv3 trap as pysnmp encodes it, captured off a local socket."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.setblocking(False)
    engine = hlapi.SnmpEngine()
    kwargs: dict[str, Any] = {}
    if auth != "none":
        kwargs["authProtocol"] = _AUTH_OIDS[auth]
    if priv != "none":
        kwargs["privProtocol"] = _PRIV_OIDS[priv]
    credentials = hlapi.UsmUserData(
        user, auth_pw if auth != "none" else None, PRIV if priv != "none" else None, **kwargs
    )
    try:
        await hlapi.send_notification(
            engine,
            credentials,
            await hlapi.UdpTransportTarget.create(sock.getsockname()),
            hlapi.ContextData(),
            "trap",
            hlapi.NotificationType(hlapi.ObjectIdentity(LINK_DOWN)).add_varbinds(
                ("1.3.6.1.2.1.2.2.1.1.7", hlapi.OctetString("ge-0/0/7"))
            ),
        )
        loop = asyncio.get_running_loop()
        data = await asyncio.wait_for(loop.sock_recv(sock, 65535), timeout=5)
    finally:
        engine.close_dispatcher()
        sock.close()
    return data


def _policy(auth: str, priv: str, **extra: Any) -> snmpconf.SnmpPolicy:
    user = snmpconf.UsmUser(
        name="noc",
        auth=auth,
        priv=priv,
        auth_key=snmpconf.hash_passphrase(AUTH, auth) if auth != "none" else b"",
        priv_key=snmpconf.hash_passphrase(PRIV, auth) if priv != "none" else b"",
        engine_id=extra.pop("engine_id", None),
    )
    return snmpconf.SnmpPolicy(users={b"noc": user}, **extra)


def _parse(data: bytes, policy: snmpconf.SnmpPolicy, **kwargs: Any) -> TrapEvent:
    return receiver.parse_trap("192.0.2.7", data, 1000.0, KEY, policy, **kwargs)


def _refused(data: bytes, policy: snmpconf.SnmpPolicy, **kwargs: Any) -> str:
    with pytest.raises(receiver.TrapParseError) as caught:
        _parse(data, policy, **kwargs)
    return caught.value.reason


@pytest.mark.parametrize("auth", ["MD5", "SHA", "SHA-256", "SHA-512"])
async def test_an_authenticated_trap_is_accepted_under_every_hash(auth: str) -> None:
    event = _parse(await _v3_trap(auth, "none"), _policy(auth, "none"))
    assert event.trap_oid == LINK_DOWN
    assert event.instance == "ge-0/0/7"
    assert event.device == "192.0.2.7"
    # The tag names the user, keyed like a community's: the user name itself is not stored.
    assert event.community_tag == receiver.community_tag(b"usm:noc", KEY)


@needs_privacy
@pytest.mark.parametrize("priv", list(_PRIV_OIDS))
async def test_a_private_trap_is_decrypted_under_every_cipher(priv: str) -> None:
    event = _parse(await _v3_trap("SHA", priv), _policy("SHA", priv))
    assert (event.trap_oid, event.instance) == (LINK_DOWN, "ge-0/0/7")


async def test_a_trap_without_security_is_accepted_from_a_user_without_keys() -> None:
    assert _parse(await _v3_trap("none", "none"), _policy("none", "none")).trap_oid == LINK_DOWN


async def test_a_wrong_passphrase_is_refused_as_an_authentication_failure() -> None:
    data = await _v3_trap("SHA", "none", auth_pw="not-the-passphrase")
    assert _refused(data, _policy("SHA", "none")) == "v3-authentication-failed"


async def test_one_flipped_byte_breaks_the_mac() -> None:
    data = bytearray(await _v3_trap("SHA-256", "none"))
    data[-3] ^= 0x01  # inside the varbinds, covered by the MAC
    assert _refused(bytes(data), _policy("SHA-256", "none")) == "v3-authentication-failed"


async def test_an_unknown_user_and_a_disabled_version_say_so() -> None:
    data = await _v3_trap("SHA", "none", user="intruder")
    assert _refused(data, _policy("SHA", "none")) == "v3-unknown-user"
    no_v3 = snmpconf.SnmpPolicy(v3=False)
    assert _refused(await _v3_trap("SHA", "none"), no_v3) == "snmp-v3-not-accepted"


async def test_a_user_configured_for_privacy_refuses_a_trap_without_it() -> None:
    data = await _v3_trap("SHA", "none")
    assert _refused(data, _policy("SHA", "AES-128")) == "v3-security-level-too-low"
    assert _refused(await _v3_trap("SHA", "none"), _policy("none", "none")) == (
        "v3-unsupported-security-level"
    )


async def test_a_user_pinned_to_an_engine_refuses_another_engine() -> None:
    data = await _v3_trap("SHA", "none")
    pinned = _policy("SHA", "none", engine_id=bytes.fromhex("8000000001020304"))
    assert _refused(data, pinned) == "v3-unknown-engine-id"


async def test_a_replayed_trap_from_before_the_window_is_refused() -> None:
    """RFC 3414 §3.2.7: the receiver learns each sender's clock and refuses a stale message."""
    data = await _v3_trap("SHA", "none")
    policy = _policy("SHA", "none")
    state = usm.UsmState()
    header = usm.parse_header(data)
    # A later message from the same engine has been seen (its clock moved on by ten minutes)…
    assert state.in_window(header.engine_id, header.boots, header.engine_time + 600, 1000.0)
    # …so the original one is now 600 s behind it: a replay.
    assert _refused(data, policy, state=state) == "v3-not-in-time-window"
    # With the window switched off the same bytes are accepted.
    relaxed = snmpconf.SnmpPolicy(users=policy.users, time_window=False)
    assert _parse(data, relaxed, state=state).trap_oid == LINK_DOWN


def test_the_receiver_caches_are_bounded() -> None:
    state = usm.UsmState()
    for n in range(usm.CACHE_ENTRIES + 10):
        assert state.in_window(n.to_bytes(8, "big"), 1, 100, 1000.0)
    assert len(state._clocks) == usm.CACHE_ENTRIES


async def test_a_privacy_trap_without_cryptography_is_quarantined_not_dropped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = await _v3_trap("SHA", "AES-128") if usm.privacy_available() else None
    if data is None:
        pytest.skip("needs a private trap, which needs `cryptography` to encode")
    monkeypatch.setattr(usm, "_CIPHERS", [None])
    assert _refused(data, _policy("SHA", "AES-128")) == "v3-privacy-unavailable"


def test_a_malformed_v3_message_is_quarantined_with_a_reason() -> None:
    truncated = bytes.fromhex("3010020103")  # SEQUENCE { INTEGER 3, … } and nothing else
    assert _refused(truncated, snmpconf.DEFAULT_POLICY) == "v3-malformed"


# --- v1/v2c communities and versions ----------------------------------------------------------


def _v2c(community: bytes) -> bytes:
    from pysnmp.proto import api

    mod = api.PROTOCOL_MODULES[api.SNMP_VERSION_2C]
    pdu = mod.TrapPDU()
    mod.apiTrapPDU.set_defaults(pdu)
    mod.apiPDU.set_varbinds(
        pdu,
        [
            (mod.ObjectIdentifier("1.3.6.1.2.1.1.3.0"), mod.TimeTicks(1)),
            (mod.ObjectIdentifier("1.3.6.1.6.3.1.1.4.1.0"), mod.ObjectIdentifier(LINK_DOWN)),
        ],
    )
    message = mod.Message()
    mod.apiMessage.set_defaults(message)
    mod.apiMessage.set_community(message, community)
    mod.apiMessage.set_pdu(message, pdu)
    from pyasn1.codec.ber import encoder

    return bytes(encoder.encode(message))


def test_any_community_is_accepted_by_default() -> None:
    assert _parse(_v2c(b"public"), snmpconf.DEFAULT_POLICY).trap_oid == LINK_DOWN


def test_a_named_community_list_refuses_the_others() -> None:
    digest = receiver.community_digest(b"s3cret", KEY)
    policy = snmpconf.SnmpPolicy(communities=(snmpconf.Community(digest, "s•••• (6)"),))
    assert _parse(_v2c(b"s3cret"), policy).community_tag == digest[:12]
    assert _refused(_v2c(b"public"), policy) == "community-not-accepted"


def test_v2c_can_be_switched_off() -> None:
    assert _refused(_v2c(b"public"), snmpconf.SnmpPolicy(v2c=False)) == "snmp-v2c-not-accepted"


def test_the_receiver_counts_refusals_by_reason_and_swaps_its_policy() -> None:
    queue: asyncio.Queue[receiver.QueueItem] = asyncio.Queue()
    proto = receiver.TrapReceiver(queue, community_key=KEY, policy=snmpconf.SnmpPolicy(v2c=False))
    proto.datagram_received(_v2c(b"public"), ("192.0.2.7", 162))
    assert proto.reasons == {"snmp-v2c-not-accepted": 1}
    proto.accepts = snmpconf.DEFAULT_POLICY  # what Settings → SNMP does on save
    proto.datagram_received(_v2c(b"public"), ("192.0.2.7", 162))
    assert proto.stats.accepted == 1 and proto.stats.quarantined == 1


# --- the stored policy -----------------------------------------------------------------------


def test_the_stored_document_round_trips_and_keeps_no_passphrase() -> None:
    policy = _policy("SHA-256", "AES-256", time_window=False)
    text = snmpconf.to_document(policy)
    assert AUTH not in text and PRIV not in text
    parsed, problems = snmpconf.from_document(text)
    assert problems == [] and parsed == policy


def test_a_bad_stored_document_refuses_to_start_naming_the_way_out() -> None:
    from netcorenoc.crosscutting.settings import SettingsError

    with pytest.raises(SettingsError, match=r"DELETE FROM meta WHERE key='config\.snmp'"):
        snmpconf.stored_policy('{"version": 1, "users": [{"name": "x", "auth": "SHA"}]}')
    assert snmpconf.stored_policy(None) is snmpconf.DEFAULT_POLICY


def test_a_policy_that_accepts_nothing_is_a_problem() -> None:
    nothing = snmpconf.SnmpPolicy(v1=False, v2c=False, v3=False)
    assert snmpconf.policy_problems(nothing)


def test_the_master_key_matches_rfc_3414_appendix_a() -> None:
    """RFC 3414 A.3.1/A.3.2: "maplesyrup" with engine ID 000000000000000000000002."""
    engine = bytes.fromhex("000000000000000000000002")
    md5 = usm.localize(snmpconf.hash_passphrase("maplesyrup", "MD5"), engine, "MD5")
    sha = usm.localize(snmpconf.hash_passphrase("maplesyrup", "SHA"), engine, "SHA")
    assert md5.hex() == "526f5eed9fcce26f8964c2930787d82b"
    assert sha.hex() == "6695febc9288e36282235fc7151f128497b38f3f"
