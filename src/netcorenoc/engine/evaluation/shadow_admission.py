"""The admission filter: **may this model compete at all?**

Split from `shadow_eval.py` in v0.10.0 (DECISIONS #150). The seam is a real one rather than a size
accident: `shadow_eval.py` answers *how good is this model* and this answers *is it allowed to be
measured* — a different question, asked first, and with a different consequence. A model that fails
here does not get a bad score; it gets **no score**.

The filter was written before the first model existed, so it could not be shaped around whatever
won, and it is run against the **champion** too — a filter nobody has run against the incumbent is a
filter whose budget is a guess.

## v0.14.0 — the band gains a lower side, and the lower side is not the clock

`PREREGISTRATION-0.14.0.md` §4, registered before any tree existed. The upper bound was always a
**ratio** to the champion measured in the same process at the same time, because Phase 0 of v0.10.0
measured the champion's own p99 moving 2.6x between two runs on one machine — so an absolute budget
would be a measurement of the scheduler.

A **lower** bound in wall clock would be worse than useless, and v0.14.0's Gate 0 measured why:

    degenerate-vs-working gap, additive shape : 0.0190 us
    degenerate-vs-working gap, logistic shape : 0.0010 us
    worst run-to-run spread on ONE unchanged arm: 0.0950 us

The all-zero model — which raises nothing, returns a finite score, sums its contributions correctly,
and **writes not one link** — sits *inside the timing noise* of a working model of the same shape.
The clock carries no signal about this failure in either direction. The output distribution carries
all of it: 10 976 links against 0 over the same corpus.

So the registered lower bound is **discrimination**, and it is `MIN_WEIGHT_SUM` generalised from
parameters to behaviour:

* **spread** — the standard deviation of the scorer's scores over a fixed probe set must exceed
  `MIN_SCORE_SPREAD`;
* **decision** — it must return `linked = True` for at least one probe and `False` for at least one.

**Both are hardening-only.** A deployment may raise the spread floor and may never lower it, and may
never disable the decision half. `resolve_spread_floor` composes with `max`, which is the same
`resolved = the more demanding of (project floor, deployment policy)` rule
`training.resolve_floors` applies to the evidence floors.

An optional wall-clock lower bound exists as a **mechanism**-class setting with a default of zero.
It is a proxy, the surface says so, and it may never be the only lower bound in effect — which is
structural here rather than a promise: the discrimination checks have no off switch.

**Why this generalisation is required rather than tidy.** For an in-process kind the plan's §2 rules
inspect the parameters directly. For a model whose parameters cannot be inspected — v0.15.0's
cartridge — a behavioural floor is the **only** form threshold-reachability can take. This is
written to be that form, and §4.3 says v0.15.0 must not write a second one.
"""

from __future__ import annotations

import math
import time
from typing import Any

from netcorenoc.engine.correlate.scorer_contract import FEATURE_NAMES, TAU0_S, LinkFeatures
from netcorenoc.engine.model.background import BACKGROUND

__all__ = [
    "MIN_SCORE_SPREAD",
    "SPREAD_META_KEY",
    "WALL_CLOCK_META_KEY",
    "admission",
    "probe_features",
    "resolve_spread_floor",
    "resolve_wall_clock_floor",
    "verdict",
]

# §4.2, registered: "which is `scoring.MIN_THRESHOLD`'s magnitude and is chosen for consistency with
# it rather than from any observed distribution". Stated that way in the plan deliberately — a floor
# derived from a distribution nobody had measured yet would be a floor chosen to suit a result.
MIN_SCORE_SPREAD = 0.01

# The deployment's hardening-only override, absent by default. `meta` is how this product already
# persists operator configuration (DECISIONS #111/#114) — no route and no capability for a value no
# scoped principal may read anyway.
SPREAD_META_KEY = "config.min_score_spread"
# The optional wall-clock lower bound. **Mechanism class, default zero, and a proxy.**
WALL_CLOCK_META_KEY = "config.min_score_us"


def probe_features() -> list[LinkFeatures]:
    """**The fixed probe set**: the plan's §3 background, as `LinkFeatures`, in the same order.

    The same rows the attribution is measured against, so a model is admitted on the distribution it
    is explained on. `delta_t_s` is recovered from the stored decay by inverting
    `challenger.feature_vector`'s `exp(-|Δt| / TAU0_S)`; the decay is bounded strictly above zero by
    the correlation window, so the logarithm is total.
    """
    return [
        LinkFeatures(
            delta_t_s=-TAU0_S * math.log(decay),
            class_i=0,
            class_j=1,
            class_affinity=class_affinity,
            ne_i=0,
            ne_j=1,
            entity_affinity=entity_affinity,
        )
        for decay, class_affinity, entity_affinity in BACKGROUND
    ]


def resolve_spread_floor(stored: str | None) -> tuple[float, str | None]:
    """`(resolved floor, warning)` — the project floor, hardened by a deployment policy.

    **A deployment may raise it and can never lower it**, including by setting it to zero, to null,
    or by omitting it. An unreadable value falls back to the project floor and returns a warning,
    the same discipline `training.resolve_floors` applies and for the same reason: a policy that
    cannot be parsed must not become a policy that admits more than the shipped default would.
    """
    if stored is None:
        return MIN_SCORE_SPREAD, None
    try:
        return max(MIN_SCORE_SPREAD, float(stored)), None
    except (TypeError, ValueError):
        return MIN_SCORE_SPREAD, (
            f"The stored discrimination floor ({SPREAD_META_KEY}) could not be read and was "
            f"ignored; the project floor {MIN_SCORE_SPREAD} is in effect. It may only ever be "
            "raised."
        )


def resolve_wall_clock_floor(stored: str | None) -> float:
    """The optional wall-clock lower bound, in microseconds. **Mechanism class, default zero.**

    Zero means *no wall-clock floor*, which is the shipped state and the honest one: §4.1 measured
    that the clock cannot separate a working model from a degenerate one. A deployment may set it,
    and the surface tells them it is a proxy and which check is the real one.
    """
    if stored is None:
        return 0.0
    try:
        return max(0.0, float(stored))
    except (TypeError, ValueError):
        return 0.0


def admission(
    scorer: Any,
    samples: list[LinkFeatures],
    *,
    budget_ratio: float,
    probes: list[LinkFeatures] | None = None,
    min_spread: float = MIN_SCORE_SPREAD,
    min_score_us: float = 0.0,
) -> dict[str, Any]:
    """Run one scorer against the filter. **The champion is measured on the same samples.**

    A model competes on quality **only after** passing this, and the filter was written before the
    first model so it could not be shaped around whatever won. Eight checks:

    * **speed (upper)** — median and p99 microseconds per `score()` call, expressed *relative to the
      champion measured in the same process at the same time*.
    * **discrimination (lower)** — score spread over the fixed probe set, and both verdicts present.
      v0.14.0, and the reason it is not a clock is in the module docstring.
    * **wall clock (lower, optional)** — a proxy, default off.
    * **explainability** — emits `terms`, one per free parameter, summing to
      `score - base_value`. Asserted.
    * **determinism** — the same features give a byte-identical score on a second call.
    * **memory** — the scorer's own state does not grow with the number of calls.
    * **contract** — implements `LinkScorer` at a version this build supports.
    * **dependencies** — none new; asserted at the suite level, reported here for completeness.
    """
    warmed = samples[: min(200, len(samples))]
    for features in warmed:
        scorer.score(features)
    timings: list[float] = []
    for features in samples:
        started = time.perf_counter_ns()
        scorer.score(features)
        timings.append((time.perf_counter_ns() - started) / 1000.0)
    timings.sort()

    probe = samples[0]
    first, second = scorer.score(probe), scorer.score(probe)
    total = 0.0
    for term in first.terms:
        total += term.contribution
    before = _state_size(scorer)
    for features in samples[: min(1000, len(samples))]:
        scorer.score(features)

    if probes is None:
        probes = probe_features()
    spread, linked, unlinked = _discrimination(scorer, probes)
    return {
        "scorer_id": scorer.scorer_id,
        "contract_version": scorer.contract_version,
        "calls": len(timings),
        "median_us": round(_quantile(timings, 0.5), 3),
        "p99_us": round(_quantile(timings, 0.99), 3),
        "budget_ratio": budget_ratio,
        # **One expression, correct for both bases** (DECISIONS #186). `base_value` defaults to 0.0
        # for a weighted sum, so this is the v0.11.0 check unchanged wherever it was already right.
        "explainable": bool(first.terms) and total + first.base_value == first.score,
        "basis": first.basis,
        "base_value": first.base_value,
        "terms": len(first.terms),
        "deterministic": first == second and first.score.hex() == second.score.hex(),
        "memory_stable": _state_size(scorer) == before,
        "state_floats": before,
        # v0.14.0 — the lower side of the band.
        "score_spread": round(spread, 9),
        "min_score_spread": min_spread,
        "probes": len(probes),
        "probes_linked": linked,
        "probes_unlinked": unlinked,
        "min_score_us": min_score_us,
    }


def _discrimination(scorer: Any, probes: list[LinkFeatures]) -> tuple[float, int, int]:
    """`(population standard deviation of the scores, probes linked, probes not linked)`.

    Population rather than sample standard deviation: the probe set is the **whole** population this
    floor is defined over — it is a fixed, registered set, not a draw from something larger — so
    dividing by `n - 1` would be correcting for a sampling that did not happen.
    """
    scores = [scorer.score(features) for features in probes]
    values = [result.score for result in scores]
    if not values:
        return 0.0, 0, 0
    mean = sum(values) / len(values)
    spread = math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))
    linked = sum(1 for result in scores if result.linked)
    return spread, linked, len(scores) - linked


def _quantile(ordered: list[float], q: float) -> float:
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))] if ordered else 0.0


def _state_size(scorer: object) -> int:
    """The number of scalars the scorer carries. A cache or an accumulator would move it."""
    return sum(1 for value in vars(scorer).values() if isinstance(value, (int, float, str))) + len(
        FEATURE_NAMES
    )


def verdict(challenger: dict[str, Any], champion: dict[str, Any]) -> tuple[bool, list[str]]:
    """`(admitted, reasons it was not)`. **A model failing any check does not compete.**"""
    reasons: list[str] = []
    if not challenger["explainable"]:
        reasons.append(
            "explainability: the contributions plus the base value do not equal the score"
        )
    if not challenger["deterministic"]:
        reasons.append("determinism: two calls on identical features differed")
    if not challenger["memory_stable"]:
        reasons.append("memory: the scorer's state grew while scoring")
    if challenger["contract_version"] != champion["contract_version"]:
        reasons.append(
            f"contract: {challenger['contract_version']} is not the running "
            f"{champion['contract_version']}"
        )
    budget = champion["p99_us"] * challenger["budget_ratio"]
    if challenger["p99_us"] > budget:
        reasons.append(
            f"speed: p99 {challenger['p99_us']:.3f}us over the budget {budget:.3f}us "
            f"({challenger['budget_ratio']:.1f}x the champion's {champion['p99_us']:.3f}us)"
        )
    # -- the lower side of the band (v0.14.0) ---------------------------------------------------
    # Named halves, because §4.2 requires the reason to say WHICH half failed: "gives every pair a
    # near-identical score" and "gives every pair the same answer" are different malfunctions with
    # different repairs, and a single message would send an operator to the wrong one.
    if challenger["score_spread"] <= challenger["min_score_spread"]:
        reasons.append(
            f"discrimination (spread): the scores over {challenger['probes']} probes have a "
            f"standard deviation of {challenger['score_spread']:.6f}, at or below the floor "
            f"{challenger['min_score_spread']}. A scorer that gives every pair a near-identical "
            "score cannot discriminate, whatever its speed."
        )
    if not challenger["probes_linked"] or not challenger["probes_unlinked"]:
        reasons.append(
            f"discrimination (decision): {challenger['probes_linked']} of "
            f"{challenger['probes']} probes linked and {challenger['probes_unlinked']} did not. "
            "A scorer that links every pair or no pair returns one answer to every question."
        )
    if challenger["min_score_us"] > 0.0 and challenger["median_us"] < challenger["min_score_us"]:
        reasons.append(
            f"wall clock (proxy): median {challenger['median_us']:.3f}us below the configured "
            f"floor {challenger['min_score_us']:.3f}us. This is a PROXY for a model that is not "
            "doing the work; the discrimination checks above are the real ones."
        )
    return not reasons, reasons
