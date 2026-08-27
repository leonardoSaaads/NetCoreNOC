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

# The fixed-point solve in `minimum_detectable_difference`. The bound is a SECOND guard, not the
# stopping rule: the iteration is a contraction and converges to 1e-12 in under ten steps at every
# `n` this project can produce, so reaching 64 would be evidence that the arithmetic changed rather
# than that the problem was hard. Returning the last iterate rather than raising is deliberate and
# matches `incidents.MAX_CHAIN_DEPTH`: this is an offline report, and a threshold that is slightly
# off is a number to report, where an exception would take the whole evaluation down with it.
_MDD_MAX_ITERATIONS = 64
_MDD_TOLERANCE = 1e-12


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
    measured over `n` clusters each. **Each arm carries its own variance:**

        delta = (z_alpha/2 + z_power) * sqrt((p(1-p) + q(1-q)) / n),   q = p + delta

    which is implicit in `delta` and solved by fixed-point iteration from the equal-variance value.
    It converges monotonically downward in a handful of steps, adds no dependency, and is a
    contraction wherever it is evaluated here — `d(rhs)/d(delta)` is bounded well below 1 for every
    `n >= 1` at these quantiles, because the square root's argument moves by at most `1/(4n)` per
    unit of `delta`.

    **v0.10.1 (A2) replaced `2 * p(1-p)`, and the correction runs in the opposite direction to the
    one DECISIONS #142 recorded.** The old form gave **both** arms the base-rate variance. The
    second arm sits at `p + delta`, and when the detectable delta is large — which is exactly the
    small-`n` regime — its variance is far smaller: at `n = 37` and `p = 0.70` the arms are 0.210
    and **0.058**. Assuming the larger one for both **demands a bigger delta than reality**, and the
    error grows as `n` shrinks. Measured, against an independent Monte-Carlo sharing no arithmetic
    with this expression (`tests/test_shadow_cv_power.py`):

        n     naive    this    MC     plan
        37    0.298   0.238   0.237   0.25
        120   0.166   0.149   0.150   0.16
        300   0.105   0.099   0.099   0.10

    So the plan's §3.1 table was **not** optimistic; the closed form was pessimistic, and the two
    "independent" methods that agreed with each other in v0.10.0 shared the assumption that was
    wrong. DECISIONS **#154** supersedes #142. `PREREGISTRATION-0.10.0.md` is **not edited** — it is
    ratified and hash-guarded, and this table is a reported quantity and a verdict trigger, never a
    floor, so nothing registered depends on the number.

    The direction matters for what it does **not** do: a lower threshold makes
    `observed > detectable` *easier* to satisfy, so v0.10.0's `INSUFFICIENT_EVIDENCE` — reached on a
    floor failure — is unaffected. What it repairs is a trigger that was more conservative than
    intended, which would have told a future release with a real corpus that it could not resolve a
    difference it actually could.
    """
    if n <= 0:
        return 1.0
    delta = (Z_ALPHA + Z_POWER) * math.sqrt(2.0 * p * (1.0 - p) / n)
    for _step in range(_MDD_MAX_ITERATIONS):
        # Clamped because a large delta at tiny `n` can push the second arm past 1.0, where
        # `q(1-q)` would go negative and the square root would raise. At q = 1 the arm's variance is
        # zero, which is the correct limit: a proportion of 1 has no sampling variance.
        q = min(1.0, p + delta)
        nxt = (Z_ALPHA + Z_POWER) * math.sqrt((p * (1.0 - p) + q * (1.0 - q)) / n)
        if abs(nxt - delta) < _MDD_TOLERANCE:
            return nxt
        delta = nxt
    return delta


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
