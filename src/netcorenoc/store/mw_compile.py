"""The three reads the per-trap window index is built from (v0.21.0, Part V).

**The one query the maintenance-window feature runs on behalf of the ingest path, and it runs
once per maintenance tick rather than once per trap.** Its own module rather than a third section
of :mod:`netcorenoc.store.maintenance_windows` because it is the seam that matters most in this
feature: everything here is *off* the hot path and produces something that must be usable *on* it.

**It returns rows, and nothing else.** Turning them into the frozen predicate objects the check
reads is `engine/mw/compile.py`'s job, and the split is the layer rule rather than a preference:
`store` is the data layer and `engine` is above it, so a store module importing
`engine.mw.rules` would be an upward import — which `tests/test_layers.py` refuses, and rightly.
The store's job is rows; giving them meaning is the engine's.
"""

from __future__ import annotations

from typing import Any

from netcorenoc.store.base import StoreBase

#: How far ahead and behind :meth:`MaintenanceCompileMixin.window_index_rows` reaches. Both are
#: efficiency bounds only — every decision is made against the trap's own timestamp — so they need
#: only be comfortably longer than one maintenance tick.
#:
#: The values are literals here rather than imports from `engine/mw/index.py`, for the layer
#: reason above. `test_the_store_and_the_engine_agree_on_the_index_horizons`, in
#: `tests/test_maintenance_window.py`, asserts them against the engine's own constants, so the two
#: cannot drift.
INDEX_LOOKAHEAD_S = 3600.0
INDEX_LOOKBEHIND_S = 60.0


class MaintenanceCompileMixin(StoreBase):
    async def window_index_rows(self, now: float) -> list[dict[str, Any]]:
        """Every window in force or about to be, as rows: `{window, targets, rules}`.

        Three statements — windows, targets, rules — rather than one three-way join, because the
        join would return one row per rule per target and the assembly would have to de-duplicate
        windows out of it.

        `pending_confirmation` is deliberately **absent** from the status filter. An unconfirmed
        window suppresses nothing (II.7), and the cheapest way to guarantee that is for it never
        to reach the structure the check reads — rather than for the check to remember to exclude
        it.
        """
        if not self._has_maintenance:
            # Pre-0020 schema: the feature's tables do not exist yet (`base.py::_has_maintenance`).
            return []
        cur = await self.conn.execute(
            "SELECT id, name, organization_id, starts_at, ends_at, patch_s, ledger_enabled "
            "FROM maintenance_window WHERE status IN ('scheduled', 'active') "
            "AND (starts_at - patch_s) <= ? AND (ends_at + patch_s) >= ?",
            (now + INDEX_LOOKAHEAD_S, now - INDEX_LOOKBEHIND_S),
        )
        windows = [dict(row) for row in await cur.fetchall()]
        if not windows:
            return []
        ids = [int(w["id"]) for w in windows]
        marks = ",".join("?" * len(ids))
        cur = await self.conn.execute(
            "SELECT window_id, ne_id FROM maintenance_window_target "  # nosec B608
            f"WHERE window_id IN ({marks})",  # nosec B608 - placeholders only
            tuple(ids),
        )
        targets: dict[int, list[int]] = {}
        for row in await cur.fetchall():
            targets.setdefault(int(row["window_id"]), []).append(int(row["ne_id"]))
        cur = await self.conn.execute(
            "SELECT window_id, ne_id, kind, severity_rank, oid_root, match_on, "  # nosec B608
            "slot_starts_at, slot_ends_at FROM maintenance_window_rule "  # nosec B608
            f"WHERE window_id IN ({marks})",  # nosec B608 - placeholders only
            tuple(ids),
        )
        rules: dict[int, list[dict[str, Any]]] = {}
        for row in await cur.fetchall():
            rules.setdefault(int(row["window_id"]), []).append(dict(row))
        for window in windows:
            wid = int(window["id"])
            window["targets"] = targets.get(wid, [])
            window["rules"] = rules.get(wid, [])
        return windows
