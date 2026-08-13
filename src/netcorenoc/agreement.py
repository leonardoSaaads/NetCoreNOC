"""**How well the champion already agrees with the operators** — v0.9.0's primary deliverable.

> The most valuable output of a shadow-mode release is not a model. It is two numbers: how well the
> incumbent already agrees with the operators, and whether there is enough signal in the data to
> learn anything at all. This module is the first.

**It needs no model, no training and no new theory**, which is why it is delivered first and
independently of everything else. The headline is cheap — at bag level the champion's agreement *is*
the confirm rate. **The deliverable is the conditioning**, because an aggregate of 94 % that
decomposes into 99 % on uniform storm bags and 58 % on mixed bags is the difference between an ML
programme with no headroom and one with a clear target.

Six cuts, each answering a question the aggregate cannot:

* **by bag size** — confirmation strength decays with size; confirming a 500-member storm and
  confirming a 2-member pair are not the same evidence;
* **by storm** — in a storm the champion is right about something it could not have got wrong;
* **by mixed versus uniform bag** — the strongest cut. A *uniform* bag contained no decision: every
  pair in it fell on the same side of the threshold, so the operator confirmed an outcome the
  champion could not have reached differently;
* **by `scope_restricted`** — that operator was judging a partial view;
* **by operator** — a headline over three people is three people's opinion;
* **by `capture_provenance`** — `current` and `legacy_capture` **reported separately and never
  averaged**.

**Aggregates only, and operators are anonymised.** Every value below is a count, a rate or an
interval. No NE, no address, no OID, no varbind, and no row leaves this module — the same posture
`bias.py` holds, for the same reason: these tables are a scope bypass by construction. The
per-operator cut prints **`operator 1 … operator N`**, never a `principal_ref`, because what the cut
is for is the *spread* — whether `confirm` means the same thing to two people — and an identity
would turn a bias measurement into a per-employee performance report nobody consented to
(DECISIONS #120). `bias.py` already holds this line by reporting shares and never names.

**Determinism is a hard requirement**, because `tests/test_agreement.py` compares the rendered
output byte-for-byte against a frozen fixture. One `ORDER BY`-stable query, no wall clock, fixed
float precision, and a bootstrap driven by an LCG written out below rather than by `random`, whose
generator is a stdlib implementation detail this report must not depend on.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

# v0.10.1 (B1). `Bag`, `size_bucket` and `SIZE_ORDER` moved to `agreement_bags.py` when routing this
# report's incident identity through `netcorenoc.incidents` took the module past its 400-line
# ceiling. They are re-exported because `agreement_report.py` and `tests/test_agreement.py` have
# imported them from here since v0.9.0, and a split is not a reason to move a caller's import
# (DECISIONS #155). **Split, never exempted**: the ceiling has not been raised, and
# `DEBT_ALLOWLIST` is still empty.
from netcorenoc.agreement_bags import SIZE_ORDER, Bag, load_bags, size_bucket

if TYPE_CHECKING:  # pragma: no cover - type-only, no runtime edge (tests/test_layers.py)
    from netcorenoc.store import Store

__all__ = [
    "BOOTSTRAP_RESAMPLES",
    "BOOTSTRAP_SEED",
    "MIN_INCIDENTS_FOR_INTERVAL",
    "SIZE_ORDER",
    "Bag",
    "Rate",
    "collect",
    "load_bags",
    "size_bucket",
]

# Fixed in `docs/analysis/PREREGISTRATION-0.9.0.md` §5.5 and not tuned afterwards.
BOOTSTRAP_RESAMPLES = 1000
BOOTSTRAP_SEED = 20090409
MIN_INCIDENTS_FOR_INTERVAL = 10

# A 64-bit LCG (Knuth's MMIX constants), written out rather than imported.
# `random.Random` is deterministic for a given seed *on a given implementation*; the report is a
# byte-for-byte gate that must survive an interpreter upgrade, so the generator is part of the
# source. Six lines, no dependency, and the interval is reproducible forever.
_LCG_A = 6364136223846793005
_LCG_C = 1442695040888963407
_LCG_MASK = (1 << 64) - 1


class _Rng:
    """The resampler. `below(n)` is the top 31 bits of the state, which avoids the low-order
    short-period behaviour every LCG has."""

    __slots__ = ("_state",)

    def __init__(self, seed: int) -> None:
        self._state = seed & _LCG_MASK

    def below(self, n: int) -> int:
        self._state = (_LCG_A * self._state + _LCG_C) & _LCG_MASK
        return ((self._state >> 33) & 0x7FFFFFFF) % n


@dataclass(frozen=True)
class Rate:
    """A confirm rate over a set of bags, with its clustered interval.

    ``lo``/``hi`` are ``None`` when the bootstrap was refused — see :func:`_interval`. A rate with
    an interval nobody should read is reported without one rather than with a narrow lie.
    """

    n: int
    confirms: int
    lo: float | None
    hi: float | None

    @property
    def pct(self) -> float | None:
        return None if self.n == 0 else 100.0 * self.confirms / self.n


def _percentile(ordered: list[float], q: float) -> float:
    """Nearest-rank, the same convention `eval/metrics.py::percentile` uses. Deterministic."""
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def _interval(bags: list[Bag]) -> tuple[float | None, float | None]:
    """A cluster bootstrap over **incidents**, per the pre-registration §5.5.

    Incidents, not bags and never pairs. Two labels on situations that were merged into one are one
    incident, and resampling bags would treat them as independent — which is the factor
    `SHADOW-MODE-0.9-DRAFT.md` §4 says nobody checks. Below
    :data:`MIN_INCIDENTS_FOR_INTERVAL` incidents the bootstrap is refused outright: resampling six
    clusters produces an interval that looks like a measurement and is an artefact of six numbers.
    """
    clusters: dict[int, list[Bag]] = {}
    for bag in bags:
        clusters.setdefault(bag.incident, []).append(bag)
    keys = sorted(clusters)
    if len(keys) < MIN_INCIDENTS_FOR_INTERVAL:
        return None, None
    rng = _Rng(BOOTSTRAP_SEED)
    rates: list[float] = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        total = confirms = 0
        for _draw in range(len(keys)):
            for bag in clusters[keys[rng.below(len(keys))]]:
                total += 1
                confirms += 1 if bag.agreed else 0
        if total:
            rates.append(100.0 * confirms / total)
    if not rates:
        return None, None
    rates.sort()
    return _percentile(rates, 0.025), _percentile(rates, 0.975)


def rate_of(bags: list[Bag], *, interval: bool = True) -> Rate:
    """The confirm rate over a set of bags. ``interval=False`` for cuts whose cells are too small
    to bootstrap meaningfully anyway — the refusal is then structural rather than incidental."""
    confirms = sum(1 for bag in bags if bag.agreed)
    lo, hi = _interval(bags) if interval else (None, None)
    return Rate(n=len(bags), confirms=confirms, lo=lo, hi=hi)


UNATTRIBUTED = "(unattributed)"


def operator_aliases(bags: list[Bag]) -> dict[str, str]:
    """`principal_ref -> 'operator N'`. **The identity never leaves this module.**

    Ordered by bag count descending, then by a digest of the reference — never by the reference
    itself, which would leak an alphabetical position, and never by insertion order, which would
    make the report depend on row ids. `(unattributed)` keeps its own name because it is a *state*,
    not a person.
    """
    counts: dict[str, int] = {}
    for bag in bags:
        counts[bag.operator] = counts.get(bag.operator, 0) + 1
    named = sorted(
        (ref for ref in counts if ref != UNATTRIBUTED),
        key=lambda ref: (-counts[ref], hashlib.sha256(ref.encode()).hexdigest()),
    )
    aliases = {ref: f"operator {i}" for i, ref in enumerate(named, start=1)}
    aliases[UNATTRIBUTED] = UNATTRIBUTED
    return aliases


def _group(bags: list[Bag], key: Any, order: list[str] | None = None) -> list[tuple[str, Rate]]:
    """Group by a key function and return `(label, Rate)` in a fixed order.

    ``order`` fixes the row order for a known, closed set of values; otherwise rows are sorted by
    label. Either way the output order is a property of the code, not of the data.
    """
    grouped: dict[str, list[Bag]] = {}
    for bag in bags:
        grouped.setdefault(str(key(bag)), []).append(bag)
    labels = [k for k in (order or []) if k in grouped] if order else sorted(grouped)
    if order:
        labels += sorted(k for k in grouped if k not in order)
    return [(label, rate_of(grouped[label])) for label in labels]


def _cuts(bags: list[Bag]) -> dict[str, Any]:
    """The five conditional cuts, computed once and reused per channel (v0.9.1).

    A channel that changes WHICH situations get labelled changes what every one of these rates is a
    rate *of*, so each is repeated per channel rather than averaged — and one definition means the
    per-channel figure and the overall figure can never drift apart.
    """
    return {
        "by_size": _group(
            bags, lambda b: size_bucket(b.size) if b.size >= 0 else "unrecorded", SIZE_ORDER
        ),
        "by_storm": _group(bags, lambda b: b.storm_state, ["quiet", "partly", "storm", "no-pairs"]),
        "by_mixedness": _group(
            bags, lambda b: b.mixedness, ["mixed", "uniform-accept", "uniform-reject", "no-pairs"]
        ),
        "by_scope": _group(
            bags,
            lambda b: "restricted" if b.scope_restricted else "full view",
            ["full view", "restricted"],
        ),
        "by_coverage": _group(
            bags, lambda b: b.coverage, ["full", "partial", "none", "empty", "unrecorded"]
        ),
    }


async def collect(store: Store) -> dict[str, Any]:
    """Every measurement Workstream 1 requires. Caller holds ``store.lock``.

    **The headline and every cut below it are computed over `capture_provenance = 'current'`
    alone.** `legacy_capture` rows were acquired over the path v0.7.5 repaired; they are of
    *unknown* quality, which is a weaker claim than bad, and blending them into a headline would
    make the headline uninterpretable in a way no reader could detect. They get their own row in the
    provenance section, and that is where they are compared.
    """
    everything = await load_bags(store)
    bags = [b for b in everything if b.provenance == "current"]
    aliases = operator_aliases(bags)
    out: dict[str, Any] = {
        "bags_all": len(everything),
        "bags": len(bags),
        "incidents": len({b.incident for b in bags}),
        "operators": len({b.operator for b in bags if b.operator != UNATTRIBUTED}),
        "headline": rate_of(bags),
        **_cuts(bags),
        # Anonymised at the boundary: the alias is computed here and the reference is never put in
        # the returned document, so a renderer cannot print one even by mistake.
        "by_operator": _group(bags, lambda b: aliases[b.operator]),
        # v0.9.1. The channel split, and then every cut above it repeated within each channel.
        "by_channel": _group(bags, lambda b: b.channel, ["organic", "close", "unrecorded"]),
        "per_channel_cuts": {
            c: {"headline": rate_of(sub), **_cuts(sub)}
            for c, sub in (
                (c, [b for b in bags if b.channel == c]) for c in sorted({b.channel for b in bags})
            )
        },
        # The one cut computed over EVERYTHING, because separating the populations is its entire
        # purpose. Never averaged, never folded into the headline.
        "by_provenance": _group(
            everything, lambda b: b.provenance, ["current", "legacy_capture", "unrecorded"]
        ),
        # The two quantities a reader will want to check the headline against.
        "verdicts": {
            v: sum(1 for b in bags if b.verdict == v) for v in sorted({b.verdict for b in bags})
        },
        "bags_without_pairs": sum(1 for b in bags if b.pairs == 0),
        "pairs_total": sum(b.pairs for b in bags),
    }
    return out
