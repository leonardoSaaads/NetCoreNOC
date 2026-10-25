"""`crosscutting/secretbox.py` and `engine/noc/lease.py` — the vault's seal and its leases.

The claims a breach tests (ADRs #455, #458): a credential opens only with the database row **and**
`secret.key` **and** `secrets.key`; it opens only for the metadata it was sealed with, so a row
whose host was rewritten does not open; a tampered seal is refused without echoing anything; and a
lease carries no secret, is refused with any algorithm but HS256, and is refused when forged,
expired, or for another audience.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from netcorenoc.crosscutting import secretbox, vault
from netcorenoc.engine.noc import health, kinds, lease

#: The real installer, captured before the suite's autouse fixture replaces it for each test.
REAL_INSTALL = secretbox.install
PAYLOAD = {"username": "noc", "password": "root-pw-that-opens-the-backbone"}
BINDING = kinds.binding("ref1", "ssh_password", "10.0.0.1", 22, "SSH-2", "root")


def test_a_credential_round_trips_and_its_seal_holds_nothing_readable() -> None:
    token = secretbox.current().seal(BINDING, PAYLOAD)
    assert token.startswith(secretbox.PREFIX)
    assert "root-pw" not in token and "noc" not in token.removeprefix(secretbox.PREFIX)
    assert secretbox.current().open(BINDING, token) == PAYLOAD
    assert secretbox.current().seal(BINDING, PAYLOAD) != token, "fresh keys and nonces per seal"


@pytest.mark.parametrize(
    "rebound",
    [
        kinds.binding("ref1", "ssh_password", "203.0.113.66", 22, "SSH-2", "root"),  # host
        kinds.binding("ref1", "ssh_password", "10.0.0.1", 2222, "SSH-2", "root"),  # port
        kinds.binding("ref1", "ssh_password", "10.0.0.1", 22, "SSH-2", "view_only"),  # level
        kinds.binding("ref2", "ssh_password", "10.0.0.1", 22, "SSH-2", "root"),  # another row
        kinds.binding("ref1", "sftp", "10.0.0.1", 22, "SSH-2", "root"),  # kind
    ],
)
def test_a_credential_does_not_open_for_metadata_it_was_not_sealed_for(rebound: str) -> None:
    """A row whose host was rewritten to an attacker's machine does not hand the password over."""
    token = secretbox.current().seal(BINDING, PAYLOAD)
    with pytest.raises(vault.VaultError) as caught:
        secretbox.current().open(rebound, token)
    assert "root-pw" not in str(caught.value)


def test_each_key_alone_opens_nothing() -> None:
    """Two independent layers: the wrong secrets key, or the wrong secret key, is refused."""
    token = secretbox.current().seal(BINDING, PAYLOAD)
    other_box = secretbox.SecretBox.from_key(bytes(range(32)))
    with pytest.raises(vault.VaultError, match="another secrets key"):
        other_box.open(BINDING, token)
    original = vault.current()
    vault.configure(vault.Vault.from_key(bytes(range(32))))
    try:
        with pytest.raises(vault.VaultError, match="another secret key"):
            secretbox.current().open(BINDING, token)
    finally:
        vault.configure(original)


def test_a_tampered_or_truncated_seal_is_refused_without_echoing_it() -> None:
    box = secretbox.current()
    token = box.seal(BINDING, PAYLOAD)
    raw = bytearray(base64.urlsafe_b64decode(token[len(secretbox.PREFIX) :]))
    for index in (-1, len(raw) // 2, 20):
        flipped = bytearray(raw)
        flipped[index] ^= 1
        bad = secretbox.PREFIX + base64.urlsafe_b64encode(bytes(flipped)).decode("ascii")
        with pytest.raises(vault.VaultError) as caught:
            box.open(BINDING, bad)
        assert "root-pw" not in str(caught.value)
    for bad in (token[:30], "ncs1:!!!", "plaintext", "nc1:" + token[5:]):
        with pytest.raises(vault.VaultError):
            box.open(BINDING, bad)


def test_the_secrets_key_file_is_created_owner_only_and_a_shared_directory_is_said(
    tmp_path: Path,
) -> None:
    secret_key = tmp_path / "secret.key"
    original = secretbox._current
    real_install = REAL_INSTALL
    try:
        real_install(tmp_path / "secrets.key", secret_key=secret_key)
        assert (tmp_path / "secrets.key").stat().st_mode & 0o077 == 0
        assert secretbox.warnings() and "same directory" in secretbox.warnings()[0]
        apart = tmp_path / "elsewhere" / "secrets.key"
        real_install(apart, secret_key=secret_key)
        assert secretbox.warnings() == []
        (tmp_path / "loose.key").write_text(base64.b64encode(bytes(32)).decode() + "\n")
        (tmp_path / "loose.key").chmod(0o644)
        with pytest.raises(vault.VaultError, match="other users"):
            real_install(tmp_path / "loose.key", secret_key=secret_key)
    finally:
        secretbox.configure(original)


# --- leases -------------------------------------------------------------------------------


def _claims(now: int = 1_000_000, ttl: int = 900) -> lease.Claims:
    return lease.Claims(
        jti=lease.new_id(),
        sub="token:7",
        sid=3,
        ref="ref1",
        ver=1,
        kind="ssh_password",
        host="10.0.0.1",
        port=22,
        scope="diagnostic",
        iat=now,
        nbf=now,
        exp=now + ttl,
    )


def test_a_lease_carries_no_secret_and_decodes_to_what_was_issued() -> None:
    claims = _claims()
    token = lease.issue(claims)
    _header, payload, _sig = token.split(".")
    body = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    assert set(body) == set(lease.Claims.__dataclass_fields__)
    assert "password" not in token and "root-pw" not in token
    assert lease.decode(token, 1_000_100) == claims


def test_a_lease_is_refused_when_forged_expired_early_or_for_another_algorithm() -> None:
    token = lease.issue(_claims())
    header, payload, sig = token.split(".")
    with pytest.raises(lease.LeaseError, match="expired"):
        lease.decode(token, 1_000_000 + 900 + lease.LEEWAY_S + 1)
    with pytest.raises(lease.LeaseError, match="not yet valid"):
        lease.decode(token, 1_000_000 - lease.LEEWAY_S - 1)
    forged = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    forged["scope"] = "root"
    tampered = base64.urlsafe_b64encode(json.dumps(forged).encode()).rstrip(b"=").decode()
    with pytest.raises(lease.LeaseError, match="bad signature"):
        lease.decode(f"{header}.{tampered}.{sig}", 1_000_100)
    for alg in ("none", "HS512", "RS256"):
        other = (
            base64.urlsafe_b64encode(json.dumps({"alg": alg, "typ": "JWT"}).encode())
            .rstrip(b"=")
            .decode()
        )
        with pytest.raises(lease.LeaseError, match="unsupported algorithm"):
            lease.decode(f"{other}.{payload}.{sig}", 1_000_100)
    with pytest.raises(lease.LeaseError, match="bad signature"):
        secretbox.configure(secretbox.SecretBox.from_key(bytes(range(32))))
        try:
            lease.decode(token, 1_000_100)
        finally:
            from conftest import TEST_BOX

            secretbox.configure(TEST_BOX)
    for bad in ("", "a.b", "x" * 5000, "a.b.c"):
        with pytest.raises(lease.LeaseError):
            lease.decode(bad, 1_000_100)


# --- the catalogue and the light ----------------------------------------------------------


def test_the_catalogue_refuses_unknown_fields_and_missing_secrets_by_name_only() -> None:
    kind, fields, version = kinds.clean_fields(
        "ssh_password", {"username": " noc ", "password": " pw with spaces "}, version=""
    )
    assert (kind.id, version) == ("ssh_password", "SSH-2")
    assert fields == {"username": "noc", "password": " pw with spaces "}, "secrets kept verbatim"
    for kind_id, given, message in [
        ("ssh_password", {"username": "noc"}, "password: required"),
        ("ssh_password", {"username": "noc", "password": "x", "extra": "y"}, "extra: not a field"),
        ("sftp", {"username": "noc"}, "password: give password or private_key"),
        (
            "snmp_v3",
            {
                "username": "u",
                "auth_protocol": "SHA",
                "auth_key": "k" * 8,
                "priv_protocol": "AES-128",
            },
            "priv_key: required when privacy is on",
        ),
        ("telnet", {}, "kind: one of"),
    ]:
        with pytest.raises(kinds.CredentialError, match=message) as caught:
            kinds.clean_fields(kind_id, given, version="")
        assert "k" * 8 not in str(caught.value)
    for host in ("http://10.0.0.1", "10.0.0.1:22", "a b", "", "-bad.example"):
        with pytest.raises(kinds.CredentialError):
            kinds.clean_host(host)
    assert kinds.clean_host(" Router-1.Example.NET. ") == "router-1.example.net"
    assert kinds.clean_host("2001:db8::1") == "2001:db8::1"


def test_the_light_is_green_yellow_red_or_grey_and_always_says_why() -> None:
    ok = {"at": 10.0, "ok": True, "latency_ms": 12.0, "detail": "SSH-2.0-OpenSSH"}
    bad = {"at": 9.0, "ok": False, "latency_ms": None, "detail": "connection refused"}
    slow = {**ok, "latency_ms": health.SLOW_MS + 1}
    assert health.status([ok, ok], checkable=True, monitored=True)["state"] == "up"
    assert health.status([ok, bad], checkable=True, monitored=True)["state"] == "degraded"
    assert health.status([slow], checkable=True, monitored=True)["state"] == "degraded"
    down = health.status([bad, ok], checkable=True, monitored=True)
    assert down["state"] == "down" and "connection refused" in down["reason"]
    assert health.status([], checkable=True, monitored=True)["state"] == "unknown"
    assert health.status([ok], checkable=False, monitored=True)["state"] == "unknown"
    for answer in (down, health.status([], checkable=False, monitored=False)):
        assert answer["reason"]
