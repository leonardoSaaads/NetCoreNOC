"""What a bag is once its membership moves. **`PREREGISTRATION-0.16.1.md`, enforced.**

F90 and F89 are one question from two sides, and this file is where the amendment's answer stops
being prose. Every test below is a reading of one of its four decisions:

* **§1 — a label is judged against the membership captured at the gesture.** The bag is
  `feedback_member(source='server')` and the marked set is `feedback_exclusion` reconciled against
  it. Two tests, and each carries the control that makes it a probe rather than a coincidence.
* **§2 — a bag's identity is `(situation_id, verdict, bag_key)` over the member SET.** Two
  gestures on one situation are two labels when the bag differs and one when it does not, so F36's
  measured bound is asserted here too — in the same file, because a release that repaired one by
  losing the other would pass a suite that tested them apart.
* **The compensating control.** `bag_key` is derived by the store from `situation_alarm` while the
  snapshot is written by the engine from `server_bag`, so *"the two can never disagree"* is a claim
  that has to be checked rather than asserted in a docstring — and it is checked over the real
  write path, on a bag the correlator actually formed.

**Why the marked set is `[7, 8]` and not `[1, 2]` in the probes.** The defect reconstructed the
marked set as `members[:n]`, so a bag marked LOW reconstructs correctly by accident. The control is
that case: it must agree both before and after the repair. Without it a green treatment could not
distinguish a repaired judge from a broken probe — the trap Appendix B names and F53 is the
instance of.
"""

from __future__ import annotations

from pathlib import Path

from netcorenoc.engine.dataset.labels import member_digest
from netcorenoc.engine.evaluation.promotion_metrics import _asserting_bags, _hidden
from netcorenoc.store import Store
from netcorenoc.store.feedback import UNKEYED_BAG, bag_key

import authutil
import util

BASE = 1_700_000_000.0
#: Above `PREREGISTRATION-0.16.0.md` §4's registered floor of 0.50, so a gesture produces a label.
SURE = 0.8
_NEXT = [0]


async def _situation(store: Store, n: int) -> tuple[int, list[int]]:
    """One situation of `n` members, each on its own device, NE and class.

    Hand-built rather than replayed, and only here: these tests are about the **reader** of a
    stored label, so the bag has to be an exact, chosen shape — eight members whose marked pair is
    high in the order. The tests that assert about the write path drive real traps instead.
    """
    alarms: list[int] = []
    sid = await store.create_situation(BASE, None)
    for _ in range(n):
        _NEXT[0] += 1
        u = _NEXT[0]
        ip = f"10.1.{u // 250}.{u % 250 + 1}"
        cur = await store.conn.execute(
            "INSERT INTO device (ip, first_seen, last_seen) VALUES (?, ?, ?) RETURNING id",
            (ip, BASE, BASE),
        )
        device = int((await cur.fetchone())[0])  # type: ignore[index]
        cur = await store.conn.execute(
            "INSERT INTO ne (ip, first_seen, last_seen) VALUES (?, ?, ?) RETURNING id",
            (ip, BASE, BASE),
        )
        ne = int((await cur.fetchone())[0])  # type: ignore[index]
        cur = await store.conn.execute(
            "INSERT INTO alarm_class (oid, first_seen, last_seen) VALUES (?, ?, ?) RETURNING id",
            (f"1.3.6.1.4.1.99.{u}", BASE, BASE),
        )
        cls = int((await cur.fetchone())[0])  # type: ignore[index]
        cur = await store.conn.execute(
            "INSERT INTO alarm (device_id, class_id, ne_id, instance, severity, status, "
            "first_seen, last_seen) VALUES (?, ?, ?, '', 3, 'active', ?, ?) RETURNING id",
            (device, cls, ne, BASE, BASE),
        )
        alarm = int((await cur.fetchone())[0])  # type: ignore[index]
        await store.conn.execute(
            "INSERT INTO situation_alarm (situation_id, alarm_id) VALUES (?, ?)", (sid, alarm)
        )
        alarms.append(alarm)
    return sid, alarms


async def _asserting_label(
    store: Store, sid: int, bag: list[int], marked: list[int], *, redacted: int = 0, blind: int = 0
) -> int:
    """One `split` carrying `marked`, in the shape `labels.record_label` writes.

    Written through the store rather than over HTTP for the reason `_situation` is hand-built: the
    subject is the reader, and the writer's own path is covered by the tests below that use it.
    """
    recorded = await store.add_feedback(sid, "split", BASE, principal_ref="u:1", role="editor")
    assert recorded.id is not None
    await store.add_feedback_members(recorded.id, "server", bag)
    await store.add_feedback_exclusion(recorded.id, marked)
    await store.annotate_feedback(
        recorded.id,
        member_digest=member_digest(bag),
        member_count=len(bag),
        acquisition_channel="move",
        capture_provenance="current",
        coverage="full",
        coverage_found=len(bag) * (len(bag) - 1) // 2,
        coverage_expected=len(bag) * (len(bag) - 1) // 2,
        scope_redacted_members=redacted,
        excluded_count=len(marked),
        excluded_reconciled=len(marked),
        excluded_reconciled_source="live",
        excluded_reconciled_out_of_scope=blind,
        excluded_truncated=0,
    )
    return recorded.id


# --- §1: the marked set is what the operator marked (F90) -----------------------------------


async def test_the_judge_reads_the_ids_the_operator_marked_and_not_a_prefix(store: Store) -> None:
    """**F90's treatment.** A bag of eight, marked high; the judge must read the marked ids.

    Before the repair this reconstructed `[1, 2]` — `members[:excluded_reconciled]` — so **4 of
    the 12 pairs it measured were pairs the operator had asserted** and the other 8 were asserted
    by nobody. It did not read as an error; it read as a rate, which is the whole reason a wrong
    marked set is worse than a missing one.
    """
    async with store.lock:
        sid, alarms = await _situation(store, 8)
        marked = [alarms[6], alarms[7]]
        await _asserting_label(store, sid, alarms, marked)
        await store.commit()

    bags, _members = await _asserting_bags(store, {})
    assert len(bags) == 1
    assert sorted(bags[0].marked) == sorted(marked), (
        "the judge reconstructed a marked set the operator did not mark; every pair it measures "
        "is then a pair nobody asserted (F90)"
    )

    # The pairs, which is the quantity that actually reaches the fourth named metric.
    rest = [a for a in alarms if a not in marked]
    asserted = {(min(a, b), max(a, b)) for a in marked for b in rest}
    measured = {(min(a, b), max(a, b)) for a, b in bags[0].observable_pairs()}
    assert measured == asserted, "the judge measures different pairs than the operator asserted"
    assert len(asserted) == 12


async def test_a_marked_set_that_is_a_prefix_agrees_before_and_after(store: Store) -> None:
    """**F90's control**, and the file cannot make its claim without it.

    The broken reconstruction was `members[:n]`, so a bag whose marked members happen to be the
    first `n` reconstructs *correctly* under both the defect and the repair. If this disagreed,
    the probe above would be measuring the harness rather than the judge.
    """
    async with store.lock:
        sid, alarms = await _situation(store, 8)
        marked = [alarms[0], alarms[1]]
        await _asserting_label(store, sid, alarms, marked)
        await store.commit()

    bags, _members = await _asserting_bags(store, {})
    assert sorted(bags[0].marked) == sorted(marked)


async def test_a_mark_that_named_nothing_in_the_bag_reconciles_to_nothing(store: Store) -> None:
    """The intersection is where the untrusted half meets the trusted one, and it stays silent.

    `reconciled_marks` is the same expression `reconciliation_drift` recomputes the stored count
    from, so the count and the set cannot drift apart (F46, one level down). A marked id that names
    no member of the bag is still recorded verbatim in `feedback_exclusion` — this asserts both
    halves, because a reader that dropped the row would be validating input and a reader that
    counted it would be asserting about a pair that does not exist.
    """
    async with store.lock:
        sid, alarms = await _situation(store, 4)
        ghost = max(alarms) + 9_000
        fid = await _asserting_label(store, sid, alarms, [alarms[3], ghost])
        # `excluded_reconciled` is what the server derived; the client reported two.
        await store.annotate_feedback(fid, member_count=4, excluded_count=2, excluded_reconciled=1)
        await store.commit()

    assert await store.feedback_exclusion(fid) == [alarms[3], ghost], "the report is verbatim"
    assert await store.reconciled_marks(fid) == [alarms[3]], "the ghost asserts nothing"
    assert not await store.reconciliation_drift(), "the set and the stored count must agree"


# --- §1: the bag is the captured snapshot, not live membership (F90, second half) ------------


async def test_the_judge_reads_the_captured_bag_and_not_the_live_one(store: Store) -> None:
    """**The second half of F90.** A `move` labels the source *before* removing the alarm.

    So live membership holds `k - 1` members and does not contain the very member the operator
    marked, while the snapshot holds all `k` and does. Measured before the repair: an 8-member
    snapshot, a 7-member bag, and the marked alarm absent from the bag being judged.
    """
    async with store.lock:
        sid, alarms = await _situation(store, 8)
        await _asserting_label(store, sid, alarms, [alarms[6]])
        await store.conn.execute(
            "DELETE FROM situation_alarm WHERE situation_id=? AND alarm_id=?", (sid, alarms[6])
        )
        await store.commit()

    live = await store.situation_member_ids(sid)
    assert len(live) == 7, "the fixture must actually have moved a member out"
    bags, _members = await _asserting_bags(store, {})
    assert list(bags[0].members) == alarms, "the bag judged is the snapshot, in its own order"
    assert alarms[6] in bags[0].marked, "the marked member left the situation and not the label"


async def test_an_unchanged_bag_reads_the_same_either_way(store: Store) -> None:
    """**The control for the half above.** Where nothing moved, snapshot and live agree.

    Without it, "the judge now reads the snapshot" could not be distinguished from "the judge now
    reads something else that happens to be right once".
    """
    async with store.lock:
        sid, alarms = await _situation(store, 8)
        await _asserting_label(store, sid, alarms, [alarms[6]])
        await store.commit()

    bags, _members = await _asserting_bags(store, {})
    assert list(bags[0].members) == alarms
    assert sorted(await store.situation_member_ids(sid)) == sorted(alarms)


async def test_the_hidden_set_honours_the_blind_count_the_row_records(store: Store) -> None:
    """§2.4's expression, against a row where the previous reconstruction understated it.

    `(m - b) · ((n - m) - (h - b))` with `n=6, m=2, h=3, b=1` is **2**. Taking the last `h`
    members as hidden puts zero of them inside the marked set whatever the column says, which
    yields `m · (n - m - h) = 2` here only by coincidence of these numbers — so the assertion below
    is on the expression, over the case `test_evidence_boundary_observable.py` calls the measured
    one, and `_hidden` is exercised directly because a stored label cannot carry the identities.
    """
    members = [10, 20, 30, 40, 50, 60]
    marked = frozenset({10, 20})
    hidden = _hidden(members, marked, 3, 1)
    assert len(hidden) == 3
    assert len(hidden & marked) == 1, "b of the hidden members are marked, exactly as recorded"
    visible_marked = [a for a in members if a in marked and a not in hidden]
    visible_rest = [a for a in members if a not in marked and a not in hidden]
    assert len(visible_marked) * len(visible_rest) == 2, "(m - b) * ((n - m) - (h - b))"


# --- §2: two gestures on one situation are two labels when the bag differs (F89) --------------


async def test_a_second_gesture_on_a_changed_bag_records_its_own_label(store: Store) -> None:
    """**F89's treatment**, over the real routes: two moves out of one situation, two labels.

    Before the repair the second `move` recorded its event, its two membership snapshots, its
    confidence and its provenance — and no label — so a busy operator restructuring one storm five
    times contributed one asserting bag rather than five. `asserting_bags` is registered to count a
    **gesture** (`PREREGISTRATION-0.16.0.md` §2), and this is the population that works hardest.
    """
    engine, queue, app = await authutil.make_env(store)
    await util.drive(engine, queue, util.fixture_events("fiber_cut.json", BASE))
    rows = [r for r in await store.list_situations(None, 100) if r["status"] != "resolved"]
    source = int(rows[0]["id"])
    members = await store.situation_member_ids(source)
    assert len(members) >= 4, "the seed must offer enough members to move two out"

    client = await authutil.client_as(app, "editor")
    try:
        # A destination the appliance could produce: split two members into their own situation.
        response = await client.post(
            f"/api/situations/{source}/split",
            json={"alarm_ids": members[:2], "confidence": SURE},
        )
        assert response.status_code == 200, response.text
        cur = await store.conn.execute(
            "SELECT peer_situation_id FROM situation_event WHERE kind='operator_split' "
            "ORDER BY id DESC LIMIT 1"
        )
        row = await cur.fetchone()
        assert row is not None
        destination = int(row[0])

        labels_before = await _labels_on(store, destination)
        moved = await store.situation_member_ids(destination)
        for alarm in moved[:2]:
            response = await client.post(
                f"/api/situations/{destination}/move",
                json={"alarm_id": alarm, "to_situation_id": source, "confidence": SURE},
            )
            assert response.status_code == 200, response.text
    finally:
        await client.aclose()

    labels = await _labels_on(store, destination)
    assert labels_before == 0
    assert labels == 2, (
        "the second move out of one situation asserted about a bag the first one changed, and it "
        "recorded its event and lost its label (F89)"
    )
    keys = await _bag_keys_on(store, destination)
    assert len(set(keys)) == 2, "two labels on one situation must name two different bags"


async def test_a_repeated_verdict_on_an_unchanged_bag_still_inserts_once(store: Store) -> None:
    """**F36's bound, preserved exactly where F36 measured it** — and the control for the above.

    v0.7.0's defect was that *N identical posts* wrote N rows and drove N learning effects, each
    advancing the global forgetting epoch. N identical posts have one `bag_key`. Widening the index
    without this assertion beside it would trade a measured invariant for an unmeasured one, which
    is the trade `docs/findings.md` F89 refused to make inside a feature release.
    """
    async with store.lock:
        sid, _alarms = await _situation(store, 4)
        first = await store.add_feedback(sid, "confirm", BASE, principal_ref="u:1", role="editor")
        again = await store.add_feedback(sid, "confirm", BASE + 1, principal_ref="u:1")
        third = await store.add_feedback(sid, "confirm", BASE + 2, principal_ref="u:2")
        await store.commit()
    assert first.inserted is True
    assert again.inserted is False and third.inserted is False, (
        "three identical posts about one unchanged bag are one assertion, not three"
    )
    assert await _labels_on(store, sid) == 1


async def test_the_key_is_the_member_set_and_not_its_order(store: Store) -> None:
    """§2: order is part of the *record* and not part of the *identity*.

    A key over `member_digest` — the ordered digest — would let a correlator that merely re-ordered
    a bag manufacture a second assertion out of one human decision, which is F36's defect wearing
    this release's clothes. Asserted on the function, because no route can re-order a bag on demand.
    """
    assert bag_key([3, 1, 2]) == bag_key([1, 2, 3]) == bag_key([2, 3, 1, 1])
    assert bag_key([1, 2, 3]) != bag_key([1, 2, 3, 4])
    assert bag_key([]) != UNKEYED_BAG, "the empty bag has an identity; an unkeyed row has none"
    assert member_digest([3, 1, 2]) != member_digest([1, 2, 3]), "the record still keeps the order"


async def test_a_verdict_on_a_situation_whose_membership_grew_is_a_second_assertion(
    store: Store,
) -> None:
    """The correlator's own additions count as a changed bag, and that is the registered answer.

    An operator confirms; the correlator adds an alarm; the operator confirms again. Those are two
    assertions about two groupings and the second is not a repeat of the first — which is exactly
    the trade `PREREGISTRATION-0.16.1.md` §2 names: the cap moves from *two applications* to *one
    per verdict per distinct membership*. Bounded, monotone in operator acts, no longer a constant.
    """
    async with store.lock:
        sid, alarms = await _situation(store, 3)
        first = await store.add_feedback(sid, "confirm", BASE, principal_ref="u:1")
        _sid2, extra = await _situation(store, 1)
        await store.conn.execute(
            "INSERT INTO situation_alarm (situation_id, alarm_id) VALUES (?, ?)", (sid, extra[0])
        )
        second = await store.add_feedback(sid, "confirm", BASE + 1, principal_ref="u:1")
        await store.commit()
    assert first.inserted and second.inserted
    assert len(alarms) == 3
    assert await _labels_on(store, sid) == 2


# --- the compensating control: the key and the snapshot cannot disagree ----------------------


async def test_every_labels_key_is_the_set_digest_of_its_own_recorded_snapshot(
    store: Store,
) -> None:
    """**The compensating control**, and the reason `add_feedback` may derive the key at all.

    `bag_key` is computed by the store from `situation_alarm`; `feedback_member(source='server')`
    is written by the engine from `capture.server_bag`, which prefers **engine state** for a live
    situation and falls back to the store. Those are two readings of one fact, and the design rests
    on their agreeing — so it is asserted rather than assumed, over the real HTTP write path, on
    bags the correlator actually formed. If they ever diverge this goes red, which is the whole
    point: `engine.apply_feedback` stayed byte-identical because this test exists.
    """
    engine, queue, app = await authutil.make_env(store)
    await util.drive(engine, queue, util.fixture_events("fiber_cut.json", BASE))
    rows = [r for r in await store.list_situations(None, 100) if r["status"] != "resolved"]
    assert rows, "the seed must form a live situation"
    sid = int(rows[0]["id"])
    members = await store.situation_member_ids(sid)

    client = await authutil.client_as(app, "editor")
    try:
        response = await client.post(
            f"/api/situations/{sid}/feedback",
            json={"verdict": "split", "member_ids": members, "excluded_ids": members[:1]},
        )
        assert response.status_code == 200, response.text
    finally:
        await client.aclose()

    cur = await store.conn.execute("SELECT id, bag_key FROM feedback ORDER BY id")
    labelled = [(int(r[0]), str(r[1])) for r in await cur.fetchall()]
    assert labelled, "no label was written, so this guard would pass vacuously"
    for feedback_id, key in labelled:
        snapshot = await store.feedback_members(feedback_id, "server")
        assert key == bag_key(snapshot), (
            f"feedback {feedback_id}: the key the store derived from `situation_alarm` is not the "
            "set digest of the snapshot the engine recorded — the two readings have diverged"
        )
        assert key != UNKEYED_BAG, "a row written after 0015 must carry a real key"


async def _labels_on(store: Store, sid: int) -> int:
    cur = await store.conn.execute("SELECT COUNT(*) FROM feedback WHERE situation_id=?", (sid,))
    row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def _bag_keys_on(store: Store, sid: int) -> list[str]:
    cur = await store.conn.execute(
        "SELECT bag_key FROM feedback WHERE situation_id=? ORDER BY id", (sid,)
    )
    return [str(r[0]) for r in await cur.fetchall()]


# --- the schema, and what it still refuses ----------------------------------------------------


async def test_the_unique_index_is_the_three_column_one_and_the_old_one_is_gone(
    store: Store,
) -> None:
    """`0015` replaces rather than adds, and a leftover two-column index would silently win.

    Asserted against `sqlite_master` rather than by behaviour, because a database carrying **both**
    indexes would pass every behavioural test above for the wrong reason: the narrower one would
    refuse the second gesture and the widened key would look like it had no effect.
    """
    cur = await store.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='feedback'"
    )
    names = {str(r[0]) for r in await cur.fetchall()}
    assert "idx_feedback_situation_verdict_bag" in names
    assert "idx_feedback_situation_verdict" not in names, (
        "0007's two-column index survived 0015, so the second gesture is still refused"
    )


async def test_a_database_frozen_before_0015_keeps_the_two_column_key(tmp_path: Path) -> None:
    """The probe, and the property it buys: identical store code on an un-migrated database.

    `tests/test_upgrade.py` runs the **current** store against a frozen migration directory, which
    is what makes *"the migration changes behaviour and the code does not"* checkable. So the write
    path has to know whether `bag_key` is there — and on a database where it is not, the bound in
    force is `0007`'s, unchanged and byte-identical to what v0.16.0 issued.
    """
    import netcorenoc.store.lifecycle as store_mod

    real_dir = store_mod.MIGRATIONS_DIR

    class _FrozenAtV14:
        def glob(self, pattern: str) -> list[Path]:
            return [p for p in real_dir.glob(pattern) if int(p.name.split("_", 1)[0]) <= 14]

    store_mod.MIGRATIONS_DIR = _FrozenAtV14()  # type: ignore[assignment]
    try:
        old = Store(str(tmp_path / "v0160.db"))
        await old.open()
        try:
            assert await old.schema_version() == 14
            assert old._has_bag_key is False
            async with old.lock:
                sid, alarms = await _situation(old, 3)
                first = await old.add_feedback(sid, "confirm", BASE, principal_ref="u:1")
                await old.conn.execute(
                    "DELETE FROM situation_alarm WHERE situation_id=? AND alarm_id=?",
                    (sid, alarms[0]),
                )
                again = await old.add_feedback(sid, "confirm", BASE + 1, principal_ref="u:1")
                await old.commit()
            assert first.inserted is True
            assert again.inserted is False, (
                "an un-migrated database must keep 0007's two-column bound; the widened key is a "
                "property of the schema, not of the code"
            )
        finally:
            await old.close()
    finally:
        store_mod.MIGRATIONS_DIR = real_dir


async def test_the_probe_is_true_on_a_migrated_database(store: Store) -> None:
    """The control for the freeze above: on a current database the probe says so.

    A probe that answered `False` everywhere would make the test above pass while the repair did
    nothing at all, which is `test_the_preregistration_exists`'s vacuity trap in a different file.
    """
    assert store._has_bag_key is True
    assert await store.schema_version() == Store.latest_schema_version() == 16
