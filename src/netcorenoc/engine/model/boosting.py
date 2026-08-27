"""The `gradient_boosting` kind: additive trees on residuals. **No RNG, and not called XGBoost.**

v0.14.0. `PREREGISTRATION-0.14.0.md` §2.3 fixes G1-G4 before any fit existed.

## The name, and why it is a correctness question rather than a preference (DECISIONS #189)

XGBoost is a **specific algorithm** — second-order gradients, a regularised objective,
sparsity-aware split finding, weighted quantile sketches — and also a project name. What it fits is
first-order gradient boosting with squared loss and constant-shrinkage steps: the classic Friedman
formulation, not that one. A `model_version` row whose `kind` read `xgboost` would put a false claim
in the model registry and, through `promotion.applied`, in the **audit log** — a hash-chained,
append-only record whose whole value is that what it says happened, happened.

So the kind is `gradient_boosting`, and the surface says *gradient boosting* everywhere.

## The fit

    F₀(x) = base_score                            (the weighted mean of y)
    rₖ    = y - F(x)                              (the residual, continuous)
    Fₖ(x) = Fₖ₋₁(x) + learning_rate · treeₖ(x)    (treeₖ fitted to rₖ)

Squared loss, so the negative gradient **is** the residual and no gradient arithmetic is needed
beyond a subtraction. Each round's tree is fitted with `cart.VARIANCE`, which is the correct
criterion on a continuous target and is the reason that criterion exists at all — it is not
operator-facing and this is its only caller (`cart.py`'s docstring records why).

**Deterministic with no seed**, unlike `forest`: there is no subsampling of rows and none of
features, so every round is a pure function of the rows and of the previous round. That is worth
stating rather than assuming, because "boosting" and "stochastic gradient boosting" differ by
exactly the row subsample this module does not do.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from netcorenoc.engine.model import attribution, tree
from netcorenoc.engine.model.cart import VARIANCE, Node, extremes, fit
from netcorenoc.engine.model.training import TrainingRow

__all__ = [
    "DEFAULT_LEARNING_RATE",
    "DEFAULT_N_ROUNDS",
    "KEYS",
    "KIND",
    "MIN_ROUNDS",
    "build_scorer",
    "fit_document",
    "scorer_from_payload",
    "validate",
    "validate_payload",
]

KIND = "gradient_boosting"

# fmt: off
KEYS = ("base_score", "base_value", "learning_rate", "max_depth", "min_samples_leaf", "n_rounds",
        "threshold", "trees")
# fmt: on

MIN_ROUNDS = 1
# Twelve rounds at 0.10 shrinkage moves the accumulated output by at most 1.2 leaf-values, which is
# a full traverse of the [0, 1] range a leaf can hold. Both are mechanism-class settings and an
# operator may change either; these are the shipped defaults, not bounds.
DEFAULT_N_ROUNDS = 12
DEFAULT_LEARNING_RATE = 0.10


def _members(
    grown: Sequence[Sequence[Node]], learning_rate: float
) -> list[tuple[float, Sequence[Node]]]:
    """The ensemble as `(weight, nodes)`: every round carries the same shrinkage."""
    return [(learning_rate, nodes) for nodes in grown]


async def _grow(
    rows: Sequence[TrainingRow],
    *,
    n_rounds: int,
    learning_rate: float,
    max_depth: int,
    min_samples_leaf: int,
    base_score: float,
) -> list[tuple[Node, ...]]:
    """Every round's tree, in order. Round `k` sees the residual left by rounds `0 … k-1`."""
    running = [base_score] * len(rows)
    grown: list[tuple[Node, ...]] = []
    for _round in range(n_rounds):
        residuals = [
            TrainingRow(y=row.y - running[index], weight=row.weight, x=row.x)
            for index, row in enumerate(rows)
        ]
        nodes = await fit(
            residuals,
            max_depth=max_depth,
            min_samples_leaf=min_samples_leaf,
            criterion=VARIANCE,
        )
        grown.append(nodes)
        for index, row in enumerate(rows):
            running[index] += learning_rate * _predict(nodes, row.x)
    return grown


def _predict(nodes: Sequence[Node], x: Sequence[float]) -> float:
    from netcorenoc.engine.model.cart import predict

    return predict(nodes, x)


def base_score_of(rows: Sequence[TrainingRow]) -> float:
    """`F₀`: the weighted mean of the target, which is the constant minimising squared loss."""
    mass = sum(row.weight for row in rows)
    if mass <= 0.0:
        return 0.0
    return sum(row.weight * row.y for row in rows) / mass


def validate(
    grown: Sequence[Sequence[Node]],
    params: dict[str, Any],
    *,
    threshold: float,
    max_abs: float,
    bounds: Sequence[tuple[float, float]],
) -> None:
    """**G1-G4**, plus T1-T3 and T6 on every round's tree. Raises `tree.TreePayloadError`.

    Reachability is applied to the **accumulated** output (G3), never per round: a single round's
    tree predicts a residual, so asking whether *it* crosses the link threshold is a question about
    a quantity that is not a score.
    """
    learning_rate = params.get("learning_rate")
    if not isinstance(learning_rate, int | float) or isinstance(learning_rate, bool):
        raise tree.TreePayloadError(
            f"{KIND}: 'learning_rate' must be a number, got {learning_rate!r}"
        )
    if not 0.0 < float(learning_rate) <= 1.0:
        raise tree.TreePayloadError(
            f"{KIND}: learning_rate is {learning_rate!r}, outside (0, 1]. Zero shrinkage makes "
            "every round after the first a no-op, so the model is its base score wearing "
            f"{len(grown)} trees."
        )
    if len(grown) < MIN_ROUNDS:
        raise tree.TreePayloadError(
            f"{KIND}: n_rounds is {len(grown)}, below {MIN_ROUNDS}: there is no model here"
        )
    base_score = params.get("base_score")
    if not isinstance(base_score, int | float) or isinstance(base_score, bool):
        raise tree.TreePayloadError(
            f"{KIND}: 'base_score' must be present and a number, got {base_score!r}. It is the "
            "constant every round is measured from; a model without it is a set of residuals with "
            "nothing to add them to."
        )

    for index, nodes in enumerate(grown):
        tree.validate(
            nodes,
            threshold=threshold,
            max_abs=max_abs,
            bounds=bounds,
            label=f"{KIND} round {index}",
            check_depth=False,
        )

    low, high = extremes(_members(grown, float(learning_rate)), float(base_score), bounds)
    if high <= threshold:
        raise tree.TreePayloadError(
            f"{KIND}: the highest reachable accumulated output is {high:.6f}, at or below the "
            f"threshold {threshold!r}: nothing would ever link and every alarm would be a singleton"
        )
    if low > threshold:
        raise tree.TreePayloadError(
            f"{KIND}: the lowest reachable accumulated output is {low:.6f}, above the threshold "
            f"{threshold!r}: every candidate pair would cross it, collapsing every alarm into one "
            "situation"
        )


def build_scorer(
    grown: Sequence[Sequence[Node]],
    *,
    learning_rate: float,
    base_score: float,
    threshold: float,
    base_value: float,
    scorer_id: str,
    contract_version: str,
    document: str,
    fingerprint: str,
) -> attribution.AttributedScorer:
    """Assemble the scorer, recomputing the base value and refusing a document that disagrees."""
    explainer = attribution.build(
        [(weight, tree.leaves(nodes)) for weight, nodes in _members(grown, learning_rate)],
        base_score,
    )
    if explainer.base_value != base_value:
        raise tree.TreePayloadError(
            f"{KIND}: the document's base_value {base_value!r} is not the model's mean output over "
            f"the registered background set ({explainer.base_value!r})"
        )
    return attribution.AttributedScorer(
        explainer=explainer,
        threshold=threshold,
        scorer_id=scorer_id,
        contract_version=contract_version,
        params_document=document,
        fingerprint=fingerprint,
    )


async def fit_document(
    rows: Sequence[TrainingRow],
    *,
    n_rounds: int,
    learning_rate: float,
    max_depth: int,
    min_samples_leaf: int,
    threshold: float,
) -> dict[str, Any]:
    """Fit a boosted model and return the `params_document` payload that fully describes it."""
    base_score = base_score_of(rows)
    grown = await _grow(
        rows,
        n_rounds=n_rounds,
        learning_rate=learning_rate,
        max_depth=max_depth,
        min_samples_leaf=min_samples_leaf,
        base_score=base_score,
    )
    explainer = attribution.build(
        [(weight, tree.leaves(nodes)) for weight, nodes in _members(grown, learning_rate)],
        base_score,
    )
    return {
        "base_score": base_score,
        "base_value": explainer.base_value,
        "learning_rate": learning_rate,
        "max_depth": max_depth,
        "min_samples_leaf": min_samples_leaf,
        "n_rounds": n_rounds,
        "threshold": threshold,
        "trees": [tree.document_payload(nodes) for nodes in grown],
    }


def validate_payload(
    payload: dict[str, Any], *, bounds: Sequence[tuple[float, float]], max_abs: float
) -> None:
    """The boosted kind's whole document check: T1 on the scalars, then G1-G4 over the rounds."""
    tree.scalar_error(KIND, payload)
    validate(
        tree.trees_from_payload(payload["trees"], KIND),
        payload,
        threshold=float(payload["threshold"]),
        max_abs=max_abs,
        bounds=bounds,
    )


def scorer_from_payload(
    payload: dict[str, Any], *, contract_version: str, document: str, fingerprint: str
) -> attribution.AttributedScorer:
    """The boosted kind's half of `model_version.scorer_for`."""
    return build_scorer(
        tree.trees_from_payload(payload["trees"], KIND),
        learning_rate=float(payload["learning_rate"]),
        base_score=float(payload["base_score"]),
        threshold=float(payload["threshold"]),
        base_value=float(payload["base_value"]),
        scorer_id=KIND,
        contract_version=contract_version,
        document=document,
        fingerprint=fingerprint,
    )
