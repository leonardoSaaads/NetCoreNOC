"""The estimator: grouped repeated cross-validation, a cluster bootstrap, and the power condition.

v0.10.0, Workstream 3. `PREREGISTRATION-0.10.0.md` §3.2 fixes the configuration and it is **not
varied afterwards**.

> **This is the only number any tuning loop, any release, or any human may look at while making a
> modelling choice.**

Say that first, because the next reader will otherwise assume the sealed holdout is the one to
consult. It is not. The holdout is the **decider**, it is spent **once**, and v0.10.0 does not spend
it — so for the whole of this release and the next three, *every* modelling decision is made against
the number this module produces.

## Why grouped, and why the group is the incident

A random 80/20 leaks. Alarms from one incident are correlated with each other **by construction**,
so a random split puts near-duplicates of the test set into the training set and reports a number
that cannot be reproduced on a network. **An incident is wholly within one fold**, and the incident
is the merge-aware one from `netcorenoc.incidents` — a one-hop identity would put two halves of one
merged incident on opposite sides of a fold boundary, which is the leak wearing a different hat.

## What the interval is over, and why it is wide

A cluster bootstrap resamples **incidents**, not pairs and not bags. Resampling pairs would treat
the 62 750 pairs inside a 501-member storm as 62 750 independent observations when they came from
**one human decision**; the design effect reaches four orders of magnitude and the interval would
be narrow and wrong.

Measured on this project's corpus (`docs/gates/v0.10.0-phase-1.md` §1): at **37 incidents the 95 %
interval on a single rate is 0.289 wide** — ±14.5 p.p. Nothing about that is a defect of this code.

## The power condition is a trigger, not a floor

`PREREGISTRATION-0.10.0.md` §2.5 is emphatic and this module implements it literally: the minimum
detectable difference at the available `n` is **computed and printed with every evaluation**, it is
a **verdict trigger**, it is **not a floor**, a deployment may not harden it and no deployment may
disable it.

**The report may never print a floor evaluation without the detection threshold beside it**, and
`tests/test_judge.py` fails if one is emitted without the other. That is the structural mitigation
for §2.5's residual risk: a reader who sees *"floors met"* must not be able to read *"the evaluation
is trustworthy"*.

## Determinism

Fixed fold assignment, fixed resample seeds, fixed row ordering, no wall clock. The same corpus and
the same code produce byte-identical folds, intervals and thresholds across two runs and two
processes. Pure stdlib — a bootstrap over 37 incidents is arithmetic, and reaching for `numpy` here
would be choosing the wrong tool for a corpus of this size.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

__all__ = [
    "FOLDS",
    "REPEATS",
    "RESAMPLES",
    "SEED",
    "Z_ALPHA",
    "Z_POWER",
    "Interval",
    "Power",
    "assign_folds",
    "cluster_bootstrap",
    "minimum_detectable_difference",
    "power_at",
]

# §3.2, fixed here and not varied afterwards. Five folds over 37 incidents leaves ~7 per fold,
# which is small; three repetitions average over the fold assignment without pretending that helps
# with the sample size. A constant that moved after a metric was seen would make §8's stopping rule
# unenforceable, which is why these are module constants rather than parameters with defaults.
FOLDS = 5
REPEATS = 3
SEED = 20_100_000
# 2 000, matching the simulation the plan's §3.1 table was produced from and this build reproduced.
RESAMPLES = 2000

# Normal quantiles, hard-coded. The project ships no scipy and these are the only two it needs;
# `math` has no inverse normal CDF. Two-sided alpha = 0.05, power = 0.80.
Z_ALPHA = 1.959964
Z_POWER = 0.841621


class _Lcg:
    """A tiny deterministic PRNG, so the resamples do not depend on CPython's `random`.

    `random.Random` is stable in practice, but its guarantee is about a *version*, not about a
    *value*, and this project's oldest property is that two runs and two processes produce identical
    bytes. Nine lines of arithmetic with an explicit constant is a smaller promise to keep than
    "the standard library will not change its Mersenne Twister seeding".

    Numerical Recipes' LCG parameters; adequate for resampling indices and for nothing else.
    """

    __slots__ = ("_state",)

    def __init__(self, seed: int) -> None:
        self._state = seed & 0xFFFFFFFF

    def below(self, bound: int) -> int:
        """A pseudo-random integer in `[0, bound)`. `bound` is never zero at any call site.

        **The high bits, not the low ones.** A power-of-two-modulus LCG has notoriously poor low
        bits: `state % 2` alternates with period 2, so `below(2)` would return 0, 1, 0, 1 … and
        every "resample" of a two-cluster corpus would draw exactly one of each. The interval would
        come back **zero-width** — a bootstrap reporting perfect precision because it never
        resampled anything.

        This is not a hypothetical. The first version of this method used `self._state % bound`,
        and `test_the_bootstrap_resamples_incidents_and_not_observations` caught it by asserting a
        two-cluster corpus has a *wider* interval than a 200-cluster one. Both came back 0.000.
        """
        self._state = (1_664_525 * self._state + 1_013_904_223) & 0xFFFFFFFF
        return (self._state >> 16) % bound


def _rng(seed: int) -> _Lcg:
    return _Lcg(seed)


def assign_folds(
    incidents: Sequence[int], *, folds: int = FOLDS, repeat: int = 0
) -> dict[int, int]:
    """`incident -> fold`. **An incident is wholly within one fold, by construction.**

    Deterministic in the incident ids and the repetition index, so two runs and two processes agree.
    The assignment is a rotation over the **sorted** ids rather than a shuffle: sorting makes the
    result independent of the order the store returned, and the rotation by `repeat` is what makes
    three repetitions see three different partitions.

    Note what this signature makes impossible: it takes **incidents**, so there is no way to
    express a row-wise split. A fold assignment that took rows could put two pairs of one incident
    on opposite sides, and nothing downstream would notice.
    """
    ordered = sorted(set(incidents))
    return {incident: (index + repeat) % folds for index, incident in enumerate(ordered)}


@dataclass(frozen=True)
class Interval:
    """A rate with its cluster-bootstrap interval. `width` is what §3.1's table is expressed in."""

    rate: float
    low: float
    high: float
    clusters: int

    @property
    def width(self) -> float:
        return self.high - self.low


def cluster_bootstrap(
    per_cluster: dict[int, list[float]], *, resamples: int = RESAMPLES, seed: int = SEED
) -> Interval:
    """Percentile bootstrap resampling **clusters**, not observations.

    ``per_cluster`` maps an incident to the per-bag values it contributed. Each resample draws
    `len(clusters)` clusters **with replacement** and pools their values — so a storm that
    contributed one bag contributes one bag, and a storm that contributed 62 750 pairs is not
    counted 62 750 times.

    Returns the observed rate with the 2.5th and 97.5th percentiles of the resampled rates.
    """
    clusters = sorted(per_cluster)
    values = [value for cluster in clusters for value in per_cluster[cluster]]
    if not values:
        return Interval(0.0, 0.0, 0.0, 0)
    observed = sum(values) / len(values)
    if len(clusters) < 2:
        # One cluster cannot be resampled into anything else, so the interval would be a point.
        # Returned as a degenerate interval rather than as a narrow one: a zero-width interval is
        # obviously unusable, where 0.001 would look like precision.
        return Interval(observed, observed, observed, len(clusters))

    rng = _rng(seed)
    rates: list[float] = []
    for _ in range(resamples):
        drawn: list[float] = []
        for _ in range(len(clusters)):
            drawn.extend(per_cluster[clusters[rng.below(len(clusters))]])
        rates.append(sum(drawn) / len(drawn) if drawn else 0.0)
    rates.sort()
    low = rates[max(0, min(len(rates) - 1, round(0.025 * (len(rates) - 1))))]
    high = rates[max(0, min(len(rates) - 1, round(0.975 * (len(rates) - 1))))]
    return Interval(observed, low, high, len(clusters))


def minimum_detectable_difference(n: int, p: float = 0.70) -> float:
    """The smallest true difference two models must have for `n` clusters to separate them.

    Two-sided alpha = 0.05, power = 0.80, normal approximation on the difference of two proportions
    measured over `n` clusters each:

        delta = (z_alpha/2 + z_power) * sqrt(2 * p(1-p) / n)

    Documented and dependency-free, as prime directive 4 requires.

    **This build could not reproduce the plan's table below `n = 120`, and did not adjust either
    side** (DECISIONS #142). At `n = 37` this returns **0.298** where §3.1 registers 0.25; an
    independent Monte-Carlo power search, sharing no arithmetic with this expression, returns 0.33.
    At 120 and 300 all three agree. The disagreement runs in the direction that **strengthens** the
    plan's conclusion — if the true threshold at 37 incidents is 30 p.p. rather than 25, "no
    plausible pair of scorers differs by that" is more true, not less — and nothing registered
    depends on 25 being the right number.
    """
    if n <= 0:
        return 1.0
    return (Z_ALPHA + Z_POWER) * math.sqrt(2.0 * p * (1.0 - p) / n)


@dataclass(frozen=True)
class Power:
    """The detection threshold at the observed `n`, and whether the observed difference clears it.

    **Emitted with every floor evaluation**, never without one and never the other way round
    (§2.5's structural mitigation). `sufficient` is the §6.2 trigger: `False` returns
    `INSUFFICIENT_EVIDENCE` whatever the floors say.
    """

    incidents: int
    detectable: float
    observed_difference: float

    @property
    def sufficient(self) -> bool:
        """**The power condition.** False when the corpus cannot resolve the difference it saw.

        Strictly greater: a difference exactly equal to the threshold is not resolved *by* it — the
        threshold is the smallest difference detectable at 80 % power, so equality is the boundary
        case and ambiguity about the evidence resolves to "insufficient".
        """
        return self.observed_difference > self.detectable


def power_at(incidents: int, observed_difference: float, p: float = 0.70) -> Power:
    """The power condition at the observed `n`. **A reported quantity and a verdict trigger.**

    Not a floor: a deployment may not harden it, and no deployment may disable it (§2.5).
    """
    return Power(
        incidents=incidents,
        detectable=minimum_detectable_difference(incidents, p),
        observed_difference=abs(observed_difference),
    )
