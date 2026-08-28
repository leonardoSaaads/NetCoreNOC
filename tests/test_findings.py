"""Phase 4.4/4.6 and findings F3-F6: log redaction + secret-leak scan, community
redaction, quarantine sanitization, TLS/warning banners, legacy-token audit."""

from __future__ import annotations

import hashlib
import hmac as _hmac
import logging
from pathlib import Path

import pytest

from netcorenoc import main
from netcorenoc.crosscutting.logsetup import RedactionFilter, configure_logging
from netcorenoc.ingest import receiver
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
    from netcorenoc.crosscutting import auth

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


# -- F69 / F75: a setting that cannot be read names itself, and an unusable one is never stored ---


@pytest.mark.parametrize(
    ("variable", "value", "expects"),
    [
        ("TRAP_PORT", "", "UDP port"),
        ("TRAP_PORT", "abc", "UDP port"),
        ("HTTP_PORT", "", "TCP port"),
        ("RETENTION_DAYS", "", "number of days"),
        ("RETENTION_DAYS", "seven", "number of days"),
        ("AUDIT_RETENTION_DAYS", "", "number of days"),
    ],
)
def test_an_unreadable_setting_names_the_variable(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str, expects: str
) -> None:
    """F69. The message must carry the variable, the value, and what was expected.

    The bar is the one the project already meets twice: `NETCORENOC_API_TOKEN` and any `OPTICORR_*`
    are refused by name with the replacement spelled out. These five were converted with a bare
    `int()` / `float()` and produced `ValueError: invalid literal for int() with base 10: ''` —
    a sentence about a value, in a process about to exit, with no way back to the setting.
    """
    from netcorenoc.crosscutting.settings import ENV_PREFIX, Settings, SettingsError

    monkeypatch.setenv(f"{ENV_PREFIX}{variable}", value)
    with pytest.raises(SettingsError) as caught:
        Settings.from_env()
    message = str(caught.value)
    assert f"{ENV_PREFIX}{variable}" in message, message
    assert repr(value) in message, message
    assert expects in message, message


def test_the_control_a_readable_setting_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without this, `from_env` could raise unconditionally and every case above would pass."""
    from netcorenoc.crosscutting.settings import ENV_PREFIX, Settings

    monkeypatch.setenv(f"{ENV_PREFIX}TRAP_PORT", "1162")
    monkeypatch.setenv(f"{ENV_PREFIX}RETENTION_DAYS", "21.5")
    settings = Settings.from_env()
    assert settings.trap_port == 1162
    assert settings.retention_days == 21.5


def test_a_bad_allowlist_entry_is_named_with_the_shape_it_wanted() -> None:
    """`ip_network`'s own message is about a value; an operator needs the shape and an example."""
    from netcorenoc.ingest.receiver import parse_allowlist

    with pytest.raises(ValueError) as caught:
        parse_allowlist("10.0.0.0/8, not-a-cidr")
    message = str(caught.value)
    assert "'not-a-cidr'" in message, message
    assert "10.20.0.0/16" in message, message  # an example of a good one
    # CONTROL: a list of good entries parses, so the assertion above is about the bad entry.
    assert parse_allowlist("10.0.0.0/8, 192.0.2.10") is not None


def test_the_startup_refusal_names_which_home_holds_the_bad_allowlist() -> None:
    """The stored override is the awkward branch: the screen that would fix it is served by the
    appliance that will not start, so the message has to carry the way out."""
    from netcorenoc.crosscutting.settings import SettingsError
    from netcorenoc.runner import _check_allowlist

    with pytest.raises(SettingsError) as from_env:
        _check_allowlist("nope", stored=False)
    assert "NETCORENOC_ALLOWLIST" in str(from_env.value)

    with pytest.raises(SettingsError) as from_store:
        _check_allowlist("nope", stored=True)
    assert "config.allowlist" in str(from_store.value)
    assert "NETCORENOC_ALLOWLIST" not in str(from_store.value)

    _check_allowlist("10.0.0.0/8", stored=True)  # CONTROL: a good one is not refused
    _check_allowlist("", stored=False)  # CONTROL: empty means accept all, and is not an error


async def test_an_unusable_allowlist_is_refused_before_it_can_be_stored(store: Store) -> None:
    """F75. `POST /api/config` wrote the value to `meta` and *then* handed it to the receiver, so an
    allowlist the parser refuses was persisted and answered 200 — and because the stored value
    overrides the environment, the next boot could not start.
    """
    _engine, _queue, app = await authutil.make_env(store)
    async with authutil.new_client(app) as client:
        await authutil.login(client, "admin")
        bad = await client.post(
            "/api/config", json={"allowlist": "not-a-cidr", "retention_days": 7.0}
        )
        assert bad.status_code == 422, bad.text
        assert "not-a-cidr" in bad.text
        async with store.lock:
            assert await store.get_meta("config.allowlist") is None, (
                "the refused allowlist was stored anyway; the next boot would not start (F75)"
            )
        # CONTROL: a valid one is accepted and stored, so the assertion above is about the parse.
        good = await client.post(
            "/api/config", json={"allowlist": "10.20.0.0/16", "retention_days": 7.0}
        )
        assert good.status_code == 200, good.text
        async with store.lock:
            assert await store.get_meta("config.allowlist") == "10.20.0.0/16"


@pytest.mark.parametrize(
    ("env", "must_name"),
    [
        ({"HTTP_PORT": "99999"}, "outside the range of a TCP port"),
        ({"TRAP_PORT": "0"}, "outside the range of a UDP port"),
        ({"TLS_CERT": "/tmp/only-a-cert"}, "NETCORENOC_TLS_KEY is not"),  # nosec B108
        ({"TLS_KEY": "/tmp/only-a-key"}, "NETCORENOC_TLS_CERT is not"),  # nosec B108
        (
            {"TLS_CERT": "/nonexistent/tls.crt", "TLS_KEY": "/nonexistent/tls.key"},
            "cannot be read",
        ),
    ],
)
def test_a_setting_outside_its_bounds_is_refused_by_name(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], must_name: str
) -> None:
    """The other half of F69: a value that *parses* and still cannot be used.

    A port out of range used to reach `sock.bind()` and come back as
    `OverflowError: bind(): port must be 0-65535` from inside asyncio, **after** the appliance had
    logged that it was listening. One TLS variable set without the other was worse than a plain
    failure: `tls_enabled` needs both, so the appliance told admins it was serving plain HTTP while
    uvicorn was handed the certificate and died on it.
    """
    from netcorenoc.crosscutting.settings import ENV_PREFIX, Settings, SettingsError

    for suffix, value in env.items():
        monkeypatch.setenv(f"{ENV_PREFIX}{suffix}", value)
    with pytest.raises(SettingsError) as caught:
        Settings.from_env()
    assert must_name in str(caught.value), str(caught.value)


def test_the_control_a_usable_tls_pair_and_an_in_range_port_are_accepted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without this, `_tls` and `_port` could refuse unconditionally and every case above pass."""
    from netcorenoc.crosscutting.settings import ENV_PREFIX, Settings

    cert, key = tmp_path / "tls.crt", tmp_path / "tls.key"
    cert.write_text("not a real certificate")
    key.write_text("not a real key")
    monkeypatch.setenv(f"{ENV_PREFIX}TLS_CERT", str(cert))
    monkeypatch.setenv(f"{ENV_PREFIX}TLS_KEY", str(key))
    monkeypatch.setenv(f"{ENV_PREFIX}HTTP_PORT", "65535")
    monkeypatch.setenv(f"{ENV_PREFIX}TRAP_PORT", "1")
    settings = Settings.from_env()
    assert settings.tls_enabled and settings.http_port == 65535 and settings.trap_port == 1


async def test_an_unopenable_database_names_the_variable_and_the_path(tmp_path: Path) -> None:
    """`sqlite3.OperationalError: unable to open database file` names neither the setting nor the
    path — and the path defaults to the **working directory**, which is how it goes wrong."""
    from netcorenoc.crosscutting.settings import Settings, SettingsError
    from netcorenoc.runner import run

    missing = tmp_path / "no-such-directory" / "netcorenoc.db"
    with pytest.raises(SettingsError) as caught:
        await run(Settings(db_path=str(missing)))
    message = str(caught.value)
    assert "NETCORENOC_DB" in message and str(missing) in message, message
    assert "working directory" in message, message


def test_a_denied_trap_raises_a_warning_and_a_clean_receiver_does_not() -> None:
    """F68's repair at the seam. The console renders `warnings` as a banner on every screen, so
    this is the channel that reaches an operator without a log line per packet."""
    from netcorenoc.ingest.receiver import ReceiverStats
    from netcorenoc.runner import receiver_warnings

    quiet = receiver_warnings(ReceiverStats(received=8, accepted=8), "10.0.0.0/8")
    assert quiet == [], quiet  # CONTROL: nothing denied, nothing said
    noisy = receiver_warnings(ReceiverStats(received=8, denied=8), "10.99.0.0/16")
    assert len(noisy) == 1 and "8 trap(s) refused" in noisy[0] and "10.99.0.0/16" in noisy[0], noisy
