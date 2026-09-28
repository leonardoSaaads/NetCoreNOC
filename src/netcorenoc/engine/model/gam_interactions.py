"""The GA²M stage of a `gam` fit: which feature pairs interact, and their small 2-D tables.

Split out of `gam_fit.py` in v0.26.0 at the 400-line module guard; the arithmetic is unchanged
byte for byte, so a fit's document is unchanged (the determinism test would say otherwise). Pairs
are ranked by the FAST heuristic — the Newton gain of the best four-quadrant split of the current
residual (Lou, Caruana, Gehrke & Hooker, *Accurate Intelligible Models with Pairwise Interactions*,
KDD 2013) — and the best ``params.interactions`` get a coarse table boosted on the residual.
"""

from __future__ import annotations

import bisect
import math
import random
from array import array
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - type-only; `gam_fit` imports this module
    from netcorenoc.engine.model.gam_fit import Dataset, FitParams

__all__ = ["interactions"]


def _sigmoid(z: float) -> float:
    if z >= 0.0:
        return 1.0 / (1.0 + math.exp(-min(z, 700.0)))
    e = math.exp(max(z, -700.0))
    return e / (1.0 + e)


def _coarse(edges: tuple[float, ...], bins: int) -> tuple[float, ...]:
    if len(edges) + 1 <= bins:
        return edges
    step = (len(edges) + 1) / bins
    picks = sorted({edges[min(len(edges) - 1, round(step * k) - 1)] for k in range(1, bins)})
    return tuple(picks)


def interactions(
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
    cbins = [
        array("H", (bisect.bisect_right(coarse[f], v) for v in col))
        for f, col in enumerate(train.columns)
    ]
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
        rows = (
            [i for i in range(n) if rng.random() < params.subsample]
            if params.subsample < 1.0
            else list(range(n))
        )
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
