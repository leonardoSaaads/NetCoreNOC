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

import json
from collections import deque
from collections.abc import Container, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from netcorenoc.engine.correlate import features as pair_features
from netcorenoc.engine.correlate.episodes import CO_WINDOW_S, EpisodeMemory
from netcorenoc.engine.correlate.grouping import (
    MODE_CLUSTER,
    MODE_COMPONENTS,
    Grouper,
    GroupingParams,
    Placement,
    Scored,
)
from netcorenoc.engine.correlate.learn import STORM_ALARMS, Learner
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
    "explained_terms",
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
    # **v0.18.0 (F135): the trap OID's enterprise root**, so `same_oid_root` can be *served*.
    #
    # `PREREGISTRATION-0.9.0.md` §2.3 registered `same_oid_root` as the fourth feature in v0.9.0
    # and it was never implemented, for one reason recorded in `model/challenger.py`: this class
    # carried no OID, and adding one meant editing `correlate.py`, whose bytes were pinned. The
    # v0.18.0 brief withdrew that pin, so a feature that was computable offline and not online —
    # the definition of guaranteed training/serving skew — can finally be computed in both.
    #
    # The **root**, not the OID: `class_id` already identifies the class, and storing the OID
    # here would put a second identifier on the hot path for no gain. The root is a bounded
    # string computed once per activation, never per candidate pair.
    #
    # Defaulted to "" so every existing constructor — the engine's, preview's, and every test's —
    # keeps working, and an empty root simply means "not known", which `features` reports as
    # `same_oid_root=None` rather than as a false.
    oid_root: str = ""
    # **v0.26.0: what the v2 feature vector needs**, all computed once per activation.
    #
    # ``arcs`` is the trap OID split on its dots, so the shared-subtree feature is an arc-by-arc
    # comparison (Appendix B: a string prefix is not a subtree). ``severity_rank`` is the X.733
    # rank the engine placed (0 critical .. 4), -1 when nothing placed it. ``chatter`` is how many
    # times this fingerprint activated in the past hour, read from the flap detector's history.
    arcs: tuple[str, ...] = ()
    severity_rank: int = -1
    chatter: int = 0
    # The management address the trap came from, and the addresses its varbinds **name** (a BGP
    # peer, an OSPF neighbour, a far end). Two alarms where one names the other's source are
    # topology the trap carried — the only topology a zero-configuration appliance has.
    source: str = ""
    refs: frozenset[str] = frozenset()

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


def explained_terms(result: LinkScore) -> str | None:
    """A stored link's full explanation, for a scorer whose terms are not the formula's three.

    The `link` table has carried `term_t`, `term_a`, `term_e` since v0.2.0, and for the additive
    formula those three columns *are* the explanation. A trained model has one term per feature,
    so its explanation is written whole — canonical JSON of ``[name, value, contribution]`` plus the
    base value — and the three columns hold what they can (DECISIONS #50). ``None`` for the formula,
    whose explanation the columns already carry.
    """
    if result.basis == "weighted-sum":
        return None
    return json.dumps(
        {
            "basis": result.basis,
            "base": round(result.base_value, 9),
            "threshold": result.threshold,
            "terms": [[t.name, round(t.value, 9), round(t.contribution, 9)] for t in result.terms],
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def contribution_of(result: LinkScore, name: str) -> float:
    """A named term's contribution, or 0.0 when this scorer does not emit that term."""
    for term in result.terms:
        if term.name == name:
            return term.contribution
    return 0.0


@dataclass(frozen=True)
class EvaluatedPair:
    """One candidate pair **and the verdict it received** — accepted or rejected.

    Deliberately a separate type from :class:`ScoredLink` even though the two share fields: a
    `ScoredLink` means *"the scorer accepted this and it was kept"*; an `EvaluatedPair` means *"the
    scorer looked at this"*, and most of them were rejected. `FEEDBACK-DATASET-0.8-DRAFT.md` §3.1a
    exists to prevent reading one as the other.

    ``result`` is the full verdict with its explanation. For a trained model it is ``None`` on
    pairs that were not kept as links: the model's logit was computed on the fast path and building
    fourteen terms for a pair nobody will ever display is exactly the per-pair work the ingest path
    must not do. ``vector`` is the v2 feature vector (``None`` for the additive formula, which
    reads the v0.5.0 three), and ``margin`` / ``accepted`` carry the decision whichever path made it.

    The decision is the champion's opinion and is recorded as `dataset_pair.incumbent_linked`:
    **provenance, never a feature and never a target** (Part VI.4, DECISIONS #103).
    """

    other: WindowAlarm
    result: LinkScore | None
    vector: tuple[float, ...] | None = None
    margin: float = 0.0
    accepted: bool = False

    @property
    def evidence(self) -> float:
        """How far above the link threshold this pair scored — log-odds for a trained model."""
        if self.result is not None:
            return self.result.score - self.result.threshold
        return self.margin

    @property
    def linked(self) -> bool:
        return self.result.linked if self.result is not None else self.accepted


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
    # **v0.26.0.** Where grouping placed the alarm, and how many candidates stage-one recall added
    # beyond the window. Defaulted so a hand-built result (tests, preview) needs neither.
    placement: Placement = field(default_factory=lambda: Placement(None, ()))
    recalled: int = 0


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
        kept = sorted(
            (p for p in evaluated if p.linked), key=lambda p: p.evidence, reverse=True
        )[:MAX_LINKS_PER_ALARM]
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
