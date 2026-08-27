"""Learned severity with an honest fallback (S8, §5.3).

A varbind is treated as severity only when two independent tests agree:

1. **Shape** (from the profiler's bounded accumulators): a small ordinal range seen across
   at least two alarm classes, whose values are either integers or members of the bundled
   ``known_oids.SEVERITY_VOCAB`` — public data that supplies a *candidate* ranking, never an
   assumed one. The entity discriminator is excluded: an identifier is not a severity.
2. **Ordinality** (from observed alarm lifetimes): grouping recent closed alarms by the
   varbind's value, the median lifetimes must be monotonic in the candidate rank and actually
   spread — i.e. the values genuinely stratify how long alarms live. If lifetimes cannot
   confirm the ordering, the field stays **unknown**: a fabricated severity is worse than none.

This module only judges. The engine acts on the judgement in the maintenance sweep and sets
``alarm.severity``/``severity_rank`` at ingest; the store never assumes a default.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from itertools import pairwise

from netcorenoc import known_oids
from netcorenoc.engine.correlate.varbind_profile import MAX_DISPLAY_CHARS, VarbindProfiler

SEVERITY_MAX_DISTINCT = 8  # a severity field has a small range; more is an identifier or a count
SEVERITY_MIN_OBS = 200  # observations of the varbind on the NE before it can be confirmed
SEVERITY_MIN_CLOSED = 50  # closed alarms needed to validate ordinality against lifetimes
SEVERITY_MIN_PER_VALUE = 5  # closed alarms per value before its median lifetime is trusted


@dataclass(frozen=True)
class SeverityCandidate:
    """A varbind proposed as the severity field for one NE, with a candidate ranking."""

    varbind_oid: str
    kind: str  # "vocab" | "int"
    ranks: dict[str, int]  # readable value -> candidate rank (0 = most severe for vocab)
    n_obs: int
    n_classes: int

    def rank_of(self, value: str) -> int | None:
        return self.ranks.get(value[:MAX_DISPLAY_CHARS])


def _candidate_ranks(values: dict[str, int]) -> tuple[str, dict[str, int]] | None:
    """(kind, value->rank) if every observed value is ordinal, else None. Vocab wins over
    integers when a value is both (no bundled token is numeric, so they never collide)."""
    tokens = list(values)
    vocab = {v: known_oids.severity_rank(v) for v in tokens}
    if all(rank is not None for rank in vocab.values()):
        return "vocab", {v: rank for v, rank in vocab.items() if rank is not None}
    ints: dict[str, int] = {}
    for value in tokens:
        try:
            ints[value] = int(value.strip())
        except (ValueError, AttributeError):
            return None
    return "int", ints


def severity_candidate(
    profiler: VarbindProfiler, ne_id: int, entity_oids: set[str]
) -> SeverityCandidate | None:
    """The best severity-shaped varbind on the NE, or None. Shape only — ordinality is
    confirmed separately against lifetimes. Prefers the bundled vocabulary over raw integers,
    then more classes, then fewer distinct values (a tighter ordinal scale)."""
    best: SeverityCandidate | None = None
    best_key: tuple[int, int, int] = (-1, -1, -1)
    seen: set[str] = set()
    for _ne, _cls, oid in list(profiler.profiles):
        if _ne != ne_id or oid in seen or oid in entity_oids:
            continue
        seen.add(oid)
        values = profiler.display_values(ne_id, oid)
        if values is None or not (2 <= len(values) <= SEVERITY_MAX_DISTINCT):
            continue
        n_classes = profiler.class_count(ne_id, oid)
        n_obs = profiler.score(ne_id, oid).n_obs
        if n_classes < 2 or n_obs < SEVERITY_MIN_OBS:
            continue
        ranked = _candidate_ranks(values)
        if ranked is None:
            continue
        kind, ranks = ranked
        key = (1 if kind == "vocab" else 0, n_classes, -len(ranks))
        if key > best_key:
            best_key = key
            best = SeverityCandidate(oid, kind, ranks, n_obs, n_classes)
    return best


def confirm_ordinality(cand: SeverityCandidate, samples: list[tuple[str, float]]) -> bool:
    """True when the candidate ranking is borne out by closed-alarm lifetimes: enough closed
    samples, at least two values with a stable median, medians monotonic in rank, and a real
    spread. Direction is not assumed — a severity may clear faster or slower; what matters is
    that the values form a genuinely ordered axis, not noise."""
    by_value: dict[str, list[float]] = {}
    for value, lifetime in samples:
        rank = cand.rank_of(value)
        if rank is not None and lifetime >= 0.0:
            by_value.setdefault(value, []).append(lifetime)
    if sum(len(lts) for lts in by_value.values()) < SEVERITY_MIN_CLOSED:
        return False
    medians = sorted(
        (cand.ranks[v], statistics.median(lts))
        for v, lts in by_value.items()
        if len(lts) >= SEVERITY_MIN_PER_VALUE
    )
    if len(medians) < 2:
        return False
    ys = [m for _rank, m in medians]
    if len({round(y, 3) for y in ys}) < 2:
        return False  # no spread: the values do not stratify lifetime at all
    non_dec = all(a <= b for a, b in pairwise(ys))
    non_inc = all(a >= b for a, b in pairwise(ys))
    return non_dec or non_inc


def normalize(value: str) -> tuple[str | None, int | None]:
    """(display, rank) for a trap value on a confirmed severity field, or (None, None) when the
    value is not ordinal — honestly *unknown*, never a default. Vocab tokens rank 0 (critical)
    to 4; a raw integer is its own rank. A confirmed field's values are homogeneous, so this
    reconstructs the rank from the value alone — no per-NE state to persist or reload (S8)."""
    token = value.strip()
    vocab_rank = known_oids.severity_rank(token)
    if vocab_rank is not None:
        return token.lower(), vocab_rank
    try:
        return token, int(token)
    except ValueError:
        return None, None
