"""Greedy CART over the three link features — **the fit the whole tree family shares.**

v0.14.0. `ROADMAP-0.8-TO-0.13.md` §v0.11.0 said a gradient-boosted model *"can only enter through
the ONNX door"*; DECISIONS #183 records why that does not follow from its own premise. This module
is the short version of the argument: **a CART over three continuous features is arithmetic.** A
split search is a sort, three prefix sums and a comparison, and there is nothing here a dependency
would do better at this size.

## What is fixed, and why each thing is fixed

* **The tie-break.** Features are searched in ascending index order and thresholds in ascending
  value order, and a candidate replaces the incumbent only on a **strictly** greater improvement. So
  the winner among equals is the lowest feature index and then the lowest threshold — the rule the
  build prompt's V.1 table states, true here because of those two orderings plus one `>`. A `>=` on
  that line would make the answer depend on iteration order, which is the non-determinism prime
  directive 6 forbids.
* **No RNG.** Not "a seeded RNG": none. Bagging draws its rows in `forest.py`, which is the only
  kind with a seed and is an ADR rather than a footnote (DECISIONS #188). Everything here is a pure
  function of the rows it is handed, in the order it is handed them.
* **Bounded work.** `MAX_SPLIT_CANDIDATES` caps the thresholds tried per feature per node by taking
  an evenly-spaced subset of the admissible positions. Bounded by construction rather than by a
  timer, for `training.fit`'s reason: an early stop on a clock makes the answer a property of the
  machine, and determinism outranks a deadline.
* **It yields.** `fit` is `async` and sleeps zero between **depth levels**, never inside the split
  search, so a multi-second fit cannot stall ingestion and no yield changes an arithmetic result.
  Same contract as `training.fit`, same reason (DECISIONS #118).

## The three criteria, and why the operator is offered only two of them

`gini` and `entropy` order splits differently on the same binary target, so choosing between them is
a real choice. A third obvious candidate — variance reduction on a 0/1 target — is **not**: the
variance of a Bernoulli is `p(1-p)` and the Gini impurity is `2p(1-p)`, so the two differ by a
constant factor and rank every candidate split identically. Offering it as a classification
criterion would be offering a control that changes nothing, which is worse than offering two.

`variance` exists here all the same, and is not operator-facing: `gradient_boosting` fits its rounds
to **residuals**, which are continuous, and on a continuous target variance reduction is the correct
criterion rather than a redundant one. `boosting.py` passes it; nothing else may.

## Why a leaf holds a weighted mean

Every kind in this family produces a **score**, and the link decision is `score > threshold`. A leaf
holding the weighted mean of its rows' targets gives a tree an output that reads as *"this much of
the evidence at this leaf said link"*, so one `threshold` means the same thing for a tree, a forest
and the accumulated output of a boosted model. A leaf holding a hard class would make the threshold
meaningless and every reachability rule vacuous.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Sequence
from typing import NamedTuple

from netcorenoc.attribution import FEATURE_COUNT, AttributionError
from netcorenoc.training import TrainingRow

__all__ = [
    "CRITERIA",
    "DEFAULT_CRITERION",
    "DEFAULT_MAX_DEPTH",
    "DEFAULT_MIN_SAMPLES_LEAF",
    "LEAF",
    "MAX_REACHABILITY_CELLS",
    "MAX_SPLIT_CANDIDATES",
    "VARIANCE",
    "Node",
    "extremes",
    "fit",
    "predict",
]

# `Node.feature` for a leaf. `-1` rather than `None` so a node is five numbers and serialises into
# `params_document` without a special case.
LEAF = -1

CRITERIA = ("gini", "entropy")
DEFAULT_CRITERION = "gini"
# Not operator-facing. `boosting.py` is the only caller; see the module docstring.
VARIANCE = "variance"

# Depth 4 is at most 15 interior nodes, which is what keeps the attribution table small enough to
# precompute exactly (`attribution.MAX_CELLS_PER_TREE`). Twenty rows per leaf, on a corpus whose
# registered floor is fifty bags, is the events-per-variable convention this project already applies
# to the logistic fit, applied where the "variable" is a leaf.
DEFAULT_MAX_DEPTH = 4
DEFAULT_MIN_SAMPLES_LEAF = 20

# Thresholds tried per feature per node. Candidates are the midpoints between consecutive distinct
# values; beyond this many, an evenly-spaced subset is taken — by integer stride, never by sampling.
MAX_SPLIT_CANDIDATES = 64

_INF = math.inf

# The reachability arithmetic of T5/F3/G3 enumerates the cells the ensemble's thresholds cut and
# evaluates one representative point in each. Exact, because the ensemble is constant on a cell.
# The cap is the same kind of statement `attribution.MAX_CELLS_PER_TREE` makes — *this build does
# not understand that payload* — and not a degeneracy rule, which the plan does not authorise this
# module to invent.
MAX_REACHABILITY_CELLS = 200_000


class Node(NamedTuple):
    """One node: an interior split, or a leaf holding a value.

    `feature == LEAF` is the discriminator, and `left`/`right` are indices into the same node list.
    Immutable, like every other artefact in this project that a fingerprint is taken over.
    """

    feature: int
    threshold: float
    left: int
    right: int
    value: float


def predict(nodes: Sequence[Node], x: Sequence[float]) -> float:
    """Walk one tree. `x[f] <= threshold` goes **left**, which is the whole of the split rule.

    Used by the fit and by the reachability arithmetic. It is deliberately **not** what `score()`
    calls: `attribution.build` precomputes the same function per cell, and the scorer reads that, so
    the decision and the explanation come from one place and cannot drift apart.
    """
    index = 0
    while True:
        node = nodes[index]
        if node.feature == LEAF:
            return node.value
        index = node.left if x[node.feature] <= node.threshold else node.right


def _impurity(mass: float, total: float, squared: float, criterion: str) -> float:
    """Impurity from the three weighted sums: `Σw`, `Σw·y`, `Σw·y²`.

    One expression per criterion over one set of accumulators, so the split search does not need to
    know which criterion it is running — and so adding a fourth would be one branch here rather than
    a second search.
    """
    if mass <= 0.0:
        return 0.0
    mean = total / mass
    if criterion == VARIANCE:
        return max(0.0, squared / mass - mean * mean)
    if mean <= 0.0 or mean >= 1.0:
        return 0.0
    if criterion == "entropy":
        return -(mean * math.log2(mean) + (1.0 - mean) * math.log2(1.0 - mean))
    return 2.0 * mean * (1.0 - mean)


def _positions(values: Sequence[float]) -> list[int]:
    """Indices **after** which a split may be placed, bounded and deterministic.

    A split is admissible only between two *different* values: placing one inside a run of equal
    values would put identical rows on both sides of a threshold, which no tree can honour. Beyond
    `MAX_SPLIT_CANDIDATES` the admissible positions are strided by an integer, so the subset is a
    property of the data rather than of a draw.
    """
    admissible = [i for i in range(len(values) - 1) if values[i] < values[i + 1]]
    if len(admissible) <= MAX_SPLIT_CANDIDATES:
        return admissible
    return admissible[:: len(admissible) // MAX_SPLIT_CANDIDATES + 1]


def _best_split(
    rows: Sequence[TrainingRow],
    subset: Sequence[int],
    *,
    min_samples_leaf: int,
    criterion: str,
    features: Sequence[int],
) -> tuple[int, float, list[int], list[int]] | None:
    """The best `(feature, threshold, left rows, right rows)`, or `None` if none is admissible.

    **The tie-break lives in the two loops and the one `>`.** `features` is ascending, the sort
    inside is ascending, and `gain > best_gain` is strict, so equal gains keep the first candidate
    seen: the lowest feature index, then the lowest threshold.
    """
    mass = sum(rows[i].weight for i in subset)
    total = sum(rows[i].weight * rows[i].y for i in subset)
    squared = sum(rows[i].weight * rows[i].y * rows[i].y for i in subset)
    parent = _impurity(mass, total, squared, criterion) * mass
    best_gain = 0.0
    best: tuple[int, float, list[int], list[int]] | None = None

    for feature in features:
        order = sorted(subset, key=lambda i: (rows[i].x[feature], i))
        values = [rows[i].x[feature] for i in order]
        left_mass = left_total = left_squared = 0.0
        cut = 0
        for position in _positions(values):
            while cut <= position:
                row = rows[order[cut]]
                left_mass += row.weight
                left_total += row.weight * row.y
                left_squared += row.weight * row.y * row.y
                cut += 1
            if cut < min_samples_leaf or len(order) - cut < min_samples_leaf:
                continue
            gain = parent - (
                _impurity(left_mass, left_total, left_squared, criterion) * left_mass
                + _impurity(mass - left_mass, total - left_total, squared - left_squared, criterion)
                * (mass - left_mass)
            )
            if gain > best_gain:
                best_gain = gain
                best = (
                    feature,
                    (values[position] + values[position + 1]) / 2.0,
                    order[: position + 1],
                    order[position + 1 :],
                )
    return best


def _leaf_of(rows: Sequence[TrainingRow], subset: Sequence[int]) -> Node:
    """A leaf holding the weighted mean of its rows' targets."""
    mass = sum(rows[i].weight for i in subset)
    if mass <= 0.0:
        return Node(LEAF, 0.0, LEAF, LEAF, 0.0)
    return Node(LEAF, 0.0, LEAF, LEAF, sum(rows[i].weight * rows[i].y for i in subset) / mass)


async def fit(
    rows: Sequence[TrainingRow],
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    min_samples_leaf: int = DEFAULT_MIN_SAMPLES_LEAF,
    criterion: str = DEFAULT_CRITERION,
    subset: Sequence[int] | None = None,
    features: Sequence[int] | None = None,
) -> tuple[Node, ...]:
    """Grow one tree, breadth-first. **Deterministic in the rows and in their order.**

    Breadth-first rather than depth-first for two reasons, neither of them style: it gives a natural
    yield point once per depth level, and it assigns node indices in a canonical order — so two fits
    on the same rows produce the same **document**, not merely the same function. The second is what
    `params_hash` fingerprints, and "the same function under a different node numbering" would give
    two hashes to one model.

    `subset` selects rows (a forest's bag); `features` restricts the split search (`mtry`). Both
    default to everything, which is the single tree's case.
    """
    order = list(range(len(rows))) if subset is None else list(subset)
    columns = list(range(FEATURE_COUNT)) if features is None else list(features)
    nodes: list[Node] = [_leaf_of(rows, order)]
    level: list[tuple[int, list[int], int]] = [(0, order, 0)]

    while level:
        pending: list[tuple[int, list[int], int]] = []
        for index, members, depth in level:
            if depth >= max_depth or len(members) < 2 * min_samples_leaf:
                continue
            split = _best_split(
                rows,
                members,
                min_samples_leaf=min_samples_leaf,
                criterion=criterion,
                features=columns,
            )
            if split is None:
                continue
            feature, threshold, left_rows, right_rows = split
            left_index = len(nodes)
            nodes.append(_leaf_of(rows, left_rows))
            right_index = len(nodes)
            nodes.append(_leaf_of(rows, right_rows))
            nodes[index] = Node(feature, threshold, left_index, right_index, 0.0)
            pending.append((left_index, left_rows, depth + 1))
            pending.append((right_index, right_rows, depth + 1))
        level = pending
        # Between levels, never inside the split search: a yield here changes no arithmetic, and the
        # fit runs in the maintenance pass off the batch lock (prime directive 9).
        await asyncio.sleep(0)
    return tuple(nodes)


def _representatives(cuts: Sequence[float], low: float, high: float) -> list[float]:
    """One point inside each reachable cell of `(lo, hi]` cut by `cuts`, clipped to `[low, high]`.

    The **upper** end of each cell is taken because the cells are right-closed, so the point is
    always a member of its own cell. A cell whose intersection with the declared bounds is empty
    contributes nothing, which is what makes the enumeration *reachable* cells rather than all of
    them — T5 asks about leaves a real pair could land in.
    """
    points: list[float] = []
    previous = -_INF
    for cut in [*cuts, _INF]:
        point = min(cut, high)
        if point > previous and point >= low:
            points.append(point)
        previous = cut
    return points


def extremes(
    members: Sequence[tuple[float, Sequence[Node]]],
    base_score: float,
    bounds: Sequence[tuple[float, float]],
) -> tuple[float, float]:
    """`(minimum, maximum)` of `base_score + Σ wᵢ·treeᵢ(x)` over the declared feature bounds.

    **Exact, not a bound.** A weighted sum of trees is constant on each cell of the grid its
    thresholds cut, so evaluating one representative point per reachable cell visits every value the
    ensemble can take. Composing per-tree minima and maxima would be cheaper and *wrong*: those
    extremes are attained at different points, so their sum is an interval the ensemble may never
    reach — and a reachability rule that admitted a model on a value it cannot produce would be
    exactly the vacuous guard §2.4 warns about.
    """
    columns: list[list[float]] = []
    for feature in range(FEATURE_COUNT):
        cuts = sorted(
            {
                node.threshold
                for _weight, nodes in members
                for node in nodes
                if node.feature == feature
            }
        )
        columns.append(_representatives(cuts, bounds[feature][0], bounds[feature][1]))
    if columns[0] and columns[1] and columns[2]:
        cells = len(columns[0]) * len(columns[1]) * len(columns[2])
        if cells > MAX_REACHABILITY_CELLS:
            raise AttributionError(
                f"the reachability arithmetic would need {cells} cells, beyond the "
                f"{MAX_REACHABILITY_CELLS} this build enumerates; the payload describes a model "
                "whose threshold reachability this build cannot decide, and ambiguity about "
                "whether a model can discriminate resolves to 'it cannot'"
            )
    lowest = _INF
    highest = -_INF
    for first in columns[0]:
        for second in columns[1]:
            for third in columns[2]:
                point = (first, second, third)
                total = base_score + sum(
                    weight * predict(nodes, point) for weight, nodes in members
                )
                lowest = min(lowest, total)
                highest = max(highest, total)
    return lowest, highest
