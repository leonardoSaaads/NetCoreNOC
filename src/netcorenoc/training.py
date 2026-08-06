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
import json
import math
import time
from dataclasses import dataclass, field
from typing import Any

from netcorenoc.challenger import Coefficients, feature_vector, sigmoid

__all__ = [
    "ITERATIONS",
    "L2",
    "LEARNING_RATE",
    "MAX_PAIRS_PER_BAG",
    "MAX_TRAINING_ROWS",
    "PROJECT_FLOORS",
    "CorpusStats",
    "Floors",
    "LabelledPair",
    "Sufficiency",
    "TrainingRow",
    "derive",
    "fit",
]

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
class Floors:
    """The sufficiency bar. Pre-registered §5.2, and a deployment may only make it harder."""

    split_bags: int = 50
    mixed_bags: int = 20
    operators: int = 3
    top_operator_share_pct: float = 60.0
    incidents: int = 30

    def as_dict(self) -> dict[str, float]:
        return {
            "split_bags": self.split_bags,
            "mixed_bags": self.mixed_bags,
            "operators": self.operators,
            "top_operator_share_pct": self.top_operator_share_pct,
            "incidents": self.incidents,
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))


# Ten `split` bags per free parameter. The challenger has four free parameters — three features and
# an intercept — so the events-per-variable convention gives forty. **Fifty is kept**, the number
# registered when the plan expected five parameters: `resolved = the more demanding of` runs
# monotone toward evidence, and lowering a floor because the model got simpler is the move
# DECISIONS #114 exists to forbid. `challenger.py`'s docstring records why the fourth feature could
# not be built.
PROJECT_FLOORS = Floors()


def resolve_floors(stored: str | None) -> tuple[Floors, str | None]:
    """`(resolved floors, warning)` — the project floor, hardened by a deployment policy.

    **A deployment may raise a requirement and can never lower one**, including by setting it to
    zero, to null, or by omitting it. "Harder" is resolved per threshold with its direction
    declared: every count is a minimum so the larger value wins, and the operator-concentration
    ceiling is a maximum so the *smaller* value wins.

    An unreadable value falls back to the **project floors as a whole** and returns a warning —
    never a partial reconstruction, the same discipline DECISIONS #111 applies to retention, and
    for the same reason: a policy that cannot be parsed must not become a policy that admits more
    than the shipped default would.
    """
    if stored is None:
        return PROJECT_FLOORS, None
    warning = (
        "The stored evidence-floor policy (config.evidence_floors) could not be read and was "
        "ignored; the shipped project floors are in effect. A floor may only ever be made harder."
    )
    try:
        payload = json.loads(stored)
        if not isinstance(payload, dict):
            raise ValueError("not an object")
        return (
            Floors(
                split_bags=max(PROJECT_FLOORS.split_bags, int(payload.get("split_bags", 0))),
                mixed_bags=max(PROJECT_FLOORS.mixed_bags, int(payload.get("mixed_bags", 0))),
                operators=max(PROJECT_FLOORS.operators, int(payload.get("operators", 0))),
                # A CEILING: harder means lower, so the minimum wins.
                top_operator_share_pct=min(
                    PROJECT_FLOORS.top_operator_share_pct,
                    float(payload.get("top_operator_share_pct", 100.0)),
                ),
                incidents=max(PROJECT_FLOORS.incidents, int(payload.get("incidents", 0))),
            ),
            None,
        )
    except (ValueError, TypeError):
        return PROJECT_FLOORS, warning


@dataclass(frozen=True)
class CorpusStats:
    """What the labelled corpus actually contains, in the units the floors are expressed in."""

    bags: int = 0
    confirm_bags: int = 0
    split_bags: int = 0
    mixed_bags: int = 0
    incidents: int = 0
    operators: int = 0
    top_operator_share_pct: float = 0.0
    pairs: int = 0
    oldest_label_at: float | None = None
    newest_label_at: float | None = None

    @property
    def span_days(self) -> float:
        """The labelling window. Zero when there is one label or none, and the projection says so
        rather than extrapolating from a single instant."""
        if self.oldest_label_at is None or self.newest_label_at is None:
            return 0.0
        return (self.newest_label_at - self.oldest_label_at) / DAY_S


@dataclass(frozen=True)
class Sufficiency:
    """The verdict, the floors that were missed, and how long until they would be met."""

    ok: bool
    unmet: tuple[str, ...] = ()
    projections: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(
            {"unmet": list(self.unmet), "projections": self.projections},
            sort_keys=True,
            separators=(",", ":"),
        )


def _projection(shortfall: int, observed: int, span_days: float) -> str:
    """`"about N.N months"`, or **`undefined`**.

    *"Not enough yet"* is not actionable; *"about seven months at the current rate"* is. But a rate
    needs a span, and a corpus whose labels all arrived at one instant has none — extrapolating
    from it would be a fabricated number, and this release does not print one.
    """
    if span_days <= 0.0 or observed <= 0:
        return "undefined (no measurable labelling rate yet)"
    per_day = observed / span_days
    return f"about {shortfall / per_day / MONTH_DAYS:.1f} months at the current rate"


def assess(stats: CorpusStats, floors: Floors) -> Sufficiency:
    """Evaluate the corpus against the resolved floors. **Ambiguity resolves to "insufficient".**"""
    unmet: list[str] = []
    projections: dict[str, str] = {}
    checks: tuple[tuple[str, int, int], ...] = (
        ("split_bags", stats.split_bags, floors.split_bags),
        ("mixed_bags", stats.mixed_bags, floors.mixed_bags),
        ("incidents", stats.incidents, floors.incidents),
        ("operators", stats.operators, floors.operators),
    )
    for name, observed, floor in checks:
        if observed < floor:
            unmet.append(f"{name}: {observed} < {floor}")
            projections[name] = _projection(floor - observed, observed, stats.span_days)
    if stats.operators and stats.top_operator_share_pct > floors.top_operator_share_pct:
        unmet.append(
            f"top_operator_share_pct: {stats.top_operator_share_pct:.1f} > "
            f"{floors.top_operator_share_pct:.1f}"
        )
        projections["top_operator_share_pct"] = (
            "undefined (concentration falls only when another operator labels)"
        )
    return Sufficiency(ok=not unmet, unmet=tuple(unmet), projections=projections)


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
    by_bag: dict[int, list[LabelledPair]] = {}
    for pair in sorted(pairs, key=lambda p: (p.feedback_id, p.pair_id)):
        if policy == "B" and pair.verdict != "confirm":
            continue
        bucket = by_bag.setdefault(pair.feedback_id, [])
        if len(bucket) < MAX_PAIRS_PER_BAG:
            bucket.append(pair)

    # The overall ceiling: keep the NEWEST bags whole rather than truncating every bag a little,
    # so a bag is either fully represented (up to the per-bag cap) or absent — a half-represented
    # bag would carry one unit of mass over an arbitrary fraction of its pairs.
    order = sorted(by_bag, key=lambda fid: (-by_bag[fid][0].label_at, fid))
    kept: list[int] = []
    budget = MAX_TRAINING_ROWS
    for fid in order:
        if len(by_bag[fid]) <= budget:
            kept.append(fid)
            budget -= len(by_bag[fid])
    kept.sort()

    rows: list[TrainingRow] = []
    for fid in kept:
        bucket = by_bag[fid]
        w_bag = 1.0 / len(bucket)
        y = 1.0 if bucket[0].verdict == "confirm" else 0.0
        for pair in bucket:
            rows.append(
                TrainingRow(
                    y=y,
                    weight=w_bag,
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
