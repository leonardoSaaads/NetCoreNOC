"""Deterministic training rows for the v0.14.0 model kinds. **No RNG, no corpus, no store.**

Beside `uifixtures.py` and `apisource.py`, and for the same reason those exist: a fixture a
subprocess can import is what makes a *cross-process* determinism test possible without duplicating
the data-generating rule in a `-c` string, where the two copies would drift with nothing going red.

The target has real structure — near in time **and** affine implies link — so a tree has something
to find and both classes are present. Both matter: a fit on a constant target produces a single-leaf
tree, which T4 refuses, so a fixture without structure would test the refusal rather than the fit.
"""

from __future__ import annotations

from netcorenoc.challenger import feature_vector
from netcorenoc.training import TrainingRow

__all__ = ["DEFAULT_ROWS", "training_rows"]

DEFAULT_ROWS = 400


def training_rows(count: int = DEFAULT_ROWS) -> list[TrainingRow]:
    """`count` rows, a pure function of `count` and of nothing else.

    The three modular strides are coprime with each other and with `count`'s divisors, so the
    features do not fall into a lattice that a single split could separate perfectly — a fixture a
    depth-1 tree could fit exactly would make every depth hyperparameter untestable.
    """
    rows: list[TrainingRow] = []
    for index in range(count):
        delta = (index % 61) * 2.0  # 0 .. 120 s, the correlation window
        class_affinity = ((index * 37) % 101) / 100.0
        entity_affinity = ((index * 17) % 51) / 50.0
        linked = delta < 30.0 and class_affinity + entity_affinity > 0.8
        rows.append(
            TrainingRow(
                y=1.0 if linked else 0.0,
                weight=1.0 + (index % 5) * 0.05,
                x=feature_vector(delta, class_affinity, entity_affinity),
            )
        )
    return rows
