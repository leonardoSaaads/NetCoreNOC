"""The link-scoring seam: the built-in scorer, its bounds, and the fail-safe wrapper.

The correlation link decision used to be an expression inlined in ``correlate.py``. Here it is
the *default implementation of an interface*: :class:`AdditiveScorer` computes exactly what
v0.5.0 computed, and :class:`~netcorenoc.scorer_contract.LinkScorer` is the contract any future
scorer must satisfy.

**The contract itself moved to `scorer_contract.py` in v0.14.0** (DECISIONS #191), at the 400-line
guard and on a seam that was already there: that module answers *what must a scorer satisfy*, this
one answers *what does the built-in scorer compute, and what happens when a scorer misbehaves*.
Every contract name is re-exported below, so ``from netcorenoc.scoring import ...`` is unchanged for
every existing importer — including ``correlate.py``, which this release may not touch by a byte.

Three properties are load-bearing:

* **Parity.** At the default parameters the arithmetic is byte-identical to v0.5.0 — same terms,
  computed in the same order (float addition is not associative), same strict ``> threshold``
  comparison. The eval gate proves it mechanically.
* **Explainability is contractual.** Every scorer must return a per-term breakdown
  (:class:`~netcorenoc.scorer_contract.TermContribution`), so "why did it decide that?" can never
  regress. The default emits the three terms the API and UI already show.
* **Purity.** ``score()`` is pure, deterministic, side-effect-free and inference-only. That is
  the type-level statement that no scorer reaches the network, the disk, or the clock — which is
  what forecloses an external scoring criterion on the hot path (DECISIONS #44) rather than
  merely discouraging it.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass

# Re-exported with the redundant-alias form, which is the explicit "this is a re-export" spelling
# both ruff and mypy understand. `correlate.py` imports every one of these from `netcorenoc.scoring`
# and this release may not change one byte of it, so the names have to stay here whatever module
# defines them (DECISIONS #191).
from netcorenoc.scorer_contract import (
    BASIS_SHAPLEY as BASIS_SHAPLEY,
)
from netcorenoc.scorer_contract import (
    BASIS_WEIGHTED_SUM as BASIS_WEIGHTED_SUM,
)
from netcorenoc.scorer_contract import (
    CONTRACT_VERSION as CONTRACT_VERSION,
)
from netcorenoc.scorer_contract import (
    DEFAULT_SCORER_ID as DEFAULT_SCORER_ID,
)
from netcorenoc.scorer_contract import (
    ContractVersionError as ContractVersionError,
)
from netcorenoc.scorer_contract import (
    LinkFeatures as LinkFeatures,
)
from netcorenoc.scorer_contract import (
    LinkScore as LinkScore,
)
from netcorenoc.scorer_contract import (
    LinkScorer as LinkScorer,
)
from netcorenoc.scorer_contract import (
    TermContribution as TermContribution,
)
from netcorenoc.scorer_contract import (
    check_contract_version as check_contract_version,
)
from netcorenoc.scorer_contract import (
    contract_major as contract_major,
)

# The coded defaults — the v0.5.0 constants, now the default scorer's dataclass defaults
# (the P2 tidy promised in EXTENSIBILITY-0.6-DRAFT.md). `correlate.py` re-exports these names.
TAU_S = 30.0
W_T = 0.3
W_A = 0.35
W_E = 0.35
LINK_THRESHOLD = 0.5

# -- validation bounds (DECISIONS #46) -------------------------------------------------------
# Every ambiguity resolves toward the tighter bound: a slightly-too-tight bound costs a little
# tuning range, a too-loose one lets an admin shatter or collapse every incident.
MIN_TAU_S = 1.0  # below 1 s the temporal term is a step function inside a single engine batch
MAX_TAU_S = 3600.0  # far beyond the 120 s window the term stops discriminating between pairs
MIN_WEIGHT_SUM = 0.10  # a vanishing weight sum makes every threshold unreachable: silent stop
MIN_THRESHOLD = 0.01  # threshold <= 0 links every candidate pair into one giant situation
THRESHOLD_MARGIN = 0.01  # threshold must stay this far below the max achievable score, else
# nothing ever links and every alarm is a singleton


class ScorerParamsError(ValueError):
    """A parameter set that is out of bounds or degenerate. Never stored (§validation)."""


def validate_params(w_t: float, w_a: float, w_e: float, tau_s: float, threshold: float) -> None:
    """Reject out-of-range **and degenerate** parameter sets. The single validation point.

    Range checks alone are not enough: a syntactically valid set can destroy correlation without
    touching an alarm row (threshold at zero merges everything; threshold at or above the maximum
    achievable score links nothing, ever). Raises :class:`ScorerParamsError` with a precise,
    operator-readable reason; the caller turns it into a 4xx and never stores the row.
    """
    for name, value in (("w_t", w_t), ("w_a", w_a), ("w_e", w_e), ("threshold", threshold)):
        if not math.isfinite(value):
            raise ScorerParamsError(f"{name} must be a finite number, got {value!r}")
        if not 0.0 <= value <= 1.0:
            raise ScorerParamsError(f"{name} must be between 0 and 1, got {value!r}")
    if not math.isfinite(tau_s):
        raise ScorerParamsError(f"tau_s must be a finite number, got {tau_s!r}")
    if not MIN_TAU_S <= tau_s <= MAX_TAU_S:
        raise ScorerParamsError(
            f"tau_s must be between {MIN_TAU_S} and {MAX_TAU_S} seconds, got {tau_s!r}"
        )
    weight_sum = w_t + w_a + w_e
    if weight_sum < MIN_WEIGHT_SUM:
        raise ScorerParamsError(
            f"the weights sum to {weight_sum:.4f}, below the minimum {MIN_WEIGHT_SUM}: no pair "
            "could ever reach the threshold and grouping would stop silently"
        )
    if threshold < MIN_THRESHOLD:
        raise ScorerParamsError(
            f"threshold must be at least {MIN_THRESHOLD}, got {threshold!r}: a threshold at or "
            "below zero links every candidate pair into one situation"
        )
    if threshold > weight_sum - THRESHOLD_MARGIN:
        raise ScorerParamsError(
            f"threshold {threshold!r} leaves no headroom below the maximum achievable score "
            f"{weight_sum:.4f} (margin {THRESHOLD_MARGIN}): nothing would ever link and every "
            "alarm would be a singleton"
        )


def params_hash(
    scorer_id: str,
    contract_version: str,
    w_t: float,
    w_a: float,
    w_e: float,
    tau_s: float,
    threshold: float,
) -> str:
    """Stable fingerprint of a parameter set, for provenance and change detection.

    Canonicalised the same way audit rows are (sorted keys, tight separators) so the value is
    reproducible from SQL, from Python, and from the migration seed alike.
    """
    canonical = json.dumps(
        {
            "scorer_id": scorer_id,
            "contract_version": contract_version,
            "w_t": w_t,
            "w_a": w_a,
            "w_e": w_e,
            "tau_s": tau_s,
            "threshold": threshold,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AdditiveScorer:
    """The built-in three-term score — the default, and the always-available safe fallback.

        s = w_t·e^(-Δt/τ) + w_a·A[c_i, c_j] + w_e·E[ne_i, ne_j],   linked ⟺ s > threshold

    The defaults are the v0.5.0 constants and the arithmetic is the v0.5.0 arithmetic in the
    v0.5.0 order — float addition is not associative, so the order is the thing that had to be
    preserved. Constructing one does **not** validate: the coded defaults are valid by
    construction, and a stored set is validated at its write and again at load
    (:func:`validate_params`), so the fallback path can never be blocked by its own guard.
    """

    w_t: float = W_T
    w_a: float = W_A
    w_e: float = W_E
    tau_s: float = TAU_S
    threshold: float = LINK_THRESHOLD
    scorer_id: str = DEFAULT_SCORER_ID
    contract_version: str = CONTRACT_VERSION

    def score(self, features: LinkFeatures) -> LinkScore:
        # Parity note: these three products and their left-to-right sum reproduce the v0.5.0
        # expression exactly. Do not reorder — float addition is not associative, and a last-bit
        # difference either side of `threshold` is a different grouping.
        decay = math.exp(-abs(features.delta_t_s) / self.tau_s)
        term_t = self.w_t * decay
        term_a = self.w_a * features.class_affinity
        term_e = self.w_e * features.entity_affinity
        total = term_t + term_a + term_e
        return LinkScore(
            linked=total > self.threshold,
            score=total,
            threshold=self.threshold,
            terms=(
                TermContribution("temporal", self.w_t, decay, term_t),
                TermContribution("class_affinity", self.w_a, features.class_affinity, term_a),
                TermContribution("entity_affinity", self.w_e, features.entity_affinity, term_e),
            ),
        )

    def params_fingerprint(self) -> str:
        return params_hash(
            self.scorer_id,
            self.contract_version,
            self.w_t,
            self.w_a,
            self.w_e,
            self.tau_s,
            self.threshold,
        )

    def params(self) -> dict[str, float]:
        """The five tunable parameters, for the API/UI read path and for audit details."""
        return {
            "w_t": self.w_t,
            "w_a": self.w_a,
            "w_e": self.w_e,
            "tau_s": self.tau_s,
            "threshold": self.threshold,
        }


def default_scorer() -> AdditiveScorer:
    """The coded-default scorer: the safe fallback the engine may always reach for."""
    return AdditiveScorer()


# A single scoring call is arithmetic over five floats; anything approaching this budget is a
# malfunction, not slow hardware. Deliberately generous so a loaded CI box never trips it.
SCORE_BUDGET_S = 0.5


class SafeScorer:
    """Fail-safe wrapper: any scorer misbehaviour degrades to the coded defaults.

    Prime directive: *the default is always available and is the safe fallback.* On any
    exception, contract violation (a non-:class:`LinkScore` result, a non-finite score, or an
    empty ``terms`` list — explainability is contractual), or an over-budget call, this wrapper
    switches permanently to :class:`AdditiveScorer` for the rest of the process, records the
    reason, and lets the engine surface it (`scorer.fallback` audit row + operator warning).
    **Fail-safe, never fail-open, never fail-stop**: correlation continues on the shipped
    default rather than stalling ingestion or silently linking everything.

    *Honest limit on "timeout":* ``score()`` is a synchronous, in-process call, so a scorer that
    hangs forever cannot be interrupted from here — the budget is checked after the call returns
    and degrades every **subsequent** call. Real preemption needs a worker process with
    ``resource.setrlimit``, which is specified for v0.8.0's plugin harness
    (`docs/architecture/SCORER-PLUGINS-0.8-DRAFT.md`), not built here — v0.6.0 ships exactly one
    scorer, and it is arithmetic.
    """

    def __init__(self, delegate: LinkScorer | None = None, budget_s: float = SCORE_BUDGET_S):
        self._fallback = default_scorer()
        self.delegate: LinkScorer = delegate if delegate is not None else self._fallback
        self.budget_s = budget_s
        self.degraded = False
        self.failures = 0
        self.last_error = ""
        self.audited = False  # set by the engine once it has written the scorer.fallback row

    @property
    def active(self) -> LinkScorer:
        """The scorer actually being used — the delegate, or the default after degradation."""
        return self._fallback if self.degraded else self.delegate

    @property
    def scorer_id(self) -> str:
        return self.active.scorer_id

    @property
    def contract_version(self) -> str:
        return self.active.contract_version

    def params_fingerprint(self) -> str:
        return self.active.params_fingerprint()

    def _degrade(self, reason: str) -> None:
        self.failures += 1
        self.last_error = reason
        self.degraded = True

    def score(self, features: LinkFeatures) -> LinkScore:
        if self.degraded:
            return self._fallback.score(features)
        started = time.monotonic()
        try:
            result = self.delegate.score(features)
        except Exception as exc:  # a scorer must never take the engine down
            self._degrade(f"{type(exc).__name__} raised by scorer {self.delegate.scorer_id!r}")
            return self._fallback.score(features)
        elapsed = time.monotonic() - started
        problem = _contract_violation(result)
        if problem is not None:
            self._degrade(f"scorer {self.delegate.scorer_id!r} {problem}")
            return self._fallback.score(features)
        if elapsed > self.budget_s:
            self._degrade(
                f"scorer {self.delegate.scorer_id!r} took {elapsed:.3f}s, over the "
                f"{self.budget_s}s budget"
            )
            return result  # this call already succeeded; the *next* one uses the default
        return result

    def warnings(self) -> list[str]:
        """Persistent operator warning after a degradation (surfaced in /api/stats)."""
        if not self.degraded:
            return []
        return [
            f"Link scorer fell back to the built-in defaults after {self.failures} failure(s) "
            f"({self.last_error}). Correlation is running on the shipped formula; check the "
            "scorer configuration and the logs."
        ]


def _contract_violation(result: object) -> str | None:
    """Why this result is not a usable :class:`LinkScore`, or None if it is fine."""
    if not isinstance(result, LinkScore):
        return f"returned {type(result).__name__}, not a LinkScore"
    if not math.isfinite(result.score):
        return f"returned a non-finite score ({result.score!r})"
    if not result.terms:
        return "returned no term breakdown (explainability is contractual)"
    return None
