"""Growing one histogram tree — the inner loop every tree member of the league is fitted with.

Split out of `trees_fit.py` at the 400-line guard (v0.27.0). `Binned` holds a dataset's features as
bin indices (``bisect_left``, so the training partition and the served ``x <= threshold`` rule agree
on an edge); `Grower` grows one tree depth-first over per-bin sums, summing only the smaller child
and deriving its sibling by subtraction; `walk_binned` reads a grown tree on binned rows. The two
criteria and their leaf values are `trees_fit`'s to choose and are passed in.
"""

from __future__ import annotations

import bisect
import math
import random
from array import array
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from netcorenoc.engine.model import trees
from netcorenoc.engine.model.gam_data import Dataset
from netcorenoc.engine.model.gam_fit import bin_edges

if TYPE_CHECKING:  # pragma: no cover - type-only
    from netcorenoc.engine.model.trees_fit import TreeParams

__all__ = ["Binned", "Grower", "sigmoid", "walk_binned"]


@dataclass
class Binned:
    """A dataset's features as bin indices, with the edges that produced them."""

    edges: list[tuple[float, ...]]
    bins: list[array[int]]

    @classmethod
    def of(
        cls, data: Dataset, max_bins: int, edges: list[tuple[float, ...]] | None = None
    ) -> Binned:
        cuts = edges if edges is not None else [bin_edges(col, max_bins) for col in data.columns]
        return cls(
            cuts,
            [
                array("B", (bisect.bisect_left(e, v) for v in col))
                for e, col in zip(cuts, data.columns, strict=True)
            ],
        )


def sigmoid(z: float) -> float:
    if z >= 0.0:
        return 1.0 / (1.0 + math.exp(-min(z, 700.0)))
    e = math.exp(max(z, -700.0))
    return e / (1.0 + e)


class Grower:
    """One tree over binned rows. ``g``/``h`` are the per-row sums the criterion reads."""

    def __init__(
        self,
        binned: Binned,
        g: Sequence[float],
        h: Sequence[float],
        params: TreeParams,
        criterion: str,
        rng: random.Random,
        leaf_value: Callable[[float, float], float],
    ) -> None:
        self.b = binned
        self.g, self.h = g, h
        self.p = params
        self.criterion = criterion
        self.rng = rng
        self.leaf_value = leaf_value
        self.nf = len(binned.bins)
        self.mtry = max(1, math.ceil(params.colsample * self.nf))
        # nodes: [local feature, threshold, left, right, value]; bin index kept for binned walks
        self.nodes: list[list[Any]] = []
        self.kbin: list[int] = []
        self.depth: list[int] = []
        self.own: list[float] = []  # each node's value as if it were a leaf (depth truncation)

    def _hist(
        self, idx: Sequence[int], feats: Sequence[int]
    ) -> dict[int, tuple[list[float], list[float]]]:
        gi = [self.g[i] for i in idx]
        hi = [self.h[i] for i in idx]
        out: dict[int, tuple[list[float], list[float]]] = {}
        for f in feats:
            col = self.b.bins[f]
            nb = len(self.b.edges[f]) + 1
            hg, hh = [0.0] * nb, [0.0] * nb
            for k, gv, hv in zip([col[i] for i in idx], gi, hi, strict=True):
                hg[k] += gv
                hh[k] += hv
            out[f] = (hg, hh)
        return out

    def _score(self, g: float, h: float) -> float:
        if self.criterion == "newton":
            return g * g / (h + self.p.l2)
        return -(g * (h - g) / h) if h > 0 else 0.0  # minus the weighted Gini impurity (halved)

    def _best(
        self, hist: dict[int, tuple[list[float], list[float]]], feats: Sequence[int]
    ) -> tuple[float, int, int] | None:
        best: tuple[float, int, int] | None = None
        for f in feats:
            hg, hh = hist[f]
            gt, ht = sum(hg), sum(hh)
            parent = self._score(gt, ht)
            gl = hl = 0.0
            for k in range(len(hg) - 1):
                gl += hg[k]
                hl += hh[k]
                hr = ht - hl
                if hl < self.p.min_leaf or hr < self.p.min_leaf:
                    continue
                gain = self._score(gl, hl) + self._score(gt - gl, hr) - parent
                if gain > 1e-12 and (best is None or gain > best[0]):
                    best = (gain, f, k)
        return best

    def grow(self, idx: list[int]) -> list[list[Any]]:
        all_feats = list(range(self.nf))
        subtract = self.mtry >= self.nf
        root_hist = self._hist(idx, all_feats) if subtract else None
        self._new(idx, 0)
        stack: list[tuple[int, list[int], dict[int, tuple[list[float], list[float]]] | None]] = [
            (0, idx, root_hist)
        ]
        while stack:
            node, rows, hist = stack.pop()
            if self.depth[node] >= self.p.max_depth or len(rows) < 2:
                continue
            feats = all_feats if subtract else sorted(self.rng.sample(all_feats, self.mtry))
            if hist is None:
                hist = self._hist(rows, feats)
            found = self._best(hist, feats)
            if found is None:
                continue
            _gain, f, k = found
            col = self.b.bins[f]
            left = [i for i in rows if col[i] <= k]
            right = [i for i in rows if col[i] > k]
            lo = self._new(left, self.depth[node] + 1)
            hi = self._new(right, self.depth[node] + 1)
            self.nodes[node][0:4] = [f, self.b.edges[f][k], lo, hi]
            self.kbin[node] = k
            lh = rh = None
            if subtract:
                small = left if len(left) <= len(right) else right
                sh = self._hist(small, all_feats)
                bh = {
                    q: (
                        [a - b for a, b in zip(hist[q][0], sh[q][0], strict=True)],
                        [a - b for a, b in zip(hist[q][1], sh[q][1], strict=True)],
                    )
                    for q in all_feats
                }
                lh, rh = (sh, bh) if small is left else (bh, sh)
            stack.append((hi, right, rh))
            stack.append((lo, left, lh))
        return self.nodes

    def _new(self, rows: Sequence[int], depth: int) -> int:
        g = sum(self.g[i] for i in rows)
        h = sum(self.h[i] for i in rows)
        value = self.leaf_value(g, h)
        self.nodes.append([trees.LEAF, 0.0, 0, 0, value])
        self.kbin.append(0)
        self.depth.append(depth)
        self.own.append(value)
        return len(self.nodes) - 1


def walk_binned(nodes: list[list[Any]], kbin: list[int], bins: list[array[int]], i: int) -> float:
    n = 0
    f = nodes[0][0]
    while f != trees.LEAF:
        n = nodes[n][2] if bins[f][i] <= kbin[n] else nodes[n][3]
        f = nodes[n][0]
    return float(nodes[n][4])
