"""Maintenance windows: the writes and the state machine.

Two siblings carry the rest, split on seams this package already uses everywhere. The reads — the
upcoming list, one window's detail, the preview counts, the markers other screens show — are
:mod:`netcorenoc.store.mw_reads`: what mutates against what projects. The compile that builds the
structure the ingest path reads is :mod:`netcorenoc.store.mw_compile`: off the hot path against
on it.

## The state machine, in one place

    pending_confirmation --confirm--> scheduled --(clock)--> active --(clock)--> ended
             |                            |                     |
             |                            +-------cancel--------+--> cancelled
             +--(start arrives)--> expired            end now --+--> ended

Six statuses, and each one exists because an operator does something different about it. The two
the clock drives — `scheduled -> active -> ended` — are advanced by :meth:`advance_statuses`, which
the maintenance sweep calls; the four a person drives are their own methods. **Cancel is a state,
not a delete** (Part III): the record of what was planned, and the audit row that created it,
both survive.

`expired` is the D6 safe failure (II.7). A window over six hours that nobody confirmed does not
take effect when its start arrives — alarms keep flowing, which is the failure that cannot hide an
outage — and it becomes visibly expired rather than silently ignored.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from netcorenoc.store.base import StoreBase

#: Every status a window may hold. `tests/test_maintenance_window.py` asserts the migration's
#: documented set against this tuple, so the schema comment and the code cannot drift.
STATUSES = (
    "pending_confirmation",
    "scheduled",
    "active",
    "ended",
    "cancelled",
    "expired",
)

#: The statuses a window can still act from. A cancelled or expired window is inert for ever.
LIVE_STATUSES = ("pending_confirmation", "scheduled", "active")

#: D6: **up to six hours, no human confirmation; over six hours, an editor or an admin confirms.**
#: The maintainer's decision, unchanged; II.7's two edges are settled in `api/routes/maintenance.py`
#: where the actor is known.
CONFIRMATION_THRESHOLD_S = 6 * 3600.0


@dataclass(frozen=True)
class WindowDraft:
    """Everything a create needs, already validated by the API model.

    A dataclass rather than fifteen positional arguments because the create is the one write in
    this appliance with enough fields to transpose two of them silently, and `mypy --strict` cannot
    catch `starts_at`/`ends_at` swapped in a call but can catch a missing field here.
    """

    name: str
    description: str
    organization_id: int
    tz: str
    starts_at: float
    ends_at: float
    all_day: bool
    patch_s: float
    ledger_enabled: bool
    visibility: str
    owner_ref: str
    owner_role: str
    created_by_agent: bool
    needs_confirmation: bool
    status: str
    idempotency_key: str | None


class MaintenanceWindowMixin(StoreBase):
    # -- writes ---------------------------------------------------------------------------

    async def create_maintenance_window(self, draft: WindowDraft, now: float) -> int:
        cur = await self.conn.execute(
            "INSERT INTO maintenance_window (name, description, organization_id, status, tz, "
            "starts_at, ends_at, all_day, patch_s, ledger_enabled, visibility, owner_ref, "
            "owner_role, created_by_agent, needs_confirmation, created_at, updated_at, "
            "idempotency_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id",
            (
                draft.name,
                draft.description,
                draft.organization_id,
                draft.status,
                draft.tz,
                draft.starts_at,
                draft.ends_at,
                int(draft.all_day),
                draft.patch_s,
                int(draft.ledger_enabled),
                draft.visibility,
                draft.owner_ref,
                draft.owner_role,
                int(draft.created_by_agent),
                int(draft.needs_confirmation),
                now,
                now,
                draft.idempotency_key,
            ),
        )
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    async def window_for_idempotency_key(self, key: str) -> int | None:
        """The window a previous call with this key created, or None. **The retry answer.**

        An agent that times out and retries must not create two windows (Part III). The partial
        unique index makes a duplicate insert impossible; this makes the retry *succeed* — it
        returns the window the first call made, so the agent gets the same id and the same body
        rather than a 409 it has to interpret.
        """
        cur = await self.conn.execute(
            "SELECT id FROM maintenance_window WHERE idempotency_key=?", (key,)
        )
        row = await cur.fetchone()
        return int(row[0]) if row is not None else None

    async def set_window_targets(self, window_id: int, ne_ids: list[int]) -> None:
        """Replace the window's target set. Rules for elements no longer targeted go with them.

        Replace rather than merge, because an update carries the whole target list the operator is
        looking at: merging would make *"remove this host"* impossible to express, and a rule left
        behind on an untargeted element is a rule that can never fire and will confuse whoever
        reads the window next.
        """
        await self.conn.execute(
            "DELETE FROM maintenance_window_target WHERE window_id=?", (window_id,)
        )
        for ne_id in sorted(set(ne_ids)):
            await self.conn.execute(
                "INSERT INTO maintenance_window_target (window_id, ne_id) VALUES (?, ?)",
                (window_id, ne_id),
            )
        if ne_ids:
            marks = ",".join("?" * len(set(ne_ids)))
            await self.conn.execute(
                "DELETE FROM maintenance_window_rule WHERE window_id=? "  # nosec B608
                f"AND ne_id NOT IN ({marks})",  # nosec B608 - placeholders only
                (window_id, *sorted(set(ne_ids))),
            )
        else:
            await self.conn.execute(
                "DELETE FROM maintenance_window_rule WHERE window_id=?", (window_id,)
            )

    async def set_window_rules(
        self, window_id: int, rules: list[dict[str, Any]], now: float
    ) -> None:
        """Replace the window's collection rules. Same replace-not-merge reasoning as the targets.

        Every value is already validated by `api/models_maintenance.py` — the kind's own columns
        present, the others absent, the OID digits-and-dots, the rank inside the scale. This writes
        what it is given.
        """
        await self.conn.execute(
            "DELETE FROM maintenance_window_rule WHERE window_id=?", (window_id,)
        )
        for rule in rules:
            await self.conn.execute(
                "INSERT INTO maintenance_window_rule (window_id, ne_id, kind, severity_rank, "
                "oid_root, match_on, slot_starts_at, slot_ends_at, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    window_id,
                    int(rule["ne_id"]),
                    str(rule["kind"]),
                    rule.get("severity_rank"),
                    rule.get("oid_root"),
                    rule.get("match_on"),
                    rule.get("slot_starts_at"),
                    rule.get("slot_ends_at"),
                    now,
                ),
            )

    async def update_window_schedule(
        self,
        window_id: int,
        *,
        starts_at: float,
        ends_at: float,
        tz: str,
        patch_s: float,
        needs_confirmation: bool,
        status: str,
        now: float,
    ) -> None:
        """Move a window in time, and **re-decide D6 in the same statement**.

        The re-decision is here and only here. `needs_confirmation` is stored rather than derived
        on read (see `0021`'s comment) precisely so that an extend which pushes a four-hour window
        past six hours is a visible line of code that re-asks the question — rather than a window
        silently losing its confirmed state because somebody moved its end.
        """
        await self.conn.execute(
            "UPDATE maintenance_window SET starts_at=?, ends_at=?, tz=?, patch_s=?, "
            "needs_confirmation=?, status=?, updated_at=? WHERE id=?",
            (
                starts_at,
                ends_at,
                tz,
                patch_s,
                int(needs_confirmation),
                status,
                now,
                window_id,
            ),
        )

    async def update_window_fields(
        self,
        window_id: int,
        *,
        name: str,
        description: str,
        visibility: str,
        ledger_enabled: bool,
        now: float,
    ) -> None:
        await self.conn.execute(
            "UPDATE maintenance_window SET name=?, description=?, visibility=?, ledger_enabled=?, "
            "updated_at=? WHERE id=?",
            (name, description, visibility, int(ledger_enabled), now, window_id),
        )

    async def confirm_window(self, window_id: int, by_ref: str, now: float) -> bool:
        """D6's human gesture. Returns False when the window was not waiting for one.

        The `status=` guard in the WHERE clause is what makes this safe against two editors
        pressing confirm on the same card: the second `UPDATE` matches no row and answers False,
        and the route turns that into the 409 that tells them to reload rather than a second audit
        row claiming a confirmation that did not happen.
        """
        cur = await self.conn.execute(
            "UPDATE maintenance_window SET status='scheduled', confirmed_at=?, confirmed_by=?, "
            "updated_at=? WHERE id=? AND status='pending_confirmation'",
            (now, by_ref, now, window_id),
        )
        return bool(cur.rowcount)

    async def cancel_window(self, window_id: int, now: float) -> bool:
        """Call a window off. **A state, not a delete**: the plan and its audit row both survive."""
        marks = ",".join("?" * len(LIVE_STATUSES))
        cur = await self.conn.execute(
            "UPDATE maintenance_window SET status='cancelled', cancelled_at=?, updated_at=? "  # nosec B608
            f"WHERE id=? AND status IN ({marks})",  # nosec B608 - placeholders only
            (now, now, window_id, *LIVE_STATUSES),
        )
        return bool(cur.rowcount)

    async def end_window_now(self, window_id: int, now: float) -> bool:
        """*"The work is done, give me my alarms back."*

        `ends_at` is left where the operator declared it and `ended_at` records when it actually
        stopped. Both facts matter: one is what was planned, the other is what happened, and a
        window that overwrote the first with the second would erase the evidence that the estimate
        was wrong.
        """
        cur = await self.conn.execute(
            "UPDATE maintenance_window SET status='ended', ended_at=?, updated_at=? "
            "WHERE id=? AND status IN ('active', 'scheduled', 'pending_confirmation')",
            (now, now, window_id),
        )
        return bool(cur.rowcount)

    async def advance_statuses(self, now: float) -> list[int]:
        """The clock's three transitions. Returns the windows that **just ended**.

        Called once per maintenance tick, off the ingest path. The return value is what the
        end-of-window sweep needs: a window that ended on this tick is one whose ledger must now
        be read and whose unresolved raises must surface.

        Order matters and is the order below:

        1. **expire** an unconfirmed window whose start has arrived — before anything can make it
           active, which is the whole of D6's safe failure;
        2. **activate** a scheduled window whose effective start has arrived;
        3. **end** an active window whose effective end has passed.

        The bounds are the **effective** ones — widened by `patch_s` — because those are what the
        ingest check uses, and a window that stopped being in force at a different instant from
        the one the sweep believes would leave the ledger holding raises nobody surfaces.
        """
        await self.conn.execute(
            "UPDATE maintenance_window SET status='expired', updated_at=? "
            "WHERE status='pending_confirmation' AND (starts_at - patch_s) <= ?",
            (now, now),
        )
        await self.conn.execute(
            "UPDATE maintenance_window SET status='active', updated_at=? "
            "WHERE status='scheduled' AND (starts_at - patch_s) <= ? AND (ends_at + patch_s) > ?",
            (now, now, now),
        )
        cur = await self.conn.execute(
            "UPDATE maintenance_window SET status='ended', ended_at=COALESCE(ended_at, ?), "
            "updated_at=? WHERE status IN ('active', 'scheduled') AND (ends_at + patch_s) <= ? "
            "RETURNING id",
            (now, now, now),
        )
        return [int(row[0]) for row in await cur.fetchall()]
