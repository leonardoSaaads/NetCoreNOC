"""The feedback dataset: the capture sink, the promotion on label, and the retention tiers.

**Every method here reads or writes rows that are a scope bypass by construction.** Capture runs
engine-side, where visibility scoping does not exist and must not — correlation learns across the
whole estate — so these tables contain every NE, entity and raw varbind in the network, ungoverned
by any scope policy. **No route below `admin` may reach any of them**
(`docs/architecture/FEEDBACK-DATASET-0.8-DRAFT.md` §5a).

Nothing here decides anything. The engine-side logic that turns a `CorrelationResult` into rows —
including the fail-safe that a capture error degrades capture and never ingestion — is
`netcorenoc.capture`. This module is SQL.

**No method here takes ``store.lock``.** That is this package's contract for callers and it is not
relaxed for a dataset feature (v0.7.3's invariant; `tests/test_store_concurrency.py` is the
control).
"""

from __future__ import annotations

from typing import Any

from netcorenoc.store.base import StoreBase

# The client's reported membership is hostile input on a write path that already produced F34, F35
# and F39. It is **bounded**, and the truncation is **recorded** on the feedback row rather than
# silently applied — a silence would make the bound itself invisible in the data.
#
# 512 is far above any situation an operator can meaningfully read (the UI renders a card, not a
# spreadsheet) and far below anything that threatens the write. It is a bound, not a validation:
# exceeding it is never an error and never changes the response.
MAX_CLIENT_MEMBERS = 512

# "No surviving pair row references this observation." Written once and shared by both prune tiers,
# because the two must agree: an observation collected while a pair still points at it would leave a
# dataset row whose raw material is gone, which is silent corruption of the thing the release exists
# to produce. Deliberately checks `dataset_pair` as a whole rather than the matching lifecycle — a
# sink pair referencing an observation is reason enough to keep it.
_UNREFERENCED = (
    "id NOT IN (SELECT observation_a FROM dataset_pair WHERE observation_a IS NOT NULL) "
    "AND id NOT IN (SELECT observation_b FROM dataset_pair WHERE observation_b IS NOT NULL)"
)


class DatasetMixin(StoreBase):
    # -- capture context -----------------------------------------------------------------------

    async def open_capture_run(self, **fields: Any) -> int:
        """Open a capture run — the constants a period of capture shares (§5.6).

        Called at engine start and whenever the scorer configuration or the retention policy
        changes, so pair rows on either side of an admin's retune are distinguishable. Nothing else
        would record that, and a model trained across a retune is not one experiment.
        """
        columns = ", ".join(fields)
        marks = ", ".join("?" * len(fields))
        cur = await self.conn.execute(
            f"INSERT INTO capture_run ({columns}) VALUES ({marks}) RETURNING id",  # nosec B608
            tuple(fields.values()),
        )
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    async def latest_capture_run(self) -> dict[str, Any] | None:
        cur = await self.conn.execute("SELECT * FROM capture_run ORDER BY id DESC LIMIT 1")
        row = await cur.fetchone()
        return dict(row) if row is not None else None

    # -- the sink ------------------------------------------------------------------------------

    async def add_observation(self, **fields: Any) -> int:
        """One immutable row per **activation** (DECISIONS #105).

        Not per trap: `alarm` is deduplicated on `(entity_id, class_id, instance)` and mutated on
        re-fire, so it holds the latest state and cannot answer what the decision saw. Measured at
        0.7142 activations per trap; a per-trap row would add 40% write volume for rows no pair
        could ever reference.
        """
        columns = ", ".join(fields)
        marks = ", ".join("?" * len(fields))
        cur = await self.conn.execute(
            f"INSERT INTO dataset_observation ({columns}) VALUES ({marks}) RETURNING id",  # nosec B608
            tuple(fields.values()),
        )
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    async def add_pairs(self, rows: list[tuple[Any, ...]]) -> None:
        """One row per **evaluated** pair — linked and rejected alike, before truncation.

        `executemany` rather than a loop: this is the highest-volume write in the schema (Phase 0
        measured 194 341 evaluated pairs against 10 973 persisted links over the eval corpus), and
        it runs inside the batch lock. The column order is fixed by the caller in
        `netcorenoc.capture`, which is the one place that knows it.
        """
        if not rows:
            return
        await self.conn.executemany(
            "INSERT INTO dataset_pair ("
            "capture_run_id, alarm_a, alarm_b, observation_a, observation_b, situation_id, "
            "delta_t_s, class_affinity, entity_affinity, a_epoch, e_epoch, score, "
            "incumbent_linked, storm, truncated, evaluated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )

    # -- promotion on label --------------------------------------------------------------------

    async def promote_for_situation(self, situation_id: int, ts: float) -> tuple[int, int]:
        """Promote a labelled situation's pairs, and the observations they reference.

        Returns ``(pairs_promoted, observations_promoted)``.

        Promotion is an `UPDATE` of one column and **nothing moves** — ids are stable and the
        references between a pair and its observations cannot break. That is the property
        DECISIONS #107 chose this layout for, against a measurement that favoured two tables.

        The observation update is driven from the promoted pairs rather than from the situation, so
        an observation is promoted **because a promoted pair references it** — which is the actual
        invariant, and it survives a pair whose situation was reassigned by a merge.
        """
        cur = await self.conn.execute(
            "UPDATE dataset_pair SET lifecycle='dataset', promoted_at=? "
            "WHERE situation_id=? AND lifecycle='sink' RETURNING id",
            (ts, situation_id),
        )
        pairs = len(list(await cur.fetchall()))
        cur = await self.conn.execute(
            "UPDATE dataset_observation SET lifecycle='dataset', promoted_at=? "
            "WHERE lifecycle='sink' AND id IN ("
            "  SELECT observation_a FROM dataset_pair WHERE situation_id=? "
            "    AND lifecycle='dataset' AND observation_a IS NOT NULL"
            "  UNION"
            "  SELECT observation_b FROM dataset_pair WHERE situation_id=? "
            "    AND lifecycle='dataset' AND observation_b IS NOT NULL"
            ") RETURNING id",
            (ts, situation_id, situation_id),
        )
        observations = len(list(await cur.fetchall()))
        return pairs, observations

    async def count_evaluated_pairs_for(self, situation_id: int) -> int:
        """How many pairs of this situation were ever evaluated — the coverage numerator (§6.3)."""
        cur = await self.conn.execute(
            "SELECT COUNT(*) FROM dataset_pair WHERE situation_id=?", (situation_id,)
        )
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    # -- the membership record -----------------------------------------------------------------

    async def add_feedback_members(
        self, feedback_id: int, source: str, alarm_ids: list[int]
    ) -> None:
        """The ordered bag, server-side or client-reported (§5.4).

        `INSERT OR REPLACE` because a *changed* verdict is a legitimate correction (F36) that
        re-posts, and the second post's bag is the one that matches its row.

        An **empty** ``alarm_ids`` writes nothing and that is correct, not a no-op to guard against:
        a verdict posted to an already-merged situation genuinely has an empty bag, and recording
        zero rows is how that population becomes countable.
        """
        if not alarm_ids:
            return
        await self.conn.executemany(
            "INSERT OR REPLACE INTO feedback_member (feedback_id, source, position, alarm_id) "
            "VALUES (?, ?, ?, ?)",
            [(feedback_id, source, i, aid) for i, aid in enumerate(alarm_ids)],
        )

    async def feedback_members(self, feedback_id: int, source: str) -> list[int]:
        cur = await self.conn.execute(
            "SELECT alarm_id FROM feedback_member WHERE feedback_id=? AND source=? "
            "ORDER BY position",
            (feedback_id, source),
        )
        return [int(r[0]) for r in await cur.fetchall()]

    async def annotate_feedback(self, feedback_id: int, **fields: Any) -> None:
        """Set the label row's dataset columns. Written once, at the moment of the verdict."""
        if not fields:
            return
        assignments = ", ".join(f"{name}=?" for name in fields)
        await self.conn.execute(
            f"UPDATE feedback SET {assignments} WHERE id=?",  # nosec B608
            (*fields.values(), feedback_id),
        )

    # -- retention -----------------------------------------------------------------------------

    async def prune_sink(self, cutoff: float, row_cap: int) -> dict[str, int]:
        """The sink's **dual bound**: time, and a row cap, whichever binds first.

        Every statement carries ``lifecycle='sink'``. That clause is the whole separation between
        the sink and the labelled corpus under this layout, and DECISIONS #107 accepted it as a
        *checked* guarantee where two tables would have given a *structural* one.
        `tests/test_dataset.py` is the control that neither bound can reach a promoted row.

        The row cap is applied **after** the age bound so it only ever trims what age did not, and
        it deletes oldest-first — the same discipline as the correlator's window overflow.
        """
        counts = {"pairs_aged": 0, "pairs_capped": 0, "observations": 0}
        cur = await self.conn.execute(
            "DELETE FROM dataset_pair WHERE lifecycle='sink' AND evaluated_at < ? RETURNING id",
            (cutoff,),
        )
        counts["pairs_aged"] = len(list(await cur.fetchall()))
        cur = await self.conn.execute("SELECT COUNT(*) FROM dataset_pair WHERE lifecycle='sink'")
        row = await cur.fetchone()
        assert row is not None
        excess = int(row[0]) - row_cap
        if excess > 0:
            cur = await self.conn.execute(
                "DELETE FROM dataset_pair WHERE id IN ("
                "  SELECT id FROM dataset_pair WHERE lifecycle='sink' "
                "  ORDER BY evaluated_at, id LIMIT ?"
                ") RETURNING id",
                (excess,),
            )
            counts["pairs_capped"] = len(list(await cur.fetchall()))
        # An observation is dropped only once no sink pair references it. Promoted observations are
        # excluded by the lifecycle clause, so a promoted pair's raw material is never collected.
        cur = await self.conn.execute(
            "DELETE FROM dataset_observation WHERE lifecycle='sink' AND observed_at < ? "
            f"AND {_UNREFERENCED} RETURNING id",
            (cutoff,),
        )
        counts["observations"] = len(list(await cur.fetchall()))
        return counts

    async def prune_dataset(self, cutoff: float) -> dict[str, int]:
        """The training tier. **This deletes human labels and there is no rollback for a DELETE.**

        Never called without the operator having seen :meth:`preview_retention`'s count first —
        that discipline lives in the caller, because the preview is an HTTP-side, admin-only,
        read-only action (`preview.py`'s pattern, v0.6.0).
        """
        counts = {"pairs": 0, "observations": 0}
        cur = await self.conn.execute(
            "DELETE FROM dataset_pair WHERE lifecycle='dataset' AND evaluated_at < ? RETURNING id",
            (cutoff,),
        )
        counts["pairs"] = len(list(await cur.fetchall()))
        cur = await self.conn.execute(
            "DELETE FROM dataset_observation WHERE lifecycle='dataset' AND observed_at < ? "
            f"AND {_UNREFERENCED} RETURNING id",
            (cutoff,),
        )
        counts["observations"] = len(list(await cur.fetchall()))
        return counts

    async def preview_retention(self, cutoff: float) -> dict[str, Any]:
        """**Bounded, read-only, deterministic**: what a retention reduction would destroy.

        Returns counts and the date range, never rows. This is the one destructive control in the
        product, and the preview is the whole of the safeguard: the residual — that an admin who
        reads the count and proceeds can still destroy the corpus — is deliberate and is the same
        posture the product takes on scorer parameters.
        """
        cur = await self.conn.execute(
            "SELECT COUNT(*), MIN(evaluated_at), MAX(evaluated_at) FROM dataset_pair "
            "WHERE lifecycle='dataset' AND evaluated_at < ?",
            (cutoff,),
        )
        row = await cur.fetchone()
        assert row is not None
        pairs, oldest, newest = int(row[0]), row[1], row[2]
        cur = await self.conn.execute(
            "SELECT COUNT(*) FROM feedback WHERE created_at < ?", (cutoff,)
        )
        frow = await cur.fetchone()
        assert frow is not None
        cur = await self.conn.execute(
            "SELECT COUNT(*) FROM dataset_observation "
            "WHERE lifecycle='dataset' AND observed_at < ?",
            (cutoff,),
        )
        orow = await cur.fetchone()
        assert orow is not None
        return {
            "pairs": pairs,
            "observations": int(orow[0]),
            "labels": int(frow[0]),
            "oldest": float(oldest) if oldest is not None else None,
            "newest": float(newest) if newest is not None else None,
        }

    async def dataset_stats(self) -> dict[str, Any]:
        """What capture costs, in rows — the `dataset stats` CLI view (§7.3).

        Zero-config means the default is good, not that the operator is blind.
        """
        out: dict[str, Any] = {}
        for table in ("dataset_pair", "dataset_observation"):
            cur = await self.conn.execute(
                f"SELECT lifecycle, COUNT(*) FROM {table} GROUP BY lifecycle"  # nosec B608
            )
            for r in await cur.fetchall():
                out[f"{table}.{r[0]}"] = int(r[1])
        for table in ("capture_run", "feedback_member"):
            cur = await self.conn.execute(f"SELECT COUNT(*) FROM {table}")  # nosec B608
            row = await cur.fetchone()
            assert row is not None
            out[table] = int(row[0])
        cur = await self.conn.execute(
            "SELECT MIN(evaluated_at), MAX(evaluated_at) FROM dataset_pair WHERE lifecycle='sink'"
        )
        row = await cur.fetchone()
        assert row is not None
        out["sink_oldest"] = float(row[0]) if row[0] is not None else None
        out["sink_newest"] = float(row[1]) if row[1] is not None else None
        return out
