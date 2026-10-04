"""The `knn` league member (v0.29.0, ADR #438): data in, a vote out, an explanation that adds up.

* **A document is data.** Every structural fault is refused before a scorer exists, for its own
  reason — and the fitted document is the control that a refusing validator would fail.
* **The vote is the registered one**: the k nearest prototypes in the model's metric, smoothed
  toward the training rate, and a document that cannot cross its threshold is refused.
* **The explanation is honest**: its terms sum exactly to ``score - base_value``, it is the same on
  every call, and it says it is a sampled Shapley value.
* **The metric is learned**: a feature that carries no information is left out of the distance.
"""

from __future__ import annotations

import json
import math
import random
from typing import Any

import pytest

from netcorenoc.engine.correlate.features import FEATURE_NAMES
from netcorenoc.engine.correlate.scorer_contract import BASIS_SHAPLEY_SAMPLED
from netcorenoc.engine.model import knn, knn_fit, league
from netcorenoc.engine.model.knn_fit import KnnParams

from leaguefixtures import league_data

TRAIN, VALID = league_data(2500, 21), league_data(800, 22)


@pytest.fixture(scope="module")
def document() -> str:
    return knn_fit.fit(TRAIN, VALID, KnnParams(prototypes=96, metric_power=1.0, seed=3)).document


def _doc(base: str, **changes: Any) -> str:
    payload = json.loads(base)
    payload.update(changes)
    return json.dumps(payload)


def _vectors(n: int, seed: int) -> list[tuple[float, ...]]:
    rng = random.Random(seed)
    return [
        tuple(rng.random() * (60 if i in (0, 10) else 1) for i in range(len(FEATURE_NAMES)))
        for _ in range(n)
    ]


def test_the_fitted_document_loads_and_votes(document: str) -> None:
    scorer = league.KINDS["knn"][1](document, "knn")
    assert isinstance(scorer, knn.KnnScorer)
    assert 1 <= scorer.model.k <= len(scorer.model.points)
    values = {round(scorer.logit(v), 6) for v in _vectors(60, 1)}
    assert len(values) > 3, "the vote answers the same for every pair"


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"format": "netcorenoc.knn/0"}, "format must be"),
        ({"features": ["dt", "not_a_feature"]}, "drawn from"),
        ({"k": 0}, "k must be an integer"),
        ({"k": 10_000}, "k must be an integer"),
        ({"weighting": "cosine"}, "weighting must be one of"),
        ({"prior_rate": 1.0}, "strictly between 0 and 1"),
        ({"prior": -1}, "must not be negative"),
        ({"points": []}, "points must hold"),
    ],
)
def test_a_malformed_document_is_refused_for_its_own_reason(
    document: str, change: dict[str, Any], reason: str
) -> None:
    with pytest.raises(knn.KnnDocumentError, match=reason):
        knn.validate(_doc(document, **change))


def test_shape_faults_are_refused(document: str) -> None:
    payload = json.loads(document)
    short = [p[:-1] for p in payload["points"]] if len(payload["features"]) > 1 else None
    if short is not None:
        with pytest.raises(knn.KnnDocumentError, match="point 0"):
            knn.validate(_doc(document, points=short))
    with pytest.raises(knn.KnnDocumentError, match="one rate per point"):
        knn.validate(_doc(document, rates=payload["rates"][:-1]))
    with pytest.raises(knn.KnnDocumentError, match="scale must be positive"):
        knn.validate(_doc(document, scale=[0.0] * len(payload["features"])))
    with pytest.raises(knn.KnnDocumentError, match="exactly the keys"):
        knn.validate(_doc(document, extra=1))
    with pytest.raises(knn.KnnDocumentError, match="non-finite"):
        knn.validate(document.replace(json.dumps(payload["rates"][0]), "NaN", 1))


def test_a_vote_that_cannot_cross_its_threshold_is_refused(document: str) -> None:
    payload = json.loads(document)
    with pytest.raises(knn.KnnDocumentError, match="cannot discriminate"):
        knn.validate(_doc(document, rates=[0.0] * len(payload["rates"]), threshold=0.0))


def test_the_explanation_adds_up_is_stable_and_says_it_is_sampled(document: str) -> None:
    scorer = knn.load(document)
    for v in _vectors(30, 2):
        first, second = scorer.explain(v), scorer.explain(v)
        assert first == second
        assert first.basis == BASIS_SHAPLEY_SAMPLED
        total = first.base_value + sum(t.contribution for t in first.terms)
        assert math.isclose(total, first.score, abs_tol=1e-9)
        assert first.score == scorer.logit(v)
        assert first.linked == (first.score > first.threshold)


def test_an_uninformative_feature_is_left_out_of_the_metric() -> None:
    """``league_data`` builds its target from four features and leaves the rest at zero; a learned
    metric keeps only features that vary and inform, and the plain one keeps every varying one."""
    learned = json.loads(knn_fit.fit(TRAIN, VALID, KnnParams(prototypes=64, seed=1)).document)
    plain = json.loads(
        knn_fit.fit(TRAIN, VALID, KnnParams(prototypes=64, metric_power=0.0, seed=1)).document
    )
    assert "dt" in learned["features"] and "same_ne" in learned["features"]
    assert set(learned["features"]) <= set(plain["features"])
    assert "severity" not in plain["features"], "a constant feature reached the distance"


def test_the_fit_is_deterministic_and_k_comes_from_validation() -> None:
    params = KnnParams(prototypes=64, weighting="distance", seed=4)
    first, second = knn_fit.fit(TRAIN, VALID, params), knn_fit.fit(TRAIN, VALID, params)
    assert first.document == second.document
    assert first.points == [k for k in knn_fit.K_CURVE if k <= 64]
    best = first.points[first.valid_trace.index(min(first.valid_trace))]
    assert json.loads(first.document)["k"] == best == first.best
