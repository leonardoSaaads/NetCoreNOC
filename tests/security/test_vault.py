"""`crosscutting/vault.py` — secrets at rest (ADR #453).

The claims are the ones a breach tests: a sealed value does not open without the key, does not open
in another place, does not survive tampering, and says "wrong key" rather than "corrupt" when the
key file is not the one the database was written with. The key file is created owner-only, and one
any user may read is refused.
"""

from __future__ import annotations

import base64
import os
import stat
from pathlib import Path

import pytest

from netcorenoc.crosscutting import vault

KEY = bytes(range(32))


def test_a_sealed_value_opens_only_in_its_place_and_never_shows_its_plaintext() -> None:
    v = vault.Vault.from_key(KEY)
    token = v.seal("app_user.email", "vera@example.com")
    assert token.startswith(vault.PREFIX) and "vera" not in token
    assert v.open_text("app_user.email", token) == "vera@example.com"
    assert v.seal("app_user.email", "vera@example.com") != token, "a nonce per value"
    with pytest.raises(vault.VaultError, match="failed authentication"):
        v.open("app_user.display_name", token)  # moved to another column


def test_the_wrong_key_is_named_as_such() -> None:
    token = vault.Vault.from_key(KEY).seal("meta:config.email", b"secret")
    other = vault.Vault.from_key(bytes(32))
    with pytest.raises(vault.VaultError, match="another secret key"):
        other.open("meta:config.email", token)


def test_a_tampered_or_truncated_value_is_refused_without_echoing_it() -> None:
    v = vault.Vault.from_key(KEY)
    token = v.seal("meta:config.snmp", "s3cret-material")
    raw = bytearray(base64.urlsafe_b64decode(token[len(vault.PREFIX) :]))
    raw[-1] ^= 1  # one bit of the tag (a base64 character may carry only padding bits)
    flipped = vault.PREFIX + base64.urlsafe_b64encode(bytes(raw)).decode("ascii")
    for bad in (flipped, token[:20], "nc1:!!!", "plaintext"):
        with pytest.raises(vault.VaultError) as caught:
            v.open("meta:config.snmp", bad)
        assert "s3cret" not in str(caught.value)


def test_a_blind_index_is_deterministic_keyed_and_placed() -> None:
    v = vault.Vault.from_key(KEY)
    assert v.index("email", "a@b.c") == v.index("email", "a@b.c")
    assert v.index("email", "a@b.c") != v.index("username", "a@b.c")
    assert v.index("email", "a@b.c") != vault.Vault.from_key(bytes(32)).index("email", "a@b.c")


def test_the_pepper_depends_on_the_key() -> None:
    assert vault.Vault.from_key(KEY).pepper("pw") != vault.Vault.from_key(bytes(32)).pepper("pw")
    assert "_key" not in repr(vault.Vault.from_key(KEY)), "repr must not print key material"


def test_the_key_file_is_created_owner_only_once_and_reused(tmp_path: Path) -> None:
    path = tmp_path / "own.key"  # the suite's autouse fixture holds secret.key
    first, created = vault.load_or_create(path)
    assert created and stat.S_IMODE(path.stat().st_mode) == 0o600
    second, created_again = vault.load_or_create(path)
    assert not created_again and second.kid == first.kid


def test_a_key_file_any_user_may_read_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "own.key"  # the suite's autouse fixture holds secret.key
    vault.load_or_create(path)
    os.chmod(path, 0o644)
    with pytest.raises(vault.VaultError, match="chmod 600"):
        vault.load_or_create(path)
    os.chmod(path, 0o440)  # a secret mounted with a group is fine
    vault.load_or_create(path)


def test_an_unconfigured_process_refuses_rather_than_inventing_a_key() -> None:
    previous = vault._current
    vault.configure(None)
    try:
        with pytest.raises(vault.VaultError, match="no secret key is loaded"):
            vault.current()
    finally:
        vault.configure(previous)
