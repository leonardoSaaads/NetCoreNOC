"""Correlation: candidate generation, the pair decision, and the placement of a new alarm.

For each newly activated alarm the correlator builds candidates, scores every pair with the
active :class:`~netcorenoc.scoring.LinkScorer`, and asks :mod:`grouping` where the alarm belongs.
What it does depends on **which decider is active** (ADR #405):

* **a trained model** (the shipped `gam`, or a site-adapted one) — candidates are the correlation
  window **plus** the bounded recall of :mod:`retrieval` (same element, same OID parent, learned
  neighbours, back to the idle-close horizon); every pair gets the v2 feature vector of
  :mod:`features`; grouping is average-linkage correlation clustering over the model's log-odds.
* **the additive formula** (opt-in) — candidates are the correlation window only, the pair
  features are the v0.5.0 three, and a situation is a connected component of the accepted links:
  exactly what the formula's documentation says it does.

The arithmetic of each decider lives in its scorer; the choice of situation lives in
:mod:`grouping`; this module selects candidates, computes each pair's features once, and keeps the
window and the memories the features read. Every stored link carries its per-term explanation.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from netcorenoc.engine.correlate import features as pair_features
from netcorenoc.engine.correlate.episodes import CO_WINDOW_S, EpisodeMemory
from netcorenoc.engine.correlate.grouping import (
    MODE_CLUSTER,
    MODE_COMPONENTS,
    Grouper,
    GroupingParams,
    Scored,
)
from netcorenoc.engine.correlate.learn import STORM_ALARMS, Learner
from netcorenoc.engine.correlate.pairs import MAX_CANDIDATES as MAX_CANDIDATES
from netcorenoc.engine.correlate.pairs import WINDOW_S as WINDOW_S
from netcorenoc.engine.correlate.pairs import CorrelationResult as CorrelationResult
from netcorenoc.engine.correlate.pairs import EvaluatedPair as EvaluatedPair
from netcorenoc.engine.correlate.pairs import ScoredLink as ScoredLink
from netcorenoc.engine.correlate.pairs import WindowAlarm as WindowAlarm
from netcorenoc.engine.correlate.pairs import WindowEntry as WindowEntry
from netcorenoc.engine.correlate.pairs import contribution_of as contribution_of
from netcorenoc.engine.correlate.pairs import explained_terms as explained_terms
from netcorenoc.engine.correlate.pairs import select_candidates as select_candidates
from netcorenoc.engine.correlate.retrieval import CandidateIndex
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
    "explained_terms",
    "select_candidates",
]

MAX_LINKS_PER_ALARM = 5  # strongest links kept; components need one, audits need few
MAX_WINDOW_ALARMS = 20_000  # absolute window cap; oldest-first eviction records a gap (§5.6)


@dataclass
class Correlator:
    """The window, the memories, the scorer, and the placement decision (§5.6, ADR #405).

    O(1) removal and bounded per-event work, as since v0.3.0: a parallel ``index`` gives O(1)
    removal (the deque entry becomes a tombstone, cleared on eviction), window candidates are the
    last ``max_candidates`` *live* entries, and an absolute ``max_window`` cap evicts oldest-first,
    counting each forced-out live alarm as a window-overflow drop.

    v0.26.0 adds three bounded memories — ``recall`` (stage-one candidates), ``episodes``
    (separate-occasion co-failure counts) and ``grouper`` (cross-situation evidence) — and which of
    them is consulted is decided by the active scorer, never by a flag: a trained model gets the
    two-stage recall and correlation clustering, the additive formula gets the window and
    connected components.
    """

    window_s: float = WINDOW_S
    max_candidates: int = MAX_CANDIDATES
    max_window: int = MAX_WINDOW_ALARMS
    scorer: SafeScorer = field(default_factory=SafeScorer)
    window: deque[WindowAlarm] = field(default_factory=deque)
    index: dict[int, WindowAlarm] = field(default_factory=dict)
    recall: CandidateIndex = field(default_factory=CandidateIndex)
    episodes: EpisodeMemory = field(default_factory=EpisodeMemory)
    grouper: Grouper = field(default_factory=lambda: Grouper(MODE_COMPONENTS))
    #: Every activation that has not cleared and that a ring or the window can still propose.
    #: Wider than ``index``, which forgets an alarm after 120 s while recall may propose it for an
    #: hour; bounded by :meth:`prune`.
    live: set[int] = field(default_factory=set)
    _overflow_dropped: int = 0

    def set_scorer(self, scorer: LinkScorer) -> None:
        """Swap the active scorer (engine reload point only — never mid-batch).

        The grouping rule travels with it: a trained model carries its own grouping parameters in
        its document, and the additive formula means connected components. The cross-situation
        evidence already accumulated is kept either way — it is evidence about situations, not
        about the scorer that produced it.
        """
        self.scorer = SafeScorer(scorer)
        grouping = getattr(getattr(scorer, "model", None), "grouping", None)
        if isinstance(grouping, dict):
            self.grouper.mode = MODE_CLUSTER
            self.grouper.params = GroupingParams(
                join_bias=float(grouping["join_bias"]),
                merge_bias=float(grouping["merge_bias"]),
                merge_min_pairs=int(grouping["merge_min_pairs"]),
            )
        else:
            self.grouper.mode = MODE_COMPONENTS

    @property
    def two_stage(self) -> bool:
        """Whether the active decider reads the v2 features (and therefore gets stage-one recall).
        A degraded scorer runs the built-in formula, and so gets the formula's candidates."""
        return self.scorer.fast_path

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
        self.live.discard(alarm_id)

    def prune(self, now: float) -> None:
        """Bound the memories (maintenance sweep, never per trap).

        ``live`` holds every activation that has not cleared, and an alarm that never clears would
        otherwise stay in it forever; only ids a ring or the window can still propose are kept.
        """
        self.recall.prune(now)
        self.episodes.prune(now)
        reachable = set(self.index)
        for table in (self.recall.by_ne, self.recall.by_parent):
            for ring in table.values():
                reachable.update(a.alarm_id for a in ring)
        self.live.intersection_update(reachable)

    def take_overflow(self) -> int:
        """Return and reset the window-overflow drop count (read by the gap tracker)."""
        dropped, self._overflow_dropped = self._overflow_dropped, 0
        return dropped

    def _recent_live(self, now: float) -> list[WindowAlarm]:
        """The last ``max_candidates`` live alarms, chronological — v0.2.0 semantics without the
        full-deque copy. Delegates to :func:`select_candidates`, the one implementation of the
        rule, passing ``self.index`` as the liveness set so tombstones are skipped."""
        return select_candidates(
            self.window,
            now=now,
            window_s=self.window_s,
            max_candidates=self.max_candidates,
            live=self.index,
        )

    @staticmethod
    def features(
        new: WindowAlarm,
        old: WindowAlarm,
        learner: Learner,
        vector: tuple[float, ...] | None = None,
    ) -> LinkFeatures:
        """Build the scorer's input once per candidate pair.

        `same_oid_root` is a string comparison of two values already in hand, `None` whenever
        either root is unknown (F135). ``vector`` is the v2 feature vector when the active decider
        reads it; the additive formula reads the first seven fields and ignores it.
        """
        both_known = bool(new.oid_root) and bool(old.oid_root)
        if vector is not None:
            class_affinity, entity_affinity = vector[4], vector[5]
        else:
            class_affinity = learner.class_affinity(new.class_id, old.class_id)
            entity_affinity = learner.entity_affinity(
                new.entity_id, new.device_id, old.entity_id, old.device_id
            )
        return LinkFeatures(
            delta_t_s=abs(new.ts - old.ts),
            class_i=new.class_id,
            class_j=old.class_id,
            class_affinity=class_affinity,
            ne_i=new.device_id,
            ne_j=old.device_id,
            entity_affinity=entity_affinity,
            same_oid_root=(new.oid_root == old.oid_root) if both_known else None,
            vector=vector,
        )

    def score_link(self, new: WindowAlarm, old: WindowAlarm, learner: Learner) -> LinkScore:
        """The link verdict for one pair, with its per-term explanation."""
        vector = None
        if self.two_stage:
            vector = pair_features.vector(new, old, learner, self.episodes, len(self.index))
        return self.scorer.score(self.features(new, old, learner, vector))

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

    def process(
        self,
        new: WindowAlarm,
        learner: Learner,
        *,
        sit_of: dict[int, int] | None = None,
        own: int | None = None,
        teaches: bool = True,
    ) -> CorrelationResult:
        """Score a newly activated alarm against its candidates, place it, then admit it.

        ``sit_of`` (alarm -> open situation) is what placement reads; ``own`` is the situation the
        alarm is already in when it re-activates inside an open one. ``teaches`` is ADR #372: an
        alarm a maintenance window let through is correlated but moves no learned memory.
        """
        self._evict(new.ts)
        self.remove(new.alarm_id)
        window = self._recent_live(new.ts)
        burst = len(self.index)
        storm = burst >= STORM_ALARMS
        two_stage = self.two_stage
        candidates = window
        recalled = 0
        if two_stage:
            extra = self.recall.recall(
                new,
                self.live,
                self.episodes.neighbours(new.device_id),
                {c.alarm_id for c in window},
            )
            recalled = len(extra)
            candidates = window + extra
        evaluated: list[EvaluatedPair] = []
        for old in candidates:
            if two_stage:
                vec = pair_features.vector(new, old, learner, self.episodes, burst)
                fast = self.scorer.fast(vec)
                if fast is not None:
                    margin, accepted = fast
                    evaluated.append(EvaluatedPair(old, None, vec, margin, accepted))
                    continue
                result = self.scorer.score(self.features(new, old, learner, vec))
                evaluated.append(EvaluatedPair(old, result, vec))
            else:
                result = self.scorer.score(self.features(new, old, learner))
                evaluated.append(EvaluatedPair(old, result))  # v0.8.0: record, decide nothing
        kept = sorted((p for p in evaluated if p.linked), key=lambda p: p.evidence, reverse=True)[
            :MAX_LINKS_PER_ALARM
        ]
        links = [ScoredLink(p.other, p.result or self._explain(new, p, learner)) for p in kept]
        placement = self.grouper.place(
            [Scored(p.other.alarm_id, p.evidence, p.linked) for p in evaluated],
            sit_of if sit_of is not None else {},
            own,
        )
        if teaches:
            key = (new.device_id, new.class_id)
            for old in candidates:
                if new.ts - old.ts <= CO_WINDOW_S:
                    self.episodes.observe(key, (old.device_id, old.class_id), new.ts)
        self.window.append(new)
        self.index[new.alarm_id] = new
        self.live.add(new.alarm_id)
        self.recall.add(new)
        return CorrelationResult(
            links=links,
            considered=window,
            storm=storm,
            evaluated=evaluated,
            placement=placement,
            recalled=recalled,
        )

    def commit(self, sid: int, outcome: CorrelationResult, sit_of: dict[int, int]) -> None:
        """The engine executed ``outcome.placement`` and the alarm is now in ``sid``: let
        grouping fold the merged situations and accumulate this activation's cross evidence."""
        self.grouper.commit(
            sid,
            outcome.placement,
            [Scored(p.other.alarm_id, p.evidence, p.linked) for p in outcome.evaluated],
            sit_of,
        )

    def _explain(self, new: WindowAlarm, pair: EvaluatedPair, learner: Learner) -> LinkScore:
        """The full verdict for a pair the fast path decided — built only for kept links."""
        return self.scorer.score(self.features(new, pair.other, learner, pair.vector))
