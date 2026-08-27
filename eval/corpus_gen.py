"""Deterministic generator for the labelled evaluation corpus.

Run ``python eval/corpus_gen.py`` to (re)write ``eval/corpus/*.json``. Four scenarios are
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

CORPUS = Path(__file__).parent / "corpus"
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
    gen_decoy_varbinds()
    print(f"wrote {len(list(CORPUS.glob('*.json')))} corpus files to {CORPUS}")  # noqa: T201


if __name__ == "__main__":
    main()
