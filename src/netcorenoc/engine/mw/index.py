"""The per-trap maintenance-window check. **No query, no lock, no I/O** (Part V).

The check runs on every trap the appliance decodes, so its cost is a property of the release rather
than a detail. This module is the structure that makes it free: an immutable snapshot of the
windows that are active or about to be, keyed by network element, refreshed from the store by the
**maintenance loop** at `MAINT_INTERVAL_S` and swapped in whole.

## Where it runs in the batch, and why exactly there

    decode -> class/device/NE -> entity -> severity -> [ THE CHECK ] -> store -> correlate

**After severity**, because D4's severity rule cannot be evaluated before the trap's severity is
placed — which is why D5 had to ship in the same release (ADR #364). **Before the store**, because
a trap a window suppresses is one this appliance does not record: not an alarm, not a situation,
not a training row.

## Why the snapshot is compared against the trap's own timestamp

`refresh()` runs every five seconds; a window boundary does not. Comparing `window.starts_at`
against the trap's `ts` rather than against "the tick we are in" makes the boundary exact — a trap
that arrived 40 ms before a window opened is collected, and one that arrived 40 ms after it is
not, however far the tick is from either. That is why the index holds windows that have not started
yet and windows that have just ended, and decides per trap rather than per refresh.

## What this module deliberately cannot do

It holds no connection, takes no lock and awaits nothing — `decide` is a plain synchronous method
over three dict lookups and a tuple walk. `tests/test_maintenance_window.py` reads this module's
AST and fails on an `await`, an `async def` or any name from the store, which is the same shape of
guard that replaced `TRAP_PATH_HASHES` for `datagram_received` in v0.18.0: a property nobody can
edit away by accident rather than a hash somebody recomputes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from netcorenoc.engine.mw.rules import COLLECT_NOTHING, TargetRules

#: How long after a window ends it stays in the index. The end-of-window sweep needs one tick to
#: run, and a trap whose timestamp falls inside a window that ended between two refreshes must
#: still be decided by that window rather than collected because the index forgot it.
TRAILING_GRACE_S = 60.0

#: How far ahead a window is compiled into the index. Purely an efficiency bound — the decision is
#: always made against the trap's own timestamp — so this only has to be comfortably longer than
#: one refresh interval.
LEADING_HORIZON_S = 3600.0


@dataclass(frozen=True)
class CompiledWindow:
    """One window, ready to be asked about a trap. Immutable, and built off the hot path.

    `targets` maps a network element id to the rules that apply to it **in this window**. A target
    with no rules maps to `COLLECT_NOTHING`, which is D4's default written as a value rather than
    as an absence — an absent key would mean "not a target of this window", and the two answers are
    opposite.

    **Two pairs of bounds, and they are not the same question.** `starts_at`/`ends_at` are what the
    operator DECLARED — what the card says, what an audit row records, what the timeline bar labels.
    `effective_starts_at`/`effective_ends_at` are those widened by the window's patch band, and they
    are what :meth:`covers` compares a trap against. Equipment does not come back the instant a
    window closes: a chassis rebooting at 11:58 emits its linkUp storm at 12:03, and an operator who
    declared 10:00-12:00 did not mean to be paged for it.

    Both are carried rather than one derived at the call site, because the derivation is a
    subtraction that would otherwise be repeated per trap — and because a reader of this object
    should not have to know which of the two a given field means.
    """

    window_id: int
    name: str
    organization_id: int
    starts_at: float
    ends_at: float
    effective_starts_at: float
    effective_ends_at: float
    targets: Mapping[int, TargetRules]
    ledger_enabled: bool = True

    def covers(self, ts: float) -> bool:
        """Is that instant in force for this window? Half-open, and **the effective bounds**."""
        return self.effective_starts_at <= ts < self.effective_ends_at


@dataclass(frozen=True)
class Decision:
    """What the check decided about one trap.

    Three states, and the third is the one the rest of the release turns on:

    * **`window_id is None`** — no window covers this trap. Collect it; nothing else happens. This
      is every trap on an appliance with no windows, and it is the branch that has to be free.
    * **`window_id` set, `collect` true** — a window covers it and a rule admits it. It is
      collected normally, forms situations normally, and is **excluded from learning and from the
      dataset** (ADR #372): maintenance traffic teaches affinities that are not the network's.
    * **`window_id` set, `collect` false** — suppressed. It reaches no alarm, no situation and no
      screen; the state ledger records that a raise or a clear was seen, and nothing else.
    """

    collect: bool
    window_id: int | None = None

    @property
    def suppressed(self) -> bool:
        return self.window_id is not None and not self.collect

    @property
    def under_window(self) -> bool:
        return self.window_id is not None


#: The answer for a trap no window covers. A shared singleton because it is immutable and is the
#: overwhelming majority of every decision this appliance will ever make.
COLLECT = Decision(collect=True)


@dataclass(frozen=True)
class WindowIndex:
    """Every compiled window, keyed by network element. Built by `refresh`, read per trap.

    Frozen and rebuilt rather than mutated, so the engine can swap a new one in with a single
    attribute assignment and a trap being decided at that moment reads either the old snapshot or
    the new one — never a half-updated dict. There is no lock because there is nothing to lock:
    the object a reader holds cannot change under it.
    """

    by_ne: Mapping[int, tuple[CompiledWindow, ...]] = field(default_factory=dict)

    def decide(
        self,
        ne_id: int,
        ts: float,
        trap_oid: str,
        varbind_oids: tuple[str, ...],
        severity_rank: int | None,
    ) -> Decision:
        """**THE** per-trap decision. One dict lookup on the empty-index path.

        The first line is the whole performance story: an appliance with no windows, or with
        windows on other elements, pays one hash of an integer and returns a shared constant. Only
        a trap from an element that is actually a target of some window walks any rules.

        When two windows cover the same instant on the same element — an operator extended one and
        scheduled another — a trap admitted by **either** is collected. Union rather than
        intersection, because each window's rules are that window's operator saying *"this still
        matters to me"*, and the stricter reading would let one operator's window silence another's
        exception.
        """
        windows = self.by_ne.get(ne_id)
        if not windows:
            return COLLECT
        covering = [window for window in windows if window.covers(ts)]
        if not covering:
            return COLLECT
        for window in covering:
            rules = window.targets.get(ne_id, COLLECT_NOTHING)
            if rules.admits(ts, trap_oid, varbind_oids, severity_rank):
                return Decision(collect=True, window_id=window.window_id)
        # Suppressed, and the window reported is the first one covering this instant — the one an
        # operator is shown on the alarm's marker and the one the state ledger is keyed on.
        return Decision(collect=False, window_id=covering[0].window_id)

    def active(self, ts: float) -> tuple[CompiledWindow, ...]:
        """Every distinct window covering that instant, for the marker and the Overview count."""
        seen: dict[int, CompiledWindow] = {}
        for windows in self.by_ne.values():
            for window in windows:
                if window.covers(ts):
                    seen.setdefault(window.window_id, window)
        return tuple(seen[key] for key in sorted(seen))


#: An appliance with no windows. Assigned at `Engine.__init__` so the check is answerable before
#: the first refresh has run — a cold start must never be the branch that reaches a `None`.
EMPTY = WindowIndex()


def compile_windows(windows: Sequence[CompiledWindow]) -> WindowIndex:
    """Group compiled windows by the elements they target. Off the hot path, once per refresh."""
    by_ne: dict[int, list[CompiledWindow]] = {}
    for window in windows:
        for ne_id in window.targets:
            by_ne.setdefault(ne_id, []).append(window)
    return WindowIndex(by_ne={ne_id: tuple(items) for ne_id, items in by_ne.items()})


def build(
    window_id: int,
    name: str,
    organization_id: int,
    starts_at: float,
    ends_at: float,
    patch_s: float,
    targets: Mapping[int, TargetRules],
    *,
    ledger_enabled: bool = True,
) -> CompiledWindow:
    """One window, with its patch band applied. The single place the effective bounds are derived.

    Derived here rather than at each caller so that *"what does `patch_s` actually do"* has one
    answer a reader can find: it widens the window symmetrically, and the widened interval is what
    the ingest check and the end-of-window sweep both use.
    """
    margin = max(0.0, patch_s)
    return CompiledWindow(
        window_id=window_id,
        name=name,
        organization_id=organization_id,
        starts_at=starts_at,
        ends_at=ends_at,
        effective_starts_at=starts_at - margin,
        effective_ends_at=ends_at + margin,
        targets=targets,
        ledger_enabled=ledger_enabled,
    )
