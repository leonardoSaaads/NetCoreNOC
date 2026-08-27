"""The three new model kinds, their fits, and their fourteen degeneracy rules (v0.14.0).

`PREREGISTRATION-0.14.0.md` §2 registers **T1-T6, F1-F4 and G1-G4 before any fit existed** that they
could have been chosen to suit. This file tests them the way `test_model_version.py` tests the
logistic five, and for the same stated reason: **a payload-degeneracy test per rule, each rejecting
its own case and no other.**

That last clause is the design of this file. A test that only asserted *"this payload is rejected"*
would pass against a validator that rejected everything, so every rule's test asserts the
**reason**,
and the three `test_a_valid_*_payload_is_accepted` tests are the standing controls for all fourteen.

Determinism is tested **across two processes** rather than twice in one, for `test_challenger.py`'s
reason: hash randomisation, dict ordering and import order are per-process, and a within-process
repeat would see none of them.
"""

from __future__ import annotations

import json
import subprocess  # nosec B404 - runs this interpreter on a literal script, no shell, no input
import sys
from pathlib import Path
from typing import Any

import pytest

from modelfixtures import training_rows
from netcorenoc.engine.correlate import scoring
from netcorenoc.engine.correlate.scorer_contract import BASIS_SHAPLEY, LinkFeatures
from netcorenoc.engine.model import attribution, boosting, forest, model_version, tree
from netcorenoc.engine.model.cart import LEAF
from netcorenoc.engine.model.model_version import (
    KIND_FOREST,
    KIND_GRADIENT_BOOSTING,
    KIND_TREE,
    ModelPayloadError,
)

import util

REPO_ROOT = Path(__file__).resolve().parent.parent
CV = scoring.CONTRACT_VERSION

TREE_HYPERS = {"max_depth": 4, "min_samples_leaf": 20, "criterion": "gini", "threshold": 0.5}
FOREST_HYPERS = {
    "n_estimators": 6,
    "max_depth": 4,
    "min_samples_leaf": 20,
    "mtry": 3,
    "seed": 20_140_000,
    "threshold": 0.5,
}
BOOSTING_HYPERS = {
    "n_rounds": 8,
    "learning_rate": 0.2,
    "max_depth": 3,
    "min_samples_leaf": 20,
    "threshold": 0.5,
}

FEATURES = LinkFeatures(
    delta_t_s=7.5,
    class_i=1,
    class_j=2,
    class_affinity=0.9,
    ne_i=10,
    ne_j=11,
    entity_affinity=0.9,
)


# -- fitted documents, once per session ---------------------------------------------------------


@pytest.fixture(scope="module")
def fitted() -> dict[str, dict[str, Any]]:
    """One fitted document per kind. Module-scoped: the fits are deterministic, so sharing them
    across tests cannot leak state, and refitting for every test would triple the file's runtime."""
    import asyncio

    rows = training_rows()

    async def run() -> dict[str, dict[str, Any]]:
        return {
            KIND_TREE: await tree.fit_document(rows, **TREE_HYPERS),  # type: ignore[arg-type]
            KIND_FOREST: await forest.fit_document(rows, **FOREST_HYPERS),  # type: ignore[arg-type]
            KIND_GRADIENT_BOOSTING: await boosting.fit_document(
                rows,
                **BOOSTING_HYPERS,  # type: ignore[arg-type]
            ),
        }

    return asyncio.run(run())


def LEAF_ROW(value: float) -> list[float]:  # noqa: N802 - a document row, not a class
    """One leaf, in the document's own five-number form."""
    return [float(LEAF), 0.0, float(LEAF), float(LEAF), value]


def document_of(payload: dict[str, Any], **overrides: Any) -> str:
    return model_version.canonical_object({**payload, **overrides})


def rejects(kind: str, document: str, *, because: str) -> None:
    """Assert the payload is refused, and refused **for the stated reason**.

    The `because` match is what makes a per-rule test a per-rule test. Without it, fourteen tests
    would all pass against a validator with one rule and thirteen dead branches.
    """
    with pytest.raises(ModelPayloadError, match=because):
        model_version.validate_document(kind, CV, document)


# -- the controls, first ------------------------------------------------------------------------


@pytest.mark.parametrize("kind", [KIND_TREE, KIND_FOREST, KIND_GRADIENT_BOOSTING])
def test_a_valid_payload_is_accepted_and_builds_a_scorer(
    kind: str, fitted: dict[str, dict[str, Any]]
) -> None:
    """**The control for every degeneracy test below.** A validator that refused everything would
    satisfy all fourteen and make three kinds unusable — a worse defect than the ones they guard
    against, and invisible to a test that only checks that bad input fails."""
    document = document_of(fitted[kind])
    model_version.validate_document(kind, CV, document)
    scorer = model_version.scorer_for(kind, CV, document)
    assert isinstance(scorer, attribution.AttributedScorer)
    assert scorer.scorer_id == kind
    result = scorer.score(FEATURES)
    assert result.basis == BASIS_SHAPLEY
    assert len(result.terms) == 3


@pytest.mark.parametrize("kind", [KIND_TREE, KIND_FOREST, KIND_GRADIENT_BOOSTING])
def test_the_fit_produces_a_model_that_actually_splits(
    kind: str, fitted: dict[str, dict[str, Any]]
) -> None:
    """A fit that produced single-leaf trees would satisfy every *document* assertion here while
    learning nothing, and T4/F3/G3 would then be testing the fixture rather than the rules."""
    payload = fitted[kind]
    members = [payload["nodes"]] if kind == KIND_TREE else payload["trees"]
    assert members, kind
    assert any(len(nodes) > 1 for nodes in members), f"{kind} fitted only single-leaf trees"


# -- T1-T6, the tree kind -----------------------------------------------------------------------


def test_t1_refuses_a_non_finite_threshold_in_a_node(fitted: dict[str, dict[str, Any]]) -> None:
    payload = dict(fitted[KIND_TREE])
    nodes = [list(row) for row in payload["nodes"]]
    interior = next(i for i, row in enumerate(nodes) if int(row[0]) != LEAF)
    document = json.dumps({**payload, "nodes": nodes}, sort_keys=True).replace(
        str(nodes[interior][1]), "NaN", 1
    )
    rejects(KIND_TREE, document, because="not finite")


def test_t2_refuses_a_child_index_outside_the_node_list(
    fitted: dict[str, dict[str, Any]],
) -> None:
    payload = dict(fitted[KIND_TREE])
    nodes = [list(row) for row in payload["nodes"]]
    interior = next(i for i, row in enumerate(nodes) if int(row[0]) != LEAF)
    nodes[interior][2] = float(len(nodes) + 5)
    rejects(KIND_TREE, document_of(payload, nodes=nodes), because="outside the node list")


def test_t2_refuses_an_unreachable_node(fitted: dict[str, dict[str, Any]]) -> None:
    """A node nobody points at is a document describing something the traversal cannot serve."""
    payload = dict(fitted[KIND_TREE])
    nodes = [list(row) for row in payload["nodes"]]
    nodes.append([float(LEAF), 0.0, float(LEAF), float(LEAF), 0.5])
    rejects(KIND_TREE, document_of(payload, nodes=nodes), because="unreachable from the root")


def test_t2_refuses_a_cycle(fitted: dict[str, dict[str, Any]]) -> None:
    payload = dict(fitted[KIND_TREE])
    nodes = [list(row) for row in payload["nodes"]]
    interior = next(i for i, row in enumerate(nodes) if int(row[0]) != LEAF)
    nodes[interior][2] = 0.0  # point a child back at the root
    rejects(KIND_TREE, document_of(payload, nodes=nodes), because="reachable more than once")


def test_t3_refuses_a_feature_id_outside_the_feature_set(
    fitted: dict[str, dict[str, Any]],
) -> None:
    """A document written against a different feature set is training/serving skew arriving through
    the database — the defect the plan's §6 exists to detect, one kind over."""
    payload = dict(fitted[KIND_TREE])
    nodes = [list(row) for row in payload["nodes"]]
    interior = next(i for i, row in enumerate(nodes) if int(row[0]) != LEAF)
    nodes[interior][0] = 3.0
    rejects(KIND_TREE, document_of(payload, nodes=nodes), because="outside \\[0, 3\\)")


def test_t4_refuses_a_tree_of_depth_zero(fitted: dict[str, dict[str, Any]]) -> None:
    """A single leaf returns the same score for every pair: the all-zero logistic in another
    shape."""
    payload = dict(fitted[KIND_TREE])
    rejects(
        KIND_TREE,
        document_of(payload, nodes=[[float(LEAF), 0.0, float(LEAF), float(LEAF), 0.7]]),
        because="depth 0",
    )


def test_t5_refuses_a_tree_whose_every_leaf_is_above_the_threshold(
    fitted: dict[str, dict[str, Any]],
) -> None:
    payload = dict(fitted[KIND_TREE])
    nodes = [
        [0.0, 0.5, 1.0, 2.0, 0.0],
        [float(LEAF), 0.0, float(LEAF), float(LEAF), 0.8],
        [float(LEAF), 0.0, float(LEAF), float(LEAF), 0.9],
    ]
    rejects(KIND_TREE, document_of(payload, nodes=nodes), because="lowest reachable output")


def test_t5_refuses_a_tree_whose_every_leaf_is_below_the_threshold(
    fitted: dict[str, dict[str, Any]],
) -> None:
    payload = dict(fitted[KIND_TREE])
    nodes = [
        [0.0, 0.5, 1.0, 2.0, 0.0],
        [float(LEAF), 0.0, float(LEAF), float(LEAF), 0.1],
        [float(LEAF), 0.0, float(LEAF), float(LEAF), 0.2],
    ]
    rejects(KIND_TREE, document_of(payload, nodes=nodes), because="highest reachable output")


def test_t5_counts_only_leaves_a_real_pair_can_reach(fitted: dict[str, dict[str, Any]]) -> None:
    """**The half of T5 that a naive `min(leaf) / max(leaf)` would get wrong.**

    `decay` is bounded below by `exp(-WINDOW_S / TAU0_S)`, so a leaf behind `decay <= 0.001` cannot
    be reached by any pair the correlator can build. A tree whose only sub-threshold leaf sits there
    discriminates on paper and not in the network, and the rule must refuse it.
    """
    payload = dict(fitted[KIND_TREE])
    nodes = [
        [0.0, 0.001, 1.0, 2.0, 0.0],
        [float(LEAF), 0.0, float(LEAF), float(LEAF), 0.1],  # unreachable: decay > 0.0183
        [float(LEAF), 0.0, float(LEAF), float(LEAF), 0.9],
    ]
    rejects(KIND_TREE, document_of(payload, nodes=nodes), because="lowest reachable output")


def test_t6_refuses_a_saturating_leaf(fitted: dict[str, dict[str, Any]]) -> None:
    payload = dict(fitted[KIND_TREE])
    nodes = [list(row) for row in payload["nodes"]]
    leaf = next(i for i, row in enumerate(nodes) if int(row[0]) == LEAF)
    nodes[leaf][4] = model_version.MAX_ABS_COEFFICIENT + 1.0
    rejects(KIND_TREE, document_of(payload, nodes=nodes), because="magnitude bound")


def test_the_tree_kind_refuses_a_missing_or_unexpected_key(
    fitted: dict[str, dict[str, Any]],
) -> None:
    payload = dict(fitted[KIND_TREE])
    without = {k: v for k, v in payload.items() if k != "criterion"}
    rejects(KIND_TREE, model_version.canonical_object(without), because="missing.*criterion")
    rejects(KIND_TREE, document_of(payload, learning_rate=0.1), because="unexpected.*learning_rate")


def test_the_tree_kind_refuses_a_criterion_it_does_not_implement(
    fitted: dict[str, dict[str, Any]],
) -> None:
    """`variance` exists in `cart` for boosting's residuals and is **not** operator-facing."""
    rejects(KIND_TREE, document_of(fitted[KIND_TREE], criterion="variance"), because="criterion")


# -- F1-F4, the forest kind ---------------------------------------------------------------------


def test_f1_refuses_a_forest_whose_seed_is_absent_or_not_an_integer(
    fitted: dict[str, dict[str, Any]],
) -> None:
    """**The injection Phase 8 runs against the seed**, as a standing test.

    Two forests grown from the same rows with different seeds are different models; a seed outside
    the document would let them share a `params_hash`.
    """
    payload = dict(fitted[KIND_FOREST])
    without = {k: v for k, v in payload.items() if k != "seed"}
    rejects(KIND_FOREST, model_version.canonical_object(without), because="missing.*seed")
    rejects(KIND_FOREST, document_of(payload, seed=1.5), because="must be an integer")


def test_f2_refuses_a_forest_of_one(fitted: dict[str, dict[str, Any]]) -> None:
    payload = dict(fitted[KIND_FOREST])
    document = document_of(payload, trees=payload["trees"][:1], n_estimators=1)
    rejects(KIND_FOREST, document, because="tree with a misleading name")


def test_f3_applies_reachability_to_the_average_and_not_to_a_member(
    fitted: dict[str, dict[str, Any]],
) -> None:
    """**The rule that would be wrong if applied per member**, asserted both ways.

    Two members that each sit wholly on one side of the threshold average to something that crosses
    it, and the plan says the aggregate is what matters. A validator that applied T5 per member
    would refuse this model, which the plan does not.

    Both members are one-split trees rather than single leaves: an ensemble of constants is
    constant everywhere, so its lowest and highest reachable outputs coincide and it fails
    reachability from the other side — correctly, and for a reason this test is not about.
    """
    payload = dict(fitted[KIND_FOREST])
    # Splits on decay at 0.5; both leaves ABOVE the threshold. T5 alone would refuse it.
    above = [[0.0, 0.5, 1.0, 2.0, 0.0], LEAF_ROW(0.9), LEAF_ROW(0.8)]
    # Splits on entity affinity at 0.5; both leaves BELOW. T5 alone would refuse it too.
    below = [[2.0, 0.5, 1.0, 2.0, 0.0], LEAF_ROW(0.3), LEAF_ROW(0.1)]
    # Averaged, the four cells are 0.60, 0.55, 0.50 and 0.45 — which straddle 0.5.
    explainer = attribution.build(
        [
            (0.5, tree.leaves(tree.nodes_from_payload(above))),
            (0.5, tree.leaves(tree.nodes_from_payload(below))),
        ]
    )
    accepted = document_of(
        payload, trees=[above, below], n_estimators=2, base_value=explainer.base_value
    )
    model_version.validate_document(KIND_FOREST, CV, accepted)  # must not raise

    # And the aggregate rule still bites when the AVERAGE cannot cross.
    lower = [[2.0, 0.5, 1.0, 2.0, 0.0], LEAF_ROW(0.3), LEAF_ROW(0.2)]
    rejects(
        KIND_FOREST,
        document_of(payload, trees=[below, lower], n_estimators=2),
        because="highest reachable averaged output",
    )


def test_f4_refuses_a_forest_whose_members_are_all_identical(
    fitted: dict[str, dict[str, Any]],
) -> None:
    """A forest whose bagging drew the same sample every time is a tree that costs `n` times
    more."""
    payload = dict(fitted[KIND_FOREST])
    one = payload["trees"][0]
    rejects(
        KIND_FOREST,
        document_of(payload, trees=[one] * 3, n_estimators=3),
        because="every member tree is identical",
    )


# -- G1-G4, the boosted kind --------------------------------------------------------------------


def test_g1_refuses_a_learning_rate_outside_the_registered_interval(
    fitted: dict[str, dict[str, Any]],
) -> None:
    payload = dict(fitted[KIND_GRADIENT_BOOSTING])
    rejects(KIND_GRADIENT_BOOSTING, document_of(payload, learning_rate=0.0), because=r"\(0, 1\]")
    rejects(KIND_GRADIENT_BOOSTING, document_of(payload, learning_rate=1.5), because=r"\(0, 1\]")


def test_g2_refuses_a_boosted_model_with_no_rounds(fitted: dict[str, dict[str, Any]]) -> None:
    payload = dict(fitted[KIND_GRADIENT_BOOSTING])
    rejects(
        KIND_GRADIENT_BOOSTING,
        document_of(payload, trees=[], n_rounds=0),
        because="there is no model here",
    )


def test_g3_applies_reachability_to_the_accumulated_output(
    fitted: dict[str, dict[str, Any]],
) -> None:
    payload = dict(fitted[KIND_GRADIENT_BOOSTING])
    flat = [[float(LEAF), 0.0, float(LEAF), float(LEAF), 0.0]]
    rejects(
        KIND_GRADIENT_BOOSTING,
        document_of(payload, trees=[flat], n_rounds=1, base_score=0.9, learning_rate=0.1),
        because="lowest reachable accumulated output",
    )


def test_g4_refuses_a_boosted_model_with_no_base_score(
    fitted: dict[str, dict[str, Any]],
) -> None:
    payload = dict(fitted[KIND_GRADIENT_BOOSTING])
    without = {k: v for k, v in payload.items() if k != "base_score"}
    rejects(
        KIND_GRADIENT_BOOSTING,
        model_version.canonical_object(without),
        because="missing.*base_score",
    )


# -- the round trip, per kind -------------------------------------------------------------------


@pytest.mark.parametrize("kind", [KIND_TREE, KIND_FOREST, KIND_GRADIENT_BOOSTING])
def test_document_for_round_trips_scorer_for(kind: str, fitted: dict[str, dict[str, Any]]) -> None:
    """`document_for(scorer_for(document)) == document`, per kind, byte for byte."""
    document = document_of(fitted[kind])
    assert model_version.document_for(model_version.scorer_for(kind, CV, document)) == document


@pytest.mark.parametrize("kind", [KIND_TREE, KIND_FOREST, KIND_GRADIENT_BOOSTING])
def test_the_base_value_enters_the_fingerprint(
    kind: str, fitted: dict[str, dict[str, Any]]
) -> None:
    """A model whose base value moved is a model that explains its decisions differently, so it must
    not share a fingerprint. And the loader refuses it outright, which is the stronger guard."""
    payload = fitted[kind]
    document = document_of(payload)
    tampered = document_of(payload, base_value=payload["base_value"] + 0.01)
    assert model_version.params_hash(kind, CV, document) != model_version.params_hash(
        kind, CV, tampered
    )
    with pytest.raises(ModelPayloadError, match="base_value"):
        model_version.scorer_for(kind, CV, tampered)


@pytest.mark.parametrize(
    "kind,field,value",
    [
        (KIND_TREE, "max_depth", 3),
        (KIND_TREE, "criterion", "entropy"),
        (KIND_FOREST, "mtry", 2),
        (KIND_FOREST, "seed", 999),
        (KIND_GRADIENT_BOOSTING, "n_rounds", 4),
        (KIND_GRADIENT_BOOSTING, "learning_rate", 0.3),
    ],
)
def test_a_hyperparameter_change_moves_the_params_hash(
    kind: str, field: str, value: Any, fitted: dict[str, dict[str, Any]]
) -> None:
    """`UI-0.13-DRAFT.md` §8: a hyperparameter that changes the trained model appears in
    `params_document` and therefore in `params_hash`. Otherwise two models with the same hash and
    different hyperparameters are indistinguishable and v0.11.0's provenance becomes fiction."""
    payload = fitted[kind]
    assert model_version.params_hash(kind, CV, document_of(payload)) != model_version.params_hash(
        kind, CV, document_of(payload, **{field: value})
    )


# -- determinism, across two processes ----------------------------------------------------------


def _fit_in_a_subprocess(kind: str, hypers: dict[str, Any]) -> str:
    """Fit `kind` in a fresh interpreter and return the canonical document it produced."""
    script = (
        "import asyncio,json,sys;"
        f"sys.path.insert(0, {str(REPO_ROOT / 'src')!r});"
        f"sys.path.insert(0, {str(REPO_ROOT / 'tests')!r});"
        "from modelfixtures import training_rows;"
        "from netcorenoc.engine.model import boosting, forest, model_version, tree;"
        f"kind={kind!r}; hypers={hypers!r};"
        "mod={'tree':tree,'forest':forest,'gradient_boosting':boosting}[kind];"
        "doc=asyncio.run(mod.fit_document(training_rows(), **hypers));"
        "print(model_version.canonical_object(doc))"
    )
    out = subprocess.run(  # nosec B603 - this interpreter, a literal script, no shell
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


@pytest.mark.parametrize(
    "kind,hypers",
    [
        (KIND_TREE, TREE_HYPERS),
        (KIND_FOREST, FOREST_HYPERS),
        (KIND_GRADIENT_BOOSTING, BOOSTING_HYPERS),
    ],
)
def test_two_fits_in_two_processes_are_byte_identical(
    kind: str, hypers: dict[str, Any], fitted: dict[str, dict[str, Any]]
) -> None:
    """**Prime directive 6.** Byte-identical documents *and* an identical `params_hash`."""
    remote = _fit_in_a_subprocess(kind, hypers)
    local = document_of(fitted[kind])
    assert remote == local
    assert model_version.params_hash(kind, CV, remote) == model_version.params_hash(kind, CV, local)


def test_the_forest_seed_changes_the_trees_and_the_same_seed_does_not() -> None:
    """**Both halves, because only the pair proves the seed is doing anything.**

    A test that asserted only *"the same seed gives the same trees"* would pass against an
    implementation that ignored the seed entirely — which is the failure mode a seeded fit most
    plausibly has, and the one that would make `params_hash` claim a difference that is not there.
    """
    same = _fit_in_a_subprocess(KIND_FOREST, FOREST_HYPERS)
    again = _fit_in_a_subprocess(KIND_FOREST, FOREST_HYPERS)
    other = _fit_in_a_subprocess(KIND_FOREST, {**FOREST_HYPERS, "seed": FOREST_HYPERS["seed"] + 1})
    assert same == again, "the same seed produced two different forests"
    assert same != other, "a different seed produced the same forest: the seed does nothing"
    assert model_version.params_hash(KIND_FOREST, CV, same) != model_version.params_hash(
        KIND_FOREST, CV, other
    )


def test_the_seed_is_inside_the_document_and_therefore_inside_the_hash(
    fitted: dict[str, dict[str, Any]],
) -> None:
    """F1 as a positive statement rather than a refusal."""
    document = document_of(fitted[KIND_FOREST])
    assert '"seed":' in document
    assert json.loads(document)["seed"] == FOREST_HYPERS["seed"]


# -- one attribution module, three kinds, no per-kind branch ------------------------------------


def test_one_scorer_class_serves_all_three_kinds(fitted: dict[str, dict[str, Any]]) -> None:
    """Gate 3's requirement, asserted structurally rather than described.

    The caller — `model_version.scorer_for` — hands every kind to the same `AttributedScorer`
    reading the same `Explainer`. If a per-kind scorer were ever introduced, this goes red.
    """
    built = {
        kind: model_version.scorer_for(kind, CV, document_of(fitted[kind]))
        for kind in (KIND_TREE, KIND_FOREST, KIND_GRADIENT_BOOSTING)
    }
    assert {type(scorer) for scorer in built.values()} == {attribution.AttributedScorer}
    for kind, scorer in built.items():
        result = scorer.score(FEATURES)
        running = 0.0
        for term in result.terms:
            running += term.contribution
        assert running + result.base_value == result.score, kind


def test_a_fitted_model_of_every_kind_is_a_link_scorer(
    fitted: dict[str, dict[str, Any]],
) -> None:
    """Structural satisfaction of the Protocol, which is what lets `SafeScorer` wrap these and what
    makes a promotion a pointer move rather than a parallel architecture."""
    for kind in (KIND_TREE, KIND_FOREST, KIND_GRADIENT_BOOSTING):
        scorer = model_version.scorer_for(kind, CV, document_of(fitted[kind]))
        assert isinstance(scorer, scoring.LinkScorer)
        assert scoring.SafeScorer(scorer).score(FEATURES).terms


def test_an_unknown_kind_is_still_refused() -> None:
    """`SUPPORTED_KINDS` grew from two to five and stayed **closed**."""
    assert {
        "additive",
        "logistic",
        "tree",
        "forest",
        "gradient_boosting",
    } == model_version.SUPPORTED_KINDS
    with pytest.raises(ModelPayloadError, match="not one this build implements"):
        model_version.validate_document("xgboost", CV, "{}")


def test_the_boosted_kind_is_not_called_xgboost() -> None:
    """DECISIONS #189. XGBoost is a specific algorithm and a project name; this is neither, and a
    `model_version` row claiming otherwise would put a false statement in the audit log."""
    assert boosting.KIND == "gradient_boosting"
    assert "xgboost" not in model_version.SUPPORTED_KINDS
    source = util.module_path("boosting.py").read_text(encoding="utf-8")
    assert "second-order" in source, "the reason the name differs is not recorded where it applies"


def test_the_tree_family_cannot_reach_the_store_the_clock_or_the_network() -> None:
    """By construction, checked by parsing rather than by reading (`test_challenger.py`'s
    method)."""
    import ast

    forbidden = {"time", "socket", "random", "secrets", "sqlite3", "aiosqlite", "urllib", "http"}
    for name in ("tree", "forest", "boosting", "attribution", "background"):
        source = util.module_path(f"{name}.py").read_text(encoding="utf-8")
        imported = {
            alias.name.split(".")[0]
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module.split(".")[0]
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert not (imported & forbidden), (name, sorted(imported & forbidden))
