"""The testbed scenario format: hosts, phases, and **no ground truth** (v0.17.0, DECISIONS #329).

**What this format is, and the one thing it deliberately cannot express.**

`eval/corpus/*.json` is a *labelled* corpus: every event carries a `truth` block naming the
situation it belongs to, the entity it is about and whether it is the root. That is what makes it a
gate — the harness aligns predictions to those labels and scores them.

A testbed scenario carries **none of that, and cannot**. `load()` refuses a file containing a
`truth` key anywhere, at any depth. The reason is `docs/analysis/PREREGISTRATION-0.10.0.md` §6: a
generator's own idea of what it generated may never become a label, a training row or promotion
evidence. v0.17.0 makes pointing a generator at a running appliance a one-line command, so the
boundary stops being a property of nobody having tried and becomes a property of the format.

**Generated traffic is traffic. Only a human gesture is evidence.** An operator looking at the
console may confirm, deny, merge or split what the appliance inferred from this traffic, and *that*
is a label — because a person made it. The scenario that produced the traps has no standing to say
whether the appliance was right, and this loader is where that is enforced rather than assumed.

The shape:

    hosts   — the simulated NEs. Each has an `address` (what the receiver will see as the source),
              an `enterprise` (a real IANA PEN) and, for a PON OLT, the `onus` behind it. ONUs have
              no address of their own, exactly as in a real GPON network: the OLT reports for them
              and the ONU identity travels in a varbind, which is the entity inference the appliance
              has to do for itself.

    phases  — named groups of events rather than one flat timeline, so a phase can be *triggered*
              (`control.py cut`) instead of only replayed. `steady` repeats on `repeat_every_s`;
              `cut` and `repair` run once when asked.

    events  — `at_s` is the offset from the moment the phase starts. `per_onu` fans the event out
              over the host's ONUs, spread across `spread_s`, with `{onu}` substituted in varbind
              values — which is what makes a storm a storm rather than one trap. `clears` names the
              trap OID this event clears and is documentation for the reader: the appliance infers
              clearing from the stream and is never told.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
SCENARIO_DIR = HERE.parent / "scenarios"

#: Keys a testbed scenario may not contain, at any depth, with the reason.
#:
#: `truth` is the labelled corpus's own key, so a corpus scenario copied into `testbed/scenarios/`
#: is refused rather than quietly becoming a source of labels. The others are the shapes the same
#: mistake takes when someone renames it.
FORBIDDEN_KEYS: dict[str, str] = {
    "truth": "the labelled corpus's ground-truth block",
    "label": "a label is an operator's gesture, never a generator's claim",
    "labels": "a label is an operator's gesture, never a generator's claim",
    "situation_key": "the corpus's situation label",
    "entity_key": "the corpus's entity label",
    "is_root": "the corpus's root label",
    "expected": "an expectation is a claim about the appliance's answer",
}


class TruthInScenarioError(ValueError):
    """A testbed scenario tried to carry ground truth. It may not, ever."""


@dataclass(frozen=True)
class Host:
    """One simulated network element."""

    id: str
    address: str
    role: str
    enterprise: int
    onus: tuple[str, ...]


@dataclass(frozen=True)
class Event:
    """One trap to send, already resolved to an absolute offset within its phase."""

    at_s: float
    host: str
    trap_oid: str
    varbinds: tuple[dict[str, str], ...]
    role: str


@dataclass(frozen=True)
class Phase:
    """A named group of events; `repeat_every_s` is set for a phase that loops."""

    name: str
    description: str
    events: tuple[Event, ...]
    repeat_every_s: float | None


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    hosts: tuple[Host, ...]
    phases: dict[str, Phase]

    def host(self, host_id: str) -> Host:
        for host in self.hosts:
            if host.id == host_id:
                return host
        raise KeyError(f"no host {host_id!r} in scenario {self.name!r}")

    @property
    def addresses(self) -> tuple[str, ...]:
        return tuple(host.address for host in self.hosts)


def _reject_truth(node: Any, path: str = "") -> None:
    """Walk the whole document and refuse any forbidden key, naming where it was found.

    Recursive over both dicts and lists, because the corpus keeps `truth` **inside each event** —
    a top-level check would pass a file whose every event is labelled, which is the entire failure
    this guard exists to prevent.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            where = f"{path}.{key}" if path else key
            if key in FORBIDDEN_KEYS:
                raise TruthInScenarioError(
                    f"testbed scenario carries {where!r} — {FORBIDDEN_KEYS[key]}.\n\n"
                    "A testbed scenario is TRAFFIC. It may not carry ground truth, because "
                    "generated truth may never become a label, a training row or promotion "
                    "evidence (PREREGISTRATION-0.10.0.md §6). If you want a labelled scenario, "
                    "it belongs in eval/corpus/ and is replayed by the harness, not by the lab."
                )
            _reject_truth(value, where)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _reject_truth(value, f"{path}[{index}]")


def _events(raw_events: list[dict[str, Any]], hosts: dict[str, Host]) -> tuple[Event, ...]:
    """Resolve one phase's raw events, fanning `per_onu` events out over the host's ONUs."""
    out: list[Event] = []
    for raw in raw_events:
        host_id = str(raw["host"])
        host = hosts[host_id]
        base = float(raw.get("at_s", 0.0))
        role = str(raw.get("role", ""))
        oid = str(raw["trap_oid"])
        varbinds = [dict(vb) for vb in raw.get("varbinds", [])]
        if not raw.get("per_onu"):
            out.append(Event(base, host_id, oid, tuple(varbinds), role))
            continue
        spread = float(raw.get("spread_s", 0.0))
        count = len(host.onus)
        for index, onu in enumerate(host.onus):
            # Evenly spread rather than random: a storm whose shape changes between runs is a
            # scenario nobody can reproduce, and `test_testbed.py` asserts two loads are identical.
            offset = base + (spread * index / max(count - 1, 1) if count > 1 else 0.0)
            resolved = tuple(
                {**vb, "value": str(vb.get("value", "")).replace("{onu}", onu)} for vb in varbinds
            )
            out.append(Event(round(offset, 4), host_id, oid, resolved, f"{role} ({onu})"))
    return tuple(sorted(out, key=lambda e: (e.at_s, e.host, e.trap_oid)))


def load(path: Path | str) -> Scenario:
    """Read and validate a testbed scenario. Raises `TruthInScenarioError` if it carries truth."""
    path = Path(path)
    if not path.is_file():
        candidate = SCENARIO_DIR / f"{path}.json" if path.suffix != ".json" else SCENARIO_DIR / path
        if not candidate.is_file():
            raise FileNotFoundError(f"no testbed scenario at {path} or {candidate}")
        path = candidate
    raw = json.loads(path.read_text(encoding="utf-8"))
    _reject_truth(raw)

    hosts = {
        str(h["id"]): Host(
            id=str(h["id"]),
            address=str(h["address"]),
            role=str(h.get("role", "")),
            enterprise=int(h["enterprise"]),
            onus=tuple(str(o) for o in h.get("onus", [])),
        )
        for h in raw["hosts"]
    }
    if len({host.address for host in hosts.values()}) != len(hosts):
        raise ValueError(
            "two hosts share an address. The whole point of the lab is that the receiver sees "
            "distinct sources, and it cannot if they are the same address."
        )
    phases = {
        name: Phase(
            name=name,
            description=str(body.get("description", "")),
            events=_events(list(body.get("events", [])), hosts),
            repeat_every_s=(
                float(body["repeat_every_s"]) if body.get("repeat_every_s") is not None else None
            ),
        )
        for name, body in raw["phases"].items()
    }
    return Scenario(
        name=str(raw["name"]),
        description=str(raw.get("description", "")),
        hosts=tuple(hosts.values()),
        phases=phases,
    )


def available() -> list[str]:
    """Every scenario the testbed ships, derived by listing the directory."""
    return sorted(p.stem for p in SCENARIO_DIR.glob("*.json"))
