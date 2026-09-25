"""What still gets through a maintenance window — D4's collection rules, compiled.

**A target under a maintenance window is not collected by default.** That is D4, and the default is
*nothing*: a window with no rule on a target collects no trap from it at all. Rules are the
exceptions an operator writes, and there are three kinds.

## The composition rule, stated once

    rules of DIFFERENT kinds are ANDed;  rules of the SAME kind are ORed.

The maintainer's own example is what settles it (ADR #368). *"On host B, collect traps only from
11:15 to 11:20, and only OIDs under `1.3.6.1.4.1.2011.5.25.31.1.1.1.1`"* — a trap at 11:17 on an
unrelated OID must not pass, so the slot and the subtree are an intersection. Two subtrees on the
same target are plainly a union, because the operator wrote a second one in order to admit more.

## The trap this feature is most likely to fall into

**An OID subtree is matched on arc boundaries, never on string prefixes.** A rule for

    1.3.6.1.4.1.2011.5.25.31.1.1.1.1

must admit `…1.1.1.1.4.2` and must **not** admit `…1.1.1.10`, which a naive `startswith` does —
`"…1.1.1.10".startswith("…1.1.1.1")` is `True`, and the tenth column of a table is not a member of
the first column's subtree. :func:`under_subtree` is the whole of the fix and
`tests/test_maintenance_window.py` runs the injection that proves a prefix match fails it.

## Why nothing here touches the database

Every method below is pure and every input is already in the caller's hand at the moment the trap
is decoded. Part V requires the per-trap check to cost no query, no lock and no I/O, and a rule
that had to look anything up could not satisfy it. See `engine/mw/index.py` for the structure that
holds these and `docs/architecture/DESIGN.md` for where it sits in the batch.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# The arc-boundary predicate lives in the ingest layer since v0.22.0, because the alarm-class
# catalogue needs the same rule one layer down (ADR #384). One implementation, imported by both.
from netcorenoc.ingest.known_oids import under_subtree

#: Where an OID rule looks. **Both, and the rule says which** (ADR #369).
#:
#: `trap` matches `snmpTrapOID.0` — the notification's own identity, which is what an operator
#: means by *"only link traps"*. `varbind` matches any varbind OID the trap carries, which is what
#: they mean when the subtree is a **table column**: the maintainer's example arc,
#: `1.3.6.1.4.1.2011.5.25.31.1.1.1.1`, is shaped like `…Entry.column` under Huawei's
#: `hwEntityMIB`, and its further arcs (`.4.1`, `.4.2`) read as instance suffixes on a column
#: rather than as notification identifiers. So the example is expressed with `varbind`, and the
#: choice is explicit in every stored rule rather than guessed per estate.
MatchOn = Literal["trap", "varbind"]

__all__ = ["MatchOn", "under_subtree"]


@dataclass(frozen=True)
class SeverityRule:
    """*"Collect this target's alarms at or above this severity."*

    `at_or_above_rank` is an X.733 rank, so **smaller is more severe**: 0 is `critical`, 1 is
    `major`. A trap admits when its rank is less than or equal to the threshold — *"critical only"*
    is rank 0 and admits nothing else.

    **A trap with no placed severity never admits.** That is not a technicality, it is the reason
    D5 had to ship in this release: before it, every alarm was unplaced, so a severity rule would
    have admitted nothing on any estate and looked like it worked. Inventing a severity to make a
    rule fire is forbidden (prime directive 2), so the honest behaviour is to refuse — and the
    console says so beside the control.
    """

    at_or_above_rank: int

    def admits(self, severity_rank: int | None) -> bool:
        if severity_rank is None:
            return False
        return severity_rank <= self.at_or_above_rank


@dataclass(frozen=True)
class OidRule:
    """*"Collect only traps under this subtree"*, matched on the trap OID or on any varbind.

    `root` is stored as the operator typed it, dot-separated and digits-only — the API validator
    refuses anything else, so this never has to defend against a hostile string. See
    :func:`under_subtree` for the arc-boundary rule and :data:`MatchOn` for the choice of where to
    look.
    """

    root: str
    match_on: MatchOn

    def admits(self, trap_oid: str, varbind_oids: tuple[str, ...]) -> bool:
        if self.match_on == "trap":
            return under_subtree(trap_oid, self.root)
        return any(under_subtree(oid, self.root) for oid in varbind_oids)


@dataclass(frozen=True)
class SlotRule:
    """*"Collect only between these two instants"* — a window inside the window.

    Absolute instants rather than an offset from the window's start, because the operator writes
    them as wall-clock times at the site (*"11:15 to 11:20"*) and the API resolves them against the
    window's zone once, at write time. Resolving per trap would put a `ZoneInfo` load on the hot
    path, and resolving against a stored offset would be wrong across a DST transition.

    Half-open at the top — `[from, to)` — so two adjacent slots cannot both admit one instant.
    """

    starts_at: float
    ends_at: float

    def admits(self, ts: float) -> bool:
        return self.starts_at <= ts < self.ends_at


@dataclass(frozen=True)
class TargetRules:
    """Every rule on one target of one window, grouped by kind, ready to be asked.

    Built once when the window is compiled into the in-memory index and then read per trap, so the
    grouping by kind is done here rather than per trap: the composition rule is *"different kinds
    AND, same kind OR"*, and a flat list would have to be partitioned on every packet.
    """

    severity: tuple[SeverityRule, ...] = ()
    oid: tuple[OidRule, ...] = ()
    slot: tuple[SlotRule, ...] = ()

    @property
    def collects_nothing(self) -> bool:
        """True when this target has no rule at all — D4's default, and the common case."""
        return not (self.severity or self.oid or self.slot)

    def admits(
        self,
        ts: float,
        trap_oid: str,
        varbind_oids: tuple[str, ...],
        severity_rank: int | None,
    ) -> bool:
        """Does this trap still get through? **The** D4 decision, and it is pure.

        Ordered cheapest-first — a float comparison, then an integer comparison, then the OID walk
        — because the great majority of traps under a window are refused and refusing early is
        free. The structure is the composition rule written literally: every kind that has rules
        must admit (AND), and within a kind one rule admitting is enough (OR).
        """
        if self.collects_nothing:
            return False
        if self.slot and not any(rule.admits(ts) for rule in self.slot):
            return False
        if self.severity and not any(rule.admits(severity_rank) for rule in self.severity):
            return False
        return not self.oid or any(rule.admits(trap_oid, varbind_oids) for rule in self.oid)


#: A target named by a window with no rules on it: collect nothing. Shared rather than constructed
#: per target, because an empty `TargetRules` is immutable and the common case by a wide margin.
COLLECT_NOTHING = TargetRules()
