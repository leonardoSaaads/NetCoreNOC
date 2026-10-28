"""The NOC lab's two devices answer the appliance's own executor (ADR #469).

The owner's test round, as a test: two devices to log in to — a MikroTik and a Ciena 3930 — at
their own addresses, reached by `engine/noc/executor.py` exactly as an agent's lease or a playbook
reaches equipment. Before a cut they say the trunk is up; after it the MikroTik's port has no link
and the Ciena lists a loss of signal; SNMP reads the same state; and a cut is three traps from two
source addresses.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pyasn1.codec.ber import decoder

import noc_devices
from netcorenoc.engine.noc import executor

PASSWORD = "lab-Passw0rd-for-the-test"


def _row(address: str, port: int, kind: str) -> dict[str, Any]:
    return {
        "host": address,
        "port": port,
        "access_level": "view_only",
        "kind": kind,
        "protocol_version": "v2c",
    }


async def test_the_devices_answer_ssh_and_snmp_and_follow_the_cut(tmp_path: Path) -> None:
    running = await noc_devices.serve(PASSWORD, "nocLab", tmp_path, ssh_port=0, snmp_port=0)
    try:
        (mk, mk_ssh, _mk_snmp, *_), (ci, ci_ssh, ci_snmp, *_) = running
        login = {"username": noc_devices.USERNAME, "password": PASSWORD}
        trunk = "/interface ethernet monitor ether5 once"
        up = await executor.ssh(_row(mk.address, mk_ssh, "ssh_password"), login, trunk, pinned=None)
        assert up.ok and "link-ok" in up.output and up.host_key, up
        listing = await executor.ssh(
            _row(mk.address, mk_ssh, "ssh_password"), login, "/interface print", pinned=up.host_key
        )
        assert listing.ok and "R ether5" in listing.output

        noc_devices.write_state(tmp_path, "down")
        down = await executor.ssh(
            _row(mk.address, mk_ssh, "ssh_password"), login, trunk, pinned=up.host_key
        )
        assert down.ok and "no-link" in down.output
        alarms = await executor.ssh(
            _row(ci.address, ci_ssh, "ssh_password"), login, "show alarm list", pinned=None
        )
        assert alarms.ok and "Loss of signal (LOS)" in alarms.output
        typed = await executor.ssh(
            _row(ci.address, ci_ssh, "ssh_password"),
            login,
            "show xcvr status port 3",
            pinned=alarms.host_key,
            mode="shell",
        )
        assert typed.ok and "-40.00 dBm (no light)" in typed.output
        refused = await executor.ssh(
            _row(ci.address, ci_ssh, "ssh_password"),
            {"username": noc_devices.USERNAME, "password": "not-it"},
            "show port 3",
            pinned=alarms.host_key,
        )
        assert not refused.ok and "credential was not accepted" in refused.detail

        read = await executor.snmp_get(
            _row(ci.address, ci_snmp, "snmp_community"),
            {"community": "nocLab"},
            ["1.3.6.1.2.1.1.1.0", "1.3.6.1.2.1.2.2.1.8.3"],
        )
        assert read.ok and "SAOS 10.10.1" in read.output, read
        assert read.values[1][1] == "2", "ifOperStatus.3 is down(2) after the cut"
    finally:
        for _device, _ssh, _snmp, server, transport in running:
            server.close()
            await server.wait_closed()
            transport.close()


def test_a_cut_is_three_traps_from_two_addresses() -> None:
    from pysnmp.proto import api

    p = api.PROTOCOL_MODULES[api.SNMP_VERSION_2C]
    cut = noc_devices.traps("cut", "nocLab")
    assert [d.key for d, _pdu, _wait in cut] == ["ciena", "ciena", "mikrotik"]
    assert len({d.address for d, _pdu, _wait in cut}) == 2
    oids = []
    for _device, pdu, _wait in cut:
        message, _rest = decoder.decode(pdu, asn1Spec=p.Message())
        binds = p.apiPDU.get_varbinds(p.apiMessage.get_pdu(message))
        oids.append(str(binds[1][1]))
    assert oids == [noc_devices.CIENA_DEGRADE_SET, noc_devices.LINK_DOWN, noc_devices.LINK_DOWN]
    repair = [d.key for d, _pdu, _wait in noc_devices.traps("repair", "nocLab")]
    assert repair == ["ciena", "ciena", "mikrotik"]
