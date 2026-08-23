"""The `forest` kind: bagging over `tree`, and **the only kind in this project with a seed.**

v0.14.0. `PREREGISTRATION-0.14.0.md` §2.2 fixes F1-F4 before any fit existed.

## The seed is an ADR, not a footnote (DECISIONS #188)

v0.9.0's directive 5 permits *"no RNG — or a fixed seed, argued in an ADR."* Here is the argument,
and the three properties that make it hold:

1. **The seed is in `params_document`, and therefore in `params_hash`.** Two forests grown from the
   same rows with different seeds are different models; if the seed were outside the document they
   would share a fingerprint, and v0.11.0's provenance would be fiction — the exact failure
   `UI-0.13-DRAFT.md` §8 names.
2. **The draw is a pure function, not a stream.** There is no generator object and no state to
   advance: row `j` of tree `t`'s bag is `_draw(seed, t, j, n)`, a hash. So the bag does not depend
   on how many times anything was called before it, which is the property an LCG-with-state does not
   have and the reason a resumed or reordered fit cannot silently differ.
3. **The draw order is documented and asserted.** Tree `t` draws `n` indices for `j = 0 … n-1`, in
   that order, with replacement, from `[0, n)`. Then, if `mtry < 3`, it draws its feature subset.
   `tests/test_forest.py` asserts both halves of the seed's contract: same seed → byte-identical
   trees across two processes; **different seed → different trees**. Only the pair proves the seed
   is doing anything.

## What `mtry` can and cannot do here, said on the screen as well as in this docstring

With **three** features, `mtry` draws one or two of three, so a member tree sees at most two thirds
of a feature space that is already small. **Almost all of a forest's diversity here comes from
bagging, not from feature subsampling**, and a forest on this feature set is close to a bagged tree.
An operator choosing `forest` over `tree` should be told that rather than left to infer it, which is
why the console prints it beside the control rather than burying it here.

`mtry` is drawn **per tree** rather than per node. Per-node is the classic formulation, and it would
put a draw inside the split search — which `cart.py` is deliberately free of, so that the search is
a pure function of its rows. Per-tree keeps the whole of the randomness in this module, where the
seed is, and the cost is diversity this feature space cannot supply anyway.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from netcorenoc import attribution, tree
from netcorenoc.cart import Node, extremes, fit
from netcorenoc.training import TrainingRow

__all__ = [
    "DEFAULT_MTRY",
    "DEFAULT_N_ESTIMATORS",
    "KEYS",
    "KIND",
    "MIN_ESTIMATORS",
    "build_scorer",
    "fit_document",
    "scorer_from_payload",
    "validate",
    "validate_payload",
]

KIND = "forest"

# fmt: off
KEYS = ("base_value", "max_depth", "min_samples_leaf", "mtry", "n_estimators", "seed", "threshold",
        "trees")
# fmt: on

# **F2's floor as a constant.** A forest of one is a tree with a misleading name, and a name that
# misleads about what a model is would reach the audit log.
MIN_ESTIMATORS = 2
DEFAULT_N_ESTIMATORS = 8
# All three features by default. `mtry = 3` is "no feature subsampling", which is the honest default
# on a three-dimensional feature space — see the module docstring.
DEFAULT_MTRY = 3

_MASK64 = (1 << 64) - 1


def _draw(seed: int, tree_index: int, position: int, bound: int) -> int:
    """The `position`-th row index of tree `tree_index`'s bag. **A pure function of the seed.**

    A SplitMix64 finaliser over the three inputs. Deliberately not a linear congruential generator:
    `shadow_cv._Lcg` records what a power-of-two-modulus LCG's low bits cost this project once — a
    two-cluster bootstrap that alternated 0, 1, 0, 1 and reported a zero-width interval, *"a
    bootstrap reporting perfect precision because it never resampled anything"*. A finaliser has no
    such structure in any bit range, and the high half is taken anyway, which is the same belt that
    lesson bought.
    """
    z = (
        seed * 0x9E3779B97F4A7C15 + tree_index * 0xBF58476D1CE4E5B9 + position * 0x94D049BB133111EB
    ) & _MASK64
    z ^= z >> 30
    z = (z * 0xBF58476D1CE4E5B9) & _MASK64
    z ^= z >> 27
    z = (z * 0x94D049BB133111EB) & _MASK64
    z ^= z >> 31
    return (z >> 32) % bound


def _features_for(seed: int, tree_index: int, mtry: int) -> list[int]:
    """Tree `tree_index`'s feature subset: `mtry` of the three, **ascending**, without replacement.

    Ascending because `cart._best_split`'s tie-break is "lowest feature index first", and a subset
    handed over in draw order would make the tie-break depend on the draw.
    """
    if mtry >= attribution.FEATURE_COUNT:
        return list(range(attribution.FEATURE_COUNT))
    pool = list(range(attribution.FEATURE_COUNT))
    chosen: list[int] = []
    for position in range(mtry):
        chosen.append(pool.pop(_draw(seed, tree_index, 1_000 + position, len(pool))))
    return sorted(chosen)


async def _grow(
    rows: Sequence[TrainingRow],
    *,
    n_estimators: int,
    max_depth: int,
    min_samples_leaf: int,
    mtry: int,
    seed: int,
) -> list[tuple[Node, ...]]:
    """Every member tree, in index order. Bag `t` is drawn before tree `t` is grown."""
    count = len(rows)
    grown: list[tuple[Node, ...]] = []
    for index in range(n_estimators):
        bag = [_draw(seed, index, position, count) for position in range(count)] if count else []
        grown.append(
            await fit(
                rows,
                max_depth=max_depth,
                min_samples_leaf=min_samples_leaf,
                subset=bag,
                features=_features_for(seed, index, mtry),
            )
        )
    return grown


def _members(grown: Sequence[Sequence[Node]]) -> list[tuple[float, Sequence[Node]]]:
    """The ensemble as `(weight, nodes)` pairs: a forest averages, so every weight is `1/n`."""
    weight = 1.0 / len(grown)
    return [(weight, nodes) for nodes in grown]


def validate(
    grown: Sequence[Sequence[Node]],
    params: dict[str, Any],
    *,
    threshold: float,
    max_abs: float,
    bounds: Sequence[tuple[float, float]],
) -> None:
    """**F1-F4**, plus T1-T3 and T6 on every member. Raises `tree.TreePayloadError`.

    Reachability is applied to the **averaged** output and to no member (F3). A forest whose
    individual trees each sat on one side of the threshold can still discriminate once averaged, so
    applying T5 per member would refuse models the plan does not refuse — and a validator that
    refuses more than its rule is as wrong as one that refuses less.
    """
    seed = params.get("seed")
    if not isinstance(seed, int) or isinstance(seed, bool):
        raise tree.TreePayloadError(
            f"{KIND}: 'seed' must be an integer inside params_document, got {seed!r}. Two forests "
            "grown from the same rows with different seeds are different models; a seed outside "
            "the document would let them share a params_hash."
        )
    if len(grown) < MIN_ESTIMATORS:
        raise tree.TreePayloadError(
            f"{KIND}: n_estimators is {len(grown)}, below {MIN_ESTIMATORS}: a forest of one is a "
            "tree with a misleading name"
        )
    for index, nodes in enumerate(grown):
        tree.validate(
            nodes,
            threshold=threshold,
            max_abs=max_abs,
            bounds=bounds,
            label=f"{KIND} tree {index}",
            check_depth=False,
        )
    if len({tuple(nodes) for nodes in grown}) == 1:
        raise tree.TreePayloadError(
            f"{KIND}: every member tree is identical, so the bagging drew the same sample every "
            f"time — this is a tree that costs {len(grown)} times more"
        )
    low, high = extremes(_members(grown), 0.0, bounds)
    if high <= threshold:
        raise tree.TreePayloadError(
            f"{KIND}: the highest reachable averaged output is {high:.6f}, at or below the "
            f"threshold {threshold!r}: nothing would ever link and every alarm would be a singleton"
        )
    if low > threshold:
        raise tree.TreePayloadError(
            f"{KIND}: the lowest reachable averaged output is {low:.6f}, above the threshold "
            f"{threshold!r}: every candidate pair would cross it, collapsing every alarm into one "
            "situation"
        )


def build_scorer(
    grown: Sequence[Sequence[Node]],
    *,
    threshold: float,
    base_value: float,
    scorer_id: str,
    contract_version: str,
    document: str,
    fingerprint: str,
) -> attribution.AttributedScorer:
    """Assemble the scorer, recomputing the base value and refusing a document that disagrees."""
    explainer = attribution.build(
        [(weight, tree.leaves(nodes)) for weight, nodes in _members(grown)]
    )
    if explainer.base_value != base_value:
        raise tree.TreePayloadError(
            f"{KIND}: the document's base_value {base_value!r} is not the model's mean output over "
            f"the registered background set ({explainer.base_value!r})"
        )
    return attribution.AttributedScorer(
        explainer=explainer,
        threshold=threshold,
        scorer_id=scorer_id,
        contract_version=contract_version,
        params_document=document,
        fingerprint=fingerprint,
    )


async def fit_document(
    rows: Sequence[TrainingRow],
    *,
    n_estimators: int,
    max_depth: int,
    min_samples_leaf: int,
    mtry: int,
    seed: int,
    threshold: float,
) -> dict[str, Any]:
    """Fit a forest and return the `params_document` payload that fully describes it.

    **Every hyperparameter that changes the trained model is in the returned mapping**, the seed
    included, and therefore in `params_hash`.
    """
    grown = await _grow(
        rows,
        n_estimators=n_estimators,
        max_depth=max_depth,
        min_samples_leaf=min_samples_leaf,
        mtry=mtry,
        seed=seed,
    )
    explainer = attribution.build(
        [(weight, tree.leaves(nodes)) for weight, nodes in _members(grown)]
    )
    return {
        "base_value": explainer.base_value,
        "max_depth": max_depth,
        "min_samples_leaf": min_samples_leaf,
        "mtry": mtry,
        "n_estimators": n_estimators,
        "seed": seed,
        "threshold": threshold,
        "trees": [tree.document_payload(nodes) for nodes in grown],
    }


def validate_payload(
    payload: dict[str, Any], *, bounds: Sequence[tuple[float, float]], max_abs: float
) -> None:
    """The `forest` kind's whole document check: T1 on the scalars, then F1-F4 over the members."""
    tree.scalar_error(KIND, payload)
    validate(
        tree.trees_from_payload(payload["trees"], KIND),
        payload,
        threshold=float(payload["threshold"]),
        max_abs=max_abs,
        bounds=bounds,
    )


def scorer_from_payload(
    payload: dict[str, Any], *, contract_version: str, document: str, fingerprint: str
) -> attribution.AttributedScorer:
    """The `forest` kind's half of `model_version.scorer_for`."""
    return build_scorer(
        tree.trees_from_payload(payload["trees"], KIND),
        threshold=float(payload["threshold"]),
        base_value=float(payload["base_value"]),
        scorer_id=KIND,
        contract_version=contract_version,
        document=document,
        fingerprint=fingerprint,
    )
