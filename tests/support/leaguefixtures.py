"""Small model inputs shared by the league tests and the Judge screen's DOM tests: a seeded
dataset and a valid decision-tree document with overridable fields."""

from __future__ import annotations

import json
import math
import random
from typing import Any

from netcorenoc.engine.correlate.features import FEATURE_NAMES
from netcorenoc.engine.model import (
    trees,
)
from netcorenoc.engine.model.gam_data import Dataset


def league_data(n: int, seed: int) -> Dataset:
    rng = random.Random(seed)
    rows, ys = [], []
    for _ in range(n):
        x = [0.0] * len(FEATURE_NAMES)
        x[0] = rng.expovariate(1 / 30)  # dt
        x[1] = float(rng.random() < 0.5)  # same_ne
        x[2] = float(rng.random() < 0.3)  # same_class
        x[5] = rng.random()  # entity_affinity
        x[10] = float(rng.randint(1, 400))  # burst
        z = -1 + 2 * x[1] + 1.5 * x[2] - 0.05 * x[0] + 2 * x[5] * x[1] - 0.002 * x[10]
        ys.append(1 if rng.random() < 1 / (1 + math.exp(-z)) else 0)
        rows.append(x)
    return Dataset.from_rows(FEATURE_NAMES, rows, ys, [0.5] * n)


def tree_doc(**changes: Any) -> str:
    doc: dict[str, Any] = {
        "format": trees.FORMAT,
        "method": "decision_tree",
        "features": ["dt", "same_ne"],
        "reference": [10.0, 0.0],
        "base": 0.0,
        "scale": 1.0,
        "trees": [[[1, 0.5, 1, 2, 0.0], [-1, 0.0, 0, 0, -2.0], [-1, 0.0, 0, 0, 2.0]]],
        "threshold": 0.0,
        "grouping": {"join_bias": 0.0, "merge_bias": 1.0, "merge_min_pairs": 3.0},
    }
    doc.update(changes)
    return json.dumps(doc)
