"""The metrics that fit a probabilistic classifier, each with its sample size and baseline.

Part V of the v0.26.0 brief, and each item is a way of getting a number that is not real:

* **Under extreme class imbalance ROC-AUC looks excellent and means little** (Saito & Rehmsmeier,
  *The Precision-Recall Plot Is More Informative than the ROC Plot When Evaluating Binary
  Classifiers on Imbalanced Datasets*, PLoS ONE 10(3), 2015). The headline is therefore the area
  under the precision-recall curve (average precision), **always printed beside its baseline** —
  the positive rate, which is what a model that knows nothing scores.
* **A classifier's "error dispersion around zero" is its calibration**, not MAE or RMSE, which are
  regression metrics. So: the reliability curve (does 0.8 mean 80 %?) and the Brier score with
  Murphy's decomposition (*A New Vector Partition of the Probability Score*, J. Applied
  Meteorology 12, 1973) into reliability (lower is better), resolution (higher is better) and
  uncertainty (the data's own, fixed). ``reliability`` is the calibration error the maintainer's
  phrase was reaching for.
* **Every number has a sample size**, and every headline an interval from a **cluster bootstrap**:
  resampling whole clusters (streams, incidents) rather than pairs, because pairs from one incident
  are not independent and a pair bootstrap would print an interval far narrower than the truth.

Pure arithmetic; no store, no clock. Used offline by the training pipeline and online by the Judge
screen for a site model's numbers, so the two are the same code.
"""

from __future__ import annotations

import itertools
import math
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass

__all__ = [
    "Calibration",
    "Confusion",
    "Interval",
    "average_precision",
    "brier",
    "calibration",
    "cluster_bootstrap",
    "confusion",
    "log_loss",
    "pr_curve",
    "roc_auc",
    "roc_curve",
]


@dataclass(frozen=True)
class Interval:
    point: float
    low: float
    high: float
    n: int  # clusters resampled

    def as_dict(self) -> dict[str, float]:
        return {"point": self.point, "low": self.low, "high": self.high, "n": float(self.n)}


@dataclass(frozen=True)
class Confusion:
    tp: float
    fp: float
    tn: float
    fn: float

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0

    def as_dict(self) -> dict[str, float]:
        return {
            "tp": self.tp,
            "fp": self.fp,
            "tn": self.tn,
            "fn": self.fn,
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
        }


@dataclass(frozen=True)
class Calibration:
    """The reliability curve and Murphy's decomposition.

    ``bins`` rows: (mean p, observed, weight)."""

    bins: tuple[tuple[float, float, float], ...]
    brier: float
    reliability: float
    resolution: float
    uncertainty: float

    def as_dict(self) -> dict[str, object]:
        return {
            "bins": [list(b) for b in self.bins],
            "brier": self.brier,
            "reliability": self.reliability,
            "resolution": self.resolution,
            "uncertainty": self.uncertainty,
        }


def _w(w: Sequence[float] | None, n: int) -> Sequence[float]:
    return w if w is not None else [1.0] * n


def log_loss(y: Sequence[int], p: Sequence[float], w: Sequence[float] | None = None) -> float:
    ws = _w(w, len(y))
    total = mass = 0.0
    for yi, pi, wi in zip(y, p, ws, strict=True):
        q = min(max(pi, 1e-12), 1.0 - 1e-12)
        total -= wi * (math.log(q) if yi else math.log(1.0 - q))
        mass += wi
    return total / mass if mass else 0.0


def brier(y: Sequence[int], p: Sequence[float], w: Sequence[float] | None = None) -> float:
    ws = _w(w, len(y))
    mass = sum(ws)
    return (
        sum(wi * (pi - yi) ** 2 for yi, pi, wi in zip(y, p, ws, strict=True)) / mass
        if mass
        else 0.0
    )


def confusion(
    y: Sequence[int], p: Sequence[float], threshold: float, w: Sequence[float] | None = None
) -> Confusion:
    tp = fp = tn = fn = 0.0
    for yi, pi, wi in zip(y, p, _w(w, len(y)), strict=True):
        if pi > threshold:
            if yi:
                tp += wi
            else:
                fp += wi
        elif yi:
            fn += wi
        else:
            tn += wi
    return Confusion(tp, fp, tn, fn)


def _ranked(
    y: Sequence[int], p: Sequence[float], w: Sequence[float]
) -> list[tuple[float, float, float]]:
    """Distinct scores, descending, with the positive and negative weight at each."""
    by: dict[float, list[float]] = {}
    for yi, pi, wi in zip(y, p, w, strict=True):
        acc = by.setdefault(pi, [0.0, 0.0])
        acc[0 if yi else 1] += wi
    return [(s, v[0], v[1]) for s, v in sorted(by.items(), key=lambda kv: -kv[0])]


def average_precision(
    y: Sequence[int], p: Sequence[float], w: Sequence[float] | None = None
) -> float:
    """Area under the precision-recall curve as average precision (step-wise, no interpolation)."""
    ws = _w(w, len(y))
    positives = sum(wi for yi, wi in zip(y, ws, strict=True) if yi)
    if positives <= 0:
        return 0.0
    tp = fp = ap = 0.0
    for _s, pos, neg in _ranked(y, p, ws):
        tp += pos
        fp += neg
        if pos:
            ap += (pos / positives) * (tp / (tp + fp))
    return ap


def pr_curve(
    y: Sequence[int], p: Sequence[float], w: Sequence[float] | None = None, points: int = 60
) -> list[tuple[float, float, float]]:
    """(recall, precision, threshold), thinned to about ``points`` rows for drawing."""
    ws = _w(w, len(y))
    positives = sum(wi for yi, wi in zip(y, ws, strict=True) if yi)
    rows: list[tuple[float, float, float]] = []
    tp = fp = 0.0
    for s, pos, neg in _ranked(y, p, ws):
        tp += pos
        fp += neg
        if positives > 0 and tp + fp > 0:
            rows.append((tp / positives, tp / (tp + fp), s))
    return _thin(rows, points)


def roc_curve(
    y: Sequence[int], p: Sequence[float], w: Sequence[float] | None = None, points: int = 60
) -> list[tuple[float, float, float]]:
    """(false-positive rate, true-positive rate, threshold), thinned for drawing."""
    ws = _w(w, len(y))
    positives = sum(wi for yi, wi in zip(y, ws, strict=True) if yi)
    negatives = sum(ws) - positives
    rows = [(0.0, 0.0, 1.0)]
    tp = fp = 0.0
    for s, pos, neg in _ranked(y, p, ws):
        tp += pos
        fp += neg
        rows.append((fp / negatives if negatives else 0.0, tp / positives if positives else 0.0, s))
    return _thin(rows, points)


def roc_auc(y: Sequence[int], p: Sequence[float], w: Sequence[float] | None = None) -> float:
    curve = roc_curve(y, p, w, points=10**9)
    return sum(
        (x2 - x1) * (y1 + y2) / 2.0 for (x1, y1, _), (x2, y2, _) in itertools.pairwise(curve)
    )


def _thin(rows: list[tuple[float, float, float]], points: int) -> list[tuple[float, float, float]]:
    if len(rows) <= points:
        return rows
    step = len(rows) / points
    picked = [rows[int(i * step)] for i in range(points)]
    if picked[-1] != rows[-1]:
        picked.append(rows[-1])
    return picked


def calibration(
    y: Sequence[int], p: Sequence[float], w: Sequence[float] | None = None, bins: int = 10
) -> Calibration:
    """Equal-width reliability bins and Murphy's exact decomposition over those bins."""
    ws = _w(w, len(y))
    mass = sum(ws)
    acc = [[0.0, 0.0, 0.0] for _ in range(bins)]  # sum w*p, sum w*y, sum w
    for yi, pi, wi in zip(y, p, ws, strict=True):
        b = min(bins - 1, max(0, int(pi * bins)))
        acc[b][0] += wi * pi
        acc[b][1] += wi * yi
        acc[b][2] += wi
    base = sum(wi * yi for yi, wi in zip(y, ws, strict=True)) / mass if mass else 0.0
    rel = res = 0.0
    rows: list[tuple[float, float, float]] = []
    for sp, sy, sw in acc:
        if sw <= 0:
            continue
        mp, oy = sp / sw, sy / sw
        rows.append((mp, oy, sw))
        rel += sw * (mp - oy) ** 2
        res += sw * (oy - base) ** 2
    rel /= mass or 1.0
    res /= mass or 1.0
    return Calibration(tuple(rows), brier(y, p, ws), rel, res, base * (1.0 - base))


def cluster_bootstrap(
    clusters: Sequence[Sequence[int]],
    statistic: Callable[[Sequence[int]], float],
    *,
    replicates: int = 200,
    seed: int = 0,
    alpha: float = 0.05,
) -> Interval:
    """Percentile interval of ``statistic`` over resampled **clusters** of row indices.

    ``statistic`` receives a list of row indices (with repeats) and returns the number. Seeded and
    deterministic; clusters are resampled whole, never split.
    """
    everything = [i for c in clusters for i in c]
    point = statistic(everything)
    if len(clusters) < 2:
        return Interval(point, point, point, len(clusters))
    rng = random.Random(seed)
    values = []
    for _ in range(replicates):
        pick: list[int] = []
        for _k in range(len(clusters)):
            pick.extend(clusters[rng.randrange(len(clusters))])
        values.append(statistic(pick))
    values.sort()
    lo = values[int((alpha / 2) * (replicates - 1))]
    hi = values[int((1 - alpha / 2) * (replicates - 1))]
    return Interval(point, lo, hi, len(clusters))
