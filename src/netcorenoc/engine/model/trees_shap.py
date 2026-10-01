"""The exact one-reference Shapley walk of one tree — the arithmetic `trees` explains with.

Split out of `trees.py` at the 400-line guard (v0.27.0). The method and its proof are in that
module's docstring; this is the walk: from the root, follow every branch that the explained pair
``x`` or the reference pair ``z`` can take, tracking per feature whether only ``x``, only ``z``, or
both satisfy the path so far, and at each reachable leaf credit its value to the features only
``x`` reached (set ``A``) and debit it from those only ``z`` reached (set ``B``) with the closed
form weights. Pure, allocation-light, and bounded by the tree's size.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - type-only
    from netcorenoc.engine.model.trees import Tree

__all__ = ["LEAF", "MAX_DEPTH", "walk"]

#: A leaf's feature index, and the deepest tree a document may hold — defined here, where the
#: walk needs them, and re-exported by `trees`, so each is written once.
LEAF = -1
MAX_DEPTH = 14


# Shapley weights for the one-reference game of a leaf: (a-1)! b! / (a+b)! and a! (b-1)! / (a+b)!.
def _weights(depth: int) -> tuple[tuple[tuple[float, float], ...], ...]:
    f = [math.factorial(k) for k in range(2 * depth + 2)]
    return tuple(
        tuple(
            (
                f[a - 1] * f[b] / f[a + b] if a else 0.0,
                f[a] * f[b - 1] / f[a + b] if b else 0.0,
            )
            for b in range(depth + 1)
        )
        for a in range(depth + 1)
    )


_W = _weights(MAX_DEPTH)


def walk(
    tree: Tree, x: Sequence[float], z: Sequence[float], phi: list[float], scale: float
) -> None:
    """Add one tree's exact one-reference Shapley values to ``phi`` (see `trees`)."""
    # (node, {local feature: (x satisfies, z satisfies)}) — only restricted features are kept.
    stack: list[tuple[int, dict[int, tuple[bool, bool]]]] = [(0, {})]
    while stack:
        i, state = stack.pop()
        f = tree.feature[i]
        if f == LEAF:
            a_set = [k for k, (xo, zo) in state.items() if xo and not zo]
            b_set = [k for k, (xo, zo) in state.items() if zo and not xo]
            a, b = len(a_set), len(b_set)
            if not a and not b:
                continue  # the same leaf for every coalition: part of the base value
            v = scale * tree.value[i]
            wa, wb = _W[a][b]
            for k in a_set:
                phi[k] += v * wa
            for k in b_set:
                phi[k] -= v * wb
            continue
        k = tree.local[i]
        x_left = x[f] <= tree.threshold[i]
        z_left = z[f] <= tree.threshold[i]
        xo, zo = state.get(k, (True, True))
        for child, x_goes, z_goes in (
            (tree.left[i], x_left, z_left),
            (tree.right[i], not x_left, not z_left),
        ):
            cx, cz = xo and x_goes, zo and z_goes
            if not cx and not cz:
                continue
            if (cx, cz) == (xo, zo):
                stack.append((child, state))
            else:
                stack.append((child, {**state, k: (cx, cz)}))
