"""The state ledger: **a trap reports a transition, once** (II.2).

D4's per-target rules answer most of *"what happens when the window closes"*. One case they cannot
answer, and it is the one an optical engineer knows best:

> An NE raises Loss of Signal at 11:40, inside the window, and sends the trap **once**. If that
> trap is not collected, then at 12:00 — window closed, fibre still dark — the appliance has no
> record that anything is wrong, and nothing will ever tell it, unless the NE happens to re-send.

So D4's *"not collected"* needs exactly one internal exception, and it must not look like
collection. This module is that exception.

## What the ledger holds, and what it refuses to hold

Per `(window, device, class, instance)`: **raise seen, clear seen, and the two instants.** Six
scalars. It holds **no varbinds, no severity, no community tag and no payload of any kind**,
because storing those would be collecting the trap — which is precisely what the operator said not
to do. The consequence is stated rather than hidden: an alarm surfaced from this ledger says *"this
was raised during the window and never cleared"* and **does not say how serious it is**, because
the appliance genuinely does not know. Inventing one would be prime directive 2's fabrication.

## What it is not

Not an alarm, not shown, not correlated, not trained on, not in the dataset. The ledger table is
read by exactly two things — the end-of-window sweep and the admin-only window detail — and
`tests/test_maintenance_window.py` runs the injection: a ledger row reaching the correlator, the
learner, the dataset or any route below `admin` fails the suite.

## The two answers, and which one ships

II.2 offers the ledger and D3's end-of-window poll as alternative closures for this gap, and asks
which. The answer is that **they compose and the ledger is the load-bearing half** (ADR #371): the
ledger is what the appliance *saw* and costs nothing, while a poll is what the element *says now*
and needs a credential the operator may not have given.

**Only the ledger ships in v0.21.0.** D3 slips to v0.21.1 (HANDOFF §1), and the gap it leaves is
corroboration rather than detection: an appliance with no poll credentials still surfaces the
fault, which is the property prime directive 3 actually requires. When the poller arrives, its
reading attaches to the surfaced alarm as a second opinion; nothing here changes to receive it.

## Opting out

A per-window `ledger_enabled` flag exists and defaults to **on**. Turning it off is true discard,
and the console shows the risk in the operator's own words rather than hiding the option: *"a fault
that starts during this window and never clears will not be recorded at all"*. That is the
project's first principle — *"you may, and here is the risk"* — rather than a switch nobody is
trusted with.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # no runtime edge: both are only needed for the signature of `observe_suppressed`
    from netcorenoc.engine.correlate.learn import Learner
    from netcorenoc.engine.mw.index import Decision, WindowIndex
    from netcorenoc.ingest.events import TrapEvent


@dataclass(frozen=True)
class LedgerKey:
    """What the ledger is keyed on: the alarm's identity, and nothing about its content.

    The same `(device, class, instance)` triple the `alarm` table dedups on, so a surfaced entry
    lands on exactly the row a collected trap would have opened — and a later real trap for the
    same fault bumps that row rather than opening a second one.
    """

    window_id: int
    device_id: int
    class_id: int
    instance: str


@dataclass
class LedgerEntry:
    """Raise seen, clear seen. **Six scalars, no payload** — see the module docstring."""

    ne_id: int
    raised_at: float | None = None
    cleared_at: float | None = None
    surfaced_at: float | None = None

    @property
    def unresolved(self) -> bool:
        """Raised inside the window, never cleared inside it, and not yet surfaced."""
        return self.raised_at is not None and self.cleared_at is None and self.surfaced_at is None


@dataclass
class StateLedger:
    """The in-memory ledger for the windows currently running. Flushed by the maintenance sweep.

    In memory first and persisted second, for the reason every other hot-path structure in this
    appliance is: the observation happens under the batch lock, where a write would be I/O on the
    ingest path. The sweep flushes it, so a restart mid-window loses at most one tick of
    observations — and the sweep reloads what was already flushed, so a window that spans a restart
    still surfaces what it saw before the restart.
    """

    entries: dict[LedgerKey, LedgerEntry] = field(default_factory=dict)
    #: Keys touched since the last flush. The sweep writes these and clears the set, so a busy
    #: window does not rewrite every row it has ever seen on every tick.
    dirty: set[LedgerKey] = field(default_factory=set)

    def observe_suppressed(
        self,
        decision: Decision,
        ne_id: int,
        device_id: int,
        class_id: int,
        instance: str,
        item: TrapEvent,
        learner: Learner,
        windows: WindowIndex,
    ) -> None:
        """**The whole of what happens to a suppressed trap.** One dict write, no I/O.

        Called from `Engine._process` under the batch lock, which is why the decision lives here
        rather than there: `engine.py`'s cohesion exemption covers the ingest path's *readability*,
        and *"is this suppressed trap a raise or a clear"* is a decision, not a call site.

        ## Keyed on the RAISE class, even when the trap is a clear

        This is the subtle part and getting it wrong would make the ledger silently useless. A
        learned clear arrives on its **own** class — `linkUp` is not `linkDown` — and
        `Engine._handle_clear` therefore closes the alarm of the *raise* class. The ledger has to
        do the same: keying a clear on the clear class would file it under a fingerprint no raise
        ever used, so the raise would stay unresolved and surface at the end of the window as a
        fault that had in fact cleared inside it.

        A **state** clear (S9) is the other shape: raise and clear share the class and differ only
        in a varbind's value, so there the trap's own class is already the right key.

        ## The opt-out

        A window whose `ledger_enabled` is false records nothing — true discard, offered with its
        risk visible on the card rather than hidden. `engine/mw/ledger.py`'s module docstring
        carries the sentence the console shows.
        """
        window_id = decision.window_id
        if window_id is None:
            return
        window = next((w for w in windows.by_ne.get(ne_id, ()) if w.window_id == window_id), None)
        if window is not None and not window.ledger_enabled:
            return
        raise_class = learner.clears.clear_to_raise.get(class_id)
        if raise_class is not None:
            self.observe_clear(
                LedgerKey(window_id, device_id, raise_class, instance), ne_id, item.ts
            )
            return
        if learner.states.is_clear(class_id, [(vb.oid, vb.value) for vb in item.varbinds]):
            self.observe_clear(LedgerKey(window_id, device_id, class_id, instance), ne_id, item.ts)
            return
        self.observe_raise(LedgerKey(window_id, device_id, class_id, instance), ne_id, item.ts)

    def observe_raise(self, key: LedgerKey, ne_id: int, ts: float) -> None:
        """A suppressed trap that is not a clear. **In memory, no I/O** — called under the batch
        lock from the ingest path.

        A second raise of a fingerprint already raised keeps the **first** instant: the fault
        started when it started, and a repeat is the same fault still being reported.
        """
        entry = self.entries.get(key)
        if entry is None:
            entry = self.entries[key] = LedgerEntry(ne_id=ne_id)
        if entry.raised_at is None or entry.cleared_at is not None:
            entry.raised_at = ts
            entry.cleared_at = None
            entry.surfaced_at = None
        self.dirty.add(key)

    def observe_clear(self, key: LedgerKey, ne_id: int, ts: float) -> None:
        """A suppressed trap the appliance recognises as a clear for a fingerprint it has raised.

        A clear for a fingerprint the ledger never saw raised is recorded anyway, with no raise
        instant: it is evidence that the element is talking and that this fault is over, and the
        `unresolved` predicate already refuses to surface an entry with no raise.
        """
        entry = self.entries.get(key)
        if entry is None:
            entry = self.entries[key] = LedgerEntry(ne_id=ne_id)
        entry.cleared_at = ts
        self.dirty.add(key)

    def unresolved(self, window_id: int) -> list[tuple[LedgerKey, LedgerEntry]]:
        """Everything this window saw raised and never saw cleared. The end-of-window answer."""
        return [
            (key, entry)
            for key, entry in sorted(
                self.entries.items(),
                key=lambda item: (item[0].device_id, item[0].class_id, item[0].instance),
            )
            if key.window_id == window_id and entry.unresolved
        ]

    def forget_window(self, window_id: int) -> None:
        """Drop a finished window's entries once they have been flushed and surfaced.

        Bounded memory is the whole reason this exists: a window over a large estate can ledger one
        entry per fingerprint, and an appliance that never forgot them would grow without limit
        across a year of planned work. The durable rows stay — they are the record of what the
        window suppressed — and only the in-memory copy is released.
        """
        for key in [key for key in self.entries if key.window_id == window_id]:
            del self.entries[key]
            self.dirty.discard(key)

    def take_dirty(self) -> list[tuple[LedgerKey, LedgerEntry]]:
        """The entries changed since the last flush, and reset the set. Called by the sweep."""
        taken = [
            (key, self.entries[key])
            for key in sorted(
                self.dirty, key=lambda k: (k.window_id, k.device_id, k.class_id, k.instance)
            )
            if key in self.entries
        ]
        self.dirty.clear()
        return taken


def to_rows(
    entries: list[tuple[LedgerKey, LedgerEntry]],
) -> list[tuple[int, int, int, int, str, float | None, float | None, float | None]]:
    """The ledger's own types as the eight scalars the store speaks.

    **The conversion happens here, at the engine's boundary**, because `store` is the data layer
    and may not import these types — an upward import turns a stack into a knot, and
    `tests/test_layers.py` refuses one. The store writes rows; what the six fields *mean* is
    documented in this module, which is where a reader asking *"why is there no severity column"*
    will look.
    """
    return [
        (
            key.window_id,
            entry.ne_id,
            key.device_id,
            key.class_id,
            key.instance,
            entry.raised_at,
            entry.cleared_at,
            entry.surfaced_at,
        )
        for key, entry in entries
    ]
