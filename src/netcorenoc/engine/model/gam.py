"""The `gam` scorer kind: a boosted generalised additive model, stored as data.

## Why this family (ADR #407)

Part VI of the v0.26.0 brief keeps two properties that pull against accuracy: **every link must
decompose into per-feature contributions the console can show**, and **nothing heavy may run on the
ingest path**. Boosted trees are accurate on tabular data and their honest explanation is a Shapley
value that costs 2^F coalitions per pair — the v0.14.0 tree kinds paid it at F = 3 and refuse a
larger model. At fourteen features that door is shut.

A generalised additive model with boosted shape functions — Lou, Caruana & Gehrke's GA²M
(*Intelligible Models for Classification and Regression*, KDD 2012; *Accurate Intelligible Models
with Pairwise Interactions*, KDD 2013), the model InterpretML ships as the Explainable Boosting
Machine — is the family built for exactly this pair of constraints:

* the logit is ``intercept + Σ fᵢ(xᵢ) (+ Σ fᵢⱼ(xᵢ, xⱼ))``, so **each term is its own explanation**:
  no approximation, no coalitions, and for an additive model the centred term *is* the
  interventional Shapley value against the training distribution;
* scoring is one binary search per feature — cheaper per pair than the additive formula's `exp`;
* its accuracy on tabular problems sits close to full gradient-boosted trees, because it *is*
  gradient boosting, restricted to one (or two) features per step.

## The document is data, never code (Part VI.3)

A model is a JSON object with a fixed set of keys, validated here **before** a scorer is built:
exact keys, finite numbers, strictly increasing bin edges, bounded sizes, bounded magnitudes, a
feature list drawn only from :data:`netcorenoc.engine.correlate.features.FEATURE_NAMES`, and a
reachability rule — a model whose logit cannot cross its threshold is refused, because it would
score without error and group nothing (the lesson `model_version` records from Gate 0). No
`pickle`, no `eval`, no import of anything a document names.
"""

from __future__ import annotations

import bisect
import hashlib
import itertools
import json
import math
from dataclasses import dataclass
from typing import Any

from netcorenoc.engine.correlate.features import FEATURE_NAMES
from netcorenoc.engine.correlate.scorer_contract import (
    CONTRACT_VERSION,
    LinkFeatures,
    LinkScore,
    TermContribution,
)

__all__ = [
    "BASIS_SHAPE",
    "FORMAT",
    "KIND",
    "MAX_ABS_SCORE",
    "MAX_BINS",
    "MAX_DOCUMENT_BYTES",
    "MAX_INTERACTIONS",
    "GamDocumentError",
    "GamScorer",
    "Shape",
    "load",
    "validate",
]

KIND = "gam"
FORMAT = "netcorenoc.gam/1"
#: How the terms of a GAM are derived: each is a shape function's value at the pair, centred on
#: the training distribution. Distinct from `weighted-sum` (no weight exists) and from `shapley`
#: (no coalition was enumerated) — although for an additive model the two coincide.
BASIS_SHAPE = "shape"

MAX_BINS = 64
MAX_INTERACTIONS = 8
MAX_INTERACTION_BINS = 16
MAX_ABS_SCORE = 25.0
MAX_DOCUMENT_BYTES = 256 * 1024

_KEYS = frozenset(
    {"format", "features", "intercept", "threshold", "shapes", "interactions", "grouping"}
)
_SHAPE_KEYS = frozenset({"feature", "edges", "scores"})
_PAIR_KEYS = frozenset({"features", "edges", "scores"})
_GROUPING_KEYS = frozenset({"join_bias", "merge_bias", "merge_min_pairs"})


class GamDocumentError(ValueError):
    """A GAM document that is malformed, oversized or degenerate. Never activated."""


@dataclass(frozen=True)
class Shape:
    """One feature's shape function: piecewise constant over ``edges``."""

    feature: str
    index: int  # position in the runtime feature vector
    edges: tuple[float, ...]
    scores: tuple[float, ...]  # len(edges) + 1

    def at(self, x: float) -> float:
        return self.scores[bisect.bisect_right(self.edges, x)]


@dataclass(frozen=True)
class Pair:
    """One pairwise interaction: a small table over two features' coarse bins."""

    features: tuple[str, str]
    index: tuple[int, int]
    edges: tuple[tuple[float, ...], tuple[float, ...]]
    scores: tuple[tuple[float, ...], ...]  # [len(edges[0]) + 1][len(edges[1]) + 1]

    def at(self, x: float, y: float) -> float:
        return self.scores[bisect.bisect_right(self.edges[0], x)][
            bisect.bisect_right(self.edges[1], y)
        ]

    @property
    def name(self) -> str:
        return f"{self.features[0]} x {self.features[1]}"


def _num(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise GamDocumentError(f"{what} must be a number, got {type(value).__name__}")
    out = float(value)
    if not math.isfinite(out):
        raise GamDocumentError(f"{what} must be finite, got {value!r}")
    return out


def _edges(raw: Any, what: str, limit: int) -> tuple[float, ...]:
    if not isinstance(raw, list) or len(raw) > limit - 1:
        raise GamDocumentError(f"{what} must be a list of at most {limit - 1} bin edges")
    edges = tuple(_num(v, what) for v in raw)
    if any(b <= a for a, b in itertools.pairwise(edges)):
        raise GamDocumentError(f"{what} must be strictly increasing")
    return edges


def _scores(raw: Any, what: str, n: int) -> tuple[float, ...]:
    if not isinstance(raw, list) or len(raw) != n:
        raise GamDocumentError(f"{what} must hold exactly {n} scores")
    scores = tuple(_num(v, what) for v in raw)
    if any(abs(s) > MAX_ABS_SCORE for s in scores):
        raise GamDocumentError(f"{what} has a score beyond ±{MAX_ABS_SCORE}: a hard switch")
    return scores


def _feature(name: Any) -> int:
    if not isinstance(name, str) or name not in FEATURE_NAMES:
        raise GamDocumentError(f"unknown feature {name!r}: this build serves {FEATURE_NAMES}")
    return FEATURE_NAMES.index(name)


@dataclass(frozen=True)
class Model:
    """A validated document, ready to score."""

    features: tuple[str, ...]
    intercept: float
    threshold: float
    shapes: tuple[Shape, ...]
    pairs: tuple[Pair, ...]
    grouping: dict[str, float]


def validate(document: str) -> Model:
    """Parse and validate a document. **The single validation point.** Raises `GamDocumentError`."""
    if len(document.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise GamDocumentError(f"document exceeds {MAX_DOCUMENT_BYTES} bytes")
    try:
        payload = json.loads(document, parse_constant=_refuse_constant)
    except (TypeError, ValueError) as exc:
        raise GamDocumentError(f"document is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict) or set(payload) != _KEYS:
        got = sorted(payload) if isinstance(payload, dict) else type(payload).__name__
        raise GamDocumentError(f"document must have exactly the keys {sorted(_KEYS)}, got {got}")
    if payload["format"] != FORMAT:
        raise GamDocumentError(f"format must be {FORMAT!r}, got {payload['format']!r}")
    features = payload["features"]
    if not isinstance(features, list) or not features or len(set(features)) != len(features):
        raise GamDocumentError("features must be a non-empty list of distinct names")
    for name in features:
        _feature(name)
    shapes_raw = payload["shapes"]
    if not isinstance(shapes_raw, list) or len(shapes_raw) != len(features):
        raise GamDocumentError("there must be exactly one shape per feature, in order")
    shapes: list[Shape] = []
    for name, raw in zip(features, shapes_raw, strict=True):
        if not isinstance(raw, dict) or set(raw) != _SHAPE_KEYS or raw["feature"] != name:
            raise GamDocumentError(f"shape for {name!r} is malformed")
        edges = _edges(raw["edges"], f"{name}.edges", MAX_BINS)
        shapes.append(
            Shape(
                name,
                _feature(name),
                edges,
                _scores(raw["scores"], f"{name}.scores", len(edges) + 1),
            )
        )
    pairs_raw = payload["interactions"]
    if not isinstance(pairs_raw, list) or len(pairs_raw) > MAX_INTERACTIONS:
        raise GamDocumentError(f"interactions must be a list of at most {MAX_INTERACTIONS}")
    pairs: list[Pair] = []
    for raw in pairs_raw:
        if not isinstance(raw, dict) or set(raw) != _PAIR_KEYS:
            raise GamDocumentError("an interaction is malformed")
        names = raw["features"]
        if not isinstance(names, list) or len(names) != 2 or any(n not in features for n in names):
            raise GamDocumentError("an interaction must name two of the model's features")
        grid = raw["edges"]
        if not isinstance(grid, list) or len(grid) != 2:
            raise GamDocumentError("an interaction needs two edge lists")
        ex = _edges(grid[0], "interaction edges", MAX_INTERACTION_BINS)
        ey = _edges(grid[1], "interaction edges", MAX_INTERACTION_BINS)
        rows = raw["scores"]
        if not isinstance(rows, list) or len(rows) != len(ex) + 1:
            raise GamDocumentError("interaction scores must have one row per bin")
        table = tuple(_scores(row, "interaction scores", len(ey) + 1) for row in rows)
        pairs.append(
            Pair((names[0], names[1]), (_feature(names[0]), _feature(names[1])), (ex, ey), table)
        )
    grouping = payload["grouping"]
    if not isinstance(grouping, dict) or set(grouping) != _GROUPING_KEYS:
        raise GamDocumentError(f"grouping must have exactly the keys {sorted(_GROUPING_KEYS)}")
    model = Model(
        tuple(features),
        _num(payload["intercept"], "intercept"),
        _num(payload["threshold"], "threshold"),
        tuple(shapes),
        tuple(pairs),
        {k: _num(v, f"grouping.{k}") for k, v in sorted(grouping.items())},
    )
    if abs(model.intercept) > MAX_ABS_SCORE:
        raise GamDocumentError("intercept is beyond the magnitude bound")
    if model.grouping["merge_min_pairs"] < 1:
        raise GamDocumentError("grouping.merge_min_pairs must be at least 1")
    _reachable(model)
    return model


def _refuse_constant(name: str) -> float:
    raise GamDocumentError(f"non-finite constant {name} in document")


def _reachable(model: Model) -> None:
    """Refuse a model that can only ever answer one way: it would group nothing, or everything."""
    low = model.intercept + sum(min(s.scores) for s in model.shapes)
    high = model.intercept + sum(max(s.scores) for s in model.shapes)
    low += sum(min(min(r) for r in p.scores) for p in model.pairs)
    high += sum(max(max(r) for r in p.scores) for p in model.pairs)
    if not low < model.threshold < high:
        raise GamDocumentError(
            f"the threshold {model.threshold} is outside the attainable logit range "
            f"[{low:.3f}, {high:.3f}]: the model cannot discriminate"
        )


def fingerprint(document: str) -> str:
    return hashlib.sha256(f"{KIND}\n{CONTRACT_VERSION}\n{document}".encode()).hexdigest()


class GamScorer:
    """A `LinkScorer` over the v2 feature vector. Pure, deterministic, allocation-light."""

    def __init__(self, model: Model, document: str, scorer_id: str = KIND) -> None:
        self.model = model
        self.params_document = document
        self._fingerprint = fingerprint(document)
        self._scorer_id = scorer_id
        self._shapes = model.shapes
        self._pairs = model.pairs
        self._intercept = model.intercept
        self.threshold = model.threshold

    @property
    def scorer_id(self) -> str:
        return self._scorer_id

    @property
    def contract_version(self) -> str:
        return CONTRACT_VERSION

    def params_fingerprint(self) -> str:
        return self._fingerprint

    def logit(self, vector: tuple[float, ...]) -> float:
        """The hot path: the logit alone, no explanation built."""
        total = self._intercept
        for shape in self._shapes:
            total += shape.scores[bisect.bisect_right(shape.edges, vector[shape.index])]
        for pair in self._pairs:
            total += pair.at(vector[pair.index[0]], vector[pair.index[1]])
        return total

    def explain(self, vector: tuple[float, ...]) -> LinkScore:
        """The logit **and** its decomposition: one term per shape and per interaction."""
        terms = [
            TermContribution(s.feature, 0.0, vector[s.index], s.at(vector[s.index]))
            for s in self._shapes
        ]
        terms.extend(
            TermContribution(p.name, 0.0, 0.0, p.at(vector[p.index[0]], vector[p.index[1]]))
            for p in self._pairs
        )
        total = self._intercept
        for term in terms:
            total += term.contribution
        return LinkScore(
            linked=total > self.threshold,
            score=total,
            threshold=self.threshold,
            terms=tuple(terms),
            basis=BASIS_SHAPE,
            base_value=self._intercept,
        )

    def score(self, features: LinkFeatures) -> LinkScore:
        if features.vector is None:
            raise GamDocumentError("the gam scorer needs the v2 feature vector, and none was built")
        return self.explain(features.vector)


def load(document: str, scorer_id: str = KIND) -> GamScorer:
    return GamScorer(validate(document), document, scorer_id)
