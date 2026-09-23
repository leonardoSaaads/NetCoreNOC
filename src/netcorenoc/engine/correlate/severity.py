"""Placing a severity on a trap, and learning one when the trap does not carry it (S8, §5.3).

**Two sources, and the standard one wins** (v0.21.0, D5, ADR #365). :func:`place` is the decision
the ingest path makes on every trap:

1. **The trap's own word**, read in the X.733 vocabulary — provenance ``standard``. This requires
   no learning at all: an IETF-registered perceived-severity column carries a word the ITU
   standardised, and reading it for what the standard says it is is not an inference.
2. **A learned severity field**, when the NE has one confirmed by the two tests below — provenance
   ``learned``.
3. **Nothing.** Honestly unplaced, never a default.

Until v0.21.0 the ingest path knew only (2), so `alarm.severity` was NULL in every row of a lab
that had been running for fourteen cut/repair cycles while 29 of its 30 alarms carried a severity
word. v0.17.1 had added the standard read to the **census** — the read model — which made the
Overview's numbers right and left every row unplaced. D4's severity rule runs at ingest, where
there is no census, so the rule would have admitted nothing on any estate.

A varbind is treated as a **learned** severity only when two independent tests agree:

1. **Shape** (from the profiler's bounded accumulators): a small ordinal range seen across
   at least two alarm classes, whose values are either integers or members of the bundled
   ``known_oids.SEVERITY_VOCAB`` — public data that supplies a *candidate* ranking, never an
   assumed one. The entity discriminator is excluded: an identifier is not a severity.
2. **Ordinality** (from observed alarm lifetimes): grouping recent closed alarms by the
   varbind's value, the median lifetimes must be monotonic in the candidate rank and actually
   spread — i.e. the values genuinely stratify how long alarms live. If lifetimes cannot
   confirm the ordering, the field stays **unknown**: a fabricated severity is worse than none.

This module judges, and — since v0.21.0 — it also **decides**, in :func:`place`. The engine calls
that one function per trap and writes what it returns; the store never assumes a default.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from itertools import pairwise

from netcorenoc.engine.correlate.varbind_profile import MAX_DISPLAY_CHARS, VarbindProfiler
from netcorenoc.ingest import known_oids
from netcorenoc.ingest.events import Varbind

SEVERITY_MAX_DISTINCT = 8  # a severity field has a small range; more is an identifier or a count
SEVERITY_MIN_OBS = 200  # observations of the varbind on the NE before it can be confirmed
SEVERITY_MIN_CLOSED = 50  # closed alarms needed to validate ordinality against lifetimes
SEVERITY_MIN_PER_VALUE = 5  # closed alarms per value before its median lifetime is trusted


#: The provenance of a placed severity, in precedence order. `declared` is not here: an operator's
#: declaration is a row of `label` against an alarm CLASS and is resolved at read time, where one
#: row moves every active alarm of that class at once (#338).
STANDARD = "standard"
LEARNED = "learned"


@dataclass(frozen=True)
class Placement:
    """What the appliance decided about one trap's severity, and where it got it.

    `source is None` is the honest unknown: the trap carried no word in the X.733 vocabulary and
    the NE has no confirmed severity field. It is counted as **unplaced** and rendered as such —
    never as a default band, and never as `indeterminate`, which is a severity a device can
    legitimately *assert* and therefore cannot double as "we do not know".
    """

    value: str | None = None
    rank: int | None = None
    source: str | None = None

    @property
    def placed(self) -> bool:
        return self.source is not None


#: The answer for a trap nothing can place. Shared because it is immutable and, on an estate whose
#: equipment does not implement ALARM-MIB, it is every trap.
UNPLACED = Placement()


def place(varbinds: list[Varbind], learned_oid: str | None) -> Placement:
    """**THE** per-trap severity decision: the trap's own word first, then the learned field.

    Called once per trap from the ingest path, so it does no I/O and allocates nothing beyond the
    result. The precedence is the standard read first, and that ordering is the whole of D5:

    * **The trap said so.** An IETF-registered perceived-severity column carries a word the ITU
      standardised, and believing it requires no evidence this appliance has to gather. Keyed on
      the **value** rather than on the OID, for the reason `known_oids.standard_severity` gives at
      length: the registries are unreachable from this build environment, and a value-keyed read
      works at whatever OID a vendor chose — which an OID table would miss.
    * **Otherwise, what the NE taught us.** The learned field needs 200 observations and 50 closed
      alarms to confirm, which is minutes to days of traffic; an estate is not unplaced for that
      whole period any more.
    * **Otherwise nothing**, and `unplaced` stays a first-class, visible count.

    The false positive the standard read admits is real, bounded and unchanged from v0.17.1: a
    varbind whose value happens to be `minor` for an unrelated reason is read as a severity. The
    provenance says `standard`, the console says so, and an operator's declaration outranks it — so
    the recourse is one gesture. The alternative is what every release before this one shipped:
    nothing placed on any row, ever.
    """
    standard = known_oids.standard_severity([{"oid": vb.oid, "value": vb.value} for vb in varbinds])
    if standard is not None:
        return Placement(value=standard[0], rank=standard[1], source=STANDARD)
    if learned_oid is not None:
        value = next((vb.value for vb in varbinds if vb.oid == learned_oid), None)
        if value is not None:
            token, rank = normalize(value)
            if token is not None:
                return Placement(value=token, rank=rank, source=LEARNED)
    return UNPLACED


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
