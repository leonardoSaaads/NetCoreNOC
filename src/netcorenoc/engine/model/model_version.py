"""The artefact: a scorer's parameters as a canonical document, and **the dispatch** that guards it.

v0.11.0 built this for two kinds. v0.14.0 grows it to five — `tree`, `forest` and
`gradient_boosting` join `additive` and `logistic` (DECISIONS #185) — and the growth is deliberately
**only** in this module and in the kind modules. `scorer_lifecycle` still asks *which scorer does
this row describe* and gets an answer; there is no registry, no plugin surface, no entry point and
no dynamic import (build prompt VI.5).

`PREREGISTRATION-0.11.0.md` §5 fixes the logistic degeneracy rules and `PREREGISTRATION-0.14.0.md`
§2 fixes the fourteen for the tree family, both **before any fit existed** that they could have been
chosen to suit. DECISIONS #160 and #161 record why this is a separate table holding a document
rather than columns on `scorer_config`.

**Pure.** Nothing here touches the store, the clock, the network or the engine. It parses, it
validates, and it constructs a `LinkScorer` — which is what lets the load path call it without
acquiring anything.

## Where a kind's rules live, and where its bounds live (DECISIONS #187)

**Every kind owns its rules; this module owns the dispatch and the bounds.** `challenger.py` holds
the logistic rules, `tree.py` T1-T6, `forest.py` F1-F4, `boosting.py` G1-G4 — and each takes
`FEATURE_BOUNDS`, `MAX_ABS_COEFFICIENT` and `LOGIT_MARGIN` as **arguments**. So each bound is
written down exactly once, here, and no kind module can import this one, which is what keeps the
family acyclic. `validate_document` remains the **single validation point**: a rule is reachable
only through it.

## The one thing this module exists to prevent

`SafeScorer` catches an **exception at score time**. It does not catch **a parameter set that scores
without raising and destroys grouping**.

An all-zero logistic weight vector raises nothing, returns a finite score, sums its contributions
correctly, and returns `linked=False` for every pair. Gate 0 measured what that actually looks like,
and it is worse than the sentence this docstring used to carry: the appliance does not go quiet, it
forms **2 256 singleton situations from 2 256 alarms with zero links** — *more* apparent activity,
not less — and the clock cannot see it either, because the same probe measured the degenerate
model's median within 0.019 us of a working one against 0.095 us of run-to-run noise. The only true
symptom is that no link is ever written.

**A payload validator without degeneracy rules is a type check wearing a safety check's name.**

## Why the feature bounds are real numbers and not an assumption

Reachability asks whether a threshold is *reachable*, which is meaningless without knowing what the
features can be. Both affinities are `learn.npmi`, clamped to `[0, 1]`. The decay is
`exp(-|dt| / TAU0_S)` with `|dt|` bounded by the correlation window, so it lives in
`[exp(-WINDOW_S/TAU0_S), 1]` — a little above zero, never at it. `WINDOW_S` is **imported from
`correlate`, not restated**: `correlate.select_candidates`' docstring records what happened the last
time two modules each kept their own copy of the window length.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from netcorenoc import scoring
from netcorenoc.correlate import WINDOW_S
from netcorenoc.engine.model import attribution, boosting, challenger, forest, tree

__all__ = [
    "KIND_ADDITIVE",
    "KIND_FOREST",
    "KIND_GRADIENT_BOOSTING",
    "KIND_LOGISTIC",
    "KIND_TREE",
    "MAX_ABS_COEFFICIENT",
    "SUPPORTED_KINDS",
    "TREE_KINDS",
    "ModelPayloadError",
    "canonical_document",
    "canonical_object",
    "document_for",
    "params_hash",
    "scorer_for",
    "validate_document",
]

KIND_ADDITIVE = scoring.DEFAULT_SCORER_ID  # 'additive'
KIND_LOGISTIC = "logistic"
KIND_TREE = tree.KIND
KIND_FOREST = forest.KIND
KIND_GRADIENT_BOOSTING = boosting.KIND

# The three kinds whose document holds a node list rather than a flat set of weights. Named as a set
# because three branches that each tested `kind == ...` would be three places to forget one.
TREE_KINDS = frozenset({KIND_TREE, KIND_FOREST, KIND_GRADIENT_BOOSTING})

# **A closed set, checked rather than trusted.** An unknown kind is not an error to raise, it is a
# payload this build does not understand — and the load path falls back to the built-in default for
# it, exactly as it does for a malformed document.
SUPPORTED_KINDS = frozenset({KIND_ADDITIVE, KIND_LOGISTIC, *TREE_KINDS})

# **The dispatch table**, and it is the whole of what this module knows about a tree kind. Each
# module publishes `KEYS`, `validate_payload` and `scorer_from_payload`; a fourth tree kind would be
# one entry here and one module, with nothing at any call site (DECISIONS #187).
_FAMILY: dict[str, Any] = {
    KIND_TREE: tree,
    KIND_FOREST: forest,
    KIND_GRADIENT_BOOSTING: boosting,
}

# The magnitude bound. **Argued in DECISIONS #164 rather than asserted**: a coefficient above ~9.2
# can saturate the logistic link on its own, at which point the feature is a hard switch whose
# per-term contribution still sums correctly while no longer meaning "this much evidence". **T6
# reuses it for a leaf value** (plan §2.1), for the same reason: a leaf that saturates the decision
# on its own is a hard switch, whatever produced it.
MAX_ABS_COEFFICIENT = 25.0

# The logistic analogue of `scoring.THRESHOLD_MARGIN`, in logit units. A threshold that sits exactly
# at an endpoint of the attainable logit range is the boundary case, and ambiguity about whether a
# scorer can still discriminate resolves to "it cannot".
LOGIT_MARGIN = 0.01

# What each feature can actually be, so reachability is arithmetic rather than an assumption.
# `decay` never reaches 0: `exp(-x)` is positive, and `|Δt|` is bounded by the correlation window.
FEATURE_BOUNDS: dict[str, tuple[float, float]] = {
    "decay": (math.exp(-WINDOW_S / challenger.TAU0_S), 1.0),
    "class_affinity": (0.0, 1.0),
    "entity_affinity": (0.0, 1.0),
}

# The same bounds indexed by feature id, which is how a tree names a feature. Derived rather than
# restated, so the two can never disagree.
ORDERED_BOUNDS: tuple[tuple[float, float], ...] = tuple(
    FEATURE_BOUNDS[name] for name in challenger.FEATURE_NAMES
)

_ADDITIVE_KEYS = ("w_t", "w_a", "w_e", "tau_s", "threshold")


class ModelPayloadError(ValueError):
    """A stored parameter document that is malformed, unknown, or degenerate. **Never activated.**

    One error type for every rejection reason, because every one of them resolves the same way on
    the load path — fall back to the built-in default, warn, audit — and a caller that had to
    enumerate reasons in order to fail safe would eventually miss one.
    """


def canonical_document(params: dict[str, float]) -> str:
    """A **flat** parameter document's stored form: sorted keys, tight separators.

    Canonicalised exactly as `audit.canonical`, `scoring.params_hash` and
    `challenger.Coefficients.to_json` are, so the stored text is reproducible from SQL and from
    Python alike and two runs that fitted the same numbers produce the same bytes.
    """
    return json.dumps(
        {key: float(value) for key, value in params.items()}, sort_keys=True, separators=(",", ":")
    )


def canonical_object(payload: dict[str, Any]) -> str:
    """The same canonicalisation for a document that is **not** flat.

    A tree is a list of nodes, a forest is a list of trees and a seed, and a boosted model is a list
    of trees and a shrinkage — none of which is a mapping of floats. Same sorted keys, same tight
    separators, same reproducibility; the only difference is that the values may be lists and, for
    `criterion`, a string. Kept beside :func:`canonical_document` rather than replacing it, because
    the flat form coerces to `float` and that coercion is load-bearing for the two older kinds.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def params_hash(kind: str, contract_version: str, document: str) -> str:
    """Stable fingerprint over the whole artefact, not just its numbers.

    `kind` is inside the hash deliberately: the identical weight document means different things to
    different kinds, so two artefacts that differ only in kind must not share a fingerprint.
    """
    return hashlib.sha256(f"{kind}\n{contract_version}\n{document}".encode()).hexdigest()


def _object(document: str) -> dict[str, Any]:
    """The document as a JSON object, or `ModelPayloadError`."""
    try:
        payload = json.loads(document)
    except (TypeError, ValueError) as exc:
        raise ModelPayloadError(f"parameter document is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ModelPayloadError(
            f"parameter document must be a JSON object, got {type(payload).__name__}"
        )
    return payload


def _flat_floats(payload: dict[str, Any]) -> dict[str, float]:
    """A flat mapping of finite floats, or `ModelPayloadError`.

    Rule 1 (finiteness) is applied here rather than per kind, because a non-finite value is not a
    degenerate *model*, it is a document that does not describe one. `json.loads` accepts `NaN` and
    `Infinity` by default, which is exactly how a non-finite coefficient would arrive.
    """
    out: dict[str, float] = {}
    for key, value in payload.items():
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ModelPayloadError(f"{key!r} must be a number, got {type(value).__name__}")
        if not math.isfinite(float(value)):
            raise ModelPayloadError(f"{key!r} must be a finite number, got {value!r}")
        out[str(key)] = float(value)
    return out


def _exact_keys(params: dict[str, Any], expected: tuple[str, ...], kind: str) -> None:
    """Feature completeness. **An unexpected key is malformed, not a warning.**

    Both directions matter and they fail for different reasons. A *missing* weight would be
    defaulted to something by whoever constructed the scorer, silently inventing a model nobody
    fitted. An *extra* key is a document written against a different feature set — a model that
    learned four features being served by a build that knows three is training/serving skew arriving
    through the database, which is the exact defect `PREREGISTRATION-0.9.0.md` §6 exists to detect.
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


def validate_document(kind: str, contract_version: str, document: str) -> dict[str, Any]:
    """**The single validation point for a stored artefact.** Raises `ModelPayloadError`.

    Raises, rather than returning a verdict, for `scoring.validate_params`' reason: the caller turns
    it into a 4xx and never stores the row, or — on the load path — into a fallback to the built-in
    default. A function that returned `(ok, reason)` would let a caller ignore the second half.

    The contract version is checked here too, so that *"this build does not implement that
    contract"* and *"these parameters are degenerate"* arrive at the same caller through the same
    door and cannot be handled differently by accident.

    Returns the parsed payload so `scorer_for` does not parse a second time — one parse, one set of
    numbers, and no way for the validated document and the constructed scorer to describe different
    models.
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

    payload = _object(document)
    if kind in TREE_KINDS:
        module = _FAMILY[kind]
        _exact_keys(payload, module.KEYS, kind)
        try:
            module.validate_payload(payload, bounds=ORDERED_BOUNDS, max_abs=MAX_ABS_COEFFICIENT)
        except (tree.TreePayloadError, attribution.AttributionError) as exc:
            raise ModelPayloadError(str(exc)) from exc
        return payload

    params = _flat_floats(payload)
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
        _exact_keys(params, challenger.LOGISTIC_KEYS, KIND_LOGISTIC)
        try:
            challenger.validate_logistic(
                params,
                bounds=FEATURE_BOUNDS,
                max_abs=MAX_ABS_COEFFICIENT,
                logit_margin=LOGIT_MARGIN,
            )
        except challenger.LogisticDegeneracyError as exc:
            raise ModelPayloadError(str(exc)) from exc
    return params


def scorer_for(kind: str, contract_version: str, document: str) -> scoring.LinkScorer:
    """Validate a stored artefact and build the scorer it describes. Raises `ModelPayloadError`.

    **This is the dispatch**, and it is the only one. `scorer_lifecycle` asks this module which
    scorer a row describes rather than deciding for itself, so a new kind is a branch here and
    nothing at the call site — deliberately closer to a `match` than to a plugin surface
    (build prompt VII.6). v0.14.0 adds three branches and nothing else.
    """
    params = validate_document(kind, contract_version, document)
    if kind in TREE_KINDS:
        try:
            return _FAMILY[kind].scorer_from_payload(  # type: ignore[no-any-return]
                params,
                contract_version=contract_version,
                document=document,
                fingerprint=params_hash(kind, contract_version, document),
            )
        except (tree.TreePayloadError, attribution.AttributionError) as exc:
            raise ModelPayloadError(str(exc)) from exc
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

    An :class:`~netcorenoc.attribution.AttributedScorer` **carries** its document, so the three tree
    kinds share one branch and the round trip is an identity rather than a re-serialisation — a node
    list rebuilt from a live scorer would have to reproduce the fit's node numbering, and "the same
    function under a different numbering" is the two-hashes-for-one-model failure `cart.fit`'s
    breadth-first construction exists to prevent.
    """
    if isinstance(scorer, attribution.AttributedScorer):
        return scorer.params_document
    if isinstance(scorer, challenger.LogisticScorer):
        return canonical_document({"threshold": scorer.threshold, **scorer.coefficients.as_dict()})
    if isinstance(scorer, scoring.AdditiveScorer):
        return canonical_document(scorer.params())
    raise ModelPayloadError(f"no canonical document is defined for {type(scorer).__name__}")
