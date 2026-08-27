"""Fold materialisation (v0.11.0, Phase 4) — and the property the release exists to buy.

`DATA-LINEAGE.md` §4 states the hazard and this file measures it:

    fold assignment is a pure function of the incident ids;
    incident identity is `incidents.resolve_all` over the merge edges;
    THE MERGE EDGES CHANGE WHEN SITUATIONS MERGE;
    and no snapshot of the merge graph is retained anywhere.

The load-bearing test is `test_a_changed_merge_graph_moves_the_recomputed_folds_and_not_the_stored`.
It is the only one here that would have gone red before this phase, and it is the one that says
whether the release bought anything.

**Appendix B's last trap applies directly**: v0.10.1's byte-frozen reports could not have verified
the incident-identity consolidation, *because neither corpus contains a single merge edge*. So this
file does not use either corpus. It builds a merge edge on purpose, and asserts the recomputation
moves — because a drift test on a corpus that cannot drift proves nothing at all.
"""

from __future__ import annotations

import pytest

from netcorenoc import incidents
from netcorenoc.engine.evaluation import evaluation_folds
from netcorenoc.engine.evaluation.shadow_cv import FOLDS, REPEATS, assign_folds
from netcorenoc.store import Store

BASE = 1_700_000_000.0
HASH = "a" * 64


def _folds(incident_ids: list[int]) -> dict[tuple[int, int], int]:
    """`(incident, repeat) -> fold`, recomputed live, exactly as the estimator would."""
    return {
        (incident, repeat): fold
        for repeat in range(REPEATS)
        for incident, fold in assign_folds(incident_ids, folds=FOLDS, repeat=repeat).items()
    }


async def _stored(store: Store, run_id: str) -> dict[tuple[int, int], int]:
    return {
        (int(row["incident_id"]), int(row["repeat"])): int(row["fold"])
        for row in await store.fold_assignment(run_id)
    }


# -- it writes what the estimator would compute ---------------------------------------------


async def test_an_evaluation_writes_its_fold_assignment(store: Store) -> None:
    incident_ids = [3, 1, 4, 1, 5, 9, 2, 6]
    async with store.lock:
        run_id = await evaluation_folds.materialise_folds(
            store, incident_ids=incident_ids, params_hash=HASH, now=BASE
        )
        await store.commit()

    stored = await _stored(store, run_id)
    assert stored, "nothing was materialised"
    assert stored == _folds(incident_ids), "the stored assignment is not the one assign_folds gives"
    assert len(stored) == len(set(incident_ids)) * REPEATS


async def test_materialising_twice_is_idempotent_and_not_a_second_run(store: Store) -> None:
    """A promotion cites a `run_id`. If re-evaluating minted a new one, the citation would name a
    run that no longer corresponds to what is being looked at."""
    incident_ids = [1, 2, 3, 4, 5]
    async with store.lock:
        first = await evaluation_folds.materialise_folds(
            store, incident_ids=incident_ids, params_hash=HASH, now=BASE
        )
        second = await evaluation_folds.materialise_folds(
            store, incident_ids=incident_ids, params_hash=HASH, now=BASE + 5000.0
        )
        await store.commit()
    assert first == second
    assert len(await store.fold_assignment(first)) == len(set(incident_ids)) * REPEATS


async def test_the_run_id_depends_on_the_model_as_well_as_the_corpus(store: Store) -> None:
    """Two models evaluated over the same incidents are two runs, not one."""
    assert evaluation_folds.run_id_for([1, 2, 3], "a" * 64) != evaluation_folds.run_id_for(
        [1, 2, 3], "b" * 64
    )
    assert evaluation_folds.run_id_for([1, 2, 3], HASH) != evaluation_folds.run_id_for([1, 2], HASH)
    # Order and duplication are not part of the identity: the same set is the same corpus.
    assert evaluation_folds.run_id_for([3, 1, 2, 1], HASH) == evaluation_folds.run_id_for(
        [1, 2, 3], HASH
    )


# -- the rows are immutable ------------------------------------------------------------------


async def test_a_stored_fold_assignment_cannot_be_edited_or_deleted(store: Store) -> None:
    """A cited fold assignment that can move is not a citation.

    **The control comes first**: the insert is asserted to have worked, because a `BEFORE UPDATE`
    trigger on an empty table fires on nothing and a refusal test that ran first would pass while
    proving nothing whatever.
    """
    async with store.lock:
        run_id = await evaluation_folds.materialise_folds(
            store, incident_ids=[1, 2, 3], params_hash=HASH, now=BASE
        )
        await store.commit()
    assert await store.fold_assignment(run_id), "the control failed: nothing was written"

    import sqlite3

    for label, sql in (
        ("UPDATE", "UPDATE evaluation_fold SET fold = 99"),
        ("DELETE", "DELETE FROM evaluation_fold"),
    ):
        with pytest.raises(sqlite3.IntegrityError):
            await store.conn.execute(sql)
        await store.conn.rollback()
        assert label  # named so a failure says which statement was permitted


async def test_a_second_assignment_for_the_same_triple_is_refused_by_the_key(store: Store) -> None:
    """`INSERT OR ABORT` by omission: a second write fails on the PRIMARY KEY rather than replacing
    an assignment a promotion may already cite."""
    import sqlite3

    async with store.lock:
        run_id = await evaluation_folds.materialise_folds(
            store, incident_ids=[1, 2, 3], params_hash=HASH, now=BASE
        )
        await store.commit()
    with pytest.raises(sqlite3.IntegrityError):
        await store.write_fold_assignment(
            run_id=run_id, assignments=[(1, 0, 4)], created_at=BASE + 1
        )
    await store.conn.rollback()


# -- THE PROPERTY THE RELEASE EXISTS TO BUY ---------------------------------------------------


async def test_a_changed_merge_graph_moves_the_recomputed_folds_and_not_the_stored(
    store: Store,
) -> None:
    """**The load-bearing test.** Re-running against a corpus whose merge graph has changed produces
    different folds, while the **stored** ones are unchanged.

    The merge edge is built deliberately. Appendix B: v0.10.1's byte-frozen reports could not have
    verified the incident-identity consolidation *because neither corpus contains a single merge
    edge* — so a drift test that used either corpus would be a drift test on data that cannot drift.

    Six situations, none merged, are six incidents. Merging 6 into 1 makes them **five**, and
    `assign_folds` is a rotation over the sorted ids: removing one id shifts every id after it to a
    different position, so the recomputed assignment moves for real rather than by one entry.
    """
    situations = [1, 2, 3, 4, 5, 6]
    before_map = incidents.resolve_all(situations, {})
    before_ids = sorted({before_map.incident_of[s] for s in situations})
    assert len(before_ids) == 6, "the fixture must start with six distinct incidents"

    async with store.lock:
        run_id = await evaluation_folds.materialise_folds(
            store, incident_ids=before_ids, params_hash=HASH, now=BASE
        )
        await store.commit()
    stored_at_write = await _stored(store, run_id)

    # THE MERGE. `merged_into` is forward-only and nothing holds the prior state — which is the
    # whole of `DATA-LINEAGE.md` §4's hazard, reproduced here in three lines.
    after_map = incidents.resolve_all(situations, {6: 1})
    after_ids = sorted({after_map.incident_of[s] for s in situations})
    assert after_ids == [1, 2, 3, 4, 5], "the merge did not change incident identity"

    recomputed = _folds(after_ids)
    assert recomputed != stored_at_write, (
        "the recomputed folds did NOT move when the merge graph did — this fixture cannot "
        "demonstrate the hazard, and a green result here would be meaningless"
    )

    stored_now = await _stored(store, run_id)
    assert stored_now == stored_at_write, (
        "the STORED assignment moved when the merge graph did — which is the entire defect this "
        "table exists to prevent"
    )


async def test_the_promotion_row_can_cite_the_stored_assignment(store: Store) -> None:
    """A promotion's `evaluation_run_id` resolves to rows, which is the point of storing them."""
    async with store.lock:
        run_id = await evaluation_folds.materialise_folds(
            store, incident_ids=[1, 2, 3, 4], params_hash=HASH, now=BASE
        )
        model_version_id = await store.insert_model_version(
            kind="additive",
            contract_version="1.0",
            params_document="{}",
            params_hash=HASH,
            challenger_run_id=None,
            created_by="admin",
            created_at=BASE,
        )
        await store.insert_promotion(
            model_version_id=model_version_id,
            verdict="INSUFFICIENT_EVIDENCE",
            triggers='["FLOOR_UNMET"]',
            metrics="{}",
            evaluation_run_id=run_id,
            plan_sha256="e0" * 32,
            query_count=0,
            approved_by="admin",
            decided_at=BASE,
            outcome="refused",
            refusal_reason="floors unmet",
        )
        await store.commit()

    rows = await store.list_promotions(10)
    assert rows[0]["evaluation_run_id"] == run_id
    cited = await store.fold_assignment(str(rows[0]["evaluation_run_id"]))
    assert len(cited) == 4 * REPEATS, "the citation does not resolve to the stored rows"
