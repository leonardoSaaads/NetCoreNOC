"""The training rows of a `gam` fit, column-major: what `gam_fit`, the search and the site
adaptation all consume.

Split out of `gam_fit.py` in v0.26.0 at the 400-line module guard; `gam_fit` re-exports it by
identity, so `gam_fit.Dataset` still names this class. Column-major ``array`` storage because a
boosting round reads one feature's column for every row, and a list of row tuples would touch
every row's every feature to do it.
"""

from __future__ import annotations

from array import array
from collections.abc import Sequence
from dataclasses import dataclass

__all__ = ["Dataset"]


@dataclass
class Dataset:
    """Training rows, column-major. ``init`` is an optional starting logit per row (warm start)."""

    features: tuple[str, ...]
    columns: list[array[float]]
    y: array[int]
    w: array[float]
    init: array[float] | None = None

    def __len__(self) -> int:
        return len(self.y)

    @classmethod
    def from_rows(
        cls,
        features: Sequence[str],
        rows: Sequence[Sequence[float]],
        y: Sequence[int],
        w: Sequence[float] | None = None,
        columns: Sequence[int] | None = None,
    ) -> Dataset:
        """Build from row-major data. ``columns`` picks which positions of each row to keep."""
        picks = list(columns) if columns is not None else list(range(len(features)))
        cols = [array("d", (float(r[j]) for r in rows)) for j in picks]
        weights = array("d", (float(v) for v in w)) if w is not None else array("d", [1.0] * len(y))
        return cls(tuple(features), cols, array("b", (int(v) for v in y)), weights)

    def subset(self, index: Sequence[int]) -> Dataset:
        return Dataset(
            self.features,
            [array("d", (col[i] for i in index)) for col in self.columns],
            array("b", (self.y[i] for i in index)),
            array("d", (self.w[i] for i in index)),
            None if self.init is None else array("d", (self.init[i] for i in index)),
        )
