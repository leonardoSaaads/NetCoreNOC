"""Two lab devices the NOC can log in to: a MikroTik switch and a Ciena 3930 (ADR #469).

The fibre-cut lab beside this file (`run_local.py`) proves the Broker groups a storm; this one is
for the NOC plane — a case to take, an agent to run commands on the equipment, a person to watch it
happen in the console. Each device binds its own loopback address and answers like the real thing,
enough to work a case on it:

* **SSH** — the read-only commands the executor allows: RouterOS ``print``s on the MikroTik
  (``/interface print``, ``/interface ethernet monitor ether5 once``, ``/log print``…), SAOS
  ``show``s on the Ciena (``show alarm list``, ``show port 3``, ``show xcvr status port 3``…);
  exec requests and an interactive shell alike;
* **SNMP GET** (v2c) — sysDescr, sysObjectID, sysUpTime, sysName, ifDescr and ifOperStatus;
* **traps** — what a cut of the trunk between them raises (the Ciena's signal degrade, then
  ``linkDown`` at both ends), and what the repair clears, each from its device's own address.

``cut`` and ``repair`` send those traps and change what the CLIs and SNMP answer, so an agent that
logs in after a cut sees the port down and the optical power gone.

    python tests/lab/noc_devices.py serve --password 'lab-Passw0rd!'   # both devices; Ctrl-C stops
    python tests/lab/noc_devices.py cut --target 127.0.0.1:1162
    python tests/lab/noc_devices.py repair --target 127.0.0.1:1162
    python tests/lab/noc_devices.py status

Development tooling: never shipped, never imported by the product. The trunk's state is one JSON
file (``tests/lab/state/noc.json``), written atomically, and each device's host key is kept beside
it so the key the vault pins survives a restart.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncssh
from pyasn1.codec.ber import decoder, encoder
from pysnmp.proto import api

_HERE = Path(__file__).resolve()
for _base in _HERE.parents:
    if (_base / "tools" / "trap_replay.py").is_file():
        sys.path.insert(0, str(_base / "tools"))
        break

import trap_replay  # noqa: E402


def _say(text: str) -> None:
    sys.stdout.write(text + "\n")


STATE_DIR = _HERE.parent / "state"
USERNAME = "noc"
_P = api.PROTOCOL_MODULES[api.SNMP_VERSION_2C]
LINK_DOWN, LINK_UP = "1.3.6.1.6.3.1.1.5.3", "1.3.6.1.6.3.1.1.5.4"
CIENA_DEGRADE_SET, CIENA_DEGRADE_CLEAR = "1.3.6.1.4.1.1271.2.2.2.0.5", "1.3.6.1.4.1.1271.2.2.2.0.6"


@dataclass(frozen=True)
class Device:
    key: str
    name: str
    address: str
    descr: str
    object_id: str
    ports: tuple[str, ...]
    trunk: int  # the ifIndex of the port facing the other device


DEVICES = (
    Device(
        "mikrotik",
        "mk-crs326-lab",
        "127.0.10.1",
        "RouterOS CRS326-24G-2S+",
        "1.3.6.1.4.1.14988.1",
        ("ether1", "ether2", "ether3", "ether4", "ether5", "sfp-sfpplus1"),
        5,
    ),
    Device(
        "ciena",
        "ciena-3930-lab",
        "127.0.10.2",
        "Ciena 3930 Service Delivery Switch, SAOS 10.10.1",
        "1.3.6.1.4.1.1271.1.2.5",
        ("1", "2", "3", "4", "5", "6"),
        3,
    ),
)


# -- the trunk's state ---------------------------------------------------------------------------


def read_state(state_dir: Path) -> dict[str, Any]:
    try:
        return dict(json.loads((state_dir / "noc.json").read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {"trunk": "up", "since": time.time()}


def write_state(state_dir: Path, trunk: str) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    temp = state_dir / "noc.json.tmp"
    temp.write_text(json.dumps({"trunk": trunk, "since": time.time()}), encoding="utf-8")
    os.replace(temp, state_dir / "noc.json")


# -- what each CLI answers -------------------------------------------------------------------------


def _clock(epoch: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(epoch))


def _uptime(started: float) -> str:
    s = int(time.time() - started)
    return f"{s // 86400}d{s % 86400 // 3600:02d}h{s % 3600 // 60:02d}m{s % 60:02d}s"


def routeros(device: Device, line: str, state: dict[str, Any], started: float) -> tuple[str, int]:
    words = line.replace("/", " ").split()
    down = state["trunk"] == "down"
    trunk = device.ports[device.trunk - 1]
    if words[:2] == ["system", "identity"]:
        return f"  name: {device.name}\n", 0
    if words[:2] == ["system", "resource"]:
        return (
            f"                   uptime: {_uptime(started)}\n"
            "                  version: 7.16.1 (stable)\n"
            "               board-name: CRS326-24G-2S+\n"
            "                 cpu-load: 4%\n"
            "              free-memory: 401.2MiB\n"
            "             total-memory: 512.0MiB\n"
        ), 0
    if words[:2] == ["interface", "ethernet"] and "monitor" in words:
        name = words[words.index("monitor") + 1] if len(words) > words.index("monitor") + 1 else ""
        if name not in device.ports:
            return f"no such item ({name})\n", 1
        if name == trunk and down:
            return f"                   name: {name}\n                 status: no-link\n", 0
        return (
            f"                   name: {name}\n                 status: link-ok\n"
            "                   rate: 1Gbps\n            full-duplex: yes\n"
        ), 0
    if words[:1] == ["interface"] and "print" in words:
        rows = [
            "Flags: R - RUNNING",
            "Columns: NAME, TYPE, ACTUAL-MTU, MAC-ADDRESS",
            " #   NAME           TYPE   ACTUAL-MTU  MAC-ADDRESS",
        ]
        for i, name in enumerate(device.ports):
            flag = " " if (name == trunk and down) else "R"
            rows.append(f"{i:2} {flag} {name:<14} ether        1500  48:A9:8A:10:00:{i + 1:02X}")
        return "\n".join(rows) + "\n", 0
    if words[:2] == ["ip", "address"]:
        return (
            "Columns: ADDRESS, NETWORK, INTERFACE\n #  ADDRESS          NETWORK      INTERFACE\n"
            f" 0  10.20.0.1/30     10.20.0.0    {trunk}\n 1  192.0.2.10/24    192.0.2.0    ether1\n"
        ), 0
    if words[:1] == ["log"]:
        when = _clock(float(state["since"]))
        lines = [f"{_clock(started)} system,info router rebooted"]
        if down:
            lines.append(f"{when} interface,info {trunk} link down")
        elif float(state["since"]) > started:
            lines.append(f"{when} interface,info {trunk} link up (speed 1G, full duplex)")
        return "\n".join(lines) + "\n", 0
    if words[:1] == ["ping"]:
        target = words[1] if len(words) > 1 else "?"
        if down and target.startswith("10.20.0."):
            return (
                f"  SEQ HOST       SIZE TTL TIME  STATUS\n    0 {target}  timeout\n",
                0,
            )
        return f"  SEQ HOST       SIZE TTL TIME  STATUS\n    0 {target}  56  64 0ms\n", 0
    return f"bad command name {words[0] if words else ''} (line 1 column 2)\n", 1


def saos(device: Device, line: str, state: dict[str, Any], started: float) -> tuple[str, int]:
    words = line.split()
    down = state["trunk"] == "down"
    port = str(device.trunk)
    if words[:3] == ["show", "alarm", "list"] or words[:2] == ["show", "alarms"]:
        rows = ["Index  Severity  Resource   Condition                    Raised"]
        if down:
            since = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(float(state["since"])))
            rows.append(f"1      critical  port {port}     Loss of signal (LOS)         {since}")
            rows.append(f"2      major     port {port}     Link down                    {since}")
        else:
            rows.append("(no active alarms)")
        return "\n".join(rows) + "\n", 0
    if words[:2] == ["show", "port"]:
        wanted = words[2] if len(words) > 2 else None
        rows = ["Port  Admin  Oper   Speed   Description"]
        for name in device.ports:
            if wanted and name != wanted:
                continue
            oper = "down" if (name == port and down) else "up"
            what = "trunk to mk-crs326-lab ether5" if name == port else ""
            rows.append(f"{name:<5} up     {oper:<6} 1G      {what}")
        if wanted and len(rows) == 1:
            return f"error: no such port {wanted}\n", 1
        return "\n".join(rows) + "\n", 0
    if words[:3] == ["show", "xcvr", "status"]:
        rx = "-40.00 dBm (no light)" if down else "-5.21 dBm"
        return (
            f"Port {port}  1000BASE-LX  SFP  Ciena XCVR-A10D31\n"
            f"  Tx power: -4.80 dBm\n  Rx power: {rx}\n  Temperature: 41.2 C\n"
        ), 0
    if words[:2] in (["show", "system"], ["show", "software"]):
        return (
            f"Hostname: {device.name}\nPlatform: 3930\nSoftware: SAOS 10.10.1\n"
            f"Uptime: {_uptime(started)}\n"
        ), 0
    return f"error: unknown command '{line}'\n", 1


def answer(device: Device, line: str, state: dict[str, Any], started: float) -> tuple[str, int]:
    """What ``device`` prints for one command line, and its exit status."""
    cli = routeros if device.key == "mikrotik" else saos
    return cli(device, line.strip(), state, started)


# -- SSH -------------------------------------------------------------------------------------------


class _Login(asyncssh.SSHServer):
    def __init__(self, password: str) -> None:
        self.password = password

    def begin_auth(self, username: str) -> bool:
        return True

    def password_auth_supported(self) -> bool:
        return True

    def validate_password(self, username: str, password: str) -> bool:
        return username == USERNAME and password == self.password


def _prompt(device: Device) -> str:
    return f"[{USERNAME}@{device.name}] > " if device.key == "mikrotik" else f"{device.name}> "


async def _session(process: Any, device: Device, state_dir: Path, started: float) -> None:
    if process.command:
        text, status = answer(device, process.command, read_state(state_dir), started)
        process.stdout.write(text)
        process.exit(status)
        return
    process.stdout.write(_prompt(device))
    try:
        async for line in process.stdin:
            if line.strip() in ("exit", "quit", "/quit", "logout"):
                break
            if line.strip():
                text, _status = answer(device, line, read_state(state_dir), started)
                process.stdout.write(text)
            process.stdout.write(_prompt(device))
    except asyncssh.BreakReceived:
        pass
    process.exit(0)


def host_key(state_dir: Path, device: Device) -> asyncssh.SSHKey:
    """The device's host key, made once and kept, so the key the vault pinned stays valid."""
    path = state_dir / f"noc-{device.key}.key"
    if path.is_file():
        return asyncssh.read_private_key(str(path))
    key = asyncssh.generate_private_key("ssh-ed25519")
    state_dir.mkdir(parents=True, exist_ok=True)
    key.write_private_key(str(path))
    path.chmod(0o600)
    return key


# -- SNMP GET --------------------------------------------------------------------------------------


def mib(device: Device, oid: str, state: dict[str, Any], started: float) -> Any:
    """The value of one OID on ``device``, or None for one it does not have."""
    system = {
        "1.3.6.1.2.1.1.1.0": _P.OctetString(device.descr),
        "1.3.6.1.2.1.1.2.0": _P.ObjectIdentifier(device.object_id),
        "1.3.6.1.2.1.1.3.0": _P.TimeTicks(int((time.time() - started) * 100)),
        "1.3.6.1.2.1.1.5.0": _P.OctetString(device.name),
    }
    if oid in system:
        return system[oid]
    for table, column in (("1.3.6.1.2.1.2.2.1.2.", "descr"), ("1.3.6.1.2.1.2.2.1.8.", "oper")):
        if oid.startswith(table) and oid[len(table) :].isdigit():
            index = int(oid[len(table) :])
            if not 1 <= index <= len(device.ports):
                return None
            if column == "descr":
                return _P.OctetString(device.ports[index - 1])
            down = index == device.trunk and state["trunk"] == "down"
            return _P.Integer(2 if down else 1)
    return None


class _Agent(asyncio.DatagramProtocol):
    def __init__(self, device: Device, community: str, state_dir: Path, started: float) -> None:
        self.device, self.community, self.state_dir, self.started = (
            device,
            community,
            state_dir,
            started,
        )
        self.transport: Any = None

    def connection_made(self, transport: Any) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            message, _rest = decoder.decode(data, asn1Spec=_P.Message())
        except Exception:  # a lab agent drops what it cannot read, as devices do
            return
        if str(_P.apiMessage.get_community(message)) != self.community:
            return
        request = _P.apiMessage.get_pdu(message)
        if not request.isSameTypeWith(_P.GetRequestPDU()):
            return
        response = _P.apiMessage.get_response(message)
        pdu = _P.apiMessage.get_pdu(response)
        state = read_state(self.state_dir)
        binds = []
        for oid, _value in _P.apiPDU.get_varbinds(request):
            value = mib(self.device, str(oid), state, self.started)
            binds.append((oid, value if value is not None else _P.NoSuchObject()))
        _P.apiPDU.set_varbinds(pdu, binds)
        self.transport.sendto(encoder.encode(response), addr)


async def serve(
    password: str,
    community: str,
    state_dir: Path,
    *,
    ssh_port: int,
    snmp_port: int,
    devices: tuple[Device, ...] = DEVICES,
) -> list[tuple[Device, int, int, Any, Any]]:
    """Start each device's SSH server and SNMP agent; returns (device, ssh, snmp, servers…)."""
    started = time.time()
    loop = asyncio.get_running_loop()
    out = []
    for device in devices:

        def factory(process: Any, device: Device = device) -> Any:
            return _session(process, device, state_dir, started)

        server = await asyncssh.create_server(
            lambda: _Login(password),
            device.address,
            ssh_port,
            server_host_keys=[host_key(state_dir, device)],
            process_factory=factory,
        )
        transport, _protocol = await loop.create_datagram_endpoint(
            lambda device=device: _Agent(device, community, state_dir, started),  # type: ignore[misc]
            local_addr=(device.address, snmp_port),
        )
        out.append(
            (
                device,
                server.sockets[0].getsockname()[1],
                transport.get_extra_info("sockname")[1],
                server,
                transport,
            )
        )
    return out


# -- traps -----------------------------------------------------------------------------------------


def _link(trap: str, device: Device, uptime: int, community: str) -> bytes:
    index = device.trunk
    return trap_replay.encode_trap(
        trap,
        [
            {"oid": f"1.3.6.1.2.1.2.2.1.1.{index}", "value": str(index), "kind": "int"},
            {"oid": f"1.3.6.1.2.1.2.2.1.7.{index}", "value": "1", "kind": "int"},
            {
                "oid": f"1.3.6.1.2.1.2.2.1.8.{index}",
                "value": "2" if trap == LINK_DOWN else "1",
                "kind": "int",
            },
            {"oid": f"1.3.6.1.2.1.31.1.1.1.1.{index}", "value": device.ports[index - 1]},
        ],
        community,
        uptime,
    )


def traps(phase: str, community: str) -> list[tuple[Device, bytes, float]]:
    """A cut's or a repair's traps, in order: (device, PDU, seconds to wait before sending)."""
    mikrotik, ciena = DEVICES
    uptime = int(time.monotonic() * 100) % 2**32
    port = [{"oid": "1.3.6.1.4.1.1271.2.1.1.1.1", "value": str(ciena.trunk)}]
    if phase == "cut":
        return [
            (ciena, trap_replay.encode_trap(CIENA_DEGRADE_SET, port, community, uptime), 0.0),
            (ciena, _link(LINK_DOWN, ciena, uptime, community), 2.0),
            (mikrotik, _link(LINK_DOWN, mikrotik, uptime, community), 0.5),
        ]
    return [
        (ciena, trap_replay.encode_trap(CIENA_DEGRADE_CLEAR, port, community, uptime), 0.0),
        (ciena, _link(LINK_UP, ciena, uptime, community), 1.0),
        (mikrotik, _link(LINK_UP, mikrotik, uptime, community), 0.5),
    ]


def send(phase: str, target: tuple[str, int], community: str, state_dir: Path) -> int:
    """Change the trunk's state and send what the devices raise about it. Returns traps sent."""
    sender = trap_replay.Sender(target)
    try:
        write_state(state_dir, "down" if phase == "cut" else "up")
        for device, payload, wait in traps(phase, community):
            time.sleep(wait)
            sender.send(device.address, payload)
        return sender.sent
    finally:
        sender.close()


def _target(text: str) -> tuple[str, int]:
    host, _, port = text.rpartition(":")
    return host or "127.0.0.1", int(port)


async def _serve_forever(args: argparse.Namespace) -> None:
    running = await serve(
        args.password,
        args.community,
        args.state_dir,
        ssh_port=args.ssh_port,
        snmp_port=args.snmp_port,
    )
    for device, ssh_port, snmp_port, _server, _transport in running:
        _say(
            f"{device.name:<16} {device.address}  ssh :{ssh_port} (user {USERNAME})  "
            f"snmp v2c :{snmp_port} (community {args.community})  — {device.descr}"
        )
    _say(
        "Store each one in NOC → Secrets (an SSH password and an SNMP community, agent use on),"
        " then: noc_devices.py cut | repair.  Ctrl-C stops."
    )
    await asyncio.Event().wait()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("action", choices=("serve", "cut", "repair", "status"))
    parser.add_argument(
        "--password",
        default=os.environ.get("NOC_LAB_PASSWORD", ""),
        help=f"the SSH password of user {USERNAME!r} (or NOC_LAB_PASSWORD)",
    )
    parser.add_argument("--community", default="nocLab")
    parser.add_argument("--ssh-port", type=int, default=2222)
    parser.add_argument("--snmp-port", type=int, default=1161)
    parser.add_argument(
        "--target",
        type=_target,
        default=("127.0.0.1", 1162),
        help="the appliance's trap listener, HOST:PORT",
    )
    parser.add_argument("--state-dir", type=Path, default=STATE_DIR)
    args = parser.parse_args()
    if args.action == "status":
        _say(json.dumps(read_state(args.state_dir)))
        return 0
    if args.action in ("cut", "repair"):
        sent = send(args.action, args.target, args.community, args.state_dir)
        _say(f"{args.action}: {sent} trap(s) sent to {args.target[0]}:{args.target[1]}")
        return 0
    if len(args.password) < 12:
        parser.error("--password: at least 12 characters (the devices accept SSH logins)")
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_serve_forever(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
