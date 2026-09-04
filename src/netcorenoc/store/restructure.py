"""What an **operator** does to a situation's membership: move, merge, split, and a zombie clear.

Separate from `situations.py`, which owns what the **correlator** does. The seam is not a size
accident: every method there runs on the ingest path under the batch lock and describes a decision
the appliance made, and every method here runs on the HTTP write path and describes a decision a
person made. They mutate the same two tables and they answer different questions, which is the same
line `engine/dataset/labels.py` was split from `capture.py` on.

**Every mutation here leaves the derived name correct.** `refresh_derived_name` is called for both
sides of every gesture, inside the same statement group, which is what makes staleness structurally
impossible rather than promised (DECISIONS #257).

**No method here writes a `situation_event`.** The event, its membership snapshot, its confidence
and its bag provenance are `engine/dataset/gestures.py`'s subject, because *what a gesture asserts*
is the pre-registration's question and this module is SQL.

**No method here takes ``store.lock``.** That is this package's contract for callers.
"""

from __future__ import annotations

from netcorenoc.store.situations import LIVE, SituationMixin

__all__ = ["RestructureMixin"]


class RestructureMixin(SituationMixin):
    """The three restructuring mutations, plus the zombie clear.

    Inherits `SituationMixin` — and through it the event mixin — rather than restating their
    signatures, on DECISIONS #88's terms: a declaration needs a body, and a stub that resolves
    instead of the real method is a silent no-op write. `operator_merge` calls `merge_situations`
    so a change to what a merge *is* cannot apply to one caller and not the other, and
    `operator_split` calls `create_situation` so the new situation is `new` by the same rule every
    other situation is.
    """

    async def move_alarm(self, alarm_id: int, from_situation: int, to_situation: int) -> bool:
        """Move one alarm between situations. Returns False if it was not where the caller said.

        **The release's product** — the only gesture that yields a negative and a positive from one
        action at pair granularity, and the reason is that it says two things at once: *this alarm
        does not belong with those* and *it belongs with these*.

        The `DELETE … RETURNING` is the check and the mutation in one statement, so a caller cannot
        act on a membership that changed between reading and writing: a concurrent correlator merge
        that moved the alarm first makes this return False and the route answers 409 rather than
        silently moving an alarm out of a situation it had already left.

        **Link rows are not moved and not deleted.** A `link` records what the correlator computed
        and why; an operator disagreeing with the grouping does not make the arithmetic untrue, and
        deleting it would destroy the explanation the card exists to show. A link whose endpoints
        are no longer both members of its situation is filtered out of the *view*
        (`situation_detail`), which is a presentation decision rather than a loss of evidence.
        """
        cur = await self.conn.execute(
            "DELETE FROM situation_alarm WHERE situation_id=? AND alarm_id=? RETURNING alarm_id",
            (from_situation, alarm_id),
        )
        if await cur.fetchone() is None:
            return False
        await self.conn.execute(
            "INSERT OR IGNORE INTO situation_alarm (situation_id, alarm_id) VALUES (?, ?)",
            (to_situation, alarm_id),
        )
        await self.refresh_derived_name(from_situation)
        await self.refresh_derived_name(to_situation)
        return True

    async def operator_merge(self, dst: int, src: int, ts: float) -> None:
        """Merge `src` into `dst` **because an operator said so**, not because a link bridged them.

        The row movement is deliberately identical to `merge_situations` — same three statements,
        same `merged_into`, same `resolution` — because the *result* must be one situation however
        it came about, and two shapes of merged situation would be two shapes every later reader
        has to handle. What differs is entirely in `situation_event`: the actor, the confidence, and
        an `acquisition_channel` of `merge` rather than nothing at all.

        `merge_situations` is called rather than re-implemented, so a change to what a merge *is*
        cannot apply to one caller and not the other.
        """
        await self.merge_situations(dst, src, ts)
        await self.refresh_derived_name(dst)

    async def operator_split(self, situation_id: int, departing: list[int], ts: float) -> int:
        """Split `departing` out of a situation into a new one. Returns the new situation's id.

        The new situation is `new`, not `open`: it has never been triaged, and the operator who
        created it by splitting has judged the *original* grouping rather than the new one.

        **Links whose two endpoints both depart move with them**, because they are still the
        correlator's explanation of a grouping that still exists — the sub-bag simply lives
        somewhere else now. Links that cross the new boundary stay on the original situation, where
        they record the joins this gesture is asserting were wrong, and the view filters them out.

        `departing` is the caller's already-reconciled list — every id in it is a current member,
        checked by the route against the same membership read that produced the snapshot — so this
        method moves rows and does not validate. An id that is not a member simply moves nothing,
        exactly as `move_alarm`'s `DELETE` matches nothing, and the count the route compares is what
        makes that visible rather than silent.
        """
        new_id = await self.create_situation(ts)
        marks = ",".join("?" * len(departing))
        if departing:
            await self.conn.execute(
                f"DELETE FROM situation_alarm WHERE situation_id=? AND alarm_id IN ({marks})",  # nosec B608
                (situation_id, *departing),
            )
            await self.conn.executemany(
                "INSERT OR IGNORE INTO situation_alarm (situation_id, alarm_id) VALUES (?, ?)",
                [(new_id, alarm) for alarm in departing],
            )
            await self.conn.execute(
                f"UPDATE link SET situation_id=? WHERE situation_id=? "  # nosec B608
                f"AND alarm_a IN ({marks}) AND alarm_b IN ({marks})",
                (new_id, situation_id, *departing, *departing),
            )
        await self.refresh_derived_name(situation_id)
        await self.refresh_derived_name(new_id)
        return new_id

    async def manual_clear_alarm(self, alarm_id: int, ts: float) -> bool:
        """Hand-clear one **active** alarm. Returns False if it was not active.

        A zombie alarm — one whose device never sent the clear — holds its situation open forever,
        and clearing it by hand is an operator asserting something about the **network**. It says
        nothing whatever about the grouping, so it produces no link-training row
        (`PREREGISTRATION-0.16.0.md` §1). That prohibition is enforced two layers up, in
        `engine/dataset/gestures.py`, and guarded by a test that fails if a row ever appears.

        `AND status='active'` makes the operation idempotent and makes a second click a 409 rather
        than a second event about an alarm that was already cleared.
        """
        cur = await self.conn.execute(
            "UPDATE alarm SET status='cleared', cleared_at=? WHERE id=? AND status='active' "
            "RETURNING id",
            (ts, alarm_id),
        )
        return await cur.fetchone() is not None

    async def situation_of_alarm(self, alarm_id: int) -> int | None:
        """Which situation currently holds this alarm, if any. The route's 404 decision."""
        cur = await self.conn.execute(
            "SELECT situation_id FROM situation_alarm WHERE alarm_id=?", (alarm_id,)
        )
        row = await cur.fetchone()
        return int(row[0]) if row is not None else None

    async def alarm_ne(self, alarm_id: int) -> tuple[bool, int | None]:
        """`(the alarm exists, its NE id)` — the scope decision for a gesture that names an alarm.

        Two facts from one read, because they answer one question: an alarm that does not exist and
        one whose NE the caller cannot see must take the **same** 404 branch, and computing them
        separately is how the two come to differ in timing. `ne_id` may legitimately be `None` on a
        row written before entity resolution, and `Scope.allows_ne(None)` is what decides that case
        — here as everywhere else, rather than by a second rule.
        """
        cur = await self.conn.execute("SELECT ne_id FROM alarm WHERE id=?", (alarm_id,))
        row = await cur.fetchone()
        if row is None:
            return False, None
        return True, (int(row[0]) if row[0] is not None else None)

    async def resolve_situation(self, situation_id: int, resolution: str, ts: float) -> bool:
        """Resolve a live situation with an **explicit** reason. Returns False if it was not live.

        Distinct from `close_situation`, which *derives* its reason from whether the members are
        still active, and from `manual_close_situation`, which always writes `operator`. This is the
        path for a cause only the caller knows: a hand-cleared zombie that happened to be the last
        active member resolves as `manual_clear`, and calling `close_situation` there would have
        recorded `self_cleared` — *"the network fixed itself"* — about a situation the network did
        nothing about.
        """
        if not self._has_lifecycle:
            return False
        cur = await self.conn.execute(
            "UPDATE situation SET status='resolved', resolution=?, closed_at=?, updated_at=? "
            f"WHERE id=? AND {LIVE} RETURNING id",  # nosec B608 - module literal
            (resolution, ts, ts, situation_id),
        )
        return await cur.fetchone() is not None
