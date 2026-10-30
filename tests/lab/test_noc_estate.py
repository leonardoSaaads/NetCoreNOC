"""The lab estate answers the appliance's own executor, and breaks and mends as a network does.

ADR #476's round, as a test: six elements of three vendors at their own addresses — reached by
`engine/noc/executor.py` exactly as an agent's lease reaches equipment — a fibre cut between a
Huawei NE40E and a Ciena 3930 that each CLI and SNMP then reads as down, and the traps the cut and
its repair send: the IF-MIB pair, Huawei's DEVM pair, Ciena's port and signal-degrade pairs, each
a pair the appliance knows without learning it (ADR #475).
"""

from __future__ import annotations

import asyncio
import socket
import time
from pathlib import Path
from typing import Any

import noc_estate
from netcorenoc.engine.noc import executor
from netcorenoc.ingest import known_oids
from netcorenoc.ingest.receiver import parse_trap

PASSWORD = "lab-Passw0rd-for-the-estate"
LOGIN = {"username": noc_estate.USERNAME, "password": PASSWORD}


def _row(address: str, port: int, kind: str) -> dict[str, Any]:
    return {
        "host": address,
        "port": port,
        "access_level": "diagnostic",
        "kind": kind,
        "protocol_version": "v2c",
    }


def _received(sink: socket.socket) -> list[tuple[str, str]]:
    """Every trap waiting on ``sink``, as (source address, trap OID)."""
    out: list[tuple[str, str]] = []
    while True:
        try:
            data, (source, _port) = sink.recvfrom(65536)
        except BlockingIOError:
            return out
        out.append((source, parse_trap(source, data, time.time()).trap_oid))


async def test_the_estate_answers_and_a_cut_reads_down_until_its_repair(tmp_path: Path) -> None:
    sink = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sink.bind(("127.0.0.1", 0))
    sink.setblocking(False)
    estate = noc_estate.Estate(sink.getsockname(), "nocLab", tmp_path / "truth.jsonl", seed=1)
    try:
        ports = await noc_estate.serve(estate, PASSWORD, tmp_path, ssh_port=0, snmp_port=0)
        pe01, sila, crs = "127.0.40.2", "127.0.10.2", "127.0.10.1"

        async def ssh(address: str, command: str) -> executor.Outcome:
            row = _row(address, ports[address][0], "ssh_password")
            return await executor.ssh(row, LOGIN, command, pinned=None)

        brief = await ssh(pe01, "dis int b")
        assert brief.ok and "GigabitEthernet1/0/0" in brief.output, brief
        sfp = await ssh(crs, "/interface ethernet monitor sfp-sfpplus1 once")
        assert sfp.ok and "link-ok" in sfp.output and "sfp-rx-power" in sfp.output
        assert (await ssh(sila, "show xcvr status port 3")).ok

        await estate.cut("L3")
        down = await ssh(pe01, "display interface GE1/0/0")
        assert down.ok and "current state : DOWN" in down.output, down.output
        optics = await ssh(sila, "show xcvr status port 3")
        assert "-40.00 dBm (no light)" in optics.output
        alarms = await ssh(sila, "show alarm list")
        assert "Loss of signal (LOS)" in alarms.output
        read = await executor.snmp_get(
            _row(sila, ports[sila][1], "snmp_community"),
            {"community": "nocLab"},
            ["1.3.6.1.2.1.1.1.0", "1.3.6.1.2.1.2.2.1.8.3"],
        )
        assert read.ok and read.values[1][1] == "2", "ifOperStatus.3 is down(2) after the cut"
        raised = _received(sink)
        assert {source for source, _oid in raised} == {pe01, sila}, "each end from its address"
        faults = {oid for _source, oid in raised}
        assert {noc_estate.LINK_DOWN, noc_estate.HW_PHY_DOWN, noc_estate.CIENA_DOWN} <= faults
        assert {noc_estate.HW_OPTICAL, noc_estate.CIENA_SD_SET} <= faults

        await estate.repair("L3")
        up = await ssh(pe01, "display interface GigabitEthernet1/0/0")
        assert "current state : UP" in up.output
        cleared = {oid for _source, oid in _received(sink)}
        for fault in faults:
            assert known_oids.clear_of(fault) in cleared, f"{fault} was raised and never cleared"
    finally:
        await estate.close()
        sink.close()


async def test_a_lost_power_downs_the_neighbour_and_comes_back_with_a_cold_start(
    tmp_path: Path,
) -> None:
    sink = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sink.bind(("127.0.0.1", 0))
    sink.setblocking(False)
    estate = noc_estate.Estate(sink.getsockname(), "nocLab", tmp_path / "truth.jsonl", seed=2)
    try:
        await estate.power_off("127.0.40.3")
        lost = _received(sink)
        assert lost == [("127.0.10.1", noc_estate.LINK_DOWN)], "only the neighbour can say it"
        await estate.power_on("127.0.40.3", boot_s=(0.0, 0.01))
        back = _received(sink)
        assert back[0] == ("127.0.40.3", noc_estate.COLD_START)
        assert ("127.0.10.1", noc_estate.LINK_UP) in back
        await asyncio.sleep(0)
    finally:
        await estate.close()
        sink.close()
