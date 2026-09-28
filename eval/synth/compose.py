"""Composing incidents into a stream: overlap, recurrence, noise, and the path to the appliance.

A **stream** is what one fresh appliance would receive from one estate over hours or weeks. It is
the unit of everything downstream: the replay starts every stream from an empty appliance, a split
never cuts a stream in two (so no incident's pairs can land on both sides of it), and the first
hour of a stream is literally the first hour of a fresh deployment.

What this module adds on top of the families, each drawn per stream:

* **Onsets** by a Poisson process — independent faults do not queue politely.
* **Concurrency**: a fraction of incidents is started deliberately close to another one, at one
  of three distances — inside two seconds, inside thirty, inside five minutes — on shared or
  disjoint elements. This is the `dual_incident` failure at many degrees of overlap.
* **Recurrence**: a fraction of faults returns hours to weeks later **with the same draws** —
  the same elements, the same traps — as a *new* incident, which is the memory case. The truth
  never merges a recurrence into its predecessor; remembering it is the correlator's job.
* **Background noise** at its own rate: every noise event is its own incident.
* **The path**: per-element management latency (most fast, a few slow), per-trap jitter, loss,
  duplication, and NMS-side ingest gaps whose backlog arrives late and out of order.

Deterministic: every draw comes from a `random.Random` seeded by a SHA-256 of the stream seed and
a salt, so a stream is a pure function of its spec, independent of what else was generated.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from typing import Any

from synth.emit import Event
from synth.estate import Estate, build_estate
from synth.families import FAMILIES, NOISE, Builder, Spec, hosts

__all__ = ["Stream", "StreamSpec", "compose", "derived_rng"]

#: A real epoch at local midnight, so a stream's clock has a time of day. Nothing in the
#: appliance reads it except through features a release may choose to try.
EPOCH = 1_767_225_600.0  # 2026-01-01T00:00:00Z


def derived_rng(*parts: object) -> random.Random:
    """A generator seeded from a SHA-256 of ``parts`` — independent of any other draw."""
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


@dataclass(frozen=True)
class StreamSpec:
    """Everything that decides a stream. Two equal specs produce byte-identical streams."""

    name: str
    seed: int
    families: tuple[str, ...]
    hours: float
    incidents_per_hour: float
    noise_per_hour: float
    concurrency: float = 0.25
    recurrence: float = 0.1
    #: Weights by family; absent means 1.0. Lets a held-out stream favour its own families while
    #: still carrying ordinary traffic around them.
    weights: tuple[tuple[str, float], ...] = ()


@dataclass
class Stream:
    spec: StreamSpec
    events: list[Event]
    #: incident key -> family, for every incident including noise.
    incidents: dict[str, str] = field(default_factory=dict)
    #: incident key -> the key of the incident it recurs (absent when it is a first occurrence).
    recurs: dict[str, str] = field(default_factory=dict)
    #: incident key -> the key of the incident it was deliberately placed close to, and how close.
    concurrent: dict[str, tuple[str, str]] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return {
            "name": self.spec.name,
            "description": f"generated stream {self.spec.name} (seed {self.spec.seed})",
            "events": [e.as_json() for e in self.events],
        }


def _draw_family(rng: random.Random, spec: StreamSpec, estate: Estate) -> str | None:
    weights = dict(spec.weights)
    names = [n for n in spec.families if hosts(estate, FAMILIES[n])]
    if not names:
        return None
    return rng.choices(names, weights=[weights.get(n, 1.0) for n in names])[0]


def _run(spec: Spec, rng_seed: tuple[object, ...], estate: Estate, key: str, fam: str) -> list[Event]:
    builder = Builder(derived_rng(*rng_seed), estate, key, fam)
    spec.function(builder)
    return builder.events


def compose(spec: StreamSpec) -> Stream:
    """Draw one stream from its spec."""
    rng = derived_rng("stream", spec.name, spec.seed)
    estate = build_estate(derived_rng("estate", spec.name, spec.seed))
    horizon = spec.hours * 3600.0
    stream = Stream(spec, [])
    placed: list[tuple[float, str]] = []

    def place(key: str, family: str, fam_spec: Spec, seed_key: tuple[object, ...], t0: float) -> None:
        events = _run(fam_spec, seed_key, estate, key, family)
        if not events:
            return
        start = min(e.t for e in events)
        for e in events:
            e.t = t0 + (e.t - start)
        stream.events.extend(events)
        stream.incidents[key] = family
        placed.append((t0, key))

    t = 0.0
    n = 0
    while True:
        t += rng.expovariate(spec.incidents_per_hour / 3600.0)
        if t >= horizon:
            break
        family = _draw_family(rng, spec, estate)
        if family is None:
            break
        key = f"{spec.name}/i{n}"
        onset = t
        if family == "planned_maintenance":
            day = int(onset // 86400)
            onset = day * 86400 + rng.uniform(1.0, 5.0) * 3600.0  # at night, local time
        if placed and rng.random() < spec.concurrency:
            anchor_t, anchor = rng.choice(placed[-6:])
            closeness = rng.choice(("2s", "30s", "300s"))
            onset = anchor_t + rng.uniform(0.0, {"2s": 2.0, "30s": 30.0, "300s": 300.0}[closeness])
            stream.concurrent[key] = (anchor, closeness)
        seed_key: tuple[object, ...] = ("incident", spec.name, spec.seed, n)
        place(key, family, FAMILIES[family], seed_key, onset)
        if rng.random() < spec.recurrence:
            later = onset
            for r in range(rng.randint(1, 3)):
                later += rng.uniform(2.0, 240.0) * 3600.0
                if later >= horizon:
                    break
                rkey = f"{key}r{r + 1}"
                place(rkey, family, FAMILIES[family], seed_key, later)  # the SAME draws
                stream.recurs[rkey] = key
        n += 1

    t = 0.0
    m = 0
    while spec.noise_per_hour > 0:
        t += rng.expovariate(spec.noise_per_hour / 3600.0)
        if t >= horizon:
            break
        place(f"{spec.name}/n{m}", "noise", NOISE, ("noise", spec.name, spec.seed, m), t)
        m += 1

    stream.events = _deliver(rng, estate, stream.events, horizon)
    return stream


def _deliver(rng: random.Random, estate: Estate, events: list[Event], horizon: float) -> list[Event]:
    """The management network between the elements and the appliance, applied to every trap."""
    latency: dict[str, float] = {}
    for ip in sorted(estate.elements):
        roll = rng.random()
        latency[ip] = (
            rng.uniform(0.0, 0.3) if roll < 0.7 else rng.uniform(0.3, 3.0) if roll < 0.95 else
            rng.uniform(3.0, 15.0)
        )
    loss = rng.uniform(0.0, 0.04)
    dup = rng.uniform(0.0, 0.03)
    jitter = rng.uniform(0.01, 0.4)
    gaps: list[tuple[float, float, bool]] = []
    for _ in range(rng.randint(0, max(0, int(horizon // 43200)))):
        start = rng.uniform(0.0, horizon)
        gaps.append((start, start + rng.uniform(30.0, 900.0), rng.random() < 0.5))
    out: list[Event] = []
    for e in sorted(events, key=lambda ev: (ev.t, ev.source, ev.trap_oid, ev.entity)):
        if e.t < 0.0 or e.t >= horizon:
            continue
        if rng.random() < loss:
            continue
        arrival = e.t + latency[e.source] + rng.expovariate(1.0 / jitter)
        dropped = False
        for g0, g1, drop in gaps:
            if g0 <= arrival < g1:
                if drop:
                    dropped = True
                else:
                    arrival = g1 + rng.uniform(0.0, 60.0)  # the backlog arrives late, reordered
        if dropped:
            continue
        e.arrival = arrival
        out.append(e)
        if rng.random() < dup:
            twin = Event(**{**e.__dict__, "arrival": arrival + rng.uniform(0.01, 2.0)})
            out.append(twin)
    out.sort(key=lambda ev: (ev.arrival, ev.source, ev.trap_oid, ev.entity))
    return out
