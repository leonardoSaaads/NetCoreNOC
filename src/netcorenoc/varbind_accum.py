"""The profiler's bounded accumulators: per-varbind statistics, scored candidates, FD evidence.

Extracted from `varbind_profile.py` in v0.7.4 (DECISIONS #97) — **one extraction, not a package**,
because there is one coherent thing here and `VarbindProfiler` is the other. The three classes are
the *state*; the profiler is the *policy* that reads it.

Every structure is **capped and evictable**, which is not a detail: they live in memory in the
engine, under the batch lock, on the ingest path. `Accumulator` stops tracking new values at
`MAX_TRACKED_VALUES` and stops keeping readable ones at `MAX_DISPLAY_VALUES`; `_CoOccur` caps its
two maps the same way. Unbounded growth here would be an ingest-path memory leak driven by trap
content, which is to say by anything on the network.

The constants below travel with the classes whose semantics they define. Four of them —
`ENTITY_MIN_DISTINCT`, `ENTITY_MAX_CARD_RATIO`, `FD_MIN_PAIRS` and `value_hash` — are also used by
`VarbindProfiler`, which imports them back. That direction is forced rather than chosen: leaving
them in `varbind_profile.py` would make this module import from the module that imports it
(DECISIONS #97). `varbind_profile` re-exports every name here, so
`varbind_profile.MAX_DISPLAY_CHARS` (used by `severity.py`) and
`varbind_profile.ENTITY_PROMOTE_SCORE` (used by `tests/test_promotion.py`) keep resolving.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

ENTITY_PROMOTE_SCORE = 0.60  # minimum S_entity to promote a discriminator
ENTITY_PROMOTE_OBS = 200  # minimum observations of the varbind on the NE
ENTITY_MIN_DISTINCT = 2  # a discriminator must take at least two values
ENTITY_MAX_CARD_RATIO = 0.50  # reject per-trap-unique values (timestamps, serials)
MAX_TRACKED_VALUES = 2048  # value-frequency cap per accumulator (then count-only)
MAX_DISPLAY_VALUES = 16  # keep readable values only while distinct is small (severity, S8)
MAX_DISPLAY_CHARS = 32  # truncate each retained display value (bounds hostile strings)
FD_THRESHOLD = 0.95  # functional-dependency fraction for a parent->child edge (S6)
FD_MIN_PAIRS = 100  # co-observations required before an FD edge is trusted (S6)


def value_hash(value: str) -> str:
    """8-byte digest of a varbind value — bounded, and keeps hostile strings out of the
    hot dictionary (the displayable value is taken from the live trap only at promotion)."""
    return hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()[:16]


def _as_int(value: str) -> int | None:
    try:
        return int(value.strip())
    except (ValueError, AttributeError):
        return None


@dataclass
class Accumulator:
    """Bounded statistics for one ``(ne_id, class_id, varbind_oid)``."""

    n_obs: int = 0
    n_repeat: int = 0
    n_monotonic: int = 0
    n_numeric: int = 0
    values: dict[str, int] = field(default_factory=dict)  # value_hash -> count, capped
    display: dict[str, str] = field(default_factory=dict)  # value_hash -> readable value (S8)
    display_capped: bool = False  # too many distinct values to keep readable ones (bounded)
    _last_numeric: int | None = None
    updated_at: float = 0.0

    def observe(self, value: str, now: float) -> None:
        self.n_obs += 1
        self.updated_at = now
        vh = value_hash(value)
        if vh in self.values:
            self.values[vh] += 1
            self.n_repeat += 1
        elif len(self.values) < MAX_TRACKED_VALUES:
            self.values[vh] = 1
        self._observe_display(vh, value)
        # once full: neither add the key nor count a repeat — bounded, and honest
        num = _as_int(value)
        if num is not None:
            self.n_numeric += 1
            if self._last_numeric is not None and num > self._last_numeric:
                self.n_monotonic += 1
            self._last_numeric = num
        else:
            self._last_numeric = None  # a non-numeric value breaks the monotonic run

    def _observe_display(self, vh: str, value: str) -> None:
        """Keep the readable value for a low-cardinality varbind so the severity test (S8) can
        see it. A high-cardinality varbind trips the cap and forgets them — bounded memory,
        and it is not a severity field anyway. Hostile strings are truncated on the way in."""
        if self.display_capped or vh in self.display:
            return
        if len(self.display) >= MAX_DISPLAY_VALUES:
            self.display_capped = True
            self.display.clear()
            return
        self.display[vh] = value[:MAX_DISPLAY_CHARS]

    @property
    def n_distinct(self) -> int:
        return len(self.values)


@dataclass(frozen=True)
class Candidate:
    """A scored entity-discriminator candidate for one ``(ne_id, varbind_oid)``."""

    varbind_oid: str
    r: float
    x: float
    d: float
    score: float
    n_obs: int
    n_distinct: int

    def meets_floor(self) -> bool:
        """Every promotion condition except the runner-up margin (checked by the profiler)."""
        return (
            self.score >= ENTITY_PROMOTE_SCORE
            and self.n_obs >= ENTITY_PROMOTE_OBS
            and self.n_distinct >= ENTITY_MIN_DISTINCT
            and self.n_distinct <= ENTITY_MAX_CARD_RATIO * self.n_obs
        )


@dataclass
class _CoOccur:
    """Compact functional-dependency evidence for a canonical varbind pair (oid_x < oid_y):
    the first partner value seen for each value, and the set that later contradicted it. A
    value with no contradiction functionally determines its partner."""

    n_co: int = 0
    x_to_y: dict[str, str] = field(default_factory=dict)
    y_to_x: dict[str, str] = field(default_factory=dict)
    x_bad: set[str] = field(default_factory=set)  # x-values seen with more than one y
    y_bad: set[str] = field(default_factory=set)

    def observe(self, xh: str, yh: str) -> None:
        self.n_co += 1
        seen_y = self.x_to_y.get(xh)
        if seen_y is None and len(self.x_to_y) < MAX_TRACKED_VALUES:
            self.x_to_y[xh] = yh
        elif seen_y is not None and seen_y != yh:
            self.x_bad.add(xh)
        seen_x = self.y_to_x.get(yh)
        if seen_x is None and len(self.y_to_x) < MAX_TRACKED_VALUES:
            self.y_to_x[yh] = xh
        elif seen_x is not None and seen_x != xh:
            self.y_bad.add(yh)

    def determines(self, child_is_x: bool) -> bool:
        """Does the child (x if child_is_x else y) functionally determine the other?"""
        tracked, bad = (self.x_to_y, self.x_bad) if child_is_x else (self.y_to_x, self.y_bad)
        if self.n_co < FD_MIN_PAIRS or not tracked:
            return False
        return (len(tracked) - len(bad)) / len(tracked) >= FD_THRESHOLD
