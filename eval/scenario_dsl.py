"""Declarative, deterministic trap-scenario DSL (C.1).

A **test/tooling asset only** — it lives under ``eval/`` and is never imported by ``netcorenoc/``,
and it adds no runtime dependency. It standardises how fault/abuse scenarios are authored so the
coverage can grow without ad-hoc per-scenario code: a scenario describes devices, the trap classes
they emit (real-shaped vendor/standard OIDs), varbind templates (an entity discriminator, a
severity field, deliberate decoys), timing, and ground-truth labels (``situation_key``,
``entity_key``, ``is_root``, ``severity``). It expands to the exact event format the harness and
``tools/trap_sim.py`` consume.

**Realistic vendor OIDs live here and in the corpus, never in the runtime** — the engine still
learns them blind. Determinism is mandatory: ``build()`` is a pure function of the spec plus a
fixed seed and an injected base time, so the output is byte-reproducible.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

SYS_UPTIME_OID = "1.3.6.1.2.1.1.3.0"

# A few real-shaped enterprise roots used by the authored scenarios (IANA PENs; test data only).
HUAWEI = "1.3.6.1.4.1.2011"
CISCO = "1.3.6.1.4.1.9"
DATACOM = "1.3.6.1.4.1.3709"
# Standard SNMPv2 authenticationFailure (RFC 3418) — a security event reported over SNMP.
AUTH_FAILURE_OID = "1.3.6.1.6.3.1.1.5.5"


@dataclass(frozen=True)
class Varbind:
    """One varbind template; ``value`` is literal (the DSL is deterministic, no late binding)."""

    oid: str
    value: str
    kind: str = "str"


@dataclass(frozen=True)
class Emission:
    """One labelled trap emission: device, class/varbinds, timing, and its ground-truth labels."""

    device: str
    trap_oid: str
    entity_key: str
    situation_key: str
    varbinds: tuple[Varbind, ...] = ()
    severity: str | None = None
    is_root: bool = False
    delay: float = 0.0
    uptime: int = 1000


@dataclass
class Scenario:
    """A named, self-describing scenario that expands to corpus events deterministically."""

    name: str
    description: str
    emissions: list[Emission] = field(default_factory=list)

    def build(self) -> dict[str, Any]:
        """Expand to the corpus document: {name, description, events:[...]} — byte-reproducible."""
        events: list[dict[str, Any]] = []
        for e in sorted(self.emissions, key=lambda x: (x.delay, x.device, x.trap_oid)):
            varbinds = [{"oid": SYS_UPTIME_OID, "kind": "ticks", "value": str(e.uptime)}]
            varbinds += [{"oid": v.oid, "kind": v.kind, "value": v.value} for v in e.varbinds]
            truth: dict[str, Any] = {"situation_key": e.situation_key, "entity_key": e.entity_key}
            if e.is_root:
                truth["is_root"] = True
            if e.severity is not None:
                truth["severity"] = e.severity
            events.append(
                {
                    "delay": round(e.delay, 3),
                    "source": e.device,
                    "trap_oid": e.trap_oid,
                    "varbinds": varbinds,
                    "truth": truth,
                }
            )
        return {"name": self.name, "description": self.description, "events": events}


# -- authored scenarios (deterministic builders) -------------------------------------


def login_failure_burst(
    device: str = "10.20.0.1", count: int = 12, spacing: float = 0.4
) -> Scenario:
    """C.3: a coordinated login-attempt burst reported over SNMP as standard authenticationFailure
    traps from one device, each a distinct attempt (instance). The engine must group them into one
    situation (same device, tight temporal window) — ordinary correlation, not an app-security
    test. The first attempt is the root."""
    emissions = [
        Emission(
            device=device,
            trap_oid=AUTH_FAILURE_OID,
            entity_key=device,
            situation_key="security_login_burst",
            varbinds=(Varbind(f"{CISCO}.9.9.9.1.1.1", f"attempt-{i}"),),  # attempt id (decoy-ish)
            severity="warning",
            is_root=(i == 0),
            delay=round(i * spacing, 3),
        )
        for i in range(count)
    ]
    return Scenario(
        name="security_login_burst",
        description=(
            f"{count} SNMP authenticationFailure traps from {device} in a tight window — a "
            "coordinated login-attempt burst that must correlate into one situation."
        ),
        emissions=emissions,
    )


def chassis_card_containment(device: str = "10.30.0.1", ports: int = 6) -> Scenario:
    """C.2: a line-card failure on a chassis takes its ports with it (containment). The card is the
    root; the ports are contained. Same NE, so the burst forms one situation; the discriminator
    varbind names card vs port."""
    disc = f"{HUAWEI}.5.25.31.1.1.1"  # entPhysicalIndex-shaped discriminator
    emissions = [
        Emission(
            device=device,
            trap_oid=f"{HUAWEI}.5.25.31.1.5.1",
            entity_key="card-3",
            situation_key="chassis_card_fail",
            varbinds=(Varbind(disc, "card-3"),),
            severity="critical",
            is_root=True,
            delay=0.0,
        )
    ]
    emissions += [
        Emission(
            device=device,
            trap_oid=f"{HUAWEI}.5.25.31.1.5.2",
            entity_key=f"card-3-port-{p}",
            situation_key="chassis_card_fail",
            varbinds=(Varbind(disc, f"card-3-port-{p}"),),
            severity="major",
            delay=round(0.1 + p * 0.05, 3),
        )
        for p in range(ports)
    ]
    return Scenario(
        name="chassis_card_fail",
        description=f"Line-card failure on {device} taking {ports} ports (containment hierarchy).",
        emissions=emissions,
    )


def bgp_adjacency_flap(device_a: str = "10.40.0.1", device_b: str = "10.40.0.2") -> Scenario:
    """C.2: an OSPF/BGP adjacency drop reported from both ends of the same link. Two NEs report the
    same event; co-occurrence should group them into one situation."""
    oid = f"{CISCO}.9.187.0.1"  # bgp/ospf-shaped
    return Scenario(
        name="bgp_adjacency_flap",
        description=f"BGP adjacency down reported from both {device_a} and {device_b}.",
        emissions=[
            Emission(device_a, oid, device_a, "bgp_adjacency", severity="major", is_root=True),
            Emission(device_b, oid, device_b, "bgp_adjacency", severity="major", delay=0.2),
        ],
    )


# Registry for tools/trap_sim.py (each builder is callable with no args — all params default).
SCENARIOS: dict[str, Callable[[], Scenario]] = {
    "login_burst": login_failure_burst,
    "chassis_card": chassis_card_containment,
    "bgp_flap": bgp_adjacency_flap,
}
