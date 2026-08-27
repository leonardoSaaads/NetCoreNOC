"""What the labelled corpus contains — **the census, and the incident identity it rests on**.

v0.10.0, Workstream 1. Split out of `shadow.py`, which reached 417 of its 400-line budget when the
transitive incident resolution arrived. The seam is a real one rather than a size accident:
`shadow.py` owns the shadow **lifecycle** — when to sample, when to train, what to write back — and
this owns the question *what does the labelled corpus actually hold*, which every floor, every
estimate and the seal all ask and none of them owns.

**The one place `incident` comes to exist.** The store returns `situation_id` and the merge
**edges**; `netcorenoc.incidents` follows the edges to a fixed point; this module is where the two
meet, and it is the only thing that writes the `incident` key onto a row. A consumer that skipped it
would have no `incident` key at all rather than a quietly one-hop one — which is the difference
between a loud failure and the silent disagreement between the estimator and the seal that
`PREREGISTRATION-0.10.0.md` §3.3 exists to prevent.

Nothing here fits, writes or decides. It reads and it counts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from netcorenoc.engine.dataset.incidents import IncidentMap, resolve_all, stamp

if TYPE_CHECKING:  # pragma: no cover - type-only, no runtime edge (tests/test_layers.py)
    from netcorenoc.store import Store

__all__ = ["CorpusStats", "corpus_stats", "first_label_per_incident", "resolve_identity"]

DAY_S = 86400.0


@dataclass(frozen=True)
class CorpusStats:
    """What the labelled corpus actually contains, in the units the floors are expressed in."""

    bags: int = 0
    confirm_bags: int = 0
    split_bags: int = 0
    mixed_bags: int = 0
    incidents: int = 0
    # v0.10.0 (Workstream 1). What `COALESCE(merged_into, situation_id)` would have reported, kept
    # beside the merge-aware count so the census can print the REDUCTION rather than only the
    # result. Zero when no identity map was supplied, which is how the pre-v0.10.0 callers behave.
    # A number that only appears when it differs is a number nobody checks.
    incidents_one_hop: int = 0
    # Situations whose merge chain met the cycle guard or the depth guard. Counted, never dropped:
    # `PREREGISTRATION-0.10.0.md` §7.9 makes them `unknown` and a verdict trigger.
    unsound_chains: int = 0
    operators: int = 0
    top_operator_share_pct: float = 0.0
    pairs: int = 0
    oldest_label_at: float | None = None
    newest_label_at: float | None = None

    @property
    def reduction_from_one_hop(self) -> int:
        """How many incidents following the merge chain removed, against the one-hop answer.

        **Zero is a real answer**, and on this project's own corpus it is the observed one: every
        merge chain in it is exactly one hop (`docs/gates/v0.10.0-phase-1.md` §5.1). Printed
        regardless, because a number that only appears when it differs is a number nobody checks.
        """
        return self.incidents - self.incidents_one_hop

    @property
    def span_days(self) -> float:
        """The labelling window. Zero when there is one label or none, and the projection says so
        rather than extrapolating from a single instant."""
        if self.oldest_label_at is None or self.newest_label_at is None:
            return 0.0
        return (self.newest_label_at - self.oldest_label_at) / DAY_S


async def resolve_identity(
    store: Store, bags: list[dict[str, Any]], pairs: list[dict[str, Any]]
) -> IncidentMap:
    """Resolve every read row's incident **once**, through the one implementation, and stamp it.

    v0.10.0's Workstream 1. The store used to answer this with
    `COALESCE(s.merged_into, f.situation_id)` inside each of two statements — one hop, twice, with
    no way for a consumer to tell. Now the store returns the merge **edges** and
    :func:`netcorenoc.incidents.resolve_all` follows them to a fixed point, so the bags and the
    pairs are stamped from **the same map** and cannot disagree about which incidents exist.

    Caller holds ``store.lock``: this is three reads on the shared connection, exactly like the ones
    around it.
    """
    edges = await store.merge_edges()
    situations = {int(row["situation_id"]) for row in bags}
    situations |= {int(row["situation_id"]) for row in pairs}
    identity = resolve_all(sorted(situations), edges)
    stamp(bags, identity)
    stamp(pairs, identity)
    return identity


def corpus_stats(bags: list[dict[str, Any]], identity: IncidentMap) -> CorpusStats:
    """The corpus, counted in the units the floors are expressed in.

    A **mixed** bag is one whose promoted pairs sit on both sides of the champion's threshold — the
    only population where there was a decision to get wrong. Phase 0 measured five of them in the
    richest corpus this repository can construct, which is why this count is the one the report
    leads with.

    ``identity`` is **required, and that is the point.** It carries the merge-aware resolution and
    the one-hop answer it replaces, so the report can print the *reduction* rather than only the
    result. Making it optional would let a caller obtain a `CorpusStats` whose `incidents` came from
    somewhere else — which is precisely the silent disagreement between the estimator and the seal
    that `PREREGISTRATION-0.10.0.md` §3.3 exists to prevent. A caller that has not resolved identity
    cannot call this function.
    """
    if not bags:
        return CorpusStats()
    per_operator: dict[str, int] = {}
    for bag in bags:
        principal = str(bag["principal"])
        if principal != "(unattributed)":
            per_operator[principal] = per_operator.get(principal, 0) + 1
    attributed = sum(per_operator.values())
    times = [float(bag["label_at"]) for bag in bags]
    return CorpusStats(
        bags=len(bags),
        confirm_bags=sum(1 for b in bags if b["verdict"] == "confirm"),
        split_bags=sum(1 for b in bags if b["verdict"] == "split"),
        mixed_bags=sum(1 for b in bags if 0 < int(b["accepted"]) < int(b["pairs"])),
        incidents=identity.incidents,
        incidents_one_hop=identity.incidents_one_hop,
        unsound_chains=len(identity.unsound_situations),
        operators=len(per_operator),
        top_operator_share_pct=(
            100.0 * max(per_operator.values()) / attributed if attributed else 0.0
        ),
        pairs=sum(int(b["pairs"]) for b in bags),
        oldest_label_at=min(times),
        newest_label_at=max(times),
    )


async def first_label_per_incident(store: Store) -> dict[int, float]:
    """`incident -> the timestamp of its EARLIEST label`. What the seal is ordered by.

    `PREREGISTRATION-0.10.0.md` §3.3(2). Earliest rather than latest because the seal is meant to
    hold the *most recent third of the corpus by when it was first labelled*, and a bag relabelled
    today does not make its incident new.

    Resolved through the one implementation, like everything else that groups by incident: an
    ordering built on a one-hop identity would seal a different set from the one the estimator
    excludes, and nothing would go red.

    :func:`resolve_identity`'s return value is deliberately not bound. It stamps ``bags`` **in
    place**, which is the effect this function needs, and the map it also returns is the caller's
    business elsewhere. Until v0.10.1 this read ``identity = await resolve_identity(...)`` followed
    by ``_ = identity`` — a discarded computation, and the kind of leftover a dead-code gate cannot
    see because the discard is what makes the name used.
    """
    bags = await store.labelled_bags()
    await resolve_identity(store, bags, [])
    earliest: dict[int, float] = {}
    for bag in bags:
        incident = int(bag["incident"])
        at = float(bag["label_at"])
        if incident not in earliest or at < earliest[incident]:
            earliest[incident] = at
    return earliest
