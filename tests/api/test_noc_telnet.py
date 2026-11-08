"""Telnet for the executor (ADR #481): login, one read-only command, a pager, and a refusal.

A fake device on a loopback socket speaks just enough Telnet — it proposes an option, asks for a
username and a password, paginates its answer — so the client's whole path runs without equipment.
Plain Telnet here; over TLS the same session runs inside the TLS stream, and its certificate is
pinned as an SSH host key is.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from netcorenoc.engine.noc import commands, telnet

IAC, DO, WILL = 255, 253, 251


async def _device(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    async def line() -> bytes:
        data = b""
        while not data.endswith(b"\r\n"):
            chunk = await reader.read(1)
            if not chunk:
                return data
            if chunk[0] == IAC:  # the client's answer to an option: three bytes
                await reader.read(2)
                continue
            data += chunk
        return data.strip()

    writer.write(
        bytes([IAC, DO, 24, IAC, WILL, 1]) + b"\r\nUser Access Verification\r\n\r\nUsername: "
    )
    user = await line()
    writer.write(b"Password: ")
    password = await line()
    if (user, password) != (b"noc", b"Dev1ce-Passw0rd!"):
        writer.write(b"\r\n% Authentication failed\r\nUsername: ")
        await writer.drain()
        writer.close()
        return
    writer.write(b"\r\nedge-1>")
    command = await line()
    writer.write(command + b"\r\nGi1/0/3 is down, line protocol is down\r\n ---- More ----")
    await writer.drain()
    await reader.read(1)  # the space the pager waits for
    writer.write(b"\r\n  Rx power -40.00 dBm\r\nedge-1>")
    await writer.drain()
    await line()  # exit
    writer.close()


def _row(port: int) -> dict[str, Any]:
    return {
        "host": "127.0.0.1",
        "port": port,
        "access_level": "view_only",
        "protocol_version": "Telnet",
    }


async def test_telnet_logs_in_runs_one_read_only_command_and_reads_past_the_pager() -> None:
    server = await asyncio.start_server(_device, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        fields = {"username": "noc", "password": "Dev1ce-Passw0rd!"}
        outcome = await telnet.run(
            _row(port), fields, "show interface Gi1/0/3", pinned=None, timeout=5
        )
        assert outcome.ok, outcome.detail
        assert outcome.action == "telnet" and outcome.host_key is None, "plain: nothing to pin"
        assert "Gi1/0/3 is down" in outcome.output and "Rx power -40.00 dBm" in outcome.output
        assert "edge-1>" not in outcome.output, "the prompt it returned to is not the answer"
        assert "show interface" not in outcome.output.splitlines()[0], "nor the device's echo"

        refused = await telnet.run(
            _row(port),
            {"username": "noc", "password": "wrong"},
            "show version",
            pinned=None,
            timeout=5,
        )
        assert not refused.ok and "not accepted" in refused.detail
        with pytest.raises(commands.CommandError):
            await telnet.run(_row(port), fields, "configure terminal", pinned=None, timeout=5)
    finally:
        server.close()


async def test_a_device_that_does_not_answer_is_said_so() -> None:
    held: list[asyncio.StreamWriter] = []
    server = await asyncio.start_server(lambda _r, w: held.append(w), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        quiet = await telnet.run(
            _row(port), {"password": "x"}, "show version", pinned=None, timeout=0.5
        )
        assert not quiet.ok and "no answer" in quiet.detail
    finally:
        for writer in held:
            writer.close()
        server.close()
