"""The artefact: a scorer's parameters as a canonical document, and the validator that guards it.

v0.11.0. `PREREGISTRATION-0.11.0.md` §5 fixes the logistic degeneracy rules and it is **not varied
afterwards**; DECISIONS #160 and #161 record why this is a separate table holding a document rather
than columns on `scorer_config`.

**Pure.** Nothing here touches the store, the clock, the network or the engine. It parses, it
validates, and it constructs a `LinkScorer`. That is what lets the load path call it without
acquiring anything, and it is the same type-level statement `scoring.py` makes about
`AdditiveScorer`.

## The one thing this module exists to prevent

`SafeScorer` catches an **exception at score time**. It does not catch **a parameter set that scores
without raising and destroys grouping**.

An all-zero logistic weight vector raises nothing. It returns a finite score, a full term breakdown
whose contributions sum correctly, and `linked=False` for every pair in the network — because every
pair gets the identical logit. Correlation stops, every guard stays green, and the only symptom is
that the appliance quietly forms no situations. That is the logistic analogue of `MIN_WEIGHT_SUM`,
and `MIN_WEIGHT_SUM` exists because *silently stops grouping* is a failure this project has already
reasoned about once.

**A payload validator without degeneracy rules is a type check wearing a safety check's name.** The
five rules below were registered in the plan **before any fit existed** that they could have been
chosen to suit.

## Why the feature bounds are real numbers and not an assumption

Rule 4 asks whether a threshold is *reachable*, which is meaningless without knowing what the
features can actually be. Both affinities are `learn.npmi`, documented and clamped to `[0, 1]`. The
decay is `exp(-|Δt| / TAU0_S)` and `|Δt|` is bounded by the correlation window, so it lives in
`[exp(-WINDOW_S/TAU0_S), 1]` — a little above zero, never at it.

`WINDOW_S` is **imported from `correlate`, not restated here.** `correlate.select_candidates`'
docstring records what happened the last time two modules each kept their own copy of the window
length: *"The two happened to agree, but nothing"* made them.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from netcorenoc import challenger, scoring
from netcorenoc.correlate import WINDOW_S

__all__ = [
    "KIND_ADDITIVE",
    "KIND_LOGISTIC",
    "MAX_ABS_COEFFICIENT",
    "SUPPORTED_KINDS",
    "ModelPayloadError",
    "canonical_document",
    "params_hash",
    "scorer_for",
    "validate_document",
]

KIND_ADDITIVE = scoring.DEFAULT_SCORER_ID  # 'additive'
KIND_LOGISTIC = "logistic"

# **A closed set, checked rather than trusted.** An unknown kind is not an error to raise, it is a
# payload this build does not understand — and the load path falls back to the built-in default for
# it, exactly as it does for a malformed document.
SUPPORTED_KINDS = frozenset({KIND_ADDITIVE, KIND_LOGISTIC})

# Rule 5's bound. **Argued in DECISIONS #164 rather than asserted**, as the plan's §5 requires.
# In one line: a single coefficient above ~9.2 can drive the logistic link from mid-range to
# saturated on its own, at which point the feature is a hard switch and the per-term contribution
# still sums correctly while no longer meaning "this much evidence". 25.0 leaves ample room for a
# genuinely strong learned effect and refuses the runaway magnitudes that are the signature of an
# unpenalised fit on separable data.
MAX_ABS_COEFFICIENT = 25.0

# The logistic analogue of `scoring.THRESHOLD_MARGIN`, in logit units. A threshold that sits exactly
# at an endpoint of the attainable logit range is the boundary case, and ambiguity about whether a
# scorer can still discriminate resolves to "it cannot".
LOGIT_MARGIN = 0.01

# What each feature can actually be, so rule 4 is arithmetic rather than an assumption.
# `decay` never reaches 0: `exp(-x)` is positive, and `|Δt|` is bounded by the correlation window.
FEATURE_BOUNDS: dict[str, tuple[float, float]] = {
    "decay": (math.exp(-WINDOW_S / challenger.TAU0_S), 1.0),
    "class_affinity": (0.0, 1.0),
    "entity_affinity": (0.0, 1.0),
}

_ADDITIVE_KEYS = ("w_t", "w_a", "w_e", "tau_s", "threshold")
_LOGISTIC_KEYS = ("intercept", "threshold", *challenger.FEATURE_NAMES)


class ModelPayloadError(ValueError):
    """A stored parameter document that is malformed, unknown, or degenerate. **Never activated.**

    One error type for every rejection reason, because every one of them resolves the same way on
    the load path — fall back to the built-in default, warn, audit — and a caller that had to
    enumerate reasons in order to fail safe would eventually miss one.
    """


def canonical_document(params: dict[str, float]) -> str:
    """The parameter document's stored form: sorted keys, tight separators.

    Canonicalised exactly as `audit.canonical`, `scoring.params_hash` and
    `challenger.Coefficients.to_json` are, so the stored text is reproducible from SQL and from
    Python alike and two runs that fitted the same numbers produce the same bytes.
    """
    return json.dumps(
        {key: float(value) for key, value in params.items()}, sort_keys=True, separators=(",", ":")
    )


def params_hash(kind: str, contract_version: str, document: str) -> str:
    """Stable fingerprint over the whole artefact, not just its numbers.

    `kind` is inside the hash deliberately: the identical weight document means different things to
    different kinds, so two artefacts that differ only in kind must not share a fingerprint.
    """
    return hashlib.sha256(f"{kind}\n{contract_version}\n{document}".encode()).hexdigest()


def _parsed(document: str) -> dict[str, float]:
    """The document as a flat mapping of finite floats, or `ModelPayloadError`.

    Rule 1 (finiteness) is applied here rather than per kind, because a non-finite value is not a
    degenerate *model*, it is a document that does not describe one. `json.loads` accepts `NaN` and
    `Infinity` by default, which is exactly how a non-finite coefficient would arrive.
    """
    try:
        payload = json.loads(document)
    except (TypeError, ValueError) as exc:
        raise ModelPayloadError(f"parameter document is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ModelPayloadError(
            f"parameter document must be a JSON object, got {type(payload).__name__}"
        )
    out: dict[str, float] = {}
    for key, value in payload.items():
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ModelPayloadError(f"{key!r} must be a number, got {type(value).__name__}")
        if not math.isfinite(float(value)):
            raise ModelPayloadError(f"{key!r} must be a finite number, got {value!r}")
        out[str(key)] = float(value)
    return out


def _exact_keys(params: dict[str, float], expected: tuple[str, ...], kind: str) -> None:
    """Rule 2 — feature completeness. **An unexpected key is malformed, not a warning.**

    Both directions matter and they fail for different reasons. A *missing* weight would be
    defaulted to something by whoever constructed the scorer, silently inventing a model nobody
    fitted. An *extra* key is a document written against a different feature set — a model that
    learned four features being served by a build that knows three is training/serving skew arriving
    through the database, which is the exact defect the plan's §6 exists to detect.
    """
    present, want = set(params), set(expected)
    if present != want:
        missing = sorted(want - present)
        unexpected = sorted(present - want)
        detail = ", ".join(
            part
            for part in (
                f"missing {missing}" if missing else "",
                f"unexpected {unexpected}" if unexpected else "",
            )
            if part
        )
        raise ModelPayloadError(f"{kind} parameter document has the wrong keys: {detail}")


def _validate_logistic(params: dict[str, float]) -> None:
    """The plan's §5, rules 2 to 5. Rule 1 was applied by :func:`_parsed`.

    Every rule refuses **its own case and no other**, which is what makes a per-rule test able to
    say more than "something was rejected".
    """
    _exact_keys(params, _LOGISTIC_KEYS, KIND_LOGISTIC)

    weights = {name: params[name] for name in challenger.FEATURE_NAMES}
    threshold = params["threshold"]
    intercept = params["intercept"]

    # RULE 3 — non-degenerate discrimination. An all-zero weight vector gives every pair the same
    # logit and the scorer stops discriminating SILENTLY. The intercept is deliberately not counted:
    # a non-zero intercept with zero weights is still constant, so it links everything or nothing.
    if all(weight == 0.0 for weight in weights.values()):
        raise ModelPayloadError(
            "every feature weight is zero: the logit is constant, so the scorer would give every "
            "pair the same answer and grouping would stop silently (the logistic analogue of "
            f"MIN_WEIGHT_SUM={scoring.MIN_WEIGHT_SUM})"
        )

    # RULE 5 — magnitude sanity. Checked before rule 4 because a runaway coefficient makes the
    # reachability arithmetic report a huge range and pass, so a build that ordered these the other
    # way would let the least plausible payloads through the rule written to catch them.
    for name, value in (("intercept", intercept), *weights.items()):
        if abs(value) > MAX_ABS_COEFFICIENT:
            raise ModelPayloadError(
                f"{name} is {value!r}, beyond the magnitude bound {MAX_ABS_COEFFICIENT}: a "
                "coefficient this large saturates the link function, turning a probabilistic "
                "scorer into a step function whose per-term explanation still sums correctly and "
                "no longer means anything (DECISIONS #164)"
            )

    # RULE 4 — threshold reachability, over the features' DECLARED ranges. The logit is monotone in
    # each feature, so the attainable extremes sit at the corners of the box: take each weight's
    # better and worse end independently.
    low = high = intercept
    for name, weight in weights.items():
        lo, hi = FEATURE_BOUNDS[name]
        low += min(weight * lo, weight * hi)
        high += max(weight * lo, weight * hi)

    if threshold >= high - LOGIT_MARGIN:
        raise ModelPayloadError(
            f"threshold {threshold!r} is at or above the highest attainable logit {high:.6f} "
            f"(margin {LOGIT_MARGIN}): no pair could ever cross it, so nothing would ever link and "
            "every alarm would be a singleton"
        )
    if threshold < low:
        raise ModelPayloadError(
            f"threshold {threshold!r} is below the lowest attainable logit {low:.6f}: every "
            "candidate pair would cross it, collapsing every alarm into one situation"
        )


def validate_document(kind: str, contract_version: str, document: str) -> dict[str, float]:
    """**The single validation point for a stored artefact.** Raises `ModelPayloadError`.

    Raises, rather than returning a verdict, for `scoring.validate_params`' reason: the caller turns
    it into a 4xx and never stores the row, or — on the load path — into a fallback to the built-in
    default. A function that returned `(ok, reason)` would let a caller ignore the second half.

    The contract version is checked here too, so that *"this build does not implement that
    contract"* and *"these parameters are degenerate"* arrive at the same caller through the same
    door and cannot be handled differently by accident.
    """
    if kind not in SUPPORTED_KINDS:
        raise ModelPayloadError(
            f"scorer kind {kind!r} is not one this build implements "
            f"({sorted(SUPPORTED_KINDS)}); refusing to activate it"
        )
    try:
        scoring.check_contract_version(contract_version)
    except scoring.ContractVersionError as exc:
        raise ModelPayloadError(str(exc)) from exc

    params = _parsed(document)
    if kind == KIND_ADDITIVE:
        # The additive kind reuses `scoring.validate_params` UNCHANGED. Its five degeneracy rules
        # were won by experience and this module does not get to reinterpret them — a second
        # implementation is how the rule the API enforces and the rule the loader enforces come to
        # disagree with nothing going red.
        _exact_keys(params, _ADDITIVE_KEYS, KIND_ADDITIVE)
        try:
            scoring.validate_params(
                params["w_t"], params["w_a"], params["w_e"], params["tau_s"], params["threshold"]
            )
        except scoring.ScorerParamsError as exc:
            raise ModelPayloadError(str(exc)) from exc
    else:
        _validate_logistic(params)
    return params


def scorer_for(kind: str, contract_version: str, document: str) -> scoring.LinkScorer:
    """Validate a stored artefact and build the scorer it describes. Raises `ModelPayloadError`.

    **This is the dispatch**, and it is the only one. `scorer_lifecycle` asks this module which
    scorer a row describes rather than deciding for itself, so a future kind is a branch here and
    nothing at the call site — which is as close to a registry as this release is willing to come,
    and deliberately closer to a `match` than to a plugin surface (build prompt VII.6).
    """
    params = validate_document(kind, contract_version, document)
    if kind == KIND_ADDITIVE:
        return scoring.AdditiveScorer(
            w_t=params["w_t"],
            w_a=params["w_a"],
            w_e=params["w_e"],
            tau_s=params["tau_s"],
            threshold=params["threshold"],
            scorer_id=KIND_ADDITIVE,
            contract_version=contract_version,
        )
    return challenger.LogisticScorer(
        coefficients=challenger.Coefficients(
            intercept=params["intercept"],
            decay=params["decay"],
            class_affinity=params["class_affinity"],
            entity_affinity=params["entity_affinity"],
        ),
        threshold=params["threshold"],
        scorer_id=KIND_LOGISTIC,
        contract_version=contract_version,
    )


def document_for(scorer: Any) -> str:
    """The canonical document describing a scorer this module could rebuild from.

    The inverse of :func:`scorer_for`, used when an artefact is registered from an object rather
    than from operator-supplied JSON. Kept here beside its inverse so the two cannot drift.
    """
    if isinstance(scorer, challenger.LogisticScorer):
        return canonical_document({"threshold": scorer.threshold, **scorer.coefficients.as_dict()})
    if isinstance(scorer, scoring.AdditiveScorer):
        return canonical_document(scorer.params())
    raise ModelPayloadError(f"no canonical document is defined for {type(scorer).__name__}")
