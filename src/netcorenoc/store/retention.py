"""Bounded growth: drop old cleared alarms, closed situations, and quarantine.

**This is OPERATIONAL retention, and a human label is not operational data.** Nothing here may
delete a `feedback` row; that is F44, and §4 of `../../../docs/security/SECURITY-REVIEW-0.8.1.md`
records why the line that used to was correct when it was written and wrong by the time v0.8.0
shipped. The tiers that *do* govern a label are `capture.RetentionPolicy`'s, and only those.
"""

from __future__ import annotations

from netcorenoc.store.base import StoreBase


class RetentionMixin(StoreBase):
    async def prune(self, now: float, retention_s: float) -> dict[str, int]:
        """Bounded growth: drop old cleared alarms, closed situations, and quarantine.

        **F44 (v0.8.1): this does not delete `feedback`, and the omission is deliberate.**

        Until v0.8.0 a `feedback` row was a transient learning signal — applied by
        `learn.penalize()` at click time and then genuinely disposable — so deleting it with its
        situation was right. v0.8.0 made that row **the dataset's label** and did not revisit the
        line that deleted it, so in a default deployment every human verdict died
        `NETCORENOC_RETENTION_DAYS` (7.0) after its situation closed while the `dataset_pair`
        features it justified survived, because `dataset_pair` deliberately carries no foreign key
        to `alarm` or `situation`. Silent, asymmetric, and it emptied the one asset that cannot be
        recomputed, re-derived, or asked for again.

        **Why the labelled situation ROW is retained too, rather than left dangling.**
        `feedback.situation_id` is `NOT NULL REFERENCES situation (id)` with **no `ON DELETE`
        action** (`0031_postgresql.sql`), and PostgreSQL always enforces it. Deleting the situation
        out from under a surviving label does not produce a dangling reference — the database
        refuses it, and this whole sweep would raise on every pass. So a situation carrying a label
        keeps its one row, and **only that row**: its `situation_alarm` and `link` rows are
        collected here exactly as before, and the alarms behind them become collectable in the same
        pass. Bounded by the label count, which is the rarest event in the system. DECISIONS #109.

        The retained shell is collected by an ordinary later pass once the **audit sweep**
        (`capture.prune`, via :meth:`prune_dataset_audit`) removes its label — the one background
        path that may.
        """
        cutoff = now - retention_s
        counts: dict[str, int] = {}
        # v0.16.0: `closed` and `merged` were two `status` values and are now one — `resolved`,
        # with the reason in `resolution`. The set this sweep collects is unchanged: everything
        # that has left. Migration `0014` rewrote both values in place, so a database upgraded
        # mid-retention-window is collected on the same schedule it would have been.
        # ADR #453: the resolved set is a subquery, not a bound id list — asyncpg takes at most
        # 32 767 arguments, and the first prune after an import or a long stop had more — and the
        # exclusions are anti-joins, not `NOT IN (SELECT ...)`, which hashed whole tables per pass.
        gone = "SELECT id FROM situation WHERE status='resolved' AND closed_at < ?"
        await self.conn.execute(
            f"DELETE FROM situation_alarm WHERE situation_id IN ({gone})",  # nosec B608
            (cutoff,),
        )
        await self.conn.execute(
            f"DELETE FROM link WHERE situation_id IN ({gone})",  # nosec B608
            (cutoff,),
        )
        # No `DELETE FROM feedback` here. See the docstring: that line was F44.
        #
        # **v0.16.0 adds the second shell-retaining reason, for F44's reason exactly.**
        # `situation_event` is the append-only record of every operator gesture and it carries
        # no foreign key, precisely so history outlives its subject — but an event whose
        # `situation_id` names a row this sweep deleted is an event nothing can join. So a
        # situation carrying an event keeps its one row, like a situation carrying a label, and
        # both shells are collected by the ordinary later pass once the **audit sweep**
        # (`prune_dataset_audit`) has removed what was holding them.
        cur = await self.conn.execute(
            "DELETE FROM situation s WHERE s.status='resolved' AND s.closed_at < ? "
            "AND NOT EXISTS (SELECT 1 FROM feedback f WHERE f.situation_id = s.id) "
            "AND NOT EXISTS (SELECT 1 FROM situation_event e WHERE e.situation_id = s.id)",
            (cutoff,),
        )
        # The count is what was actually collected, not what was eligible: a labelled
        # situation is retained, and reporting it as pruned would misdescribe the sweep.
        counts["situations"] = cur.rowcount
        cur = await self.conn.execute(
            "DELETE FROM alarm a WHERE a.status='cleared' AND a.last_seen < ? "
            "AND NOT EXISTS (SELECT 1 FROM situation_alarm sa WHERE sa.alarm_id = a.id)",
            (cutoff,),
        )
        counts["alarms"] = cur.rowcount
        cur = await self.conn.execute("DELETE FROM quarantine WHERE received_at < ?", (cutoff,))
        counts["quarantine"] = cur.rowcount
        return counts
