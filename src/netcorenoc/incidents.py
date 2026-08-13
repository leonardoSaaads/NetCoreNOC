"""Incident identity: **one function, one implementation, and it follows the chain.**

A labelled bag is a verdict about a *situation*. The unit every clustered estimate in this project
resamples is an **incident** — because two bags that were merged into one are one incident, and
treating them as two overstates independence by exactly the factor the merge removed.

`situation.merged_into` (added by `0008`, forward-only) records the destination of a merge. Every
consumer before v0.10.0 read it as `COALESCE(situation.merged_into, feedback.situation_id)`, which
is **one hop**. `merge_situations(sid, other)` marks the source merged into `sid`, and `sid` can
itself later be merged, so a chain can be longer than one hop and resolving identity means
**following `merged_into` to a fixed point**.

## Why this is a module and not four SQL expressions

v0.10.0 measured four places computing incident identity independently:
`store/shadow.py::labelled_pairs`, `store/shadow.py::labelled_bags`, `agreement.py`'s bag query and
`bias.py`'s `distinct_incidents`. Two of them now call this function and the SQL stopped computing
identity at all; the other two are named in the census as still one-hop
(`docs/gates/v0.10.0-phase-2.md` §4).

The reason this matters is specific and silent: the **estimator** and the **seal** both group by
incident, and if they disagreed about which incidents exist, the seal would be reserving a different
set from the one the estimator excluded — and **nothing would go red.** A guarantee whose failure is
invisible has to be structural, so there is one function and the callers cannot express the other
answer.

## The two unsound chains, reported and never silently collapsed

The schema forbids neither, and no code prevents either:

* **a cycle** — `a → b → a`. Resolved to the **minimum id in the cycle**, so two bags entering the
  same cycle at different points get the **same** incident, and flagged.
* **a chain that does not terminate** within :data:`MAX_CHAIN_DEPTH`. Flagged separately, because
  *"these situations point at each other"* and *"this chain is implausibly long"* are different
  facts about a database and a reader must be able to tell which one happened.

`PREREGISTRATION-0.10.0.md` §7.9 registers what follows: affected incidents are excluded, counted,
and reported as `unknown`, and the verdict is `INSUFFICIENT_EVIDENCE` if their exclusion would
change any floor evaluation. **Neither condition raises**, because a corrupt merge chain is a fact
about the corpus to be reported, not an error that should stop an offline report.

Pure arithmetic over a mapping. No store, no lock, no I/O, no clock.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

__all__ = [
    "MAX_CHAIN_DEPTH",
    "IncidentMap",
    "Resolution",
    "resolve",
    "resolve_all",
    "stamp",
]

# A merge chain longer than this is not a chain. The bound is deliberately far above anything a
# real database could produce — a chain of 64 means 64 successive merges into successive
# destinations — so hitting it is evidence of corruption rather than of a busy night. It is a
# SECOND guard: the `seen` set already terminates on a cycle, and this terminates on a map that is
# somehow acyclic and still absurd. Two guards because they detect two different defects, and
# collapsing them would make the report say "cycle" about something that is not one.
MAX_CHAIN_DEPTH = 64


@dataclass(frozen=True)
class Resolution:
    """One situation's incident, and whether the chain that produced it was sound."""

    incident: int
    cycle: bool = False
    unterminated: bool = False


def resolve(situation_id: int, merged_into: Mapping[int, int]) -> Resolution:
    """Follow `merged_into` from ``situation_id`` to a fixed point.

    ``merged_into`` maps a merged situation's id to its destination. A situation absent from the
    mapping is its own incident, which is the common case and the one that must stay cheap.

    **A cycle resolves to the minimum id in the cycle**, not to wherever the walk happened to stop.
    That is not a cosmetic choice: two bags entering the same cycle at different points would
    otherwise be assigned different incidents, so a corrupt chain would silently *inflate* the
    incident count — the exact error the transitive resolution exists to remove, re-entering through
    the failure path. Minimum, because `sid = min(sids)` is already this project's convention for
    naming a merged group.

    **The minimum is over the cycle and not over the walk** (F50, v0.10.1). Until v0.10.1 this
    returned `min(seen)`, and `seen` is the whole walk — including the *tail* that led into the
    cycle. A tail id smaller than every cycle member became the answer for that tail alone, so the
    tail resolved to itself while the cycle members resolved to the cycle minimum: `{1: 7, 7: 8,
    8: 7}` reported **two** incidents where there is one, which is the failure mode this paragraph
    claims to prevent, produced by the line that claimed to prevent it.
    """
    seen: set[int] = {situation_id}
    current = situation_id
    for _step in range(MAX_CHAIN_DEPTH):
        nxt = merged_into.get(current)
        if nxt is None:
            return Resolution(current)
        if nxt in seen:
            return Resolution(min(_cycle_members(nxt, merged_into)), cycle=True)
        seen.add(nxt)
        current = nxt
    return Resolution(current, unterminated=True)


def _cycle_members(entry: int, merged_into: Mapping[int, int]) -> set[int]:
    """The ids on the cycle reached at ``entry``, and nothing that merely led into it.

    ``entry`` is the already-visited node the walk arrived at a second time, so it is **on** the
    cycle by construction and every node from it back to itself has an edge. Walking that loop is
    what separates the cycle from the tail, and the separation is the whole of F50: `min` over the
    walk is not `min` over the cycle whenever the tail carries the smaller id.
    """
    members = {entry}
    node = merged_into[entry]
    while node != entry:
        members.add(node)
        node = merged_into[node]
    return members


@dataclass(frozen=True)
class IncidentMap:
    """Every situation's incident, beside the one-hop answer it replaces.

    Both are carried on purpose. The census prints the transitive count **and** the one-hop count so
    the change is visible — including when it is zero, which is what the v0.10.0 corpus produces
    (`docs/gates/v0.10.0-phase-1.md` §5.1: every chain in it is exactly one hop). A number that only
    appears when it differs is a number nobody checks.
    """

    incident_of: Mapping[int, int]
    one_hop_of: Mapping[int, int]
    cycles: frozenset[int]
    unterminated: frozenset[int]

    @property
    def incidents(self) -> int:
        """Distinct merge-aware incidents. **The `n` every clustered estimate is expressed in.**"""
        return len(set(self.incident_of.values()))

    @property
    def incidents_one_hop(self) -> int:
        """What `COALESCE(merged_into, situation_id)` would have reported."""
        return len(set(self.one_hop_of.values()))

    @property
    def unsound_situations(self) -> frozenset[int]:
        """Every situation whose identity is `unknown` under §7.9, counted rather than dropped."""
        return self.cycles | self.unterminated


def resolve_all(situation_ids: Iterable[int], merged_into: Mapping[int, int]) -> IncidentMap:
    """Resolve a whole corpus at once. **The one entry point every caller uses.**

    Deterministic: the result depends only on the inputs, and the inputs are read with a stable
    ordering by the caller. Nothing here consults a clock or an RNG.
    """
    incident_of: dict[int, int] = {}
    one_hop_of: dict[int, int] = {}
    cycles: set[int] = set()
    unterminated: set[int] = set()
    for situation_id in situation_ids:
        outcome = resolve(situation_id, merged_into)
        incident_of[situation_id] = outcome.incident
        one_hop_of[situation_id] = merged_into.get(situation_id, situation_id)
        if outcome.cycle:
            cycles.add(situation_id)
        if outcome.unterminated:
            unterminated.add(situation_id)
    return IncidentMap(
        incident_of=incident_of,
        one_hop_of=one_hop_of,
        cycles=frozenset(cycles),
        unterminated=frozenset(unterminated),
    )


def stamp(
    rows: list[dict[str, Any]], mapping: IncidentMap, *, key: str = "situation_id"
) -> list[dict[str, Any]]:
    """Set `incident` on every row from ``mapping``. **The only way that column comes to exist.**

    Until v0.10.0 the `incident` column was produced by a `COALESCE` inside two SQL statements, so
    every consumer received a one-hop answer and none of them could tell. Now the store returns
    `situation_id` and nothing else about identity, and this is the single step that turns it into
    an incident — which means a consumer that forgets to call it has no `incident` key at all and
    fails loudly, rather than quietly reading a subtly wrong one.

    Mutates in place and also returns the list, so a caller can write
    ``bags = stamp(await store.labelled_bags(), identity)`` without a second name.

    A row whose situation is not in the mapping is left to resolve to itself, which is what
    :func:`resolve` does for an unmerged situation anyway; the fallback exists so that a row read
    after the edges were snapshotted cannot raise.
    """
    for row in rows:
        situation_id = int(row[key])
        row["incident"] = mapping.incident_of.get(situation_id, situation_id)
    return rows
