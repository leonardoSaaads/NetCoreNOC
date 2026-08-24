"""The `tree` kind: one CART, its document, and the six degeneracy rules the plan registered.

v0.14.0. `PREREGISTRATION-0.14.0.md` §2.1 fixes **T1-T6 before any fit existed** that they could
have been chosen to suit, and §2.4 is equally load-bearing: no rule here constrains accuracy, fit
quality, or agreement with the champion. Degeneracy is about whether a model can decide **at all**;
whether it decides *well* is the judge's question, and a validator that pre-empted it would be a
promotion gate wearing a type check's clothes.

## What this module owns, and what it lends the other two kinds

It owns the `tree` kind end to end — fit, document, rules, scorer. It also owns the three pieces a
forest and a boosted model need and must not reimplement:

* :func:`leaves` — a tree's leaves with the **box** of feature values that reach each one, which is
  the only input `attribution.build` takes;
* :func:`extremes` — the exact minimum and maximum of a *weighted sum of trees* over the declared
  feature bounds, which is what T5, F3 and G3 are each an application of;
* :func:`structure_error` — T2 and T3, which every member tree of every ensemble must satisfy.

**The feature bounds are parameters, not imports.** `model_version` holds `FEATURE_BOUNDS` and
`MAX_ABS_COEFFICIENT` and passes them in, so this module cannot import it and the pair cannot cycle
— and, more usefully, there is exactly one place each bound is written down.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

from netcorenoc import attribution
from netcorenoc.cart import CRITERIA, LEAF, Node, extremes, fit
from netcorenoc.training import TrainingRow

__all__ = [
    "KEYS",
    "KIND",
    "build_scorer",
    "document_payload",
    "fit_document",
    "leaves",
    "nodes_from_payload",
    "scalar_error",
    "scorer_from_payload",
    "structure_error",
    "trees_from_payload",
    "validate",
    "validate_payload",
]

KIND = "tree"

# The `tree` kind's document keys. Each kind module publishes its own, and `model_version` checks
# them — one key check for all five kinds, each kind's key set beside the kind.
KEYS = ("base_value", "criterion", "max_depth", "min_samples_leaf", "nodes", "threshold")

# The tree family's non-node fields, split by what "well-formed" means for each. Shared by all three
# kinds: a key absent from a given kind's `KEYS` simply never appears in its payload.
_SCALARS = ("base_score", "base_value", "learning_rate", "seed", "threshold")
_COUNTS = ("max_depth", "min_samples_leaf", "mtry", "n_estimators", "n_rounds")
_INF = math.inf
_WHOLE_LINE = ((-_INF, _INF), (-_INF, _INF), (-_INF, _INF))


def leaves(nodes: Sequence[Node]) -> list[attribution.Leaf]:
    """Every leaf as `(value, box)`, in **ascending node-index order**.

    The box is `(lo, hi]` per feature, which matches the split rule exactly: a node sends
    `x[f] <= threshold` left, so taking the left branch tightens `hi` and taking the right branch
    tightens `lo`. Ascending index order rather than traversal order so that two fits producing the
    same node list also produce the same leaf list — the difference between "the same function" and
    "the same document", and the second is what `params_hash` fingerprints.
    """
    boxes: dict[int, tuple[tuple[float, float], ...]] = {0: _WHOLE_LINE}
    frontier = [0]
    while frontier:
        index = frontier.pop()
        node = nodes[index]
        if node.feature == LEAF:
            continue
        box = boxes[index]
        low, high = box[node.feature]
        boxes[node.left] = _replace(box, node.feature, (low, min(high, node.threshold)))
        boxes[node.right] = _replace(box, node.feature, (max(low, node.threshold), high))
        frontier.append(node.left)
        frontier.append(node.right)
    return [(nodes[i].value, boxes[i]) for i in sorted(boxes) if nodes[i].feature == LEAF]


def _replace(
    box: tuple[tuple[float, float], ...], feature: int, span: tuple[float, float]
) -> tuple[tuple[float, float], ...]:
    return tuple(span if index == feature else pair for index, pair in enumerate(box))


def structure_error(nodes: Sequence[Node]) -> str | None:
    """**T2 and T3.** The reason this node list is not a well-formed tree, or `None`.

    One traversal answers both halves of T2 — *exactly one root, every child index in range, no
    cycle, every leaf reachable* — because a list that fails any of them fails to visit each node
    exactly once from index 0. Reaching a node twice is a cycle or a shared subtree, and neither is
    a tree; failing to reach one is an unreachable node, which is a document describing something
    the traversal cannot serve.
    """
    if not nodes:
        return "the node list is empty: there is no tree here"
    seen: set[int] = set()
    frontier = [0]
    while frontier:
        index = frontier.pop()
        if index in seen:
            return f"node {index} is reachable more than once: this node list is not a tree"
        seen.add(index)
        node = nodes[index]
        if node.feature == LEAF:
            if node.left != LEAF or node.right != LEAF:
                return f"leaf node {index} names children {node.left} and {node.right}"
            continue
        if not 0 <= node.feature < attribution.FEATURE_COUNT:
            return (
                f"node {index} splits on feature {node.feature}, outside "
                f"[0, {attribution.FEATURE_COUNT}) — the document was written against a different "
                "feature set"
            )
        for child in (node.left, node.right):
            if not 0 <= child < len(nodes):
                return f"node {index} names child {child}, outside the node list"
        frontier.append(node.left)
        frontier.append(node.right)
    if len(seen) != len(nodes):
        unreachable = sorted(set(range(len(nodes))) - seen)
        return f"node(s) {unreachable} are unreachable from the root"
    return None


def nodes_from_payload(payload: Any, label: str = KIND) -> tuple[Node, ...]:
    """**T1, for the node list.** Parse `[[feature, threshold, left, right, value], …]`.

    Finiteness is applied here rather than in a later rule because a non-finite threshold is not a
    degenerate *model*, it is a document that does not describe one — `model_version._parsed`'s
    argument, applied where the numbers are nested. `json.loads` accepts `NaN` and `Infinity` by
    default, which is exactly how a diverged fit arrives.
    """
    if not isinstance(payload, list):
        return _reject(f"{label}: 'nodes' must be a list, got {type(payload).__name__}")
    out: list[Node] = []
    for position, row in enumerate(payload):
        if not isinstance(row, list) or len(row) != 5:
            return _reject(f"{label}: node {position} must be a list of five numbers")
        for column, value in enumerate(row):
            if isinstance(value, bool) or not isinstance(value, int | float):
                return _reject(
                    f"{label}: node {position} field {column} must be a number, "
                    f"got {type(value).__name__}"
                )
            if not math.isfinite(float(value)):
                return _reject(f"{label}: node {position} field {column} is {value!r}, not finite")
        out.append(Node(int(row[0]), float(row[1]), int(row[2]), int(row[3]), float(row[4])))
    return tuple(out)


class TreePayloadError(ValueError):
    """A node list that does not describe a tree. Translated to `ModelPayloadError` upstream."""


def _reject(reason: str) -> tuple[Node, ...]:
    raise TreePayloadError(reason)


def trees_from_payload(payload: Any, label: str) -> list[tuple[Node, ...]]:
    """Parse an ensemble's `"trees"` list, applying T1 to every number in every member."""
    if not isinstance(payload, list):
        raise TreePayloadError(f"{label}: 'trees' must be a list, got {type(payload).__name__}")
    return [
        nodes_from_payload(member, label=f"{label} tree {index}")
        for index, member in enumerate(payload)
    ]


def document_payload(nodes: Sequence[Node]) -> list[list[float]]:
    """The node list as plain numbers, for `params_document`."""
    return [
        [float(node.feature), node.threshold, float(node.left), float(node.right), node.value]
        for node in nodes
    ]


def validate(
    nodes: Sequence[Node],
    *,
    threshold: float,
    max_abs: float,
    bounds: Sequence[tuple[float, float]],
    label: str = KIND,
    check_depth: bool = True,
) -> None:
    """**T2, T3, T4 and T6** — and T5 when `check_depth` is set. Raises :class:`TreePayloadError`.

    `check_depth` is `False` for a *member* of an ensemble: F1-F4 and G1-G4 apply T1-T3 and T6 to
    each member and reachability to the **aggregate**, because a forest whose individual trees each
    sat on one side of the threshold can still discriminate once averaged, and a single-leaf member
    of a boosted model is an ordinary round that learned nothing. Applying the single-tree rules to
    a member would refuse models the plan does not refuse.
    """
    problem = structure_error(nodes)
    if problem is not None:
        raise TreePayloadError(f"{label}: {problem}")

    for position, node in enumerate(nodes):
        if node.feature == LEAF and abs(node.value) > max_abs:
            raise TreePayloadError(
                f"{label}: leaf {position} is {node.value!r}, beyond the magnitude bound "
                f"{max_abs}: a value this large saturates the decision on its own, which is a hard "
                "switch whose contribution still sums correctly while no longer meaning 'this much "
                "evidence' (DECISIONS #164)"
            )

    if check_depth:
        if len(nodes) == 1:
            raise TreePayloadError(
                f"{label}: a tree of depth 0 is a single leaf, so it returns the same score for "
                "every pair — the all-zero logistic in another shape, and grouping would stop "
                "silently"
            )
        low, high = extremes([(1.0, nodes)], 0.0, bounds)
        if high <= threshold:
            raise TreePayloadError(
                f"{label}: the highest reachable output is {high:.6f}, at or below the threshold "
                f"{threshold!r}: no pair could ever cross it, so nothing would ever link and every "
                "alarm would be a singleton"
            )
        if low > threshold:
            raise TreePayloadError(
                f"{label}: the lowest reachable output is {low:.6f}, above the threshold "
                f"{threshold!r}: every candidate pair would cross it, collapsing every alarm into "
                "one situation"
            )


def build_scorer(
    nodes: Sequence[Node],
    *,
    threshold: float,
    base_value: float,
    scorer_id: str,
    contract_version: str,
    document: str,
    fingerprint: str,
) -> attribution.AttributedScorer:
    """Assemble the scorer for one tree, checking the document's base value against the rebuilt one.

    **The base value is recomputed and compared rather than trusted.** It is a property of (model,
    background set) and it enters `params_hash`, so a document whose base value does not match the
    model it describes is a document whose fingerprint identifies something that does not exist. A
    loader that took the stored number on faith would let a model be registered under a hash that
    says it explains its decisions one way while it explains them another.
    """
    explainer = attribution.build([(1.0, leaves(nodes))])
    if explainer.base_value != base_value:
        raise TreePayloadError(
            f"the document's base_value {base_value!r} is not the model's mean output over the "
            f"registered background set ({explainer.base_value!r}). The base value enters "
            "params_hash, so a mismatch means the fingerprint names a model that does not exist."
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
    max_depth: int,
    min_samples_leaf: int,
    criterion: str,
    threshold: float,
) -> dict[str, Any]:
    """Fit one tree and return the `params_document` payload that fully describes it.

    Every hyperparameter that changes the trained model is in the returned mapping, and therefore in
    `params_hash` — `UI-0.13-DRAFT.md` §8's constraint, which exists because two models with the
    same hash and different hyperparameters would make v0.11.0's provenance fiction.
    """
    nodes = await fit(
        rows, max_depth=max_depth, min_samples_leaf=min_samples_leaf, criterion=criterion
    )
    explainer = attribution.build([(1.0, leaves(nodes))])
    return {
        "base_value": explainer.base_value,
        "criterion": criterion,
        "max_depth": max_depth,
        "min_samples_leaf": min_samples_leaf,
        "nodes": document_payload(nodes),
        "threshold": threshold,
    }


def scalar_error(label: str, payload: dict[str, Any]) -> None:
    """**T1 for everything in a tree-family document that is not a node.**

    The node lists carry their own finiteness check inside :func:`nodes_from_payload`, because a
    nested list needs a different walk. This covers the scalars and the counts, and it is shared by
    all three kinds so that "what a well-formed hyperparameter looks like" is written once.
    """
    for key in _SCALARS:
        if key not in payload:
            continue
        value = payload[key]
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise TreePayloadError(f"{label}: {key!r} must be a number, got {type(value).__name__}")
        if not math.isfinite(float(value)):
            raise TreePayloadError(f"{label}: {key!r} must be a finite number, got {value!r}")
    for key in _COUNTS:
        if key not in payload:
            continue
        value = payload[key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise TreePayloadError(
                f"{label}: {key!r} must be a non-negative integer, got {value!r}"
            )


def validate_payload(
    payload: dict[str, Any], *, bounds: Sequence[tuple[float, float]], max_abs: float
) -> None:
    """The `tree` kind's whole document check: T1 on the scalars, the criterion, then T2-T6."""
    scalar_error(KIND, payload)
    if payload.get("criterion") not in CRITERIA:
        raise TreePayloadError(
            f"{KIND}: 'criterion' must be one of {list(CRITERIA)}, got {payload.get('criterion')!r}"
        )
    validate(
        nodes_from_payload(payload["nodes"]),
        threshold=float(payload["threshold"]),
        max_abs=max_abs,
        bounds=bounds,
    )


def scorer_from_payload(
    payload: dict[str, Any], *, contract_version: str, document: str, fingerprint: str
) -> attribution.AttributedScorer:
    """The `tree` kind's half of `model_version.scorer_for`."""
    return build_scorer(
        nodes_from_payload(payload["nodes"]),
        threshold=float(payload["threshold"]),
        base_value=float(payload["base_value"]),
        scorer_id=KIND,
        contract_version=contract_version,
        document=document,
        fingerprint=fingerprint,
    )
