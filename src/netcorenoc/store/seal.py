"""The sealed holdout's SQL: construct once, ratify, read through the log, count the queries.

**Every method here is a scope bypass by construction**, exactly as `store/shadow.py`'s are, and no
route below `admin` may reach any of them — v0.10.0 adds no route at all, which is the strongest
form of that.

**Nothing here decides anything.** Whether a read is permitted, what the seal contains and how it is
cut are `netcorenoc.seal`. This module is SQL, plus the two refusals SQLite itself enforces.

**No method here takes ``store.lock``**, which is this package's contract for callers
(`tests/test_store_concurrency.py` is the control).

## The one thing this module does that the others do not

`sealed_incident_ids` is the **only** way the sealed membership can be read, and the access path
above it refuses unless the caller presents a plan hash already on record. That is not a convenience
wrapper over a `SELECT`: there is no unguarded read of `holdout_seal_member` anywhere in this
package, so a caller who wants the sealed ids has no expression available except the one that logs.
"""

from __future__ import annotations

from typing import Any

from netcorenoc.store.base import StoreBase

__all__ = ["SealMixin"]


class SealMixin(StoreBase):
    # -- construction ---------------------------------------------------------------------------

    async def seal_row(self) -> dict[str, Any] | None:
        """The seal, or `None` if none has been constructed. **Carries no membership.**

        Deliberately: this is the read a report needs — when it was cut, how many incidents, over
        what corpus, under which plan — and none of that discloses *which* incidents. A report that
        wanted the membership would have to call :meth:`sealed_incident_ids`, which logs.
        """
        cur = await self.conn.execute("SELECT * FROM holdout_seal LIMIT 1")
        row = await cur.fetchone()
        return dict(row) if row is not None else None

    async def construct_seal(
        self,
        *,
        release: str,
        plan_sha256: str,
        created_at: float,
        digest: str,
        incident_ids: list[int],
        corpus_incidents: int,
    ) -> int:
        """Write the seal and its membership. **Raises on a second construction.**

        The refusal comes from the schema — `holdout_seal.singleton` is `UNIQUE` and pinned to 1 —
        so it holds against this method, against a later method, and against anyone with a SQLite
        prompt. This method does not check first and then insert: it inserts, and the database says
        no. A check-then-act would be a race and, worse, a rule a later caller could forget.
        """
        cur = await self.conn.execute(
            "INSERT INTO holdout_seal (release, plan_sha256, created_at, digest, "
            "incident_count, corpus_incidents) VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
            (release, plan_sha256, created_at, digest, len(incident_ids), corpus_incidents),
        )
        row = await cur.fetchone()
        assert row is not None
        seal_id = int(row[0])
        await self.conn.executemany(
            "INSERT INTO holdout_seal_member (seal_id, position, incident_id) VALUES (?, ?, ?)",
            [(seal_id, position, incident) for position, incident in enumerate(incident_ids)],
        )
        return seal_id

    # -- the access log -------------------------------------------------------------------------

    async def record_access(
        self,
        *,
        at: float,
        release: str,
        plan_sha256: str | None,
        purpose: str,
        granted: bool,
        outcome: str,
    ) -> None:
        """Append one access row. **Refusals are recorded too.**

        `granted = 0` is the row that makes the log answer *"how many times has somebody tried"*
        rather than only *"how many times did it work"*, and it is the only trace a missing-plan
        refusal leaves anywhere.
        """
        await self.conn.execute(
            "INSERT INTO holdout_access (at, release, plan_sha256, purpose, granted, outcome) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (at, release, plan_sha256, purpose, 1 if granted else 0, outcome),
        )

    async def ratified_plan_hashes(self) -> set[str]:
        """Every plan hash on record: the seal's own, plus any recorded by a `ratify` row.

        **The seal's own hash counts** because the plan that authorised cutting the seal is by
        construction a ratified plan — it had to exist before the seal did.

        A later release records its plan with :meth:`ratify_plan` **before** reading, and the row is
        append-only and timestamped, so "registered before the read" is a durable fact rather than
        a claim. Nothing here can stop a determined author registering and then reading in the same
        minute; what it can do, and does, is make that sequence permanently visible.
        """
        hashes: set[str] = set()
        cur = await self.conn.execute("SELECT plan_sha256 FROM holdout_seal")
        hashes.update(str(r[0]) for r in await cur.fetchall())
        cur = await self.conn.execute(
            "SELECT plan_sha256 FROM holdout_access "
            "WHERE purpose = 'ratify' AND granted = 1 AND plan_sha256 IS NOT NULL"
        )
        hashes.update(str(r[0]) for r in await cur.fetchall())
        return hashes

    async def query_count(self) -> int:
        """**How many times the sealed membership has actually been read.**

        Granted reads only, and `ratify` rows excluded: registering a plan is not spending the
        holdout. `PREREGISTRATION-0.10.0.md` §4.3(5) requires this to be **0** for v0.10.0 and
        §4.3(4) requires it to be printed beside every holdout number ever published.
        """
        cur = await self.conn.execute(
            "SELECT COUNT(*) FROM holdout_access WHERE granted = 1 AND purpose <> 'ratify'"
        )
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])

    async def access_log(self, limit: int = 100) -> list[dict[str, Any]]:
        """The log, oldest first. Read by the report; never used in a decision."""
        cur = await self.conn.execute("SELECT * FROM holdout_access ORDER BY id LIMIT ?", (limit,))
        return [dict(r) for r in await cur.fetchall()]

    # -- the guarded read -----------------------------------------------------------------------

    async def sealed_incident_ids(self, *, plan_sha256: str) -> list[int]:
        """The sealed membership. **The only expression in this package that returns it.**

        Callers do not reach this directly — `netcorenoc.seal.spend` is the access path, and it is
        what enforces the plan-hash requirement and writes the log row. This method is deliberately
        *not* named `select_...`, carries a keyword-only `plan_sha256` it does not itself validate,
        and exists in a module the estimator does not import.

        The reason the check lives one layer up rather than here: the refusal has to be **logged**,
        and a store method that both refused and logged would be making a policy decision inside the
        SQL layer, which is the seam this package has kept clean since v0.7.3.
        """
        cur = await self.conn.execute(
            "SELECT m.incident_id FROM holdout_seal_member m "
            "JOIN holdout_seal s ON s.id = m.seal_id AND s.plan_sha256 IS NOT NULL "
            "ORDER BY m.position"
        )
        _ = plan_sha256  # validated by `seal.spend`; named here so the contract is visible
        return [int(r[0]) for r in await cur.fetchall()]

    # -- the asserting-bag census ---------------------------------------------------------------

    async def asserting_bag_rows(self) -> list[dict[str, Any]]:
        """Every labelled bag that carries an assertion, with what §2.2 needs to judge it.

        Here rather than in `store/shadow.py` because it is read by the **judge** — the thing that
        decides whether the evidence suffices — and not by the training join. `store/shadow.py` owns
        *what the challenger read and wrote back*; this owns *what the seal and the judge are
        allowed to count*, which is the same seam `0011` drew between the report and the learner.

        `excluded_reconciled`, never `excluded_count` (F46): every threshold counts the quantity the
        **server** derived. `capture_provenance = 'current'` because `legacy_capture` rows were
        acquired over the path v0.7.5 repaired and are of *unknown* quality.
        """
        cur = await self.conn.execute(
            "SELECT f.id AS feedback_id, f.situation_id, f.verdict, "
            "       COALESCE(f.coverage, 'unrecorded') AS coverage, "
            "       COALESCE(f.member_count, 0) AS member_count, "
            "       f.excluded_reconciled, f.excluded_reconciled_out_of_scope, "
            "       f.scope_redacted_members, f.excluded_truncated, f.capture_provenance "
            "FROM feedback f "
            "WHERE f.verdict = 'split' AND f.excluded_reconciled >= 1 "
            "  AND f.capture_provenance = 'current' "
            "ORDER BY f.id"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def unknown_scope_rows(self) -> int:
        """Labels written before `0011`, whose scope is **permanently uninterpretable**.

        §2.3: the `unknown` population is excluded, counted, and reported. It may not be assumed
        clean and may not swell a denominator, and `NULL` means unknown **forever** — never zero.
        """
        cur = await self.conn.execute(
            "SELECT COUNT(*) FROM feedback WHERE scope_redacted_members IS NULL"
        )
        row = await cur.fetchone()
        assert row is not None
        return int(row[0])
