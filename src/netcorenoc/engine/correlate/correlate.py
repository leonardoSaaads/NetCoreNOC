"""Sliding-window correlation: candidate selection, the link decision, and its bookkeeping.

For each newly activated alarm the correlator builds a :class:`~netcorenoc.scoring.LinkFeatures`
against every alarm currently in the 120 s window and asks the active
:class:`~netcorenoc.scoring.LinkScorer` for a verdict. The built-in
:class:`~netcorenoc.scoring.AdditiveScorer` computes

    s = w_t · e^(-Δt/τ) + w_A · A[c_i, c_j] + w_E · E[v_i, v_j]

with same device ⇒ E = 1 and defaults w_t = 0.3, w_A = 0.35, w_E = 0.35, τ = 30 s, and links
when s > 0.5. **The arithmetic itself now lives in `scoring.py`** (v0.6.0): this module selects
candidates, applies the verdict, and keeps the window — it no longer inlines the formula. Every
link still carries its per-term contributions, so any grouping decision is auditable by
inspection. Situations are the connected components of the link graph; component bookkeeping
lives in the engine.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Container, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from netcorenoc.engine.correlate.learn import STORM_ALARMS, Learner
from netcorenoc.engine.correlate.scoring import (
    LINK_THRESHOLD,
    TAU_S,
    W_A,
    W_E,
    W_T,
    LinkFeatures,
    LinkScore,
    LinkScorer,
    SafeScorer,
    TermContribution,
)

# Re-exported from `scoring` so the coded defaults keep one home while every existing importer
# of `netcorenoc.correlate` (tests, tools) keeps working unchanged.
__all__ = [
    "LINK_THRESHOLD",
    "MAX_CANDIDATES",
    "MAX_LINKS_PER_ALARM",
    "MAX_WINDOW_ALARMS",
    "TAU_S",
    "WINDOW_S",
    "W_A",
    "W_E",
    "W_T",
    "CorrelationResult",
    "Correlator",
    "ScoredLink",
    "WindowAlarm",
    "select_candidates",
]

WINDOW_S = 120.0
MAX_CANDIDATES = 100  # bounded work per event, storms chain-link through recency
MAX_LINKS_PER_ALARM = 5  # strongest links kept; components need one, audits need few
MAX_WINDOW_ALARMS = 20_000  # absolute window cap; oldest-first eviction records a gap (§5.6)


class WindowEntry(Protocol):
    """What candidate selection needs to know about one windowed alarm: who it is, and when.

    Satisfied structurally by both :class:`WindowAlarm` (the engine) and
    :class:`netcorenoc.preview.PreviewAlarm` (the what-if), which is what lets one helper serve
    both without either importing the other's type.
    """

    @property
    def alarm_id(self) -> int: ...

    @property
    def ts(self) -> float: ...


def select_candidates[T: WindowEntry](
    window: Sequence[T],
    *,
    now: float,
    window_s: float = WINDOW_S,
    max_candidates: int = MAX_CANDIDATES,
    live: Container[int] | None = None,
) -> list[T]:
    """THE candidate-selection rule: the newest ``max_candidates`` live alarms within the window.

    v0.6.0 shipped this rule **twice** — here and, separately, inside ``preview.partition()`` —
    with its own copies of the window length and the cap. The two happened to agree, but nothing
    tied them together, so changing ``WINDOW_S`` alone would have made the what-if quietly lie
    about what the engine does. This is the one implementation both now call (DECISIONS #61).

    ``window`` is in chronological order, oldest first. Iteration runs **newest-first** and stops
    at the first entry outside the window — sound because the sequence is ordered, and it is what
    keeps the engine's per-event work bounded by ``max_candidates`` rather than by the window
    length. The result is reversed back to chronological order, which is the order the scorer and
    the union-find both expect.

    ``live`` is the one genuine difference between the two callers. The engine's deque carries
    **tombstones** — alarms cleared or re-activated, dropped from its ``index`` in O(1) but still
    physically present — so it passes that index as the liveness set. Preview replays an immutable
    snapshot in which every entry is live, so it passes ``None`` and every entry qualifies.

    Pure: it reads no clock (``now`` is supplied) and mutates nothing, so the same window and the
    same ``now`` always yield the same candidates.
    """
    recent: list[T] = []
    for entry in reversed(window):
        if now - entry.ts > window_s:
            break  # ordered oldest-first: everything earlier is outside the window too
        if live is not None and entry.alarm_id not in live:
            continue  # a tombstone: still in the deque, no longer a candidate
        recent.append(entry)
        if len(recent) >= max_candidates:
            break
    recent.reverse()
    return recent


@dataclass(frozen=True)
class WindowAlarm:
    """The facts scoring needs about one active alarm."""

    alarm_id: int
    class_id: int
    device_id: int
    ts: float
    entity_id: int = 0  # the alarmed entity (§5.5); defaults to device_id via __post_init__

    def __post_init__(self) -> None:
        # At level 0 the entity is 1:1 with the device, so an unset entity_id defaults to the
        # device_id — same-device alarms then share an entity (affinity 1.0), exactly v0.2.0.
        if self.entity_id == 0:
            object.__setattr__(self, "entity_id", self.device_id)


@dataclass(frozen=True)
class ScoredLink:
    """An accepted link, carrying the scorer's full explanation.

    ``result.terms`` is the typed source of the explanation (v0.6.0). ``term_t``/``term_a``/
    ``term_e`` remain as the *default* scorer's three named contributions — the projection the
    ``link`` table and the UI have always stored (DECISIONS #50) — looked up by name so a scorer
    that emits a different term set degrades to 0.0 rather than mis-labelling someone else's
    number.
    """

    other: WindowAlarm
    result: LinkScore

    @property
    def score(self) -> float:
        return self.result.score

    @property
    def terms(self) -> tuple[TermContribution, ...]:
        return self.result.terms

    @property
    def term_t(self) -> float:
        return contribution_of(self.result, "temporal")

    @property
    def term_a(self) -> float:
        return contribution_of(self.result, "class_affinity")

    @property
    def term_e(self) -> float:
        return contribution_of(self.result, "entity_affinity")


def contribution_of(result: LinkScore, name: str) -> float:
    """A named term's contribution, or 0.0 when this scorer does not emit that term."""
    for term in result.terms:
        if term.name == name:
            return term.contribution
    return 0.0


@dataclass(frozen=True)
class EvaluatedPair:
    """One candidate pair **and the verdict it received** — accepted or rejected.

    Deliberately a separate type from :class:`ScoredLink` even though the two carry the same two
    fields, because in this release conflating them is the cardinal error. `ScoredLink` means *"the
    scorer accepted this"*; `EvaluatedPair` means *"the scorer looked at this"*, and most of them
    were rejected or were dropped by ``MAX_LINKS_PER_ALARM``. A reader who sees `ScoredLink` in the
    capture path would reasonably assume every row is a link, which is exactly the misreading
    `docs/architecture/FEEDBACK-DATASET-0.8-DRAFT.md` §3.1a exists to prevent.

    ``result.linked`` is the champion's decision, captured as `dataset_pair.incumbent_linked`. It is
    **provenance, not a target** — see the invariant in that section, and DECISIONS #103.
    """

    other: WindowAlarm
    result: LinkScore


@dataclass(frozen=True)
class CorrelationResult:
    links: list[ScoredLink]
    considered: list[WindowAlarm]
    storm: bool
    # **v0.8.0 — the only change this release makes to `correlate.py`, and it is additive.**
    #
    # `process()` already calls `score_link` for every candidate and already discards the
    # `LinkScore` of every pair it does not keep: `considered` carries the `WindowAlarm` objects
    # (identity and time) but **not** their scores, so the arithmetic was computed and thrown away
    # on every call. Capturing the rejected pairs therefore needs `process()` to *return* what it
    # computed.
    #
    # The alternative — re-scoring afterwards — is worse on both counts: it doubles the arithmetic
    # on the ingest path, and it can produce **different numbers**, because `A` and `E` move between
    # the decision and the re-score. A dataset of features the scorer never saw is worse than no
    # dataset.
    #
    # Defaulted to an empty tuple-backed list so every existing constructor call and every test that
    # builds a `CorrelationResult` by hand keeps working unchanged. Bounded by `MAX_CANDIDATES`
    # (100), so the per-call memory cost is bounded by construction, like everything else on this
    # path.
    evaluated: list[EvaluatedPair] = field(default_factory=list)


@dataclass
class Correlator:
    """Sliding window with O(1) removal and bounded per-event work (§5.6).

    v0.2.0 was quadratic per event: ``remove`` linearly scanned the deque and the candidate
    selection copied the whole deque, so a 100 000-trap burst inside the window was ~10^10
    operations and the engine stalled. Here a parallel ``index`` gives O(1) removal
    (the deque entry becomes a tombstone, cleared on eviction), candidates are the last
    ``max_candidates`` *live* entries reached by iterating the deque tail, and an absolute
    ``max_window`` cap evicts oldest-first, counting each forced-out live alarm as a
    window-overflow drop. Behaviour is identical to v0.2.0 whenever the cap does not bite and
    there are no tombstones among the recent entries — the parity gate proves it.

    v0.6.0: the five scoring parameters live on ``scorer`` (a :class:`SafeScorer` wrapping the
    active :class:`LinkScorer`), not on this class — one home for the formula, and a swap is an
    assignment at the engine's documented reload point, never a mutation mid-batch.
    """

    window_s: float = WINDOW_S
    max_candidates: int = MAX_CANDIDATES
    max_window: int = MAX_WINDOW_ALARMS
    scorer: SafeScorer = field(default_factory=SafeScorer)
    window: deque[WindowAlarm] = field(default_factory=deque)
    index: dict[int, WindowAlarm] = field(default_factory=dict)
    _overflow_dropped: int = 0

    def set_scorer(self, scorer: LinkScorer) -> None:
        """Swap the active scorer (engine reload point only — never mid-batch)."""
        self.scorer = SafeScorer(scorer)

    def _evict(self, now: float) -> None:
        while self.window and now - self.window[0].ts > self.window_s:
            self.index.pop(self.window.popleft().alarm_id, None)
        # Absolute cap: force out the oldest, and count any live alarm we shed as a gap.
        while len(self.window) > self.max_window:
            oldest = self.window.popleft()
            if self.index.pop(oldest.alarm_id, None) is not None:
                self._overflow_dropped += 1

    def remove(self, alarm_id: int) -> None:
        """Drop a cleared or re-activated alarm from the window in O(1) (tombstone)."""
        self.index.pop(alarm_id, None)

    def take_overflow(self) -> int:
        """Return and reset the window-overflow drop count (read by the gap tracker)."""
        dropped, self._overflow_dropped = self._overflow_dropped, 0
        return dropped

    def _recent_live(self, now: float) -> list[WindowAlarm]:
        """The last ``max_candidates`` live alarms, chronological — v0.2.0 semantics without the
        full-deque copy. Delegates to :func:`select_candidates`, the one implementation of the
        rule, passing ``self.index`` as the liveness set so tombstones are skipped.

        ``_evict(now)`` has already dropped everything outside the window, so the helper's
        time predicate is a no-op here — which is precisely the equivalence that makes preview's
        selection and the engine's selection the same function rather than two that agree.
        """
        return select_candidates(
            self.window,
            now=now,
            window_s=self.window_s,
            max_candidates=self.max_candidates,
            live=self.index,
        )

    @staticmethod
    def features(new: WindowAlarm, old: WindowAlarm, learner: Learner) -> LinkFeatures:
        """Build the scorer's input once per candidate pair. Reserved slots stay ``None``."""
        return LinkFeatures(
            delta_t_s=abs(new.ts - old.ts),
            class_i=new.class_id,
            class_j=old.class_id,
            class_affinity=learner.class_affinity(new.class_id, old.class_id),
            ne_i=new.device_id,
            ne_j=old.device_id,
            entity_affinity=learner.entity_affinity(
                new.entity_id, new.device_id, old.entity_id, old.device_id
            ),
        )

    def score_link(self, new: WindowAlarm, old: WindowAlarm, learner: Learner) -> LinkScore:
        """The link verdict for one pair, with its per-term explanation."""
        return self.scorer.score(self.features(new, old, learner))

    def score(
        self, new: WindowAlarm, old: WindowAlarm, learner: Learner
    ) -> tuple[float, float, float, float]:
        """``(score, term_t, term_a, term_e)`` — the v0.2.0-shaped view of :meth:`score_link`."""
        result = self.score_link(new, old, learner)
        return (
            result.score,
            contribution_of(result, "temporal"),
            contribution_of(result, "class_affinity"),
            contribution_of(result, "entity_affinity"),
        )

    def process(self, new: WindowAlarm, learner: Learner) -> CorrelationResult:
        """Score a newly activated alarm against the window, then admit it."""
        self._evict(new.ts)
        self.remove(new.alarm_id)
        candidates = self._recent_live(new.ts)
        storm = len(self.index) >= STORM_ALARMS
        evaluated = []
        links = []
        for old in candidates:
            result = self.score_link(new, old, learner)
            evaluated.append(EvaluatedPair(old, result))  # v0.8.0: record, decide nothing
            if result.linked:
                links.append(ScoredLink(old, result))
        links = sorted(links, key=lambda link: link.score, reverse=True)[:MAX_LINKS_PER_ALARM]
        self.window.append(new)
        self.index[new.alarm_id] = new
        return CorrelationResult(
            links=links, considered=candidates, storm=storm, evaluated=evaluated
        )
