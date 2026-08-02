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
        action** (`0001_init.sql:89`) and `lifecycle.py` sets `PRAGMA foreign_keys=ON`. Deleting the
        situation out from under a surviving label does not produce a dangling reference — SQLite
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
        cur = await self.conn.execute(
            "SELECT id FROM situation WHERE status IN ('closed','merged') AND closed_at < ?",
            (cutoff,),
        )
        gone = [int(r[0]) for r in await cur.fetchall()]
        counts["situations"] = 0
        if gone:
            # nosec B608 - `marks` is only "?" placeholders; ids are bound parameters
            marks = ",".join("?" * len(gone))
            await self.conn.execute(
                f"DELETE FROM situation_alarm WHERE situation_id IN ({marks})",  # nosec B608
                gone,
            )
            await self.conn.execute(
                f"DELETE FROM link WHERE situation_id IN ({marks})",  # nosec B608
                gone,
            )
            # No `DELETE FROM feedback` here. See the docstring: that line was F44.
            cur = await self.conn.execute(
                f"DELETE FROM situation WHERE id IN ({marks}) "  # nosec B608
                "AND id NOT IN (SELECT situation_id FROM feedback) RETURNING id",
                gone,
            )
            # The count is what was actually collected, not what was eligible: a labelled
            # situation is retained, and reporting it as pruned would misdescribe the sweep.
            counts["situations"] = len(list(await cur.fetchall()))
        cur = await self.conn.execute(
            "DELETE FROM alarm WHERE status='cleared' AND last_seen < ? AND id NOT IN "
            "(SELECT alarm_id FROM situation_alarm) RETURNING id",
            (cutoff,),
        )
        counts["alarms"] = len(list(await cur.fetchall()))
        cur = await self.conn.execute(
            "DELETE FROM quarantine WHERE received_at < ? RETURNING id", (cutoff,)
        )
        counts["quarantine"] = len(list(await cur.fetchall()))
        return counts
