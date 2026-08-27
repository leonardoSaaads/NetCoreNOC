"""Exact marginal Shapley attribution for piecewise-constant scorers — **one module, three kinds.**

`PREREGISTRATION-0.14.0.md` §3, registered before any tree existed:

> **exact marginal (interventional) Shapley values over the three features, computed by enumeration
> of all `2**3 = 8` coalitions, against a fixed background set.**

## Why a tree needs this at all

`AdditiveScorer` and `LogisticScorer` are weighted sums, so a per-term contribution is *what they
already compute*. **A tree predicts a leaf value, not a weighted sum.** There is no weight to
report, and `LinkScore.terms` is contractual: the admission filter asserts the contributions add up,
and the operator-facing EXPLAIN is a product promise. A plausible-looking attribution — "the feature
that split at the root matters most" — would be an artefact of tree structure wearing an
explanation's clothes.

**Marginal rather than path-dependent** (ADR #190): path-dependent TreeSHAP is a biased estimator of
the conditional values, so two trees computing the **same function** can receive different feature
rankings under it. An answer that differs between two models that decide identically is not an
answer about the decision.

## The arithmetic

For a coalition `S` of the three features,

    v(S)(x) = mean over the background B of  f( x on S, z on the complement of S )

with `v(empty)` the model's mean output over `B` — the **base value** — and `v(all)` the model's
output at `x`. Then

    phi_i = sum over S not containing i of  W[|S|] * ( v(S + i) - v(S) ),   W = (1/3, 1/6, 1/3)

and the three sum to `v(all) - v(empty)`: to `score - base_value`, **never to `score`**. That is
registered in the plan's §3.1 and is why the admission check is
`sum(contributions) + base_value == score`.

## What makes it cheap enough for the ingest path

Naively `6 * |B|` model evaluations per pair — about 1 500 — and Gate 0 premise 1 measured that the
scorer runs per candidate pair on the ingest path, up to `MAX_CANDIDATES` (100) per activation. Two
**exact** identities collapse it (ADR #190): Shapley is linear in the model, so work is per tree;
and a tree is constant on the cells its own thresholds cut, so each cell's three contributions are
computed once, at construction, and scoring is three binary searches and a tuple read. The
background enters through one precomputed number per (leaf, coalition) — the fraction of background
rows satisfying that leaf's constraints on the features *not* in `S`.

**The plan's own cost sentence is wrong by two orders of magnitude** (it says eight evaluations).
The plan is ratified and is not edited; the discrepancy goes to `SECURITY-REVIEW-0.14.0.md` as an
opinion for v0.15.0, where its §10 directs one. The **method** here is the registered one,
exactly.

## Purity

Every function is pure in its arguments and in `background.BACKGROUND`. No store, no clock, no
filesystem, no RNG, **no cache** — which is what makes `shadow_admission`'s `memory_stable` check
hold: an :class:`Explainer` is built once and never mutated.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass

from netcorenoc.engine.model.background import BACKGROUND
from netcorenoc.scorer_contract import (
    BASIS_SHAPLEY,
    FEATURE_NAMES,
    LinkFeatures,
    LinkScore,
    TermContribution,
    feature_vector,
)

__all__ = [
    "FEATURE_COUNT",
    "MAX_CELLS_PER_TREE",
    "AttributedScorer",
    "AttributionError",
    "Explainer",
    "Leaf",
    "build",
]

# `challenger.FEATURE_NAMES` has three entries. Enumerating `2**3` coalitions is what makes an exact
# Shapley value affordable at all, and it is stated as a constant here so that a fourth feature
# breaks loudly rather than silently changing what "exact" costs.
FEATURE_COUNT = 3
_FULL = (1 << FEATURE_COUNT) - 1

# W[|S|] = |S|! (n - |S| - 1)! / n!  for n = 3 and S ranging over subsets of N \ {i}.
_SHAPLEY_WEIGHT = (1.0 / 3.0, 1.0 / 6.0, 1.0 / 3.0)

# A structural limit, **not a degeneracy rule** — the plan registers no bound on tree size and this
# module may not invent one. It is the same kind of statement `SUPPORTED_KINDS` makes: *this build
# does not understand that payload*. A tree whose thresholds cut more cells than this cannot have
# its table precomputed in bounded time, and the alternative — a loop over leaves at score time —
# put a loop over leaves on the ingest path. The bound is generous: a complete depth-4 tree cuts at
# most 15 thresholds across three features, and the worst product under that constraint is 6·6·6.
MAX_CELLS_PER_TREE = 4096

# `(value, box)` for one leaf. `box[f] = (lo, hi)` means `lo < x[f] <= hi`, matching the split rule
# (a node sends `x[f] <= threshold` left), with `-inf` / `+inf` for an unconstrained side.
Leaf = tuple[float, tuple[tuple[float, float], ...]]


class AttributionError(ValueError):
    """A model this build cannot precompute an exact attribution table for.

    Raised only by :func:`build`, and only for `MAX_CELLS_PER_TREE`. Callers turn it into a
    `ModelPayloadError`, so it reaches the load path through the one door every other rejection
    reason uses and fails safe to the built-in default.
    """


class _Table:
    """One member tree's precomputed grid. **Immutable after construction.**

    `cells[i]` is `(weighted φ₀, weighted φ₁, weighted φ₂)` for the cell whose index is `i`. The
    member's weight is folded in here rather than applied at score time, so scoring an ensemble is
    three additions per tree and nothing else.

    **The output is deliberately not stored.** A tree's contribution to the score is exactly the sum
    of its three contributions — `v(N) - v(∅)` — so storing it separately would give the score a
    second derivation, and two derivations of one number agree only until they do not. The admission
    filter compares `sum(contributions) + base_value == score` with `==` rather than a tolerance;
    that comparison is honest only if the score is *defined* as that sum.
    """

    __slots__ = ("cells", "cuts", "strides")

    def __init__(
        self,
        cuts: tuple[tuple[float, ...], ...],
        strides: tuple[int, ...],
        cells: tuple[tuple[float, float, float], ...],
    ) -> None:
        self.cuts = cuts
        self.strides = strides
        self.cells = cells


@dataclass(frozen=True)
class Explainer:
    """A model's attribution, precomputed. `explain()` is arithmetic and three binary searches.

    `base_value` is `v(∅)` for the whole model — the mean output over the background set — and it is
    what the three contributions are measured *from*. It is a property of (model, background), both
    fixed at registration, which is why it lives in `params_document` and enters `params_hash`: two
    models with the same trees and different base values would explain the same decision differently
    and must not share a fingerprint.
    """

    base_value: float
    tables: tuple[_Table, ...]

    def explain(self, x: Sequence[float]) -> tuple[float, tuple[float, float, float]]:
        """`(score, (φ₀, φ₁, φ₂))`, with `sum(φ) + base_value == score` **exactly**.

        "Exactly" is meant in the float sense the rest of this project means it: the same additions
        in the same order on every call and in every process, so two runs agree bit for bit. The
        order below is `(φ₀ + φ₁) + φ₂` and then `base_value`, which is the order
        `shadow_admission.admission` accumulates its check in — float addition is commutative, so
        `base_value + s` and `s + base_value` are the same bits, and the two agree by construction
        rather than by luck.

        The score therefore differs from a direct walk of the trees by at most a last-bit rounding,
        and it is this value — not the walk — that decides `linked`, so the decision and the
        explanation cannot drift apart. That is `challenger.py`'s rule for the logistic kind
        (*"two quantities, each in the place it is correct, rather than one quantity that is
        slightly wrong in both"*) applied where the arithmetic is a sum of leaf deltas.
        """
        first = second = third = 0.0
        for table in self.tables:
            cuts, strides = table.cuts, table.strides
            phi_0, phi_1, phi_2 = table.cells[
                bisect_left(cuts[0], x[0]) * strides[0]
                + bisect_left(cuts[1], x[1]) * strides[1]
                + bisect_left(cuts[2], x[2])
            ]
            first += phi_0
            second += phi_1
            third += phi_2
        return (first + second + third) + self.base_value, (first, second, third)


def build(members: Sequence[tuple[float, Sequence[Leaf]]], base_score: float = 0.0) -> Explainer:
    """Precompute the attribution for a weighted sum of trees. **Pure, deterministic, no RNG.**

    `members` is `(weight, leaves)` per tree — a forest passes `1/n` for each, a boosted model
    passes its shrinkage, and a single tree passes `1.0`. `base_score` is the constant the ensemble
    adds
    before any tree (0 for a tree or a forest, the initial prediction for a boosted model); a
    constant shifts every `v(S)` equally, so it contributes nothing to any `φ` and only moves the
    base value.
    """
    tables: list[_Table] = []
    base_value = base_score
    for weight, leaves in members:
        table, member_base = _table_for(leaves, weight)
        tables.append(table)
        base_value += member_base
    return Explainer(base_value=base_value, tables=tuple(tables))


def _table_for(leaves: Sequence[Leaf], weight: float) -> tuple[_Table, float]:
    """One tree's grid and its weighted base value."""
    cuts = tuple(_cuts_for(leaves, feature) for feature in range(FEATURE_COUNT))
    sizes = tuple(len(column) + 1 for column in cuts)
    total_cells = sizes[0] * sizes[1] * sizes[2]
    if total_cells > MAX_CELLS_PER_TREE:
        raise AttributionError(
            f"this tree's thresholds cut {total_cells} cells, beyond the {MAX_CELLS_PER_TREE} this "
            "build precomputes an exact attribution table for; the payload describes a model this "
            "build cannot explain, and a scorer that cannot decompose its own decision is not one "
            "this project runs"
        )
    strides = (sizes[1] * sizes[2], sizes[2], 1)

    # Per leaf: its box in CELL-INDEX form, and the eight background fractions.
    bounds = [_index_box(box, cuts) for _value, box in leaves]
    values = [value for value, _box in leaves]
    fractions = [_background_fractions(box) for _value, box in leaves]

    cells: list[tuple[float, float, float]] = []
    # `v(∅)` does not depend on the cell: the empty coalition takes every coordinate from the
    # background, so it is the model's mean output and is computed once.
    member_base = sum(
        value * fraction[0] for value, fraction in zip(values, fractions, strict=True)
    )
    for first in range(sizes[0]):
        for second in range(sizes[1]):
            for third in range(sizes[2]):
                masks = [_cell_mask(bound, (first, second, third)) for bound in bounds]
                coalition = [
                    sum(
                        value * fraction[subset]
                        for value, fraction, mask in zip(values, fractions, masks, strict=True)
                        if (mask & subset) == subset
                    )
                    for subset in range(_FULL + 1)
                ]
                phi = _shapley(coalition)
                cells.append((weight * phi[0], weight * phi[1], weight * phi[2]))
    return _Table(cuts, strides, tuple(cells)), weight * member_base


def _shapley(coalition: Sequence[float]) -> tuple[float, float, float]:
    """The three exact marginal Shapley values from the eight coalition values.

    Enumeration, not a formula to check: every `S ⊆ N \\ {i}` contributes
    `W[|S|] · (v(S  union  {i}) - v(S))`, and the loop below *is* that sum. Their total is
    `v(N) - v(∅)` by construction rather than by arrangement.
    """
    out: list[float] = []
    for feature in range(FEATURE_COUNT):
        bit = 1 << feature
        value = 0.0
        for subset in range(_FULL + 1):
            if subset & bit:
                continue
            size = bin(subset).count("1")
            value += _SHAPLEY_WEIGHT[size] * (coalition[subset | bit] - coalition[subset])
        out.append(value)
    return (out[0], out[1], out[2])


def _cuts_for(leaves: Sequence[Leaf], feature: int) -> tuple[float, ...]:
    """Every finite threshold this tree uses on one feature, ascending and deduplicated."""
    seen: set[float] = set()
    for _value, box in leaves:
        low, high = box[feature]
        if math.isfinite(low):
            seen.add(low)
        if math.isfinite(high):
            seen.add(high)
    return tuple(sorted(seen))


def _index_box(
    box: tuple[tuple[float, float], ...], cuts: tuple[tuple[float, ...], ...]
) -> tuple[tuple[int, int], ...]:
    """A leaf's box as `(low index, high index)` per feature, in cell-index space.

    With `cell(x) = |{c in cuts : c < x}|`, the two membership tests become integer comparisons:
    `x <= cuts[j]` is `cell <= j`, and `cuts[i] < x` is `cell > i`. An unconstrained low side is
    `-1` (every cell is above it) and an unconstrained high side is `len(cuts)` (every cell is at or
    below it).
    """
    out: list[tuple[int, int]] = []
    for feature in range(FEATURE_COUNT):
        low, high = box[feature]
        column = cuts[feature]
        low_index = -1 if not math.isfinite(low) else bisect_left(column, low)
        high_index = len(column) if not math.isfinite(high) else bisect_left(column, high)
        out.append((low_index, high_index))
    return tuple(out)


def _cell_mask(bound: tuple[tuple[int, int], ...], cell: tuple[int, int, int]) -> int:
    """Which features of this cell satisfy the leaf's constraints, as a bitmask."""
    mask = 0
    for feature in range(FEATURE_COUNT):
        low_index, high_index = bound[feature]
        if low_index < cell[feature] <= high_index:
            mask |= 1 << feature
    return mask


def _background_fractions(box: tuple[tuple[float, float], ...]) -> tuple[float, ...]:
    """`q_S(L)` for all eight `S`: the fraction of background rows inside the leaf **off** `S`.

    Computed by counting each background row's own satisfaction mask once — three range checks —
    and then summing the counts whose mask covers the complement of `S`. That is `8 · |B|` cheap
    integer operations per leaf instead of `8 · |B|` model evaluations, and it is the same number.
    """
    counts = [0] * (_FULL + 1)
    for row in BACKGROUND:
        mask = 0
        for feature in range(FEATURE_COUNT):
            low, high = box[feature]
            if low < row[feature] <= high:
                mask |= 1 << feature
        counts[mask] += 1
    size = float(len(BACKGROUND))
    out: list[float] = []
    for subset in range(_FULL + 1):
        complement = _FULL ^ subset
        out.append(
            sum(count for mask, count in enumerate(counts) if (mask & complement) == complement)
            / size
        )
    return tuple(out)


@dataclass(frozen=True)
class AttributedScorer:
    """A `LinkScorer` whose terms are exact Shapley values. **Three kinds, one class.**

    `tree`, `forest` and `gradient_boosting` differ in how a document becomes an :class:`Explainer`
    and in nothing after that, so there is no per-kind scorer and no per-kind attribution — the
    property Gate 3 asserts rather than describes. Frozen and stateless: `score()` reads only
    its own
    precomputed tables and the features it is handed, so it is pure, deterministic and
    inference-only, exactly as `AdditiveScorer` and `LogisticScorer` are.

    **Memory is bounded and does not grow**: the tables are built once and never written to, no
    cache, no accumulator. `shadow_admission`'s `memory_stable` check asserts it rather than
    trusting this sentence.

    It carries its own `params_document` and `fingerprint` rather than deriving them, so that
    `model_version.params_hash` stays the one place an artefact is fingerprinted and
    `document_for(scorer_for(document))` is an identity rather than a re-serialisation.
    """

    explainer: Explainer
    threshold: float
    scorer_id: str
    contract_version: str
    params_document: str
    fingerprint: str

    @property
    def base_value(self) -> float:
        """What the contributions are measured **from** — the model's mean output over the
        registered background set. In `params_document`, and therefore in `params_hash`."""
        return self.explainer.base_value

    def score(self, features: LinkFeatures) -> LinkScore:
        """The score, its verdict, and one exact Shapley contribution per feature.

        The feature vector is built by `challenger.feature_vector` — **the one place a feature
        vector is built** — so training, offline reconstruction and this online path cannot disagree
        about what a feature is. That is what makes the skew test a real test rather than a
        tautology.

        `weight` is `0.0` on every term and means nothing; `basis` says so. A Shapley value in a
        field named `weight` would be a lie in the field's own name (DECISIONS #186).
        """
        values = feature_vector(
            features.delta_t_s, features.class_affinity, features.entity_affinity
        )
        total, contributions = self.explainer.explain(values)
        return LinkScore(
            linked=total > self.threshold,
            score=total,
            threshold=self.threshold,
            terms=tuple(
                TermContribution(name, 0.0, value, contribution)
                for name, value, contribution in zip(
                    FEATURE_NAMES, values, contributions, strict=True
                )
            ),
            basis=BASIS_SHAPLEY,
            base_value=self.explainer.base_value,
        )

    def params_fingerprint(self) -> str:
        return self.fingerprint
