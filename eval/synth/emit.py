"""One generated trap, and how a fault role on an element becomes its varbinds.

Events are emitted in the **corpus's own JSON shape** (`eval/corpus/*.json`: `delay`, `source`,
`trap_oid`, `varbinds`, `truth`) so that any generated stream can be replayed through the real
ingest path by `eval/harness.py` unchanged. That is what the skew test uses: the same stream through
the appliance and through the fast offline replay must produce the same pairs and the same features.

The truth carries more than the corpus did — the incident's family and whether the trap is a clear
— because the evaluation needs to hold whole families out and to know which traps a correct
correlator should never group at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from synth.catalogue import (
    ALARM_SEVERITY_OID,
    BGP_PEER_REMOTE_ADDR,
    IF_DESCR,
    IF_INDEX,
    OSPF_NBR_IP,
    OSPF_NBR_STATE,
    UPS_ALARM_DESCR,
    Rendering,
)
from synth.estate import Element

__all__ = ["Event", "severity_of", "trap"]

LINK_DOWN = "1.3.6.1.6.3.1.1.5.3"
LINK_UP = "1.3.6.1.6.3.1.1.5.4"
_OSPF_STD = "1.3.6.1.2.1.14.16.2.2"
UPS_ALARM_ID = "1.3.6.1.2.1.33.1.6.2.1.1"

#: The perceived severity each role raises with (X.733 words). A default, as a vendor's own
#: grading would be; the varbind carries it only where the vendor stamps severities at all.
_ROLE_SEVERITY = {
    "link_down": "major",
    "signal_degrade": "minor",
    "rx_power_low": "minor",
    "optical_los": "critical",
    "amp_output_low": "major",
    "amp_abnormal": "major",
    "loss_of_frame": "critical",
    "protection_switch": "warning",
    "board_fail": "critical",
    "board_removed": "major",
    "psu_fail": "major",
    "mains_fail": "critical",
    "on_battery": "major",
    "fan_fail": "major",
    "temp_high": "minor",
    "dying_gasp": "critical",
    "cold_start": "warning",
    "warm_start": "warning",
    "config_change": "warning",
    "auth_failure": "warning",
    "cpu_high": "minor",
    "bgp_down": "major",
    "ospf_nbr": "major",
    "pon_los": "critical",
    "onu_los": "major",
    "onu_dying_gasp": "major",
}


def severity_of(role: str) -> str:
    return _ROLE_SEVERITY[role]


@dataclass
class Event:
    """One trap as the element emitted it. ``t`` is the causal time; arrival is set later."""

    t: float
    source: str
    trap_oid: str
    varbinds: tuple[tuple[str, str, str], ...]  # (oid, kind, value)
    incident: str
    family: str
    entity: str
    is_root: bool
    is_clear: bool
    arrival: float = 0.0

    def as_json(self) -> dict[str, Any]:
        return {
            "delay": round(self.arrival, 6),
            "source": self.source,
            "trap_oid": self.trap_oid,
            "varbinds": [{"oid": o, "kind": k, "value": v} for o, k, v in self.varbinds],
            "truth": {
                "situation_key": self.incident,
                "entity_key": self.entity,
                "is_root": self.is_root,
                "family": self.family,
                "clear": self.is_clear,
            },
        }


def trap(
    rendering: Rendering,
    element: Element,
    entity: str,
    *,
    t: float,
    incident: str,
    family: str,
    root: bool = False,
    clear: bool = False,
    peer: str = "",
) -> Event | None:
    """Render ``rendering`` on ``element`` about ``entity``, as a raise or its clear.

    Returns ``None`` for the clear of a notification that has none — the fault simply stops
    being reported, which is what a dying gasp or a cold start does.
    """
    note = rendering.notification
    if clear and note.clear is None:
        return None
    oid = note.clear if clear and note.clear is not None else note.oid
    state_style = note.clear == note.oid
    word = "cleared" if clear else severity_of(rendering.role)
    varbinds: list[tuple[str, str, str]]
    if oid in (LINK_DOWN, LINK_UP):
        index = element.port(entity)
        varbinds = [
            (f"{IF_INDEX}.{index}", "int", str(index)),
            (f"{IF_DESCR}.{index}", "str", entity),
        ]
    elif rendering.role == "bgp_down":
        varbinds = [(f"{BGP_PEER_REMOTE_ADDR}.{peer}", "ip", peer or entity)]
    elif rendering.role == "ospf_nbr":
        state = "8" if clear else "1"
        varbinds = [(f"{OSPF_NBR_IP}.{peer}", "ip", peer or entity), (OSPF_NBR_STATE, "int", state)]
        if oid != _OSPF_STD:
            varbinds[1] = (f"{rendering.vendor.object_arc}.9", "int", state)
    elif rendering.role in ("mains_fail", "on_battery") and oid.startswith("1.3.6.1.2.1.33"):
        varbinds = [
            (UPS_ALARM_ID, "int", str(hash_index(entity) % 1000)),
            (UPS_ALARM_DESCR, "str", "upsAlarmInputBad"),
        ]
    else:
        varbinds = [(f"{rendering.vendor.object_arc}.{hash_index(entity)}", "str", entity)]
    if state_style or (rendering.vendor.carries_severity and oid not in (LINK_DOWN, LINK_UP)):
        varbinds.append((ALARM_SEVERITY_OID, "str", word))
    if element.decoys:  # a sequence number and an event time, unique per trap (ADR #426)
        element.serial += 1
        varbinds.append((f"{rendering.vendor.object_arc}.0.1", "int", str(element.serial)))
        varbinds.append(
            (f"{rendering.vendor.object_arc}.0.2", "int", str(1_700_000_000 + 7 * element.serial))
        )
    return Event(
        t=t,
        source=element.ip,
        trap_oid=oid,
        varbinds=tuple(varbinds),
        incident=incident,
        family=family,
        entity=f"{element.ip}|{entity}",
        is_root=root,
        is_clear=clear,
    )


def hash_index(text: str) -> int:
    """A small, stable table index for an entity name (not Python's salted ``hash``)."""
    value = 0
    for ch in text:
        value = (value * 131 + ord(ch)) % 1_000_003
    return value
