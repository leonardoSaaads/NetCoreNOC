"""Raise/clear pairing, learned from strict alternation — the two learners that do it.

Split out of `learn.py` in v0.18.0, at the 400-line module guard: the F134 repair took that file
to 457 lines and the guard asked for a seam rather than an allowlist entry.

**The seam is a subject, not a size.** `learn.py` answers *"how related are these two things?"* —
the affinity matrices, normalised PMI, exponential forgetting, storm damping. This file answers a
different question: *"which alarm class turns this one OFF?"* Both learn from the stream and
neither reads the other; `ClearPairLearner` works on the sequence of classes on one
`(device, instance)`, `StateClearLearner` on the sequence of values of one varbind. Nothing here
touches a matrix and nothing in `learn.py` touches an alternation.

Every name is re-exported from `netcorenoc.engine.correlate.learn`, so every existing importer —
the engine, the tests, the tools — is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from netcorenoc.ingest import known_oids
from netcorenoc.store import EdgeRow

#: Full X→Y alternations before a clear pair is trusted.
CLEAR_CYCLES_TO_LEARN = 2
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

    def known_role(self, class_id: int) -> str | None:
        """``"raise"``, ``"clear"`` or None — the role this class already holds, if any.

        **The question the guard has to ask, and did not** (v0.18.0, F134). Until this release
        `register` asked only *"is this class already a raise?"* and *"is that one already a
        clear?"*, so it could not see the case where the two are already a pair **the other way
        round** — and registering the inverse is what makes a raise trap dispatch as a clear.
        """
        if class_id in self.raise_to_clear:
            return "raise"
        if class_id in self.clear_to_raise:
            return "clear"
        return None

    def register(self, raise_class: int, clear_class: int) -> None:
        """Record that `clear_class` clears `raise_class`, unless a class is already spoken for.

        **A class holds exactly one role, for good** — this is what F134 turned out to require.
        A pair is a statement about which of two classes turns an alarm ON, and the appliance
        cannot hold both answers: `clear_to_raise` is consulted on the ingest path *before*
        anything else, so a class present there is never a raise again, whatever the rest of the
        state says. Registering the inverse of a known pair therefore does not add information,
        it **silently deletes** the alarm the raise would have created.
        """
        if self.known_role(raise_class) is not None or self.known_role(clear_class) is not None:
            return
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
            # **Either class in either role** (F134). This asked `first in raise_to_clear or
            # second in clear_to_raise` — the two halves of the orientation it was about to
            # write — so an alternation that happened to begin with the CLEAR class saw a pair
            # it already knew as unknown, and registered it backwards.
            known = self.known_role(alt.first) is not None or (
                alt.second is not None and self.known_role(alt.second) is not None
            )
            if alt.cycles >= CLEAR_CYCLES_TO_LEARN and not known and alt.second is not None:
                self.register(alt.first, alt.second)

    def flush(self) -> list[EdgeRow]:
        rows = [
            EdgeRow("clear_pair", raise_c, clear_c, 1.0, 1.0, 0)
            for raise_c, clear_c in sorted(self.dirty)
        ]
        self.dirty.clear()
        return rows

    def load(self, edges: list[EdgeRow]) -> tuple[int, ...]:
        """Adopt the persisted pairs, **dropping any class the stored rows contradict** (F134).

        A database written before this release can carry both `(X, Y)` and `(Y, X)`: the defect
        was durable, so fixing the learner alone would leave an appliance that reloads the
        corruption on every restart. Assigning both would restore exactly the state that makes a
        raise trap dispatch as a clear.

        **Contradictory evidence is dropped rather than resolved**, and that is the conservative
        direction: a dropped pair means the two classes are ordinary alarms again — the raise is
        visible, the clear raises one of its own — until `CLEAR_PAIR_SEEDS` re-registers the
        standard orientation or two clean alternations re-learn it. Picking a winner would mean
        guessing which of two contradictory rows was right, and guessing wrong reinstates the
        defect silently. Returns the classes it refused, so a caller can tell the operator.
        """
        first: dict[int, int] = {}
        contradicted: set[int] = set()
        # Sorted so two processes reading the same table reach the same conclusion.
        for edge in sorted(edges, key=lambda e: (e.a_id, e.b_id)):
            if edge.b_id in first and first[edge.b_id] == edge.a_id:
                # (a → b) and (b → a) both stored: each class claims both roles.
                contradicted.update({edge.a_id, edge.b_id})
            first.setdefault(edge.a_id, edge.b_id)
        for edge in sorted(edges, key=lambda e: (e.a_id, e.b_id)):
            if edge.a_id in contradicted or edge.b_id in contradicted:
                continue
            self.raise_to_clear[edge.a_id] = edge.b_id
            self.clear_to_raise[edge.b_id] = edge.a_id
        return tuple(sorted(contradicted))


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
