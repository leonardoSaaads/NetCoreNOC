"""Candidate generation, stage one: a cheap, wide recall with no hard time cut.

Until v0.26.0 the only candidates an arriving alarm was ever compared with were the newest hundred
live alarms of the last **120 seconds**. That window was the ceiling on everything above it: an
optical degradation whose stages are ten minutes apart, a BGP session timing out three minutes
after its link, the second half of a slow power failure — none of it could be grouped by any
scorer, because none of it was ever *scored*.

Every retrieval system solves this the same way: a cheap, high-recall first stage that proposes a
bounded set, and an expensive, precise second stage that scores it. This is the first stage. The
second stage is the model.

## What is recalled, beyond the window

Live alarms from the last :data:`RING_S` (the idle-close horizon, an hour) that share something
structural with the newcomer:

* the **same network element** — the last :data:`PER_NE` activations on it;
* the **same OID parent** — the last :data:`PER_PARENT` activations whose trap OID has the same
  parent arc (siblings in one vendor module: every stage of an amplifier chain);
* a **named neighbour** (v0.29.0, ADR #442) — the last :data:`PER_REF` activations that name this
  element's management address in their varbinds, and the last :data:`PER_REF` activations *from*
  each address this alarm names: the far end of a span, a BGP peer, the element a port faces. The
  topology the traps themselves carry, so a far end's alarm ten minutes later is still scored;
* a **learned neighbour** — the last :data:`PER_NEIGHBOUR` activations on each of the elements
  this one has co-failed with on at least two separate occasions (`episodes.EpisodeMemory`).

**Nothing here reads a situation.** Recall is a function of the alarm stream alone, so which pairs
get scored does not depend on how the model grouped anything earlier — which is what lets the
offline replay generate training pairs once and evaluate any number of models against them.

## Bounds

Every ring is a `deque` with a fixed ``maxlen``; the union is capped at :data:`MAX_RECALL`. Keys
(elements, OID parents) are pruned by the maintenance sweep when their newest entry is older than
the horizon. Per activation the work is at most ``PER_NE + PER_PARENT + (1 + features.MAX_REFS) *
PER_REF + NEIGHBOURS * PER_NEIGHBOUR`` dictionary and deque reads — no clock, no I/O, no lock.

## Why the named neighbour (measured, v0.29.0)

On the v0.29.0 validation streams, an alarm whose incident's nearest earlier alarm was 5 to 30
minutes before could reach none of its incident's open alarms through the rings above in 26 % of
cases, and between one and five minutes before in 18 %: a far end is a different element, its trap
usually a different OID subtree, and on a fresh appliance no element has co-failed with anything
yet. The address in the trap is the one relation available from the first trap on.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Container
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - type-only; no runtime edge
    from netcorenoc.engine.correlate.correlate import WindowAlarm

__all__ = [
    "MAX_RECALL",
    "PER_NE",
    "PER_NEIGHBOUR",
    "PER_PARENT",
    "PER_REF",
    "RING_S",
    "CandidateIndex",
]

RING_S = 3600.0
PER_NE = 24
PER_PARENT = 16
PER_REF = 8
PER_NEIGHBOUR = 6
MAX_RECALL = 64


def parent_of(arcs: tuple[str, ...]) -> tuple[str, ...]:
    """The parent arc of a trap OID — a subtree, compared as arcs (Appendix B)."""
    return arcs[:-1] if len(arcs) > 1 else arcs


@dataclass
class CandidateIndex:
    """Bounded rings of recent activations, keyed by element and by OID parent."""

    by_ne: dict[int, deque[WindowAlarm]] = field(default_factory=dict)
    by_parent: dict[tuple[str, ...], deque[WindowAlarm]] = field(default_factory=dict)
    #: v0.29.0 (ADR #442): activations by each address their varbinds name (`WindowAlarm.refs`).
    by_named: dict[str, deque[WindowAlarm]] = field(default_factory=dict)
    #: The element behind each management address seen, so a named address finds its NE ring.
    ne_of: dict[str, int] = field(default_factory=dict)

    def add(self, alarm: WindowAlarm) -> None:
        ring = self.by_ne.get(alarm.device_id)
        if ring is None:
            ring = self.by_ne[alarm.device_id] = deque(maxlen=PER_NE)
        ring.append(alarm)
        key = parent_of(alarm.arcs)
        pring = self.by_parent.get(key)
        if pring is None:
            pring = self.by_parent[key] = deque(maxlen=PER_PARENT)
        pring.append(alarm)
        if alarm.source:
            self.ne_of[alarm.source] = alarm.device_id
        for ref in alarm.refs:
            nring = self.by_named.get(ref)
            if nring is None:
                nring = self.by_named[ref] = deque(maxlen=PER_REF)
            nring.append(alarm)

    def rings(self) -> list[deque[WindowAlarm]]:
        """Every ring, for the correlator's liveness bound."""
        return [*self.by_ne.values(), *self.by_parent.values(), *self.by_named.values()]

    def recall(
        self,
        new: WindowAlarm,
        live: Container[int],
        neighbours: list[int],
        exclude: Container[int],
    ) -> list[WindowAlarm]:
        """Live alarms from the rings that are within :data:`RING_S` of ``new``, newest first per
        ring, deduplicated, capped at :data:`MAX_RECALL`, returned oldest-first.

        ``exclude`` is what stage one already has (the window candidates), so a ring never spends
        its budget re-proposing an alarm the window already proposed.
        """
        seen: set[int] = set()
        out: list[WindowAlarm] = []
        rings: list[tuple[deque[WindowAlarm], int]] = []
        own = self.by_ne.get(new.device_id)
        if own is not None:
            rings.append((own, PER_NE))
        parent = self.by_parent.get(parent_of(new.arcs))
        if parent is not None:
            rings.append((parent, PER_PARENT))
        # The named neighbours: who names me, and the elements I name (`features.MAX_REFS` at most).
        naming = self.by_named.get(new.source) if new.source else None
        if naming is not None:
            rings.append((naming, PER_REF))
        for ref in sorted(new.refs):
            ne = self.ne_of.get(ref)
            named = self.by_ne.get(ne) if ne is not None and ne != new.device_id else None
            if named is not None:
                rings.append((named, PER_REF))
        for ne in neighbours:
            ring = self.by_ne.get(ne)
            if ring is not None:
                rings.append((ring, PER_NEIGHBOUR))
        for ring, budget in rings:
            taken = 0
            for alarm in reversed(ring):
                if new.ts - alarm.ts > RING_S or taken >= budget or len(out) >= MAX_RECALL:
                    break
                aid = alarm.alarm_id
                if aid == new.alarm_id or aid in seen or aid in exclude or aid not in live:
                    continue
                seen.add(aid)
                out.append(alarm)
                taken += 1
        out.sort(key=lambda a: (a.ts, a.alarm_id))
        return out

    def prune(self, now: float) -> None:
        """Drop rings whose newest entry has aged out of the horizon."""
        for ne in [k for k, ring in self.by_ne.items() if not ring or now - ring[-1].ts > RING_S]:
            del self.by_ne[ne]
        stale = [k for k, ring in self.by_parent.items() if not ring or now - ring[-1].ts > RING_S]
        for key in stale:
            del self.by_parent[key]
        named = [k for k, ring in self.by_named.items() if not ring or now - ring[-1].ts > RING_S]
        for ref in named:
            del self.by_named[ref]
        for addr in [a for a, ne in self.ne_of.items() if ne not in self.by_ne]:
            del self.ne_of[addr]
