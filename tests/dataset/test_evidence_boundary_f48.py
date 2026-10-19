"""F48 — the `source = 'server'` predicate, demonstrated in all three places it appears.

v0.9.2 repaired F46 by reconciling the client's reported marks against the server's own bag. That
reconciliation was expressed **three times**: in the live path (`Exclusion.marked_positions`), in
migration `0011`'s backfill, and in `Store.reconciliation_drift`. Only the first was probed
adversarially — removing `AND m.source = 'server'` from either of the other two leaves the whole
suite green.

**Since ADR #452 the backfill is gone**: the schema is the PostgreSQL baseline `0031`, which has no
backfill to run — a v0.30.0 SQLite database already carries the counts `0011` derived, and
`db import-sqlite` copies them verbatim. The test of the backfill expression now drives the same
join where the store still ships it, `Store.reconciled_marks` (the marked set the promotion judge
reads, v0.16.1). The two F49 tests that tied the pinned copy of the backfill to
`0011_evidence_boundary.sql` were deleted with that file: there is no migration left to pin, and
the store's own SQL is exercised directly.

The case is not hostile. A browser whose view is stale reports a member the bag no longer holds and
marks it; the v0.7.5 SSE teardown produced exactly this, routinely. Verified non-equivalent: the
live path records `excluded_reconciled = 0` where an unfiltered join records 1.

Every test carries a control that must behave the other way, so neither "everything reconciles to
zero" nor "everything reconciles to the report" can be mistaken for the property.
"""

from __future__ import annotations

from netcorenoc.engine.correlate.rootcause import Member
from netcorenoc.store import Store

import authutil

TS = 1_700_000_000.0

# The reconciliation's join WITHOUT the predicate: the control that proves the stale-view fixture
# tells a filtered expression from an unfiltered one.
UNFILTERED = """
SELECT COUNT(DISTINCT x.alarm_id) FROM feedback_exclusion x
  JOIN feedback_member m ON m.feedback_id = x.feedback_id
   AND m.alarm_id = x.alarm_id
 WHERE x.feedback_id = ?
"""


async def _stale_view_label(store: Store) -> int:
    """One label from a client whose view is one alarm behind the server's. Returns its id."""
    engine, _q, app = await authutil.make_env(store)
    triples: list[tuple[int, int, int]] = []
    async with store.lock:
        # The element (`ne`; the `device` copy of it is gone, ADR #452).
        cur = await store.conn.execute(
            "INSERT INTO ne (ip, first_seen, last_seen) VALUES ('10.5.0.1', ?, ?) RETURNING id",
            (TS, TS),
        )
        ne = int((await cur.fetchone())[0])  # type: ignore[index]
        for k in range(5):
            cur = await store.conn.execute(
                "INSERT INTO alarm_class (oid, first_seen, last_seen) "
                "VALUES (?, ?, ?) RETURNING id",
                (f"1.3.6.1.4.1.9.5.{k}", TS, TS),
            )
            cls = int((await cur.fetchone())[0])  # type: ignore[index]
            cur = await store.conn.execute(
                "INSERT INTO alarm (class_id, ne_id, instance, status, first_seen, "
                "last_seen, count) VALUES (?, ?, '', 'active', ?, ?, 1) RETURNING id",
                (cls, ne, TS, TS),
            )
            triples.append((int((await cur.fetchone())[0]), cls, ne))  # type: ignore[index]
        sid = await store.create_situation(TS, None)
        for aid, _c, _d in triples[:3]:  # the SERVER's bag is the first three members
            await store.add_alarm_to_situation(sid, aid)
        await store.commit()
    engine.members[sid] = [Member(*t, TS) for t in triples[:3]]

    stale = triples[3][0]  # a real alarm, and not a member of this situation
    client = await authutil.client_as(app, "editor")
    try:
        resp = await client.post(
            f"/api/situations/{sid}/feedback",
            json={
                "verdict": "split",
                "member_ids": [t[0] for t in triples[:3]] + [stale],
                "excluded_ids": [stale],
            },
        )
    finally:
        await client.aclose()
    assert resp.status_code == 200, resp.status_code
    cur = await store.conn.execute("SELECT id FROM feedback ORDER BY id DESC LIMIT 1")
    return int((await cur.fetchone())[0])  # type: ignore[index]


async def test_the_live_path_reconciles_against_the_server_bag_only(store: Store) -> None:
    """CONTROL, and the half v0.9.2 already tested. The client's report may not validate itself."""
    fid = await _stale_view_label(store)
    cur = await store.conn.execute(
        "SELECT excluded_count, excluded_reconciled FROM feedback WHERE id=?", (fid,)
    )
    row = dict(await cur.fetchone())  # type: ignore[arg-type]
    assert row["excluded_count"] == 1, "CONTROL: the report is still recorded verbatim (tier 1)"
    assert row["excluded_reconciled"] == 0, (
        "the marked id is in the CLIENT's reported bag but not the server's, so it asserts nothing"
    )


async def test_the_drift_check_reconciles_against_the_server_bag_only(store: Store) -> None:
    """Losing the predicate here manufactures a drift alarm on a healthy row."""
    await _stale_view_label(store)
    assert await store.reconciliation_drift() == [], (
        "the drift check disagreed with the live path on a stale-view label, which would send an "
        "operator hunting a write-path defect that does not exist"
    )


async def test_the_reconciled_set_reconciles_against_the_server_bag_only(
    store: Store,
) -> None:
    """Losing the predicate here lets the client's own report reconcile itself — F46,
    re-entering.

    Was the backfill expression of `0011` (see the module docstring); `reconciled_marks` is the
    same join, and the set the promotion judge reads as what the operator marked.
    """
    fid = await _stale_view_label(store)
    reconciled = await store.reconciled_marks(fid)
    unfiltered = int((await (await store.conn.execute(UNFILTERED, (fid,))).fetchone())[0])  # type: ignore[index]
    cur = await store.conn.execute("SELECT excluded_reconciled FROM feedback WHERE id=?", (fid,))
    live = int((await cur.fetchone())[0])  # type: ignore[index]

    assert unfiltered == 1, (
        "CONTROL: the unfiltered join must differ on this fixture, or the test proves nothing"
    )
    assert reconciled == [], "the marked id is a member of the CLIENT's bag only"
    assert len(reconciled) == live == 0, "the reconciled set must agree with the live path"
