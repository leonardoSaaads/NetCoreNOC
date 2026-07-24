"""Sliding-window correlation: the three-term link score and its bookkeeping.

For each newly activated alarm the correlator scores it against the alarms currently in
the 120 s window:

    s = w_t · e^(-Δt/τ) + w_A · A[c_i, c_j] + w_E · E[v_i, v_j]

with same device ⇒ E = 1 and defaults w_t = 0.3, w_A = 0.35, w_E = 0.35, τ = 30 s.
A link is accepted when s > 0.5, and the three terms are kept on every link so any
grouping decision is auditable by inspection. Situations are the connected components
of the link graph; component bookkeeping lives in the engine.
"""

from __future__ import annotations

import itertools
import math
from collections import deque
from dataclasses import dataclass, field

from netcorenoc.learn import STORM_ALARMS, Learner

WINDOW_S = 120.0
TAU_S = 30.0
W_T = 0.3
W_A = 0.35
W_E = 0.35
LINK_THRESHOLD = 0.5
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
    """An accepted link with its three explainable terms."""

    other: WindowAlarm
    score: float
    term_t: float
    term_a: float
    term_e: float


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
    """

    window_s: float = WINDOW_S
    tau: float = TAU_S
    threshold: float = LINK_THRESHOLD
    max_candidates: int = MAX_CANDIDATES
    max_window: int = MAX_WINDOW_ALARMS
    window: deque[WindowAlarm] = field(default_factory=deque)
    index: dict[int, WindowAlarm] = field(default_factory=dict)
    _overflow_dropped: int = 0

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

    def score(
        self, new: WindowAlarm, old: WindowAlarm, learner: Learner
    ) -> tuple[float, float, float, float]:
        term_t = W_T * math.exp(-abs(new.ts - old.ts) / self.tau)
        term_a = W_A * learner.class_affinity(new.class_id, old.class_id)
        term_e = W_E * learner.entity_affinity(
            new.entity_id, new.device_id, old.entity_id, old.device_id
        )
        return term_t + term_a + term_e, term_t, term_a, term_e

    def process(self, new: WindowAlarm, learner: Learner) -> CorrelationResult:
        """Score a newly activated alarm against the window, then admit it."""
        self._evict(new.ts)
        self.remove(new.alarm_id)
        candidates = self._recent_live()
        storm = len(self.index) >= STORM_ALARMS
        links = []
        for old in candidates:
            score, term_t, term_a, term_e = self.score(new, old, learner)
            if score > self.threshold:
                links.append(ScoredLink(old, score, term_t, term_a, term_e))
        links = sorted(links, key=lambda link: link.score, reverse=True)[:MAX_LINKS_PER_ALARM]
        self.window.append(new)
        self.index[new.alarm_id] = new
        return CorrelationResult(links=links, considered=candidates, storm=storm)
