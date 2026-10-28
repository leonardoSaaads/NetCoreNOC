"""The executor against a real SSH server, in process (ADR #462).

What a reviewer asks of something that logs into backbone equipment for an AI:

* it sends only read-only commands, and refuses the rest before connecting;
* the host key is pinned on first use, and a different key later is refused — the credential is
  never offered to an impostor;
* a refused credential, a silent host and a closed port each say which they are;
* what comes back is bounded.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import asyncssh
import pytest

from netcorenoc.engine.noc import commands, executor

PASSWORD = "Backbone-r00t-only-the-executor-sees"


class _Server(asyncssh.SSHServer):
    def begin_auth(self, username: str) -> bool:
        return True

    def password_auth_supported(self) -> bool:
        return True

    def validate_password(self, username: str, password: str) -> bool:
        return username == "noc" and password == PASSWORD


async def _answer(process: Any) -> None:
    command = process.command or ""
    if command.startswith("show alarm"):
        process.stdout.write("Port 3/4  LOS  critical  active\n")
    elif command == "show big":
        process.stdout.write("x" * (executor.MAX_OUTPUT + 1000))
    else:
        process.stdout.write(f"% unknown command: {command}\n")
    process.exit(0)


@pytest.fixture
async def device() -> AsyncIterator[dict[str, Any]]:
    key = asyncssh.generate_private_key("ssh-ed25519")
    server = await asyncssh.create_server(
        _Server, "127.0.0.1", 0, server_host_keys=[key], process_factory=_answer
    )
    port = server.sockets[0].getsockname()[1]
    try:
        yield {
            "row": {
                "host": "127.0.0.1",
                "port": port,
                "access_level": "view_only",
                "kind": "ssh_password",
            },
            "fields": {"username": "noc", "password": PASSWORD},
            "key": key.export_public_key("openssh").decode().strip(),
        }
    finally:
        server.close()
        await server.wait_closed()


async def test_the_first_connection_pins_the_host_key_and_a_different_one_is_refused(
    device: dict[str, Any],
) -> None:
    first = await executor.ssh(
        device["row"], device["fields"], "show alarm list active-alarms type ALL", pinned=None
    )
    assert first.ok and "LOS" in first.output and first.host_key == device["key"]
    again = await executor.ssh(
        device["row"], device["fields"], "show alarm list", pinned=device["key"]
    )
    assert again.ok and again.host_key is None, "a pinned key is not reported twice"
    impostor = asyncssh.generate_private_key("ssh-ed25519").export_public_key("openssh").decode()
    refused = await executor.ssh(
        device["row"], device["fields"], "show alarm list", pinned=impostor
    )
    assert not refused.ok and "not the one pinned" in refused.detail and not refused.output


async def test_a_wrong_credential_a_closed_port_and_a_big_answer(device: dict[str, Any]) -> None:
    wrong = await executor.ssh(
        device["row"], {**device["fields"], "password": "nope"}, "show alarm", pinned=None
    )
    assert not wrong.ok and "not accepted" in wrong.detail
    big = await executor.ssh(device["row"], device["fields"], "show big", pinned=device["key"])
    assert big.truncated and len(big.output.encode()) <= executor.MAX_OUTPUT
    closed = await executor.ssh(
        {**device["row"], "port": 1}, device["fields"], "show alarm", pinned=None, timeout=3
    )
    assert not closed.ok and closed.output == ""


async def test_a_write_never_reaches_the_device(device: dict[str, Any]) -> None:
    for command in (
        "configure terminal",
        "show run; reload",
        "show alarm | tee /tmp/x",
        "show alarm\nreload",
        "display current-configuration && reboot",
        "show $(reboot)",
        "show alarm clear",
        "ping 10.0.0.1",  # a diagnostic verb, on a view-only credential
    ):
        with pytest.raises(commands.CommandError):
            await executor.ssh(device["row"], device["fields"], command, pinned=None)


def test_the_read_only_rule() -> None:
    assert commands.check("  show alarm list active-alarms type ALL ", "view_only") == (
        "show alarm list active-alarms type ALL"
    )
    assert commands.check("display interface brief", "view_only")
    assert commands.check("ping 10.0.0.1 count 3", "diagnostic")
    with pytest.raises(commands.CommandError, match="read-only"):
        commands.check("set system host-name x", "root")
    with pytest.raises(commands.CommandError, match="writes or escalates"):
        commands.check("show configuration commit", "root")


def test_the_read_only_rule_on_routeros() -> None:
    """MikroTik RouterOS (ADR #469): a menu path then `print`, `monitor … once` or `ping`.

    The owner's lab switch is a MikroTik: before this, no command could reach it, because RouterOS
    puts no read verb in front. What reads may run; every way to act is refused before connecting —
    a write command, a hyphenated one (`reset-counters`), a `print` that writes a file, and a
    bracket, which RouterOS evaluates as a nested command.
    """
    for line in (
        "/interface print",
        "/interface ethernet print detail without-paging",
        "/system resource print",
        '/log print where topics~"interface"',
        "/interface ethernet monitor ether1 once",
    ):
        assert commands.check(line, "view_only") == line
    assert commands.check("/ping 10.0.0.2 count=3", "diagnostic")
    for line, why in (
        ("/interface set ether1 disabled=yes", "'set' writes"),
        ("/interface ethernet reset-counters ether1", "'reset' writes"),
        ("/interface print file=dump", "'file' writes"),
        ("/interface print where name=[/system script run x]", "nested command"),
        ("/system script run cleanup", "'run' writes"),
        ("/user add name=x password=y", "writes"),
        ("/export", "'export' writes"),
        ("/interface ethernet monitor ether1", "monitor … once"),
        ("/system reboot", "'reboot' writes"),
    ):
        with pytest.raises(commands.CommandError, match=why):
            commands.check(line, "root")
    with pytest.raises(commands.CommandError, match="diagnostic access level"):
        commands.check("/ping 10.0.0.2 count=3", "view_only")
