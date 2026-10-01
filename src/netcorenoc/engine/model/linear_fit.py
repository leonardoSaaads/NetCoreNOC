"""Fitting the linear member of the league: L2-regularised logistic regression by Newton's method.

v0.27.0 (ADR #424). Iteratively reweighted least squares — each step solves the penalised Newton
system exactly, in a ``(d+1) x (d+1)`` matrix for ``d`` features — with step-halving whenever a
step would raise the objective. Pure Python and deterministic: no RNG at all, sums in row order.

The objective is the weighted mean log loss plus ``l2/2 · ‖w‖²`` over the **standardised** weights;
the intercept is not penalised. The count-like relations are ``log1p``-transformed first when
``log_counts`` is set (see `linear`), and every feature is clipped at serving time to the box it was
trained on, so a storm ten times larger than any in training cannot extrapolate a straight line
into a hard switch.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from netcorenoc.engine.model import linear
from netcorenoc.engine.model.gam_data import Dataset
from netcorenoc.engine.model.gam_fit import log_loss

__all__ = ["LOG1P_FEATURES", "LinearParams", "LinearResult", "fit"]

#: Heavy-tailed counts: seconds, occasions, alarms, activations, elements.
LOG1P_FEATURES = frozenset(
    {"dt", "ne_episodes", "class_episodes", "item_episodes", "burst", "chatter", "degree"}
)


@dataclass(frozen=True)
class LinearParams:
    l2: float = 1e-3
    log_counts: bool = True
    iterations: int = 30
    tol: float = 1e-8

    def as_dict(self) -> dict[str, float]:
        return {k: float(v) for k, v in sorted(self.__dict__.items())}


@dataclass
class LinearResult:
    document: str
    capacity: str = "iterations"
    best: int = 0
    points: list[int] = field(default_factory=list)
    train_trace: list[float] = field(default_factory=list)
    valid_trace: list[float] = field(default_factory=list)


def _sigmoid(z: float) -> float:
    if z >= 0.0:
        return 1.0 / (1.0 + math.exp(-min(z, 700.0)))
    e = math.exp(max(z, -700.0))
    return e / (1.0 + e)


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting. ``a`` is symmetric positive definite here."""
    n = len(b)
    m = [[*row, b[i]] for i, row in enumerate(a)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[pivot] = m[pivot], m[col]
        lead = m[col][col]
        if abs(lead) < 1e-300:
            continue
        for r in range(col + 1, n):
            factor = m[r][col] / lead
            if factor:
                row, top = m[r], m[col]
                for c in range(col, n + 1):
                    row[c] -= factor * top[c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        acc = m[r][n] - sum(m[r][c] * x[c] for c in range(r + 1, n))
        x[r] = acc / m[r][r] if abs(m[r][r]) > 1e-300 else 0.0
    return x


def _transformed(data: Dataset, transforms: Sequence[str]) -> list[list[float]]:
    return [
        [math.log1p(max(v, 0.0)) for v in col] if t == "log1p" else [float(v) for v in col]
        for col, t in zip(data.columns, transforms, strict=True)
    ]


def fit(
    train: Dataset,
    valid: Dataset,
    params: LinearParams,
    *,
    threshold: float = 0.0,
    grouping: dict[str, float] | None = None,
) -> LinearResult:
    names = train.features
    transforms = [
        "log1p" if params.log_counts and name in LOG1P_FEATURES else "identity" for name in names
    ]
    tcols = _transformed(train, transforms)
    vcols = _transformed(valid, transforms)
    w = list(train.w)
    mass = sum(w)
    low = [min(c) for c in tcols]
    high = [max(c) for c in tcols]
    center = [sum(wi * v for wi, v in zip(w, c, strict=True)) / mass for c in tcols]
    scale = []
    for c, mu in zip(tcols, center, strict=True):
        var = sum(wi * (v - mu) ** 2 for wi, v in zip(w, c, strict=True)) / mass
        scale.append(math.sqrt(var) if var > 1e-12 else 1.0)
    z = [[(v - mu) / s for v in c] for c, mu, s in zip(tcols, center, scale, strict=True)]
    zv = [
        [(min(max(v, lo), hi) - mu) / s for v in c]
        for c, mu, s, lo, hi in zip(vcols, center, scale, low, high, strict=True)
    ]
    d = len(names)
    rate = sum(wi for wi, yi in zip(w, train.y, strict=True) if yi) / mass
    beta = [math.log(rate / (1.0 - rate))] + [0.0] * d

    def eta(cols: list[list[float]], i: int, b: list[float]) -> float:
        return b[0] + sum(b[j + 1] * cols[j][i] for j in range(d))

    def objective(b: list[float]) -> float:
        loss = log_loss(train.y, [_sigmoid(eta(z, i, b)) for i in range(len(w))], w)
        return loss + 0.5 * params.l2 * sum(v * v for v in b[1:])

    def valid_loss(b: list[float]) -> float:
        return log_loss(valid.y, [_sigmoid(eta(zv, i, b)) for i in range(len(valid))], valid.w)

    points, tr, va = [0], [objective(beta)], [valid_loss(beta)]
    for it in range(1, params.iterations + 1):
        grad = [0.0] * (d + 1)
        hess = [[0.0] * (d + 1) for _ in range(d + 1)]
        for i in range(len(w)):
            p = _sigmoid(eta(z, i, beta))
            gi = w[i] * (p - train.y[i]) / mass
            hi = w[i] * max(p * (1.0 - p), 1e-9) / mass
            xi = [1.0] + [z[j][i] for j in range(d)]
            for r_i in range(d + 1):
                grad[r_i] += gi * xi[r_i]
                row, ha = hess[r_i], hi * xi[r_i]
                for c_i in range(r_i, d + 1):
                    row[c_i] += ha * xi[c_i]
        for row_i in range(d + 1):
            for col_i in range(row_i):
                hess[row_i][col_i] = hess[col_i][row_i]
        for j in range(1, d + 1):
            grad[j] += params.l2 * beta[j]
            hess[j][j] += params.l2
        step = _solve(hess, grad)
        before = tr[-1]
        size = 1.0
        while True:
            trial = [b - size * s for b, s in zip(beta, step, strict=True)]
            after = objective(trial)
            if after <= before + 1e-15 or size < 1e-4:
                break
            size /= 2.0
        beta = trial
        points.append(it)
        tr.append(after)
        va.append(valid_loss(beta))
        if before - after < params.tol:
            break
    doc = {
        "format": linear.FORMAT,
        "features": list(names),
        "transform": transforms,
        "center": [round(v, 12) for v in center],
        "scale": [round(v, 12) for v in scale],
        "low": low,
        "high": high,
        "weights": [
            round(max(-linear.MAX_ABS_WEIGHT, min(linear.MAX_ABS_WEIGHT, v)), 9) for v in beta[1:]
        ],
        "intercept": round(beta[0], 9),
        "threshold": threshold,
        "grouping": grouping or {"join_bias": 0.0, "merge_bias": 1.0, "merge_min_pairs": 3.0},
    }
    return LinearResult(
        json.dumps(doc, sort_keys=True, separators=(",", ":")),
        "iterations",
        points[-1],
        points,
        tr,
        va,
    )
