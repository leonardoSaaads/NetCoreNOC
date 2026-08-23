"""The scoring **contract**: what any scorer is handed, what it must return, and its version.

Split out of `scoring.py` in v0.14.0 at the 400-line guard (DECISIONS #191), and the seam is a real
one rather than a size accident. This module answers *what must a scorer satisfy*; `scoring.py`
answers *what does the built-in scorer compute, and what happens when a scorer misbehaves*. The
first has no implementation in it at all — no arithmetic, no defaults, no fail-safe — which is what
lets `model_version`, `attribution` and the three v0.14.0 model kinds depend on the contract without
dragging in `AdditiveScorer` and `SafeScorer`.

Nothing here imports from `scoring`, so the dependency runs one way and the pair cannot cycle.
**Every name in this module is re-exported by `netcorenoc.scoring`**, so `correlate.py` — which this
release may not touch by a single byte — keeps its import list exactly as it was.

Three properties are load-bearing and they belong to the contract rather than to any implementation:

* **Parity.** The default's arithmetic is byte-identical to v0.5.0 — same terms, computed in the
  same order (float addition is not associative), same strict ``> threshold`` comparison. The eval
  gate proves it mechanically.
* **Explainability is contractual.** Every scorer must return a per-term breakdown
  (:class:`TermContribution`), so "why did it decide that?" can never regress.
* **Purity.** ``score()`` is pure, deterministic, side-effect-free and inference-only. That is the
  type-level statement that no scorer reaches the network, the disk, or the clock — which is what
  forecloses an external scoring criterion on the hot path (DECISIONS #44) rather than merely
  discouraging it.

Contract versioning (DECISIONS #49): adding an *optional* field to :class:`LinkFeatures` or a term
to :class:`LinkScore` is a **minor** bump; changing or removing an existing field is a **major**
bump. A stored configuration whose declared major version this code does not support is refused at
activation, never coerced. v0.14.0 makes exactly one such bump — two optional fields on
:class:`LinkScore` — and it is minor.
"""

from __future__ import annotations

import math
from typing import NamedTuple, Protocol, runtime_checkable

__all__ = [
    "BASIS_SHAPLEY",
    "BASIS_WEIGHTED_SUM",
    "CONTRACT_VERSION",
    "DEFAULT_SCORER_ID",
    "FEATURE_NAMES",
    "TAU0_S",
    "ContractVersionError",
    "LinkFeatures",
    "LinkScore",
    "LinkScorer",
    "TermContribution",
    "check_contract_version",
    "contract_major",
    "feature_vector",
]

# The contract version this code implements. Only the MAJOR component gates activation.
CONTRACT_VERSION = "1.0"
DEFAULT_SCORER_ID = "additive"

# -- the feature vocabulary ------------------------------------------------------------------
#
# Moved here from `challenger.py` in v0.14.0 (DECISIONS #192), and the move is a correction rather
# than a tidy-up. These three names are **not the challenger's**: the champion reads them too, and
# from v0.14.0 so do the tree kinds through `attribution.py`. Leaving them in the challenger's
# module made `tests/test_challenger.py::test_no_code_path_makes_the_challenger_the_active_scorer`
# — the guard that keeps a shadow model off the champion path — fire on a module that imports a
# constant. A guard that fires on the wrong thing gets widened until it fires on nothing.
#
# `challenger.py` re-exports all three, so every existing importer is unchanged.

# The champion's tau, held fixed and NOT learned. Learning it makes the objective non-convex and the
# fit non-deterministic in the way prime directive 5 forbids. Recorded as a limitation in
# `PREREGISTRATION-0.9.0.md` §2.3 rather than hidden as an implementation detail.
TAU0_S = 30.0

# The three live features, in the order every coefficient vector, every node's `feature` id and
# every attribution uses. Fixed here so that training, offline reconstruction, the online shadow
# path and the tree family cannot disagree about it — the skew test (DECISIONS #119) is what proves
# that claim rather than repeating it.
FEATURE_NAMES = ("decay", "class_affinity", "entity_affinity")


def feature_vector(
    delta_t_s: float, class_affinity: float, entity_affinity: float
) -> tuple[float, float, float]:
    """**The one place a feature vector is built.**

    Training, offline reconstruction, the online shadow path and every tree kind call this function,
    with the values each has to hand — from `dataset_pair` in the first two, from
    :class:`LinkFeatures` in the others. That is what makes the skew test a real test rather than a
    tautology: if they disagreed it would be because one of them computed a feature differently, and
    there is exactly one function that could have.

    ``abs(delta_t_s)`` matches :class:`AdditiveScorer`, whose decay is over ``abs(delta_t_s)``; the
    captured ``dataset_pair.delta_t_s`` is already non-negative, so the two agree by construction
    and the ``abs`` is the belt to that brace.
    """
    return (math.exp(-abs(delta_t_s) / TAU0_S), class_affinity, entity_affinity)


class ContractVersionError(ValueError):
    """A stored configuration declaring a major contract version this code does not implement."""


class LinkFeatures(NamedTuple):
    """Everything a scorer may see about one candidate pair, built once by the engine.

    The first block is exactly what the v0.5.0 computation used. The second block is **reserved**:
    every field is ``None`` in v0.6.0 and ignored by :class:`AdditiveScorer`. They exist so the
    X.733 / 3GPP TS 32.111 features (deferred behind MIB enrichment) and v0.8.0's richer scorers
    are additive, non-breaking extensions — populating one is a minor contract bump.

    A ``NamedTuple`` rather than a frozen dataclass, for two reasons that both matter here. It is
    *genuinely* immutable (a tuple, not a dataclass that raises on ``__setattr__``), which is the
    guarantee the "pure, side-effect-free" contract needs; and it is built at C speed, which
    matters because the engine constructs one **per candidate pair** — up to 100 per activated
    alarm. A frozen dataclass costs ~2 µs per construction here; this costs ~0.4 µs. Appending an
    optional field stays a minor contract bump either way (DECISIONS #49).
    """

    delta_t_s: float
    class_i: int
    class_j: int
    class_affinity: float  # A[class_i, class_j]
    ne_i: int
    ne_j: int
    entity_affinity: float  # E[ne_i, ne_j] (or the structural intra-NE value)

    # Reserved for v0.7.0+ — None throughout v0.6.0.
    severity_i: int | None = None
    severity_j: int | None = None
    topo_distance: float | None = None
    probable_cause_i: str | None = None
    probable_cause_j: str | None = None
    event_type_i: str | None = None
    event_type_j: str | None = None


# How a scorer derived its terms (v0.14.0, DECISIONS #186). A tree predicts a leaf value, not a
# weighted sum, so a per-feature attribution for one is a Shapley value — neither a weight nor a
# weight times a value. Putting it in a field named `weight` would be a lie in the field's own name.
BASIS_WEIGHTED_SUM = "weighted-sum"
BASIS_SHAPLEY = "shapley"


class TermContribution(NamedTuple):
    """One explainable term of a link score.

    ``contribution = weight · value`` **only when** :attr:`LinkScore.basis` is
    :data:`BASIS_WEIGHTED_SUM`. Under :data:`BASIS_SHAPLEY` ``contribution`` is a Shapley value,
    ``value`` is still the feature's own value, and ``weight`` is **undefined** — written as ``0.0``
    and meaning nothing. Read the basis before reading this field.
    """

    name: str
    weight: float
    value: float
    contribution: float


class LinkScore(NamedTuple):
    """A scorer's verdict *and* its explanation. The breakdown is contractual, not optional.

    ``terms`` is a tuple: a scorer hands out its explanation, it does not lend a mutable list
    that a caller could edit under it.

    ``basis`` and ``base_value`` are optional and defaulted — a *minor* bump (DECISIONS #49) — so
    every scorer and reader written before v0.14.0 is unaffected. ``base_value`` is what the
    contributions are measured **from**: zero for a weighted sum, the model's mean output over its
    registered background set for a Shapley attribution. So the check that holds for both bases is
    ``sum(contributions) + base_value == score``.
    """

    linked: bool
    score: float
    threshold: float
    terms: tuple[TermContribution, ...]
    basis: str = BASIS_WEIGHTED_SUM
    base_value: float = 0.0


@runtime_checkable
class LinkScorer(Protocol):
    """The scoring contract. One default implementation ships; the rest is future work.

    ``score`` must be pure, deterministic, side-effect-free and inference-only: no I/O, no clock,
    no network, no mutation of the features or of the scorer.

    ``scorer_id`` and ``contract_version`` are declared read-only so a frozen dataclass (the
    default) and a property-backed wrapper both satisfy the protocol structurally — no base class
    to inherit and no registry to edit, which is what lets v0.8.0 drop in an adapter without
    touching ``src/netcorenoc/``.
    """

    @property
    def scorer_id(self) -> str: ...

    @property
    def contract_version(self) -> str: ...

    def score(self, features: LinkFeatures) -> LinkScore: ...

    def params_fingerprint(self) -> str: ...


def contract_major(version: str) -> int:
    """The MAJOR component of a semver-ish contract version ('1.0' -> 1)."""
    try:
        return int(version.split(".", 1)[0])
    except ValueError as exc:
        raise ContractVersionError(f"malformed contract version {version!r}") from exc


def check_contract_version(version: str) -> None:
    """Refuse a configuration whose major contract version this code does not implement."""
    if contract_major(version) != contract_major(CONTRACT_VERSION):
        raise ContractVersionError(
            f"scorer contract version {version!r} is not supported by this build "
            f"(implements {CONTRACT_VERSION!r}); refusing to activate it"
        )
