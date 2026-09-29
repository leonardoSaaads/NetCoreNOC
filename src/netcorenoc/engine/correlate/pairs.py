"""The values a correlation decision is made of: a windowed alarm, a scored pair, the result.

Split out of `correlate.py` in v0.26.0 at the 400-line module guard, on the seam between **data**
and **decision**: everything here is a frozen value or a pure function over values (candidate
selection, a link's stored explanation), and `correlate.py` keeps the stateful `Correlator` that
builds them. `correlate.py` re-exports every name by identity, so `from
netcorenoc.engine.correlate.correlate import WindowAlarm` still works for every existing importer.
"""

from __future__ import annotations

import json
from collections.abc import Container, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from netcorenoc.engine.correlate.grouping import Placement
from netcorenoc.engine.correlate.scoring import LinkScore, TermContribution

__all__ = [
    "MAX_CANDIDATES",
    "WINDOW_S",
    "CorrelationResult",
    "EvaluatedPair",
    "ScoredLink",
    "WindowAlarm",
    "WindowEntry",
    "contribution_of",
    "explained_terms",
    "select_candidates",
]

WINDOW_S = 120.0
MAX_CANDIDATES = 100  # bounded work per event, storms chain-link through recency


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
    reads the v0.5.0 three), and ``margin`` / ``accepted`` carry the decision whichever path
    made it.

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
