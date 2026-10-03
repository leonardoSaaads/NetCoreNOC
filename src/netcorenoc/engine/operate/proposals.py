"""The fast loop's one rule about confirmed work: **a model never grows an `open` situation.**

v0.27.0 (ADR #428). An operator who promoted a situation to `open` has confirmed its grouping —
*this is the incident I am working*. Until this release the correlator went on adding alarms to it
exactly as it adds them to a `new` one, so an operator's confirmed bag changed under them, silently,
on the model's opinion alone. The maintainer reported it from the Situations screen.

The correlator's placement (`grouping.Grouper.place`) is untouched: it still says where the model
thinks an alarm belongs. This module decides what the **engine may do** with that opinion, given
each situation's state, and it is the only place that decision is made:

* the alarm's best home is `new`, or `pending`, and no `open` situation is involved -> as before;
* the model would place the alarm (or merge a `new` situation) **with an `open` one** -> the
  alarm, and any `new` situations the model would have merged, go into a `pending` situation that
  **proposes** to join the `open` one (created on the first such alarm, reused after), with the
  model's probability that they belong together. An operator accepts (a merge) or rejects (the
  pending bag becomes `new`, a situation of its own);
* a rejected bag is never proposed to the same target again — the operator has answered;
* two `open` situations are never merged by the model, and a `pending` bag only ever folds into
  another that proposes the same target.

Read-only against the store (one bounded read of the states involved, under the batch lock the
caller holds) and pure otherwise. On a schema without `0027` it proposes nothing and the engine
behaves exactly as it did in v0.26.0 — which is what `tests/store/test_upgrade.py` relies on.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from netcorenoc.engine.correlate.grouping import Placement

if TYPE_CHECKING:  # pragma: no cover - type-only, no runtime edge (tests/repo/test_layers.py)
    from netcorenoc.engine.operate.engine import Engine

__all__ = ["Route", "lapse", "route"]


@dataclass(frozen=True)
class Route:
    """What the engine may execute: where the alarm goes, what folds into it, and whether that
    situation is (or opens as) a proposal to join ``propose``."""

    sid: int | None
    merges: tuple[int, ...] = ()
    propose: int | None = None
    confidence: float = 0.0

    @property
    def act(self) -> str:
        """The lifecycle act that creates ``sid`` when it does not exist yet (`TRANSITIONS`)."""
        return "correlate" if self.propose is None else "propose"


def _probability(evidence: float) -> float:
    """The model's probability from its mean pair evidence. League models link at logit 0, so the
    evidence *is* the logit and this is the probability itself (ADR #424)."""
    if evidence >= 0.0:
        return 1.0 / (1.0 + math.exp(-min(evidence, 700.0)))
    e = math.exp(max(evidence, -700.0))
    return e / (1.0 + e)


async def route(engine: Engine, placement: Placement, own: int | None) -> Route:
    """Turn the correlator's placement into what the engine may do. See the module docstring."""
    sid = placement.join if placement.join in engine.members else None
    merges = [m for m in placement.merge if m != sid and m in engine.members]
    if not engine.store._has_proposals:  # pre-0027 schema: v0.26.0 behaviour, byte for byte
        return Route(sid, tuple(merges))
    ids = ([sid] if sid is not None else []) + merges
    if not ids:
        return Route(None)
    states = await engine.store.lifecycle_states(ids)

    def status(i: int) -> str:
        return states.get(i, ("new", None))[0]

    def target_of(i: int) -> int | None:
        return states.get(i, ("new", None))[1]

    rejected = engine.rejected_proposals
    if sid is not None and status(sid) == "pending":
        # Growing a proposal is model territory; it may absorb `new` bags not refused its target,
        # and pending bags proposing the same target. Never an `open` one.
        proposed = target_of(sid)
        keep = [
            m
            for m in merges
            if (status(m) == "new" and (m, proposed) not in rejected)
            or (status(m) == "pending" and target_of(m) == proposed)
        ]
        return Route(sid, tuple(keep))
    target: int | None
    if sid is not None and status(sid) == "open":
        target = sid
    else:
        target = next((m for m in merges if status(m) == "open" and (sid, m) not in rejected), None)
    if target is None:
        # No confirmed situation involved: `new` bags merge as before; pending bags stay proposals.
        return Route(sid, tuple(m for m in merges if status(m) == "new"))
    if own == target:
        # The alarm re-activated inside the confirmed situation: it is already a member, nothing
        # changes, and a re-activation is not evidence enough to fold other bags into it.
        return Route(target)
    support = {s: mean for s, mean, _n in placement.support}
    confidence = _probability(support.get(target, 0.0))
    pending = await engine.store.pending_for(target)
    folds = tuple(
        i
        for i in ids
        if i not in (target, pending)
        and (
            (status(i) == "new" and (i, target) not in rejected)
            or (status(i) == "pending" and target_of(i) == target)
        )
    )
    return Route(pending, folds, target, confidence)


async def lapse(store: Any, now: float) -> None:
    """A proposal whose target is no longer `open` has nothing to join: it lapses to `new`. Run
    by the maintenance pass after its closes, so a target resolved in a pass lapses in it too."""
    for sid in await store.orphan_proposals():
        await store.withdraw_proposal(sid, now)
