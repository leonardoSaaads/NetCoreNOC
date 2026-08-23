"""The attribution module (v0.14.0, Phase 2).

`PREREGISTRATION-0.14.0.md` §3 registers **exact marginal (interventional) Shapley values over the
three features, computed by enumeration of all `2**3 = 8` coalitions, against a fixed background
set**,
before any tree existed.

Three things are asserted here and each of them is a different kind of claim:

1. **The background set has the registered shape.** Sorted, deduplicated, 256 rows, the stride the
   plan's rule implies, every row inside the declared feature bounds. That it is *the corpus's* is a
   stronger claim and this suite does not make it: regenerating it means replaying all ten scenarios
   through a real engine, which is a minute of work and belongs in a command rather than in a unit
   test. `eval/background_gen.py --check` is that command, and Gate 2 quotes its output.
2. **The attribution is exact.** Not "close to" a reference implementation — equal to the
   *definition*, computed by brute force over the whole background, to within float rounding.
   Appendix B's *"a guard that compares against the constant it guards"* is the trap here, so the
   brute force shares no arithmetic with `Explainer`: it walks the tree directly, builds the hybrid
   points by hand, and averages.
3. **The sum identity holds with `==`.** `shadow_admission` compares
   `sum(contributions) + base_value == score` without a tolerance, and that is only honest if the
   score is *defined* as that sum.
"""

from __future__ import annotations

import json
import math
import subprocess  # nosec B404 - runs this interpreter on a literal script, no shell, no input
import sys
from pathlib import Path

import pytest

from netcorenoc import attribution
from netcorenoc.background import BACKGROUND, BACKGROUND_STRIDE, CORPUS_DISTINCT_VECTORS
from netcorenoc.scorer_contract import BASIS_SHAPLEY, LinkFeatures

REPO_ROOT = Path(__file__).resolve().parent.parent
INF = math.inf

# A three-leaf tree: split on decay at 0.5, then on entity affinity at 0.5 on the right. Small
# enough that the brute force below is readable, and asymmetric enough that all three Shapley
# values differ.
LEAVES: list[attribution.Leaf] = [
    (0.10, ((-INF, 0.5), (-INF, INF), (-INF, INF))),
    (0.40, ((0.5, INF), (-INF, INF), (-INF, 0.5))),
    (0.90, ((0.5, INF), (-INF, INF), (0.5, INF))),
]

PROBES = [
    (0.2, 0.3, 0.4),
    (0.7, 0.1, 0.9),
    (0.5, 0.5, 0.5),
    (0.9, 0.9, 0.2),
    (0.51, 0.0, 0.51),
    (1.0, 1.0, 1.0),
    (0.0272, 0.0, 0.0),
]


def reference_predict(x: tuple[float, float, float]) -> float:
    """The tree of :data:`LEAVES`, written out. **Shares no code with `Explainer`.**"""
    if x[0] <= 0.5:
        return 0.10
    return 0.40 if x[2] <= 0.5 else 0.90


def reference_value(subset: int, x: tuple[float, float, float]) -> float:
    """`v(S)` straight from the definition: average over the background of the hybrid point."""
    total = 0.0
    for row in BACKGROUND:
        point = tuple(x[f] if (subset >> f) & 1 else row[f] for f in range(3))
        total += reference_predict(point)  # type: ignore[arg-type]
    return total / len(BACKGROUND)


def reference_shapley(x: tuple[float, float, float]) -> tuple[float, float, float]:
    """The three Shapley values by enumeration, with the weights written as factorials."""
    out = []
    for feature in range(3):
        bit = 1 << feature
        value = 0.0
        for subset in range(8):
            if subset & bit:
                continue
            size = bin(subset).count("1")
            weight = math.factorial(size) * math.factorial(3 - size - 1) / math.factorial(3)
            value += weight * (reference_value(subset | bit, x) - reference_value(subset, x))
        out.append(value)
    return (out[0], out[1], out[2])


# -- the background set, and where it came from ------------------------------------------------


def test_the_background_set_has_the_registered_shape() -> None:
    """256 rows of three features, sorted, distinct — the plan's §3 sampling rule, checked."""
    assert len(BACKGROUND) == 256
    assert all(len(row) == 3 for row in BACKGROUND)
    assert list(BACKGROUND) == sorted(BACKGROUND), "the sample is not in the sorted order §3 fixes"
    assert len(set(BACKGROUND)) == len(BACKGROUND), "the sample is not deduplicated"
    assert BACKGROUND_STRIDE == -(-CORPUS_DISTINCT_VECTORS // 256), (
        "the stride is not ceil(distinct / 256): the sample was not drawn by the registered rule"
    )


def test_every_background_row_is_inside_the_feature_bounds() -> None:
    """The rows are real evaluated pairs, so they must satisfy the bounds reachability relies on.

    A row outside them would mean the background and `FEATURE_BOUNDS` disagree about what a feature
    can be — and every threshold-reachability rule in the release is arithmetic over those bounds.
    """
    from netcorenoc.model_version import ORDERED_BOUNDS

    for row in BACKGROUND:
        for feature, value in enumerate(row):
            low, high = ORDERED_BOUNDS[feature]
            assert low <= value <= high, (feature, value, low, high)


# -- exactness ----------------------------------------------------------------------------------


@pytest.mark.parametrize("probe", PROBES)
def test_the_contributions_equal_the_definition(probe: tuple[float, float, float]) -> None:
    """**The exactness claim**, against a brute force that shares no arithmetic with the module.

    The tolerance is `1e-12`, which is float noise over a 256-term average — not a fitted tolerance.
    A path-dependent or approximate attribution would miss by orders of magnitude more.
    """
    explainer = attribution.build([(1.0, LEAVES)])
    _score, phi = explainer.explain(probe)
    expected = reference_shapley(probe)
    for feature in range(3):
        assert abs(phi[feature] - expected[feature]) < 1e-12, (feature, phi, expected)


def test_the_base_value_is_the_mean_output_over_the_background() -> None:
    """§3: *"The base value is the model's mean output over that background set."*

    **Compared with a tolerance, and the distinction matters.** The module sums one
    `value x fraction` per leaf; the brute force below sums 256 predictions one at a time. Both are
    the same quantity and neither is more correct, but they are different summation orders, so
    demanding bit equality here would be demanding that float addition be associative. Where this
    file *does* demand `==` — `test_the_sum_identity_holds_exactly` — it is because the order there
    is controlled on both sides, which is the only circumstance under which that demand is honest.
    """
    explainer = attribution.build([(1.0, LEAVES)])
    expected = sum(reference_predict(row) for row in BACKGROUND) / len(BACKGROUND)
    assert explainer.base_value == pytest.approx(expected, abs=1e-12)
    assert explainer.base_value == pytest.approx(reference_value(0, (0.0, 0.0, 0.0)), abs=1e-12), (
        "v(the empty coalition) and the base value must be the same quantity"
    )


@pytest.mark.parametrize("probe", PROBES)
def test_the_sum_identity_holds_exactly(probe: tuple[float, float, float]) -> None:
    """`sum(contributions) + base_value == score`, with `==`.

    Accumulated in the order `shadow_admission.admission` accumulates it, because that is the
    comparison this has to survive — and float addition is commutative, so `base + s` and
    `s + base` are the same bits.
    """
    explainer = attribution.build([(1.0, LEAVES)])
    score, phi = explainer.explain(probe)
    running = 0.0
    for contribution in phi:
        running += contribution
    assert running + explainer.base_value == score


@pytest.mark.parametrize("probe", PROBES)
def test_the_score_reproduces_the_model(probe: tuple[float, float, float]) -> None:
    """The explained score is the tree's own prediction, to a last-bit rounding and no more."""
    explainer = attribution.build([(1.0, LEAVES)])
    score, _phi = explainer.explain(probe)
    assert abs(score - reference_predict(probe)) < 1e-12


# -- the guard on the guard ---------------------------------------------------------------------


def test_a_constant_model_attributes_nothing_to_any_feature() -> None:
    """**The control this file needs most.** A single leaf is a constant function, so every Shapley
    value is exactly zero and the base value is that constant.

    Without it, every assertion above would pass against an implementation that returned zeros —
    and zeros are what a broken attribution most plausibly returns.
    """
    explainer = attribution.build([(1.0, [(0.42, ((-INF, INF), (-INF, INF), (-INF, INF)))])])
    score, phi = explainer.explain((0.3, 0.4, 0.5))
    assert phi == (0.0, 0.0, 0.0)
    assert explainer.base_value == 0.42
    assert score == 0.42


def test_a_model_that_uses_one_feature_attributes_to_that_feature_alone() -> None:
    """A one-split tree on `decay` must give `class_affinity` and `entity_affinity` exactly zero.

    This is the assertion that would fail if the coalition bookkeeping leaked a feature's value into
    a coalition it does not belong to — the most likely bug in eight-way enumeration, and one the
    sum identity cannot see because it sums over all three.
    """
    leaves: list[attribution.Leaf] = [
        (0.2, ((-INF, 0.5), (-INF, INF), (-INF, INF))),
        (0.8, ((0.5, INF), (-INF, INF), (-INF, INF))),
    ]
    explainer = attribution.build([(1.0, leaves)])
    _score, phi = explainer.explain((0.9, 0.1, 0.1))
    assert phi[1] == 0.0 and phi[2] == 0.0
    assert phi[0] != 0.0


def test_linearity_over_an_ensemble_is_exact() -> None:
    """`φ(Σ wᵢ·treeᵢ) == Σ wᵢ·φ(treeᵢ)` — the identity the per-tree tables rest on.

    Asserted rather than assumed, because the whole reason scoring an ensemble is cheap is that this
    holds; if it did not, the module would be fast and wrong.
    """
    other: list[attribution.Leaf] = [
        (0.0, ((-INF, INF), (-INF, 0.25), (-INF, INF))),
        (1.0, ((-INF, INF), (0.25, INF), (-INF, INF))),
    ]
    combined = attribution.build([(0.5, LEAVES), (0.5, other)])
    first = attribution.build([(1.0, LEAVES)])
    second = attribution.build([(1.0, other)])
    probe = (0.7, 0.6, 0.8)
    _s, phi = combined.explain(probe)
    _s1, phi_1 = first.explain(probe)
    _s2, phi_2 = second.explain(probe)
    for feature in range(3):
        assert abs(phi[feature] - 0.5 * (phi_1[feature] + phi_2[feature])) < 1e-15


def test_a_model_too_large_to_tabulate_is_refused_rather_than_approximated() -> None:
    """`MAX_CELLS_PER_TREE` is a *structural* limit and it refuses; it never falls back.

    A fallback to an approximate attribution would be the worst possible response: the model would
    ship, the contributions would still sum, and the explanation would silently stop being the one
    the plan registered.
    """
    cuts = [i / 40.0 for i in range(1, 40)]
    leaves: list[attribution.Leaf] = []
    for feature in range(3):
        previous = -INF
        for cut in [*cuts, INF]:
            box = tuple((previous, cut) if index == feature else (-INF, INF) for index in range(3))
            leaves.append((0.5, box))
            previous = cut
    with pytest.raises(attribution.AttributionError, match="cells"):
        attribution.build([(1.0, leaves)])


# -- the scorer the three kinds share -----------------------------------------------------------


def test_the_scorer_reports_a_shapley_basis_and_no_weight() -> None:
    """The contract bump, asserted where it matters: `basis` says the terms are Shapley values, and
    every `weight` is exactly `0.0` — the value `scorer_contract` documents as *undefined*.

    A term carrying a plausible non-zero weight under a Shapley basis is the lie DECISIONS #186
    exists to prevent, and it is invisible to every other assertion in this file.
    """
    scorer = attribution.AttributedScorer(
        explainer=attribution.build([(1.0, LEAVES)]),
        threshold=0.5,
        scorer_id="tree",
        contract_version="1.0",
        params_document="{}",
        fingerprint="f" * 64,
    )
    result = scorer.score(
        LinkFeatures(
            delta_t_s=7.5,
            class_i=1,
            class_j=2,
            class_affinity=0.33,
            ne_i=10,
            ne_j=11,
            entity_affinity=0.66,
        )
    )
    assert result.basis == BASIS_SHAPLEY
    assert [term.weight for term in result.terms] == [0.0, 0.0, 0.0]
    assert [term.name for term in result.terms] == [
        "decay",
        "class_affinity",
        "entity_affinity",
    ]
    running = 0.0
    for term in result.terms:
        running += term.contribution
    assert running + result.base_value == result.score
    assert result.base_value == scorer.base_value


def test_the_attribution_is_byte_identical_across_two_processes() -> None:
    """Across processes, for `test_challenger.py`'s reason: hash randomisation, dict ordering and
    import order are all per-process, and a within-process repeat would see none of them."""
    script = (
        "import json,sys,math;"
        f"sys.path.insert(0, {str(REPO_ROOT / 'src')!r});"
        "from netcorenoc import attribution;"
        "INF=math.inf;"
        "L=[(0.10,((-INF,0.5),(-INF,INF),(-INF,INF))),"
        "(0.40,((0.5,INF),(-INF,INF),(-INF,0.5))),"
        "(0.90,((0.5,INF),(-INF,INF),(0.5,INF)))];"
        "e=attribution.build([(1.0,L)]);"
        "s,p=e.explain((0.7,0.1,0.9));"
        "print(json.dumps([e.base_value.hex(),s.hex(),[v.hex() for v in p]]))"
    )
    out = subprocess.run(  # nosec B603 - this interpreter, a literal script, no shell
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    remote = json.loads(out.stdout)
    explainer = attribution.build([(1.0, LEAVES)])
    score, phi = explainer.explain((0.7, 0.1, 0.9))
    assert remote[0] == explainer.base_value.hex()
    assert remote[1] == score.hex()
    assert remote[2] == [value.hex() for value in phi]
