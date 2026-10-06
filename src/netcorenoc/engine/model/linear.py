"""Logistic regression over the v2 feature vector — the league's linear baseline, as data.

v0.27.0 (ADR #424). The simplest member a competition should have: if a regularised linear model
over the same fifteen relations does as well as the ensembles, the ensembles are not earning their
cost, and the judge's table is where that shows.

    logit(x) = intercept + Σᵢ wᵢ · (tᵢ(xᵢ) - cᵢ) / sᵢ

``tᵢ`` is ``identity`` or ``log1p`` per feature — the count-like relations (``dt``, ``burst``,
``chatter``, ``degree``, the episode counts) are heavy-tailed, and a straight line through a raw
count lets one storm's thousand alarms outweigh everything else. ``cᵢ`` and ``sᵢ`` are the training
mean and standard deviation of the transformed feature, so every weight is in the same unit and an
L2 penalty treats them alike.

**The explanation is the model**: each term's contribution is ``wᵢ · zᵢ`` exactly, the base value is
the intercept (the logit at the training mean), and the contract's sum holds with no approximation.
Its basis is ``linear`` (v0.29.0), not the formula's ``weighted-sum``: the readers that see that
name store the formula's three columns and read the score as a probability.

A document is JSON with exactly the keys :data:`_KEYS`; nothing in it names code. It is refused if a
number is non-finite, a weight is beyond :data:`MAX_ABS_WEIGHT`, a scale is not positive, a feature
is not one this build serves, or the logit cannot reach its threshold anywhere in the features'
attainable range (every transformed feature is bounded by the document's own ``low``/``high``).
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any

from netcorenoc.engine.correlate.features import FEATURE_NAMES
from netcorenoc.engine.correlate.scorer_contract import (
    BASIS_LINEAR,
    CONTRACT_VERSION,
    LinkFeatures,
    LinkScore,
    TermContribution,
)

__all__ = ["FORMAT", "TRANSFORMS", "LinearDocumentError", "LinearScorer", "load", "validate"]

FORMAT = "netcorenoc.linear/1"
TRANSFORMS = ("identity", "log1p")
MAX_ABS_WEIGHT = 25.0
MAX_DOCUMENT_BYTES = 64 * 1024

_KEYS = frozenset(
    {
        "format",
        "features",
        "transform",
        "center",
        "scale",
        "low",
        "high",
        "weights",
        "intercept",
        "threshold",
        "grouping",
    }
)
_GROUPING_KEYS = frozenset({"join_bias", "merge_bias", "merge_min_pairs"})


class LinearDocumentError(ValueError):
    """A linear document that is malformed or degenerate. Never activated."""


@dataclass(frozen=True)
class LinearModel:
    features: tuple[str, ...]
    positions: tuple[int, ...]
    transform: tuple[str, ...]
    center: tuple[float, ...]
    scale: tuple[float, ...]
    low: tuple[float, ...]
    high: tuple[float, ...]
    weights: tuple[float, ...]
    intercept: float
    threshold: float
    grouping: dict[str, float]


def _num(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise LinearDocumentError(f"{what} must be a number, got {type(value).__name__}")
    out = float(value)
    if not math.isfinite(out):
        raise LinearDocumentError(f"{what} must be finite, got {value!r}")
    return out


def _numbers(raw: Any, what: str, n: int) -> tuple[float, ...]:
    if not isinstance(raw, list) or len(raw) != n:
        raise LinearDocumentError(f"{what} must hold one number per feature")
    return tuple(_num(v, what) for v in raw)


def _refuse_constant(name: str) -> float:
    raise LinearDocumentError(f"non-finite constant {name} in document")


def validate(document: str) -> LinearModel:
    """Parse and validate. **The single validation point.** Raises `LinearDocumentError`."""
    if len(document.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise LinearDocumentError(f"document exceeds {MAX_DOCUMENT_BYTES} bytes")
    try:
        payload = json.loads(document, parse_constant=_refuse_constant)
    except (TypeError, ValueError) as exc:
        raise LinearDocumentError(f"document is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict) or set(payload) != _KEYS:
        got = sorted(payload) if isinstance(payload, dict) else type(payload).__name__
        raise LinearDocumentError(f"document must have exactly the keys {sorted(_KEYS)}, got {got}")
    if payload["format"] != FORMAT:
        raise LinearDocumentError(f"format must be {FORMAT!r}, got {payload['format']!r}")
    features = payload["features"]
    if not isinstance(features, list) or not features or len(set(features)) != len(features):
        raise LinearDocumentError("features must be a non-empty list of distinct names")
    if any(not isinstance(f, str) or f not in FEATURE_NAMES for f in features):
        raise LinearDocumentError(f"features must be drawn from {FEATURE_NAMES}")
    n = len(features)
    transform = payload["transform"]
    if (
        not isinstance(transform, list)
        or len(transform) != n
        or any(t not in TRANSFORMS for t in transform)
    ):
        raise LinearDocumentError(f"transform must name one of {TRANSFORMS} per feature")
    scale = _numbers(payload["scale"], "scale", n)
    if any(s <= 0.0 for s in scale):
        raise LinearDocumentError("every scale must be positive")
    weights = _numbers(payload["weights"], "weights", n)
    if any(abs(w) > MAX_ABS_WEIGHT for w in weights):
        raise LinearDocumentError(f"a weight is beyond ±{MAX_ABS_WEIGHT}: a hard switch")
    low, high = _numbers(payload["low"], "low", n), _numbers(payload["high"], "high", n)
    if any(lo > hi for lo, hi in zip(low, high, strict=True)):
        raise LinearDocumentError("every low bound must be at or below its high bound")
    grouping = payload["grouping"]
    if not isinstance(grouping, dict) or set(grouping) != _GROUPING_KEYS:
        raise LinearDocumentError(f"grouping must have exactly the keys {sorted(_GROUPING_KEYS)}")
    model = LinearModel(
        tuple(features),
        tuple(FEATURE_NAMES.index(f) for f in features),
        tuple(transform),
        _numbers(payload["center"], "center", n),
        scale,
        low,
        high,
        weights,
        _num(payload["intercept"], "intercept"),
        _num(payload["threshold"], "threshold"),
        {k: _num(v, f"grouping.{k}") for k, v in sorted(grouping.items())},
    )
    if abs(model.intercept) > MAX_ABS_WEIGHT:
        raise LinearDocumentError("intercept is beyond the magnitude bound")
    if model.grouping["merge_min_pairs"] < 1:
        raise LinearDocumentError("grouping.merge_min_pairs must be at least 1")
    _reachable(model)
    return model


def _reachable(model: LinearModel) -> None:
    """The logit's range over the box ``[low, high]`` must straddle the threshold."""
    lo = hi = model.intercept
    for w, c, s, a, b in zip(
        model.weights, model.center, model.scale, model.low, model.high, strict=True
    ):
        ends = (w * (a - c) / s, w * (b - c) / s)
        lo += min(ends)
        hi += max(ends)
    if not lo < model.threshold < hi:
        raise LinearDocumentError(
            f"the threshold {model.threshold} is outside the attainable logit range "
            f"[{lo:.3f}, {hi:.3f}]: the model cannot discriminate"
        )


def fingerprint(document: str) -> str:
    return hashlib.sha256(f"linear\n{CONTRACT_VERSION}\n{document}".encode()).hexdigest()


class LinearScorer:
    """A `LinkScorer` over the v2 feature vector. Pure and deterministic."""

    def __init__(self, model: LinearModel, document: str, scorer_id: str = "linear") -> None:
        self.model = model
        self.params_document = document
        self._fingerprint = fingerprint(document)
        self._scorer_id = scorer_id
        self._rows = tuple(
            zip(
                model.positions,
                model.transform,
                model.center,
                model.scale,
                model.low,
                model.high,
                model.weights,
                strict=True,
            )
        )
        self.threshold = model.threshold
        self.base_value = model.intercept

    @property
    def scorer_id(self) -> str:
        return self._scorer_id

    @property
    def contract_version(self) -> str:
        return CONTRACT_VERSION

    def params_fingerprint(self) -> str:
        return self._fingerprint

    def standardised(self, vector: tuple[float, ...] | list[float]) -> list[float]:
        """Each feature transformed, clipped to the trained box, and standardised."""
        out = []
        for pos, transform, center, scale, low, high, _w in self._rows:
            v = float(vector[pos])
            if transform == "log1p":
                v = math.log1p(max(v, 0.0))
            v = min(max(v, low), high)
            out.append((v - center) / scale)
        return out

    def logit(self, vector: tuple[float, ...] | list[float]) -> float:
        total = self.model.intercept
        for z, w in zip(self.standardised(vector), self.model.weights, strict=True):
            total += w * z
        return total

    def explain(self, vector: tuple[float, ...]) -> LinkScore:
        z = self.standardised(vector)
        terms = tuple(
            TermContribution(name, w, v, w * v)
            for name, w, v in zip(self.model.features, self.model.weights, z, strict=True)
        )
        total = self.model.intercept
        for term in terms:
            total += term.contribution
        return LinkScore(
            linked=total > self.threshold,
            score=total,
            threshold=self.threshold,
            terms=terms,
            basis=BASIS_LINEAR,
            base_value=self.model.intercept,
        )

    def score(self, features: LinkFeatures) -> LinkScore:
        if features.vector is None:
            raise LinearDocumentError(
                "a linear scorer needs the v2 feature vector, and none was built"
            )
        return self.explain(features.vector)


def load(document: str, scorer_id: str = "linear") -> LinearScorer:
    return LinearScorer(validate(document), document, scorer_id)
