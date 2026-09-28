"""Which situation an arriving alarm belongs to — the question the project never modelled.

Until v0.26.0 a situation was a **connected component** of the link graph: any single accepted link
from the newcomer to a member of another situation merged the two, permanently. That is
single-linkage clustering, and its textbook failure is *chaining* — one weak bridge joins two
clusters that every other pair says are separate (Jain, Murty & Flynn, *Data Clustering: A Review*,
ACM Computing Surveys 31(3), 1999, §5.2). No pairwise model of any family fixes it, because it is a
property of the grouping step, not of the score.

## The rule this module implements

Every scored pair contributes **evidence in log-odds**: positive when the model thinks the two
alarms share an incident, negative when it thinks they do not, and the magnitude is the model's
confidence. That is the weight of the correlation-clustering objective (Bansal, Blum & Chawla,
*Correlation Clustering*, Machine Learning 56, 2004), whose optimum partition maximises the
agreement between the grouping and every pairwise opinion — including the negative ones that
connected components throw away.

It is solved greedily and online, the way greedy additive edge contraction solves it offline
(Keuper et al., ICCV 2015), in two moves:

1. **Join.** For each open situation the newcomer was scored against, average its pair evidence.
   Join the best if that average clears ``join_bias``; otherwise open a new situation. An
   average, not a sum: in a storm a situation has a hundred members in range, and a hundred
   faint "maybe"s must not outvote one "no".
2. **Merge.** Evidence between two situations **accumulates** across every activation that scores
   members of both. Two situations merge only when the *mean* of all cross evidence clears
   ``merge_bias`` over at least ``merge_min_pairs`` pairs. A single bridge is one pair; the dozens
   of pairs between two concurrent incidents that say "no" are counted too.

``merge_bias`` defaults above ``join_bias``: joining a fresh alarm to the wrong situation costs one
misplaced alarm, merging two situations costs every member of both.

## The legacy rule, kept

:data:`MODE_COMPONENTS` reproduces connected components exactly, and it is what the opt-in
additive formula runs under — the formula is documented as *"a situation is a connected component
of the resulting link graph"*, and an operator who opts back into it gets what the documentation
says.

## What this module is not

It reads no store and no clock and does no I/O. :meth:`Grouper.place` is a pure decision; the
engine executes it (creating and merging situations in the database) and then calls
:meth:`Grouper.commit` with the id it got. State is bounded by the number of *open* situations.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

__all__ = [
    "MODE_CLUSTER",
    "MODE_COMPONENTS",
    "Grouper",
    "GroupingParams",
    "Placement",
    "Scored",
]

MODE_COMPONENTS = "components"
MODE_CLUSTER = "cluster"


@dataclass(frozen=True)
class Scored:
    """One evaluated pair as grouping sees it: the other alarm, the evidence, the link verdict."""

    alarm_id: int
    evidence: float  # log-odds that the two share an incident
    linked: bool


@dataclass(frozen=True)
class GroupingParams:
    join_bias: float = 0.0
    merge_bias: float = 1.0
    merge_min_pairs: int = 3

    def as_dict(self) -> dict[str, float]:
        return {
            "join_bias": self.join_bias,
            "merge_bias": self.merge_bias,
            "merge_min_pairs": float(self.merge_min_pairs),
        }


@dataclass(frozen=True)
class Placement:
    """The decision: join ``join`` (``None`` opens a new situation), then fold ``merge`` into it.

    ``support`` is the evidence per situation the newcomer was scored against — ``(situation,
    mean evidence, pairs)`` — kept so the decision can be explained after the fact.
    """

    join: int | None
    merge: tuple[int, ...]
    support: tuple[tuple[int, float, int], ...] = ()


@dataclass
class Grouper:
    mode: str = MODE_CLUSTER
    params: GroupingParams = field(default_factory=GroupingParams)
    #: situation -> other situation -> [sum of evidence, pairs]. Symmetric.
    cross: dict[int, dict[int, list[float]]] = field(default_factory=dict)

    # -- the decision -----------------------------------------------------------------------

    def place(self, scored: Sequence[Scored], sit_of: Mapping[int, int], own: int | None) -> Placement:
        if self.mode == MODE_COMPONENTS:
            return self._components(scored, sit_of, own)
        support: dict[int, list[float]] = {}
        for pair in scored:
            sid = sit_of.get(pair.alarm_id)
            if sid is None:
                continue
            acc = support.setdefault(sid, [0.0, 0.0])
            acc[0] += pair.evidence
            acc[1] += 1.0
        ranked = sorted(
            ((sid, acc[0] / acc[1], int(acc[1])) for sid, acc in support.items()),
            key=lambda row: (-row[1], -row[2], row[0]),
        )
        join: int | None
        if own is not None:
            join = own
        elif ranked and ranked[0][1] > self.params.join_bias:
            join = ranked[0][0]
        else:
            join = None
        merges: list[int] = []
        if join is not None:
            prior = self.cross.get(join, {})
            for sid, _mean, _n in ranked:
                if sid == join:
                    continue
                held = prior.get(sid, [0.0, 0.0])
                total = held[0] + support[sid][0]
                pairs = held[1] + support[sid][1]
                if pairs >= self.params.merge_min_pairs and total / pairs > self.params.merge_bias:
                    merges.append(sid)
        return Placement(join, tuple(sorted(merges)), tuple(ranked))

    def _components(
        self, scored: Sequence[Scored], sit_of: Mapping[int, int], own: int | None
    ) -> Placement:
        """Connected components, exactly as v0.25.0's `_assign_situation` computed them."""
        sids = {sit_of[p.alarm_id] for p in scored if p.linked and p.alarm_id in sit_of}
        if own is not None:
            sids.add(own)
        if not sids:
            return Placement(None, ())
        join = min(sids)
        return Placement(join, tuple(sorted(sids - {join})))

    # -- the bookkeeping, after the engine has executed the decision --------------------------

    def commit(self, sid: int, placement: Placement, scored: Sequence[Scored], sit_of: Mapping[int, int]) -> None:
        """Fold the merged situations into ``sid`` and add this activation's cross evidence.

        ``sit_of`` is read **after** the engine applied the placement, so every member of a merged
        situation already maps to ``sid`` and contributes nothing across.
        """
        if self.mode == MODE_COMPONENTS:
            return
        for gone in placement.merge:
            self._fold(sid, gone)
        mine = self.cross.setdefault(sid, {})
        for pair in scored:
            other = sit_of.get(pair.alarm_id)
            if other is None or other == sid:
                continue
            acc = mine.setdefault(other, [0.0, 0.0])
            acc[0] += pair.evidence
            acc[1] += 1.0
            self.cross.setdefault(other, {})[sid] = acc  # the same list: symmetric by identity

    def _fold(self, dst: int, src: int) -> None:
        moved = self.cross.pop(src, {})
        mine = self.cross.setdefault(dst, {})
        for other, acc in moved.items():
            back = self.cross.get(other)
            if back is not None:
                back.pop(src, None)
            if other == dst:
                continue
            held = mine.get(other)
            if held is None:
                mine[other] = acc
                self.cross.setdefault(other, {})[dst] = acc
            else:
                held[0] += acc[0]
                held[1] += acc[1]
        mine.pop(src, None)

    def merged(self, dst: int, src: int) -> None:
        """An operator merged ``src`` into ``dst``: the evidence follows the members."""
        self._fold(dst, src)

    def forget(self, sid: int) -> None:
        """A situation closed or was restructured by hand: its evidence no longer applies."""
        for other in self.cross.pop(sid, {}):
            back = self.cross.get(other)
            if back is not None:
                back.pop(sid, None)

    def prune(self, live: set[int]) -> None:
        """Drop evidence about situations that are no longer open."""
        for sid in [s for s in self.cross if s not in live]:
            self.forget(sid)
