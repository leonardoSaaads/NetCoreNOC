"""The `xgboost` league member (v0.29.0, ADR #438): the XGBoost algorithm, and only what it claims.

Each test reads one property of the regularised objective back out of a fit, against a control fit
that differs in that one parameter — so a parameter that silently did nothing would fail here:

* ``gamma`` prunes: a larger minimum split loss leaves fewer nodes;
* ``reg_alpha`` thresholds: a large L1 penalty zeroes the weakest leaves;
* ``max_delta_step`` clips: no leaf, before shrinkage, exceeds it;
* ``min_child_weight`` refuses light children;
* the fit is deterministic, early-stops on validation, and is served by `trees.py` as any tree is.
"""

from __future__ import annotations

import json

from netcorenoc.engine.model import league, trees, xgb_fit
from netcorenoc.engine.model.xgb_fit import XGBParams

from leaguefixtures import league_data

TRAIN, VALID = league_data(2500, 11), league_data(800, 12)


def _fit(**changes: object) -> trees.TreesModel:
    params = XGBParams(**{"max_depth": 4, "rounds": 25, "eta": 0.3, "seed": 5, **changes})  # type: ignore[arg-type]
    return trees.validate(xgb_fit.fit(TRAIN, VALID, params).document)


def _nodes(model: trees.TreesModel) -> int:
    return sum(len(t.feature) for t in model.trees)


def _leaves(model: trees.TreesModel) -> list[float]:
    return [
        t.value[i] for t in model.trees for i in range(len(t.feature)) if t.feature[i] == trees.LEAF
    ]


def test_the_document_is_a_trees_document_the_league_serves() -> None:
    model = _fit()
    assert model.method == "xgboost"
    assert league.KINDS["xgboost"][0] == "XGBoost"
    document = xgb_fit.fit(TRAIN, VALID, XGBParams(max_depth=3, rounds=10, seed=1)).document
    scorer = league.KINDS["xgboost"][1](document, "xgboost")
    assert scorer.model.method == "xgboost"


def test_gamma_prunes_splits_below_the_minimum_loss() -> None:
    loose, strict = _fit(gamma=0.0), _fit(gamma=0.05)
    assert _nodes(strict) < _nodes(loose), (_nodes(strict), _nodes(loose))


def test_reg_alpha_zeroes_the_weakest_leaves() -> None:
    plain, sparse = _fit(reg_alpha=0.0), _fit(reg_alpha=0.05)
    zeros_plain = sum(1 for v in _leaves(plain) if v == 0.0)
    zeros_sparse = sum(1 for v in _leaves(sparse) if v == 0.0)
    assert zeros_sparse > zeros_plain


def _raw_leaves(**changes: object) -> list[float]:
    """Leaf values straight from the fitted document — a clip this tight leaves a model that cannot
    reach its threshold in 25 rounds, which `trees.validate` rightly refuses to serve."""
    params = XGBParams(**{"max_depth": 4, "rounds": 25, "eta": 0.3, "seed": 5, **changes})  # type: ignore[arg-type]
    doc = json.loads(xgb_fit.fit(TRAIN, VALID, params).document)
    return [node[4] for tree in doc["trees"] for node in tree if node[0] == trees.LEAF]


def test_max_delta_step_bounds_every_leaf_before_shrinkage() -> None:
    eta, step = 0.3, 0.05
    clipped = _raw_leaves(eta=eta, max_delta_step=step, reg_lambda=1e-6)
    assert max(abs(v) for v in clipped) <= eta * step + 1e-9
    free = _raw_leaves(eta=eta, max_delta_step=0.0, reg_lambda=1e-6)
    assert max(abs(v) for v in free) > eta * step


def test_min_child_weight_refuses_light_children() -> None:
    light, heavy = _fit(min_child_weight=0.01), _fit(min_child_weight=50.0)
    assert _nodes(heavy) < _nodes(light)


def test_the_fit_is_deterministic_and_stops_on_validation() -> None:
    params = XGBParams(max_depth=5, rounds=200, eta=0.5, patience=10, subsample=0.7, seed=9)
    first = xgb_fit.fit(TRAIN, VALID, params)
    second = xgb_fit.fit(TRAIN, VALID, params)
    assert first.document == second.document
    assert first.best < 200, "early stopping never fired on a fast learning rate"
    assert len(trees.validate(first.document).trees) == first.best
