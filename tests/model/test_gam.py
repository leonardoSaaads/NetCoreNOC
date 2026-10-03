"""The `gam` kind: the document is data, validated before use; the fit is deterministic."""

from __future__ import annotations

import json
import pickle
import random
from typing import Any

import pytest

from netcorenoc.engine.correlate.features import FEATURE_NAMES
from netcorenoc.engine.model import gam, gam_fit, model_version, search
from netcorenoc.engine.model.gam import GamDocumentError

GOOD: dict[str, Any] = {
    "features": ["dt", "same_ne"],
    "format": gam.FORMAT,
    "grouping": {"join_bias": 0.0, "merge_bias": 1.0, "merge_min_pairs": 3.0},
    "interactions": [],
    "intercept": -0.5,
    "shapes": [
        {"edges": [5.0, 60.0], "feature": "dt", "scores": [2.0, 0.5, -1.5]},
        {"edges": [0.5], "feature": "same_ne", "scores": [-1.0, 1.0]},
    ],
    "threshold": 0.0,
}


def _doc(**changes: object) -> str:
    return json.dumps({**GOOD, **changes}, sort_keys=True, separators=(",", ":"))


def _vector(**named: float) -> tuple[float, ...]:
    return tuple(named.get(n, 0.0) for n in FEATURE_NAMES)


def test_a_valid_document_scores_and_its_terms_sum_exactly() -> None:
    scorer = gam.load(_doc())
    v = _vector(dt=2.0, same_ne=1.0)
    result = scorer.explain(v)
    assert result.score == scorer.logit(v) == -0.5 + 2.0 + 1.0
    assert sum(t.contribution for t in result.terms) + result.base_value == result.score
    assert [t.name for t in result.terms] == ["dt", "same_ne"]
    assert result.basis == gam.BASIS_SHAPE and result.linked


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"extra": 1}, "exactly the keys"),
        ({"format": "pickle"}, "format"),
        ({"features": ["dt", "device_id"]}, "unknown feature"),
        ({"intercept": 99.0}, "magnitude"),
        ({"threshold": 50.0}, "attainable logit range"),
        (
            {
                "shapes": [
                    {"edges": [60.0, 5.0], "feature": "dt", "scores": [0, 0, 0]},
                    GOOD["shapes"][1],
                ]
            },
            "increasing",
        ),
        (
            {
                "shapes": [
                    {"edges": [5.0], "feature": "dt", "scores": [30.0, 0.0]},
                    GOOD["shapes"][1],
                ]
            },
            "hard switch",
        ),
        (
            {
                "shapes": [
                    {"edges": ["__import__('os')"], "feature": "dt", "scores": [0, 0]},
                    GOOD["shapes"][1],
                ]
            },
            "number",
        ),
    ],
)
def test_malformed_documents_are_refused(change: dict[str, object], reason: str) -> None:
    with pytest.raises(GamDocumentError, match=reason):
        gam.validate(_doc(**change))


def test_non_json_and_executable_content_is_refused() -> None:
    """Part VI.3: a model file is data. A pickle, or JSON smuggling a non-finite constant, is
    not."""
    blob = pickle.dumps({"shapes": []}).decode("latin-1")
    with pytest.raises(GamDocumentError, match="not valid JSON"):
        gam.validate(blob)
    with pytest.raises(GamDocumentError, match="non-finite"):
        gam.validate(_doc().replace('"intercept":-0.5', '"intercept":NaN'))
    with pytest.raises(GamDocumentError, match="exceeds"):
        gam.validate(" " * (gam.MAX_DOCUMENT_BYTES + 1))


def test_the_dispatch_validates_the_gam_kind() -> None:
    scorer = model_version.scorer_for(gam.KIND, "1.0", _doc())
    assert isinstance(scorer, gam.GamScorer)
    with pytest.raises(model_version.ModelPayloadError):
        model_version.scorer_for(gam.KIND, "1.0", _doc(extra=1))


def _toy(n: int, seed: int) -> gam_fit.Dataset:
    rng = random.Random(seed)
    rows, y = [], []
    for _ in range(n):
        dt = rng.uniform(0, 600)
        same = float(rng.random() < 0.5)
        logit = 2.0 - dt / 100.0 + 1.5 * same
        rows.append((dt, same))
        y.append(1 if rng.random() < 1 / (1 + 2.718281828**-logit) else 0)
    return gam_fit.Dataset.from_rows(("dt", "same_ne"), rows, y)


def test_the_fit_is_deterministic_and_learns_the_shape() -> None:
    train, valid = _toy(3000, 1), _toy(1000, 2)
    params = gam_fit.FitParams(rounds=80, subsample=0.7, seed=3, interactions=1)
    a = gam_fit.fit(train, valid, params)
    b = gam_fit.fit(train, valid, params)
    assert a.document == b.document, "same rows, same parameters, same bytes"
    c = gam_fit.fit(
        train, valid, gam_fit.FitParams(rounds=80, subsample=0.7, seed=4, interactions=1)
    )
    assert c.document != a.document, "the seed is what varies the subsample"
    scorer = gam.load(a.document)
    near = scorer.logit(_vector(dt=10.0, same_ne=1.0))
    far = scorer.logit(_vector(dt=550.0, same_ne=0.0))
    assert near > 1.0 > -1.0 > far
    assert a.valid_trace and min(a.valid_trace) < a.valid_trace[0]


def test_terms_are_centred_on_the_training_rows() -> None:
    train = _toy(2000, 5)
    doc = json.loads(gam_fit.fit(train, None, gam_fit.FitParams(rounds=40, seed=1)).document)
    for shape in doc["shapes"]:
        col = train.columns[doc["features"].index(shape["feature"])]
        import bisect

        mean = sum(shape["scores"][bisect.bisect_right(shape["edges"], v)] for v in col) / len(col)
        assert abs(mean) < 1e-6


def test_a_warm_start_adds_to_its_base_model() -> None:
    train = _toy(2000, 6)
    base_doc = gam_fit.fit(train, None, gam_fit.FitParams(rounds=40, seed=1)).document
    base = gam.validate(base_doc)
    edges = {s.feature: s.edges for s in base.shapes}
    tuned = gam_fit.fit(train, None, gam_fit.FitParams(rounds=10, seed=1), edges=edges, base=base)
    assert gam.validate(tuned.document).features == base.features


def test_search_draws_are_pure_and_a_resumed_search_refits_nothing() -> None:
    assert search.draw(7, 3) == search.draw(7, 3) != search.draw(7, 4)
    train, valid = _toy(800, 1), _toy(300, 2)
    budget = search.Budget(trials=3, min_rounds=10, max_rounds=30, eta=3)
    first = search.Search(train, valid, budget, seed=1)
    trials = first.run()
    assert {t.rung for t in trials} == {0, 1}
    fitted: list[int] = []
    resumed = search.Search(
        train, valid, budget, seed=1, done=list(trials), on_trial=lambda t: fitted.append(t.index)
    )
    resumed.run()
    assert fitted == [], "every trial was already recorded"
    stopped = search.Search(train, valid, budget, seed=2, stop=lambda: True).run()
    assert stopped == []
    imp = search.importance(trials)
    assert all(0.0 <= v <= 1.0 for v in imp.values())
