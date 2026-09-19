"""What the live scorer is actually doing — counted as it decides (v0.18.0, Part II).

## The gap this closes

The appliance could measure a *challenger* in shadow mode, report on the feedback dataset, and
draw the promotion history. It could say **nothing at all about the scorer that is running**:
no per-decision record, no distribution of scores, no count of links made and refused, no drift
signal. `challenger_run` has 24 columns and not one is a loss or a residual. The champion was
measured by nothing, and the console drew the promotion history beside a correlator nobody could
inspect.

The question this module exists to answer, in the console, without an offline report:

> **Is the correlator doing a good job right now?**

It decomposes into six, and each one below is a counter rather than an opinion:

1. *Is it deciding at all?* — activations and candidate pairs evaluated.
2. *How often does it link?* — the accept rate, and whether it has moved.
3. *How sure is it?* — the distribution of scores, and how many decisions land in a narrow band
   either side of the threshold. A correlator whose decisions cluster on the threshold is
   guessing, and it looks exactly like one that is confident unless someone counts.
4. *What is carrying the links?* — which of the three terms contributed most to each accepted
   link. If entity affinity carries almost everything, F58 is happening live: `MIN_EDGE_N` is
   cleared by six alarms and the appliance is linking on learned topology alone.
5. *How much is it merging?* — merges per activation. A rising merge rate is the over-merge
   signature that F76 turned out to be.
6. *What is actually running?* — the scorer, its parameters, and whether it has degraded.

## Two horizons, because "right now" is the question

Every figure is reported **lifetime** (since this process started) and **recent** (the last
:data:`RECENT_ACTIVATIONS` activations). One number cannot answer *"is this normal?"*; two can,
and the difference between them is the only drift signal that needs no stored history.

## What this is not

**It is not evidence, and it must never become any.** Nothing here is written to the store,
nothing reaches `feedback`, `dataset_pair` or `shadow_opinion`, and no promotion path reads it.
Only a human gesture is evidence for promotion; measuring the champion is not labelling it. The
state is in memory and dies with the process, which is also the honest shape for a figure whose
caption says *"since this appliance started"*.

## Cost

Called once per activation from `Engine._process`, which runs under the batch lock and is not
the per-packet path. The work is a bounded loop over the pairs the correlator **already
evaluated** (at most `MAX_CANDIDATES`), plus integer increments — no allocation per pair, no
I/O, no lock of its own. `datagram_received` never reaches this module.
"""

from __future__ import annotations

import contextlib
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from netcorenoc.engine.correlate.correlate import CorrelationResult, WindowAlarm

#: How many activations the "recent" horizon covers. Large enough that a quiet minute does not
#: make the rate meaningless, small enough that a change shows up while an operator is watching.
RECENT_ACTIVATIONS = 500

#: Score-histogram edges. Ten equal buckets over [0, 1]: the score is a weighted sum of three
#: terms each in [0, 1] with weights summing to 1.0 at the defaults, so [0, 1] is the range and a
#: tenth is fine enough to see a cluster on the threshold without inviting a reading of noise.
HISTOGRAM_BUCKETS = 10

#: A decision within this much of the threshold is counted as *near* it. 0.05 is a tenth of the
#: default threshold — close enough that a small move in either affinity would flip the verdict,
#: which is exactly what "the correlator is guessing here" means.
NEAR_THRESHOLD = 0.05

#: The three terms the built-in scorer emits. Derived from the score at runtime rather than
#: listed as a constant anywhere else: a scorer that emits a different term set is counted under
#: its own names, and this module never has to be edited to keep up.
_UNATTRIBUTED = "none"


@dataclass
class _Activation:
    """One activation's decision summary — the unit the recent horizon is a window over."""

    evaluated: int
    linked: int
    near: int
    suppressed: int
    merged: int
    storm: bool
    carried: str


@dataclass
class CorrelationMonitor:
    """Counters over the champion's own decisions. In memory, aggregate, never evidence."""

    evaluated: int = 0
    linked: int = 0
    near_threshold: int = 0
    subtree_suppressed: int = 0
    activations: int = 0
    storm_activations: int = 0
    merges: int = 0
    histogram: list[int] = field(default_factory=lambda: [0] * HISTOGRAM_BUCKETS)
    carried_by: dict[str, int] = field(default_factory=dict)
    recent: deque[_Activation] = field(default_factory=lambda: deque(maxlen=RECENT_ACTIVATIONS))

    def observe(self, entry: WindowAlarm, outcome: CorrelationResult, merged: int = 0) -> None:
        """Record one activation's decisions. Never raises — observability is not a decision.

        `merged` is how many *existing* situations this activation fused, which the engine knows
        and the correlator does not; 0 for the ordinary case of joining one or starting one.

        `entry` is the activating alarm, and it is here for one reason: the subtree gate's
        condition is a fact about the **pair** (`different elements AND different enterprise
        roots`), and reading it off the emitted terms instead would count a pair whose affinity
        is honestly zero as a pair the gate refused. Those are different facts and an
        observability release may not blur them.
        """
        # A counter must never stop ingestion: `_observe` is arithmetic over values the
        # correlator already produced, so there is nothing here worth failing a batch for.
        with contextlib.suppress(Exception):  # pragma: no cover - defensive
            self._observe(entry, outcome, merged)

    def _observe(self, entry: WindowAlarm, outcome: CorrelationResult, merged: int) -> None:
        evaluated = linked = near = suppressed = 0
        best_score = -1.0
        carried = _UNATTRIBUTED
        for pair in outcome.evaluated:
            result = pair.result
            evaluated += 1
            bucket = min(HISTOGRAM_BUCKETS - 1, max(0, int(result.score * HISTOGRAM_BUCKETS)))
            self.histogram[bucket] += 1
            if abs(result.score - result.threshold) <= NEAR_THRESHOLD:
                near += 1
            # **The gate's own condition, not a fingerprint of it.** Two alarms on different
            # network elements whose trap OIDs are in different enterprise subtrees: exactly
            # what `scoring.cross_subtree_elements` refuses. Counting "the entity term came out
            # at zero" instead would fold in every pair whose affinity is honestly zero because
            # nothing has been learned yet, and those are different facts.
            if (
                entry.device_id != pair.other.device_id
                and entry.oid_root
                and pair.other.oid_root
                and entry.oid_root != pair.other.oid_root
            ):
                suppressed += 1
            if result.linked:
                linked += 1
                # Which term carried the strongest link this activation made — the drift signal.
                if result.score > best_score:
                    best_score = result.score
                    carried = _dominant_term(result)
        self.evaluated += evaluated
        self.linked += linked
        self.near_threshold += near
        self.subtree_suppressed += suppressed
        self.activations += 1
        self.merges += merged
        if outcome.storm:
            self.storm_activations += 1
        if linked:
            self.carried_by[carried] = self.carried_by.get(carried, 0) + 1
        self.recent.append(
            _Activation(evaluated, linked, near, suppressed, merged, outcome.storm, carried)
        )

    def snapshot(self) -> dict[str, Any]:
        """The whole picture, in the shape `/api/correlation` serves and the console draws.

        Rates are `None` — never 0.0 — when their denominator is zero. A correlator that has
        evaluated no pairs has no accept rate, and printing 0 % for that is the "chart of
        zeroes" defect the console's chart rules already forbid; `—` is the honest render.
        """
        window = list(self.recent)
        w_evaluated = sum(a.evaluated for a in window)
        w_linked = sum(a.linked for a in window)
        return {
            "lifetime": {
                "activations": self.activations,
                "evaluated": self.evaluated,
                "linked": self.linked,
                "refused": self.evaluated - self.linked,
                "accept_rate": _rate(self.linked, self.evaluated),
                "near_threshold": self.near_threshold,
                "near_threshold_rate": _rate(self.near_threshold, self.evaluated),
                "subtree_suppressed": self.subtree_suppressed,
                "storm_activations": self.storm_activations,
                "merges": self.merges,
                "merges_per_activation": _rate(self.merges, self.activations),
            },
            "recent": {
                "window_activations": len(window),
                "window_limit": RECENT_ACTIVATIONS,
                "evaluated": w_evaluated,
                "linked": w_linked,
                "refused": w_evaluated - w_linked,
                "accept_rate": _rate(w_linked, w_evaluated),
                "near_threshold": sum(a.near for a in window),
                "near_threshold_rate": _rate(sum(a.near for a in window), w_evaluated),
                "subtree_suppressed": sum(a.suppressed for a in window),
                "storm_activations": sum(1 for a in window if a.storm),
                "merges": sum(a.merged for a in window),
                "merges_per_activation": _rate(sum(a.merged for a in window), len(window)),
            },
            "histogram": {
                "buckets": list(self.histogram),
                "edges": [round(i / HISTOGRAM_BUCKETS, 2) for i in range(HISTOGRAM_BUCKETS + 1)],
            },
            "carried_by": dict(sorted(self.carried_by.items())),
            "near_threshold_band": NEAR_THRESHOLD,
        }


def _rate(numerator: int, denominator: int) -> float | None:
    """A ratio, or None when nothing was measured. Never a zero standing in for an absence."""
    return numerator / denominator if denominator else None


def _dominant_term(result: Any) -> str:
    """The name of the term that contributed most to this score.

    Ties go to the first term in the scorer's own order, which is deterministic. A scorer that
    emitted no terms cannot have a dominant one and is counted as unattributed rather than
    guessed at — `SafeScorer` already refuses an empty term list, so this is belt to that brace.
    """
    best = _UNATTRIBUTED
    best_value = float("-inf")
    for term in result.terms:
        if term.contribution > best_value:
            best_value = term.contribution
            best = term.name
    return best
