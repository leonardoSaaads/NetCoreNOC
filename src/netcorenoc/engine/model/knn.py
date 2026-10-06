"""k-nearest neighbours over the v2 feature vector — a league member that remembers (v0.29.0).

ADR #438. Every other member compresses the training pairs into a function; this one keeps a
**condensed set of them** and answers by analogy: *the pairs most like this one were linked this
often.* It is the league's non-parametric member — no assumed shape at all — and the one whose
mistakes look least like the trees', which is what a judge comparing estimators needs.

    q        = (x - center) · scale                    the vector in the model's metric
    N(q)     = the k reference points nearest to q     Euclidean, `math.dist`
    p(q)     = (Σ wᵢ rᵢ + m·r₀) / (Σ wᵢ + m)           wᵢ = 1, or 1/(dᵢ + ε) when "distance"
    logit    = log p / (1 - p)

* ``scale`` folds the metric into the standardisation: a feature's weight is its learned importance
  divided by its spread, and a feature whose weight is zero is not in the document at all;
* each reference point is a **prototype** — the centre of a cluster of training pairs, with
  ``rᵢ`` the smoothed linked rate of the pairs it stands for (`knn_fit`). Condensing is what keeps
  a query inside the fast loop's budget: a few hundred points, not a training set;
* ``m`` shrinks the vote toward the training rate ``r₀`` — a query far from everything says
  little, and says it near the base rate.

## The document is data, never code (ADR #405, kept)

Exact keys, finite bounded numbers, features drawn only from the served list, every point the same
length, ``k`` no larger than the points, and **reachability**: a document whose vote cannot cross
its threshold is refused. Nothing is imported, evaluated or unpickled.

## The explanation: a sampled Shapley value, labelled as one

A neighbour vote has no closed-form attribution: the neighbour set itself changes as features are
swapped. Exact interventional Shapley values against the reference pair ``z`` would cost ``2^F``
votes. So the explanation is the **permutation estimate** (Štrumbelj & Kononenko, *Explaining
prediction models and individual predictions with feature contributions*, KAIS 41, 2014) over a
fixed set of :data:`PERMUTATIONS` orders and their reverses, walking from ``z`` to ``x`` one feature
at a time. Two properties are exact and the contract relies on both: the contributions **sum to**
``logit(x) - logit(z)`` (each order telescopes), and the estimate is **deterministic** (the orders
are fixed). What is not exact is each feature's share, and the basis says so:
``shapley-sampled``. A feature equal in ``x`` and ``z`` moves nothing and is skipped, which is what
keeps the walk short. The correlator explains only the links it keeps (at most five per alarm).
"""

from __future__ import annotations

import hashlib
import heapq
import json
import math
import random
from dataclasses import dataclass
from itertools import repeat
from typing import Any

from netcorenoc.engine.correlate.features import FEATURE_NAMES
from netcorenoc.engine.correlate.scorer_contract import (
    BASIS_SHAPLEY_SAMPLED,
    CONTRACT_VERSION,
    LinkFeatures,
    LinkScore,
    TermContribution,
)

__all__ = ["FORMAT", "KnnDocumentError", "KnnModel", "KnnScorer", "load", "validate"]

FORMAT = "netcorenoc.knn/1"
WEIGHTINGS = ("uniform", "distance")
MAX_POINTS = 2048
MAX_K = 64
MAX_ABS_COORD = 1e6
MAX_ABS_LOGIT = 12.0  # a vote of 0 or 1 is not certainty: the logit is clipped here
MAX_DOCUMENT_BYTES = 4 * 1024 * 1024
#: Orders of the permutation estimate; each is walked both ways (antithetic pairs).
PERMUTATIONS = 4
_EPS = 1e-6

_KEYS = frozenset(
    {
        "format",
        "features",
        "center",
        "scale",
        "points",
        "rates",
        "k",
        "weighting",
        "prior_rate",
        "prior",
        "reference",
        "threshold",
        "grouping",
    }
)
_GROUPING_KEYS = frozenset({"join_bias", "merge_bias", "merge_min_pairs"})


class KnnDocumentError(ValueError):
    """A k-NN document that is malformed, oversized or degenerate. Never activated."""


@dataclass(frozen=True)
class KnnModel:
    """A validated document, ready to vote."""

    features: tuple[str, ...]
    positions: tuple[int, ...]
    center: tuple[float, ...]
    scale: tuple[float, ...]
    points: tuple[tuple[float, ...], ...]
    rates: tuple[float, ...]
    k: int
    weighting: str
    prior_rate: float
    prior: float
    reference: tuple[float, ...]
    threshold: float
    grouping: dict[str, float]


def _num(value: Any, what: str, bound: float = MAX_ABS_COORD) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise KnnDocumentError(f"{what} must be a number, got {type(value).__name__}")
    out = float(value)
    if not math.isfinite(out) or abs(out) > bound:
        raise KnnDocumentError(f"{what} must be finite and within ±{bound:g}, got {value!r}")
    return out


def _vector(raw: Any, length: int, what: str) -> tuple[float, ...]:
    if not isinstance(raw, list) or len(raw) != length:
        raise KnnDocumentError(f"{what} must hold exactly {length} numbers")
    return tuple(_num(v, what) for v in raw)


def _refuse_constant(name: str) -> float:
    raise KnnDocumentError(f"non-finite constant {name} in document")


def validate(document: str) -> KnnModel:
    """Parse and validate. **The single validation point.** Raises `KnnDocumentError`."""
    if len(document.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise KnnDocumentError(f"document exceeds {MAX_DOCUMENT_BYTES} bytes")
    try:
        payload = json.loads(document, parse_constant=_refuse_constant)
    except (TypeError, ValueError) as exc:
        raise KnnDocumentError(f"document is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict) or set(payload) != _KEYS:
        got = sorted(payload) if isinstance(payload, dict) else type(payload).__name__
        raise KnnDocumentError(f"document must have exactly the keys {sorted(_KEYS)}, got {got}")
    if payload["format"] != FORMAT:
        raise KnnDocumentError(f"format must be {FORMAT!r}, got {payload['format']!r}")
    features = payload["features"]
    if not isinstance(features, list) or not features or len(set(features)) != len(features):
        raise KnnDocumentError("features must be a non-empty list of distinct names")
    if any(not isinstance(f, str) or f not in FEATURE_NAMES for f in features):
        raise KnnDocumentError(f"features must be drawn from {FEATURE_NAMES}")
    d = len(features)
    center = _vector(payload["center"], d, "center")
    scale = _vector(payload["scale"], d, "scale")
    if any(s <= 0.0 for s in scale):
        raise KnnDocumentError("every scale must be positive: a zero-weight feature is left out")
    raw_points, raw_rates = payload["points"], payload["rates"]
    if not isinstance(raw_points, list) or not 1 <= len(raw_points) <= MAX_POINTS:
        raise KnnDocumentError(f"points must hold 1 to {MAX_POINTS} reference points")
    if not isinstance(raw_rates, list) or len(raw_rates) != len(raw_points):
        raise KnnDocumentError("rates must hold one rate per point")
    points = tuple(_vector(p, d, f"point {i}") for i, p in enumerate(raw_points))
    rates = tuple(_num(r, "rate", 1.0) for r in raw_rates)
    if any(r < 0.0 for r in rates):
        raise KnnDocumentError("every rate must lie in [0, 1]")
    k = payload["k"]
    if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= min(MAX_K, len(points)):
        raise KnnDocumentError(f"k must be an integer from 1 to {min(MAX_K, len(points))}")
    if payload["weighting"] not in WEIGHTINGS:
        raise KnnDocumentError(f"weighting must be one of {WEIGHTINGS}")
    prior_rate = _num(payload["prior_rate"], "prior_rate", 1.0)
    if not 0.0 < prior_rate < 1.0:
        raise KnnDocumentError("prior_rate must lie strictly between 0 and 1")
    prior = _num(payload["prior"], "prior", 1e4)
    if prior < 0.0:
        raise KnnDocumentError("prior must not be negative")
    grouping = payload["grouping"]
    if not isinstance(grouping, dict) or set(grouping) != _GROUPING_KEYS:
        raise KnnDocumentError(f"grouping must have exactly the keys {sorted(_GROUPING_KEYS)}")
    model = KnnModel(
        tuple(features),
        tuple(FEATURE_NAMES.index(f) for f in features),
        center,
        scale,
        points,
        rates,
        k,
        str(payload["weighting"]),
        prior_rate,
        prior,
        _vector(payload["reference"], d, "reference"),
        _num(payload["threshold"], "threshold", MAX_ABS_LOGIT),
        {k_: _num(v, f"grouping.{k_}") for k_, v in sorted(grouping.items())},
    )
    if model.grouping["merge_min_pairs"] < 1:
        raise KnnDocumentError("grouping.merge_min_pairs must be at least 1")
    _reachable(model)
    return model


def _logit(p: float) -> float:
    p = min(max(p, 1e-12), 1.0 - 1e-12)
    return max(-MAX_ABS_LOGIT, min(MAX_ABS_LOGIT, math.log(p / (1.0 - p))))


def _reachable(model: KnnModel) -> None:
    """Refuse a model whose vote can only ever answer one way (the Gate 0 lesson, ADR #164).

    The vote is a convex mix of neighbour rates and the prior rate, so it lies between the mixes of
    the ``k`` lowest and the ``k`` highest rates — exactly, for a uniform vote; a distance vote can
    lean on one neighbour, so its bounds are the extreme rates themselves."""
    ordered = sorted(model.rates)
    m, r0, k = model.prior, model.prior_rate, model.k
    if model.weighting == "uniform":
        low = (sum(ordered[:k]) + m * r0) / (k + m)
        high = (sum(ordered[-k:]) + m * r0) / (k + m)
    else:
        low, high = min(ordered[0], r0), max(ordered[-1], r0)
    lo, hi = _logit(low), _logit(high)
    if not lo < model.threshold < hi:
        raise KnnDocumentError(
            f"the threshold {model.threshold} is outside the attainable logit range "
            f"[{lo:.3f}, {hi:.3f}]: the model cannot discriminate"
        )


def fingerprint(document: str) -> str:
    return hashlib.sha256(f"knn\n{CONTRACT_VERSION}\n{document}".encode()).hexdigest()


class KnnScorer:
    """A `LinkScorer` over the v2 feature vector. Pure and deterministic."""

    def __init__(self, model: KnnModel, document: str, scorer_id: str = "knn") -> None:
        self.model = model
        self.params_document = document
        self._fingerprint = fingerprint(document)
        self._scorer_id = scorer_id
        self.threshold = model.threshold
        self._points = model.points
        self._rates = model.rates
        self._k = model.k
        self._uniform = model.weighting == "uniform"
        self._mix = model.prior * model.prior_rate
        self._z = self._standard_local(model.reference)
        self.base_value = self._vote_distances(list(map(math.dist, repeat(self._z), self._points)))
        rng = random.Random(f"knn-permutations|{len(model.features)}")  # nosec B311 - fixed orders
        self._orders = [rng.sample(range(len(model.features)), len(model.features))]
        while len(self._orders) < PERMUTATIONS:
            self._orders.append(rng.sample(range(len(model.features)), len(model.features)))

    @property
    def scorer_id(self) -> str:
        return self._scorer_id

    @property
    def contract_version(self) -> str:
        return CONTRACT_VERSION

    def params_fingerprint(self) -> str:
        return self._fingerprint

    def _standard_local(self, local: tuple[float, ...] | list[float]) -> tuple[float, ...]:
        m = self.model
        return tuple((v - c) * s for v, c, s in zip(local, m.center, m.scale, strict=True))

    def _standard(self, vector: tuple[float, ...] | list[float]) -> tuple[float, ...]:
        m = self.model
        return tuple(
            (vector[p] - c) * s for p, c, s in zip(m.positions, m.center, m.scale, strict=True)
        )

    def _vote_distances(self, dist: list[float]) -> float:
        """The vote from every point's distance: the k nearest, smoothed toward the prior."""
        near = heapq.nsmallest(self._k, range(len(dist)), key=dist.__getitem__)
        if self._uniform:
            weight = float(len(near))
            mass = sum(self._rates[i] for i in near)
        else:
            ws = [1.0 / (dist[i] + _EPS) for i in near]
            weight = sum(ws)
            mass = sum(w * self._rates[i] for w, i in zip(ws, near, strict=True))
        return _logit((mass + self._mix) / (weight + self.model.prior))

    def logit(self, vector: tuple[float, ...] | list[float]) -> float:
        """The hot path: one distance per point (C-level `math.dist`), one partial sort."""
        return self._vote_distances(
            list(map(math.dist, repeat(self._standard(vector)), self._points))
        )

    def attribution(self, vector: tuple[float, ...]) -> list[float]:
        """The permutation estimate of the Shapley values against the reference pair."""
        x = self._standard(vector)
        z = self._z
        d = len(x)
        phi = [0.0] * d
        moving = [j for j in range(d) if x[j] != z[j]]
        if not moving:
            return phi
        points = self._points
        base_sq = [sum((zj - pj) ** 2 for zj, pj in zip(z, p, strict=True)) for p in points]
        # Per feature, how each point's squared distance changes when z's value becomes x's.
        delta = {j: [(x[j] - p[j]) ** 2 - (z[j] - p[j]) ** 2 for p in points] for j in moving}
        walks = 0
        for order in self._orders:
            steps = [j for j in order if j in delta]
            for path in (steps, steps[::-1]):
                sq = list(base_sq)
                before = self.base_value
                for j in path:
                    dj = delta[j]
                    sq = [a + b for a, b in zip(sq, dj, strict=True)]
                    after = self._vote_distances([math.sqrt(max(v, 0.0)) for v in sq])
                    phi[j] += after - before
                    before = after
                walks += 1
        return [v / walks for v in phi]

    def explain(self, vector: tuple[float, ...]) -> LinkScore:
        """The logit **and** its decomposition: one sampled Shapley value per model feature."""
        phi = self.attribution(vector)
        terms = tuple(
            TermContribution(name, 0.0, vector[FEATURE_NAMES.index(name)], phi[k])
            for k, name in enumerate(self.model.features)
        )
        total = self.logit(vector)
        return LinkScore(
            linked=total > self.threshold,
            score=total,
            threshold=self.threshold,
            terms=terms,
            basis=BASIS_SHAPLEY_SAMPLED,
            base_value=self.base_value,
        )

    def score(self, features: LinkFeatures) -> LinkScore:
        if features.vector is None:
            raise KnnDocumentError("a k-NN scorer needs the v2 feature vector, and none was built")
        return self.explain(features.vector)


def load(document: str, scorer_id: str = "knn") -> KnnScorer:
    return KnnScorer(validate(document), document, scorer_id)
