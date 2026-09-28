"""Fitting the `gam` kind: binning, cyclic gradient boosting, interactions, centring.

The algorithm is the one InterpretML's Explainable Boosting Machine uses (Lou, Caruana & Gehrke,
KDD 2012 and 2013; Nori et al., *InterpretML*, arXiv:1909.09223), written out in pure Python
because the appliance ships five runtime dependencies and a model fit is not a reason for a sixth:

1. **Bin** every feature once, at quantiles of the training rows (a discrete feature keeps one bin
   per value).
2. **Boost cyclically**: each round visits every feature in a fixed order, computes the logistic
   loss's gradient and Hessian per bin, fits a small step function (at most ``max_leaves``
   contiguous segments — the smoothing EBM gets from its three-leaf trees) and adds it, shrunk by
   ``learning_rate``, to that feature's shape. Rows are subsampled per round (Friedman, *Stochastic
   Gradient Boosting*, 2002), which both regularises and cuts the cost of each round.
3. **Stop early** on a validation set's log loss, keeping the best round.
4. Optionally **add pairwise interactions** (GA²M): rank feature pairs by the gain of a four-cell
   split of the residual (the FAST heuristic of the 2013 paper), then boost small two-dimensional
   tables for the best few.
5. **Centre** every term on the training distribution, so a term's value at a pair *is* its
   contribution relative to the average pair and the intercept is the average logit.

Deterministic: fixed visiting order, a seeded `random.Random` for subsampling, float sums in a fixed
order. Two fits from the same rows and parameters produce byte-identical documents, in one process
or two — the property `tests/test_gam.py` asserts and Part VIII's injection attacks.

**Nothing here holds a lock or reads a store.** :func:`fit` is synchronous arithmetic; the callers
that run it inside the appliance run it in a worker thread from the maintenance loop.
"""

from __future__ import annotations

import bisect
import json
import math
import random
from array import array
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from netcorenoc.engine.model import gam

__all__ = ["Dataset", "FitParams", "FitResult", "bin_edges", "fit", "log_loss"]


@dataclass
class Dataset:
    """Training rows, column-major. ``init`` is an optional starting logit per row (warm start)."""

    features: tuple[str, ...]
    columns: list[array[float]]
    y: array[int]
    w: array[float]
    init: array[float] | None = None

    def __len__(self) -> int:
        return len(self.y)

    @classmethod
    def from_rows(
        cls,
        features: Sequence[str],
        rows: Sequence[Sequence[float]],
        y: Sequence[int],
        w: Sequence[float] | None = None,
        columns: Sequence[int] | None = None,
    ) -> Dataset:
        """Build from row-major data. ``columns`` picks which positions of each row to keep."""
        picks = list(columns) if columns is not None else list(range(len(features)))
        cols = [array("d", (float(r[j]) for r in rows)) for j in picks]
        weights = array("d", (float(v) for v in w)) if w is not None else array("d", [1.0] * len(y))
        return cls(tuple(features), cols, array("b", (int(v) for v in y)), weights)

    def subset(self, index: Sequence[int]) -> Dataset:
        return Dataset(
            self.features,
            [array("d", (col[i] for i in index)) for col in self.columns],
            array("b", (self.y[i] for i in index)),
            array("d", (self.w[i] for i in index)),
            None if self.init is None else array("d", (self.init[i] for i in index)),
        )


@dataclass(frozen=True)
class FitParams:
    rounds: int = 300
    learning_rate: float = 0.08
    max_bins: int = 32
    max_leaves: int = 3
    l2: float = 1.0
    min_hessian: float = 1.0
    subsample: float = 0.5
    interactions: int = 0
    interaction_bins: int = 8
    interaction_rounds: int = 60
    patience: int = 40
    check_every: int = 5
    seed: int = 0

    def as_dict(self) -> dict[str, float]:
        return {k: float(v) for k, v in sorted(self.__dict__.items())}


@dataclass
class FitResult:
    document: str
    best_round: int
    train_trace: list[float] = field(default_factory=list)
    valid_trace: list[float] = field(default_factory=list)
    trace_every: int = 5


def _sigmoid(z: float) -> float:
    if z >= 0.0:
        return 1.0 / (1.0 + math.exp(-min(z, 700.0)))
    e = math.exp(max(z, -700.0))
    return e / (1.0 + e)


def log_loss(y: Sequence[int], p: Sequence[float], w: Sequence[float]) -> float:
    """Weighted mean negative log-likelihood, with p clipped away from 0 and 1."""
    total = 0.0
    mass = 0.0
    for yi, pi, wi in zip(y, p, w, strict=True):
        q = min(max(pi, 1e-12), 1.0 - 1e-12)
        total -= wi * (math.log(q) if yi else math.log(1.0 - q))
        mass += wi
    return total / mass if mass > 0 else 0.0


def bin_edges(values: Sequence[float], max_bins: int) -> tuple[float, ...]:
    """Cut points for one feature: one bin per distinct value when there are few, else quantiles.

    Edges are **midpoints** between adjacent distinct values, so a value seen in training never
    sits on an edge and a value between two seen ones goes to the nearer side's bin rule
    deterministically (`bisect_right`).
    """
    distinct = sorted(set(values))
    if len(distinct) <= 1:
        return ()
    if len(distinct) <= max_bins:
        return tuple((a + b) / 2.0 for a, b in zip(distinct, distinct[1:], strict=False))
    ordered = sorted(values)
    n = len(ordered)
    cuts: list[float] = []
    for k in range(1, max_bins):
        v = ordered[min(n - 1, (n * k) // max_bins)]
        at = bisect.bisect_left(distinct, v)
        if at == 0:
            continue
        edge = (distinct[at - 1] + distinct[at]) / 2.0
        if not cuts or edge > cuts[-1]:
            cuts.append(edge)
    return tuple(cuts)


def _segments(g: list[float], h: list[float], leaves: int, l2: float) -> list[tuple[int, int]]:
    """Best partition of the bins into at most ``leaves`` contiguous segments (Newton gain)."""
    n = len(g)
    pg = [0.0]
    ph = [0.0]
    for a, b in zip(g, h, strict=True):
        pg.append(pg[-1] + a)
        ph.append(ph[-1] + b)

    def gain(i: int, j: int) -> float:
        sg = pg[j] - pg[i]
        return sg * sg / (ph[j] - ph[i] + l2)

    best = [(0, n)]
    best_gain = gain(0, n)
    if leaves >= 2:
        for a in range(1, n):
            v = gain(0, a) + gain(a, n)
            if v > best_gain + 1e-12:
                best_gain, best = v, [(0, a), (a, n)]
    if leaves >= 3:
        for a in range(1, n - 1):
            left = gain(0, a)
            for b in range(a + 1, n):
                v = left + gain(a, b) + gain(b, n)
                if v > best_gain + 1e-12:
                    best_gain, best = v, [(0, a), (a, b), (b, n)]
    if leaves > 3:  # per-bin steps: every bin its own segment
        best = [(i, i + 1) for i in range(n)]
    return best


def fit(
    train: Dataset,
    valid: Dataset | None,
    params: FitParams,
    *,
    edges: dict[str, tuple[float, ...]] | None = None,
    base: gam.Model | None = None,
    threshold: float = 0.0,
    grouping: dict[str, float] | None = None,
    stop: Callable[[], bool] | None = None,
) -> FitResult:
    """Fit a GAM. ``edges`` fixes the binning (a warm start must reuse its base model's bins);
    ``base`` is a model whose shapes the fitted steps are **added to** (site adaptation), and whose
    logit is then every row's starting point. ``stop`` is polled between rounds (a cancel button).
    """
    names = train.features
    n = len(train)
    if n == 0:
        raise ValueError("cannot fit on an empty dataset")
    rng = random.Random(params.seed)
    if edges is None:
        edges = {f: bin_edges(list(col), params.max_bins) for f, col in zip(names, train.columns, strict=True)}
    binned = [array("H", (bisect.bisect_right(edges[f], v) for v in col)) for f, col in zip(names, train.columns, strict=True)]
    nbins = [len(edges[f]) + 1 for f in names]
    shapes = [[0.0] * nb for nb in nbins]
    base_shapes = _base_shapes(base, names, edges)
    mass = sum(train.w)
    if base is None:
        pos = sum(w for w, y in zip(train.w, train.y, strict=True) if y)
        prior = min(max(pos / mass, 1e-6), 1.0 - 1e-6)
        intercept = math.log(prior / (1.0 - prior))
    else:
        intercept = base.intercept
    start = array("d", [intercept] * n)
    for f, shp in enumerate(base_shapes):
        if shp is not None:
            col = binned[f]
            for i in range(n):
                start[i] += shp[col[i]]
    pred = array("d", start)
    vbinned: list[array[int]] | None = None
    if valid is not None and len(valid):
        vbinned = [array("H", (bisect.bisect_right(edges[f], v) for v in col)) for f, col in zip(names, valid.columns, strict=True)]
    result = FitResult("", 0, trace_every=params.check_every)
    best_loss = math.inf
    best_shapes = [s[:] for s in shapes]
    best_round = 0
    since_best = 0
    y, w = train.y, train.w
    order = list(range(len(names)))
    for rnd in range(1, params.rounds + 1):
        if stop is not None and stop():
            break
        rows = (
            [i for i in range(n) if rng.random() < params.subsample]
            if params.subsample < 1.0
            else list(range(n))
        )
        # Predictions are rebuilt for the sampled rows only, then kept current through the round
        # by applying each feature's step inside the next feature's gradient pass (one pass per
        # feature, not two).
        for i in rows:
            z = start[i]
            for f, shape in enumerate(shapes):
                z += shape[binned[f][i]]
            pred[i] = z
        prev_step: list[float] | None = None
        prev_col: array[int] | None = None
        for f in order:
            col = binned[f]
            g = [0.0] * nbins[f]
            h = [0.0] * nbins[f]
            for i in rows:
                if prev_step is not None and prev_col is not None:
                    pred[i] += prev_step[prev_col[i]]
                p = _sigmoid(pred[i])
                b = col[i]
                wi = w[i]
                g[b] += wi * (p - y[i])
                h[b] += wi * p * (1.0 - p)
            step = [0.0] * nbins[f]
            for lo, hi in _segments(g, h, params.max_leaves, params.l2):
                sh = sum(h[lo:hi])
                if sh < params.min_hessian:
                    continue
                delta = -params.learning_rate * sum(g[lo:hi]) / (sh + params.l2)
                for k in range(lo, hi):
                    step[k] = delta
            shape = shapes[f]
            for k, d in enumerate(step):
                shape[k] += d
            prev_step, prev_col = step, col
        if rnd % params.check_every == 0 or rnd == params.rounds:
            tl = _loss_binned(binned, shapes, [None] * len(shapes), 0.0, train, start=start)
            result.train_trace.append(tl)
            if vbinned is not None and valid is not None:
                vl = _loss_binned(vbinned, shapes, base_shapes, intercept, valid)
                result.valid_trace.append(vl)
            else:
                vl = tl
            if vl < best_loss - 1e-9:
                best_loss, best_round, since_best = vl, rnd, 0
                best_shapes = [s[:] for s in shapes]
            else:
                since_best += params.check_every
                if since_best >= params.patience:
                    break
    shapes = best_shapes
    # Recompute predictions at the kept round, for the interaction stage and for centring.
    pred = array("d", start)
    for f, shape in enumerate(shapes):
        col = binned[f]
        for i in range(n):
            pred[i] += shape[col[i]]
    tables: list[tuple[int, int, tuple[float, ...], tuple[float, ...], list[list[float]]]] = []
    if params.interactions > 0:
        tables = _interactions(train, binned, edges, pred, params, rng, names)
    merged = [
        [a + b for a, b in zip(shape, base_shapes[f], strict=True)] if base_shapes[f] is not None else shape
        for f, shape in enumerate(shapes)
    ]
    document = _document(names, edges, merged, tables, intercept, binned, train, threshold, grouping)
    result.document = document
    result.best_round = best_round
    return result


def _base_shapes(
    base: gam.Model | None, names: tuple[str, ...], edges: dict[str, tuple[float, ...]]
) -> list[list[float] | None]:
    if base is None:
        return [None] * len(names)
    by_name = {s.feature: s for s in base.shapes}
    out: list[list[float] | None] = []
    for f in names:
        shape = by_name.get(f)
        if shape is None:
            out.append(None)
            continue
        if shape.edges != edges[f]:
            raise ValueError(f"warm start needs the base model's bins for {f!r}")
        out.append(list(shape.scores))
    return out


def _loss_binned(
    binned: list[array[int]],
    shapes: list[list[float]],
    base_shapes: list[list[float] | None],
    intercept: float,
    data: Dataset,
    start: array[float] | None = None,
) -> float:
    """Log loss of the current shapes on ``data``. ``start`` replaces the intercept and base
    shapes with per-row starting logits (the training rows, where they are already computed)."""
    n = len(data)
    probs = []
    for i in range(n):
        z = start[i] if start is not None else intercept
        for f, shape in enumerate(shapes):
            b = binned[f][i]
            z += shape[b]
            if start is None:
                extra = base_shapes[f]
                if extra is not None:
                    z += extra[b]
        probs.append(_sigmoid(z))
    return log_loss(data.y, probs, data.w)


def _coarse(edges: tuple[float, ...], bins: int) -> tuple[float, ...]:
    if len(edges) + 1 <= bins:
        return edges
    step = (len(edges) + 1) / bins
    picks = sorted({edges[min(len(edges) - 1, int(round(step * k)) - 1)] for k in range(1, bins)})
    return tuple(picks)


def _interactions(
    train: Dataset,
    binned: list[array[int]],
    edges: dict[str, tuple[float, ...]],
    pred: array[float],
    params: FitParams,
    rng: random.Random,
    names: tuple[str, ...],
) -> list[tuple[int, int, tuple[float, ...], tuple[float, ...], list[list[float]]]]:
    """GA²M stage: rank pairs by FAST gain on the residual, then boost the best few tables."""
    n = len(train)
    coarse = [_coarse(edges[f], params.interaction_bins) for f in names]
    cbins = [array("H", (bisect.bisect_right(coarse[f], v) for v in col)) for f, col in enumerate(train.columns)]
    probs = [_sigmoid(z) for z in pred]
    g = [train.w[i] * (probs[i] - train.y[i]) for i in range(n)]
    h = [train.w[i] * probs[i] * (1.0 - probs[i]) for i in range(n)]
    scored: list[tuple[float, int, int]] = []
    for a in range(len(names)):
        for b in range(a + 1, len(names)):
            na, nb = len(coarse[a]) + 1, len(coarse[b]) + 1
            if na < 2 or nb < 2:
                continue
            gg = [[0.0] * nb for _ in range(na)]
            hh = [[0.0] * nb for _ in range(na)]
            ca, cb = cbins[a], cbins[b]
            for i in range(n):
                gg[ca[i]][cb[i]] += g[i]
                hh[ca[i]][cb[i]] += h[i]
            scored.append((_fast_gain(gg, hh, params.l2), a, b))
    scored.sort(key=lambda t: (-t[0], t[1], t[2]))
    chosen = [(a, b) for gain_, a, b in scored[: params.interactions] if gain_ > 0.0]
    tables = [[[0.0] * (len(coarse[b]) + 1) for _ in range(len(coarse[a]) + 1)] for a, b in chosen]
    for _ in range(params.interaction_rounds):
        rows = [i for i in range(n) if rng.random() < params.subsample] if params.subsample < 1.0 else list(range(n))
        for t, (a, b) in enumerate(chosen):
            na, nb = len(tables[t]), len(tables[t][0])
            gg = [[0.0] * nb for _ in range(na)]
            hh = [[0.0] * nb for _ in range(na)]
            ca, cb = cbins[a], cbins[b]
            for i in rows:
                p = _sigmoid(pred[i])
                wi = train.w[i]
                gg[ca[i]][cb[i]] += wi * (p - train.y[i])
                hh[ca[i]][cb[i]] += wi * p * (1.0 - p)
            step = [[0.0] * nb for _ in range(na)]
            for x in range(na):
                for yb in range(nb):
                    if hh[x][yb] >= params.min_hessian:
                        step[x][yb] = -params.learning_rate * gg[x][yb] / (hh[x][yb] + params.l2)
                        tables[t][x][yb] += step[x][yb]
            for i in range(n):
                pred[i] += step[ca[i]][cb[i]]
    return [(a, b, coarse[a], coarse[b], tables[t]) for t, (a, b) in enumerate(chosen)]


def _fast_gain(gg: list[list[float]], hh: list[list[float]], l2: float) -> float:
    """The best four-quadrant split's Newton gain over the no-split baseline (FAST)."""
    na, nb = len(gg), len(gg[0])
    tg = sum(map(sum, gg))
    th = sum(map(sum, hh))
    base = tg * tg / (th + l2)
    best = 0.0
    for ca in range(1, na):
        for cb in range(1, nb):
            quads = [[0.0, 0.0] for _ in range(4)]
            for x in range(na):
                for y in range(nb):
                    q = (x >= ca) * 2 + (y >= cb)
                    quads[q][0] += gg[x][y]
                    quads[q][1] += hh[x][y]
            v = sum(qg * qg / (qh + l2) for qg, qh in quads) - base
            best = max(best, v)
    return best


def _round(v: float) -> float:
    return float(f"{v:.9g}")


def _document(
    names: tuple[str, ...],
    edges: dict[str, tuple[float, ...]],
    shapes: list[list[float]],
    tables: list[tuple[int, int, tuple[float, ...], tuple[float, ...], list[list[float]]]],
    intercept: float,
    binned: list[array[int]],
    train: Dataset,
    threshold: float,
    grouping: dict[str, float] | None,
) -> str:
    """Centre every term on the training rows and serialise canonically."""
    mass = sum(train.w)
    centred: list[list[float]] = []
    for f, shape in enumerate(shapes):
        col = binned[f]
        mean = sum(train.w[i] * shape[col[i]] for i in range(len(train))) / mass
        intercept += mean
        centred.append([s - mean for s in shape])
    pairs: list[dict[str, Any]] = []
    for a, b, ea, eb, table in tables:
        ca = [bisect.bisect_right(ea, v) for v in train.columns[a]]
        cb = [bisect.bisect_right(eb, v) for v in train.columns[b]]
        mean = sum(train.w[i] * table[ca[i]][cb[i]] for i in range(len(train))) / mass
        intercept += mean
        pairs.append(
            {
                "features": [names[a], names[b]],
                "edges": [list(ea), list(eb)],
                "scores": [[_round(v - mean) for v in row] for row in table],
            }
        )
    doc = {
        "format": gam.FORMAT,
        "features": list(names),
        "intercept": _round(intercept),
        "threshold": threshold,
        "shapes": [
            {"feature": f, "edges": list(edges[f]), "scores": [_round(v) for v in shape]}
            for f, shape in zip(names, centred, strict=True)
        ],
        "interactions": pairs,
        "grouping": grouping or {"join_bias": 0.0, "merge_bias": 1.0, "merge_min_pairs": 3.0},
    }
    return json.dumps(doc, sort_keys=True, separators=(",", ":"))
