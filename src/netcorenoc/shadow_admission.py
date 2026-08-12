"""The admission filter: **may this model compete at all?**

Split from `shadow_eval.py` in v0.10.0 (DECISIONS #150). The seam is a real one rather than a size
accident: `shadow_eval.py` answers *how good is this model* and this answers *is it allowed to be
measured* — a different question, asked first, and with a different consequence. A model that fails
here does not get a bad score; it gets **no score**.

The filter was written before the first model existed, so it could not be shaped around whatever
won, and it is run against the **champion** too — a filter nobody has run against the incumbent is a
filter whose budget is a guess.
"""

from __future__ import annotations

import time
from typing import Any

from netcorenoc.challenger import FEATURE_NAMES, LogisticScorer
from netcorenoc.scoring import AdditiveScorer, LinkFeatures

__all__ = ["admission", "verdict"]


def admission(
    scorer: LogisticScorer | AdditiveScorer, samples: list[LinkFeatures], *, budget_ratio: float
) -> dict[str, Any]:
    """Run one scorer against the filter. **The champion is measured on the same samples.**

    A model competes on quality **only after** passing this, and the filter was written before the
    first model so it could not be shaped around whatever won. Six checks:

    * **speed** — median and p99 microseconds per `score()` call, expressed *relative to the
      champion measured in the same process at the same time*. Phase 0 §5 measured the champion's
      p99 moving 2.6x between two runs on one machine, so an absolute microsecond budget would be a
      measurement of the scheduler; a ratio is not.
    * **explainability** — emits `terms`, one per free parameter, summing to the score. Asserted.
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
    return {
        "scorer_id": scorer.scorer_id,
        "contract_version": scorer.contract_version,
        "calls": len(timings),
        "median_us": round(_quantile(timings, 0.5), 3),
        "p99_us": round(_quantile(timings, 0.99), 3),
        "budget_ratio": budget_ratio,
        "explainable": bool(first.terms) and total == first.score,
        "terms": len(first.terms),
        "deterministic": first == second and first.score.hex() == second.score.hex(),
        "memory_stable": _state_size(scorer) == before,
        "state_floats": before,
    }


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
        reasons.append("explainability: contributions do not sum to the score")
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
    return not reasons, reasons
