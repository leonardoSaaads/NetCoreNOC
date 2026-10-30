"""A lab estate to work the NOC on: Huawei VRP, MikroTik RouterOS and Ciena SAOS 10 (ADR #476).

`noc_devices.py` is two devices and one trunk; this is the round that reviewed the console as its
operators use it — six elements of two customers on five links, and a driver that breaks and
mends them the way a network does, for hours:

* **a port that flaps** — both ends report it, and a Huawei end suppresses the port after four
  flaps in two minutes (control-flap) and releases it later;
* **a dirty connector** — CRC errors rising on a Huawei port, then flaps, then the resume;
* **light that fades, then a fibre cut** — the optics fall below the receiver's floor (Huawei
  `hwOpticalInvalid`, Ciena signal degrade), then loss of signal and the link down at both ends;
  the repair clears them;
* **management lost** — the element stops answering SSH and SNMP (an iptables DROP on its address
  when run as root) and its traps stop reaching the NOC, while its links stay up;
* **power lost** — its neighbours report their ports down; on power the element sends coldStart.

Every element answers SSH (exec and shell, its vendor's read-only commands: `noc_estate_cli.py`)
and SNMP GET on its own loopback address, and sends its vendor's traps from it — the IF-MIB pair,
Huawei's DEVM and IFNET traps, Ciena's port notifications, MikroTik's temperature exception. Each
change of state and each trap is one JSON line of `truth.jsonl`, so what the appliance shows can be
checked against what happened.

    python tests/lab/noc_estate.py serve --password 'lab-Passw0rd!2026' --target 127.0.0.1:1162 \\
        [--soak 6h --seed 7]
    python tests/lab/noc_estate.py ctl flap L1 6 | cut L3 | repair L3 | crc L1 on | degrade L4
    python tests/lab/noc_estate.py ctl isolate 127.0.40.1 | restore 127.0.40.1 | status
    python tests/lab/noc_estate.py ctl power-off 127.0.40.3 | power-on 127.0.40.3
    python tests/lab/noc_estate.py ctl temp 127.0.40.3 on

Development tooling: never shipped, never imported by the product.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import random
import shutil
import socket
import subprocess  # nosec B404 - the lab's own iptables rule, never caller text
import sys
import time
from collections.abc import Awaitable, Callable, Coroutine
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

from noc_estate_cli import Element, Port, answer  # noqa: E402

import trap_replay  # noqa: E402

P = api.PROTOCOL_MODULES[api.SNMP_VERSION_2C]
USERNAME = "noc"
STATE_DIR = _HERE.parent / "state"
IF = "1.3.6.1.2.1.2.2.1"
IF_NAME = "1.3.6.1.2.1.31.1.1.1.1"
ENT_NAME = "1.3.6.1.2.1.47.1.1.1.1.7"
COLD_START, LINK_DOWN, LINK_UP = "1.3.6.1.6.3.1.1.5.1", "1.3.6.1.6.3.1.1.5.3", "1.3.6.1.6.3.1.1.5.4"
HW = "1.3.6.1.4.1.2011.5.25."
HW_PHY_DOWN, HW_PHY_UP = HW + "129.2.5.1", HW + "129.2.5.2"
HW_FLAP_SUPPRESS, HW_FLAP_RESUME = HW + "41.3.31", HW + "41.3.32"
HW_CRC_RISING, HW_CRC_RESUME = HW + "41.4.1", HW + "41.4.2"
HW_OPTICAL, HW_OPTICAL_RESUME = HW + "219.2.4.5", HW + "219.2.4.6"
CIENA = "1.3.6.1.4.1.1271.2.2.2.0."
CIENA_DOWN, CIENA_UP, CIENA_SD_SET, CIENA_SD_CLEAR = (CIENA + n for n in ("1", "2", "5", "6"))
CIENA_PORT = "1.3.6.1.4.1.1271.2.1.1.1.1"
MTX_TEMP = "1.3.6.1.4.1.14988.1.1.9.0.2"


def _say(text: str) -> None:
    sys.stdout.write(text + "\n")


def _optical(
    name: str, index: int, alias: str, speed: int, xcvr: str, low: float, rx: float
) -> Port:
    return Port(name, index, alias, True, speed, xcvr, low, 0.5, rx)


def build() -> tuple[list[Element], dict[str, dict[str, Any]]]:
    """The six elements and the five links between them. Everything is fictitious."""
    lr10 = ("10GBASE_LR", -14.4)
    els = [
        Element(
            "agg01",
            "tim-spo-tambore-agg01",
            "127.0.40.1",
            "huawei",
            "S6730-H48X6C",
            "Version 5.170 (S6730 V200R021C10SPC600)",
            "Huawei Versatile Routing Platform Software VRP (R) software,Version 5.170 (S6730)",
            "1.3.6.1.4.1.2011.2.23.669",
            [
                Port("MEth0/0/1", 5, "MGMT_OOB"),
                _optical(
                    "XGigabitEthernet0/0/1",
                    10,
                    "TO_TIM-RJO-BARRA-CRS326_SFP1_FIBRASUL",
                    10000,
                    *lr10,
                    -6.1,
                ),
                _optical(
                    "XGigabitEthernet0/0/2", 11, "TO_TIM-SPO-TAMBORE-PE01", 10000, *lr10, -3.2
                ),
                _optical(
                    "XGigabitEthernet0/0/4", 13, "TO_AKAM-GRU-PE01_GE1/0/2_IX", 10000, *lr10, -5.0
                ),
            ],
        ),
        Element(
            "pe01",
            "akam-gru-pe01",
            "127.0.40.2",
            "huawei",
            "NE40E-X8A",
            "Version 8.210 (NE40E V800R013C00SPC200)",
            "Huawei Versatile Routing Platform Software VRP (R) software, Version 8.210 (NE40E)",
            "1.3.6.1.4.1.2011.2.62.2.9",
            [
                Port("GigabitEthernet0/0/0", 2, "MGMT"),
                _optical(
                    "GigabitEthernet1/0/0",
                    3,
                    "TO_AKAM-SILA-3930_P3_ZAYO",
                    1000,
                    "1000BASE_LX",
                    -20.0,
                    -8.9,
                ),
                Port("GigabitEthernet1/0/1", 4, "AKAMAI_CACHE_GRU-01"),
                _optical(
                    "GigabitEthernet1/0/2",
                    5,
                    "TO_TIM-SPO-TAMBORE-AGG01_XGE0/0/4_IX",
                    10000,
                    *lr10,
                    -4.7,
                ),
                _optical(
                    "100GE2/0/0",
                    9,
                    "TO_AKAM-GHUNAN-5164_1/1_100G",
                    100000,
                    "100GBASE_LR4",
                    -10.6,
                    -2.2,
                ),
            ],
        ),
        Element(
            "crs326",
            "tim-rjo-barra-crs326",
            "127.0.10.1",
            "mikrotik",
            "CRS326-24G-2S+",
            "7.16.1 (stable)",
            "RouterOS CRS326-24G-2S+",
            "1.3.6.1.4.1.14988.1",
            [
                Port("ether1", 1, "MGMT"),
                Port("ether2", 2, "TIM POP Barra rack A"),
                Port("ether5", 5, "tim-rjo-barra-ccr2004 ether1"),
                _optical(
                    "sfp-sfpplus1",
                    25,
                    "TIM-SPO-TAMBORE-AGG01 XGE0/0/1 (FibraSul)",
                    10000,
                    "S+31DLC10D",
                    -14.4,
                    -6.4,
                ),
            ],
        ),
        Element(
            "ccr2004",
            "tim-rjo-barra-ccr2004",
            "127.0.40.3",
            "mikrotik",
            "CCR2004-16G-2S+",
            "7.15.3 (stable)",
            "RouterOS CCR2004-16G-2S+",
            "1.3.6.1.4.1.14988.1",
            [
                Port("ether1", 1, "uplink tim-rjo-barra-crs326 ether5"),
                Port("ether2", 2, "PPPoE clientes Barra"),
            ],
        ),
        Element(
            "sila3930",
            "akam-sila-ciena3930",
            "127.0.10.2",
            "ciena",
            "3930",
            "SAOS 10.10.1",
            "Ciena 3930 Service Delivery Switch, SAOS 10.10.1",
            "1.3.6.1.4.1.1271.1.2.5",
            [
                Port("1", 1, "AKAMAI SILA cache A"),
                _optical("3", 3, "TO AKAM-GRU-PE01 GE1/0/0 ZAYO", 1000, "1000BASE-LX", -22.0, -9.3),
            ],
        ),
        Element(
            "ghunan5164",
            "akam-ghunan-5164",
            "127.0.40.4",
            "ciena",
            "5164",
            "SAOS 10.11.1",
            "Ciena 5164 Router, SAOS 10.11.1",
            "1.3.6.1.4.1.1271.1.2.25",
            [
                _optical(
                    "1/1", 1, "TO AKAM-GRU-PE01 100GE2/0/0", 100000, "100GBASE-LR4", -10.6, -2.6
                ),
                Port("1/2", 2, "AKAMAI GHUNAN edge-1", speed_mbps=10000),
            ],
        ),
    ]
    by_key = {e.key: e for e in els}
    links: dict[str, dict[str, Any]] = {
        "L1": {
            "a": ("agg01", "XGigabitEthernet0/0/1"),
            "b": ("crs326", "sfp-sfpplus1"),
            "what": "TIM SP-RJ long haul (FibraSul)",
        },
        "L2": {
            "a": ("crs326", "ether5"),
            "b": ("ccr2004", "ether1"),
            "what": "TIM Barra local patch",
        },
        "L3": {
            "a": ("pe01", "GigabitEthernet1/0/0"),
            "b": ("sila3930", "3"),
            "what": "Akamai GRU-SILA (Zayo)",
        },
        "L4": {
            "a": ("pe01", "100GE2/0/0"),
            "b": ("ghunan5164", "1/1"),
            "what": "Akamai GRU-GHUNAN 100G",
        },
        "L5": {
            "a": ("agg01", "XGigabitEthernet0/0/4"),
            "b": ("pe01", "GigabitEthernet1/0/2"),
            "what": "TIM-Akamai IX",
        },
    }
    for link in links.values():
        (ak, ap), (bk, bp) = link["a"], link["b"]
        by_key[ak].peers[ap] = (by_key[bk].name, bp)
        by_key[bk].peers[bp] = (by_key[ak].name, ap)
        link.update(state="up", crc=False, flapping=False)
    return els, links


class Estate:
    """The elements, their links, and the acts that break and mend them."""

    def __init__(
        self,
        target: tuple[str, int],
        community: str,
        truth: Path,
        *,
        seed: int | None = None,
        firewall: bool = False,
    ) -> None:
        self.rng = random.Random(seed)
        elements, self.links = build()
        self.by_addr = {e.address: e for e in elements}
        self.by_key = {e.key: e for e in elements}
        self.community = community
        self.sender = trap_replay.Sender(target)
        self.firewall = firewall and shutil.which("iptables") is not None and os.geteuid() == 0
        self.busy: set[str] = set()
        self.tasks: set[asyncio.Task[Any]] = set()
        self.servers: list[Any] = []
        truth.parent.mkdir(parents=True, exist_ok=True)
        self.truth = truth.open("a", encoding="utf-8")

    async def close(self) -> None:
        """Stop every server, incident and socket this estate holds."""
        for task in list(self.tasks):
            task.cancel()
        for server in self.servers:
            server.close()
        for server in self.servers:
            if hasattr(server, "wait_closed"):
                await server.wait_closed()
        self.sender.close()
        self.truth.close()

    # -- the record and the traps --------------------------------------------------------------

    def note(self, kind: str, **data: Any) -> None:
        row = {"t": round(time.time(), 3), "at": time.strftime("%H:%M:%S"), "kind": kind, **data}
        self.truth.write(json.dumps(row) + "\n")
        self.truth.flush()

    def trap(self, el: Element, oid: str, binds: list[dict[str, str]], what: str) -> None:
        if not el.power or not el.mgmt:
            self.note("trap-lost", el=el.name, what=what)  # nothing leaves a dead or cut-off box
            return
        payload = trap_replay.encode_trap(oid, binds, self.community, el.uptime_ticks())
        self.sender.send(el.address, payload)
        self.note("trap", el=el.name, addr=el.address, oid=oid, what=what)

    @staticmethod
    def _index(p: Port) -> dict[str, str]:
        return {"oid": f"{IF}.1.{p.index}", "value": str(p.index), "kind": "int"}

    def _link_trap(self, el: Element, p: Port, up: bool) -> None:
        name_oid = f"{IF}.2.{p.index}" if el.vendor == "huawei" else f"{IF_NAME}.{p.index}"
        binds = [
            self._index(p),
            {"oid": f"{IF}.7.{p.index}", "value": "1", "kind": "int"},
            {"oid": f"{IF}.8.{p.index}", "value": "1" if up else "2", "kind": "int"},
            {"oid": name_oid, "value": p.name},
        ]
        self.trap(el, LINK_UP if up else LINK_DOWN, binds, f"link{'Up' if up else 'Down'} {p.name}")
        if el.key == "pe01":  # VRP8 reports the physical change from DEVM too
            self.trap(
                el,
                HW_PHY_UP if up else HW_PHY_DOWN,
                [self._index(p), {"oid": f"{ENT_NAME}.{16842753 + p.index}", "value": p.name}],
                f"hwPortPhysical{'Up' if up else 'Down'} {p.name}",
            )
        if el.vendor == "ciena":
            self.trap(
                el,
                CIENA_UP if up else CIENA_DOWN,
                [
                    {"oid": CIENA_PORT, "value": p.name},
                    {"oid": f"{IF_NAME}.{p.index}", "value": p.name},
                ],
                f"cienaCesPortNotificationPort{'Up' if up else 'Down'} {p.name}",
            )

    def port_change(self, el: Element, name: str, up: bool, cause: str) -> None:
        st, p = el.port(name), el.find(name)
        if p is None or (st["oper"] == "up") == up:
            return
        st.update(oper="up" if up else "down", transitions=st["transitions"] + 1)
        st["last_up" if up else "last_down"] = time.time()
        self.note("port", el=el.name, port=name, oper=st["oper"], cause=cause)
        word = "UP" if up else "DOWN"
        if el.vendor == "huawei":
            el.say(
                f"%%01IFPDT/4/IF_STATE(l)[{{seq}}]:Interface {name} has turned into {word} state."
            )
            if up:
                el.clear_alarm(f"phy:{name}")
            else:
                el.raise_alarm(
                    f"phy:{name}",
                    "hwPortPhysicalDown",
                    "Major",
                    f"The physical status of the port changed to down.({name}, {cause})",
                )
        elif el.vendor == "mikrotik":
            el.say(f"interface,info {name} link {'up' if up else 'down'}")
        else:
            el.say(f"PORT-{name}: Link {'Up' if up else 'Down'} ({cause})")
            if up:
                el.clear_alarm(f"link:{name}")
            else:
                el.raise_alarm(
                    f"link:{name}", "linkDown", "Major", "Link down", resource=f"port {name}"
                )
        self._link_trap(el, p, up)

    def optics(self, el: Element, name: str, rx: float) -> None:
        st, p = el.port(name), el.find(name)
        if p is None:
            return
        was_low, low = st["rx"] < p.rx_low, rx < p.rx_low
        st["rx"] = rx
        self.note("optics", el=el.name, port=name, rx=round(rx, 2))
        if low == was_low:
            return
        if el.vendor == "huawei":
            if low:
                el.raise_alarm(
                    f"opt:{name}",
                    "hwOpticalInvalid",
                    "Major",
                    f"The optical module is invalid.({name}, input power too low)",
                )
            else:
                el.clear_alarm(f"opt:{name}")
            binds = [{"oid": f"{ENT_NAME}.{16842753 + p.index}", "value": name}]
            what = f"hwOpticalInvalid{'' if low else 'Resume'} {name} rx={rx:.1f}"
            self.trap(el, HW_OPTICAL if low else HW_OPTICAL_RESUME, binds, what)
        elif el.vendor == "ciena":
            if low:
                el.raise_alarm(
                    f"sd:{name}",
                    "SignalDegrade",
                    "Minor",
                    "Signal degrade (low Rx)",
                    resource=f"port {name}",
                )
            else:
                el.clear_alarm(f"sd:{name}")
            what = f"cienaCesPortNotificationPortSignalDegrade{'Set' if low else 'Clear'} {name}"
            self.trap(
                el,
                CIENA_SD_SET if low else CIENA_SD_CLEAR,
                [{"oid": CIENA_PORT, "value": name}],
                what,
            )
        el.say(f"{name}: rx power {rx:.2f} dBm {'below' if low else 'back above'} {p.rx_low:.1f}")

    # -- the acts --------------------------------------------------------------------------------

    def ends(self, lid: str) -> list[tuple[Element, str]]:
        link = self.links[lid]
        return [(self.by_key[k], p) for k, p in (link["a"], link["b"])]

    async def flap(self, lid: str, times: int = 6, gap: tuple[float, float] = (8, 40)) -> None:
        self.note("scenario", name="flap", link=lid, times=times, what=self.links[lid]["what"])
        hits: list[float] = []
        for _ in range(times):
            for el, p in self.ends(lid):
                if not el.port(p)["suppressed"]:
                    self.port_change(el, p, False, "flap")
            await asyncio.sleep(self.rng.uniform(1.5, 6))
            for el, p in self.ends(lid):
                if el.power and not el.port(p)["suppressed"]:
                    self.port_change(el, p, True, "flap")
            hits = [h for h in [*hits, time.time()] if h > time.time() - 120]
            for el, p in self.ends(lid):
                if el.vendor == "huawei" and len(hits) >= 4 and not el.port(p)["suppressed"]:
                    self.suppress(el, p)
            await asyncio.sleep(self.rng.uniform(*gap))

    def _flap_trap(self, el: Element, name: str, oid: str, what: str) -> None:
        p = el.find(name)
        if p is not None:
            self.trap(el, oid, [self._index(p), {"oid": f"{IF}.2.{p.index}", "value": name}], what)

    def suppress(self, el: Element, name: str, hold: tuple[float, float] = (90, 240)) -> None:
        """Huawei control-flap: the port flapped too often; its protocol is held down a while."""
        el.port(name)["suppressed"] = True
        el.raise_alarm(f"cf:{name}", "hwIfControlFlapSuppress", "Minor", f"{name} suppressed")
        self.note("suppress", el=el.name, port=name)
        self._flap_trap(el, name, HW_FLAP_SUPPRESS, f"hwIfControlFlapSuppress {name}")

        async def release() -> None:
            await asyncio.sleep(self.rng.uniform(*hold))
            el.port(name)["suppressed"] = False
            el.clear_alarm(f"cf:{name}")
            self.note("unsuppress", el=el.name, port=name)
            self._flap_trap(el, name, HW_FLAP_RESUME, f"hwIfControlFlapResume {name}")

        self.spawn(release())

    async def crc(self, lid: str, on: bool) -> None:
        self.links[lid]["crc"] = on
        self.note("scenario", name="crc", link=lid, on=on, what=self.links[lid]["what"])
        for el, name in self.ends(lid):
            p = el.find(name)
            if p is None:
                continue
            if on:
                el.port(name)["crc"] += self.rng.randint(800, 4000)
            if el.vendor == "huawei":
                binds = [
                    self._index(p),
                    {"oid": f"{IF}.2.{p.index}", "value": name},
                    {"oid": HW + "41.1.7.1.1.8", "value": str(el.port(name)["crc"]), "kind": "int"},
                ]
                what = f"hwIfMonitorCrcError{'Rising' if on else 'Resume'} {name}"
                self.trap(el, HW_CRC_RISING if on else HW_CRC_RESUME, binds, what)

    async def degrade(self, lid: str, step_s: tuple[float, float] = (5, 20)) -> None:
        self.note("scenario", name="degrade", link=lid, what=self.links[lid]["what"])
        for el, name in self.ends(lid):
            p = el.find(name)
            if p is None or not p.optical:
                continue
            target, rx = p.rx_low - self.rng.uniform(1.5, 5), float(el.port(name)["rx"])
            for step in range(4):  # light fades over a minute or two
                rx += (target - rx) / (4 - step)
                self.optics(el, name, rx)
                await asyncio.sleep(self.rng.uniform(*step_s))

    async def cut(self, lid: str) -> None:
        self.links[lid]["state"] = "cut"
        self.note("scenario", name="cut", link=lid, what=self.links[lid]["what"])
        for el, name in self.ends(lid):
            self.optics(el, name, -40.0)
        await asyncio.sleep(self.rng.uniform(0.2, 1.5))
        for el, name in self.ends(lid):
            self.port_change(el, name, False, "LOS")
            if el.vendor == "ciena":
                el.raise_alarm(
                    f"los:{name}",
                    "LOS",
                    "Critical",
                    "Loss of signal (LOS)",
                    resource=f"port {name}",
                )

    async def repair(self, lid: str) -> None:
        self.note("scenario", name="repair", link=lid, what=self.links[lid]["what"])
        for el, name in self.ends(lid):
            p = el.find(name)
            if p is not None and p.optical:
                self.optics(el, name, p.rx_nominal + self.rng.uniform(-0.8, 0.4))
            el.clear_alarm(f"los:{name}")
        await asyncio.sleep(self.rng.uniform(0.5, 2))
        for el, name in self.ends(lid):
            if el.power:
                self.port_change(el, name, True, "repaired")
        if self.links[lid]["crc"]:
            await self.crc(lid, False)
        self.links[lid]["state"] = "up"

    def _drop(self, address: str, drop: bool) -> None:
        """An iptables DROP of everything addressed to the element: ping, SSH and SNMP time out."""
        if not self.firewall:
            return
        rule = ["iptables", "-D" if not drop else "-I", "INPUT", "-d", address, "-j", "DROP"]
        if drop:
            subprocess.run(rule, check=False)  # nosec B603 - a literal rule and a lab address
            return
        while subprocess.run(rule, check=False, capture_output=True).returncode == 0:  # nosec B603
            pass

    async def isolate(self, address: str) -> None:
        el = self.by_addr[address]
        self.note("scenario", name="isolate", el=el.name, what="management lost")
        el.mgmt = False
        self._drop(address, True)

    async def restore(self, address: str) -> None:
        el = self.by_addr[address]
        self.note("scenario", name="restore", el=el.name)
        self._drop(address, False)
        el.mgmt = True

    def _neighbours_of(self, el: Element) -> list[tuple[str, Element, str]]:
        """(its port, the neighbour, the neighbour's port) for each link of ``el``."""
        out = []
        for link in self.links.values():
            for (k, p), (ok, op) in ((link["a"], link["b"]), (link["b"], link["a"])):
                if k == el.key:
                    out.append((p, self.by_key[ok], op))
        return out

    async def power_off(self, address: str) -> None:
        el = self.by_addr[address]
        self.note("scenario", name="power-off", el=el.name)
        el.power = False
        self._drop(address, True)
        for port, peer, peer_port in self._neighbours_of(el):
            el.port(port)["oper"] = "down"
            await asyncio.sleep(self.rng.uniform(0.05, 0.6))
            self.port_change(peer, peer_port, False, f"peer {el.name} lost power")

    async def power_on(self, address: str, boot_s: tuple[float, float] = (2, 6)) -> None:
        el = self.by_addr[address]
        self.note("scenario", name="power-on", el=el.name)
        el.booted, el.power = time.time() - 45, True
        el.alarms.clear()
        self._drop(address, False)
        self.trap(el, COLD_START, [{"oid": "1.3.6.1.2.1.1.5.0", "value": el.name}], "coldStart")
        await asyncio.sleep(self.rng.uniform(*boot_s))
        for port, peer, peer_port in self._neighbours_of(el):
            self.port_change(el, port, True, "boot")
            self.port_change(peer, peer_port, True, f"peer {el.name} back")

    async def temp(self, address: str, on: bool) -> None:
        el = self.by_addr[address]
        el.temperature = 81 if on else 52
        self.note("scenario", name="temp", el=el.name, on=on)
        el.say(f"system,{'error' if on else 'info'} cpu-temperature {el.temperature}C")
        if on:
            binds = [
                {
                    "oid": "1.3.6.1.4.1.14988.1.1.3.10.0",
                    "value": str(el.temperature * 10),
                    "kind": "int",
                }
            ]
            self.trap(el, MTX_TEMP, binds, f"mtxrTemperatureException {el.temperature}C")

    # -- the soak --------------------------------------------------------------------------------

    def spawn(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
        task = asyncio.ensure_future(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def _hold(self, names: tuple[str, ...], run: Callable[[], Awaitable[None]]) -> None:
        if any(n in self.busy for n in names):
            return  # that link or element is already in an incident
        self.busy.update(names)
        try:
            await run()
        except Exception as exc:
            self.note("error", error=repr(exc))
        finally:
            self.busy.difference_update(names)

    async def incident(self, kind: str) -> None:
        """One incident as a network has them, held until it ends (minutes, sometimes an hour)."""
        r = self.rng
        if kind == "flap":
            lid = r.choice(["L1", "L5", "L2", "L1"])
            await self._hold((lid,), lambda: self.flap(lid, r.randint(3, 10)))
        elif kind == "dirty":
            lid = r.choice(["L1", "L5"])

            async def dirty() -> None:
                await self.crc(lid, True)
                await asyncio.sleep(r.uniform(60, 300))
                await self.flap(lid, r.randint(4, 8))
                await asyncio.sleep(r.uniform(300, 900))
                await self.crc(lid, False)

            await self._hold((lid,), dirty)
        elif kind in ("cut", "degrade"):
            lid = r.choice(["L3", "L1", "L4", "L3"] if kind == "cut" else ["L3", "L4"])

            async def fibre() -> None:
                if kind == "degrade" or r.random() < 0.6:
                    await self.degrade(lid)
                    await asyncio.sleep(r.uniform(30, 240))
                if kind == "cut":
                    await self.cut(lid)
                await asyncio.sleep(r.uniform(900, 2700))
                await self.repair(lid)

            await self._hold((lid,), fibre)
        elif kind == "isolate":
            address = r.choice(["127.0.40.1", "127.0.40.2", "127.0.10.1"])

            async def lost() -> None:
                await self.isolate(address)
                await asyncio.sleep(r.uniform(240, 1200))
                await self.restore(address)

            await self._hold((address,), lost)
        elif kind == "power":
            address, lid = r.choice([("127.0.40.3", "L2"), ("127.0.40.4", "L4")])

            async def power() -> None:
                await self.power_off(address)
                await asyncio.sleep(r.uniform(180, 900))
                await self.power_on(address)

            await self._hold((address, lid), power)
        elif kind == "temp":
            address = r.choice(["127.0.40.3", "127.0.10.1"])

            async def hot() -> None:
                await self.temp(address, True)
                await asyncio.sleep(r.uniform(300, 1200))
                await self.temp(address, False)

            await self._hold((address + "/temp",), hot)

    async def soak(self, seconds: float, every: tuple[float, float] = (180, 720)) -> None:
        """Incidents at random for ``seconds``, a new one every three to twelve minutes."""
        end = time.time() + seconds
        self.note("soak", seconds=seconds)
        kinds = ["flap"] * 4 + ["dirty", "cut"] * 2 + ["degrade", "power", "temp"] + ["isolate"] * 2
        while time.time() < end:
            self.spawn(self.incident(self.rng.choice(kinds)))
            await asyncio.sleep(self.rng.uniform(*every))
        self.note("soak-end")

    # -- the control port ------------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        return {
            "links": {
                k: {x: v[x] for x in ("state", "crc", "what")} for k, v in self.links.items()
            },
            "busy": sorted(self.busy),
            "elements": {
                e.name: {
                    "address": e.address,
                    "power": e.power,
                    "mgmt": e.mgmt,
                    "down": [p for p, s in e.state.items() if s["oper"] != "up" and p in e.peers],
                    "suppressed": [p for p, s in e.state.items() if s["suppressed"]],
                    "alarms": len(e.alarms),
                }
                for e in self.by_addr.values()
            },
        }

    async def command(self, words: list[str]) -> Any:
        """One control command (see the module's docstring); returns what to print."""
        acts: dict[str, Callable[[list[str]], Awaitable[Any]]] = {
            "flap": lambda a: self._background(self.flap(a[0], int(a[1]) if len(a) > 1 else 6)),
            "cut": lambda a: self.cut(a[0]),
            "repair": lambda a: self.repair(a[0]),
            "crc": lambda a: self.crc(a[0], a[1] == "on"),
            "degrade": lambda a: self._background(self.degrade(a[0])),
            "isolate": lambda a: self.isolate(a[0]),
            "restore": lambda a: self.restore(a[0]),
            "power-off": lambda a: self.power_off(a[0]),
            "power-on": lambda a: self.power_on(a[0]),
            "temp": lambda a: self.temp(a[0], a[1] == "on"),
            "soak": lambda a: self._background(self.soak(float(a[0]))),
        }
        if not words:
            return "usage: status | " + " | ".join(acts)
        if words[0] == "status":
            return self.status()
        act = acts.get(words[0])
        if act is None:
            return f"unknown command {words[0]!r}"
        await act(words[1:])
        return "ok"

    async def _background(self, coro: Coroutine[Any, Any, Any]) -> None:
        self.spawn(coro)

    async def control(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            reply = await self.command((await reader.readline()).decode().split())
        except (KeyError, IndexError, ValueError) as exc:
            reply = f"error: {exc!r}"
        writer.write((json.dumps(reply) + "\n").encode())
        await writer.drain()
        writer.close()


# -- SSH and SNMP ----------------------------------------------------------------------------------


class _Login(asyncssh.SSHServer):
    def __init__(self, password: str) -> None:
        self.password = password

    def begin_auth(self, username: str) -> bool:
        return True

    def password_auth_supported(self) -> bool:
        return True

    def validate_password(self, username: str, password: str) -> bool:
        return username == USERNAME and password == self.password


def _prompt(el: Element) -> str:
    return {
        "huawei": f"<{el.name}>",
        "mikrotik": f"[{USERNAME}@{el.name}] > ",
        "ciena": f"{el.name}> ",
    }[el.vendor]


async def _session(process: Any, el: Element, estate: Estate) -> None:
    def run(line: str) -> str:
        text, status = answer(el, line, time.time())
        estate.note("cli", el=el.name, cmd=line.strip(), status=status)
        return text

    if process.command:
        text, status = answer(el, process.command, time.time())
        estate.note("cli", el=el.name, cmd=process.command.strip(), status=status)
        process.stdout.write(text)
        process.exit(status)
        return
    process.stdout.write(_prompt(el))
    with contextlib.suppress(asyncssh.BreakReceived):
        async for line in process.stdin:
            if line.strip() in ("exit", "quit", "/quit", "logout"):
                break
            if line.strip():
                process.stdout.write(run(line))
            process.stdout.write(_prompt(el))
    process.exit(0)


def host_key(state_dir: Path, el: Element) -> asyncssh.SSHKey:
    """The element's host key, made once and kept, so the key the vault pinned stays valid."""
    path = state_dir / f"estate-{el.key}.key"
    if path.is_file():
        return asyncssh.read_private_key(str(path))
    key = asyncssh.generate_private_key("ssh-ed25519")
    state_dir.mkdir(parents=True, exist_ok=True)
    key.write_private_key(str(path))
    path.chmod(0o600)
    return key


def mib(el: Element, oid: str) -> Any:
    """The value of one OID on ``el``, or None for one it does not have."""
    system = {
        "1.3.6.1.2.1.1.1.0": lambda: P.OctetString(el.descr),
        "1.3.6.1.2.1.1.2.0": lambda: P.ObjectIdentifier(el.object_id),
        "1.3.6.1.2.1.1.3.0": lambda: P.TimeTicks(el.uptime_ticks()),
        "1.3.6.1.2.1.1.5.0": lambda: P.OctetString(el.name),
    }
    if oid in system:
        return system[oid]()
    ports = {p.index: p for p in el.ports}
    for prefix, column in (
        (f"{IF}.2.", "descr"),
        (f"{IF}.8.", "oper"),
        (f"{IF}.14.", "errors"),
        (f"{IF_NAME}.", "name"),
        ("1.3.6.1.2.1.31.1.1.1.18.", "alias"),
    ):
        tail = oid[len(prefix) :]
        if not oid.startswith(prefix) or not tail.isdigit() or int(tail) not in ports:
            continue
        p = ports[int(tail)]
        st = el.port(p.name)
        if column == "oper":
            return P.Integer(1 if st["oper"] == "up" else 2)
        if column == "errors":
            return P.Counter32(st["crc"])
        return P.OctetString(p.alias if column == "alias" else p.name)
    return None


class _Agent(asyncio.DatagramProtocol):
    def __init__(self, el: Element, estate: Estate) -> None:
        self.el, self.estate = el, estate
        self.transport: Any = None

    def connection_made(self, transport: Any) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if not self.el.power or not self.el.mgmt:
            return  # a dead or cut-off element answers nothing
        try:
            message, _rest = decoder.decode(data, asn1Spec=P.Message())
        except Exception:
            return
        if str(P.apiMessage.get_community(message)) != self.estate.community:
            return
        request = P.apiMessage.get_pdu(message)
        if not request.isSameTypeWith(P.GetRequestPDU()):
            return
        response = P.apiMessage.get_response(message)
        binds = []
        for oid, _value in P.apiPDU.get_varbinds(request):
            value = mib(self.el, str(oid))
            binds.append((oid, value if value is not None else P.NoSuchObject()))
        P.apiPDU.set_varbinds(P.apiMessage.get_pdu(response), binds)
        self.transport.sendto(encoder.encode(response), addr)


async def serve(
    estate: Estate, password: str, state_dir: Path, *, ssh_port: int, snmp_port: int
) -> dict[str, tuple[int, int]]:
    """Each element's SSH server and SNMP agent; returns ``{address: (ssh port, snmp port)}``."""
    loop = asyncio.get_running_loop()
    out = {}
    for el in estate.by_addr.values():
        estate._drop(el.address, False)

        def factory(process: Any, el: Element = el) -> Any:
            return _session(process, el, estate)

        server = await asyncssh.create_server(
            lambda: _Login(password),
            el.address,
            ssh_port,
            server_host_keys=[host_key(state_dir, el)],
            process_factory=factory,
        )
        transport, _protocol = await loop.create_datagram_endpoint(
            lambda el=el: _Agent(el, estate),  # type: ignore[misc]
            local_addr=(el.address, snmp_port),
        )
        estate.servers += [server, transport]
        out[el.address] = (
            int(server.sockets[0].getsockname()[1]),
            int(transport.get_extra_info("sockname")[1]),
        )
    return out


def _target(text: str) -> tuple[str, int]:
    host, _, port = text.rpartition(":")
    return host or "127.0.0.1", int(port)


def _seconds(text: str) -> float:
    unit = {"s": 1, "m": 60, "h": 3600}.get(text[-1:])
    return float(text[:-1]) * unit if unit else float(text)


async def _serve_forever(args: argparse.Namespace) -> None:
    estate = Estate(
        args.target, args.community, args.state_dir / "truth.jsonl", seed=args.seed, firewall=True
    )
    ports = await serve(
        estate, args.password, args.state_dir, ssh_port=args.ssh_port, snmp_port=args.snmp_port
    )
    for el in estate.by_addr.values():
        ssh, snmp = ports[el.address]
        _say(
            f"{el.name:<24} {el.address:<11} {el.vendor:<9} {el.model:<16} ssh :{ssh} snmp :{snmp}"
        )
    await asyncio.start_server(estate.control, "127.0.0.1", args.ctl_port)
    _say(f"control on 127.0.0.1:{args.ctl_port}; the record is {args.state_dir / 'truth.jsonl'}")
    if args.soak:
        estate.spawn(estate.soak(args.soak))
    await asyncio.Event().wait()


def _ctl(port: int, words: list[str]) -> None:
    with socket.create_connection(("127.0.0.1", port), timeout=60) as s:
        s.sendall((" ".join(words) + "\n").encode())
        data = b""
        while chunk := s.recv(65536):
            data += chunk
    _say(json.dumps(json.loads(data), indent=1))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    sub = parser.add_subparsers(dest="action", required=True)
    serve_cmd = sub.add_parser("serve")
    serve_cmd.add_argument("--password", default=os.environ.get("NOC_LAB_PASSWORD", ""))
    serve_cmd.add_argument("--community", default="nocLab")
    serve_cmd.add_argument("--target", type=_target, default=("127.0.0.1", 1162))
    serve_cmd.add_argument("--ssh-port", type=int, default=2222)
    serve_cmd.add_argument("--snmp-port", type=int, default=1161)
    serve_cmd.add_argument("--soak", type=_seconds, default=0.0)
    serve_cmd.add_argument("--seed", type=int, default=None)
    serve_cmd.add_argument("--state-dir", type=Path, default=STATE_DIR)
    for cmd in (serve_cmd, ctl := sub.add_parser("ctl")):
        cmd.add_argument("--ctl-port", type=int, default=8099)
    ctl.add_argument("words", nargs="+")
    args = parser.parse_args()
    if args.action == "ctl":
        _ctl(args.ctl_port, args.words)
        return 0
    if len(args.password) < 12:
        parser.error("--password: at least 12 characters (the elements accept SSH logins)")
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_serve_forever(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
