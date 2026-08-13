"""F48 — the `source = 'server'` predicate, demonstrated in all three places it appears.

v0.9.2 repaired F46 by reconciling the client's reported marks against the server's own bag. That
reconciliation is expressed **three times**: in the live path (`Exclusion.marked_positions`), in
migration `0011`'s backfill, and in `Store.reconciliation_drift`. Only the first was probed
adversarially — removing `AND m.source = 'server'` from either of the other two leaves the whole
suite green.

The case is not hostile. A browser whose view is stale reports a member the bag no longer holds and
marks it; the v0.7.5 SSE teardown produced exactly this, routinely. Verified non-equivalent: the
live path records `excluded_reconciled = 0` where an unfiltered join records 1.

Every test carries a control that must behave the other way, so neither "everything reconciles to
zero" nor "everything reconciles to the report" can be mistaken for the property.
"""

from __future__ import annotations

import re
from pathlib import Path

from netcorenoc.rootcause import Member
from netcorenoc.store import Store

import authutil

TS = 1_700_000_000.0

MIGRATION_0011 = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "netcorenoc"
    / "migrations"
    / "0011_evidence_boundary.sql"
)

# The backfill expression of migration `0011`, pinned here. The migration cannot be re-run against
# an already-migrated database, so its arithmetic is exercised directly and asserted to agree with
# the live path.
#
# **F49, repaired in v0.10.1: the constant is now TIED to the file.**
# `test_the_pinned_expression_is_the_migrations_own` normalises whitespace and asserts this text is
# a substring of `0011_evidence_boundary.sql`. Until then nothing connected the two, and v0.10.0
# measured the consequence: deleting `AND m.source = 'server'` from the migration itself left
# **1093 tests passing**, because this constant kept its copy and the upgrade fixture had no client
# fingerprint for the predicate to exclude.
BACKFILL = """
SELECT COUNT(DISTINCT x.alarm_id) FROM feedback_exclusion x
  JOIN feedback_member m ON m.feedback_id = x.feedback_id
   AND m.alarm_id = x.alarm_id AND m.source = 'server'
 WHERE x.feedback_id = ?
"""
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
        cur = await store.conn.execute(
            "INSERT INTO device (ip, first_seen, last_seen) VALUES ('10.5.0.1', ?, ?) RETURNING id",
            (TS, TS),
        )
        dev = int((await cur.fetchone())[0])  # type: ignore[index]
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
                "INSERT INTO alarm (device_id, class_id, ne_id, instance, status, first_seen, "
                "last_seen, count) VALUES (?, ?, ?, '', 'active', ?, ?, 1) RETURNING id",
                (dev, cls, ne, TS, TS),
            )
            triples.append((int((await cur.fetchone())[0]), cls, dev))  # type: ignore[index]
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


async def test_the_backfill_expression_reconciles_against_the_server_bag_only(
    store: Store,
) -> None:
    """Losing the predicate here lets the client's own report reconcile itself — F46,
    re-entering."""
    fid = await _stale_view_label(store)
    backfill = int((await (await store.conn.execute(BACKFILL, (fid,))).fetchone())[0])  # type: ignore[index]
    unfiltered = int((await (await store.conn.execute(UNFILTERED, (fid,))).fetchone())[0])  # type: ignore[index]
    cur = await store.conn.execute("SELECT excluded_reconciled FROM feedback WHERE id=?", (fid,))
    live = int((await cur.fetchone())[0])  # type: ignore[index]

    assert unfiltered == 1, (
        "CONTROL: the unfiltered join must differ on this fixture, or the test proves nothing"
    )
    assert backfill == live == 0, "the backfill expression must agree with the live path"


# --- F49: the pin is tied to the file it pins ----------------------------------------------------


def _normalised(sql: str) -> str:
    """Collapse whitespace so an indentation change in either place is not a drift."""
    return re.sub(r"\s+", " ", sql).strip()


def test_the_pinned_expression_is_the_migrations_own() -> None:
    """**F49.** The constant above and migration `0011` are now one thing, not two copies.

    A migration cannot be re-run against an already-migrated database, so its arithmetic has to be
    pinned somewhere in order to be exercised at all. Nothing tied the pin to the file, and v0.10.0
    measured what that costs: **deleting `AND m.source = 'server'` from
    `0011_evidence_boundary.sql` left 1093 tests passing.** The pinned copy kept the predicate and
    went on asserting a property the shipped migration no longer had.

    Compared up to the `WHERE`, because that is the only clause that legitimately differs: the test
    binds a parameter (`= ?`) where the migration correlates to the row it is updating
    (`= feedback.id`). **Everything F49 is about — the join and its `source = 'server'` predicate —
    is inside the compared span.**
    """
    migration = _normalised(MIGRATION_0011.read_text(encoding="utf-8"))
    pinned = _normalised(BACKFILL)
    shared = pinned.split(" WHERE ", 1)[0]
    assert "AND m.source = 'server'" in shared, (
        "the compared span must CONTAIN the predicate, or this test ties nothing that matters"
    )
    assert shared in migration, (
        "the pinned backfill expression has drifted from 0011_evidence_boundary.sql.\n"
        f"  pinned:    {shared}\n"
        "  Whichever moved, the other is now asserting a property the database does not have."
    )


def test_the_tie_would_notice_a_predicate_deleted_from_the_migration() -> None:
    """**CONTROL for the test above**, run against a mutated copy of the migration in memory.

    A substring assertion that cannot fail proves nothing, and the obvious control here is the wrong
    one: `UNFILTERED`'s join *is* a substring of the migration, because deleting a trailing clause
    leaves a **prefix** and every prefix matches. Asking that question would have produced a control
    that fails on a healthy tree.

    The question worth asking is the one F49 is actually about: **if the predicate is deleted from
    `0011_evidence_boundary.sql`, does the pin go red?** So the deletion is performed here, on a
    copy, and the tie's own assertion is evaluated against it.

    The predicate string occurs **exactly once** in the file — in the `UPDATE`, not in its prose —
    which is asserted first, because a mutation that removed two occurrences would be testing
    something else.

    **The honest limitation**: a substring tie is directional. It detects a *deletion* from the
    migration, which is the measured failure mode. It would not detect an *addition* elsewhere in
    the file that changed the backfill's meaning without touching this span.
    """
    text = MIGRATION_0011.read_text(encoding="utf-8")
    assert text.count("AND m.source = 'server'") == 1, (
        "the predicate appears more than once; this control mutates all of them and would then be "
        "measuring a different deletion from the one F49 is about"
    )
    mutated = _normalised(text.replace("AND m.source = 'server'", ""))
    shared = _normalised(BACKFILL).split(" WHERE ", 1)[0]
    assert shared not in mutated, (
        "the pinned expression still matched a migration with the predicate deleted; the tie "
        "cannot detect the exact defect F49 was issued for, and is therefore not a tie"
    )
