"""Deriving labels, deciding whether to train at all, and the fit itself.

Three decisions live here, in the order they are made:

1. **Is the corpus sufficient?** Against floors pre-registered before any result existed, resolved
   against a deployment policy that may only ever make them *harder* (DECISIONS #114). **If not,
   nothing is fitted** — and that is a successful outcome, reported with a projection of how long
   until the floors would be met at the measured labelling rate.
2. **How does a bag-level verdict become a per-pair label?** Policies **A** and **B**, both
   implemented, both reported. A release that picked one and reported its number would have assumed
   exactly what v0.8.0 refused to assume, one release later.
3. **The fit**: weighted logistic regression by batch gradient descent, fixed iteration count, no
   RNG, pure Python. Byte-identical coefficients on two runs and in two processes — the project's
   oldest property, and a model does not get an exemption from it.

**Nothing here holds a lock.** The caller reads its rows under `store.lock`, releases it, and calls
:func:`fit`; the fit touches no store, no clock that affects a result, and no shared state.
:func:`fit` is `async` for one reason only — it yields to the event loop between iterations, so a
multi-second fit cannot stall ingestion. The yields are between iterations, never inside the
arithmetic, so they change no number.
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass
from typing import Any

from netcorenoc.engine.dataset.census import CorpusStats
from netcorenoc.engine.model import confidence
from netcorenoc.engine.model.challenger import Coefficients, feature_vector, sigmoid
from netcorenoc.engine.model.sufficiency import PROJECT_FLOORS as PROJECT_FLOORS
from netcorenoc.engine.model.sufficiency import Floors as Floors
from netcorenoc.engine.model.sufficiency import Sufficiency as Sufficiency
from netcorenoc.engine.model.sufficiency import assess as assess
from netcorenoc.engine.model.sufficiency import resolve_floors as resolve_floors

__all__ = [
    "ITERATIONS",
    "L2",
    "LEARNING_RATE",
    "MAX_PAIRS_PER_BAG",
    "MAX_TRAINING_ROWS",
    "PROJECT_FLOORS",
    "Floors",
    "LabelledPair",
    "Sufficiency",
    "TrainingRow",
    "derive",
    "fit",
]

# `CorpusStats` moved to `census.py` in v0.10.0 — "what the labelled corpus contains" is that
# module's whole subject, and this one had reached its 400-line budget. Re-exported here
# because `assess()` takes one and every caller since v0.9.0 has imported it from this module.
#
# **v0.16.0: the sufficiency half moved to `sufficiency.py` for the same reason**, and the same
# re-export rule applies. The seam: `derive` and `fit` below are arithmetic over rows, while the
# floors, a deployment's hardening of them and the two-valued verdict are policy.
__all__ += ["CorpusStats"]

DAY_S = 86400.0
MONTH_DAYS = 30.44

# -- the fit's fixed hyper-parameters --------------------------------------------------------
# Fixed constants, not tuned against a result: §8 of the pre-registration forbids re-running with
# different settings after seeing a metric, and a constant that moved would make that unenforceable.
ITERATIONS = 200
LEARNING_RATE = 0.5
# A small ridge. Not a modelling flourish: policy B's derived labels are ALL POSITIVE (see
# `derive`), and an unregularised logistic fit on a single-class target has no finite optimum — the
# intercept diverges. L2 gives it one, so the degenerate case produces a reportable model instead of
# an overflow, which is what makes B's failure visible rather than absent.
L2 = 1e-3

# -- bounded work ----------------------------------------------------------------------------
# The fit is bounded by construction rather than by a timeout, because a time-based early stop
# would make the result depend on the machine — and determinism outranks a deadline (directive 5).
# Both caps are deterministic selections, and both are reported on the run row.
#
# A bag of 1 051 members implies 551 775 pairs. Keeping every one would make the fit's cost a
# property of the largest storm in the corpus. The first `MAX_PAIRS_PER_BAG` by pair id are kept
# and the bag's weight is renormalised over what was kept, so **every bag still contributes exactly
# one unit of mass** — the pre-registration §2.1 property is preserved exactly; only the resolution
# within a large bag is reduced.
MAX_PAIRS_PER_BAG = 256
# The overall ceiling, applied after the per-bag cap by keeping the NEWEST bags. Newest rather than
# largest or random: recency is the one ordering that is both deterministic and defensible as a
# training-window choice.
MAX_TRAINING_ROWS = 8000


@dataclass(frozen=True)
class LabelledPair:
    """One promoted pair, carrying the bag it belongs to. What the store hands training."""

    pair_id: int
    feedback_id: int
    verdict: str
    incident: int
    delta_t_s: float
    class_affinity: float
    entity_affinity: float
    incumbent_linked: bool
    evaluated_at: float
    label_at: float
    # v0.16.0. Two additive fields, both defaulted so every existing constructor is unchanged.
    #
    # `source` names which record this pair's bag came from, and it exists because the two id
    # spaces are disjoint but not distinguishable: `feedback.id` and `situation_event.id` both start
    # at 1, and bucketing on the id alone would silently merge a label's bag with an event's. The
    # bucket key is the PAIR, which is what makes "every bag contributes exactly one unit of mass"
    # still true across both.
    source: str = "feedback"
    # The operator's stated confidence, or `None` for a gesture that reported none — which is every
    # label written before this release, and is why `m(None) = 1.0` (see `confidence.py`).
    confidence: float | None = None

    @property
    def bag(self) -> tuple[str, int]:
        """The bucket this pair belongs to: **one human decision**, whatever its row count."""
        return self.source, self.feedback_id


@dataclass(frozen=True)
class TrainingRow:
    """One derived training row: the target, the weight, and the features."""

    y: float
    weight: float
    x: tuple[float, ...]


def derive(pairs: list[LabelledPair], policy: str) -> tuple[list[TrainingRow], dict[str, Any]]:
    """Turn bag-level verdicts into per-pair rows under policy **A** or **B**.

    * **A** — `confirm` → every pair positive; `split` → every pair **negative**. Maximum data, and
      it **fabricates negatives**: an operator splitting nine members usually means *"these three do
      not belong with those six"*, not *"all thirty-six pairs are wrong"*. A manufactures up to
      thirty-five false negatives from one true statement, and every fabricated row is
      indistinguishable from an observed one once written.
    * **B** — `confirm` bags only; `split` discarded. Honest, and it throws away the minority class.

    > **B's derived labels are therefore ALL POSITIVE**, and that is not a defect of this
    > implementation — it is what the policy *is* on bag-level labels. The draft called B *"throws
    > away the minority class"*; measured, it throws away the only source of negatives, so the
    > target is constant and the best achievable model predicts "link" unconditionally. The result
    > is reported rather than suppressed, because a policy whose failure is invisible is a policy a
    > later release will pick by default.

    Weighting, per pre-registration §2.1: `w_bag = 1/(rows kept for that bag)` so **every bag
    contributes exactly one unit of mass** whatever its size, then class balancing so the two
    derived classes carry equal total weight. Returns the rows and a diagnostic document.
    """
    by_bag: dict[tuple[str, int], list[LabelledPair]] = {}
    dropped_below_floor = 0
    for pair in sorted(pairs, key=lambda p: (p.source, p.feedback_id, p.pair_id)):
        if policy == "B" and pair.verdict != "confirm":
            continue
        # **The registered confidence floor** (`PREREGISTRATION-0.16.0.md` §4): a gesture below it
        # produces **no training row**. The action still happened and its event is recorded in full
        # — the operator is running the network, not labelling it — and it contributes nothing here.
        #
        # The gate is here as well as at capture time, and the duplication is deliberate: the route
        # refuses to write a *label* below the floor, and this refuses to derive a *row* from one.
        # A corpus that arrived by another path — an upgrade, a restore, a future channel — is
        # governed by the plan either way, and `tests/test_evidence_boundary.py` injects a row below
        # the floor to prove this half exists rather than assuming the first half covers it.
        if not confidence.admits(pair.confidence):
            dropped_below_floor += 1
            continue
        bucket = by_bag.setdefault(pair.bag, [])
        if len(bucket) < MAX_PAIRS_PER_BAG:
            bucket.append(pair)

    # The overall ceiling: keep the NEWEST bags whole rather than truncating every bag a little,
    # so a bag is either fully represented (up to the per-bag cap) or absent — a half-represented
    # bag would carry one unit of mass over an arbitrary fraction of its pairs.
    order = sorted(by_bag, key=lambda bag: (-by_bag[bag][0].label_at, bag))
    kept: list[tuple[str, int]] = []
    budget = MAX_TRAINING_ROWS
    for fid in order:
        if len(by_bag[fid]) <= budget:
            kept.append(fid)
            budget -= len(by_bag[fid])
    kept.sort()

    rows: list[TrainingRow] = []
    multipliers: list[float] = []
    for fid in kept:
        bucket = by_bag[fid]
        w_bag = 1.0 / len(bucket)
        y = 1.0 if bucket[0].verdict == "confirm" else 0.0
        for pair in bucket:
            # **The composition, in the order the plan registers it**: the design-effect correction
            # `1/len(bucket)`, then the confidence multiplier `m(c) = 0.6 + 0.4c`, then the class
            # balance below. Applied **at derivation** and never folded into a stored `weight` —
            # `TrainingRow.weight` already carries two meanings and a third would make all three
            # unrecoverable, which is why `situation_event.confidence` is its own column.
            factor = confidence.multiplier(pair.confidence)
            multipliers.append(factor)
            rows.append(
                TrainingRow(
                    y=y,
                    weight=w_bag * factor,
                    x=feature_vector(pair.delta_t_s, pair.class_affinity, pair.entity_affinity),
                )
            )

    positive = sum(row.weight for row in rows if row.y == 1.0)
    negative = sum(row.weight for row in rows if row.y == 0.0)
    balanced: list[TrainingRow] = []
    total = positive + negative
    for row in rows:
        mass = positive if row.y == 1.0 else negative
        scale = (total / (2.0 * mass)) if mass > 0.0 else 1.0
        balanced.append(TrainingRow(y=row.y, weight=row.weight * scale, x=row.x))

    diagnostics = {
        "policy": policy,
        "bags_used": len(kept),
        "bags_dropped_by_row_cap": len(by_bag) - len(kept),
        "rows": len(balanced),
        "positive_mass": round(positive, 6),
        "negative_mass": round(negative, 6),
        "single_class": positive == 0.0 or negative == 0.0,
        # v0.16.0: **the composition is recorded**, which is the plan's own requirement rather
        # than a convenience. A run whose confidence multipliers were all 1.0 and one whose
        # operators averaged 0.7 produce different models from the same corpus, and without these
        # three numbers the difference is invisible in the run row.
        "bags_by_source": {
            source: sum(1 for s, _ in kept if s == source)
            for source in sorted({s for s, _ in kept})
        },
        "rows_dropped_below_confidence_floor": dropped_below_floor,
        "confidence_multiplier_mean": (
            round(sum(multipliers) / len(multipliers), 6) if multipliers else 1.0
        ),
        "confidence_multiplier_min": round(min(multipliers), 6) if multipliers else 1.0,
    }
    return balanced, diagnostics


async def fit(rows: list[TrainingRow]) -> tuple[Coefficients, dict[str, Any]]:
    """Weighted logistic regression by batch gradient descent. **Deterministic, by construction.**

    Fixed row order (the caller's), a fixed iteration count, no RNG, no shuffling, and no early
    stop — an early stop on a timer would make the answer a property of the machine. The work is
    bounded instead by `MAX_TRAINING_ROWS` times `ITERATIONS`, both constants.

    `await asyncio.sleep(0)` between iterations, never inside one: the fit runs off the batch lock
    but in the same event loop, so a fit that never yielded would stall ingestion by an indirect
    route — prime directive 1 broken by accident rather than by design. A yield between iterations
    changes no arithmetic and therefore no result.

    Gradients accumulate left to right over the caller's row order, which is therefore part of the
    answer — the same reason `AdditiveScorer` fixes the order of its three terms.
    """
    started = time.monotonic()
    dimensions = len(rows[0].x) if rows else 3
    intercept = 0.0
    weights = [0.0] * dimensions
    mass = sum(row.weight for row in rows) or 1.0

    for _iteration in range(ITERATIONS):
        grad_b = 0.0
        grad_w = [0.0] * dimensions
        for row in rows:
            logit = intercept
            for index in range(dimensions):
                logit += weights[index] * row.x[index]
            error = (sigmoid(logit) - row.y) * row.weight
            grad_b += error
            for index in range(dimensions):
                grad_w[index] += error * row.x[index]
        intercept -= LEARNING_RATE * (grad_b / mass + L2 * intercept)
        for index in range(dimensions):
            weights[index] -= LEARNING_RATE * (grad_w[index] / mass + L2 * weights[index])
        await asyncio.sleep(0)

    coefficients = Coefficients.from_vector(intercept, tuple(weights))
    diagnostics = {
        "iterations": ITERATIONS,
        "learning_rate": LEARNING_RATE,
        "l2": L2,
        "rows": len(rows),
        "fit_seconds": round(time.monotonic() - started, 4),
        "log_loss": round(log_loss(coefficients, rows), 6),
    }
    return coefficients, diagnostics


def log_loss(coefficients: Coefficients, rows: list[TrainingRow]) -> float:
    """Weighted mean negative log-likelihood. Reported as a **fit diagnostic, never as quality**.

    It is computed on the training rows, so it says how well the optimiser did its job and nothing
    whatever about whether the model is any good — the pre-registration's §4 metrics are the only
    quality metrics, and they are at partition level against human verdicts.
    """
    if not rows:
        return 0.0
    total = 0.0
    mass = 0.0
    for row in rows:
        logit = coefficients.intercept
        for weight, value in zip(coefficients.weights, row.x, strict=True):
            logit += weight * value
        p = min(1.0 - 1e-12, max(1e-12, sigmoid(logit)))
        total += -row.weight * (row.y * math.log(p) + (1.0 - row.y) * math.log(1.0 - p))
        mass += row.weight
    return total / mass if mass else 0.0
