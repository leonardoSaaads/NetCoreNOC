"""What a batch owes the situations it touched: the derived name, and the root's sample.

ADR #448. Both are **projections of membership**, and until this module both were recomputed on
every activation that changed one:

* `store.add_alarm_to_situation` refreshed `situation.derived_name` with two aggregate reads over
  the **whole** membership. That is O(members) per join, O(members²) over a situation's life, and
  in a storm it was the largest single cost on the batch path. Measured on a 3 000-alarm storm that
  forms a few large situations: 2.6 ms of every trap's 14.7 ms, more than the model.
* `Precedence.pick_root` sorted the **whole** membership to take its earliest `ROOT_SAMPLE`.

**The name is refreshed once per situation per batch, before the commit** (:meth:`flush`). Nothing
on the batch path reads `derived_name` — the console and the API do, and both take `store.lock`,
which the batch holds until it commits — so the name every reader can see is the name of the
membership every reader can see, exactly as before. "Never stale" (DECISIONS #257) is kept at the
only boundary anyone can observe; every other writer of membership (operator moves, merges,
splits) still refreshes in the same statement group.

**The root's sample is kept incrementally** (:meth:`earliest`), and it is *exactly*
``sorted(members, key=first_seen)[:ROOT_SAMPLE]``: an appended member goes in after every member
with an equal ``first_seen`` (``bisect_right``), which is where a stable sort puts it. Any change
that is not an append — a list replaced by a split, a member moved out — is detected and the sample
is rebuilt from scratch, so the cache can only ever be exact or absent.

No lock, no I/O but :meth:`flush`'s, which runs inside the caller's transaction.
"""

from __future__ import annotations

import bisect
from collections.abc import Collection
from dataclasses import dataclass, field

from netcorenoc.engine.correlate.rootcause import ROOT_SAMPLE, Member
from netcorenoc.store import Store

__all__ = ["SituationUpkeep"]


def _first_seen(member: Member) -> float:
    return member.first_seen


@dataclass
class _Sample:
    members: list[Member]  # the list it was taken from, by identity
    seen: int  # how many of its members the sample has accounted for
    last: Member | None  # members[seen - 1] when it was taken, by identity
    earliest: list[Member]


@dataclass
class SituationUpkeep:
    """The batch's per-situation debts and the root samples. One per engine."""

    _renamed: set[int] = field(default_factory=set)
    _samples: dict[int, _Sample] = field(default_factory=dict)

    def renamed(self, situation_id: int) -> None:
        """``situation_id`` gained a member in this batch; its name is refreshed before commit."""
        self._renamed.add(situation_id)

    async def flush(self, store: Store) -> None:
        """Refresh every name the batch moved. In the caller's transaction, before its commit."""
        for situation_id in sorted(self._renamed):
            await store.refresh_derived_name(situation_id)
        self._renamed.clear()

    def discard(self) -> None:
        """The batch rolled back: the names it would have refreshed were never changed."""
        self._renamed.clear()

    def earliest(self, situation_id: int, members: list[Member]) -> list[Member]:
        """``sorted(members, key=first_seen)[:ROOT_SAMPLE]``, without sorting ``members``."""
        cached = self._samples.get(situation_id)
        if (
            cached is None
            or cached.members is not members
            or len(members) < cached.seen
            or (cached.seen and members[cached.seen - 1] is not cached.last)
        ):
            ordered = sorted(members, key=_first_seen)[:ROOT_SAMPLE]
            cached = _Sample(members, len(members), members[-1] if members else None, ordered)
            self._samples[situation_id] = cached
            return cached.earliest
        for member in members[cached.seen :]:
            bisect.insort_right(cached.earliest, member, key=_first_seen)
            del cached.earliest[ROOT_SAMPLE:]
        cached.seen = len(members)
        cached.last = members[-1] if members else None
        return cached.earliest

    def retain(self, live: Collection[int]) -> None:
        """Forget the samples of situations that are no longer open (the maintenance sweep)."""
        for situation_id in [s for s in self._samples if s not in live]:
            del self._samples[situation_id]
