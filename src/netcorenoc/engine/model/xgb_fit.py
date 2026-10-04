"""Fitting the `xgboost` league member: the XGBoost algorithm, in pure Python (v0.29.0, ADR #438).

**The algorithm, not the library.** Chen & Guestrin, *XGBoost: A Scalable Tree Boosting System*,
KDD 2016, with the histogram split finder XGBoost uses by default since 2.0
(``tree_method="hist"``). No `xgboost` import, no new dependency; the fitted model is served by
`trees.py` like every other tree member, so the appliance runs, validates and explains it exactly
as it does them.

What makes it XGBoost rather than `boosted_trees` (`trees_fit._boost`, which is Newton boosting
with an L2 leaf penalty and nothing else):

* **the regularised objective** ``Σ l(y, ŷ) + Σₜ (gamma·Tₜ + ½λ‖wₜ‖² + alpha‖wₜ‖₁)``, second-order:
  with ``T(G) = sign(G)·max(|G| - alpha, 0)`` a node's score is ``T(G)²/(H + λ)``, a split's gain is
  ``score(L) + score(R) - score(parent)`` — the library's ``loss_chg``, which is the paper's eq. 7
  without its ½, so ``gamma`` means here what it means in XGBoost's documentation — and a leaf's
  weight is ``-T(G)/(H + λ)``;
* **``min_child_weight``**: a split is refused unless each child holds that much Hessian;
* **``max_delta_step``**: a leaf's raw weight is clipped to ``±max_delta_step`` before the learning
  rate — XGBoost's guard for logistic loss on unbalanced data, where ``H`` can be tiny;
* **pruning after growth**: XGBoost grows to ``max_depth`` on any positive gain, then removes, from
  the bottom up, every split whose two children are leaves and whose gain is below ``gamma``. A weak
  split that leads to strong ones survives, which greedy pre-pruning would lose;
* **column subsampling at three levels** — per tree, per depth level, per node — and row
  subsampling per tree;
* **early stopping** on the validation streams' log loss.

What it does not do, stated rather than implied: sparsity-aware default directions (the served
vector has no missing values — an unknown severity is a value), and the weighted quantile sketch
(the ``approx`` method), because the histogram is computed once on the training rows, as ``hist``
does.

Deterministic: one seeded `random.Random` for every draw, fixed feature order, strict ``>`` on gain.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from netcorenoc.engine.model import trees
from netcorenoc.engine.model.gam_data import Dataset
from netcorenoc.engine.model.gam_fit import log_loss
from netcorenoc.engine.model.trees_grow import Binned, sigmoid, walk_binned

__all__ = ["XGBParams", "XGBResult", "fit"]

_CLAMP = 12.0  # |leaf log-odds| after shrinkage: inside the document bound, past any certainty


@dataclass(frozen=True)
class XGBParams:
    """XGBoost's own parameter names and meanings, so a reader can check them against its docs."""

    eta: float = 0.1  # learning rate
    max_depth: int = 6
    min_child_weight: float = 1.0
    gamma: float = 0.0  # min_split_loss
    reg_lambda: float = 1.0
    reg_alpha: float = 0.0
    max_delta_step: float = 0.0  # 0 = no clip
    subsample: float = 1.0
    colsample_bytree: float = 1.0
    colsample_bylevel: float = 1.0
    colsample_bynode: float = 1.0
    max_bin: int = 32
    rounds: int = 400
    patience: int = 40
    check_every: int = 5
    seed: int = 0

    def as_dict(self) -> dict[str, float]:
        return {k: float(v) for k, v in sorted(self.__dict__.items())}


@dataclass
class XGBResult:
    document: str
    capacity: str
    best: int
    points: list[int]
    train_trace: list[float]
    valid_trace: list[float]


def _thresh(g: float, alpha: float) -> float:
    """L1 soft-thresholding of a gradient sum: XGBoost's ``ThresholdL1``."""
    if g > alpha:
        return g - alpha
    if g < -alpha:
        return g + alpha
    return 0.0


class XGBGrower:
    """One regularised tree over binned rows, grown depth-wise and then pruned by ``gamma``."""

    def __init__(
        self,
        binned: Binned,
        g: Sequence[float],
        h: Sequence[float],
        p: XGBParams,
        rng: random.Random,
        tree_feats: list[int],
    ) -> None:
        self.b, self.g, self.h, self.p, self.rng = binned, g, h, p, rng
        self.tree_feats = tree_feats
        # nodes: [local feature, threshold, left, right, value]; kbin for binned walks
        self.nodes: list[list[Any]] = []
        self.kbin: list[int] = []
        self.gain: list[float] = []
        self.level_feats: dict[int, list[int]] = {}

    def score(self, g: float, h: float) -> float:
        t = _thresh(g, self.p.reg_alpha)
        return t * t / (h + self.p.reg_lambda)

    def weight(self, g: float, h: float) -> float:
        """The leaf's output after ``max_delta_step`` and the learning rate."""
        w = -_thresh(g, self.p.reg_alpha) / (h + self.p.reg_lambda)
        if self.p.max_delta_step > 0.0:
            w = max(-self.p.max_delta_step, min(self.p.max_delta_step, w))
        return max(-_CLAMP, min(_CLAMP, round(self.p.eta * w, 9)))

    def _hist(self, idx: Sequence[int]) -> dict[int, tuple[list[float], list[float]]]:
        gi = [self.g[i] for i in idx]
        hi = [self.h[i] for i in idx]
        out: dict[int, tuple[list[float], list[float]]] = {}
        for f in self.tree_feats:
            col = self.b.bins[f]
            nb = len(self.b.edges[f]) + 1
            hg, hh = [0.0] * nb, [0.0] * nb
            for k, gv, hv in zip([col[i] for i in idx], gi, hi, strict=True):
                hg[k] += gv
                hh[k] += hv
            out[f] = (hg, hh)
        return out

    def _subset(self, pool: list[int], fraction: float) -> list[int]:
        if fraction >= 1.0 or len(pool) <= 1:
            return pool
        return sorted(self.rng.sample(pool, max(1, math.ceil(fraction * len(pool)))))

    def _best(
        self, hist: dict[int, tuple[list[float], list[float]]], feats: Sequence[int]
    ) -> tuple[float, int, int] | None:
        best: tuple[float, int, int] | None = None
        mcw = self.p.min_child_weight
        for f in feats:
            hg, hh = hist[f]
            gt, ht = sum(hg), sum(hh)
            parent = self.score(gt, ht)
            gl = hl = 0.0
            for k in range(len(hg) - 1):
                gl += hg[k]
                hl += hh[k]
                hr = ht - hl
                if hl < mcw or hr < mcw:
                    continue
                gain = self.score(gl, hl) + self.score(gt - gl, hr) - parent
                if gain > 1e-12 and (best is None or gain > best[0]):
                    best = (gain, f, k)
        return best

    def grow(self, idx: list[int]) -> list[list[Any]]:
        root_hist = self._hist(idx)
        self._new(idx)
        depth = [0]
        stack: list[tuple[int, list[int], dict[int, tuple[list[float], list[float]]]]] = [
            (0, idx, root_hist)
        ]
        while stack:
            node, rows, hist = stack.pop()
            d = depth[node]
            if d >= self.p.max_depth or len(rows) < 2:
                continue
            if d not in self.level_feats:
                self.level_feats[d] = self._subset(self.tree_feats, self.p.colsample_bylevel)
            feats = self._subset(self.level_feats[d], self.p.colsample_bynode)
            found = self._best(hist, feats)
            if found is None:
                continue
            gain, f, k = found
            col = self.b.bins[f]
            left = [i for i in rows if col[i] <= k]
            right = [i for i in rows if col[i] > k]
            lo, hi = self._new(left), self._new(right)
            depth.extend((d + 1, d + 1))
            self.nodes[node][0:4] = [f, self.b.edges[f][k], lo, hi]
            self.kbin[node] = k
            self.gain[node] = gain
            small = left if len(left) <= len(right) else right
            sh = self._hist(small)
            bh = {
                q: (
                    [a - b for a, b in zip(hist[q][0], sh[q][0], strict=True)],
                    [a - b for a, b in zip(hist[q][1], sh[q][1], strict=True)],
                )
                for q in self.tree_feats
            }
            lh, rh = (sh, bh) if small is left else (bh, sh)
            stack.append((hi, right, rh))
            stack.append((lo, left, lh))
        self._prune()
        return self._compact()

    def _new(self, rows: Sequence[int]) -> int:
        g = sum(self.g[i] for i in rows)
        h = sum(self.h[i] for i in rows)
        self.nodes.append([trees.LEAF, 0.0, 0, 0, self.weight(g, h)])
        self.kbin.append(0)
        self.gain.append(0.0)
        return len(self.nodes) - 1

    def _prune(self) -> None:
        """XGBoost's ``prune`` updater: collapse, bottom-up, every split over two leaves whose gain
        is below ``gamma``. Children always sit after their parent, so one reverse pass suffices."""
        if self.p.gamma <= 0.0:
            return
        nodes = self.nodes
        for i in range(len(nodes) - 1, -1, -1):
            node = nodes[i]
            if node[0] == trees.LEAF:
                continue
            lo, hi = node[2], node[3]
            if (
                nodes[lo][0] == trees.LEAF
                and nodes[hi][0] == trees.LEAF
                and self.gain[i] < self.p.gamma
            ):
                node[0:4] = [trees.LEAF, 0.0, 0, 0]
                self.kbin[i] = 0

    def _compact(self) -> list[list[Any]]:
        """Drop the nodes pruning orphaned and renumber, keeping children after parents."""
        order: dict[int, int] = {}
        out: list[list[Any]] = []
        kb: list[int] = []
        queue = [0]
        order[0] = 0
        out.append(list(self.nodes[0]))
        kb.append(self.kbin[0])
        while queue:
            i = queue.pop(0)
            row = out[order[i]]
            if row[0] == trees.LEAF:
                row[1:4] = [0.0, 0, 0]
                continue
            for slot in (2, 3):
                child = self.nodes[i][slot]
                order[child] = len(out)
                out.append(list(self.nodes[child]))
                kb.append(self.kbin[child])
                row[slot] = order[child]
                queue.append(child)
        self.kbin = kb
        return out


def _loss(y: Sequence[int], w: Sequence[float], z: Sequence[float]) -> float:
    return log_loss(y, [sigmoid(v) for v in z], w)


def fit(
    train: Dataset,
    valid: Dataset,
    params: XGBParams,
    *,
    threshold: float = 0.0,
    grouping: dict[str, float] | None = None,
    reference: Sequence[float] | None = None,
) -> XGBResult:
    """Boost ``params.rounds`` regularised trees; validation log loss picks the round."""
    from netcorenoc.engine.model.trees_fit import _document, _median

    rng = random.Random(params.seed)  # nosec B311 - seeded statistical sampling, never a secret
    tb = Binned.of(train, params.max_bin)
    vb = Binned.of(valid, params.max_bin, tb.edges)
    n, m = len(train), len(valid)
    mass = sum(train.w)
    rate = sum(train.w[i] for i in range(n) if train.y[i]) / mass
    base = math.log(rate / (1.0 - rate))
    ref = list(reference) if reference is not None else [_median(c) for c in train.columns]
    zt, zv = [base] * n, [base] * m
    grown: list[list[list[Any]]] = []
    points: list[int] = []
    tr: list[float] = []
    va: list[float] = []
    best_loss, best_round, since = math.inf, 0, 0
    size = max(1, int(params.subsample * n))
    all_feats = list(range(len(tb.bins)))
    for r in range(1, params.rounds + 1):
        rows = sorted(rng.sample(range(n), size)) if size < n else list(range(n))
        g = [0.0] * n
        h = [0.0] * n
        for i in rows:
            p = sigmoid(zt[i])
            g[i] = train.w[i] * (p - train.y[i])
            h[i] = train.w[i] * max(p * (1.0 - p), 1e-6)
        feats = (
            sorted(
                rng.sample(all_feats, max(1, math.ceil(params.colsample_bytree * len(all_feats))))
            )
            if params.colsample_bytree < 1.0
            else all_feats
        )
        grower = XGBGrower(tb, g, h, params, rng, feats)
        nodes = grower.grow(rows)
        grown.append(nodes)
        for i in range(n):
            zt[i] += walk_binned(nodes, grower.kbin, tb.bins, i)
        for i in range(m):
            zv[i] += walk_binned(nodes, grower.kbin, vb.bins, i)
        if r % params.check_every == 0 or r == params.rounds:
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
    best_round = max(best_round, 1)
    doc = _document(
        "xgboost",
        train.features,
        ref,
        round(base, 9),
        1.0,
        grown[:best_round],
        threshold,
        grouping,
    )
    return XGBResult(doc, "rounds", best_round, points, tr, va)
