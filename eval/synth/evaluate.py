"""Re-running the grouping step over a recorded stream, under any decider, and scoring the result.

A recorded :class:`~synth.record.StreamLog` holds every activation's candidates and their feature
vectors — both functions of the stream alone. What depends on the decider is only the **evidence**
each pair gets and the **placement** that evidence produces. This module recomputes exactly those,
with the product's own `grouping.Grouper` and the engine's own closing rules (every member cleared,
or idle for `IDLE_CLOSE_S`), so evaluating a model, a hyperparameter trial or the additive formula
costs one pass over the log rather than one pass through the appliance.

Two deciders, the two the appliance can run:

* :class:`ModelDecider` — a `gam` scorer over **every** candidate (window plus recall), grouped by
  correlation clustering with the model document's own grouping parameters;
* :class:`FormulaDecider` — the additive formula over the **window** candidates only, grouped as
  connected components: what the opt-in formula does in the product.

The situation-level quantities (IV.2 of the brief):

* ``pairwise_f1`` and ``ari`` against the ground-truth grouping — the standard clustering measures;
* ``over_merge_rate`` — predicted situations that span two or more true incidents;
* ``under_merge_rate`` — true incidents split across two or more predicted situations;
* ``split_bag_intact_rate`` — **generated analogue**: of the incident pairs the generator placed
  deliberately close together (the `dual_incident` shape at many overlaps), the fraction whose
  alarms ended up sharing a situation. An operator would split such a bag; this is how often the
  decider leaves it intact;
* ``asserted_negative_respected_rate`` — **generated analogue**: of the candidate pairs that truly
  belong to different incidents (every negative the decider actually compared), the fraction kept
  in different situations;
* ``repair_gestures`` — the operator's work to repair the partition, per incident: one merge for
  every extra piece an incident was split into, one move for every foreign incident in a situation.
  The single number the grouping biases are chosen on (ADR #409), because it prices a split and a
  contamination the way an operator pays for them.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from netcorenoc.engine.correlate.grouping import (
    MODE_CLUSTER,
    MODE_COMPONENTS,
    Grouper,
    GroupingParams,
    Scored,
)
from netcorenoc.engine.correlate.scoring import AdditiveScorer, LinkFeatures
from netcorenoc.engine.model.gam import GamScorer
from netcorenoc.engine.operate.engine import IDLE_CLOSE_S

#: The engine's hold before an all-cleared situation resolves (ADR #410). Read from the engine when
#: it defines one, so the replay and the appliance cannot disagree about it.
try:
    from netcorenoc.engine.operate.engine import CLEAR_HOLD_S
except ImportError:  # pragma: no cover - the engine before ADR #410
    CLEAR_HOLD_S = 0.0

import metrics
from synth.record import Activation, StreamLog

__all__ = [
    "FormulaDecider",
    "ModelDecider",
    "Outcome",
    "group",
    "situation_metrics",
]

#: How often idle situations are swept, in stream seconds — the engine's maintenance cadence
#: resolution is 5 s; a minute moves an idle close by at most a minute on an hour's horizon.
IDLE_SWEEP_S = 60.0


class Decider(Protocol):
    mode: str
    params: GroupingParams

    def evidence(self, act: Activation) -> list[Scored]: ...


@dataclass
class ModelDecider:
    scorer: GamScorer
    mode: str = MODE_CLUSTER
    params: GroupingParams = field(default_factory=GroupingParams)

    @classmethod
    def of(cls, scorer: GamScorer, params: GroupingParams | None = None) -> ModelDecider:
        g = scorer.model.grouping
        return cls(
            scorer,
            MODE_CLUSTER,
            params or GroupingParams(g["join_bias"], g["merge_bias"], int(g["merge_min_pairs"])),
        )

    def evidence(self, act: Activation) -> list[Scored]:
        thr = self.scorer.threshold
        out = []
        for other, _in_window, vec in act.candidates:
            logit = self.scorer.logit(vec)
            out.append(Scored(other, logit - thr, logit > thr))
        return out


@dataclass
class FormulaDecider:
    scorer: AdditiveScorer = field(default_factory=AdditiveScorer)
    mode: str = MODE_COMPONENTS
    params: GroupingParams = field(default_factory=GroupingParams)

    def evidence(self, act: Activation) -> list[Scored]:
        out = []
        for other, in_window, v in act.candidates:
            if not in_window:
                continue
            same_ne = v[1] >= 0.5
            features = LinkFeatures(
                delta_t_s=v[0],
                class_i=0,
                class_j=0,
                class_affinity=v[4],
                ne_i=0,
                ne_j=0 if same_ne else 1,
                entity_affinity=v[5],
                same_oid_root=v[3] >= 7,
            )
            result = self.scorer.score(features)
            out.append(Scored(other, result.score - result.threshold, result.linked))
        return out


@dataclass
class Outcome:
    """One stream's grouping: per activation, its truth and its final predicted situation."""

    name: str
    truth: list[str]
    family: list[str]
    pred: list[int]
    ts: list[float]
    #: (activation index a, activation index b, same truth) for every compared pair
    compared: list[tuple[int, int, bool]]
    concurrent: dict[str, tuple[str, str]]
    #: activations that joined an existing situation (a correlation decision was made)
    joins: int = 0
    #: per activation: an unrecognised clear (see `record.Activation.clear`)
    clear: list[bool] = field(default_factory=list)


def group(log: StreamLog, decider: Decider, clear_hold_s: float | None = None) -> Outcome:
    """Replay placement over ``log`` under ``decider``, with the engine's closing rules.

    ``clear_hold_s`` is how long a situation whose every member has cleared stays open for a
    member to re-raise into (the engine's `CLEAR_HOLD_S`; ``None`` reads the engine's value)."""
    hold = CLEAR_HOLD_S if clear_hold_s is None else clear_hold_s
    cleared_since: dict[int, float] = {}
    grouper = Grouper(decider.mode, decider.params)
    sit_of: dict[int, int] = {}
    members: dict[int, set[int]] = {}
    touched: dict[int, float] = {}
    active: dict[int, bool] = {}
    parent: dict[int, int] = {}
    latest: dict[int, int] = {}  # alarm id -> index of its latest activation
    act_sid: list[int] = []
    out = Outcome(log.name, [], [], [], [], [], dict(log.concurrent))
    next_sid = 1
    next_sweep = float("-inf")

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def close(sid: int) -> None:
        for m in members.pop(sid, set()):
            sit_of.pop(m, None)
        touched.pop(sid, None)
        cleared_since.pop(sid, None)
        grouper.forget(sid)

    for kind, payload in log.ops:
        if kind == "c":
            aid, at = payload
            active[aid] = False
            sid = sit_of.get(aid)
            if sid is not None and not any(active.get(m, False) for m in members.get(sid, ())):
                if hold <= 0:
                    close(sid)
                else:
                    cleared_since[sid] = at
            continue
        act: Activation = payload
        for sid in [s for s, t in cleared_since.items() if act.ts - t >= hold]:
            close(sid)
        if act.ts >= next_sweep:
            # The engine's idle sweep resolves only a situation with no member still active
            # (v0.16.2, #274): a burning situation is never idle, however long nobody touched it.
            for sid in [
                s
                for s, t in touched.items()
                if act.ts - t > IDLE_CLOSE_S and not any(active.get(m, False) for m in members[s])
            ]:
                close(sid)
            next_sweep = act.ts + IDLE_SWEEP_S
        active[act.alarm_id] = True
        scored = decider.evidence(act)
        placement = grouper.place(scored, sit_of, sit_of.get(act.alarm_id))
        sid = placement.join if placement.join in members else None
        if sid is None:
            sid = next_sid
            next_sid += 1
            members[sid] = set()
            parent[sid] = sid
        elif sit_of.get(act.alarm_id) != sid:
            out.joins += 1
        for other in placement.merge:
            if other == sid or other not in members:
                continue
            parent[find(other)] = find(sid)
            for m in members.pop(other):
                sit_of[m] = sid
                members[sid].add(m)
            touched.pop(other, None)
        sit_of[act.alarm_id] = sid
        members[sid].add(act.alarm_id)
        touched[sid] = act.ts
        cleared_since.pop(sid, None)
        grouper.commit(sid, placement, scored, sit_of)
        index = len(act_sid)
        act_sid.append(sid)
        for other, _w, _v in act.candidates:
            j = latest.get(other)
            if j is not None:
                out.compared.append((index, j, out.truth[j] == act.incident))
        latest[act.alarm_id] = index
        out.truth.append(act.incident)
        out.family.append(act.family)
        out.ts.append(act.ts)
        out.clear.append(act.clear)
    out.pred = [find(s) for s in act_sid]
    return out


def situation_metrics(outcomes: Iterable[Outcome], family: str | None = None) -> dict[str, float]:
    """The situation-level quantities, pooled over streams (labels are stream-qualified).

    With ``family``, the partition is restricted to activations of that family, and the two
    rates are read from the incident's side: an incident of the family whose situations contain
    any other incident's alarm counts as over-merged, however the other side is labelled.
    """
    pred: list[tuple[str, int]] = []
    truth: list[tuple[str, str]] = []
    compared = respected = 0
    concurrent_pairs = concurrent_merged = 0
    incident_sits: dict[tuple[str, str], set[int]] = defaultdict(set)
    sit_incidents: dict[tuple[str, int], set[str]] = defaultdict(set)
    n = 0
    clears = 0
    for o in outcomes:
        skip = o.clear or [False] * len(o.truth)
        for i, (t, f, p) in enumerate(zip(o.truth, o.family, o.pred, strict=True)):
            if skip[i]:
                clears += family is None or f == family
                continue  # an unrecognised clear: reported apart, never scored as a grouping
            sit_incidents[(o.name, p)].add(t)
            if family is not None and f != family:
                continue
            n += 1
            pred.append((o.name, p))
            truth.append((o.name, t))
            incident_sits[(o.name, t)].add(p)
        for a, b, same in o.compared:
            if skip[a] or skip[b]:
                continue
            if family is not None and o.family[a] != family and o.family[b] != family:
                continue
            if not same:
                compared += 1
                respected += o.pred[a] != o.pred[b]
        by_incident: dict[str, set[int]] = defaultdict(set)
        for t, p in zip(o.truth, o.pred, strict=True):
            by_incident[t].add(p)
        for key, (anchor, _gap) in o.concurrent.items():
            if key not in by_incident or anchor not in by_incident:
                continue
            fam_ok = family is None or family in {
                f for t, f in zip(o.truth, o.family, strict=True) if t in (key, anchor)
            }
            if not fam_ok:
                continue
            concurrent_pairs += 1
            concurrent_merged += bool(by_incident[key] & by_incident[anchor])
    incidents = len(incident_sits)
    split = sum(1 for sits in incident_sits.values() if len(sits) >= 2)
    # The operator's repair work (ADR #409): one merge per extra piece of an incident, one move per
    # foreign incident in a situation holding one of these incidents. Per incident, so a split of
    # 2 854 incidents and one of 600 read on one scale.
    merges = sum(len(sits) - 1 for sits in incident_sits.values())
    held = {(name, s) for (name, _t), sits in incident_sits.items() for s in sits}
    moves = sum(len(sit_incidents[key]) - 1 for key in held)
    contaminated = sum(
        1
        for (name, _t), sits in incident_sits.items()
        if any(len(sit_incidents[(name, s)]) >= 2 for s in sits)
    )
    result = {
        "activations": float(n),
        "unrecognised_clears": float(clears),
        "incidents": float(incidents),
        "pairwise_f1": metrics.pairwise_f1(pred, truth),
        "ari": metrics.adjusted_rand_index(pred, truth),
        "under_merge_rate": split / incidents if incidents else 0.0,
        "repair_gestures": (merges + moves) / incidents if incidents else 0.0,
        "split_bag_intact_rate": concurrent_merged / concurrent_pairs if concurrent_pairs else 0.0,
        "concurrent_pairs": float(concurrent_pairs),
        "asserted_negative_respected_rate": respected / compared if compared else 1.0,
        "negatives_compared": float(compared),
    }
    if family is None:
        result["over_merge_rate"] = metrics.over_merge_rate(pred, truth)
        result["situations"] = float(len(set(pred)))
    else:
        result["over_merge_rate"] = contaminated / incidents if incidents else 0.0
    return result


def first_hour(outcome: Outcome, horizon_s: float = 3600.0) -> Outcome:
    """The same outcome restricted to the first hour of the stream — a fresh appliance's hour."""
    if not outcome.ts:
        return outcome
    start = outcome.ts[0]
    keep = [i for i, t in enumerate(outcome.ts) if t - start <= horizon_s]
    remap = {old: new for new, old in enumerate(keep)}
    return Outcome(
        outcome.name,
        [outcome.truth[i] for i in keep],
        [outcome.family[i] for i in keep],
        [outcome.pred[i] for i in keep],
        [outcome.ts[i] for i in keep],
        [(remap[a], remap[b], s) for a, b, s in outcome.compared if a in remap and b in remap],
        outcome.concurrent,
        clear=[outcome.clear[i] for i in keep] if outcome.clear else [],
    )


def restrict(outcome: Outcome, keep: Sequence[int]) -> Outcome:
    remap = {old: new for new, old in enumerate(keep)}
    return Outcome(
        outcome.name,
        [outcome.truth[i] for i in keep],
        [outcome.family[i] for i in keep],
        [outcome.pred[i] for i in keep],
        [outcome.ts[i] for i in keep],
        [(remap[a], remap[b], s) for a, b, s in outcome.compared if a in remap and b in remap],
        outcome.concurrent,
        clear=[outcome.clear[i] for i in keep] if outcome.clear else [],
    )
