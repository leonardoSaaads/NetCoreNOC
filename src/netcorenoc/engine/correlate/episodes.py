"""Episode memory: how many **separate occasions** two things have gone wrong together.

## Why the learned affinities were not enough (measured, v0.26.0)

`A` and `E` are NPMI over co-occurrence **mass**, and mass grows with every alarm in a burst. Six
alarms in one burst clear `MIN_EDGE_N` (F61), so two unrelated incidents that happen to overlap
once teach the appliance that their elements "go together" — within seconds, from nothing but their
own overlap. `eval/corpus/dual_incident.json` rewritten onto one vendor measures it: every alarm in
one situation, `over_merge_rate 1.000`, on the tree v0.26.0 started from. v0.18.0's enterprise-
subtree gate hid it for the two-vendor file and could not for the one-vendor one.

The quantity that distinguishes *related* from *coincident* is **recurrence across independent
occasions**. Two elements at the ends of one span co-fail every time the span is cut; two elements
that shared one bad afternoon co-failed once. This module counts the occasions.

## What an episode is

Two activations co-occur when they arrive within :data:`CO_WINDOW_S` of each other. A pair's
co-occurrences belong to one **episode** until the pair has been quiet for :data:`EPISODE_GAP_S`;
the next co-occurrence after that opens a new episode. So a burst of 300 alarms is **one** episode
for every pair in it, and a span cut on Monday and again on Thursday is two.

Three granularities, each a relation and never an identifier (migration 0008 rule 2):

* ``ne`` — the two network elements;
* ``cls`` — the two alarm classes, on any elements;
* ``item`` — the two (element, class) signatures: *"this fault on that box and that fault on this
  one"*, the finest memory and the one a recurrence of the same fault hits exactly.

The feature a scorer reads is the **prior** count: episodes *before* the current one. Counting the
current episode would let an incident's own alarms vote for it, which is the defect above.

## Cost

Pure, in memory, O(1) per observed pair, bounded: each table holds at most :data:`MAX_PAIRS` keys
and :meth:`prune` evicts the stalest when it is exceeded. No clock is read — every method takes the
activation's own timestamp — so a replay and the live engine compute the same numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "CO_WINDOW_S",
    "EPISODE_GAP_S",
    "MAX_PAIRS",
    "EpisodeMemory",
    "EpisodeRow",
]

#: Two activations this close in time are a co-occurrence.
CO_WINDOW_S = 300.0
#: A pair quiet for this long has ended its episode; the next co-occurrence opens a new one.
EPISODE_GAP_S = 1800.0
#: Per-table ceiling. Past it the stalest entries go first (least recently co-occurring).
MAX_PAIRS = 200_000
#: Entries older than this are forgotten at prune time: a memory is a year, not forever.
MAX_AGE_S = 365 * 86400.0
#: How many strongest partners each element keeps for candidate recall.
TOP_PARTNERS = 6

Key = tuple[int, ...]
# (table, key, count, last_ts) — what is persisted.
EpisodeRow = tuple[str, Key, int, float]


def _pair(a: Key, b: Key) -> Key:
    return (*a, *b) if a <= b else (*b, *a)


@dataclass
class _Table:
    counts: dict[Key, list[float]] = field(default_factory=dict)  # key -> [count, last_ts]
    dirty: set[Key] = field(default_factory=set)

    def prior(self, key: Key, now: float) -> int:
        entry = self.counts.get(key)
        if entry is None:
            return 0
        count, last = int(entry[0]), entry[1]
        # The current episode is still open: it must not count as evidence for itself.
        return count - 1 if now - last <= EPISODE_GAP_S else count

    def observe(self, key: Key, now: float) -> bool:
        """Record a co-occurrence at ``now``. True when it opened a NEW episode."""
        entry = self.counts.get(key)
        self.dirty.add(key)
        if entry is None:
            self.counts[key] = [1.0, now]
            return True
        opened = now - entry[1] > EPISODE_GAP_S
        if opened:
            entry[0] += 1.0
        entry[1] = max(entry[1], now)
        return opened

    def prune(self, now: float) -> None:
        stale = [k for k, (_, last) in self.counts.items() if now - last > MAX_AGE_S]
        for key in stale:
            del self.counts[key]
        if len(self.counts) > MAX_PAIRS:
            ordered = sorted(self.counts.items(), key=lambda kv: (kv[1][1], kv[0]))
            for key, _ in ordered[: len(self.counts) - MAX_PAIRS]:
                del self.counts[key]


@dataclass
class EpisodeMemory:
    """The three tables, plus each element's strongest partners (for candidate recall)."""

    ne: _Table = field(default_factory=_Table)
    cls: _Table = field(default_factory=_Table)
    item: _Table = field(default_factory=_Table)
    partners: dict[int, dict[int, int]] = field(default_factory=dict)

    # -- reads (the features) --------------------------------------------------------------

    def ne_prior(self, a: int, b: int, now: float) -> int:
        return 0 if a == b else self.ne.prior(_pair((a,), (b,)), now)

    def cls_prior(self, a: int, b: int, now: float) -> int:
        return self.cls.prior(_pair((a,), (b,)), now)

    def item_prior(self, a: tuple[int, int], b: tuple[int, int], now: float) -> int:
        return self.item.prior(_pair(a, b), now)

    def degree(self, ne: int) -> int:
        """How many distinct elements this one has ever co-failed with — a hub's signature."""
        return len(self.partners.get(ne, ()))

    def neighbours(self, ne: int) -> list[int]:
        """The partners this element has co-failed with on the most separate occasions."""
        mine = self.partners.get(ne)
        if not mine:
            return []
        ranked = sorted(mine.items(), key=lambda kv: (-kv[1], kv[0]))
        return [partner for partner, count in ranked[:TOP_PARTNERS] if count >= 2]

    # -- writes ----------------------------------------------------------------------------

    def observe(self, a: tuple[int, int], b: tuple[int, int], now: float) -> None:
        """One co-occurrence of two (element, class) items at ``now``."""
        self.item.observe(_pair(a, b), now)
        self.cls.observe(_pair((a[1],), (b[1],)), now)
        if a[0] != b[0] and self.ne.observe(_pair((a[0],), (b[0],)), now):
            for x, y in ((a[0], b[0]), (b[0], a[0])):
                mine = self.partners.setdefault(x, {})
                mine[y] = mine.get(y, 0) + 1

    def prune(self, now: float) -> None:
        for table in (self.ne, self.cls, self.item):
            table.prune(now)
        live = set(self.ne.counts)
        for ne, mine in list(self.partners.items()):
            for other in list(mine):
                if _pair((ne,), (other,)) not in live:
                    del mine[other]
            if not mine:
                del self.partners[ne]

    # -- persistence -----------------------------------------------------------------------

    def flush(self) -> list[EpisodeRow]:
        rows: list[EpisodeRow] = []
        for name, table in (("ne", self.ne), ("cls", self.cls), ("item", self.item)):
            for key in sorted(table.dirty):
                entry = table.counts.get(key)
                if entry is not None:
                    rows.append((name, key, int(entry[0]), entry[1]))
            table.dirty.clear()
        return rows

    def load(self, rows: list[EpisodeRow]) -> None:
        tables = {"ne": self.ne, "cls": self.cls, "item": self.item}
        for name, key, count, last in rows:
            table = tables.get(name)
            if table is None:
                continue
            table.counts[key] = [float(count), float(last)]
            if name == "ne" and len(key) == 2:
                for x, y in ((key[0], key[1]), (key[1], key[0])):
                    self.partners.setdefault(x, {})[y] = count
