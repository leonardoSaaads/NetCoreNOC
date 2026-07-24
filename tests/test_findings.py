"""Phase 4.4/4.6 and findings F3-F6: log redaction + secret-leak scan, community
redaction, quarantine sanitization, TLS/warning banners, legacy-token audit."""

from __future__ import annotations

import hashlib
import hmac as _hmac
import logging

import pytest

from netcorenoc import main, receiver
from netcorenoc.logsetup import RedactionFilter, configure_logging
from netcorenoc.main import Engine
from netcorenoc.store import Store

import authutil
import trap_replay

COMMUNITY = "SECRETcommunityVALUE-42"
KEY = b"\x11" * 32


# -- F3: log redaction and the secret-leak scan --------------------------------------


def test_redaction_filter_masks_secrets() -> None:
    f = RedactionFilter()
    for text, secret in [
        ("Authorization: Bearer abcdef1234567890", "abcdef1234567890"),
        ("logging in with password=hunter2xyz now", "hunter2xyz"),
        ("token: s3cr3t-value-here-9", "s3cr3t-value-here-9"),
        ("Cookie: netcorenoc_session=deadbeefcafef00d", "deadbeefcafef00d"),
        ("community=publicsecret123", "publicsecret123"),
    ]:
        redacted = f._redact(text)
        assert secret not in redacted, redacted
        assert "<redacted>" in redacted


def test_configure_logging_installs_redaction(caplog: pytest.LogCaptureFixture) -> None:
    configure_logging(json_mode=False)
    try:
        with caplog.at_level(logging.INFO):
            logging.getLogger("netcorenoc").info("leak Authorization: Bearer topsecrettoken99")
        assert not any("topsecrettoken99" in r.getMessage() for r in caplog.records)
    finally:
        logging.getLogger().handlers.clear()  # restore pytest's capture


async def test_f3_secret_leak_scan_over_login_and_token_flows(
    store: Store, caplog: pytest.LogCaptureFixture
) -> None:
    """Run auth + token flows and grep every captured log line for every secret value."""
    caplog.set_level(logging.DEBUG)
    _engine, _queue, app = await authutil.make_env(store)
    secrets_used: list[str] = [authutil.PW]
    admin = await authutil.client_as(app, "admin")
    try:
        secrets_used.append(admin.cookies["netcorenoc_session"])  # session id
        created = await admin.post("/api/tokens", json={"name": "svc", "role": "viewer"})
        secrets_used.append(created.json()["token"])  # service-token value
        await admin.post(
            "/api/users",
            json={"username": "u2", "password": "a-secret-password-1", "role": "viewer"},
        )
        secrets_used.append("a-secret-password-1")
    finally:
        await admin.aclose()
    logged = "\n".join(r.getMessage() for r in caplog.records)
    for secret in secrets_used:
        assert secret not in logged, f"secret leaked into logs: {secret!r}"


# -- F4: community string never persisted; quarantine sanitized ----------------------


def _expected_tag() -> str:
    return _hmac.new(KEY, COMMUNITY.encode(), hashlib.sha256).hexdigest()[:12]


async def test_f4_community_never_persisted_only_tagged(store: Store) -> None:
    engine = Engine(store, __import__("asyncio").Queue())
    await engine.start()
    wire = trap_replay.encode_trap(
        "1.3.6.1.4.1.1271.2.1.1",
        [{"oid": "1.3.6.1.4.1.1271.9", "kind": "str", "value": "port-1"}],
        COMMUNITY,
        1,
    )
    event = receiver.parse_trap("10.0.0.5", wire, 1000.0, KEY)
    assert event.community_tag == _expected_tag()
    assert COMMUNITY not in repr(event)  # the plaintext community is gone from the event
    engine.queue.put_nowait(event)
    import util

    await util.run_engine_until(engine, engine.queue, count=1)
    # Scan every column of every table (text and blob) for the plaintext community.
    cur = await store.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in await cur.fetchall()]
    needle = COMMUNITY.encode()
    for table in tables:
        cur = await store.conn.execute(f"SELECT * FROM {table}")  # nosec B608 - table from schema
        for row in await cur.fetchall():
            for value in row:
                if isinstance(value, str):
                    assert COMMUNITY not in value, table
                elif isinstance(value, bytes):
                    assert needle not in value, table
    # The grouping tag IS stored on the alarm.
    cur = await store.conn.execute("SELECT community_tag FROM alarm")
    tag_row = await cur.fetchone()
    assert tag_row is not None and tag_row[0] == _expected_tag()


def test_f4_quarantine_blanks_community_in_raw() -> None:
    # A valid SNMP message (community present) that is not a trap PDU -> quarantined.
    from pyasn1.codec.ber import encoder
    from pysnmp.proto import api

    pmod = api.PROTOCOL_MODULES[api.SNMP_VERSION_2C]
    pdu = pmod.GetRequestPDU()
    pmod.apiPDU.set_defaults(pdu)
    message = pmod.Message()
    pmod.apiMessage.set_defaults(message)
    pmod.apiMessage.set_community(message, COMMUNITY)
    pmod.apiMessage.set_pdu(message, pdu)
    data = bytes(encoder.encode(message))
    assert COMMUNITY.encode() in data  # community is in the wire bytes

    pkt = receiver.quarantine_packet("10.0.0.9", data, "not-a-trap-pdu", 1.0)
    assert pkt.sanitized is True
    assert COMMUNITY.encode() not in pkt.raw  # blanked in the stored packet
    assert pkt.sha256 == hashlib.sha256(data).hexdigest()
    assert pkt.length == len(data) and pkt.first8 == data[:8].hex()


def test_f4_quarantine_metadata_only_when_unlocatable() -> None:
    junk = b"\x99\x01\x02 not snmp at all"  # cannot locate a community
    pkt = receiver.quarantine_packet("10.0.0.9", junk, "ber-decode-failed", 1.0)
    assert pkt.sanitized is False
    assert pkt.raw == b""  # never store the raw payload
    assert pkt.sha256 == hashlib.sha256(junk).hexdigest()
    assert pkt.length == len(junk) and pkt.first8 == junk[:8].hex()


# -- F6: operator warning banner -----------------------------------------------------


def test_f6_operator_warnings_conditions() -> None:
    # Empty allowlist -> warned; non-TLS non-loopback bind -> warned.
    warns = main.operator_warnings(allowlist="", tls_enabled=False, http_host="0.0.0.0")
    assert len(warns) == 2
    # Enforced allowlist + loopback bind -> no warnings.
    assert main.operator_warnings("10.0.0.0/8", tls_enabled=False, http_host="127.0.0.1") == []
    # TLS on a public bind with an allowlist -> no warnings.
    assert main.operator_warnings("10.0.0.0/8", tls_enabled=True, http_host="0.0.0.0") == []


async def test_f6_warnings_surface_in_stats(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(
        store, warnings=lambda: ["Trap allowlist is empty"]
    )
    admin = await authutil.client_as(app, "admin")
    try:
        stats = (await admin.get("/api/stats")).json()
    finally:
        await admin.aclose()
    assert stats["warnings"] == ["Trap allowlist is empty"]


# -- legacy token removed (F2 / §5.8) ------------------------------------------------


async def test_legacy_api_token_is_removed_and_errors_at_startup(tmp_path: object) -> None:
    """The shared API token (NETCORENOC_API_TOKEN / legacy OPTICORR_API_TOKEN) was removed in
    v0.3.0: setting it is a hard startup error that names the migration path to service tokens
    (DECISIONS v0.3 #29)."""
    from netcorenoc.main import LegacyTokenRemovedError, Settings, run

    settings = Settings(db_path=str(tmp_path) + "/x.db", api_token="LEGACY-SHARED-TOKEN")
    with pytest.raises(LegacyTokenRemovedError, match="was removed"):
        await run(settings)


async def test_service_token_acts_as_its_role(store: Store) -> None:
    """The replacement for the legacy shared token: a named, revocable service token bound
    to a role, sent as a Bearer credential."""
    from netcorenoc import auth

    _engine, _queue, app = await authutil.make_env(store)
    async with store.lock:
        await store.create_token(auth.hash_token("svc-value"), "svc", "admin", "adm", 0.0)
        await store.commit()
    async with authutil.new_client(app) as c:
        h = {"Authorization": "Bearer svc-value"}
        assert (await c.get("/api/stats", headers=h)).status_code == 200
        assert (await c.get("/api/users", headers=h)).status_code == 200  # admin-only route
        assert (await c.get("/api/stats")).status_code == 401  # no credential -> unauthenticated


async def test_denied_admin_action_audited_as_user_update(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    editor = await authutil.client_as(app, "editor")
    try:
        assert (await editor.get("/api/users")).status_code == 403
    finally:
        await editor.aclose()
    async with store.lock:
        rows = await store.audit_all()
    assert any(r["action"] == "user.update" and r["outcome"] == "denied" for r in rows)
