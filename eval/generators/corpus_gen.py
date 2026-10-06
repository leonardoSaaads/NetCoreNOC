"""Deterministic generator for the labelled evaluation corpus.

Run ``python eval/generators/corpus_gen.py`` to (re)write ``eval/corpus/*.json``. Four scenarios are
read back out of the corpus and re-annotated with ground truth; the six new ones are
synthesised here. Everything is deterministic — no RNG, fixed ordering — so the committed
JSON is reproducible from this file, which is what ``make corpus`` checks.

Ground-truth schema, per event, under a ``truth`` key:

    situation_key : str    stable name of the correct situation (not a numeric id)
    entity_key    : str    the correct alarmed entity (an ONU, a port, a camera, ...)
    is_root       : bool   optional; the event is the true root cause of its situation
    severity      : str    optional; the correct severity token

The harness never reads ``truth`` while running the engine — only while scoring. The new
scenarios are sized to be *representative* of the phenomenon (proxied storms, containment,
decoys) while keeping the replay fast enough for CI; the discriminator on each learnable
NE is observed well over the promotion evidence floor (see docs/SCOPE-0.3.md).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CORPUS = Path(__file__).resolve().parents[1] / "corpus"
# The four pre-existing scenarios are read back out of the corpus and relabelled in place. Until
# v0.15.0 they were read from `tests/fixtures/`, which held the same event stream a second time
# with `description` and every `truth` block removed — two copies nothing compared, replayed by
# different gates. The copy is gone and the suite derives its unlabelled stream from these files
# instead (`tests/util.scenario`, #205). `_write` rewrites `truth` and `description` wholesale, so
# reading a labelled document here and reading an unlabelled one produce identical output; what
# this file still proves on every `make corpus` is that the labelling below reproduces the
# committed bytes.

# Standard varbind that the instance heuristic and the profiler both skip.
SYS_UPTIME = "1.3.6.1.2.1.1.3.0"


def _vb(oid: str, value: str, kind: str = "str") -> dict[str, str]:
    return {"oid": oid, "kind": kind, "value": value}


def _write(name: str, events: list[dict[str, Any]], description: str) -> None:
    events.sort(key=lambda e: float(e["delay"]))
    CORPUS.mkdir(parents=True, exist_ok=True)
    (CORPUS / name).write_text(
        json.dumps(
            {"name": name.removesuffix(".json"), "description": description, "events": events},
            indent=2,
        )
        + "\n"
    )


# -- the four existing fixtures, relabelled with ground truth --------------------------


def relabel_fiber_cut() -> None:
    """Two NEs, one fibre cut. Not proxied: each NE is its own level-0 entity, so the
    only varbind (port-1/1) is a constant and stays unpromoted — parity by construction."""
    data = json.loads((CORPUS / "fiber_cut.json").read_text())
    first = min(data["events"], key=lambda e: float(e["delay"]))
    for ev in data["events"]:
        ev["truth"] = {
            "situation_key": "fiber_cut",
            "entity_key": ev["source"],  # the reporting NE is the alarmed thing
            "is_root": ev is first,
        }
    _write("fiber_cut.json", data["events"], "Two NEs, one fibre cut; sender is the sufferer.")


def relabel_olt_storm() -> None:
    """One OLT, one uplink alarm and 500 ONU alarms. Each ONU appears in a single class,
    so its id never recurs across classes: the profiler correctly abstains and this
    scenario keeps v0.2.0 behaviour (grouping perfect, entity attribution to the NE)."""
    data = json.loads((CORPUS / "olt_storm.json").read_text())
    first = min(data["events"], key=lambda e: float(e["delay"]))
    for ev in data["events"]:
        vb = ev.get("varbinds", [{}])
        entity = vb[0]["value"] if vb else ev["source"]
        ev["truth"] = {
            "situation_key": "olt_storm",
            "entity_key": entity,
            "is_root": ev is first,
        }
    _write("olt_storm.json", data["events"], "One OLT: uplink down plus 500 ONU alarms.")


def relabel_background_noise() -> None:
    """Unrelated singletons that must never merge (over-merge guard)."""
    data = json.loads((CORPUS / "background_noise.json").read_text())
    for i, ev in enumerate(data["events"]):
        ev["truth"] = {
            "situation_key": f"noise-{ev['source']}-{ev['trap_oid']}-{i}",
            "entity_key": ev["source"],
        }
    _write("background_noise.json", data["events"], "Unrelated background traps; no incident.")


def relabel_flapping_noise() -> None:
    """A flapping link on one NE: every raise dedups to one alarm, one situation."""
    data = json.loads((CORPUS / "flapping_noise.json").read_text())
    for ev in data["events"]:
        ev["truth"] = {
            "situation_key": "flap-127.0.0.30-if7",
            "entity_key": "127.0.0.30",
        }
    _write("flapping_noise.json", data["events"], "One flapping link, demoted to noise.")


# -- six new scenarios ------------------------------------------------------------------

PON = "1.3.6.1.4.1.2011.6.128.1.1"  # Huawei-style PON MIB root
ONU_ID = f"{PON}.2.43.1"  # per-ONU identifier varbind
PORT_ID = f"{PON}.1.20.1"  # per-PON-port identifier varbind
ONU_LOS = f"{PON}.2.1"
ONU_GASP = f"{PON}.2.2"
ONU_FAULT = f"{PON}.2.3"
OLT_POWER = f"{PON}.1.1"
PON_PORT_DOWN = f"{PON}.1.2"


def gen_pon_dying_gasp() -> None:
    """One OLT, a site power outage: 350 ONUs each dying across three alarm classes, plus
    the OLT's own power trap (the root). The ONU id recurs across classes on one NE — the
    signature the profiler promotes; v0.2.0 attributes all of it to the OLT."""
    events: list[dict[str, Any]] = []
    olt = "10.10.0.1"
    t = 0.0
    events.append(
        {
            "delay": 0.0,
            "source": olt,
            "trap_oid": OLT_POWER,
            "varbinds": [_vb(SYS_UPTIME, "1000", "ticks"), _vb(f"{PON}.1.9", "psu-A")],
            "truth": {
                "situation_key": "pon_dying_gasp",
                "entity_key": "olt-psu-A",
                "is_root": True,
                "severity": "critical",
            },
        }
    )
    onus = [f"onu-{n}" for n in range(350)]
    # Per-ONU bursts: a dying ONU emits LOS, dying-gasp and fault together. This is realistic
    # and it lets the ONU id recur across classes from the first ONUs, so the profiler earns
    # its promotion early instead of only after every class has run in full.
    for onu in onus:
        for cls, sev in ((ONU_LOS, "major"), (ONU_GASP, "critical"), (ONU_FAULT, "minor")):
            t += 0.01
            events.append(
                {
                    "delay": round(t, 3),
                    "source": olt,
                    "trap_oid": cls,
                    "varbinds": [
                        _vb(SYS_UPTIME, "1000", "ticks"),
                        _vb(ONU_ID, onu),
                        _vb(f"{PON}.2.44", sev),
                    ],
                    "truth": {
                        "situation_key": "pon_dying_gasp",
                        "entity_key": onu,
                        "severity": sev,
                    },
                }
            )
    _write(
        "pon_dying_gasp.json",
        events,
        "One OLT proxies ~1050 ONU traps during a power outage; entity id is in varbinds.",
    )


def gen_pon_pon_port_down() -> None:
    """A PON line card fails four ports; their 300 ONUs lose signal. Every ONU trap carries
    both its PON-port id and its ONU id, so port -> ONU containment is a functional
    dependency the profiler can recover without a MIB."""
    events: list[dict[str, Any]] = []
    olt = "10.10.1.1"
    t = 0.0
    ports = [f"port-{k}" for k in range(4)]
    for k, port in enumerate(ports):
        t += 0.01
        events.append(
            {
                "delay": round(t, 3),
                "source": olt,
                "trap_oid": PON_PORT_DOWN,
                "varbinds": [_vb(SYS_UPTIME, "500", "ticks"), _vb(PORT_ID, port)],
                "truth": {
                    "situation_key": "pon_port_down",
                    "entity_key": port,
                    "is_root": k == 0,
                    "severity": "critical",
                },
            }
        )
    # 300 ONUs, 75 per port, each raising LOS then dying-gasp.
    # Phase-ordered (all LOS, then all dying-gasp): with only two ONU classes, per-ONU bursts
    # would make LOS/gasp strictly alternate on the heuristic port instance and train a false
    # clear pair under v0.2.0. This scenario's value is the port->ONU hierarchy (S6), not early
    # S5 promotion (the ONU and port ids are an ambiguous pair the margin correctly holds).
    for cls, sev in ((ONU_LOS, "major"), (ONU_GASP, "critical")):
        for n in range(300):
            port = ports[n // 75]
            onu = f"onu-{n}"
            t += 0.01
            events.append(
                {
                    "delay": round(t, 3),
                    "source": olt,
                    "trap_oid": cls,
                    "varbinds": [
                        _vb(SYS_UPTIME, "500", "ticks"),
                        _vb(PORT_ID, port),
                        _vb(ONU_ID, onu),
                        _vb(f"{PON}.2.44", sev),
                    ],
                    "truth": {"situation_key": "pon_port_down", "entity_key": onu, "severity": sev},
                }
            )
    _write(
        "pon_pon_port_down.json",
        events,
        "PON card fails 4 ports, 300 ONUs LOS; two-level port->ONU containment present.",
    )


CHASSIS = "1.3.6.1.4.1.9.9"  # Cisco-style chassis root
CARD_ID = f"{CHASSIS}.117.1.1.1"
CH_PORT_ID = f"{CHASSIS}.276.1.1.9"
CARD_FAIL = f"{CHASSIS}.117.2.0.1"
CH_PORT_DOWN = f"{CHASSIS}.276.1.1.1"
CH_PORT_FAULT = f"{CHASSIS}.276.1.1.2"


def gen_chassis_card_fail() -> None:
    """A chassis reports its own line cards and ports (proxied). Three cards, 16 ports each;
    a card failure takes its ports down and faulty. Port id recurs across two port classes,
    card id across three classes; card -> port is the containment FD."""
    events: list[dict[str, Any]] = []
    chassis = "192.0.2.7"
    t = 0.0
    cards = [f"card-{k}" for k in range(3)]
    for k, card in enumerate(cards):
        t += 0.01
        events.append(
            {
                "delay": round(t, 3),
                "source": chassis,
                "trap_oid": CARD_FAIL,
                "varbinds": [_vb(SYS_UPTIME, "200", "ticks"), _vb(CARD_ID, card)],
                "truth": {
                    "situation_key": "chassis_card_fail",
                    "entity_key": card,
                    "is_root": k == 0,
                    "severity": "critical",
                },
            }
        )
    # 48 ports, 16 per card; each raises port-down then port-fault, repeated 3x (bounce).
    for _repeat in range(3):
        for cls, sev in ((CH_PORT_DOWN, "major"), (CH_PORT_FAULT, "minor")):
            for p in range(48):
                card = cards[p // 16]
                port = f"eth-{p}"
                t += 0.01
                events.append(
                    {
                        "delay": round(t, 3),
                        "source": chassis,
                        "trap_oid": cls,
                        "varbinds": [
                            _vb(SYS_UPTIME, "200", "ticks"),
                            _vb(CH_PORT_ID, port),
                            _vb(CARD_ID, card),
                            _vb(f"{CHASSIS}.276.1.1.4", sev),
                        ],
                        "truth": {
                            "situation_key": "chassis_card_fail",
                            "entity_key": port,
                            "severity": sev,
                        },
                    }
                )
    _write(
        "chassis_card_fail.json",
        events,
        "Chassis proxies 3 cards and 48 ports; card->port two-level containment.",
    )


AXIS = "1.3.6.1.4.1.368"  # Axis-style camera root (SNMPv1 archetype)
CAM_ID = f"{AXIS}.1.1.1"
CAM_ENTERPRISE = f"{AXIS}.4"  # SNMPv1 enterprise for the camera traps
# RFC 3584 maps an enterprise-specific v1 trap (generic 6) to snmpTrapOID.0 =
# <enterprise>.0.<specific>. Specific codes: 1 motion, 2 offline, 3 keepalive.
CAM_MOTION = f"{CAM_ENTERPRISE}.0.1"
CAM_OFFLINE = f"{CAM_ENTERPRISE}.0.2"
CAM_KEEPALIVE = f"{CAM_ENTERPRISE}.0.3"


def gen_camera_nvr() -> None:
    """A video NVR reporting 100 cameras over SNMPv1 (keepalive-heavy). v0.2.0 quarantines
    every v1 trap, so the whole NVR is invisible until the RFC 3584 mapping lands in
    v0.3.0. The camera id recurs across motion / offline / keepalive classes."""
    events: list[dict[str, Any]] = []
    nvr = "198.51.100.5"
    t = 0.0
    cams = [f"cam-{n}" for n in range(100)]
    # Keepalive-heavy: each camera keepalives twice, plus a motion and an offline event.
    plan = [
        (CAM_KEEPALIVE, 3, "warning"),
        (CAM_KEEPALIVE, 3, "warning"),
        (CAM_MOTION, 1, "minor"),
        (CAM_OFFLINE, 2, "major"),
    ]
    for cls, specific, sev in plan:
        for cam in cams:
            t += 0.01
            events.append(
                {
                    "delay": round(t, 3),
                    "source": nvr,
                    "trap_oid": cls,
                    "version": 1,
                    "enterprise": CAM_ENTERPRISE,
                    "specific": specific,
                    "agent_addr": nvr,
                    "varbinds": [_vb(CAM_ID, cam), _vb(f"{AXIS}.1.1.9", sev)],
                    # One NVR in one window is one situation for v0.3.0: separating keepalive
                    # noise from the offline incident is situation subsumption (v0.5.0, out of
                    # scope). The point here is v1 ingestion and per-camera entity learning.
                    "truth": {
                        "situation_key": "camera_nvr",
                        "entity_key": cam,
                        "severity": sev,
                    },
                }
            )
    _write(
        "camera_nvr.json",
        events,
        "NVR proxies 100 cameras over SNMPv1 (RFC 3584); v0.2.0 is blind to all of it.",
    )


def gen_dual_incident() -> None:
    """Two concurrent, unrelated incidents overlapping in the same window: a Ciena fibre
    cut on NEs A1/A2 and a Juniper power event on NEs B1/B2. They must not merge
    (over-merge guard); no shared devices or classes and no learned affinity between them."""
    events: list[dict[str, Any]] = []
    a1, a2, b1, b2 = "203.0.113.1", "203.0.113.2", "203.0.113.51", "203.0.113.52"
    ciena = "1.3.6.1.4.1.1271.2.1"
    juniper = "1.3.6.1.4.1.2636.4.5"
    t = 0.0
    for step in range(4):
        for dev in (a1, a2):
            t += 0.3
            events.append(
                {
                    "delay": round(t, 3),
                    "source": dev,
                    "trap_oid": f"{ciena}.{step + 1}",
                    "varbinds": [_vb(SYS_UPTIME, "10", "ticks"), _vb(f"{ciena}.9", "port-1/1")],
                    "truth": {
                        "situation_key": "incident_A",
                        "entity_key": dev,
                        "is_root": step == 0 and dev == a1,
                    },
                }
            )
        for dev in (b1, b2):
            t += 0.3
            events.append(
                {
                    "delay": round(t, 3),
                    "source": dev,
                    "trap_oid": f"{juniper}.{step + 1}",
                    "varbinds": [_vb(SYS_UPTIME, "10", "ticks"), _vb(f"{juniper}.9", "fpc-0")],
                    "truth": {
                        "situation_key": "incident_B",
                        "entity_key": dev,
                        "is_root": step == 0 and dev == b1,
                    },
                }
            )
    _write(
        "dual_incident.json",
        events,
        "Two unrelated incidents overlap in time on disjoint NEs; must stay separate.",
    )


def gen_dual_incident_same_vendor() -> None:
    """`dual_incident` with incident B moved onto incident A's vendor (v0.26.0, ADR #418).

    The original stopped merging in v0.18.0, when the entity term was withheld across enterprise
    subtrees (F76) — a vendor gate, not a grouping fix. Two concurrent incidents on ONE vendor, in
    different modules of it, still merged under connected components through one weak bridge. This
    is that case, and it is the one Part I.3 is measured on. Its own addresses, so a replay after
    `dual_incident` raises new alarms rather than repeating that scenario's."""
    events: list[dict[str, Any]] = []
    a1, a2, b1, b2 = "203.0.113.11", "203.0.113.12", "203.0.113.61", "203.0.113.62"
    optical = "1.3.6.1.4.1.1271.2.1"
    power = "1.3.6.1.4.1.1271.2.9"
    t = 0.0
    for step in range(4):
        for devs, root, key, port in (
            ((a1, a2), optical, "incident_A", "port-1/1"),
            ((b1, b2), power, "incident_B", "psu-0"),
        ):
            for dev in devs:
                t += 0.3
                events.append(
                    {
                        "delay": round(t, 3),
                        "source": dev,
                        "trap_oid": f"{root}.{step + 1}",
                        "varbinds": [_vb(SYS_UPTIME, "10", "ticks"), _vb(f"{root}.9", port)],
                        "truth": {
                            "situation_key": key,
                            "entity_key": dev,
                            "is_root": step == 0 and dev == devs[0],
                        },
                    }
                )
    _write(
        "dual_incident_same_vendor.json",
        events,
        "Two unrelated incidents on one vendor overlap in time on disjoint NEs; must stay apart.",
    )


# -- v0.29.0 (ADR #442): a field review's DWDM simulation, as the appliance receives it ----------

#: The script's simulation enterprise, under NET-SNMP's: not any vendor's.
SIM = "1.3.6.1.4.1.8072.9999"
SNMP_TRAP_ADDRESS = "1.3.6.1.6.3.18.1.3.0"
LINK_DOWN, LINK_UP, AUTH_FAILURE = (f"1.3.6.1.6.3.1.1.5.{n}" for n in (3, 4, 5))
#: (delay, trap OID, node, interface, peer, circuit, alarm, severity, description, root cause,
#: action, value) — the twenty `run_trap` calls of the script, with its `sleep`s summed into delays.
#: Nodes are "A"/"B"; the scenario maps them to its own addresses.
DWDM_SCRIPT: tuple[tuple[float, str, str, str, str, str, str, str, str, str, str, str], ...] = (
    (
        0,
        f"{SIM}.0.1",
        "A",
        "OTS-1-53",
        "B",
        "DWDM-CORE-01",
        "Ciena-6500-High-Received-Span-Loss",
        "MINOR",
        "Received span loss increased above baseline on the DWDM line interface",
        "Fiber attenuation increasing",
        "Monitor optical levels",
        "13.2 dB / target 9.5 dB",
    ),
    (
        3,
        f"{SIM}.0.1",
        "B",
        "OTS-3-54",
        "A",
        "DWDM-CORE-01",
        "Pre-FEC Signal Degrade",
        "MAJOR",
        "Pre-FEC BER crossed degradation threshold on coherent optical channel",
        "Optical degradation",
        "Monitor FEC counters",
        "BER 2.1e-4",
    ),
    (
        8,
        f"{SIM}.0.1",
        "A",
        "OCH-10107",
        "B",
        "DWDM-CORE-01",
        "Pre-FEC Signal Fail",
        "MAJOR",
        "Pre-FEC BER reached signal-fail threshold and became unstable",
        "Rapid optical degradation",
        "Prepare fiber investigation",
        "BER 1.8e-3",
    ),
    (
        10,
        f"{SIM}.0.2",
        "A",
        "OTS-1-53",
        "B",
        "DWDM-CORE-01",
        "Optical Line Fail",
        "CRITICAL",
        "Optical line failure detected; receive optical signal lost",
        "Suspected fiber cut",
        "Initiate protection and field investigation",
        "OPR -38 dBm",
    ),
    (
        18,
        f"{SIM}.0.2",
        "B",
        "OTS-3-54",
        "A",
        "DWDM-CORE-01",
        "Loss of Signal - Physical",
        "CRITICAL",
        "Physical optical signal lost on the remote line interface",
        "Suspected fiber cut",
        "Check optical path",
        "LOS",
    ),
    (
        19,
        LINK_DOWN,
        "A",
        "LIM-1-1",
        "B",
        "DWDM-CORE-01",
        "linkDown",
        "CRITICAL",
        "Physical transport interface transitioned to down state",
        "Loss of optical transport",
        "Raise service-impacting incident",
        "operStatus=down",
    ),
    (
        24,
        f"{SIM}.0.3",
        "A",
        "OTU-1-1",
        "B",
        "DWDM-CORE-01",
        "OTU Loss of Frame",
        "CRITICAL",
        "OTU framing lost after optical signal failure",
        "Optical line failure",
        "Correlate with OLF/LOS",
        "LOF",
    ),
    (
        25,
        f"{SIM}.0.4",
        "B",
        "ODU-3-1",
        "A",
        "DWDM-CORE-01",
        "ODU Alarm Indication Signal",
        "CRITICAL",
        "ODU-AIS propagated downstream after transport failure",
        "Upstream transport failure",
        "Correlate affected services",
        "AIS",
    ),
    (
        45,
        f"{SIM}.0.5",
        "A",
        "ETH10G-1",
        "B",
        "CIR-10G-4471",
        "Remote Fault",
        "MAJOR",
        "Ethernet service reported remote fault following DWDM transport loss",
        "Underlying optical outage",
        "Suppress as secondary alarm",
        "remoteFault=true",
    ),
    (
        65,
        LINK_DOWN,
        "B",
        "LIM-3-1",
        "A",
        "CIR-10G-4471",
        "linkDown",
        "MAJOR",
        "Customer-facing transport interface transitioned down",
        "Underlying optical outage",
        "Associate with root incident",
        "operStatus=down",
    ),
    (
        105,
        f"{SIM}.0.6",
        "A",
        "PROT-1",
        "B",
        "DWDM-CORE-01",
        "Protection Switch",
        "MAJOR",
        "Automatic protection switching initiated after working path failure",
        "Optical line failure",
        "Verify protection path",
        "PATH-1 -> PATH-2",
    ),
    (
        107,
        f"{SIM}.0.7",
        "A",
        "PROT-2",
        "B",
        "DWDM-CORE-01",
        "Protection Path Active",
        "WARNING",
        "Traffic successfully moved to protection path",
        "Working path unavailable",
        "Monitor protected service",
        "PATH-2 ACTIVE",
    ),
    (
        111,
        f"{SIM}.0.8",
        "A",
        "NOC",
        "B",
        "CIR-10G-4471",
        "Service Impact",
        "CRITICAL",
        "Customer circuit unavailable despite protection attempt",
        "Fiber break exceeds protection capability",
        "Escalate to fiber provider",
        "1 circuit affected",
    ),
    (
        114,
        f"{SIM}.0.9",
        "A",
        "NOC",
        "B",
        "DWDM-CORE-01",
        "Correlated Incident",
        "CRITICAL",
        "Multiple optical and transport alarms correlated into a single incident",
        "Suspected fiber cut",
        "Create root-cause incident; suppress secondary alarms",
        "10 alarms correlated",
    ),
    (
        164,
        f"{SIM}.0.10",
        "A",
        "OTS-1-53",
        "B",
        "DWDM-CORE-01",
        "Optical Signal Restored",
        "WARNING",
        "Optical receive power returned after field fiber repair",
        "Fiber repaired",
        "Validate optical margin",
        "OPR -12.4 dBm",
    ),
    (
        166,
        f"{SIM}.0.11",
        "B",
        "OTS-3-54",
        "A",
        "DWDM-CORE-01",
        "Pre-FEC Signal Fail Clear",
        "CLEAR",
        "Pre-FEC BER returned below signal-fail threshold",
        "Fiber path restored",
        "Continue monitoring",
        "BER 2.0e-7",
    ),
    (
        168,
        f"{SIM}.0.12",
        "B",
        "ODU-3-1",
        "A",
        "DWDM-CORE-01",
        "ODU AIS Clear",
        "CLEAR",
        "ODU alarm indication signal cleared after transport recovery",
        "Transport restored",
        "Verify downstream services",
        "AIS=false",
    ),
    (
        169,
        LINK_UP,
        "A",
        "LIM-1-1",
        "B",
        "DWDM-CORE-01",
        "linkUp",
        "CLEAR",
        "Physical transport interface returned to operational state",
        "Optical transport restored",
        "Continue monitoring",
        "operStatus=up",
    ),
    (
        171,
        LINK_UP,
        "B",
        "LIM-3-1",
        "A",
        "CIR-10G-4471",
        "linkUp",
        "CLEAR",
        "Customer-facing transport interface returned to operational state",
        "DWDM path restored",
        "Verify service stability",
        "operStatus=up",
    ),
    (
        176,
        f"{SIM}.0.13",
        "A",
        "NOC",
        "B",
        "DWDM-CORE-01",
        "Incident Clear",
        "CLEAR",
        "All correlated alarms cleared and service restored after fiber repair",
        "Fiber cut repaired",
        "Close incident after monitoring window",
        "20 events / service restored",
    ),
)


def _sim_trap(
    delay: float, oid: str, node: str, values: tuple[str, ...], truth: dict[str, Any]
) -> dict[str, Any]:
    """One `run_trap`: snmpTrapAddress, then the script's eleven simulation varbinds (strings)."""
    varbinds = [_vb(SYS_UPTIME, "0", "ticks"), _vb(SNMP_TRAP_ADDRESS, node, "ip")]
    varbinds += [_vb(f"{SIM}.1.{n}", value) for n, value in enumerate(values, start=1)]
    return {"delay": delay, "source": node, "trap_oid": oid, "varbinds": varbinds, "truth": truth}


def _dwdm(name: str, base: str, stretch: float, description: str) -> None:
    """The script's incident on nodes ``base``.1/.2, its timing multiplied by ``stretch``, among
    three isolated alerts the script did not send: a login failure on node A, a customer port on
    node B on another circuit (raised and cleared), and a CPU alarm on a third node. Each is its
    own situation; the incident's twenty traps are one."""
    node = {"A": f"{base}.1", "B": f"{base}.2", "C": f"{base}.3"}
    events = []
    for n, (t, oid, at, iface, peer, circuit, *text) in enumerate(DWDM_SCRIPT, start=1):
        events.append(
            _sim_trap(
                round(t * stretch, 3),
                oid,
                node[at],
                (str(n), text[0], text[1], node[at], iface, node[peer], circuit, *text[2:]),
                {
                    "situation_key": "dwdm_fibre_cut",
                    "entity_key": f"{node[at]}|{iface}",
                    "is_root": n == 4,
                },
            )
        )
    lone = (
        (60, AUTH_FAILURE, "A", "MGMT", "", "", "authenticationFailure", "MINOR", "isolated_login"),
        (90, f"{SIM}.0.20", "C", "CPU-0", "", "", "High CPU", "MINOR", "isolated_cpu"),
        (140, LINK_DOWN, "B", "LIM-7-1", "C", "CIR-1G-0093", "linkDown", "MAJOR", "isolated_port"),
        (150, LINK_UP, "B", "LIM-7-1", "C", "CIR-1G-0093", "linkUp", "CLEAR", "isolated_port"),
    )
    for t, oid, at, iface, peer, circuit, alarm, severity, key in lone:
        values = (str(len(events) + 1), alarm, severity, node[at], iface, node.get(peer, ""))
        events.append(
            _sim_trap(
                round(t * stretch, 3),
                oid,
                node[at],
                (*values, circuit, "", "", "", ""),
                {"situation_key": key, "entity_key": f"{node[at]}|{iface}", "is_root": True},
            )
        )
    _write(name, events, description)


def gen_dwdm_staged_fibre_cut() -> None:
    _dwdm(
        "dwdm_staged_fibre_cut.json",
        "198.51.100",
        1.0,
        "A field review's DWDM fibre cut: degradation, break, services, protection and repair over "
        "three minutes on two nodes, among three isolated alerts; one incident.",
    )


def gen_dwdm_staged_fibre_cut_slow() -> None:
    _dwdm(
        "dwdm_staged_fibre_cut_slow.json",
        "198.51.101",
        10.0,
        "The same DWDM fibre cut at ten times the pace: degradation over minutes, services and "
        "protection a quarter of an hour after the break, the repair at half an hour.",
    )


DECOY = "1.3.6.1.4.1.6486.1"  # Alcatel-Lucent Enterprise style root
DEC_PORT_ID = f"{DECOY}.5.1"  # the true discriminator
DEC_SERIAL = f"{DECOY}.9.1"  # alarm serial: unique per trap (decoy)
DEC_TSTAMP = f"{DECOY}.9.2"  # event timestamp: unique per trap (decoy)
DEC_CONST = f"{DECOY}.9.3"  # constant vendor tag (decoy)
DEC_A = f"{DECOY}.2.1"
DEC_B = f"{DECOY}.2.2"


def gen_decoy_varbinds() -> None:
    """One NE, 40 ports, each raising two alarm classes three times (repeats that should
    deduplicate). Every trap leads with a unique alarm serial and a unique timestamp, then
    the real port id, then a constant tag. v0.2.0's "first payload varbind" heuristic latches
    onto the serial, so nothing deduplicates; the profiler must reject serial, timestamp and
    constant and select the port id."""
    events: list[dict[str, Any]] = []
    ne = "233.252.0.9"
    serial = 0
    t = 0.0
    for _repeat in range(3):
        for cls, sev in ((DEC_A, "major"), (DEC_B, "minor")):
            for p in range(40):
                serial += 1
                t += 0.05
                port = f"port-{p}"
                events.append(
                    {
                        "delay": round(t, 3),
                        "source": ne,
                        "trap_oid": cls,
                        "varbinds": [
                            _vb(SYS_UPTIME, "1", "ticks"),
                            _vb(DEC_SERIAL, str(1_000_000 + serial), "int"),
                            _vb(DEC_TSTAMP, str(1_700_000_000 + serial), "int"),
                            _vb(DEC_PORT_ID, port),
                            _vb(DEC_CONST, "alu-northbound"),
                        ],
                        "truth": {
                            "situation_key": "decoy_incident",
                            "entity_key": port,
                            "severity": sev,
                        },
                    }
                )
    _write(
        "decoy_varbinds.json",
        events,
        "Timestamp, sequence and constant decoys beside the true port-id discriminator.",
    )


def main() -> None:
    relabel_fiber_cut()
    relabel_olt_storm()
    relabel_background_noise()
    relabel_flapping_noise()
    gen_pon_dying_gasp()
    gen_pon_pon_port_down()
    gen_chassis_card_fail()
    gen_camera_nvr()
    gen_dual_incident()
    gen_dual_incident_same_vendor()
    gen_decoy_varbinds()
    gen_dwdm_staged_fibre_cut()
    gen_dwdm_staged_fibre_cut_slow()
    print(f"wrote {len(list(CORPUS.glob('*.json')))} corpus files to {CORPUS}")  # noqa: T201


if __name__ == "__main__":
    main()
