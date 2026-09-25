"""The maintenance-window sweep: advance the clock, surface what outlived a window, rebuild.

Split from :mod:`netcorenoc.engine.operate.maintenance` in v0.21.0, at the 400-line guard and on a
seam that module's own docstring already draws. Everything there reasons about **accumulated
evidence** — how many observations a varbind has, how many alarms have closed. Everything here
reasons about **the clock**, and the two have different failure modes: an entity promoted too
early costs trust, while a window that does not end on time suppresses an estate.

Runs inside the lock `maintenance()` already holds, like every other method in that module's
family. **The ingest path never runs any of it** — it reads `self.windows`, which this replaces
with a finished object in a single assignment (Part V).
"""

from __future__ import annotations

from typing import Any

from netcorenoc.crosscutting import audit
from netcorenoc.engine.correlate.rootcause import Member
from netcorenoc.engine.mw import compile as mw_compile
from netcorenoc.engine.mw import ledger as mw_ledger
from netcorenoc.engine.operate.engine_base import EngineBase


class WindowSweepMixin(EngineBase):
    async def _maintenance_windows(self, now: float) -> None:
        """Advance the clock's transitions, surface what outlived a window, rebuild the index.

        **In that order, and the order is the design.** Advancing first means `just_ended` is
        exactly the set of windows whose ledgers must now be read; surfacing before the rebuild
        means the alarms exist before the index stops covering their elements; rebuilding last
        means the snapshot the ingest path reads is the one the transitions just produced.

        Runs inside the lock `maintenance()` already holds, like everything else in this module.
        The ingest path never runs any of it — it reads `self.windows`, which this method
        replaces with a finished object in a single assignment (Part V).
        """
        just_ended = await self.store.advance_statuses(now)
        await self.store.attribute_unassigned_nes()
        # The ledger is flushed BEFORE the unresolved rows are read, so a raise observed during
        # the same tick the window ended is in the table the sweep is about to read. In memory
        # first and persisted second is what keeps the observation off the ingest path; this is
        # where the two are reconciled.
        entries = self.ledger.take_dirty()
        if entries:
            await self.store.flush_ledger(mw_ledger.to_rows(entries))
        for window_id in just_ended:
            await self._surface_window(window_id, now)
            self.ledger.forget_window(window_id)
        self.windows = mw_compile.from_rows(await self.store.window_index_rows(now))

    async def surface_window_now(self, window_id: int, now: float) -> int:
        """*"End now"*, from the API. Surface what this window suppressed, **without the lock.**

        The caller is `POST /api/maintenance-windows/{id}/end`, inside `write_txn()`, which already
        holds `store.lock` — and the lock is not reentrant, so this may not take it. That is the
        whole reason this is a separate entry point from `_maintenance_windows`: the sweep holds
        the lock and calls the same body, the route is held *by* the lock and calls it directly.

        The window is already `ended` by the time this runs, so `advance_statuses` will not report
        it again on the next tick and the ledger is read once. Returns how many faults surfaced, so
        the operator who pressed the button is told — *"ended; 2 faults were raised during it and
        are still active"* is the sentence that matters, and it cannot be said by a sweep five
        seconds later.
        """
        unresolved = await self.store.unresolved_ledger(window_id)
        surfaced = await self._surface_entries(unresolved, now)
        self.ledger.forget_window(window_id)
        return surfaced

    async def _surface_entries(self, unresolved: list[Any], now: float) -> int:
        """Turn each unresolved ledger row into an alarm. Returns how many actually appeared.

        The rows are the store's eight scalars rather than the ledger's own types: `store` is the
        data layer and may not import them, so the crossing happens once, here, at the engine's
        boundary. `engine/mw/ledger.py::to_rows` is the other direction.
        """
        surfaced = 0
        for row in unresolved:
            alarm_id = await self.store.surface_ledger_entry(row, now)
            if alarm_id is not None:
                await self._open_situation_for(alarm_id, row, now)
                surfaced += 1
            await self.store.mark_ledger_surfaced(row, now)
        return surfaced

    async def _open_situation_for(self, alarm_id: int, row: Any, now: float) -> None:
        """Put a surfaced alarm in a situation of its own. **Found by the live pass** (F148).

        Without this the alarm exists in the `alarm` table and appears on **no screen**: Situations
        is the only view that lists alarms, and it lists them as members. A fault that outlived its
        window was being written to a table nobody reads, which is II.2 satisfied on paper and not
        at all in the product.

        **A singleton, never correlated.** The alarm carries no varbinds and no severity — the
        ledger holds none — so there is nothing for the correlator to score, and running it anyway
        would invent links out of an absence. It is a situation an operator can open, work and
        close like any other, and it joins nothing.

        The engine's in-memory maps are updated in the same step, and that is the part that is not
        cosmetic: `_assign_situation` reads `sit_of` to decide whether an activation needs a
        situation. Leaving this alarm out of it means the element's next real trap, which lands on
        the **same** `(device, class, instance)` row, would open a *second* situation for an alarm
        that is already in one.
        """
        _window_id, _ne_id, device_id, class_id, _instance, raised_at, _cleared, _s = row
        first_seen = float(raised_at) if raised_at is not None else now
        sid = await self.store.create_situation(first_seen, self.scorer_config_id, act="surface")
        await self.store.add_alarm_to_situation(sid, alarm_id)
        self.sit_of[alarm_id] = sid
        self.members[sid] = [Member(alarm_id, int(class_id), int(device_id), first_seen)]

    async def _surface_window(self, window_id: int, now: float) -> None:
        """**A fault that outlives its window surfaces** (II.2, prime directive 3).

        Everything the window saw raised and never saw cleared becomes an alarm marked *"raised
        during maintenance, still active"*. Read from the table rather than from memory, so a
        window that spanned a restart surfaces what it saw before the restart too.

        The alarm carries no varbinds and no severity, because the ledger holds none — see
        `store/mw_ledger.py`. An operator is told that something was raised and never cleared, and
        is not told a severity nobody transmitted to this appliance.

        Audited per window rather than per alarm: one row saying *"this window ended and surfaced
        N faults"* is what an operator reads, and N rows would bury it.
        """
        unresolved = await self.store.unresolved_ledger(window_id)
        surfaced = await self._surface_entries(unresolved, now)
        summary = await self.store.ledger_summary(window_id)
        await audit.write_event(
            self.store,
            ts=now,
            actor="system",
            role=None,
            source_ip=None,
            action="maintenance.window.end",
            outcome="ok",
            object_type="maintenance_window",
            object_id=str(window_id),
            details={
                "surfaced": surfaced,
                "suppressed_fingerprints": summary["seen"],
                "cleared_inside": summary["cleared"],
            },
        )
