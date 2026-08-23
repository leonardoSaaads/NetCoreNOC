"""The simulated network of `PREREGISTRATION-0.14.0.md` §5.1 — **shape fixed before any verdict.**

> The generator produces, in fixed proportion:
> independent faults overlapping within `TAU_S` on low-affinity NEs 30 %; a single fault spread
> beyond `TAU_S` on one NE 20 %; a mass storm concealing a simultaneous unrelated fault 15 %; a
> flapping port during a real incident 15 %; a situation merge chain of length >= 2 10 %; quiet
> background noise producing no situation 10 %.
>
> Devices, classes, entity keys, timing and decoy varbinds are drawn by the existing DSL from a
> **fixed seed recorded in the gate**. Two generations from the same seed are byte-identical.

## Why this lives outside `eval/corpus/`

Gate 0 premise 5 measured it: `eval/harness.py` reads `sorted(CORPUS_DIR.glob("*.json"))`, so a file
added to `eval/corpus/` moves the frozen `eval` hash — witnessed, 10 scenarios becoming 11 and the
hash moving from `c2e8a0ce…` to `43672b90…`. This package is a **sibling** of that directory and
nothing here is ever written into it.

## Why the incidents are separated by NE and not by time

An earlier design spaced incidents 900 s apart so their correlation windows could not overlap. It
cannot be driven over real UDP: the receiver timestamps a trap **on arrival**, so replaying an
eight-hour fixture in eight minutes collapses every incident into one 120 s window and the whole
network becomes one situation. The clock is the appliance's and this generator does not get to
inject it.

So the incidents are **concurrent and separated by network element**, which is also what a real NOC
looks like at 3 a.m. It works because of what the correlator actually computes: on an appliance that
has learned nothing, a cross-NE pair scores `0.30*decay + 0.35*0 + 0.35*0 <= 0.30`, below the 0.50
threshold, while a same-NE pair gets the structural `entity_affinity = 1.0` and scores at least
`0.35 + 0.30*decay`. Same NE links, different NEs do not — until the learner has seen enough
co-occurrence to move `A` and `E`, **which is exactly the drift the near-threshold shapes exist to
produce**.

## The proportions are exact, not sampled

Twenty incidents per increment and a quota of 6/4/3/3/2/2 — which is 30/20/15/15/10/10 % of twenty
with no remainder. A sampled proportion would be right on average and wrong in every increment, and
§5.4 forbids changing the shape afterwards, so "on average" would be a shape nobody could reproduce.

## No RNG

Every draw is `draw(seed, incident, salt, bound)`, a SplitMix64 finaliser — a pure function, not a
stream, so nothing depends on how many draws came before it. `forest.py` makes the same choice for
the same reason, and `shadow_cv._Lcg` records what the alternative cost this project once.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scenario_dsl import CISCO, DATACOM, HUAWEI, Emission, Scenario, Varbind

__all__ = [
    "INCREMENT_INCIDENTS",
    "SEED",
    "SHAPES",
    "SHAPE_QUOTA",
    "devices_of",
    "generate",
    "shape_of",
]

# **The registered seed.** Recorded in `docs/gates/v0.14.0-phase-6.md` and not varied afterwards:
# §5.4 forbids re-generating with a different seed after a verdict is seen, and a seed that lived
# only in a shell history would make that unenforceable.
SEED = 20_140_000

INCREMENT_INCIDENTS = 20

# The plan's §5.1 table, as an exact quota over one increment. The percentages are 30/20/15/15/10/10
# and twenty incidents divide by all of them.
SHAPE_QUOTA: tuple[tuple[str, int], ...] = (
    ("independent_overlap", 6),
    ("spread_beyond_tau", 4),
    ("storm_concealing", 3),
    ("flap_during_incident", 3),
    ("merge_chain", 2),
    ("quiet_noise", 2),
)
SHAPES: tuple[str, ...] = tuple(name for name, _quota in SHAPE_QUOTA)
_PATTERN: tuple[str, ...] = tuple(name for name, quota in SHAPE_QUOTA for _ in range(quota))
assert len(_PATTERN) == INCREMENT_INCIDENTS

# Trap classes, real-shaped and vendor-plausible. **They live here and never in the runtime** — the
# engine still learns them blind, which is the DSL's own rule one directory up.
PON_DYING_GASP = f"{HUAWEI}.5.25.31.1.5.10"
PON_LOS = f"{HUAWEI}.5.25.31.1.5.11"
CARD_FAIL = f"{HUAWEI}.5.25.31.1.5.1"
FIBRE_LOF = f"{DATACOM}.3.6.11.2"
PORT_DOWN = f"{CISCO}.9.276.1.1.1"
LINK_FLAP = f"{CISCO}.9.41.1.2.3"
POWER_ALARM = f"{CISCO}.9.13.1.5.1"

# Two class PAIRS reserved for the merge-chain shape and used nowhere else. They always fire
# together, one on each side of a ring span, so `learn.npmi` between the members of a pair rises
# quickly while their marginal rates stay low — which is what lets a **cross-NE** pair reach the
# 0.50 threshold on class affinity alone, with `entity_affinity` still at zero. Without them a merge
# chain cannot be built at all on an appliance that has learned nothing: `0.30*decay + 0.35*0 +
# 0.35*0` never reaches 0.50, so nothing ever bridges two NEs.
RING_EAST = f"{DATACOM}.3.6.11.21"
RING_WEST = f"{DATACOM}.3.6.11.22"
RING_NORTH = f"{DATACOM}.3.6.11.23"
RING_SOUTH = f"{DATACOM}.3.6.11.24"

_DISCRIMINATOR = f"{HUAWEI}.5.25.31.1.1.1"
# Decoy varbinds: plausible, present on every emission, and carrying **nothing** the correlator can
# key on. `eval/corpus/decoy_varbinds.json` establishes the pattern; this reuses it deliberately, so
# that a profiler that started keying on a decoy would fail here as well as there.
_DECOY_SERIAL = f"{HUAWEI}.5.25.31.1.1.9"
_DECOY_TEXT = f"{CISCO}.9.9.999.1.1.1"

_MASK64 = (1 << 64) - 1

# Within-increment timing. An increment spans about a minute of trap time; every incident inside one
# is concurrent with the others, on its own NEs. `TAU_S` is 30 s and `WINDOW_S` is 120 s, so
# `spread_beyond_tau` has to reach past 30 and stay under 120 — 45 s and 95 s do both.
_STAGGER_S = 2.0
SPREAD_DELAYS = (0.0, 45.0, 95.0)
# A storm has to clear `learn.STORM_ALARMS` (50) to exercise `STORM_DAMPING` and
# `MAX_LINKS_PER_ALARM`. Fifty-six ONUs does it with margin.
STORM_ONUS = 56


def draw(seed: int, incident: int, salt: int, bound: int) -> int:
    """A pure function of the seed. **Not a stream** — see the module docstring."""
    z = (
        seed * 0x9E3779B97F4A7C15 + incident * 0xBF58476D1CE4E5B9 + salt * 0x94D049BB133111EB
    ) & _MASK64
    z ^= z >> 30
    z = (z * 0xBF58476D1CE4E5B9) & _MASK64
    z ^= z >> 27
    z = (z * 0x94D049BB133111EB) & _MASK64
    z ^= z >> 31
    return (z >> 32) % bound


def shape_of(incident: int) -> str:
    """Which registered shape incident `incident` is. The quota repeats every increment."""
    return _PATTERN[incident % INCREMENT_INCIDENTS]


def device(incident: int, block: int, host: int) -> str:
    """A device address for one incident. Distinct incidents never share an NE.

    `10.<block>.<incident high>.<incident low + host>` — the second octet separates the roles inside
    an incident (OLT, ONU, transport, access), and the last two carry the incident number, so no two
    incidents can collide and every address is a legal, private IPv4.
    """
    return f"10.{block}.{(incident // 200) % 250 + 1}.{(incident % 200) + host}"


def _decoys(incident: int, salt: int) -> tuple[Varbind, ...]:
    """Two varbinds that look like data and key nothing.

    A serial number that is unique per emission — so a profiler keying on it would shatter every
    situation — and a free-text field. `eval/corpus/decoy_varbinds.json` is the same trap; this one
    is drawn rather than authored so it differs per incident.
    """
    serial = draw(SEED, incident, salt, 1_000_000)
    return (
        Varbind(_DECOY_SERIAL, f"SN{serial:07d}"),
        Varbind(_DECOY_TEXT, f"auto-generated event {serial % 977}"),
    )


def emit(
    device: str,
    trap_oid: str,
    entity_key: str,
    situation_key: str,
    delay: float,
    *,
    incident: int,
    salt: int,
    severity: str = "major",
    is_root: bool = False,
) -> Emission:
    return Emission(
        device=device,
        trap_oid=trap_oid,
        entity_key=entity_key,
        situation_key=situation_key,
        varbinds=(Varbind(_DISCRIMINATOR, entity_key), *_decoys(incident, salt)),
        severity=severity,
        is_root=is_root,
        delay=round(delay, 3),
    )


def _builders() -> dict[str, Any]:
    """The shape table. Imported lazily so `shapes.py` can import this module's OIDs and helpers
    without a cycle — the two halves of one package, not two layers."""
    from simulation import shapes

    return {
        "independent_overlap": shapes.independent_overlap,
        "spread_beyond_tau": shapes.spread_beyond_tau,
        "storm_concealing": shapes.storm_concealing,
        "flap_during_incident": shapes.flap_during_incident,
        "merge_chain": shapes.merge_chain,
        "quiet_noise": shapes.quiet_noise,
    }


def generate(increment: int) -> dict[str, Any]:
    """One increment of the registered network, as a corpus document. **Pure in `increment`.**

    Incidents inside an increment are concurrent, staggered by a couple of seconds so their first
    alarms do not arrive in one packet burst. The returned document carries `truth` on every event —
    which is the simulator's ground truth and, by the plan's §1, **may never enter any quantity the
    promotion gate reads**.
    """
    emissions: list[Emission] = []
    first = increment * INCREMENT_INCIDENTS
    for offset in range(INCREMENT_INCIDENTS):
        incident = first + offset
        base = (offset % 4) * _STAGGER_S
        emissions += _builders()[shape_of(incident)](incident, base)
    scenario = Scenario(
        name=f"simulation_increment_{increment:02d}",
        description=(
            f"v0.14.0 simulated network, increment {increment}: {INCREMENT_INCIDENTS} concurrent "
            f"incidents in the proportions PREREGISTRATION-0.14.0.md §5.1 registers, from seed "
            f"{SEED}."
        ),
        emissions=emissions,
    )
    return scenario.build()


def devices_of(increment: int) -> dict[str, int]:
    """`device -> incident` for one increment, re-derived from the emissions themselves.

    Used only by `measure.py`, which reports what the generator produced. **This is ground truth**,
    so `PREREGISTRATION-0.14.0.md` §1 applies: it may measure the *simulator* and may never enter a
    quantity the promotion gate reads. Deriving it from the emissions rather than re-implementing
    `_device`'s arithmetic keeps the two from disagreeing.
    """
    out: dict[str, int] = {}
    first = increment * INCREMENT_INCIDENTS
    for offset in range(INCREMENT_INCIDENTS):
        incident = first + offset
        for emission in _builders()[shape_of(incident)](incident, 0.0):
            out[emission.device] = incident
    return out
