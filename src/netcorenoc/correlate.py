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

import itertools
from collections import deque
from dataclasses import dataclass, field

from netcorenoc.learn import STORM_ALARMS, Learner
from netcorenoc.scoring import (
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
]

WINDOW_S = 120.0
MAX_CANDIDATES = 100  # bounded work per event, storms chain-link through recency
MAX_LINKS_PER_ALARM = 5  # strongest links kept; components need one, audits need few
MAX_WINDOW_ALARMS = 20_000  # absolute window cap; oldest-first eviction records a gap (§5.6)


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
class CorrelationResult:
    links: list[ScoredLink]
    considered: list[WindowAlarm]
    storm: bool


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

    def _recent_live(self) -> list[WindowAlarm]:
        """The last ``max_candidates`` live alarms, chronological — v0.2.0 semantics without
        the full-deque copy: iterate the tail newest-first, skip tombstones, stop at the cap."""
        recent: list[WindowAlarm] = []
        for wa in itertools.islice(reversed(self.window), None):
            if wa.alarm_id in self.index:
                recent.append(wa)
                if len(recent) >= self.max_candidates:
                    break
        recent.reverse()
        return recent

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
        candidates = self._recent_live()
        storm = len(self.index) >= STORM_ALARMS
        links = []
        for old in candidates:
            result = self.score_link(new, old, learner)
            if result.linked:
                links.append(ScoredLink(old, result))
        links = sorted(links, key=lambda link: link.score, reverse=True)[:MAX_LINKS_PER_ALARM]
        self.window.append(new)
        self.index[new.alarm_id] = new
        return CorrelationResult(links=links, considered=candidates, storm=storm)
