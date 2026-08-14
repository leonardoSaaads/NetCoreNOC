"""Champion/challenger storage: the artefact, the event, and the fold assignment they cite.

v0.11.0, migration `0013`. **Nothing here decides anything.** Whether a promotion is justified is
`netcorenoc.promotion`; what a parameter document may contain is `netcorenoc.model_version`. This
module is SQL, plus the refusals SQLite itself enforces.

**No method here takes ``store.lock``**, which is this package's contract for callers
(`tests/test_store_concurrency.py` is the control).

## The two things this module is careful about

**The active pointer moves through one method, and that method nulls the other side.** The schema's
`CHECK` makes two live pointers impossible, but a caller that set one without clearing the other
would get an `IntegrityError` at a moment when it was half-way through a promotion. So
:meth:`set_active_model_version` and the pre-existing `set_active_scorer_config` each write **both**
columns, and the `CHECK` is the second line of defence rather than the first.

**A refusal is written by the same method as an application.** One `INSERT`, one column deciding
which it was. Two methods would be two places to forget the audit row, and *"the refusal path
writes no row"* is a defect this release exists to make impossible.
"""

from __future__ import annotations

from typing import Any

from netcorenoc.store.base import StoreBase

__all__ = ["PromotionMixin"]


class PromotionMixin(StoreBase):
    # -- the artefact ---------------------------------------------------------------------------

    async def insert_model_version(
        self,
        *,
        kind: str,
        contract_version: str,
        params_document: str,
        params_hash: str,
        challenger_run_id: int | None,
        created_by: str | None,
        created_at: float,
        note: str = "",
    ) -> int:
        """Append one immutable model version. Never UPDATE, never DELETE (triggers abort)."""
        cur = await self.conn.execute(
            "INSERT INTO model_version (kind, contract_version, params_document, params_hash, "
            "challenger_run_id, created_by, created_at, note) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (
                kind,
                contract_version,
                params_document,
                params_hash,
                challenger_run_id,
                created_by,
                created_at,
                note,
            ),
        )
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    async def get_model_version(self, model_version_id: int) -> dict[str, Any] | None:
        cur = await self.conn.execute("SELECT * FROM model_version WHERE id=?", (model_version_id,))
        row = await cur.fetchone()
        return dict(row) if row else None

    async def list_model_versions(self, limit: int) -> list[dict[str, Any]]:
        """The immutable artefact history, newest first, with the active one flagged."""
        cur = await self.conn.execute(
            "SELECT m.*, (a.model_version_id IS NOT NULL) AS active FROM model_version m "
            "LEFT JOIN scorer_active a ON a.model_version_id = m.id AND a.id = 1 "
            "ORDER BY m.id DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def active_model_version(self) -> dict[str, Any] | None:
        """The model version the one-row pointer names, or None if it names a `scorer_config`.

        Read at the engine's configuration reload point — never per packet, never per candidate
        pair, and never in ``receiver.datagram_received`` (prime directive 2).
        """
        cur = await self.conn.execute(
            "SELECT m.* FROM scorer_active a JOIN model_version m ON m.id = a.model_version_id "
            "WHERE a.id = 1"
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def set_active_model_version(
        self, model_version_id: int, activated_by: str | None, ts: float
    ) -> bool:
        """Point the single active row at a model version, **clearing `config_id` in the same
        statement**.

        Returns False if the artefact does not exist, so a caller cannot activate a dangling id —
        the foreign key would refuse it anyway, but refusing here means the caller gets a decision
        rather than an exception mid-transaction.
        """
        cur = await self.conn.execute("SELECT 1 FROM model_version WHERE id=?", (model_version_id,))
        if await cur.fetchone() is None:
            return False
        await self.conn.execute(
            "INSERT INTO scorer_active (id, config_id, model_version_id, activated_by, "
            "activated_at) VALUES (1, NULL, ?, ?, ?) "
            "ON CONFLICT (id) DO UPDATE SET config_id=NULL, "
            "model_version_id=excluded.model_version_id, activated_by=excluded.activated_by, "
            "activated_at=excluded.activated_at",
            (model_version_id, activated_by, ts),
        )
        return True

    # -- the event ------------------------------------------------------------------------------

    async def insert_promotion(
        self,
        *,
        model_version_id: int,
        verdict: str,
        triggers: str,
        metrics: str,
        evaluation_run_id: str | None,
        plan_sha256: str,
        query_count: int,
        approved_by: str,
        decided_at: float,
        outcome: str,
        refusal_reason: str | None,
        unavailable: str = "[]",
    ) -> int:
        """Append one promotion decision. **Applied and refused go through this same method.**

        Two methods would be two places to forget a column, and a refusal that recorded less than
        an application is the failure mode `PREREGISTRATION-0.11.0.md` §2 exists to prevent: *a
        table of successes answers "what is deployed" and not "what has this appliance been asked
        to deploy"*.
        """
        cur = await self.conn.execute(
            "INSERT INTO promotion (model_version_id, verdict, triggers, metrics, "
            "evaluation_run_id, plan_sha256, query_count, approved_by, decided_at, outcome, "
            "refusal_reason, unavailable) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (
                model_version_id,
                verdict,
                triggers,
                metrics,
                evaluation_run_id,
                plan_sha256,
                query_count,
                approved_by,
                decided_at,
                outcome,
                refusal_reason,
                unavailable,
            ),
        )
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    async def list_promotions(self, limit: int) -> list[dict[str, Any]]:
        """Every decision, newest first — **refusals included**, which is the whole point."""
        cur = await self.conn.execute("SELECT * FROM promotion ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in await cur.fetchall()]

    # -- the fold assignment --------------------------------------------------------------------

    async def write_fold_assignment(
        self, *, run_id: str, assignments: list[tuple[int, int, int]], created_at: float
    ) -> int:
        """Materialise one evaluation's fold assignment. `assignments` is (incident, repeat, fold).

        `INSERT OR ABORT` by omission: a second write for the same `(run_id, incident_id, repeat)`
        fails on the PRIMARY KEY rather than replacing an assignment a promotion may already cite.
        `DATA-LINEAGE.md` §4 is why this table exists at all — fold assignment is a pure function of
        the incident ids, incident identity moves when situations merge, and **no snapshot of the
        merge graph is retained anywhere**.
        """
        await self.conn.executemany(
            "INSERT INTO evaluation_fold (run_id, incident_id, repeat, fold, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (run_id, incident, repeat, fold, created_at)
                for incident, repeat, fold in assignments
            ],
        )
        return len(assignments)

    async def fold_assignment(self, run_id: str) -> list[dict[str, Any]]:
        """One evaluation's **stored** fold assignment, in a stable order.

        The order is `(repeat, incident_id)` rather than insertion order, so two readers of the same
        run see the same sequence regardless of how it was written — the property that lets a
        citation be compared against a recomputation.
        """
        cur = await self.conn.execute(
            "SELECT incident_id, repeat, fold FROM evaluation_fold WHERE run_id=? "
            "ORDER BY repeat, incident_id",
            (run_id,),
        )
        return [dict(r) for r in await cur.fetchall()]
