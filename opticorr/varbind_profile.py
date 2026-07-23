"""The varbind profiler: which varbind identifies the alarmed entity and which contains it,
read off one set of bounded counters. Runs in memory in the engine under the batch lock and
is flushed by ``maintenance()`` (prime directive 2); every accumulator is capped and evictable.

A discriminator is scored by three explainable terms, ``S_entity = 0.35*R + 0.45*X + 0.20*D``:
``R`` = repeat rate (an index recurs, a timestamp never does), ``X`` = mean cross-class Jaccard
(the decisive term — only an entity id makes the *same* value appear under different alarm
classes on one NE, e.g. ONU 42 in both loss-of-signal and dying-gasp), ``D`` = not a counter.
Containment is the functional-dependency test (S6). The predicate is evaluated here and acted
on by the engine (S5/S6) — this module never creates an entity. See docs/DESIGN.md.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace

from opticorr import known_oids

# -- constants (one block, one line each) -------------------------------------------------
W_R, W_X, W_D = 0.35, 0.45, 0.20  # entity-score term weights (X decisive)
ENTITY_PROMOTE_SCORE = 0.60  # minimum S_entity to promote a discriminator
ENTITY_PROMOTE_OBS = 200  # minimum observations of the varbind on the NE
ENTITY_MIN_DISTINCT = 2  # a discriminator must take at least two values
ENTITY_MAX_CARD_RATIO = 0.50  # reject per-trap-unique values (timestamps, serials)
ENTITY_MARGIN = 1.25  # winner must beat the runner-up by this factor
MAX_TRACKED_VALUES = 2048  # value-frequency cap per accumulator (then count-only)
MAX_DISPLAY_VALUES = 16  # keep readable values only while distinct is small (severity, S8)
MAX_DISPLAY_CHARS = 32  # truncate each retained display value (bounds hostile strings)
MAX_TRACKED_VARBINDS_PER_CLASS = 32  # distinct varbind OIDs per (NE, class); LRU by n_obs
MAX_ENTITIES_PER_NE = 10_000  # cardinality-DoS cap, enforced by the engine (S5/§6)
FD_THRESHOLD = 0.95  # functional-dependency fraction for a parent->child edge (S6)
FD_MIN_PAIRS = 100  # co-observations required before an FD edge is trusted (S6)
MAX_ENTITY_LEVEL = 3  # containment depth cap (S6)
MAX_COOCCUR_PAIRS = 4096  # cap on tracked varbind-pairs (bounds FD memory)

# Standard framing varbinds are never entity discriminators — skip them (bounds memory too).
_SKIP_OIDS = frozenset(
    {
        known_oids.SYS_UPTIME_OID,
        known_oids.SNMP_TRAP_OID,
        "1.3.6.1.6.3.1.1.4.3.0",  # snmpTrapEnterprise
        "1.3.6.1.6.3.18.1.3.0",  # snmpTrapAddress (the v1 agent address)
    }
)


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


Key = tuple[int, int, str]  # (ne_id, class_id, varbind_oid)
# ne_id, class_id, oid, n_obs, n_repeat, n_monotonic, n_numeric, n_distinct, score, role, updated_at
ProfileRow = tuple[int, int, str, int, int, int, int, int, float, str | None, float]


class VarbindProfiler:
    """In-memory accumulators plus scoring; persistence and pruning are driven by the store."""

    def __init__(self) -> None:
        self.profiles: dict[Key, Accumulator] = {}
        self.roles: dict[tuple[int, str], str] = {}  # (ne_id, varbind_oid) -> entity|severity|state
        self.cooccur: dict[tuple[int, str, str], _CoOccur] = {}  # (ne, oid_x<oid_y) -> FD evidence

    def set_role(self, ne_id: int, varbind_oid: str, role: str) -> None:
        self.roles[(ne_id, varbind_oid)] = role

    def role_of(self, ne_id: int, varbind_oid: str) -> str | None:
        return self.roles.get((ne_id, varbind_oid))

    def clear_roles(self, ne_id: int) -> None:
        """Forget an NE's learned roles (entity/severity), keeping the evidence (S11 reset)."""
        for key in [k for k in self.roles if k[0] == ne_id]:
            del self.roles[key]

    def drop_ne(self, ne_id: int) -> None:
        """Drop all in-memory profiler state for an NE — accumulators, co-occurrence, and
        roles — so identity/severity re-measure from scratch (S11 profile reset)."""
        for pkey in [k for k in self.profiles if k[0] == ne_id]:
            del self.profiles[pkey]
        for ckey in [k for k in self.cooccur if k[0] == ne_id]:
            del self.cooccur[ckey]
        self.clear_roles(ne_id)

    # -- observation (engine, in memory, under the batch lock) ---------------------------

    def observe(
        self, ne_id: int, class_id: int, varbinds: list[tuple[str, str]], now: float
    ) -> None:
        present: list[tuple[str, str]] = []  # (oid, value_hash) for FD co-occurrence
        for oid, value in varbinds:
            if oid in _SKIP_OIDS:
                continue
            key = (ne_id, class_id, oid)
            accum = self.profiles.get(key)
            if accum is None:
                self._enforce_varbind_cap(ne_id, class_id)
                accum = Accumulator()
                self.profiles[key] = accum
            accum.observe(value, now)
            present.append((oid, value_hash(value)))
        self._observe_cooccurrence(ne_id, present)

    def _observe_cooccurrence(self, ne_id: int, present: list[tuple[str, str]]) -> None:
        """Record value-pair co-occurrence for the functional-dependency test (S6)."""
        for i in range(len(present)):
            for j in range(i + 1, len(present)):
                (oa, ha), (ob, hb) = present[i], present[j]
                if oa == ob:
                    continue
                key = (ne_id, oa, ob) if oa < ob else (ne_id, ob, oa)
                xh, yh = (ha, hb) if oa < ob else (hb, ha)
                co = self.cooccur.get(key)
                if co is None:
                    if len(self.cooccur) >= MAX_COOCCUR_PAIRS:
                        continue  # bounded: stop tracking new pairs when full
                    co = self.cooccur[key] = _CoOccur()
                co.observe(xh, yh)

    def fd_parent(self, ne_id: int, parent_oid: str, child_oid: str) -> bool:
        """True if child_oid functionally determines parent_oid (each child value maps to one
        parent value) over enough co-observations — i.e. parent_oid contains child_oid (S6)."""
        if parent_oid == child_oid:
            return False
        lo, hi = sorted((parent_oid, child_oid))
        co = self.cooccur.get((ne_id, lo, hi))
        return co is not None and co.determines(child_is_x=child_oid < parent_oid)

    def _emerging_finer_child(self, ne_id: int, cand: Candidate) -> bool:
        """Is a finer varbind that ``cand`` functionally contains still accumulating evidence?
        If so, promoting ``cand`` now would lock in a coarse parent and lose the child (S6)."""
        for other in self.candidates(ne_id):
            if (
                other.n_distinct > cand.n_distinct
                and other.n_distinct >= ENTITY_MIN_DISTINCT
                and other.n_obs >= FD_MIN_PAIRS
                and other.n_distinct <= ENTITY_MAX_CARD_RATIO * other.n_obs
                and self.fd_parent(ne_id, cand.varbind_oid, other.varbind_oid)
            ):
                return True
        return False

    def promotion_chain(self, ne_id: int) -> list[Candidate] | None:
        """The discriminator chain to promote (coarsest→finest), or None to wait. The finest
        floor-passing candidate is the entity; coarser candidates that functionally contain it
        become its parents (capped at MAX_ENTITY_LEVEL). Deferred while a finer FD-child is still
        emerging, or an unrelated candidate is within the 1.25x margin — an early wrong
        promotion costs trust (§13)."""
        valid = sorted(
            (c for c in self.candidates(ne_id) if c.meets_floor()), key=lambda c: c.n_distinct
        )
        if not valid:
            return None
        finest = valid[-1]
        if self._emerging_finer_child(ne_id, finest):
            return None
        chain = [finest]
        for coarser in reversed(valid[:-1]):
            if len(chain) >= MAX_ENTITY_LEVEL:
                break
            if self.fd_parent(ne_id, coarser.varbind_oid, chain[0].varbind_oid):
                chain.insert(0, coarser)
        chain_oids = {c.varbind_oid for c in chain}
        unrelated = [c for c in valid if c.varbind_oid not in chain_oids]
        if unrelated and finest.score < ENTITY_MARGIN * max(c.score for c in unrelated):
            return None  # a genuinely ambiguous, non-contained tie: wait for more evidence
        return chain

    def _enforce_varbind_cap(self, ne_id: int, class_id: int) -> None:
        """MAX_TRACKED_VARBINDS_PER_CLASS with LRU-by-n_obs eviction (§6 profiler DoS)."""
        keys = [k for k in self.profiles if k[0] == ne_id and k[1] == class_id]
        if len(keys) < MAX_TRACKED_VARBINDS_PER_CLASS:
            return
        victim = min(keys, key=lambda k: self.profiles[k].n_obs)
        del self.profiles[victim]

    # -- scoring -------------------------------------------------------------------------

    def _accums_for(self, ne_id: int, varbind_oid: str) -> list[Accumulator]:
        return [a for (n, _c, o), a in self.profiles.items() if n == ne_id and o == varbind_oid]

    def class_count(self, ne_id: int, varbind_oid: str) -> int:
        """Distinct alarm classes the varbind was seen under (severity is cross-class, S8)."""
        return len({c for (n, c, o) in self.profiles if n == ne_id and o == varbind_oid})

    def display_values(self, ne_id: int, varbind_oid: str) -> dict[str, int] | None:
        """Readable value -> count across classes, or None if any accumulator exceeded the
        display cap (too high-cardinality to be a severity field). Used only by S8."""
        accums = self._accums_for(ne_id, varbind_oid)
        if not accums:
            return None
        merged: dict[str, int] = {}
        for accum in accums:
            if accum.display_capped:
                return None
            for vh, value in accum.display.items():
                merged[value] = merged.get(value, 0) + accum.values.get(vh, 0)
        return merged or None

    def _cross_class_support(self, accums: list[Accumulator]) -> float:
        """Mean pairwise Jaccard of the tracked value sets across classes; 0 if < 2 classes."""
        sets = [set(a.values) for a in accums if a.values]
        if len(sets) < 2:
            return 0.0
        total, pairs = 0.0, 0
        for i in range(len(sets)):
            for j in range(i + 1, len(sets)):
                union = sets[i] | sets[j]
                if union:
                    total += len(sets[i] & sets[j]) / len(union)
                pairs += 1
        return total / pairs if pairs else 0.0

    def score(self, ne_id: int, varbind_oid: str) -> Candidate:
        accums = self._accums_for(ne_id, varbind_oid)
        n_obs = sum(a.n_obs for a in accums)
        n_repeat = sum(a.n_repeat for a in accums)
        n_monotonic = sum(a.n_monotonic for a in accums)
        n_numeric = sum(a.n_numeric for a in accums)
        distinct = len({vh for a in accums for vh in a.values})
        r = n_repeat / n_obs if n_obs else 0.0
        x = self._cross_class_support(accums)
        d = 1.0 - n_monotonic / max(1, n_numeric)
        score = W_R * r + W_X * x + W_D * d
        return Candidate(varbind_oid, r, x, d, score, n_obs, distinct)

    def candidates(self, ne_id: int) -> list[Candidate]:
        """Every varbind seen on the NE, scored, best first (for inspection and promotion)."""
        oids = {o for (n, _c, o) in self.profiles if n == ne_id}
        scored = [self.score(ne_id, oid) for oid in oids]
        return sorted(scored, key=lambda c: c.score, reverse=True)

    def best_promotable(self, ne_id: int) -> Candidate | None:
        """The winner if it clears every floor *and* beats the runner-up by ENTITY_MARGIN,
        else None (wait for more evidence). Observe-only in S4; the engine acts on it in S5.

        The winner and the runner-up are both taken from candidates that pass the floor: a
        constant (distinct 1) or a timestamp (unique per trap) scores high but is not a
        competing entity discriminator — the floor gates exist precisely to exclude them, so
        they neither win nor block a genuine discriminator.
        """
        valid = [c for c in self.candidates(ne_id) if c.meets_floor()]
        if not valid:
            return None
        if len(valid) > 1 and valid[0].score < ENTITY_MARGIN * max(valid[1].score, 1e-9):
            return None
        return valid[0]

    # -- persistence (flushed and pruned by maintenance) ---------------------------------

    def flush_rows(self, now: float) -> list[ProfileRow]:
        """One row per accumulator for the varbind_profile table; the score is the NE-level
        entity-candidate score for the varbind (denormalised across its class rows)."""
        by_varbind: dict[tuple[int, str], float] = {}
        rows: list[ProfileRow] = []
        for (ne_id, class_id, oid), accum in self.profiles.items():
            score = by_varbind.get((ne_id, oid))
            if score is None:
                score = by_varbind[(ne_id, oid)] = self.score(ne_id, oid).score
            rows.append(
                (
                    ne_id,
                    class_id,
                    oid,
                    accum.n_obs,
                    accum.n_repeat,
                    accum.n_monotonic,
                    accum.n_numeric,
                    accum.n_distinct,
                    score,
                    self.roles.get((ne_id, oid)),
                    accum.updated_at,
                )
            )
        return rows

    def prune_stale(self, cutoff: float) -> int:
        """Drop accumulators untouched since ``cutoff`` (maintenance-side, bounds memory)."""
        stale = [k for k, a in self.profiles.items() if a.updated_at < cutoff]
        for key in stale:
            del self.profiles[key]
        return len(stale)

    def load_row(
        self,
        ne_id: int,
        class_id: int,
        oid: str,
        n_obs: int,
        n_repeat: int,
        n_monotonic: int,
        n_numeric: int,
        updated_at: float,
    ) -> None:
        """Restore an accumulator's summary counts after a restart. Value sets are rebuilt
        from live traffic (they are not persisted), so X recovers conservatively — a late
        promotion is cheaper than an early wrong one."""
        self.profiles[(ne_id, class_id, oid)] = replace(
            Accumulator(),
            n_obs=n_obs,
            n_repeat=n_repeat,
            n_monotonic=n_monotonic,
            n_numeric=n_numeric,
            updated_at=updated_at,
        )
