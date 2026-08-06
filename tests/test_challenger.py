"""The challenger's contract, its isolation, and its determinism — asserted before any training.

Written and run in Phase 4 **step 1**, against a scorer whose coefficients are still zero, because
these are the properties that must hold whatever the fit turns out to be. A test written after a
model exists is a test shaped by that model.

Four things are proven here:

1. it satisfies `LinkScorer` **structurally**, like the four test-only scorers v0.6.0 shipped;
2. its **term contributions sum to the pre-link score**, exactly, including the intercept;
3. it is **deterministic** — identical features give byte-identical scores across runs and across
   **processes**;
4. **no code path makes it the active scorer**, asserted by parsing the tree rather than by reading
   it.
"""

from __future__ import annotations

import ast
import asyncio
import json
import math
import subprocess  # nosec B404 - runs this interpreter on a literal script, no shell, no input
import sys
from pathlib import Path

import pytest

from netcorenoc import scoring
from netcorenoc.challenger import (
    CHALLENGER_SCORER_ID,
    FEATURE_NAMES,
    TAU0_S,
    Coefficients,
    LogisticScorer,
    feature_vector,
    sigmoid,
)
from netcorenoc.main import Engine
from netcorenoc.scoring import AdditiveScorer, LinkFeatures, LinkScore, LinkScorer, SafeScorer
from netcorenoc.store import Store

REPO_ROOT = Path(__file__).resolve().parent.parent
PKG = REPO_ROOT / "src" / "netcorenoc"

FITTED = Coefficients(intercept=-1.25, decay=2.5, class_affinity=1.75, entity_affinity=-0.5)


def features(dt: float = 5.0, a: float = 0.4, e: float = 1.0) -> LinkFeatures:
    return LinkFeatures(
        delta_t_s=dt, class_i=1, class_j=2, class_affinity=a, ne_i=10, ne_j=11, entity_affinity=e
    )


# -- 1. the Protocol, satisfied structurally -------------------------------------------------


def test_the_challenger_satisfies_the_link_scorer_protocol() -> None:
    """The plurality proof v0.6.0 shipped, extended to the first non-test implementation.

    No base class, no registry, no edit to `scoring.py` — the same terms `ConstantScorer` and its
    three siblings satisfy the Protocol on.
    """
    assert isinstance(LogisticScorer(), LinkScorer)
    assert isinstance(LogisticScorer(FITTED), LinkScorer)
    assert LogisticScorer().contract_version == scoring.CONTRACT_VERSION
    assert LogisticScorer().scorer_id == CHALLENGER_SCORER_ID != scoring.DEFAULT_SCORER_ID


def test_scoring_py_gained_nothing() -> None:
    """`scoring.py` may not grow a challenger, an import of one, or a name that knows about one.

    The seam's whole value is that a second implementation needs no edit there. A test that only
    checked the challenger existed would not notice the seam being quietly abandoned.
    """
    source = (PKG / "scoring.py").read_text(encoding="utf-8")
    for forbidden in ("challenger", "logistic", "Coefficients", "shadow"):
        assert forbidden.lower() not in source.lower(), f"scoring.py mentions {forbidden!r}"


def test_safe_scorer_wraps_it_and_the_fail_safe_is_inherited() -> None:
    """`SafeScorer` is not re-implemented for the challenger; it already works on it."""
    safe = SafeScorer(LogisticScorer(FITTED))
    assert safe.active.scorer_id == CHALLENGER_SCORER_ID
    assert safe.score(features()) == LogisticScorer(FITTED).score(features())
    assert safe.params_fingerprint() == LogisticScorer(FITTED).params_fingerprint()
    assert not safe.degraded and safe.warnings() == []


# -- 2. explainability, by contract ----------------------------------------------------------


@pytest.mark.parametrize(
    "coefficients",
    [Coefficients(), FITTED, Coefficients(intercept=3.0), Coefficients(decay=-9.5)],
)
def test_term_contributions_sum_to_the_pre_link_score(coefficients: Coefficients) -> None:
    """**The contract test.** A logistic model IS a weighted sum before the link function, so the
    per-term breakdown is what it already computes — and the sum is exact, not approximate.

    Summed left to right in the same order `score()` accumulates, because float addition is not
    associative and a tolerance here would hide precisely the reordering bug it exists to catch.
    """
    scorer = LogisticScorer(coefficients)
    for dt, a, e in ((0.0, 0.0, 0.0), (5.0, 0.4, 1.0), (119.0, 1.0, 0.25)):
        result = scorer.score(features(dt, a, e))
        total = 0.0
        for term in result.terms:
            total += term.contribution
        assert total == result.score, f"contributions do not sum to the score for {coefficients}"


def test_there_is_one_term_per_free_parameter_and_the_intercept_is_one_of_them() -> None:
    """Omitting the intercept would leave an explanation that does not add up — and would silently
    reduce the reported parameter count, which is the number the sufficiency floor scales on."""
    result = LogisticScorer(FITTED).score(features())
    assert [term.name for term in result.terms] == ["intercept", *FEATURE_NAMES]
    assert len(result.terms) == 4  # three features and an intercept: four free parameters
    intercept = result.terms[0]
    assert (intercept.weight, intercept.value) == (FITTED.intercept, 1.0)
    for term in result.terms[1:]:
        assert term.contribution == term.weight * term.value


def test_the_verdict_is_the_probability_verdict_expressed_in_logits() -> None:
    """`linked` comes from the pre-link score against a pre-link threshold, which at 0.0 is
    exactly p > 0.5. Deriving it twice — once here, once from `probability()` — must never be able
    to disagree, so it is derived once and the equivalence is asserted."""
    scorer = LogisticScorer(FITTED)
    for dt in (0.0, 1.0, 5.0, 30.0, 90.0, 119.0):
        result = scorer.score(features(dt))
        assert result.linked == (scorer.probability(features(dt)) > 0.5)
        assert result.threshold == 0.0


def test_an_untrained_challenger_abstains_rather_than_guessing() -> None:
    """All-zero coefficients are the honest untrained state: every logit is exactly 0.0, so
    `linked` is False at the default threshold. A challenger nobody fitted should say nothing."""
    scorer = LogisticScorer()
    assert not scorer.trained
    for dt, a, e in ((0.0, 1.0, 1.0), (119.0, 0.0, 0.0), (5.0, 0.5, 0.5)):
        result = scorer.score(features(dt, a, e))
        assert result.score == 0.0 and not result.linked
        assert scorer.probability(features(dt, a, e)) == 0.5
    assert LogisticScorer(FITTED).trained


def test_the_link_function_cannot_overflow_out_of_the_contract() -> None:
    """`SafeScorer` treats a non-finite score as a contract violation and degrades PERMANENTLY, so
    an absurd coefficient must not be able to take the wrapper out of service."""
    for logit in (-1e9, -800.0, -1.0, 0.0, 1.0, 800.0, 1e9):
        p = sigmoid(logit)
        assert math.isfinite(p) and 0.0 <= p <= 1.0
    assert sigmoid(0.0) == 0.5
    wild = LogisticScorer(Coefficients(intercept=1e300, decay=1e300))
    assert math.isfinite(wild.probability(features()))


def test_the_feature_vector_has_exactly_one_implementation() -> None:
    """Training, offline reconstruction and the online path must build features the same way, and
    the way they are made to is that there is only one function that builds them."""
    assert feature_vector(5.0, 0.4, 1.0) == (math.exp(-5.0 / TAU0_S), 0.4, 1.0)
    assert feature_vector(-5.0, 0.4, 1.0) == feature_vector(5.0, 0.4, 1.0)  # abs, as the champion
    result = LogisticScorer(FITTED).score(features(5.0, 0.4, 1.0))
    assert tuple(term.value for term in result.terms[1:]) == feature_vector(5.0, 0.4, 1.0)


# -- 3. determinism ---------------------------------------------------------------------------


def test_identical_features_give_byte_identical_scores_within_a_process() -> None:
    scorer = LogisticScorer(FITTED)
    for dt, a, e in ((0.0, 0.0, 0.0), (7.5, 0.33, 0.66), (119.999, 1.0, 1.0)):
        first, second = scorer.score(features(dt, a, e)), scorer.score(features(dt, a, e))
        assert first == second
        assert first.score.hex() == second.score.hex()  # byte-identical, not merely equal


def test_the_score_is_byte_identical_across_two_processes() -> None:
    """**Across processes**, because hash randomisation, dict ordering and import order are all
    per-process and a within-process repeat would not see any of them.

    The subprocess prints the float's `hex()`, which is exact — comparing `repr` would compare a
    rounded decimal and pass through a last-bit difference.
    """
    script = (
        "import json,sys;"
        f"sys.path.insert(0, {str(REPO_ROOT / 'src')!r});"
        "from netcorenoc.challenger import Coefficients, LogisticScorer;"
        "from netcorenoc.scoring import LinkFeatures;"
        "s=LogisticScorer(Coefficients(-1.25,2.5,1.75,-0.5));"
        "f=LinkFeatures(delta_t_s=7.5,class_i=1,class_j=2,class_affinity=0.33,"
        "ne_i=10,ne_j=11,entity_affinity=0.66);"
        "r=s.score(f);"
        "print(json.dumps([r.score.hex(),r.linked,s.params_fingerprint(),"
        "[t.contribution.hex() for t in r.terms]]))"
    )
    out = subprocess.run(  # nosec B603 - this interpreter, a literal script, no shell
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    remote = json.loads(out.stdout)
    scorer = LogisticScorer(FITTED)
    local = scorer.score(
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
    assert remote[0] == local.score.hex()
    assert remote[1] is local.linked
    assert remote[2] == scorer.params_fingerprint()
    assert remote[3] == [term.contribution.hex() for term in local.terms]


def test_the_fingerprint_moves_with_every_parameter_and_only_with_them() -> None:
    base = LogisticScorer(FITTED).params_fingerprint()
    assert LogisticScorer(FITTED).params_fingerprint() == base
    for changed in (
        Coefficients(-1.25, 2.5, 1.75, -0.5000001),
        Coefficients(-1.2500001, 2.5, 1.75, -0.5),
        Coefficients(),
    ):
        assert LogisticScorer(changed).params_fingerprint() != base
    assert LogisticScorer(FITTED, threshold=0.1).params_fingerprint() != base
    # …and it is not the champion's, even at the same nominal parameters.
    assert base != AdditiveScorer().params_fingerprint()


def test_the_coefficients_round_trip_through_their_stored_form() -> None:
    """The stored JSON is the only representation the shadow table keeps, so a coefficient vector
    that could not be read back exactly would make every recorded run unreproducible."""
    restored = Coefficients.from_dict(json.loads(FITTED.to_json()))
    assert restored == FITTED
    assert (
        LogisticScorer(restored).params_fingerprint() == LogisticScorer(FITTED).params_fingerprint()
    )
    assert FITTED.to_json() == (
        '{"class_affinity":1.75,"decay":2.5,"entity_affinity":-0.5,"intercept":-1.25}'
    )


# -- 4. isolation: nothing makes it the active scorer -----------------------------------------


def test_no_code_path_makes_the_challenger_the_active_scorer() -> None:
    """**The isolation gate**, asserted by parsing the runtime package rather than by reading it.

    Two properties, and the second is the one that matters. First: no module under
    `src/netcorenoc/` passes a challenger to `Correlator.set_scorer`, assigns `engine.scorer`, or
    hands one to `SafeScorer` outside the shadow modules. Second: the modules that *are* allowed to
    construct one are enumerated here, so a new call site anywhere else is a failing diff rather
    than a review someone might not do.
    """
    allowed = {"challenger.py", "training.py", "shadow.py", "shadow_eval.py", "shadow_report.py"}
    offenders: list[str] = []
    for path in sorted(PKG.rglob("*.py")):
        name = str(path.relative_to(PKG))
        source = path.read_text(encoding="utf-8")
        if name in allowed or name == "__init__.py":
            continue
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "netcorenoc.challenger":
                offenders.append(f"{name} imports netcorenoc.challenger")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "netcorenoc.challenger":
                        offenders.append(f"{name} imports netcorenoc.challenger")
    assert not offenders, (
        "the challenger is reachable from a module that is not part of shadow mode:\n  "
        + "\n  ".join(offenders)
        + "\n\nThe champion decides everything. If a real reason to widen this appears, it needs a "
        "DECISIONS entry and a security review, not a new name in `allowed`."
    )


def test_the_shadow_modules_never_reach_the_active_scorer_or_the_learner() -> None:
    """The three reaches that would end shadow mode: `Correlator.set_scorer`, an assignment to
    `.scorer`, and `learn.penalize()`.

    Checked over **parsed attribute and call names**, not over the file's text — these modules
    document what they may not do, in prose, on purpose, and a substring search would forbid the
    sentence that states the property. A test that made the documentation fail would push the
    documentation out of the file, which is the opposite of what it is for.
    """
    forbidden = {"set_scorer", "penalize"}
    for name in ("challenger.py", "training.py", "shadow.py", "shadow_eval.py", "shadow_report.py"):
        path = PKG / name
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                assert node.attr not in forbidden, f"{name} reaches `.{node.attr}`"
                # An assignment to **another object's** `.scorer` is what would swap the champion
                # out — `engine.scorer`, `correlator.scorer`. `self.scorer` is the shadow object's
                # own holder for the challenger it scores with, which is the feature, not the leak.
                if (
                    node.attr == "scorer"
                    and isinstance(node.ctx, ast.Store)
                    and not (isinstance(node.value, ast.Name) and node.value.id == "self")
                ):
                    raise AssertionError(f"{name} assigns to another object's `.scorer`")
            if isinstance(node, ast.Name):
                assert node.id not in forbidden, f"{name} references `{node.id}`"


async def test_a_fresh_engine_runs_the_champion_and_knows_nothing_of_a_challenger(
    store: Store,
) -> None:
    """The behavioural half of the isolation claim: after a full start, the active scorer is the
    built-in one and no attribute of the engine holds a challenger."""
    engine = Engine(store, asyncio.Queue())
    await engine.start()
    assert engine.correlator.scorer.active.scorer_id == scoring.DEFAULT_SCORER_ID
    assert isinstance(engine.correlator.scorer.active, AdditiveScorer)
    for value in vars(engine).values():
        assert not isinstance(value, LogisticScorer)


def test_a_challenger_and_the_champion_disagree_which_is_the_point() -> None:
    """A guard on the guards: if the two scorers happened to agree everywhere, every isolation
    test above would pass vacuously on a challenger that had no opinion of its own."""
    champion, challenger = AdditiveScorer(), LogisticScorer(FITTED)
    disagreements = 0
    for dt in range(0, 120, 7):
        for a in (0.0, 0.5, 1.0):
            f = features(float(dt), a, 1.0)
            champion_result: LinkScore = champion.score(f)
            if champion_result.linked != challenger.score(f).linked:
                disagreements += 1
    assert disagreements > 0, "the fitted challenger never disagrees with the champion"
