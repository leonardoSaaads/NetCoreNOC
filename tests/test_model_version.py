"""The artefact and its per-kind validator (v0.11.0, Phase 3).

`PREREGISTRATION-0.11.0.md` §5 registers five degeneracy rules **before any fit existed** that they
could have been chosen to suit. This file tests them the way the build prompt requires: **a
payload-degeneracy test per rule, each rejecting its own case and no other.**

That last clause is the whole design of this file. A test that only asserted *"this payload is
rejected"* would pass against a validator that rejected everything — the failure mode Gate 2's
control caught in the pointer `CHECK`, one layer down. So every rule's test asserts the **reason**,
and `test_a_valid_logistic_payload_is_accepted` is the standing control for all five.
"""

from __future__ import annotations

import json
import math

import pytest

from netcorenoc import scoring
from netcorenoc.engine.model import challenger, model_version
from netcorenoc.engine.model.model_version import KIND_ADDITIVE, KIND_LOGISTIC, ModelPayloadError

CV = scoring.CONTRACT_VERSION

# A payload that is valid under all five rules. Every degeneracy test below is this document with
# ONE field changed, so a rejection can only be attributable to that field.
GOOD_LOGISTIC = {
    "intercept": -1.5,
    "decay": 2.0,
    "class_affinity": 1.2,
    "entity_affinity": 0.8,
    "threshold": 0.0,
}

GOOD_ADDITIVE = {"w_t": 0.3, "w_a": 0.35, "w_e": 0.35, "tau_s": 30.0, "threshold": 0.5}


def document(**overrides: float) -> str:
    return model_version.canonical_document({**GOOD_LOGISTIC, **overrides})


def rejects(kind: str, doc: str, *, because: str) -> str:
    """Assert the payload is refused, and refused **for the stated reason**.

    The `because` match is what makes a per-rule test a per-rule test. Without it, five tests would
    all pass against a validator with one rule and four dead branches.
    """
    with pytest.raises(ModelPayloadError, match=because):
        model_version.validate_document(kind, CV, doc)
    return doc


# -- the control, first --------------------------------------------------------------------


def test_a_valid_logistic_payload_is_accepted() -> None:
    """**The control for every test below.** A validator that refused everything would satisfy all
    five degeneracy tests and make the scorer kind unusable, which is a worse defect than the ones
    they guard against and is invisible to a test that only checks that bad input fails."""
    params = model_version.validate_document(KIND_LOGISTIC, CV, document())
    assert params == GOOD_LOGISTIC
    scorer = model_version.scorer_for(KIND_LOGISTIC, CV, document())
    assert isinstance(scorer, challenger.LogisticScorer)
    assert scorer.trained, "the control payload must describe a fitted model, not the zero vector"


def test_a_valid_additive_payload_is_accepted_and_rebuilds_the_default() -> None:
    """The additive kind still works, and at the seeded parameters it **is** the coded default."""
    scorer = model_version.scorer_for(
        KIND_ADDITIVE, CV, model_version.canonical_document(GOOD_ADDITIVE)
    )
    assert isinstance(scorer, scoring.AdditiveScorer)
    assert scorer.params() == GOOD_ADDITIVE
    assert scorer.params_fingerprint() == scoring.default_scorer().params_fingerprint()


# -- rule 1: finiteness --------------------------------------------------------------------


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_rule_1_refuses_a_non_finite_coefficient(literal: str) -> None:
    """`json.loads` accepts `NaN` and `Infinity` **by default**, which is exactly how a non-finite
    coefficient arrives from a diverged fit that was serialised without complaint."""
    doc = json.dumps({**GOOD_LOGISTIC, "decay": None}).replace("null", literal)
    rejects(KIND_LOGISTIC, doc, because="must be a finite number")


def test_rule_1_refuses_a_non_numeric_value() -> None:
    doc = json.dumps({**GOOD_LOGISTIC, "decay": "2.0"})
    rejects(KIND_LOGISTIC, doc, because="must be a number, got str")


def test_rule_1_refuses_a_boolean_which_python_would_otherwise_treat_as_a_number() -> None:
    """`isinstance(True, int)` is `True` in Python, so a `bool` slips through a naive numeric check
    and becomes a weight of exactly 1.0 or 0.0 that nobody fitted."""
    doc = json.dumps({**GOOD_LOGISTIC, "decay": True})
    rejects(KIND_LOGISTIC, doc, because="must be a number, got bool")


# -- rule 2: feature completeness ----------------------------------------------------------


def test_rule_2_refuses_a_missing_weight() -> None:
    """A missing weight would be defaulted by whoever built the scorer, silently inventing a model
    nobody fitted."""
    payload = dict(GOOD_LOGISTIC)
    del payload["class_affinity"]
    rejects(KIND_LOGISTIC, json.dumps(payload), because="missing.*class_affinity")


def test_rule_2_refuses_an_unexpected_key() -> None:
    """**An unexpected key is a malformed document, not a warning** (§5 rule 2, verbatim).

    A model that learned four features being served by a build that knows three is training/serving
    skew arriving through the database — the exact defect the plan's §6 exists to detect.
    """
    doc = json.dumps({**GOOD_LOGISTIC, "same_oid_root": 0.4})
    rejects(KIND_LOGISTIC, doc, because="unexpected.*same_oid_root")


def test_rule_2_applies_to_the_additive_kind_too() -> None:
    """The additive kind gets the same key discipline, so a document cannot arrive with a logistic
    shape and be read as additive."""
    rejects(KIND_ADDITIVE, document(), because="wrong keys")


# -- rule 3: non-degenerate discrimination -------------------------------------------------


def test_rule_3_refuses_the_all_zero_weight_vector() -> None:
    """**The rule this module exists for.**

    An all-zero weight vector raises nothing, returns a finite score, and produces a term breakdown
    that sums correctly — while giving every pair in the network the identical logit. `SafeScorer`
    sees a perfectly well-behaved scorer. Grouping stops and every guard stays green.
    """
    rejects(
        KIND_LOGISTIC,
        document(decay=0.0, class_affinity=0.0, entity_affinity=0.0),
        because="every feature weight is zero",
    )


def test_rule_3_is_not_fooled_by_a_non_zero_intercept() -> None:
    """An intercept does not restore discrimination: the logit is still constant across every pair,
    so the scorer links everything or nothing rather than deciding anything."""
    rejects(
        KIND_LOGISTIC,
        document(intercept=3.0, decay=0.0, class_affinity=0.0, entity_affinity=0.0),
        because="every feature weight is zero",
    )


def test_rule_3_permits_a_single_live_feature() -> None:
    """The rule is *not all zero*, not *all non-zero*. A model that learned one feature mattered is
    a legitimate model, and refusing it would be encoding a prior about the network.

    The weight is 3.0 rather than 1.5, and the difference is instructive: at 1.5 against an
    intercept of -1.5 the highest attainable logit is **exactly** the threshold, so rule 4 refuses
    the payload — correctly, because such a model links nothing ever. The first version of this test
    used 1.5 and read as rule 3 being too strict when it was rule 4 being right.
    """
    params = model_version.validate_document(
        KIND_LOGISTIC, CV, document(decay=0.0, class_affinity=0.0, entity_affinity=3.0)
    )
    assert params["entity_affinity"] == 3.0


# -- rule 4: threshold reachability --------------------------------------------------------


def test_rule_4_refuses_a_threshold_no_pair_could_reach() -> None:
    """A threshold at or above the highest attainable logit links **nothing, ever** — the logistic
    analogue of `scoring.THRESHOLD_MARGIN`, and the rule a build is most likely to omit."""
    highest = GOOD_LOGISTIC["intercept"] + sum(
        max(GOOD_LOGISTIC[name] * lo, GOOD_LOGISTIC[name] * hi)
        for name, (lo, hi) in model_version.FEATURE_BOUNDS.items()
    )
    rejects(
        KIND_LOGISTIC,
        document(threshold=highest),
        because="at or above the highest attainable logit",
    )


def test_rule_4_refuses_a_threshold_every_pair_would_cross() -> None:
    """A threshold below the lowest attainable logit links **everything** — one giant situation,
    which is what `scoring.MIN_THRESHOLD` refuses on the additive side."""
    rejects(KIND_LOGISTIC, document(threshold=-99.0), because="below the lowest attainable logit")


def test_rule_4_uses_the_features_real_ranges_and_not_the_unit_box() -> None:
    """The bounds are the features' **attainable** ranges, and `decay` never reaches zero.

    `decay = exp(-|Δt| / TAU0_S)` with `|Δt|` bounded by the correlation window, so its floor is
    `exp(-WINDOW_S/TAU0_S)` — a little above zero. Using `[0, 1]` instead would compute a lower
    minimum than any pair can produce, and would therefore **accept** a threshold that in fact links
    every pair. The window is imported from `correlate`, never restated.
    """
    lo, hi = model_version.FEATURE_BOUNDS["decay"]
    assert (lo, hi) == (math.exp(-120.0 / challenger.TAU0_S), 1.0)
    assert 0.0 < lo < 0.02, "decay's floor must be above zero and small"
    for name in ("class_affinity", "entity_affinity"):
        assert model_version.FEATURE_BOUNDS[name] == (0.0, 1.0), "npmi is documented as [0, 1]"


# -- rule 5: magnitude sanity --------------------------------------------------------------


@pytest.mark.parametrize("field", ["intercept", "decay", "class_affinity", "entity_affinity"])
def test_rule_5_refuses_a_saturating_coefficient(field: str) -> None:
    """Every coefficient is bounded, the intercept included — it is a term of the same sum."""
    rejects(
        KIND_LOGISTIC,
        document(**{field: model_version.MAX_ABS_COEFFICIENT + 0.001}),
        because="beyond the magnitude bound",
    )
    rejects(
        KIND_LOGISTIC,
        document(**{field: -model_version.MAX_ABS_COEFFICIENT - 0.001}),
        because="beyond the magnitude bound",
    )


def test_rule_5_permits_a_strong_but_convergent_effect() -> None:
    """The bound separates *a strong learned effect* from *an optimisation that did not converge*
    (DECISIONS #164). A model that says entity affinity is near-conclusive is allowed to say so."""
    params = model_version.validate_document(
        KIND_LOGISTIC, CV, document(entity_affinity=model_version.MAX_ABS_COEFFICIENT - 0.001)
    )
    assert params["entity_affinity"] == pytest.approx(model_version.MAX_ABS_COEFFICIENT - 0.001)


def test_rule_5_is_checked_before_rule_4_and_the_order_is_load_bearing() -> None:
    """A runaway coefficient makes the reachability arithmetic report an enormous attainable range,
    which **passes** rule 4. A build that ordered these the other way would let the least plausible
    payloads through the rule written to catch them.

    The payload here would satisfy rule 4 comfortably — the range is vast — and must still be
    refused, **by rule 5's message**.
    """
    rejects(
        KIND_LOGISTIC, document(decay=400.0, threshold=5.0), because="beyond the magnitude bound"
    )


# -- the kind and the contract version -----------------------------------------------------


def test_an_unknown_kind_is_refused_by_name() -> None:
    rejects("onnx", document(), because="is not one this build implements")


def test_an_unsupported_contract_major_is_refused() -> None:
    with pytest.raises(ModelPayloadError, match="not supported by this build"):
        model_version.validate_document(KIND_LOGISTIC, "2.0", document())


def test_a_minor_contract_bump_is_still_supported() -> None:
    """Only the MAJOR component gates activation (DECISIONS #49), and that is unchanged here."""
    model_version.validate_document(KIND_LOGISTIC, "1.7", document())


# -- the fingerprint ------------------------------------------------------------------------


def test_the_kind_is_inside_the_fingerprint() -> None:
    """The identical weight document means different things to different kinds, so two artefacts
    that differ only in kind must not share a fingerprint."""
    doc = document()
    assert model_version.params_hash(KIND_LOGISTIC, CV, doc) != model_version.params_hash(
        KIND_ADDITIVE, CV, doc
    )


def test_the_canonical_document_is_byte_stable_across_key_order() -> None:
    """Two runs that fitted the same numbers produce the same bytes, whatever order they built the
    mapping in — the property that lets `params_hash` be compared at all."""
    forward = model_version.canonical_document(GOOD_LOGISTIC)
    backward = model_version.canonical_document(dict(reversed(list(GOOD_LOGISTIC.items()))))
    assert forward == backward
    expected = (
        '{"class_affinity":1.2,"decay":2.0,"entity_affinity":0.8,"intercept":-1.5,"threshold":0.0}'
    )
    assert forward == expected


def test_document_for_round_trips_the_parameters_of_every_supported_kind() -> None:
    """`document_for` and `scorer_for` are inverses **over the parameters**, and they live beside
    each other so they cannot drift into describing different artefacts."""
    rebuilt_additive = model_version.scorer_for(
        KIND_ADDITIVE, CV, model_version.document_for(scoring.default_scorer())
    )
    assert isinstance(rebuilt_additive, scoring.AdditiveScorer)
    assert rebuilt_additive.params_fingerprint() == scoring.default_scorer().params_fingerprint()

    fitted = challenger.LogisticScorer(coefficients=challenger.Coefficients(-1.5, 2.0, 1.2, 0.8))
    rebuilt = model_version.scorer_for(KIND_LOGISTIC, CV, model_version.document_for(fitted))
    assert isinstance(rebuilt, challenger.LogisticScorer)
    assert rebuilt.coefficients == fitted.coefficients
    assert rebuilt.threshold == fitted.threshold


def test_a_promoted_logistic_model_is_not_the_shadow_challenger_and_says_so() -> None:
    """**The identities differ on purpose, and the fingerprints differ with them.**

    `challenger.CHALLENGER_SCORER_ID` is `'logistic-shadow'` — a scorer that runs beside the
    champion and decides nothing. A model version the pointer names is `'logistic'`, and it decides
    every grouping in the network. Those are different objects and `params_fingerprint` includes the
    `scorer_id`, so they hash differently even at identical coefficients.

    That is the right answer rather than an inconvenience: an operator reading `/api/scorer` must be
    able to tell *the challenger is being evaluated* from *the challenger is what is running*, and a
    shared fingerprint would make a shadow run and a promotion indistinguishable in provenance.
    """
    fitted = challenger.LogisticScorer(coefficients=challenger.Coefficients(-1.5, 2.0, 1.2, 0.8))
    promoted = model_version.scorer_for(KIND_LOGISTIC, CV, model_version.document_for(fitted))
    assert fitted.scorer_id == challenger.CHALLENGER_SCORER_ID == "logistic-shadow"
    assert promoted.scorer_id == KIND_LOGISTIC == "logistic"
    assert promoted.params_fingerprint() != fitted.params_fingerprint()
