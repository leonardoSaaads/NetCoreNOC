"""Shadow mode's SQL: the training join, the corpus counts, the run row, and the opinions.

**Every row here is a scope bypass by construction**, exactly as `store/dataset.py`'s are: shadow
opinions are written engine-side, where visibility scoping does not exist and must not. **No route
below `admin` may reach any of them** — and v0.9.0 adds no route at all, which is the strongest form
of that.

Nothing here decides anything. The engine-side logic — when to train, whether the corpus suffices,
what a bag-level verdict means for a pair — is `netcorenoc.training` and `netcorenoc.shadow`. This
module is SQL.

**No method here takes ``store.lock``.** That is this package's contract for callers and a shadow
feature does not relax it (`tests/test_store_concurrency.py` is the control).

A separate module from `store/dataset.py` because that file is at **395 of its 400-line budget**.
Growing it was not an option and neither was raising a guard (DECISIONS #113's precedent), and the
seam is real rather than convenient: `dataset.py` owns *what capture wrote*, this owns *what the
challenger read and wrote back*.
"""

from __future__ import annotations

from typing import Any

from netcorenoc.store.base import StoreBase

__all__ = ["ShadowMixin"]


class ShadowMixin(StoreBase):
    # -- incident identity ---------------------------------------------------------------------

    async def merge_edges(self) -> dict[int, int]:
        """Every recorded merge, as `merged situation -> destination`.

        **This module no longer decides what an incident is.** Until v0.10.0 the two joins below
        carried `COALESCE(s.merged_into, f.situation_id) AS incident`, which is **one hop** — and a
        merge chain can be longer than one hop, because `merge_situations(sid, other)` marks the
        source merged into `sid` and `sid` can itself later be merged.

        Resolving the chain is `netcorenoc.incidents.resolve_all`, and it lives there rather than
        here for a reason that is not stylistic: SQLite could follow the chain with a recursive CTE,
        and then there would be **two** implementations of incident identity — one in Python for the
        seal and one in SQL for the estimator — which is exactly how the two come to disagree about
        which incidents exist with nothing going red. So the SQL returns the **edges** and the
        arithmetic happens once.

        Ordered by `id` so the mapping is built in a stable order; the result is a `dict` and the
        order does not affect it, but a deterministic read is cheaper to reason about than an
        argument that the order cannot matter.
        """
        cur = await self.conn.execute(
            "SELECT id, merged_into FROM situation WHERE merged_into IS NOT NULL ORDER BY id"
        )
        return {int(r[0]): int(r[1]) for r in await cur.fetchall()}

    async def pre_v080_merges(self) -> int:
        """Situations merged before `0008` existed: merged with **no destination**.

        **v0.16.0: the same population, addressed through the column that now carries the fact.**
        `merged` was a `status` value and is now a `resolution`; migration `0014` rewrote every row
        in place, so this counts exactly the rows it counted before — asserted by
        `tests/test_upgrade.py`, which compares the count across the migration rather than trusting
        that the rewrite was faithful.

        Unrecoverable by construction — the destination was never written and no migration can
        reconstruct one — so such a situation *looks* independent and is not, and **no column
        distinguishes it from a genuinely independent one**. `PREREGISTRATION-0.10.0.md` §3.3
        requires these to be counted rather than assumed absent, and §7.10 makes them a verdict
        trigger above 10 % of `asserting_incidents`.
        """
        cur = await self.conn.execute(
            "SELECT COUNT(*) FROM situation WHERE resolution = 'merged' AND merged_into IS NULL"
        )
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    # -- the training join ---------------------------------------------------------------------

    async def labelled_pairs(self, *, include_legacy: bool = False) -> list[dict[str, Any]]:
        """Every promoted pair with the human verdict on its bag. **The one training query.**

        Written once, in one place, with the coverage and provenance filters visible — which is
        what `SHADOW-MODE-0.9-DRAFT.md` §1 asked for, and the reason is that a join reproduced in
        three places is three chances to forget the `capture_provenance` filter.

        `capture_provenance = 'current'` is the **default, not an option**: `legacy_capture` rows
        were acquired over the path v0.7.5 repaired and are of *unknown* quality, which is a weaker
        and different claim from bad. Including them is an explicit, conscious choice and is
        reported separately when made.

        `ORDER BY f.id, p.id` — both stable and unique, so the training row order is a property of
        the data rather than of the query planner. That ordering is part of the fit's determinism.
        """
        legacy = 1 if include_legacy else 0
        cur = await self.conn.execute(
            # v0.16.0: `e.confidence` — **one column added by a LEFT JOIN**, and nothing else about
            # this query moves. The confidence an operator stated lives on `situation_event`, in its
            # own column, and is reached here through the label row the gesture produced. A LEFT
            # JOIN because most labels have no event (every one written before this release) and
            # `NULL` there means *not reported*, which `confidence.multiplier` turns into 1.0 —
            # the status quo, unshrunk. The row count cannot change: a partial UNIQUE index on
            # `situation_event.feedback_id` admits at most one event per label.
            "SELECT p.id AS pair_id, p.delta_t_s, p.class_affinity, p.entity_affinity, "
            "       p.incumbent_linked, p.evaluated_at, p.situation_id, p.alarm_a, p.alarm_b, "
            "       f.id AS feedback_id, f.verdict, f.created_at AS label_at, e.confidence "
            "FROM dataset_pair p "
            "JOIN feedback f ON f.situation_id = p.situation_id "
            "LEFT JOIN situation_event e ON e.feedback_id = f.id "
            "WHERE p.lifecycle = 'dataset' "
            "  AND (f.capture_provenance = 'current' OR ?) "
            "  AND f.id = (SELECT f2.id FROM feedback f2 "
            "               WHERE f2.situation_id = p.situation_id "
            "                 AND (f2.capture_provenance = 'current' OR ?) "
            "               ORDER BY f2.created_at DESC, f2.id DESC LIMIT 1) "
            "ORDER BY f.id, p.id",
            (legacy, legacy),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def labelled_bags(self, *, include_legacy: bool = False) -> list[dict[str, Any]]:
        """One row per labelled bag, with its promoted-pair summary.

        The unit the sufficiency floors are expressed in — *n* is the number of independent
        **bags**, not the number of pairs — and the unit partition evaluation groups by. Same
        latest-verdict rule as :meth:`labelled_pairs`, so the two can never disagree about what a
        bag is.
        """
        legacy = 1 if include_legacy else 0
        cur = await self.conn.execute(
            "SELECT f.id AS feedback_id, f.verdict, f.created_at AS label_at, f.situation_id, "
            "       COALESCE(f.principal_ref, '(unattributed)') AS principal, "
            "       COALESCE(f.member_count, -1) AS member_count, "
            "       COALESCE(f.coverage, 'unrecorded') AS coverage, "
            "       COUNT(p.id) AS pairs, "
            "       COALESCE(SUM(p.incumbent_linked), 0) AS accepted "
            "FROM feedback f "
            "LEFT JOIN dataset_pair p "
            "       ON p.situation_id = f.situation_id AND p.lifecycle = 'dataset' "
            "WHERE (f.capture_provenance = 'current' OR ?) "
            "  AND f.id = (SELECT f2.id FROM feedback f2 "
            "               WHERE f2.situation_id = f.situation_id "
            "                 AND (f2.capture_provenance = 'current' OR ?) "
            "               ORDER BY f2.created_at DESC, f2.id DESC LIMIT 1) "
            "GROUP BY f.id ORDER BY f.id",
            (legacy, legacy),
        )
        return [dict(r) for r in await cur.fetchall()]

    # -- the challenger run --------------------------------------------------------------------

    async def open_challenger_run(self, **fields: Any) -> int:
        """Record one training attempt. **Written even when nothing was fitted.**

        `sufficient = 0` with NULL coefficients is a *result*, not an absence: which floors were
        missed, by how much, and how long until they would be met. A store that could only record a
        successful fit would make insufficiency invisible.
        """
        columns = ", ".join(fields)
        marks = ", ".join("?" * len(fields))
        cur = await self.conn.execute(
            f"INSERT INTO challenger_run ({columns}) VALUES ({marks}) RETURNING id",  # nosec B608
            tuple(fields.values()),
        )
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    async def latest_challenger_run(self) -> dict[str, Any] | None:
        cur = await self.conn.execute(
            "SELECT * FROM challenger_run ORDER BY id DESC LIMIT 1",
        )
        row = await cur.fetchone()
        return dict(row) if row is not None else None

    async def challenger_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        cur = await self.conn.execute(
            "SELECT * FROM challenger_run ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in await cur.fetchall()]

    # -- the sampled online opinions ------------------------------------------------------------

    async def add_shadow_opinions(self, rows: list[tuple[Any, ...]]) -> None:
        """The sampled opinions, written in one `executemany`.

        The column order is fixed by the caller in `netcorenoc.shadow`, which is the one place that
        knows it — the same arrangement `capture.add_pairs` has, for the same reason.
        """
        if not rows:
            return
        await self.conn.executemany(
            "INSERT INTO shadow_opinion ("
            "challenger_run_id, alarm_a, alarm_b, situation_id, delta_t_s, class_affinity, "
            "entity_affinity, same_oid_root, score, linked, incumbent_linked, evaluated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )

    async def shadow_opinions(self, run_id: int | None = None) -> list[dict[str, Any]]:
        """The sampled opinions, oldest first. The online half of the skew test."""
        if run_id is None:
            cur = await self.conn.execute(
                "SELECT * FROM shadow_opinion ORDER BY id",
            )
        else:
            cur = await self.conn.execute(
                "SELECT * FROM shadow_opinion WHERE challenger_run_id = ? ORDER BY id", (run_id,)
            )
        return [dict(r) for r in await cur.fetchall()]

    async def shadow_skew_rows(self) -> list[dict[str, Any]]:
        """**The skew test's join**: each sampled online opinion beside what capture recorded.

        Two independent paths wrote these numbers. `shadow_opinion` holds the features **as
        served** — built in the engine from `LinkFeatures` at the instant the challenger scored —
        and `dataset_pair` holds what `capture.record` persisted from the same `LinkScore`'s terms.
        Nothing forces them to agree; a difference is a feature computed one way at serving time
        and another at training time, which is among the most common and most silent ways an ML
        system fails.

        `LEFT JOIN`, and `GROUP BY o.id`, because a pair can legitimately have no captured row —
        the sink's row cap may have collected it. Those are counted as **unmatched** and reported,
        never quietly dropped: an unmatched opinion is missing evidence, not agreement.
        """
        cur = await self.conn.execute(
            "SELECT o.id AS opinion_id, o.challenger_run_id, o.score AS online_score, "
            "       o.linked AS online_linked, o.alarm_a, o.alarm_b, "
            "       o.delta_t_s AS served_delta_t_s, o.class_affinity AS served_class_affinity, "
            "       o.entity_affinity AS served_entity_affinity, "
            "       p.delta_t_s, p.class_affinity, p.entity_affinity "
            "FROM shadow_opinion o "
            "LEFT JOIN dataset_pair p ON p.alarm_a = o.alarm_a AND p.alarm_b = o.alarm_b "
            "GROUP BY o.id ORDER BY o.id"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def shadow_stats(self) -> dict[str, Any]:
        """What shadow mode costs, in rows — the counterpart of `dataset_stats`."""
        out: dict[str, Any] = {}
        for table in ("challenger_run", "shadow_opinion"):
            cur = await self.conn.execute(f"SELECT COUNT(*) FROM {table}")  # nosec B608
            row = await cur.fetchone()
            assert row is not None
            out[table] = int(row[0])
        cur = await self.conn.execute(
            "SELECT MIN(evaluated_at), MAX(evaluated_at) FROM shadow_opinion"
        )
        row = await cur.fetchone()
        assert row is not None
        out["oldest"] = float(row[0]) if row[0] is not None else None
        out["newest"] = float(row[1]) if row[1] is not None else None
        return out

    async def prune_shadow(self, row_cap: int) -> int:
        """Bound `shadow_opinion` by a row cap, oldest-first — the correlator window's discipline.

        A row cap rather than an age bound: these rows grow with **traffic times sample rate**,
        not with labels, so time is the wrong bound: a quiet week and a storm produce wildly
        different volumes over the same days. Deletes nothing else, and can never reach a human
        label: this table holds no label and joins to none.
        """
        cur = await self.conn.execute("SELECT COUNT(*) FROM shadow_opinion")
        row = await cur.fetchone()
        assert row is not None
        excess = int(row[0]) - row_cap
        if excess <= 0:
            return 0
        cur = await self.conn.execute(
            "DELETE FROM shadow_opinion WHERE id IN ("
            "  SELECT id FROM shadow_opinion ORDER BY evaluated_at, id LIMIT ?"
            ") RETURNING id",
            (excess,),
        )
        return len(list(await cur.fetchall()))
