"""Fitting the `knn` league member: a learned metric, condensed prototypes, ``k`` on validation.

v0.29.0 (ADR #438). Pure Python, like every other fitter. The steps, each deterministic from the
seed:

1. **The metric.** Each feature's spread is its weighted standard deviation; its **importance** is
   the correlation ratio η² of the label on the feature's quantile bins — the share of the label's
   variance the feature alone explains, which reads a binary feature, a count and a skewed time on
   one scale. A feature's weight is ``(importance / max importance) ** metric_power / spread``:
   power 0 is plain standardisation, larger powers let the informative features dominate the
   distance. A feature below ``min_importance`` of the best is dropped from the document, which is
   also what keeps the distance short.
2. **The prototypes.** Positive and negative pairs are clustered **separately** by weighted k-means
   (k-means++ seeding, Arthur & Vassilvitskii, SODA 2007; then Lloyd iterations), so a boundary
   between the classes cannot be averaged away inside one cluster. Each class gets prototypes in
   proportion to its weight, never fewer than a quarter of them.
3. **The rates.** Every training pair is assigned to its nearest prototype over both classes, and a
   prototype's rate is the smoothed linked share of the pairs it stands for — so a prototype grown
   from positives that sits among negatives says so.
4. **``k``** is chosen on the validation streams' log loss along :data:`K_CURVE`, the way a depth or
   a round count is chosen for the other members.

Overfitting is held by the same three things that hold it for the trees: the prototypes cap the
capacity (a few hundred points, not a memorised training set), ``k`` and the prior smooth every
vote, and the choices are made on streams the prototypes never saw.
"""

from __future__ import annotations

import bisect
import heapq
import json
import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import repeat

from netcorenoc.engine.model import knn
from netcorenoc.engine.model.gam_data import Dataset
from netcorenoc.engine.model.gam_fit import log_loss

__all__ = ["K_CURVE", "KnnParams", "KnnResult", "fit"]

#: The neighbour counts validation chooses among.
K_CURVE = (1, 3, 5, 9, 15, 25, 41, 64)
#: Pseudo-weight, in training-weight units (one activation is one unit), shrinking a prototype's
#: rate toward the training rate: a prototype that stands for a handful of pairs says little.
RATE_PRIOR = 1.0
#: Training pairs scored for the train side of the curve (the validation side scores every pair).
TRACE_ROWS = 20_000


@dataclass(frozen=True)
class KnnParams:
    prototypes: int = 256
    weighting: str = "uniform"
    metric_power: float = 1.0
    prior: float = 4.0
    min_importance: float = 0.02
    iterations: int = 8
    sample: int = 40_000  # rows per class the clustering reads
    bins: int = 10
    seed: int = 0

    def as_dict(self) -> dict[str, float | str]:
        return {
            k: (v if isinstance(v, str) else float(v)) for k, v in sorted(self.__dict__.items())
        }


@dataclass
class KnnResult:
    document: str
    capacity: str
    best: int
    points: list[int]
    train_trace: list[float]
    valid_trace: list[float]


def _weighted_moments(col: Sequence[float], w: Sequence[float]) -> tuple[float, float]:
    mass = sum(w)
    mean = sum(v * x for v, x in zip(w, col, strict=True)) / mass
    var = sum(v * (x - mean) ** 2 for v, x in zip(w, col, strict=True)) / mass
    return mean, math.sqrt(max(var, 0.0))


def importance(col: Sequence[float], y: Sequence[int], w: Sequence[float], bins: int) -> float:
    """η²: between-bin variance of the label over its total variance, on quantile bins."""
    ordered = sorted(col)
    edges = sorted({ordered[(len(ordered) * q) // bins] for q in range(1, bins)})
    mass = sum(w)
    mean_y = sum(v * t for v, t in zip(w, y, strict=True)) / mass
    total = sum(v * (t - mean_y) ** 2 for v, t in zip(w, y, strict=True))
    if total <= 0.0:
        return 0.0
    sums: dict[int, list[float]] = {}
    for x, t, v in zip(col, y, w, strict=True):
        cell = sums.setdefault(bisect.bisect_left(edges, x), [0.0, 0.0])
        cell[0] += v
        cell[1] += v * t
    between = sum(m * (s / m - mean_y) ** 2 for m, s in sums.values() if m > 0)
    return between / total


def _kmeans(
    rows: list[tuple[float, ...]], weights: list[float], k: int, iterations: int, rng: random.Random
) -> list[tuple[float, ...]]:
    """Weighted k-means: k-means++ seeding, then Lloyd. Deterministic for a given ``rng``."""
    if len(rows) <= k:
        return list(rows)
    first = rng.choices(range(len(rows)), weights=weights)[0]
    centres = [rows[first]]
    nearest = [math.dist(r, centres[0]) ** 2 for r in rows]
    while len(centres) < k:
        pick = rng.choices(
            range(len(rows)), weights=[w * d for w, d in zip(weights, nearest, strict=True)]
        )[0]
        centres.append(rows[pick])
        nearest = [
            min(a, math.dist(r, rows[pick]) ** 2) for a, r in zip(nearest, rows, strict=True)
        ]
    d = len(rows[0])
    for _ in range(iterations):
        sums = [[0.0] * d for _ in centres]
        mass = [0.0] * len(centres)
        for r, w in zip(rows, weights, strict=True):
            dist = list(map(math.dist, repeat(r), centres))
            c = min(range(len(dist)), key=dist.__getitem__)
            mass[c] += w
            acc = sums[c]
            for j in range(d):
                acc[j] += w * r[j]
        centres = [
            tuple(v / mass[c] for v in sums[c]) if mass[c] > 0 else centres[c]
            for c in range(len(centres))
        ]
    return centres


def _sample(index: list[int], weights: Sequence[float], size: int, rng: random.Random) -> list[int]:
    return index if len(index) <= size else sorted(rng.sample(index, size))


def _neighbours(scorer: knn.KnnScorer, q: tuple[float, ...], depth: int) -> list[tuple[float, int]]:
    dist = list(map(math.dist, repeat(q), scorer.model.points))
    near = heapq.nsmallest(depth, range(len(dist)), key=dist.__getitem__)
    return [(dist[i], i) for i in near]


def _curve(
    scorer: knn.KnnScorer, data: Dataset, rows: Sequence[int], weighting: str, prior: float
) -> list[float]:
    """Log loss at every ``k`` in :data:`K_CURVE` from ONE neighbour search per row."""
    m = scorer.model
    depth = min(max(K_CURVE), len(m.points))
    ks = [k for k in K_CURVE if k <= depth]
    preds: list[list[float]] = [[] for _ in ks]
    mix = prior * m.prior_rate
    cols = [data.columns[c] for c in _cols(data, m)]
    for i in rows:
        q = tuple((col[i] - ce) * s for col, ce, s in zip(cols, m.center, m.scale, strict=True))
        near = _neighbours(scorer, q, depth)
        for slot, k in enumerate(ks):
            if weighting == "uniform":
                weight = float(k)
                mass = sum(m.rates[j] for _d, j in near[:k])
            else:
                ws = [1.0 / (dd + 1e-6) for dd, _j in near[:k]]
                weight = sum(ws)
                mass = sum(w * m.rates[j] for w, (_d, j) in zip(ws, near[:k], strict=True))
            preds[slot].append(min(max((mass + mix) / (weight + prior), 1e-9), 1 - 1e-9))
    y = [data.y[i] for i in rows]
    w = [data.w[i] for i in rows]
    return [log_loss(y, p, w) for p in preds]


def _cols(data: Dataset, model: knn.KnnModel) -> list[int]:
    return [data.features.index(f) for f in model.features]


def _document(
    params: KnnParams,
    kept: list[str],
    center: list[float],
    scale: list[float],
    points: list[tuple[float, ...]],
    rates: list[float],
    k: int,
    prior_rate: float,
    reference: list[float],
    threshold: float,
    grouping: dict[str, float] | None,
) -> str:
    doc = {
        "format": knn.FORMAT,
        "features": kept,
        "center": [round(v, 9) for v in center],
        "scale": [round(v, 9) for v in scale],
        "points": [[round(v, 6) for v in p] for p in points],
        "rates": [round(r, 6) for r in rates],
        "k": k,
        "weighting": params.weighting,
        "prior_rate": round(prior_rate, 9),
        "prior": round(params.prior, 6),
        "reference": [round(v, 9) for v in reference],
        "threshold": threshold,
        "grouping": grouping or {"join_bias": 0.0, "merge_bias": 1.0, "merge_min_pairs": 3.0},
    }
    return json.dumps(doc, sort_keys=True, separators=(",", ":"))


def fit(
    train: Dataset,
    valid: Dataset,
    params: KnnParams,
    *,
    threshold: float = 0.0,
    grouping: dict[str, float] | None = None,
) -> KnnResult:
    """Fit one member. Validation picks ``k``."""
    rng = random.Random(params.seed)  # nosec B311 - seeded statistical sampling, never a secret
    n = len(train)
    y, w = list(train.y), list(train.w)
    mass = sum(w)
    prior_rate = sum(v for v, t in zip(w, y, strict=True) if t) / mass
    probe = _sample(list(range(n)), w, 4 * params.sample, rng)
    pw = [w[i] for i in probe]
    imp: list[float] = []
    moments: list[tuple[float, float]] = []
    for col in train.columns:
        sub = [col[i] for i in probe]
        moments.append(_weighted_moments(sub, pw))
        imp.append(importance(sub, [y[i] for i in probe], pw, params.bins))
    top = max(imp) if max(imp) > 0 else 1.0
    kept_idx = [
        j
        for j, (rel, (_mean, sd)) in enumerate(zip([v / top for v in imp], moments, strict=True))
        if sd > 0 and (params.metric_power == 0 or rel >= params.min_importance)
    ]
    if not kept_idx:
        kept_idx = [max(range(len(imp)), key=imp.__getitem__)]
    center = [moments[j][0] for j in kept_idx]
    scale = [max((imp[j] / top) ** params.metric_power, 1e-6) / moments[j][1] for j in kept_idx]

    def standard(i: int) -> tuple[float, ...]:
        return tuple(
            (train.columns[j][i] - c) * s for j, c, s in zip(kept_idx, center, scale, strict=True)
        )

    positives = [i for i in range(n) if y[i]]
    negatives = [i for i in range(n) if not y[i]]
    share = sum(w[i] for i in positives) / mass
    n_pos = min(
        max(round(params.prototypes * share), params.prototypes // 4), 3 * params.prototypes // 4
    )
    protos: list[tuple[float, ...]] = []
    for group, count in ((positives, n_pos), (negatives, params.prototypes - n_pos)):
        if not group or count <= 0:
            continue
        rows = _sample(group, w, params.sample, rng)
        protos.extend(
            _kmeans(
                [standard(i) for i in rows], [w[i] for i in rows], count, params.iterations, rng
            )
        )
    pos_mass = [0.0] * len(protos)
    all_mass = [0.0] * len(protos)
    for i in range(n):
        dist = list(map(math.dist, repeat(standard(i)), protos))
        c = min(range(len(dist)), key=dist.__getitem__)
        all_mass[c] += w[i]
        pos_mass[c] += w[i] * y[i]
    keep = [c for c in range(len(protos)) if all_mass[c] > 0]
    protos = [protos[c] for c in keep]
    rates = [(pos_mass[c] + RATE_PRIOR * prior_rate) / (all_mass[c] + RATE_PRIOR) for c in keep]
    reference = [sorted(train.columns[j])[n // 2] for j in kept_idx]
    kept = [train.features[j] for j in kept_idx]
    # Pick k on validation: one neighbour search per row serves the whole curve.
    k_max = min(max(K_CURVE), len(protos))
    draft = _document(
        params, kept, center, scale, protos, rates, 1, prior_rate, reference, 0.0, None
    )
    probe_model = knn.KnnModel(
        tuple(kept),
        tuple(range(len(kept))),
        tuple(center),
        tuple(scale),
        tuple(protos),
        tuple(rates),
        1,
        params.weighting,
        prior_rate,
        params.prior,
        tuple(reference),
        0.0,
        {"join_bias": 0.0, "merge_bias": 1.0, "merge_min_pairs": 3.0},
    )
    scorer = knn.KnnScorer(probe_model, draft, "knn-fit")
    trace_rows = _sample(list(range(n)), w, TRACE_ROWS, rng)
    points = [k for k in K_CURVE if k <= k_max]
    tr = _curve(scorer, train, trace_rows, params.weighting, params.prior)
    va = _curve(scorer, valid, list(range(len(valid))), params.weighting, params.prior)
    best = points[min(range(len(va)), key=lambda j: (va[j], j))]
    doc = _document(
        params, kept, center, scale, protos, rates, best, prior_rate, reference, threshold, grouping
    )
    return KnnResult(doc, "neighbours", best, points, tr, va)
