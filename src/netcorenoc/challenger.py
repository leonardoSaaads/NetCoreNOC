"""The challenger: a logistic `LinkScorer` that decides nothing.

**It is a `LinkScorer`, and that is the whole design** (DECISIONS #116). It satisfies the v0.6.0
Protocol **structurally** — no base class to inherit, no registry to edit, and `scoring.py` gains
nothing — which buys three things by contract rather than by promise:

1. **Explainability is inherited, not added.** A logistic model *is* a weighted sum before the link
   function, so a per-term contribution is what it already computes.
   `tests/test_challenger.py::test_term_contributions_sum_to_the_pre_link_score` asserts it.
2. **`SafeScorer` already wraps it**, so the fail-safe discipline — degrade to the coded defaults on
   an exception, a contract violation or an over-budget call — is inherited too.
3. **v0.11.0's promotion becomes a pointer move** in `scorer_config`, the mechanism that exists,
   rather than a parallel architecture.

**What it may never do**, and this is structural rather than conventional: reach a situation, a
link, the UI, an operator, `learn.penalize()`, or `engine.scorer`. There is **no promotion mechanism
in this release at all** — a release that could promote would be judged by the only metric it had,
which would be agreement with the champion.
`tests/test_challenger.py::test_no_code_path_makes_the_challenger_the_active_scorer` asserts it by
parsing the tree, not by reading it.

## `score()` returns the PRE-LINK score, and `threshold` is the pre-link threshold

The `LinkScore` contract compares `score` against `threshold` to produce `linked`, and
`AdditiveScorer` has the property that its terms sum exactly to its score. **That property is kept
here**, which fixes what `score` has to be: the **logit**, `b + Σ wᵢxᵢ`, with the intercept emitted
as its own term. `linked` is then `logit > 0`, which is exactly `p > 0.5` — the same decision,
expressed where the arithmetic is exact and the explanation adds up.

The probability is available separately, from :meth:`LogisticScorer.probability`, and that is what
calibration and the shadow table use. Two quantities, each in the place it is correct, rather than
one quantity that is slightly wrong in both.

## Three features and an intercept — and the fourth feature the plan registered

`docs/analysis/PREREGISTRATION-0.9.0.md` §2.3 registered **four** features: the three below plus
`same_oid_root`. The fourth **is not implemented**, and the reason is not convenience:

> `LinkFeatures` carries no trap OID, and neither does `correlate.WindowAlarm`. The online shadow
> path scores inside the engine from `LinkFeatures` alone, so `same_oid_root` is computable
> **offline and not online** — and a feature that cannot be served is a feature that *guarantees*
> training/serving skew, which is the exact defect §6 of the plan exists to detect. Adding it would
> have required editing `correlate.py`, which this release may not touch.

So the model has **four free parameters, not five**. The pre-registered floor of **50 `split` bags**
is **kept at fifty** rather than rescaled down to forty: `resolved = the more demanding of (project
floor, deployment policy)` runs monotone toward evidence (DECISIONS #114), and softening a floor
because the model got simpler is exactly the move that rule forbids. The deviation is reported in
the security review, never by editing the plan.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass

from netcorenoc.scoring import (
    CONTRACT_VERSION,
    LinkFeatures,
    LinkScore,
    TermContribution,
)

__all__ = [
    "CHALLENGER_SCORER_ID",
    "FEATURE_NAMES",
    "TAU0_S",
    "Coefficients",
    "LogisticScorer",
    "feature_vector",
    "sigmoid",
]

CHALLENGER_SCORER_ID = "logistic-shadow"

# The champion's τ, held fixed and NOT learned. Learning it makes the objective non-convex and the
# fit non-deterministic in the way prime directive 5 forbids. Recorded as a limitation in the
# pre-registration §2.3 rather than hidden as an implementation detail.
TAU0_S = 30.0

# The three live features, in the order the coefficient vector uses. Fixed here so that training,
# offline reconstruction and the online shadow path cannot disagree about it — the skew test
# (DECISIONS #119) is what proves that claim rather than repeating it.
FEATURE_NAMES = ("decay", "class_affinity", "entity_affinity")

# A logit magnitude beyond which `exp` would overflow to infinity. `SafeScorer` treats a non-finite
# score as a contract violation and degrades permanently, so the clamp is not cosmetic: an
# untrained or badly-scaled coefficient vector must not be able to take the challenger's wrapper
# out of service. ±700 is where `math.exp` reaches the double-precision limit.
_LOGIT_CLAMP = 700.0


def sigmoid(logit: float) -> float:
    """The logistic link, written so it cannot overflow on a large-magnitude logit.

    The two branches are algebraically identical and each keeps `exp`'s argument negative, which is
    the standard numerically-stable form. Deterministic, and identical in every process: it is one
    `math.exp` and two float operations.
    """
    clamped = max(-_LOGIT_CLAMP, min(_LOGIT_CLAMP, logit))
    if clamped >= 0.0:
        return 1.0 / (1.0 + math.exp(-clamped))
    scaled = math.exp(clamped)
    return scaled / (1.0 + scaled)


def feature_vector(
    delta_t_s: float, class_affinity: float, entity_affinity: float
) -> tuple[float, float, float]:
    """**The one place a feature vector is built.**

    Training, offline reconstruction and the online shadow path all call this function, with the
    values each has to hand — from `dataset_pair` in the first two, from `LinkFeatures` in the
    third. That is what makes the skew test a real test rather than a tautology: if the three
    disagreed it would be because one of them computed a feature differently, and there is exactly
    one function that could have.

    `abs(delta_t_s)` matches `AdditiveScorer`, whose decay is over `abs(features.delta_t_s)`; the
    captured `dataset_pair.delta_t_s` is already non-negative, so the two agree by construction and
    the `abs` is the belt to that brace.
    """
    return (math.exp(-abs(delta_t_s) / TAU0_S), class_affinity, entity_affinity)


@dataclass(frozen=True)
class Coefficients:
    """The learned parameters: an intercept and one weight per feature.

    Frozen, because a scorer that could be mutated after a fingerprint was taken would make the
    fingerprint a lie. All-zero is the honest **untrained** state — it gives every pair a logit of
    exactly 0.0 and therefore `linked=False` at the default threshold, which is the right behaviour
    for a challenger nobody has fitted: it abstains rather than guessing.
    """

    intercept: float = 0.0
    decay: float = 0.0
    class_affinity: float = 0.0
    entity_affinity: float = 0.0

    @property
    def weights(self) -> tuple[float, ...]:
        """The feature weights, in `FEATURE_NAMES` order."""
        return (self.decay, self.class_affinity, self.entity_affinity)

    def as_dict(self) -> dict[str, float]:
        return {"intercept": self.intercept, **dict(zip(FEATURE_NAMES, self.weights, strict=True))}

    @classmethod
    def from_dict(cls, payload: dict[str, float]) -> Coefficients:
        return cls(
            intercept=float(payload["intercept"]),
            decay=float(payload["decay"]),
            class_affinity=float(payload["class_affinity"]),
            entity_affinity=float(payload["entity_affinity"]),
        )

    @classmethod
    def from_vector(cls, intercept: float, weights: tuple[float, ...]) -> Coefficients:
        return cls(intercept, *weights)

    def to_json(self) -> str:
        """Canonicalised exactly as audit rows and `params_hash` are — sorted keys, tight
        separators — so the stored text is reproducible from SQL and from Python alike, and two
        runs that fitted the same numbers produce the same bytes."""
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class LogisticScorer:
    """A `LinkScorer` whose weights were learned, and which nothing consults.

    Frozen and stateless: `score()` reads only its own coefficients and the features it is handed,
    so it is pure, deterministic, side-effect-free and inference-only — the same type-level
    statement `scoring.py`'s docstring makes about `AdditiveScorer`, and the reason no scorer in
    this system can reach the network, the disk or the clock.

    **Memory is bounded and does not grow**: four floats, three strings, no cache, no accumulator.
    The admission filter asserts it rather than trusting this sentence.
    """

    coefficients: Coefficients = Coefficients()
    # The PRE-LINK threshold. 0.0 is exactly p = 0.5 through the logistic link, so the default is
    # the same decision the champion's 0.5 expresses, in the units where the explanation adds up.
    threshold: float = 0.0
    scorer_id: str = CHALLENGER_SCORER_ID
    contract_version: str = CONTRACT_VERSION

    def score(self, features: LinkFeatures) -> LinkScore:
        """The pre-link score, its verdict, and a term per free parameter.

        The intercept is emitted as its own term, with `value=1.0`, because it *is* a term of the
        sum: omitting it would leave contributions that do not add up to the score, and "the
        contributions sum to the score" is the property that makes the explanation checkable rather
        than decorative.

        Left-to-right summation in `FEATURE_NAMES` order, intercept first. Float addition is not
        associative, so the order is part of the contract: two processes summing differently would
        produce different last bits, and a last bit either side of the threshold is a different
        answer. This is the same reason `AdditiveScorer` fixes its own order.
        """
        values = feature_vector(
            features.delta_t_s, features.class_affinity, features.entity_affinity
        )
        weights = self.coefficients.weights
        total = self.coefficients.intercept
        terms = [TermContribution("intercept", self.coefficients.intercept, 1.0, total)]
        for name, weight, value in zip(FEATURE_NAMES, weights, values, strict=True):
            contribution = weight * value
            total += contribution
            terms.append(TermContribution(name, weight, value, contribution))
        return LinkScore(
            linked=total > self.threshold,
            score=total,
            threshold=self.threshold,
            terms=tuple(terms),
        )

    def probability(self, features: LinkFeatures) -> float:
        """The calibrated-or-not probability this pair should be linked.

        What the shadow table stores and what calibration is computed against, because a
        reliability curve over logits is unreadable. **Not** what `linked` is derived from — that
        comes from :meth:`score`, so the decision and the explanation cannot drift apart through a
        second application of the link function.
        """
        return sigmoid(self.score(features).score)

    def params_fingerprint(self) -> str:
        """Stable fingerprint over the learned coefficients and the threshold.

        Same canonicalisation as `scoring.params_hash`, so a reader comparing a challenger
        fingerprint with a champion one is comparing two hashes made the same way.
        """
        canonical = json.dumps(
            {
                "scorer_id": self.scorer_id,
                "contract_version": self.contract_version,
                "threshold": self.threshold,
                **self.coefficients.as_dict(),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @property
    def trained(self) -> bool:
        """Whether anything was ever fitted. An all-zero vector abstains, and says so."""
        return self.coefficients != Coefficients()
