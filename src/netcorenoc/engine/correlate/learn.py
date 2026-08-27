"""Incremental co-occurrence learning: matrices A (classxclass) and E (devicexdevice).

Affinity is normalized PMI over co-occurrence masses. Forgetting is exponential per learning epoch
(an epoch is a closed situation): every stored mass decays lazily by (1-λ)^Δepochs the next time it
is touched, so forgetting is O(1) per update with no matrix sweeps. Updates during mass storms are
damped 10x so confounders (e.g. a regional power outage) are not learned as structure. Operator
feedback flows back in: ``confirm`` re-applies a situation's pairwise updates; ``split`` halves them
— every pair, or **only the ones asserted** when v0.9.1's exclusion set names which do not belong.

Raise/clear pairs are learned from strict alternation of two classes on one (device, instance),
seeded with the universal standard pairs (linkDown → linkUp).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any

from netcorenoc.ingest import known_oids
from netcorenoc.store import EdgeRow, Store

LAMBDA = 0.05  # forgetting factor per learning epoch (closed situation)
MIN_EDGE_N = 5.0  # co-occurrence mass before an E edge is trusted
SAME_NE_AFFINITY = 0.8  # affinity between two distinct entities on the same NE (§5.5)
STORM_DAMPING = 0.1  # 10x smaller updates during mass storms
STORM_ALARMS = 50  # window/situation occupancy that defines a storm
SPLIT_PENALTY = 0.5  # pair-mass multiplier applied by a "split" feedback
CLEAR_CYCLES_TO_LEARN = 2  # full X→Y alternations before a clear pair is trusted
EPOCH_PAIR_CAP = 20  # members sampled when a situation reinforces the matrices
MAX_STATE_SLOTS = 4096  # bounded per-(device, instance, class, oid) alternation trackers (S9)
STATE_MAX_VALUE_CHARS = 32  # truncate tracked state values (bounds hostile strings)

# Framing varbinds are never state fields; skipping them saves alternation slots (S9).
_STATE_SKIP = frozenset(
    {
        known_oids.SYS_UPTIME_OID,
        known_oids.SNMP_TRAP_OID,
        "1.3.6.1.6.3.1.1.4.3.0",  # snmpTrapEnterprise
        "1.3.6.1.6.3.18.1.3.0",  # snmpTrapAddress (the v1 agent address)
    }
)

Item = tuple[int, int]  # (class_id, device_id)


def _pair(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a <= b else (b, a)


@dataclass
class Matrix:
    """Symmetric co-occurrence mass with lazy exponential forgetting and NPMI scores."""

    kind: str
    lam: float = LAMBDA
    epoch: int = 0
    total: float = 0.0
    total_e: int = 0
    pairs: dict[tuple[int, int], tuple[float, int]] = field(default_factory=dict)
    marginals: dict[int, tuple[float, int]] = field(default_factory=dict)
    dirty: set[tuple[int, int]] = field(default_factory=set)

    def _decayed(self, mass: float, at_epoch: int) -> float:
        return mass * (1.0 - self.lam) ** (self.epoch - at_epoch)

    def tick(self) -> None:
        """Advance one learning epoch; all masses decay lazily against it."""
        self.epoch += 1

    def observe_occurrence(self, item: int, weight: float = 1.0) -> None:
        mass, at_e = self.marginals.get(item, (0.0, self.epoch))
        self.marginals[item] = (self._decayed(mass, at_e) + weight, self.epoch)
        self.total = self._decayed(self.total, self.total_e) + weight
        self.total_e = self.epoch

    def observe_pair(self, a: int, b: int, weight: float = 1.0) -> None:
        key = _pair(a, b)
        mass, at_e = self.pairs.get(key, (0.0, self.epoch))
        self.pairs[key] = (self._decayed(mass, at_e) + weight, self.epoch)
        self.dirty.add(key)

    def pair_mass(self, a: int, b: int) -> float:
        mass, at_e = self.pairs.get(_pair(a, b), (0.0, self.epoch))
        return self._decayed(mass, at_e)

    def scale_pair(self, a: int, b: int, factor: float) -> None:
        key = _pair(a, b)
        if key in self.pairs:
            mass, at_e = self.pairs[key]
            self.pairs[key] = (mass * factor, at_e)
            self.dirty.add(key)

    def npmi(self, a: int, b: int) -> float:
        """Normalized PMI in [0, 1], discounted by evidence; 0 when unseen or negative.

        Probabilities are rates against the decayed activation total. A single
        co-occurrence is never proof: the score is shrunk by n/(n+1) so association
        must be earned by repetition.
        """
        m_ab = self.pair_mass(a, b)
        total = self._decayed(self.total, self.total_e)
        if m_ab <= 0.0 or total <= 0.0:
            return 0.0
        m_a, e_a = self.marginals.get(a, (0.0, self.epoch))
        m_b, e_b = self.marginals.get(b, (0.0, self.epoch))
        m_a, m_b = self._decayed(m_a, e_a), self._decayed(m_b, e_b)
        if m_a <= 0.0 or m_b <= 0.0:
            return 0.0
        discount = m_ab / (m_ab + 1.0)
        p_ab = min(m_ab / total, 1.0)
        if p_ab >= 1.0 - 1e-9:
            return discount
        p_a, p_b = min(m_a / total, 1.0), min(m_b / total, 1.0)
        pmi = math.log(p_ab / (p_a * p_b))
        return max(0.0, min(1.0, pmi / -math.log(p_ab))) * discount

    def flush(self) -> list[EdgeRow]:
        """Drain dirty pairs as EdgeRows (weight = NPMI, n = decayed mass, g = epoch)."""
        rows = [
            EdgeRow(self.kind, a, b, self.npmi(a, b), self.pair_mass(a, b), self.epoch)
            for a, b in sorted(self.dirty)
        ]
        self.dirty.clear()
        return rows

    def state(self) -> str:
        return json.dumps(
            {
                "epoch": self.epoch,
                "total": [self.total, self.total_e],
                "marginals": [[k, m, e] for k, (m, e) in self.marginals.items()],
            }
        )

    def load_state(self, raw: str, edges: list[EdgeRow]) -> None:
        data = json.loads(raw)
        self.epoch = int(data["epoch"])
        self.total, self.total_e = float(data["total"][0]), int(data["total"][1])
        self.marginals = {int(k): (float(m), int(e)) for k, m, e in data["marginals"]}
        self.pairs = {_pair(e.a_id, e.b_id): (e.n, e.g) for e in edges}


@dataclass
class _Alternation:
    first: int
    second: int | None = None
    last: int = 0
    cycles: int = 0


@dataclass
class ClearPairLearner:
    """Learn raise → clear class pairs from strict alternation on a (device, instance)."""

    raise_to_clear: dict[int, int] = field(default_factory=dict)
    clear_to_raise: dict[int, int] = field(default_factory=dict)
    state: dict[tuple[int, str], _Alternation] = field(default_factory=dict)
    dirty: set[tuple[int, int]] = field(default_factory=set)

    def register(self, raise_class: int, clear_class: int) -> None:
        if raise_class not in self.raise_to_clear and clear_class not in self.clear_to_raise:
            self.raise_to_clear[raise_class] = clear_class
            self.clear_to_raise[clear_class] = raise_class
            self.dirty.add((raise_class, clear_class))

    def observe(self, device_id: int, instance: str, class_id: int) -> None:
        """Track alternation; duplicates are ignored, a third class restarts tracking."""
        slot = (device_id, instance)
        alt = self.state.get(slot)
        if alt is None:
            self.state[slot] = _Alternation(first=class_id, last=class_id)
            return
        if class_id == alt.last:
            return
        if alt.second is None and class_id != alt.first:
            alt.second = class_id
        if class_id not in (alt.first, alt.second):
            self.state[slot] = _Alternation(first=class_id, last=class_id)
            return
        alt.last = class_id
        if class_id == alt.second:
            alt.cycles += 1
            known = alt.first in self.raise_to_clear or alt.second in self.clear_to_raise
            if alt.cycles >= CLEAR_CYCLES_TO_LEARN and not known and alt.second is not None:
                self.register(alt.first, alt.second)

    def flush(self) -> list[EdgeRow]:
        rows = [
            EdgeRow("clear_pair", raise_c, clear_c, 1.0, 1.0, 0)
            for raise_c, clear_c in sorted(self.dirty)
        ]
        self.dirty.clear()
        return rows

    def load(self, edges: list[EdgeRow]) -> None:
        for edge in edges:
            self.raise_to_clear[edge.a_id] = edge.b_id
            self.clear_to_raise[edge.b_id] = edge.a_id


@dataclass
class _ValueAlternation:
    """Strict two-value alternation of one varbind on one (device, instance, class)."""

    first: str
    second: str | None = None
    last: str = ""
    cycles: int = 0
    poisoned: bool = False  # a third distinct value appeared: not a two-state field


# (class_id, varbind_oid, clear_value, raise_value)
StateRow = tuple[int, str, str, str]


@dataclass
class StateClearLearner:
    """Learn a per-(class, varbind) *state* field and its clear value from strict two-value
    alternation on a (device, instance) — the single-OID analogue of ClearPairLearner (S9).

    A varbind whose value strictly alternates between exactly two values for
    ``CLEAR_CYCLES_TO_LEARN`` full cycles is a state field; the value it returns to (the second
    seen) is the clear value, the first the raise value. The two-value requirement is
    self-selecting: an identifier (many values) or a multi-level severity poisons its slot and
    is never learned. Additive — the class-level learner is untouched, and until a field is
    learned nothing is routed, so cold-start grouping is unchanged.
    """

    clear_value: dict[tuple[int, str], str] = field(default_factory=dict)  # (class,oid)->clear
    raise_value: dict[tuple[int, str], str] = field(default_factory=dict)  # (class,oid)->raise
    slots: dict[tuple[int, str, int, str], _ValueAlternation] = field(default_factory=dict)
    dirty: set[tuple[int, str]] = field(default_factory=set)

    def observe(
        self, device_id: int, instance: str, class_id: int, varbinds: list[tuple[str, str]]
    ) -> None:
        for oid, raw in varbinds:
            if oid in _STATE_SKIP or (class_id, oid) in self.clear_value:
                continue  # framing, or already learned for this class
            value = raw[:STATE_MAX_VALUE_CHARS]
            slot_key = (device_id, instance, class_id, oid)
            alt = self.slots.get(slot_key)
            if alt is None:
                if len(self.slots) < MAX_STATE_SLOTS:
                    self.slots[slot_key] = _ValueAlternation(first=value, last=value)
                continue
            if alt.poisoned or value == alt.last:
                continue
            if alt.second is None and value != alt.first:
                alt.second = value
            if value not in (alt.first, alt.second):
                alt.poisoned = True  # a third value: not a two-state field
                continue
            alt.last = value
            if value == alt.second and alt.second is not None:
                alt.cycles += 1
                if alt.cycles >= CLEAR_CYCLES_TO_LEARN:
                    self.register(class_id, oid, alt.first, alt.second)

    def register(self, class_id: int, oid: str, raise_v: str, clear_v: str) -> None:
        key = (class_id, oid)
        if key not in self.clear_value:
            self.clear_value[key] = clear_v
            self.raise_value[key] = raise_v
            self.dirty.add(key)

    def is_clear(self, class_id: int, varbinds: list[tuple[str, str]]) -> bool:
        """True when a trap of this class carries a learned state varbind at its clear value."""
        for oid, raw in varbinds:
            clear = self.clear_value.get((class_id, oid))
            if clear is not None and raw[:STATE_MAX_VALUE_CHARS] == clear:
                return True
        return False

    def flush(self) -> list[StateRow]:
        rows = [
            (c, o, self.clear_value[(c, o)], self.raise_value[(c, o)])
            for (c, o) in sorted(self.dirty)
        ]
        self.dirty.clear()
        return rows

    def load(self, rows: list[dict[str, Any]]) -> None:
        for row in rows:
            key = (int(row["class_id"]), str(row["varbind_oid"]))
            self.clear_value[key] = str(row["clear_value"])
            self.raise_value[key] = str(row["raise_value"])


class Learner:
    """A + E matrices, clear pairs, feedback, and persistence in one place."""

    def __init__(self) -> None:
        self.A = Matrix("class")
        self.E = Matrix("device")
        self.clears = ClearPairLearner()
        self.states = StateClearLearner()

    def observe_activation(self, item: Item) -> None:
        self.A.observe_occurrence(item[0])
        self.E.observe_occurrence(item[1])

    def observe_pairs(self, new: Item, others: list[Item], storm: bool) -> None:
        """One co-occurrence observation per activation per distinct other class/device,
        so pair mass can never outgrow the activation total. Class pairs include a class
        with itself across devices; device pairs only across distinct devices —
        same-device affinity is 1 by definition, not a statistic."""
        weight = STORM_DAMPING if storm else 1.0
        for other_class in {other[0] for other in others}:
            self.A.observe_pair(new[0], other_class, weight)
        for other_device in {other[1] for other in others if other[1] != new[1]}:
            self.E.observe_pair(new[1], other_device, weight)

    def class_affinity(self, a: int, b: int) -> float:
        return self.A.npmi(a, b)

    def entity_affinity(self, a_entity: int, a_ne: int, b_entity: int, b_ne: int) -> float:
        """Affinity under the entity model (§5.5), kept at NE level:

            same entity                -> 1.0
            same NE, different entity  -> SAME_NE_AFFINITY (0.8)   (intra-NE proximity, structural)
            different NE               -> learned NPMI E[ne_i, ne_j] (n >= 5)

        Before any promotion each NE has exactly one entity, so the 0.8 branch is unreachable
        and this is numerically identical to v0.2.0's device affinity — the parity argument.
        The E matrix is keyed by the NE (1:1 with the device), so learning is unchanged.
        """
        if a_entity == b_entity:
            return 1.0
        if a_ne == b_ne:
            return SAME_NE_AFFINITY
        return self.E.npmi(a_ne, b_ne) if self.E.pair_mass(a_ne, b_ne) >= MIN_EDGE_N else 0.0

    def device_affinity(self, a: int, b: int) -> float:
        """The NE-level affinity between two devices (each 1:1 with its NE) — the level-0
        case of :meth:`entity_affinity` where the entity is the NE itself. Retained for the
        query paths that reason about the learned NE-by-NE matrix directly."""
        return self.entity_affinity(a, a, b, b)

    def learn_epoch(self, members: list[Item], advance_epoch: bool = True) -> None:
        """A closed situation reinforces each distinct pair once and ages the matrices by one epoch.

        `advance_epoch=False` reinforces **without** ticking, for operator feedback (v0.7.1, F36).
        The epoch is the global forgetting clock — every stored mass decays lazily by
        `(1-LAMBDA)^Δepoch` against it — and it belongs to the *correlation lifecycle*, not to an
        operator's opinion about one grouping. v0.7.0 ticked on every `confirm`, so a `POST
        /feedback` loop aged the whole appliance's learned state, for every NE, including NEs the
        caller could not see (DECISIONS #69).
        """
        if advance_epoch:
            self.A.tick()
            self.E.tick()
        sample = members[:EPOCH_PAIR_CAP]
        weight = STORM_DAMPING if len(members) >= STORM_ALARMS else 1.0
        class_pairs = {_pair(a[0], b[0]) for i, a in enumerate(sample) for b in sample[i + 1 :]}
        device_pairs = {
            _pair(a[1], b[1]) for i, a in enumerate(sample) for b in sample[i + 1 :] if a[1] != b[1]
        }
        for a_id, b_id in class_pairs:
            self.A.observe_pair(a_id, b_id, weight)
        for a_id, b_id in device_pairs:
            self.E.observe_pair(a_id, b_id, weight)

    def penalize(self, members: list[Item], marked: frozenset[int] | None = None) -> None:
        """A "split" feedback: halve each distinct pair mass grouped together, once.

        With `marked` (positions the operator said do not belong) only the asserted marked-to-rest
        pairs are halved: a subset of what `marked=None` halves, which is v0.9.0. DECISIONS #125.
        """
        sample = members[:EPOCH_PAIR_CAP]
        asserted = [
            (a, b)
            for i, a in enumerate(sample)
            for j, b in enumerate(sample[i + 1 :], i + 1)
            if marked is None or ((i in marked) != (j in marked))
        ]
        for a_id, b_id in {_pair(a[0], b[0]) for a, b in asserted}:
            self.A.scale_pair(a_id, b_id, SPLIT_PENALTY)
        for a_id, b_id in {_pair(a[1], b[1]) for a, b in asserted if a[1] != b[1]}:
            self.E.scale_pair(a_id, b_id, SPLIT_PENALTY)

    async def save(self, store: Store, ts: float) -> None:
        rows = self.A.flush() + self.E.flush() + self.clears.flush()
        if rows:
            await store.upsert_edges(rows, ts)
        state_rows = self.states.flush()
        if state_rows:
            await store.upsert_state_clears(state_rows, ts)
        await store.set_meta("matrix_class", self.A.state())
        await store.set_meta("matrix_device", self.E.state())

    async def load(self, store: Store) -> None:
        for matrix, key in ((self.A, "matrix_class"), (self.E, "matrix_device")):
            raw = await store.get_meta(key)
            if raw is not None:
                matrix.load_state(raw, await store.load_edges(matrix.kind))
        self.clears.load(await store.load_edges("clear_pair"))
        self.states.load(await store.load_state_clears())
