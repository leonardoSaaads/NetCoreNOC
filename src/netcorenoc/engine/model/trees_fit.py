"""Fitting the tree members of the league: histogram CART, a random forest, Newton-boosted trees.

v0.27.0 (ADR #424). Pure Python, because the appliance ships five runtime dependencies and a fit
is not a reason for a sixth — the same reasoning as `gam_fit`. What makes it fast enough is the
technique LightGBM made standard (Ke et al., *LightGBM: A Highly Efficient Gradient Boosting
Decision Tree*, NeurIPS 2017):

* every feature is **binned once** at training quantiles (`gam_fit.bin_edges`), so a split search
  is a pass over at most ``max_bins`` bins rather than over sorted rows;
* a node's per-bin sums are one pass over its rows, and a child's are its parent's **minus its
  sibling's** — only the smaller child is ever summed;
* a split sends ``x <= edges[k]`` left, and rows are binned with ``bisect_left``, so the training
  partition and the served rule (`trees.Tree.leaf_of`) agree even on an edge.

Two split criteria, one per kind of target:

* ``gini`` (decision tree, random forest) — the per-bin sums are positive mass and total mass, the
  gain is the drop in weighted Gini impurity, and a leaf holds the **m-estimate log-odds** of its
  rows, shrunk toward the prior by ``prior`` rows' worth of weight;
* ``newton`` (boosted trees) — the sums are the logistic loss's gradient and Hessian, the gain is
  the second-order one (Chen & Guestrin, *XGBoost*, KDD 2016, eq. 7) and a leaf is the Newton step
  ``-G/(H+λ)`` times the learning rate.

Deterministic: fixed feature order, a strict ``>`` on gain (the lowest feature, then the lowest bin,
wins a tie), and one seeded `random.Random` for every draw. Two fits from the same rows and
parameters produce byte-identical documents.
"""

from __future__ import annotations

import json
import math
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from netcorenoc.engine.model import trees
from netcorenoc.engine.model.gam_data import Dataset
from netcorenoc.engine.model.gam_fit import log_loss
from netcorenoc.engine.model.trees_grow import Binned, Grower, sigmoid, walk_binned

__all__ = ["Binned", "TreeParams", "TreeResult", "fit"]

_CLAMP = 12.0  # |leaf log-odds|: well inside the document bound, far past any useful certainty


@dataclass(frozen=True)
class TreeParams:
    method: str = "boosted_trees"
    max_depth: int = 6
    min_leaf: float = 20.0  # weight per leaf (gini) or Hessian per leaf (newton)
    max_bins: int = 32
    trees: int = 1  # forest size, or the most boosting rounds
    learning_rate: float = 0.1
    l2: float = 1.0
    subsample: float = 1.0
    colsample: float = 1.0
    prior: float = 2.0
    patience: int = 40
    check_every: int = 5
    seed: int = 0

    def as_dict(self) -> dict[str, float | str]:
        return {
            k: (v if isinstance(v, str) else float(v)) for k, v in sorted(self.__dict__.items())
        }


@dataclass
class TreeResult:
    document: str
    capacity: str  # what the trace is over: "rounds", "trees" or "depth"
    best: int
    points: list[int] = field(default_factory=list)
    train_trace: list[float] = field(default_factory=list)
    valid_trace: list[float] = field(default_factory=list)


def _truncate(grower: Grower, depth: int) -> list[list[Any]]:
    """The same tree cut at ``depth``: deeper nodes are dropped and re-indexed forward."""
    out: list[list[Any]] = []
    order: dict[int, int] = {}

    def keep(i: int) -> int:
        order[i] = len(out)
        node = grower.nodes[i]
        if node[0] == trees.LEAF or grower.depth[i] >= depth:
            out.append([trees.LEAF, 0.0, 0, 0, grower.own[i]])
        else:
            out.append(list(node))
        return order[i]

    queue = [0]
    keep(0)
    while queue:
        i = queue.pop(0)
        row = out[order[i]]
        if row[0] == trees.LEAF:
            continue
        row[2] = keep(grower.nodes[i][2])
        row[3] = keep(grower.nodes[i][3])
        queue.extend((grower.nodes[i][2], grower.nodes[i][3]))
    return out


def _gini_leaf(prior_rate: float, prior: float) -> Callable[[float, float], float]:
    def value(pos: float, mass: float) -> float:
        z = math.log((pos + prior * prior_rate) / (mass - pos + prior * (1.0 - prior_rate)))
        return max(-_CLAMP, min(_CLAMP, round(z, 9)))

    return value


def _loss(y: Sequence[int], w: Sequence[float], z: Sequence[float]) -> float:
    return log_loss(y, [sigmoid(v) for v in z], w)


def fit(
    train: Dataset,
    valid: Dataset,
    params: TreeParams,
    *,
    threshold: float = 0.0,
    grouping: dict[str, float] | None = None,
    reference: Sequence[float] | None = None,
) -> TreeResult:
    """Fit one member. Validation picks the depth, the forest size or the boosting round."""
    rng = random.Random(params.seed)  # nosec B311 - seeded statistical sampling, never a secret
    tb = Binned.of(train, params.max_bins)
    vb = Binned.of(valid, params.max_bins, tb.edges)
    n = len(train)
    mass = sum(train.w)
    rate = sum(train.w[i] for i in range(n) if train.y[i]) / mass
    base = math.log(rate / (1.0 - rate))
    ref = list(reference) if reference is not None else [_median(c) for c in train.columns]
    if params.method == "boosted_trees":
        return _boost(train, valid, tb, vb, params, rng, base, threshold, grouping, ref)
    pos = [train.w[i] * train.y[i] for i in range(n)]
    leaf = _gini_leaf(rate, params.prior)
    if params.method == "decision_tree":
        grower = Grower(tb, pos, train.w, params, "gini", rng, leaf)
        grower.grow(list(range(n)))
        points, tr, va = [], [], []
        for d in range(1, params.max_depth + 1):
            cut = _truncate(grower, d)
            kb = _kbins(cut, tb.edges)
            tr.append(_loss(train.y, train.w, [walk_binned(cut, kb, tb.bins, i) for i in range(n)]))
            va.append(
                _loss(
                    valid.y, valid.w, [walk_binned(cut, kb, vb.bins, i) for i in range(len(valid))]
                )
            )
            points.append(d)
        best = points[min(range(len(va)), key=lambda j: (va[j], j))]
        doc = _document(
            params.method,
            train.features,
            ref,
            0.0,
            1.0,
            [_truncate(grower, best)],
            threshold,
            grouping,
        )
        return TreeResult(doc, "depth", best, points, tr, va)
    # random forest
    forest: list[list[list[Any]]] = []
    zt, zv = [0.0] * n, [0.0] * len(valid)
    points, tr, va = [], [], []
    size = max(1, int(params.subsample * n))
    for t in range(params.trees):
        grower = Grower(tb, pos, train.w, params, "gini", rng, leaf)
        rows = sorted(rng.sample(range(n), size)) if size < n else list(range(n))
        nodes = grower.grow(rows)
        forest.append(nodes)
        for i in range(n):
            zt[i] += walk_binned(nodes, grower.kbin, tb.bins, i)
        for i in range(len(valid)):
            zv[i] += walk_binned(nodes, grower.kbin, vb.bins, i)
        count = t + 1
        if count in _checkpoints(params.trees, params.check_every):
            points.append(count)
            tr.append(_loss(train.y, train.w, [v / count for v in zt]))
            va.append(_loss(valid.y, valid.w, [v / count for v in zv]))
    best = points[min(range(len(va)), key=lambda j: (va[j], j))]
    doc = _document(
        params.method, train.features, ref, 0.0, 1.0 / best, forest[:best], threshold, grouping
    )
    return TreeResult(doc, "trees", best, points, tr, va)


def _boost(
    train: Dataset,
    valid: Dataset,
    tb: Binned,
    vb: Binned,
    params: TreeParams,
    rng: random.Random,
    base: float,
    threshold: float,
    grouping: dict[str, float] | None,
    ref: list[float],
) -> TreeResult:
    n, m = len(train), len(valid)
    zt, zv = [base] * n, [base] * m
    lr, l2 = params.learning_rate, params.l2
    grown: list[list[list[Any]]] = []
    points, tr, va = [], [], []
    best_loss, best_round, since = math.inf, 0, 0
    size = max(1, int(params.subsample * n))
    for r in range(1, params.trees + 1):
        g = [0.0] * n
        h = [0.0] * n
        rows = sorted(rng.sample(range(n), size)) if size < n else list(range(n))
        for i in rows:
            p = sigmoid(zt[i])
            g[i] = train.w[i] * (p - train.y[i])
            h[i] = train.w[i] * max(p * (1.0 - p), 1e-6)

        def step(gs: float, hs: float) -> float:
            return max(-_CLAMP, min(_CLAMP, round(-lr * gs / (hs + l2), 9)))

        grower = Grower(tb, g, h, params, "newton", rng, step)
        nodes = grower.grow(rows)
        grown.append(nodes)
        for i in range(n):
            zt[i] += walk_binned(nodes, grower.kbin, tb.bins, i)
        for i in range(m):
            zv[i] += walk_binned(nodes, grower.kbin, vb.bins, i)
        if r % params.check_every == 0 or r == params.trees:
            loss = _loss(valid.y, valid.w, zv)
            points.append(r)
            tr.append(_loss(train.y, train.w, zt))
            va.append(loss)
            if loss < best_loss - 1e-9:
                best_loss, best_round, since = loss, r, 0
            else:
                since += params.check_every
                if since >= params.patience:
                    break
    doc = _document(
        "boosted_trees",
        train.features,
        ref,
        round(base, 9),
        1.0,
        grown[:best_round],
        threshold,
        grouping,
    )
    return TreeResult(doc, "rounds", best_round, points, tr, va)


def _checkpoints(total: int, every: int) -> set[int]:
    marks = {1, total}
    k = 1
    while k < total:
        marks.add(k)
        k *= 2
    marks.update(range(every, total + 1, every))
    return marks


def _kbins(nodes: list[list[Any]], edges: list[tuple[float, ...]]) -> list[int]:
    """Each node's split as a bin index, recovered from its threshold (for binned walks)."""
    return [0 if node[0] == trees.LEAF else edges[node[0]].index(node[1]) for node in nodes]


def _median(col: Sequence[float]) -> float:
    ordered = sorted(col)
    return float(ordered[len(ordered) // 2]) if ordered else 0.0


def _document(
    method: str,
    features: tuple[str, ...],
    reference: Sequence[float],
    base: float,
    scale: float,
    forest: list[list[list[Any]]],
    threshold: float,
    grouping: dict[str, float] | None,
) -> str:
    doc = {
        "format": trees.FORMAT,
        "method": method,
        "features": list(features),
        "reference": [float(v) for v in reference],
        "base": base,
        "scale": scale,
        "trees": [
            [[int(a), float(b), int(c), int(d), float(e)] for a, b, c, d, e in t] for t in forest
        ],
        "threshold": threshold,
        "grouping": grouping or {"join_bias": 0.0, "merge_bias": 1.0, "merge_min_pairs": 3.0},
    }
    return json.dumps(doc, sort_keys=True, separators=(",", ":"))
