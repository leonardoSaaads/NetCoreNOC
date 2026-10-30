"""The lab estate's elements, and what each vendor's CLI prints about them (ADR #476).

Huawei VRP (an S6730 on V200R021, an NE40E on V800R013), MikroTik RouterOS 7 and Ciena SAOS 10 —
enough of each to work a case the way an engineer does: the interface table, one interface in
detail, the optics, the active alarms, the log, the neighbours, the version. `answer(element,
line, now)` reads the element's live state and returns (text, exit status); `noc_estate.py`
changes that state as the network breaks and mends.

Development tooling: never shipped, never imported by the product.
"""

from __future__ import annotations

import collections
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

TZ = "-03:00"


@dataclass
class Port:
    name: str
    index: int  # its ifIndex
    alias: str = ""
    optical: bool = False
    speed_mbps: int = 1000
    xcvr: str = ""
    rx_low: float = -14.4
    rx_high: float = 0.5
    rx_nominal: float = -5.4


@dataclass
class Element:
    key: str
    name: str
    address: str
    vendor: str  # "huawei" | "mikrotik" | "ciena"
    model: str
    software: str
    descr: str
    object_id: str
    ports: list[Port]
    booted: float = 0.0
    power: bool = True
    mgmt: bool = True
    cpu: int = 7
    temperature: int = 47
    state: dict[str, dict[str, Any]] = field(default_factory=dict)
    log: collections.deque[tuple[float, str]] = field(
        default_factory=lambda: collections.deque(maxlen=400)
    )
    alarms: dict[int, dict[str, Any]] = field(default_factory=dict)
    seq: int = 1800
    peers: dict[str, tuple[str, str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        now = time.time()
        rng = random.Random(self.address)
        self.booted = now - rng.randint(20, 90) * 86400
        for p in self.ports:
            self.state[p.name] = {
                "oper": "up",
                "last_up": now - rng.randint(3, 40) * 86400,
                "last_down": 0.0,
                "transitions": rng.randint(2, 9) * 2,
                "crc": 0,
                "suppressed": False,
                "rx": p.rx_nominal + rng.uniform(-0.6, 0.6),
                "tx": -2.1,
                "temp": 38.5,
                "util": rng.uniform(4, 61),
            }

    def port(self, name: str) -> dict[str, Any]:
        return self.state[name]

    def find(self, name: str) -> Port | None:
        want = hw_name(name) if self.vendor == "huawei" else name
        return next((p for p in self.ports if p.name == want), None)

    def mac(self, port: Port, sep: str = "-") -> str:
        a = [int(x) for x in self.address.split(".")]
        if sep == "-":
            return f"70c7-f2{a[2]:02x}-{a[3]:02x}{port.index:02x}"
        return f"48:A9:8A:{a[2]:02X}:{a[3]:02X}:{port.index:02X}"

    def neighbours(self) -> list[tuple[Port, str, str]]:
        """Each port with a live link: (port, neighbour's name, neighbour's port) — LLDP's view."""
        return [
            (p, *self.peers[p.name])
            for p in self.ports
            if p.name in self.peers and self.state[p.name]["oper"] == "up"
        ]

    def uptime_ticks(self) -> int:
        return int((time.time() - self.booted) * 100) % 2**32

    def raise_alarm(
        self, key: str, name: str, level: str, text: str, alarm_id: str = "-", resource: str = ""
    ) -> None:
        if any(a["key"] == key for a in self.alarms.values()):
            return
        self.seq += 1
        self.alarms[self.seq] = {
            "key": key,
            "name": name,
            "level": level,
            "text": text,
            "id": alarm_id,
            "t": time.time(),
            "resource": resource,
        }

    def clear_alarm(self, key: str) -> None:
        for seq in [s for s, a in self.alarms.items() if a["key"] == key]:
            del self.alarms[seq]

    def say(self, text: str) -> None:
        """One line in the element's log; `{seq}` becomes its sequence number, as VRP writes it."""
        self.seq += 1
        self.log.append((time.time(), text.replace("{seq}", str(self.seq))))


def stamp(epoch: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(epoch))


def _uptime_words(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 86400} days, {s % 86400 // 3600} hours, {s % 3600 // 60} minutes"


def _ros_uptime(seconds: float) -> str:
    s = int(seconds)
    w, d = divmod(s // 86400, 7)
    return f"{w}w{d}d{s % 86400 // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _table(rows: list[str]) -> str:
    return "\n".join(rows) + "\n"


# -- Huawei VRP ------------------------------------------------------------------------------------

_HW_SHORT = (
    ("XGE", "XGigabitEthernet"),
    ("GE", "GigabitEthernet"),
    ("MEth", "MEth"),
    ("100GE", "100GE"),
    ("Eth-Trunk", "Eth-Trunk"),
)
_UNRECOGNIZED = "                ^\nError: Unrecognized command found at '^' position.\n"


def hw_name(word: str) -> str:
    """`xge0/0/1`, `XGigabitEthernet 0/0/1`, `GE1/0/0` -> the interface's full VRP name."""
    w = word.replace(" ", "")
    low = w.lower()
    for short, full in sorted(_HW_SHORT, key=lambda pair: -len(pair[0])):
        for prefix in (full.lower(), short.lower()):
            if low.startswith(prefix) and re.fullmatch(r"[0-9/]+", w[len(prefix) :] or "x"):
                return full + w[len(prefix) :]
    return w


def _brief(name: str) -> str:
    return name.replace("XGigabitEthernet", "XGE").replace("GigabitEthernet", "GE")


def _abbrev(words: list[str], *wanted: str) -> bool:
    """VRP takes any unambiguous prefix: `dis int b` is `display interface brief`."""
    if len(words) < len(wanted):
        return False
    return all(
        w.lower() == x or (len(w) >= (3 if i == 0 else 1) and x.startswith(w.lower()))
        for i, (w, x) in enumerate(zip(words, wanted, strict=False))
    )


def _hw_interface(el: Element, port: Port, now: float) -> str:
    st = el.port(port.name)
    state = "UP" if st["oper"] == "up" or st["suppressed"] else "DOWN"
    proto = "DOWN(Dampening Suppressed)" if st["suppressed"] else state
    rate = int(st["util"] * 1e8) if st["oper"] == "up" else 0
    up = f"{stamp(st['last_up'])}{TZ}" if st["last_up"] else "-"
    down = f"{stamp(st['last_down'])}{TZ}" if st["last_down"] else "-"
    return _table(
        [
            f"{port.name} current state : {state} (ifindex: {port.index})",
            f"Line protocol current state : {proto}",
            f"Description: {port.alias}",
            "Route Port,The Maximum Transmit Unit is 9000",
            f"IP Sending Frames' Format is PKTFMT_ETHNT_2, Hardware address is {el.mac(port)}",
            f"Last physical up time   : {up}",
            f"Last physical down time : {down}",
            f"Current system time: {stamp(now)}{TZ}",
            f"Port Mode: {'COMMON FIBER' if port.optical else 'COMMON COPPER'}",
            f"Speed: {port.speed_mbps}, Loopback: NONE",
            "Duplex: FULL, Negotiation: DISABLE",
            f"Last 300 seconds input rate {rate} bits/sec, {rate // 9000} packets/sec",
            f"Last 300 seconds output rate {rate} bits/sec, {rate // 9000} packets/sec",
            "Input:  18244719318 packets, 21003318227210 bytes",
            f"  CRC:                {st['crc']:>11},  Giants:                         0",
            "Output: 17611029112 packets, 20221093370011 bytes",
            f"Physical up/down times: {st['transitions']}",
        ]
    )


def _hw_optics(el: Element, ports: list[Port]) -> str:
    out: list[str] = []
    rule = "-" * 61
    for p in ports:
        st = el.port(p.name)
        out += [
            f"{p.name} transceiver information:",
            rule,
            "Common information:",
            f"  Transceiver Type                       :{p.xcvr}",
            "  Connector Type                         :LC",
            f"  Wavelength(nm)                         :{1310 if 'LR' in p.xcvr else 1550}",
            rule,
            "Diagnostic information:",
            f"  Temperature(°C)                        :{st['temp']:.2f}",
            f"  Current RX Power(dBm)                  :{st['rx']:.2f}",
            f"  Default RX Power High Threshold(dBm)   :{p.rx_high:.2f}",
            f"  Default RX Power Low Threshold(dBm)    :{p.rx_low:.2f}",
            f"  Current TX Power(dBm)                  :{st['tx']:.2f}",
            rule,
        ]
    return _table(out)


def _hw_log(el: Element, rest: list[str]) -> str:
    lowered = [w.lower() for w in rest]
    count = 40
    if "size" in lowered and len(rest) > lowered.index("size") + 1:
        word = rest[lowered.index("size") + 1]
        count = max(1, min(400, int(word))) if word.isdigit() else count
    rows = [
        "Logging buffer configuration and contents : enabled",
        f"Current messages : {len(el.log)}",
    ]
    for t, text in list(el.log)[-count:][::-1]:
        when = time.strftime("%b %d %Y %H:%M:%S", time.localtime(t))
        rows.append(f"{when}{TZ} {el.name} {text}")
    return _table(rows)


def huawei(el: Element, line: str, now: float) -> tuple[str, int]:
    words = line.split()
    if not words:
        return "", 0
    if not _abbrev(words[:1], "display"):
        return _UNRECOGNIZED, 1
    rest = words[1:]
    if _abbrev(rest, "version"):
        return (
            "Huawei Versatile Routing Platform Software\n"
            f"VRP (R) software, {el.software}\n"
            f"HUAWEI {el.model} uptime is {_uptime_words(now - el.booted)}\n",
            0,
        )
    if _abbrev(rest, "interface", "brief"):
        rows = [
            "PHY: Physical",
            "(d): Dampening Suppressed",
            f"{'Interface':<30}{'PHY':<9}{'Protocol':<10}{'InUti':>6} {'OutUti':>6}"
            f" {'inErrors':>10}",
        ]
        for p in el.ports:
            st = el.port(p.name)
            phy = "up" if st["oper"] == "up" or st["suppressed"] else "down"
            proto = "down(d)" if st["suppressed"] else ("up" if st["oper"] == "up" else "down")
            util = f"{st['util']:.2f}%" if st["oper"] == "up" else "0%"
            rows.append(f"{p.name:<30}{phy:<9}{proto:<10}{util:>6} {util:>6} {st['crc']:>10}")
        return _table(rows), 0
    if _abbrev(rest, "interface", "description"):
        rows = [f"{'Interface':<30}{'PHY':<6}{'Protocol':<9}Description"]
        for p in el.ports:
            o = "up" if el.port(p.name)["oper"] == "up" else "down"
            rows.append(f"{_brief(p.name):<30}{o:<6}{o:<9}{p.alias}")
        return _table(rows), 0
    if _abbrev(rest[:1], "interface") and len(rest) >= 2:
        port = el.find("".join(rest[1:]))
        if port is None:
            return "Error: Wrong parameter found at '^' position.\n", 1
        return _hw_interface(el, port, now), 0
    if _abbrev(rest[:1], "transceiver"):
        name = hw_name(rest[2]) if len(rest) >= 3 and _abbrev(rest[1:2], "interface") else None
        ports = [p for p in el.ports if p.optical and (name is None or p.name == name)]
        if name and not ports:
            return "Info: The transceiver does not exist on the interface.\n", 0
        return _hw_optics(el, ports), 0
    if _abbrev(rest, "alarm", "active"):
        rows = ["Sequence/RootKind/Generated/Cleared/ID/Name/Level/State/Description", "-" * 78]
        if not el.alarms:
            rows.append("Info: There is no alarm.")
        for seq, a in sorted(el.alarms.items()):
            rows.append(
                f"{seq}/Independent/{stamp(a['t'])}{TZ}/-/{a['id']}/{a['name']}/{a['level']}"
                f"/Start/{a['text']}"
            )
        return _table(rows), 0
    if _abbrev(rest, "logbuffer") or _abbrev(rest, "trapbuffer"):
        return _hw_log(el, rest), 0
    if _abbrev(rest, "lldp", "neighbor", "brief"):
        rows = [f"{'Local Intf':<17}{'Neighbor Dev':<26}{'Neighbor Intf':<26}Exptime(s)"]
        for p, peer, peer_port in el.neighbours():
            rows.append(f"{_brief(p.name):<17}{peer:<26}{peer_port:<26}101")
        return _table(rows), 0
    if _abbrev(rest, "cpu-usage"):
        return f"CPU Usage            : {el.cpu}% Max: 41%\n", 0
    return _UNRECOGNIZED, 1


# -- MikroTik RouterOS -----------------------------------------------------------------------------


def _ros_monitor(el: Element, name: str) -> tuple[str, int]:
    port = el.find(name)
    if port is None:
        return f"no such item ({name})\n", 1
    st = el.port(port.name)
    rows = [f"                      name: {name}"]
    if st["oper"] != "up":
        rows.append("                    status: no-link")
    else:
        rows += [
            "                    status: link-ok",
            f"                      rate: {port.speed_mbps // 1000}Gbps",
            "               full-duplex: yes",
        ]
    if port.optical:
        rows += [
            "        sfp-module-present: yes",
            f"    sfp-vendor-part-number: {port.xcvr}",
            f"           sfp-temperature: {st['temp']:.0f}C",
            f"              sfp-tx-power: {st['tx']:.3f}dBm",
            f"              sfp-rx-power: {st['rx']:.3f}dBm",
        ]
    return _table(rows), 0


def routeros(el: Element, line: str, now: float) -> tuple[str, int]:
    words = line.replace("/", " ").split()
    if words[:2] == ["system", "identity"]:
        return f"  name: {el.name}\n", 0
    if words[:2] == ["system", "resource"]:
        return (
            f"                   uptime: {_ros_uptime(now - el.booted)}\n"
            f"                  version: {el.software}\n"
            f"               board-name: {el.model}\n"
            f"                 cpu-load: {el.cpu}%\n",
            0,
        )
    if words[:2] == ["system", "health"]:
        return (
            f" # NAME              VALUE  TYPE\n 0 cpu-temperature   {el.temperature:>3}    C\n",
            0,
        )
    if words[:2] == ["interface", "ethernet"] and "monitor" in words:
        i = words.index("monitor")
        return _ros_monitor(el, words[i + 1] if len(words) > i + 1 else "")
    if words[:2] == ["interface", "ethernet"] and "print" in words and "stats" in words:
        rows = [f"{'name':<16}{'rx-fcs-error':>14}{'link-downs':>12}"]
        for p in el.ports:
            st = el.port(p.name)
            rows.append(f"{p.name:<16}{st['crc']:>14}{st['transitions'] // 2:>12}")
        return _table(rows), 0
    if words[:1] == ["interface"] and "print" in words:
        rows = [
            "Flags: R - RUNNING",
            " #    NAME            TYPE   ACTUAL-MTU  MAC-ADDRESS        COMMENT",
        ]
        for i, p in enumerate(el.ports):
            flag = "R" if el.port(p.name)["oper"] == "up" else " "
            rows.append(
                f"{i:2} {flag}   {p.name:<15} ether        1500  {el.mac(p, ':')}  {p.alias}"
            )
        return _table(rows), 0
    if words[:2] == ["ip", "neighbor"]:
        rows = ["#  INTERFACE      IDENTITY"]
        rows += [f"{i}  {p.name:<14} {peer}" for i, (p, peer, _pp) in enumerate(el.neighbours())]
        return _table(rows), 0
    if words[:1] == ["log"]:
        rows = [f"{stamp(t)} {text}" for t, text in list(el.log)[-60:]]
        return _table(rows), 0
    if words[:1] == ["ping"]:
        target = words[1] if len(words) > 1 else "?"
        rows = [f"    {seq} {target:<40}   56  64 0ms412us" for seq in range(3)]
        return _table([*rows, "    sent=3 received=3 packet-loss=0%"]), 0
    return f"bad command name {words[0] if words else ''} (line 1 column 2)\n", 1


# -- Ciena SAOS 10 ---------------------------------------------------------------------------------


def _saos_ports(el: Element, wanted: str | None) -> tuple[str, int]:
    rows = ["Port   Admin  Oper   Speed   Duplex  Description"]
    for p in el.ports:
        if wanted and p.name != wanted:
            continue
        speed = f"{p.speed_mbps // 1000}G"
        rows.append(f"{p.name:<6} up     {el.port(p.name)['oper']:<6} {speed:<7} full    {p.alias}")
    if wanted and len(rows) == 1:
        return f"error: no such port {wanted}\n", 1
    return _table(rows), 0


def _saos_xcvr(el: Element, wanted: str | None) -> tuple[str, int]:
    out = []
    for p in el.ports:
        if not p.optical or (wanted and p.name != wanted):
            continue
        st = el.port(p.name)
        rx = "-40.00 dBm (no light)" if st["rx"] <= -39 else f"{st['rx']:.2f} dBm"
        out.append(
            f"Port {p.name}  {p.xcvr}  SFP\n  Tx power: {st['tx']:.2f} dBm\n"
            f"  Rx power: {rx}  (low alarm {p.rx_low:.1f} dBm)\n  Temperature: {st['temp']:.1f} C"
        )
    if wanted and not out:
        return f"error: no transceiver on port {wanted}\n", 1
    return _table(out), 0


def saos(el: Element, line: str, now: float) -> tuple[str, int]:
    words = line.split()
    if words[:1] != ["show"]:
        return f"error: unknown command '{line}'\n", 1
    rest = words[1:]
    if rest[:2] in (["alarm", "list"], ["alarm", "active"]) or rest[:1] == ["alarms"]:
        rows = ["Index | Severity | Resource   | Condition                      | Raised"]
        if not el.alarms:
            rows.append("(no active alarms)")
        for seq, a in sorted(el.alarms.items()):
            rows.append(
                f"{seq:<5} | {a['level'].lower():<8} | {a['resource']:<10} | "
                f"{a['text'][:30]:<30} | {stamp(a['t'])}"
            )
        return _table(rows), 0
    if rest[:2] == ["port", "statistics"]:
        rows = ["Port   RxCrcErr  LinkDowns"]
        for p in el.ports:
            st = el.port(p.name)
            rows.append(f"{p.name:<6} {st['crc']:<9} {st['transitions'] // 2}")
        return _table(rows), 0
    if rest[:1] == ["port"]:
        return _saos_ports(el, rest[1] if len(rest) > 1 else None)
    if rest[:2] == ["xcvr", "status"]:
        return _saos_xcvr(el, rest[3] if len(rest) > 3 and rest[2] == "port" else None)
    if rest[:2] == ["lldp", "neighbors"]:
        rows = ["Local Port  Neighbor System         Neighbor Port"]
        rows += [f"{p.name:<11} {peer:<24} {pp}" for p, peer, pp in el.neighbours()]
        return _table(rows), 0
    if rest[:1] in (["log"], ["logging"]):
        return _table([f"{stamp(t)} {text}" for t, text in list(el.log)[-60:]]), 0
    if rest[:1] in (["system"], ["software"]):
        return (
            f"Hostname: {el.name}\nPlatform: {el.model}\nSoftware: {el.software}\n"
            f"Uptime: {_uptime_words(now - el.booted)}\n",
            0,
        )
    return f"error: unknown command '{line}'\n", 1


CLIS: dict[str, Callable[[Element, str, float], tuple[str, int]]] = {
    "huawei": huawei,
    "mikrotik": routeros,
    "ciena": saos,
}


def answer(el: Element, line: str, now: float) -> tuple[str, int]:
    """What ``el`` prints for one command line, and its exit status."""
    return CLIS[el.vendor](el, line.strip(), now)
