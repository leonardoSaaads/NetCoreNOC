"""Classifier metrics (`engine/evaluation/model_metrics.py`): the numbers Part V asks for."""

from __future__ import annotations

import pytest

from netcorenoc.engine.evaluation import model_metrics as mm


def test_average_precision_perfect_random_and_baseline() -> None:
    y = [1, 0, 1, 0, 0, 0, 0, 0]
    assert mm.average_precision(y, [0.9, 0.1, 0.8, 0.2, 0.2, 0.1, 0.3, 0.05]) == 1.0
    constant = mm.average_precision(y, [0.5] * 8)
    assert constant == pytest.approx(0.25), "a model that knows nothing scores the positive rate"


def test_roc_auc_of_a_perfect_ranking_is_one() -> None:
    assert mm.roc_auc([1, 1, 0, 0], [0.9, 0.8, 0.2, 0.1]) == pytest.approx(1.0)
    assert mm.roc_auc([1, 1, 0, 0], [0.1, 0.2, 0.8, 0.9]) == pytest.approx(0.0)


def test_murphy_decomposition_is_exact_when_bins_hold_one_value() -> None:
    """BS = reliability - resolution + uncertainty, exactly, when each bin holds one forecast."""
    y = [1, 0, 1, 1, 0, 0, 1, 0, 0, 0]
    p = [0.85, 0.85, 0.85, 0.85, 0.15, 0.15, 0.15, 0.15, 0.15, 0.15]
    cal = mm.calibration(y, p)
    assert cal.brier == pytest.approx(cal.reliability - cal.resolution + cal.uncertainty)
    assert len(cal.bins) == 2


def test_confusion_at_a_threshold() -> None:
    c = mm.confusion([1, 1, 0, 0], [0.9, 0.4, 0.6, 0.1], 0.5)
    assert (c.tp, c.fp, c.tn, c.fn) == (1.0, 1.0, 1.0, 1.0)
    assert c.precision == c.recall == c.f1 == 0.5


def test_the_cluster_bootstrap_resamples_clusters_and_is_seeded() -> None:
    clusters = [[0, 1], [2, 3], [4, 5], [6, 7]]
    values = [1, 1, 0, 0, 1, 0, 1, 1]

    def mean(idx: list[int] | tuple[int, ...]) -> float:
        return sum(values[i] for i in idx) / len(idx)

    a = mm.cluster_bootstrap(clusters, mean, replicates=100, seed=3)
    b = mm.cluster_bootstrap(clusters, mean, replicates=100, seed=3)
    assert a == b
    assert a.low <= a.point <= a.high and a.n == 4
    assert mm.cluster_bootstrap([[0]], mean).low == mm.cluster_bootstrap([[0]], mean).point
