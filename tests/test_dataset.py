"""The feedback dataset (v0.8.0): capture, promotion, the membership record, and retention.

The tests §14 of the build prompt requires, each pinning a property the release would otherwise be
only asserting: boundedness under a storm, capture failing without touching ingestion, the bag
surviving a merge, coverage recorded rather than silently dropped, the client fingerprint not
becoming an existence oracle, the retention ordering, and the preview's accuracy.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time

import pytest

from netcorenoc.capture import Capture, RetentionPolicy
from netcorenoc.correlate import ScoredLink, WindowAlarm
from netcorenoc.labels import ClientFingerprint, coverage, member_digest
from netcorenoc.main import Engine
from netcorenoc.rootcause import Member
from netcorenoc.scoring import LinkScore, TermContribution
from netcorenoc.store import MAX_CLIENT_MEMBERS, Store

import authutil
import util

TS = 1_000_000.0


async def _alarms(store: Store, n: int, *, base: int = 0) -> list[tuple[int, int, int]]:
    """`n` distinct (alarm_id, class_id, device_id) triples, written directly.

    Direct inserts rather than driven traps: these tests are about capture and retention semantics,
    and a trap fixture would make them also about parsing, dedup and flap detection.
    """
    out = []
    for i in range(n):
        cur = await store.conn.execute(
            "INSERT INTO device (ip, first_seen, last_seen) VALUES (?, ?, ?) RETURNING id",
            (f"10.{base}.0.{i + 1}", TS, TS),
        )
        dev = int((await cur.fetchone())[0])  # type: ignore[index]
        cur = await store.conn.execute(
            "INSERT INTO alarm_class (oid, first_seen, last_seen) VALUES (?, ?, ?) RETURNING id",
            (f"1.3.6.1.4.1.9.{base}.{i + 1}", TS, TS),
        )
        cls = int((await cur.fetchone())[0])  # type: ignore[index]
        cur = await store.conn.execute(
            "INSERT INTO alarm (device_id, class_id, instance, status, first_seen, last_seen, "
            "count) VALUES (?, ?, '', 'active', ?, ?, 1) RETURNING id",
            (dev, cls, TS, TS),
        )
        out.append((int((await cur.fetchone())[0]), cls, dev))  # type: ignore[index]
    return out


def _link(triple: tuple[int, int, int]) -> ScoredLink:
    return ScoredLink(
        WindowAlarm(*triple, TS),
        LinkScore(
            linked=True,
            score=0.9,
            threshold=0.5,
            terms=(TermContribution("temporal", 0.3, 1.0, 0.3),),
        ),
    )


# --- capture is bounded, and the dataset does not grow with traffic -----------------------


async def test_capture_is_bounded_by_max_candidates_per_activation(store: Store) -> None:
    """One observation row and at most MAX_CANDIDATES pair rows per activation.

    This is the *construction* half of "bounded": the ingest path already lives under
    MAX_CANDIDATES, so capture adds no new unbounded work. The retention half is below.
    """
    from netcorenoc.correlate import MAX_CANDIDATES

    engine = Engine(store, asyncio.Queue())
    await engine.start()
    events = [util.event(device=f"10.0.0.{i % 40}", ts=TS + i * 0.01) for i in range(300)]
    async with store.lock:
        for e in events:
            await engine._process(e)
        await store.commit()

    cur = await store.conn.execute(
        "SELECT alarm_b, COUNT(*) FROM dataset_pair GROUP BY alarm_b ORDER BY COUNT(*) DESC LIMIT 1"
    )
    row = await cur.fetchone()
    if row is not None:
        assert int(row[1]) <= MAX_CANDIDATES, "an activation captured more than the candidate cap"

    cur = await store.conn.execute("SELECT COUNT(*) FROM dataset_observation")
    observations = int((await cur.fetchone())[0])  # type: ignore[index]
    cur = await store.conn.execute("SELECT COUNT(DISTINCT alarm_id) FROM dataset_observation")
    distinct = int((await cur.fetchone())[0])  # type: ignore[index]
    assert observations == distinct, "one observation row per activation, never more"


async def test_the_sink_respects_both_bounds_and_the_dataset_does_not_grow(store: Store) -> None:
    """**The dual bound**, and the property the whole design turns on.

    A storm fills the **sink** and does not move the **dataset**, because the dataset grows with
    labels and the sink grows with traffic. That is bounded growth *by construction* rather than by
    a configured number — which matters because no correct traps/day figure exists across the
    deployments this product targets.
    """
    engine = Engine(store, asyncio.Queue())
    await engine.start()
    async with store.lock:
        for i in range(400):
            await engine._process(util.event(device=f"10.0.0.{i % 30}", ts=TS + i * 0.01))
        await store.commit()

    cur = await store.conn.execute("SELECT COUNT(*) FROM dataset_pair WHERE lifecycle='sink'")
    sink_before = int((await cur.fetchone())[0])  # type: ignore[index]
    assert sink_before > 0, "the storm must actually have filled the sink"
    cur = await store.conn.execute("SELECT COUNT(*) FROM dataset_pair WHERE lifecycle='dataset'")
    assert int((await cur.fetchone())[0]) == 0, (  # type: ignore[index]
        "the DATASET must not grow with traffic — no label has arrived"
    )

    # Bound 1: age. Everything is older than the cutoff, so the sink empties.
    async with store.lock:
        counts = await store.prune_sink(TS + 10_000.0, row_cap=10_000_000)
        await store.commit()
    assert counts["pairs_aged"] == sink_before

    # Bound 2: the row cap, on a fresh fill, with the age bound not biting.
    async with store.lock:
        for i in range(200):
            await engine._process(util.event(device=f"10.1.0.{i % 20}", ts=TS + 5000 + i * 0.01))
        await store.commit()
    cur = await store.conn.execute("SELECT COUNT(*) FROM dataset_pair WHERE lifecycle='sink'")
    refilled = int((await cur.fetchone())[0])  # type: ignore[index]
    assert refilled > 50
    async with store.lock:
        counts = await store.prune_sink(0.0, row_cap=50)
        await store.commit()
    assert counts["pairs_aged"] == 0, "the age bound must not have bitten"
    assert counts["pairs_capped"] == refilled - 50
    cur = await store.conn.execute("SELECT COUNT(*) FROM dataset_pair WHERE lifecycle='sink'")
    assert int((await cur.fetchone())[0]) == 50  # type: ignore[index]


async def test_neither_sink_bound_can_reach_a_promoted_row(store: Store) -> None:
    """**The control that pays for the one-table layout (DECISIONS #107).**

    Two tables would have made the sink/dataset separation *structural*; one table with a lifecycle
    column makes it a `WHERE` clause. That trade was accepted explicitly, on condition that this
    test exists — so if it is ever deleted, the layout decision loses its justification.
    """
    engine = Engine(store, asyncio.Queue())
    await engine.start()
    async with store.lock:
        for i in range(60):
            await engine._process(util.event(device=f"10.0.0.{i % 8}", ts=TS + i * 0.01))
        await store.commit()
        await store.conn.execute(
            "UPDATE dataset_pair SET lifecycle='dataset', promoted_at=? WHERE id <= 10", (TS,)
        )
        await store.conn.execute(
            "UPDATE dataset_observation SET lifecycle='dataset', promoted_at=? WHERE id <= 3", (TS,)
        )
        await store.commit()

    async with store.lock:
        await store.prune_sink(TS + 10_000.0, row_cap=0)  # both bounds, maximally aggressive
        await store.commit()

    cur = await store.conn.execute("SELECT COUNT(*) FROM dataset_pair WHERE lifecycle='dataset'")
    assert int((await cur.fetchone())[0]) == 10, "the sink prune destroyed promoted pairs"  # type: ignore[index]
    cur = await store.conn.execute(
        "SELECT COUNT(*) FROM dataset_observation WHERE lifecycle='dataset'"
    )
    assert int((await cur.fetchone())[0]) == 3, "the sink prune destroyed promoted observations"  # type: ignore[index]


# --- a capture failure degrades capture, never ingestion ----------------------------------


async def test_a_capture_write_error_degrades_capture_and_never_ingestion(store: Store) -> None:
    """**The fail-safe.** An injected write error must lose pairs, not traps.

    The alternative — letting the error propagate — would roll the *whole batch* back, so one bad
    row could cost 500 traps. That is prime directive 1 dying by an indirect route, which is exactly
    what this release was warned about.
    """
    engine = Engine(store, asyncio.Queue())
    await engine.start()

    async def boom(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError("injected: disk I/O error")

    store.add_pairs = boom  # type: ignore[method-assign]

    async with store.lock:
        for i in range(40):
            await engine._process(util.event(device=f"10.0.0.{i % 5}", ts=TS + i * 0.01))
        await store.commit()

    cur = await store.conn.execute("SELECT COUNT(*) FROM alarm")
    assert int((await cur.fetchone())[0]) > 0, "traps were lost to a CAPTURE failure"  # type: ignore[index]
    assert engine.processed == 40, "ingestion did not complete"
    assert engine.db_errors == 0, "a capture error must not be counted as a batch loss"
    assert engine.capture.errors > 0, "the capture failure was not recorded"
    assert "OperationalError" in engine.capture.last_error
    warnings = engine.capture.warnings()
    assert warnings and "Ingestion was unaffected" in warnings[0], warnings


async def test_capture_can_be_switched_off_and_writes_nothing(store: Store) -> None:
    """The documented off switch. Capture ships **on** — six months not captured cannot be
    reconstructed — but an operator who does not want it must be able to say so."""
    engine = Engine(store, asyncio.Queue())
    engine.capture.enabled = False
    await engine.start()
    async with store.lock:
        for i in range(20):
            await engine._process(util.event(device=f"10.0.0.{i % 4}", ts=TS + i * 0.01))
        await store.commit()
    for table in ("dataset_pair", "dataset_observation", "capture_run"):
        cur = await store.conn.execute(f"SELECT COUNT(*) FROM {table}")
        assert int((await cur.fetchone())[0]) == 0, f"{table} was written with capture off"  # type: ignore[index]
    cur = await store.conn.execute("SELECT COUNT(*) FROM alarm")
    assert int((await cur.fetchone())[0]) > 0, "ingestion stopped when capture was disabled"  # type: ignore[index]


# --- the bag survives the merge -----------------------------------------------------------


async def test_the_bag_survives_a_merge_that_destroys_every_other_trace(store: Store) -> None:
    """**The membership-survival test.** Phase 0 proved this impossible before v0.8.0.

    Label a situation, merge it away, and recover the bag — while `situation_alarm` for the
    absorbed id is empty and `feedback JOIN situation_alarm` still returns nothing, exactly as
    `docs/gates/v0.8.0-phase-0.md` §1 recorded.
    """
    engine = Engine(store, asyncio.Queue())
    await engine.start()
    async with store.lock:
        ids = await _alarms(store, 5)
        sid_a = await store.create_situation(TS, None)
        sid_b = await store.create_situation(TS, None)
        for triple in ids[:2]:
            await store.add_alarm_to_situation(sid_a, triple[0])
        for triple in ids[2:4]:
            await store.add_alarm_to_situation(sid_b, triple[0])
        await store.commit()
    engine.members[sid_a] = [Member(*t, TS) for t in ids[:2]]
    engine.members[sid_b] = [Member(*t, TS) for t in ids[2:4]]
    for t in ids[:2]:
        engine.sit_of[t[0]] = sid_a
    for t in ids[2:4]:
        engine.sit_of[t[0]] = sid_b

    absorbed, survivor = max(sid_a, sid_b), min(sid_a, sid_b)
    async with store.lock:
        recorded = await engine.apply_feedback(absorbed, "confirm", TS + 1.0)
        await store.commit()
    assert recorded.id is not None

    async with store.lock:  # the bridging alarm merges `absorbed` into `survivor`
        await engine._assign_situation(
            WindowAlarm(*ids[4], TS + 2.0), [_link(ids[0]), _link(ids[2])]
        )
        await store.commit()

    # Everything v0.7.5 could have used is still gone…
    cur = await store.conn.execute(
        "SELECT COUNT(*) FROM situation_alarm WHERE situation_id=?", (absorbed,)
    )
    assert int((await cur.fetchone())[0]) == 0  # type: ignore[index]
    cur = await store.conn.execute(
        "SELECT COUNT(*) FROM feedback f JOIN situation_alarm sa ON sa.situation_id=f.situation_id"
    )
    assert int((await cur.fetchone())[0]) == 0, "the natural join must still be empty"  # type: ignore[index]

    # …and the bag is recoverable anyway.
    bag = await store.feedback_members(recorded.id, "server")
    assert bag == [t[0] for t in ids[2:4]], "the server-side bag did not survive the merge"

    cur = await store.conn.execute(
        "SELECT member_digest, member_count, situation_id_at_label FROM feedback WHERE id=?",
        (recorded.id,),
    )
    row = dict(await cur.fetchone())  # type: ignore[arg-type]
    assert row["member_digest"] == member_digest(bag)
    assert row["member_count"] == 2
    assert row["situation_id_at_label"] == absorbed

    # And the merge chain is recoverable, which it was not before this release.
    cur = await store.conn.execute("SELECT merged_into FROM situation WHERE id=?", (absorbed,))
    assert int((await cur.fetchone())[0]) == survivor  # type: ignore[index]


async def test_a_verdict_on_an_already_merged_situation_records_an_empty_bag(store: Store) -> None:
    """The degenerate case, made **countable** rather than invisible.

    Phase 0 §2 measured it: 200, a full `feedback` row, and `learn.penalize([])` — a silent no-op
    indistinguishable from a real label by any column that existed. Now it is distinguishable.
    """
    engine = Engine(store, asyncio.Queue())
    await engine.start()
    async with store.lock:
        sid = await store.create_situation(TS, None)
        await store.conn.execute(
            "UPDATE situation SET status='merged', merged_into=1 WHERE id=?", (sid,)
        )
        recorded = await engine.apply_feedback(sid, "split", TS + 1.0)
        await store.commit()

    assert recorded.exists and recorded.inserted, "the 200 behaviour is unchanged"
    assert recorded.id is not None
    assert await store.feedback_members(recorded.id, "server") == []
    cur = await store.conn.execute(
        "SELECT member_count, coverage FROM feedback WHERE id=?", (recorded.id,)
    )
    row = dict(await cur.fetchone())  # type: ignore[arg-type]
    assert row["member_count"] == 0, "an empty bag must be recorded AS empty"
    assert row["coverage"] == "empty", (
        "an empty bag is its own coverage state — `full` would be vacuously true and would tell a "
        "reader a complete situation was captured, `none` would suggest pairs went missing"
    )


# --- coverage is recorded, never silent ---------------------------------------------------


def test_coverage_states_are_the_three_cases_and_nothing_else() -> None:
    """§6.3's three cases. A nine-member bag implies 36 pairs; a naive join would drop the gap."""
    assert coverage([1, 2, 3], 3) == {
        "coverage": "full",
        "coverage_found": 3,
        "coverage_expected": 3,
    }
    assert coverage([1, 2, 3], 1)["coverage"] == "partial"
    assert coverage([1, 2, 3], 0)["coverage"] == "none"
    assert coverage([1], 0)["coverage_expected"] == 0, "a singleton implies no pairs"
    assert coverage([], 0)["coverage"] == "empty", "an empty bag is the fourth, degenerate state"
    assert coverage([], 3)["coverage"] == "empty", "…even with stray pairs pointing at it"
    nine = coverage(list(range(9)), 10)
    assert nine["coverage_expected"] == 36 and nine["coverage"] == "partial"


async def test_a_bag_with_an_unevaluated_pair_is_recorded_partial(store: Store) -> None:
    """**The coverage test.** A labelled situation containing a pair that was never evaluated is
    recorded as partial — not silently dropped, which is the censoring this release exists to end.

    The window and MAX_CANDIDATES make this ordinary rather than exotic: a nine-member situation has
    thirty-six pairs and not all of them passed through `score_link`.
    """
    engine = Engine(store, asyncio.Queue())
    await engine.start()
    async with store.lock:
        ids = await _alarms(store, 4)
        sid = await store.create_situation(TS, None)
        for triple in ids:
            await store.add_alarm_to_situation(sid, triple[0])
        # Exactly ONE of the six pairs a four-member bag implies was ever evaluated.
        run = engine.capture.run_id
        await store.add_pairs(
            [(run, ids[0][0], ids[1][0], None, None, sid, 1.0, 0.0, 0.0, 0, 0, 0.9, 1, 0, 0, TS)]
        )
        await store.commit()
    engine.members[sid] = [Member(*t, TS) for t in ids]

    async with store.lock:
        recorded = await engine.apply_feedback(sid, "confirm", TS + 1.0)
        await store.commit()
    assert recorded.id is not None

    cur = await store.conn.execute(
        "SELECT coverage, coverage_found, coverage_expected FROM feedback WHERE id=?",
        (recorded.id,),
    )
    row = dict(await cur.fetchone())  # type: ignore[arg-type]
    assert row["coverage"] == "partial", row
    assert row["coverage_found"] == 1
    assert row["coverage_expected"] == 6, "four members imply six pairs"


# --- the client fingerprint is bounded, unrejected, and not an existence oracle ------------


def test_the_client_fingerprint_is_bounded_and_records_its_truncation() -> None:
    """Bounded, and the truncation is a **fact on the row** rather than a silence.

    A silent bound would make the bound itself invisible in the data, so a later reader could not
    tell a short bag from a truncated one.
    """
    short = ClientFingerprint.accept([1, 2, 3], 12.0)
    assert short.alarm_ids == [1, 2, 3] and short.truncated is False

    long = ClientFingerprint.accept(list(range(MAX_CLIENT_MEMBERS + 50)), 12.0)
    assert len(long.alarm_ids) == MAX_CLIENT_MEMBERS
    assert long.truncated is True, "truncation must be recorded, never silent"


def test_the_client_fingerprint_never_rejects_anything() -> None:
    """Rejection is the wrong primitive for an observation (§2.1, one level down).

    Negative ids, zero, and ids that cannot exist are all *accepted and recorded as reported* —
    because the field records what a client claimed, not what is true.
    """
    hostile = ClientFingerprint.accept([-1, 0, 2**62, 999_999_999], None)
    assert hostile.alarm_ids == [-1, 0, 2**62, 999_999_999]
    assert hostile.truncated is False
    assert ClientFingerprint.accept([], None).alarm_ids == []


async def test_the_client_fingerprint_is_recorded_verbatim_including_unknown_ids(
    store: Store,
) -> None:
    """**The existence-oracle property, at the storage layer.**

    An id that does not exist is stored exactly as reported and is never looked up. The HTTP half —
    identical status, body and timing — is `tests/test_abuse.py`; this is the half that proves the
    write path does not consult the alarm table, which is what a lookup would need to do.
    """
    engine = Engine(store, asyncio.Queue())
    await engine.start()
    async with store.lock:
        ids = await _alarms(store, 2)
        sid = await store.create_situation(TS, None)
        for triple in ids:
            await store.add_alarm_to_situation(sid, triple[0])
        await store.commit()
    engine.members[sid] = [Member(*t, TS) for t in ids]

    ghost = 999_999_999
    async with store.lock:
        recorded = await engine.apply_feedback(
            sid,
            "confirm",
            TS + 1.0,
            client=ClientFingerprint.accept([ids[0][0], ghost], 99.0),
        )
        await store.commit()
    assert recorded.id is not None

    assert await store.feedback_members(recorded.id, "client") == [ids[0][0], ghost], (
        "a non-existent id must be recorded as reported, not filtered"
    )
    # And the server's own bag is unaffected by whatever the client said.
    assert await store.feedback_members(recorded.id, "server") == [t[0] for t in ids]

    cur = await store.conn.execute(
        "SELECT client_member_count, client_updated_at, client_truncated FROM feedback WHERE id=?",
        (recorded.id,),
    )
    row = dict(await cur.fetchone())  # type: ignore[arg-type]
    assert row["client_member_count"] == 2
    assert row["client_updated_at"] == 99.0
    assert row["client_truncated"] == 0


async def test_an_absent_client_fingerprint_means_unrecorded_never_a_guess(store: Store) -> None:
    """An old client, or a `curl` call, still works — and leaves the client columns NULL."""
    engine = Engine(store, asyncio.Queue())
    await engine.start()
    async with store.lock:
        ids = await _alarms(store, 2)
        sid = await store.create_situation(TS, None)
        for triple in ids:
            await store.add_alarm_to_situation(sid, triple[0])
        await store.commit()
    engine.members[sid] = [Member(*t, TS) for t in ids]
    async with store.lock:
        recorded = await engine.apply_feedback(sid, "confirm", TS + 1.0)
        await store.commit()
    assert recorded.id is not None
    cur = await store.conn.execute(
        "SELECT client_member_digest, client_member_count, client_truncated FROM feedback "
        "WHERE id=?",
        (recorded.id,),
    )
    row = dict(await cur.fetchone())  # type: ignore[arg-type]
    assert all(v is None for v in row.values()), row
    assert await store.feedback_members(recorded.id, "client") == []
    # The server's own half is written regardless — that is the point of it being authoritative.
    assert await store.feedback_members(recorded.id, "server") == [t[0] for t in ids]


# --- retention: ordering, and the preview -------------------------------------------------


@pytest.mark.parametrize(
    "policy,expected",
    [
        (RetentionPolicy(), None),
        (RetentionPolicy(sink_days=400.0), "must be strictly less than training"),
        (RetentionPolicy(training_days=1000.0, audit_days=730.0), "must not exceed audit"),
        (RetentionPolicy(sink_days=0.0), "greater than zero"),
        (RetentionPolicy(sink_rows=0), "row cap must be greater than zero"),
        (RetentionPolicy(sink_days=365.0, training_days=365.0), "strictly less than training"),
    ],
)
def test_the_retention_ordering_is_validated_fail_closed(
    policy: RetentionPolicy, expected: str | None
) -> None:
    """`sink < training <= audit`, fail-closed, **with a precise reason**.

    A reason rather than a bool because the admin has to be able to act on it: "invalid" on a form
    of four numbers is not something anyone can fix. The equality case is included deliberately —
    `sink == training` is rejected, because a sink that outlives the corpus it feeds is the
    inversion the ordering exists to prevent.
    """
    reason = policy.validate()
    if expected is None:
        assert reason is None, reason
    else:
        assert reason is not None and expected in reason, reason


async def test_the_retention_preview_counts_exactly_what_would_be_destroyed(store: Store) -> None:
    """**The preview-accuracy test**, on a crafted fixture.

    The preview is the whole of the safeguard for the only destructive control in the product, so a
    preview that under-counts is worse than no preview at all: it would tell an operator a
    reduction is cheap immediately before it destroys nine months of labels.
    """
    engine = Engine(store, asyncio.Queue())
    await engine.start()
    async with store.lock:
        sid = await store.create_situation(TS, None)
        old, new = TS - 100.0, TS + 100.0
        run = engine.capture.run_id
        rows = []
        for i, ts in enumerate([old] * 7 + [new] * 3):
            rows.append((run, i, i + 1, None, None, sid, 1.0, 0.0, 0.0, 0, 0, 0.9, 1, 0, 0, ts))
        await store.add_pairs(rows)
        await store.conn.execute("UPDATE dataset_pair SET lifecycle='dataset'")
        await store.add_feedback(sid, "confirm", old)
        await store.commit()

    async with store.lock:
        impact = await store.preview_retention(TS)
    assert impact["pairs"] == 7, impact
    assert impact["labels"] == 1
    assert impact["oldest"] == old and impact["newest"] == old

    async with store.lock:
        deleted = await store.prune_dataset(TS)
        await store.commit()
    assert deleted["pairs"] == impact["pairs"], "the preview did not match what was destroyed"
    cur = await store.conn.execute("SELECT COUNT(*) FROM dataset_pair")
    assert int((await cur.fetchone())[0]) == 3  # type: ignore[index]


async def test_the_maintenance_loop_prunes_the_sink_and_never_the_dataset(store: Store) -> None:
    """**Directive 9.** The background loop may bound the sink; it may never destroy labels.

    Reducing the training tier deletes human labels irreversibly, so it is an explicit admin action
    behind a preview — not something a loop does on a schedule. This is the difference between this
    prune and every other one in the product.
    """
    engine = Engine(store, asyncio.Queue())
    await engine.start()
    async with store.lock:
        for i in range(60):
            await engine._process(util.event(device=f"10.0.0.{i % 8}", ts=TS + i * 0.01))
        await store.commit()
        await store.conn.execute(
            "UPDATE dataset_pair SET lifecycle='dataset', promoted_at=? WHERE id <= 12", (TS,)
        )
        await store.commit()

    # A maintenance pass whose clock is far beyond every row's age and every retention tier.
    await engine.maintenance(now=TS + 10**9, retention_days=1.0, tick=0)

    cur = await store.conn.execute("SELECT COUNT(*) FROM dataset_pair WHERE lifecycle='dataset'")
    assert int((await cur.fetchone())[0]) == 12, (  # type: ignore[index]
        "the maintenance loop destroyed labelled rows — it may only prune the sink"
    )
    cur = await store.conn.execute("SELECT COUNT(*) FROM dataset_pair WHERE lifecycle='sink'")
    assert int((await cur.fetchone())[0]) == 0, "the sink was not pruned"  # type: ignore[index]


async def test_a_prune_failure_degrades_capture_and_not_maintenance(store: Store) -> None:
    """A sink that failed to prune is a disk-space problem; a maintenance sweep that raised would
    also skip the learned-state flush behind it."""
    engine = Engine(store, asyncio.Queue())
    await engine.start()

    async def boom(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError("injected: database is locked")

    store.prune_sink = boom  # type: ignore[assignment]
    await engine.maintenance(now=time.time(), retention_days=1.0, tick=0)
    assert engine.capture.errors > 0, "the prune failure was not recorded"


async def test_capture_run_records_the_policy_in_effect(store: Store) -> None:
    """A model trained under twelve months' retention and one trained under three are not
    comparable, and **nothing else in the schema would record which was which**."""
    engine = Engine(store, asyncio.Queue())
    engine.retention = RetentionPolicy(sink_days=7.0, training_days=90.0, audit_days=180.0)
    await engine.start()
    run = await store.latest_capture_run()
    assert run is not None
    assert run["retention_sink_days"] == 7.0
    assert run["retention_training_days"] == 90.0
    assert run["retention_audit_days"] == 180.0
    assert run["max_candidates"] == 100, "the censoring bounds in effect must be recorded"
    assert run["max_links_per_alarm"] == 5


def test_capture_defaults_to_on() -> None:
    """Capture ships **on**: a deployment that discovers this dataset's importance six months in
    has lost six months that cannot be reconstructed. §3.4 is the whole reason."""
    assert Capture().enabled is True


async def test_applying_a_reduction_actually_deletes(store: Store) -> None:
    """**A bug the dead-code guard found before a human did.**

    The first implementation stored the new policy and audited the change — and deleted nothing,
    because the maintenance loop only prunes the sink. `vulture` flagged `prune_dataset` as unused,
    which is exactly what an unwired destructive path looks like: an operator would have set three
    months, seen the count, confirmed, and kept twelve.

    The deletion now happens in the audited transaction, **after** the audit row, so a crash
    mid-delete still leaves the intent on record.
    """
    engine = Engine(store, asyncio.Queue())
    await engine.start()
    async with store.lock:
        sid = await store.create_situation(TS, None)
        old, new = TS - 100.0, TS + 100.0
        run = engine.capture.run_id
        await store.add_pairs(
            [
                (run, i, i + 1, None, None, sid, 1.0, 0.0, 0.0, 0, 0, 0.9, 1, 0, 0, ts)
                for i, ts in enumerate([old] * 4 + [new] * 2)
            ]
        )
        await store.conn.execute("UPDATE dataset_pair SET lifecycle='dataset'")
        await store.commit()

    async with store.lock:
        impact = await store.preview_retention(TS)
        assert impact["pairs"] == 4
        deleted = await store.prune_dataset(TS)
        await store.commit()

    assert deleted["pairs"] == 4, "applying a reduction must destroy what the preview counted"
    cur = await store.conn.execute("SELECT COUNT(*) FROM dataset_pair")
    assert int((await cur.fetchone())[0]) == 2  # type: ignore[index]


# --- the HTTP surface: admin-only, previewed, and not an existence oracle ------------------


async def test_retention_routes_are_admin_only(store: Store) -> None:
    """The dataset is a scope bypass by construction, so there is no scoped view to build.

    A viewer and an editor must not reach either route — not filtered, not redacted, not
    aggregated-for-an-editor.
    """
    _engine, _queue, app = await authutil.make_env(store)
    body = {"sink_days": 21, "sink_rows": 1000, "training_days": 365, "audit_days": 730}
    for role in ("viewer", "editor"):
        client = await authutil.client_as(app, role)
        try:
            assert (await client.get("/api/dataset/retention")).status_code == 403, role
            assert (await client.post("/api/dataset/retention", json=body)).status_code == 403, role
        finally:
            await client.aclose()
    admin = await authutil.client_as(app, "admin")
    try:
        assert (await admin.get("/api/dataset/retention")).status_code == 200
    finally:
        await admin.aclose()


async def test_the_retention_route_previews_by_default_and_destroys_nothing(
    store: Store,
) -> None:
    """**`preview` defaults to True**, and that default is the safety property.

    A client that forgets the field, or a script written against an older contract, gets a count
    and no deletion. Reaching the destructive branch takes `preview=false`, sent deliberately.
    """
    engine, _queue, app = await authutil.make_env(store)
    async with store.lock:
        sid = await store.create_situation(TS - 10**7, None)
        await store.add_pairs(
            [
                (
                    engine.capture.run_id,
                    1,
                    2,
                    None,
                    None,
                    sid,
                    1.0,
                    0.0,
                    0.0,
                    0,
                    0,
                    0.9,
                    1,
                    0,
                    0,
                    TS - 10**7,
                )
            ]
        )
        await store.conn.execute("UPDATE dataset_pair SET lifecycle='dataset'")
        await store.commit()

    body = {"sink_days": 1, "sink_rows": 100, "training_days": 2, "audit_days": 3}
    client = await authutil.client_as(app, "admin")
    try:
        resp = await client.post("/api/dataset/retention", json=body)
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["applied"] is False
        assert payload["status"] == "preview"
        assert payload["would_delete"]["pairs"] == 1
    finally:
        await client.aclose()

    cur = await store.conn.execute("SELECT COUNT(*) FROM dataset_pair")
    assert int((await cur.fetchone())[0]) == 1, "the preview destroyed a row"  # type: ignore[index]

    # And the audit records the LOOK, not only the destruction.
    cur = await store.conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action='retention.preview'"
    )
    assert int((await cur.fetchone())[0]) == 1  # type: ignore[index]


async def test_the_retention_route_applies_and_audits_the_reduction(store: Store) -> None:
    """The destructive branch: deletes, and records before/after plus the previewed impact."""
    engine, _queue, app = await authutil.make_env(store)
    async with store.lock:
        sid = await store.create_situation(TS - 10**7, None)
        await store.add_pairs(
            [
                (
                    engine.capture.run_id,
                    1,
                    2,
                    None,
                    None,
                    sid,
                    1.0,
                    0.0,
                    0.0,
                    0,
                    0,
                    0.9,
                    1,
                    0,
                    0,
                    TS - 10**7,
                )
            ]
        )
        await store.conn.execute("UPDATE dataset_pair SET lifecycle='dataset'")
        await store.commit()

    body = {
        "sink_days": 1,
        "sink_rows": 100,
        "training_days": 2,
        "audit_days": 3,
        "preview": False,
    }
    client = await authutil.client_as(app, "admin")
    try:
        resp = await client.post("/api/dataset/retention", json=body)
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        assert payload["applied"] is True
        assert payload["deleted"]["pairs"] == 1
    finally:
        await client.aclose()

    cur = await store.conn.execute("SELECT COUNT(*) FROM dataset_pair")
    assert int((await cur.fetchone())[0]) == 0, "applying a reduction did not delete"  # type: ignore[index]
    assert engine.retention.training_days == 2.0

    cur = await store.conn.execute(
        "SELECT details FROM audit_log WHERE action='retention.change' ORDER BY id DESC LIMIT 1"
    )
    details = str((await cur.fetchone())[0])  # type: ignore[index]
    assert "before" in details and "after" in details and "previewed_impact" in details


async def test_an_invalid_retention_ordering_is_refused_with_a_reason(store: Store) -> None:
    """Fail-closed, and the 400 names the offending tier — not a bare "invalid"."""
    _engine, _queue, app = await authutil.make_env(store)
    client = await authutil.client_as(app, "admin")
    try:
        resp = await client.post(
            "/api/dataset/retention",
            json={
                "sink_days": 400,
                "sink_rows": 100,
                "training_days": 365,
                "audit_days": 730,
                "preview": False,
            },
        )
    finally:
        await client.aclose()
    assert resp.status_code == 400
    assert "strictly less than training" in resp.json()["detail"]
    cur = await store.conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='retention.change'")
    assert int((await cur.fetchone())[0]) == 0, "a refused change must not be audited as applied"  # type: ignore[index]


async def test_the_client_fingerprint_is_not_an_existence_oracle(store: Store) -> None:
    """**The existence-oracle test, over HTTP: status, body and timing.**

    A scoped editor must not be able to learn, from any difference in the response, whether an
    alarm id they named exists. This is the discipline F34 established for situations and F37 for
    label targets, applied to a field that accepts arbitrary integers.
    """
    engine, _queue, app = await authutil.make_env(store)
    async with store.lock:
        ids = await _alarms(store, 3)
        sid = await store.create_situation(TS, None)
        for t in ids:
            await store.add_alarm_to_situation(sid, t[0])
        await store.commit()
    engine.members[sid] = [Member(*t, TS) for t in ids]

    real, ghost = ids[0][0], 987_654_321

    async def post(member_ids: list[int], verdict: str) -> tuple[int, str, float]:
        client = await authutil.client_as(app, "editor")
        try:
            start = time.perf_counter()
            resp = await client.post(
                f"/api/situations/{sid}/feedback",
                json={"verdict": verdict, "member_ids": member_ids, "updated_at": 1.0},
            )
            return resp.status_code, resp.text, time.perf_counter() - start
        finally:
            await client.aclose()

    # `confirm` and `split` are distinct verdicts, so each POST is a genuine insert (F36) and the
    # two responses are comparable rather than one being an idempotent no-op.
    status_real, body_real, t_real = await post([real], "confirm")
    status_ghost, body_ghost, t_ghost = await post([ghost], "split")

    assert status_real == status_ghost == 200, (status_real, status_ghost)
    assert body_real.replace("confirm", "V") == body_ghost.replace("split", "V"), (
        "the response body differs between an existing and a non-existent alarm id"
    )
    # Timing: the write path never looks the id up, so there is no query whose cost could differ.
    # A generous bound — this asserts "no database round trip", not "constant time".
    assert abs(t_real - t_ghost) < 0.25, (t_real, t_ghost)

    # And both were recorded verbatim.
    cur = await store.conn.execute(
        "SELECT alarm_id FROM feedback_member WHERE source='client' ORDER BY feedback_id, position"
    )
    assert [int(r[0]) for r in await cur.fetchall()] == [real, ghost]


# --- F44: the operational prune does not delete human labels -------------------------------


async def _labelled_and_aged(store: Store) -> tuple[int, int, list[int]]:
    """A closed, labelled situation with a bag and promoted pairs, aged past every bound.

    Returns `(situation_id, feedback_id, member_alarm_ids)`.
    """
    ids = await _alarms(store, 4)
    sid = await store.create_situation(TS, None)
    for triple in ids:
        await store.add_alarm_to_situation(sid, triple[0])
    recorded = await store.add_feedback(sid, "confirm", TS + 10.0, principal_ref="u:1")
    assert recorded.id is not None
    await store.add_feedback_members(recorded.id, "server", [t[0] for t in ids])
    await store.conn.execute(
        "INSERT INTO link (situation_id, alarm_a, alarm_b, score, term_t, term_a, term_e, "
        "created_at) VALUES (?, ?, ?, 0.9, 0.3, 0.3, 0.3, ?)",
        (sid, ids[0][0], ids[1][0], TS),
    )
    await store.close_situation(sid, TS + 20.0)
    return sid, recorded.id, [t[0] for t in ids]


async def test_f44_the_operational_prune_does_not_delete_the_verdict_or_its_bag(
    store: Store,
) -> None:
    """**F44.** A labelled, closed, aged situation keeps its verdict and its bag.

    Until v0.8.1 `prune()` deleted `feedback` for every situation closed longer than the
    OPERATIONAL retention — 7.0 days by default — and `feedback_member` followed by
    `ON DELETE CASCADE`, while the `dataset_pair` features survived. The corpus kept the evidence
    and lost the judgement it was evidence for.
    """
    async with store.lock:
        sid, fid, members = await _labelled_and_aged(store)
        await store.commit()

    # Well past the operational retention: 7 days is the shipped default.
    async with store.lock:
        counts = await store.prune(now=TS + 20.0 + 30.0 * 86400.0, retention_s=7.0 * 86400.0)
        await store.commit()

    cur = await store.conn.execute("SELECT verdict FROM feedback WHERE id=?", (fid,))
    row = await cur.fetchone()
    assert row is not None, "F44: the operational prune deleted the human verdict"
    assert row[0] == "confirm"
    assert await store.feedback_members(fid, "server") == members, (
        "F44: the bag went with the label, by ON DELETE CASCADE"
    )
    # The situation row is retained -- and ONLY the row -- because `feedback.situation_id` is a
    # restricting FK. DECISIONS #109.
    assert counts["situations"] == 0, "a labelled situation must not be counted as collected"
    cur = await store.conn.execute("SELECT COUNT(*) FROM situation WHERE id=?", (sid,))
    assert int((await cur.fetchone())[0]) == 1  # type: ignore[index]


async def test_f44_the_operational_data_the_label_references_is_still_collected(
    store: Store,
) -> None:
    """The other half: retaining the label must not retain the estate.

    Only the one `situation` row survives. Its membership, its links and the cleared alarms behind
    them are collected on the operational schedule exactly as before — otherwise the fix for a
    data-loss defect would be a disk-growth defect (DECISIONS #109, rejected option (c)).
    """
    async with store.lock:
        sid, _fid, _members = await _labelled_and_aged(store)
        await store.commit()
    async with store.lock:
        await store.prune(now=TS + 20.0 + 30.0 * 86400.0, retention_s=7.0 * 86400.0)
        await store.commit()

    for table, column in (("situation_alarm", "situation_id"), ("link", "situation_id")):
        cur = await store.conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {column}=?",
            (sid,),
        )
        assert int((await cur.fetchone())[0]) == 0, (  # type: ignore[index]
            f"{table} rows survived the operational prune of a labelled situation"
        )


async def test_f44_an_unlabelled_situation_is_collected_exactly_as_before(store: Store) -> None:
    """The fix is scoped to labels. An aged closed situation with no verdict still goes."""
    async with store.lock:
        sid = await store.create_situation(TS, None)
        await store.close_situation(sid, TS + 20.0)
        await store.commit()
    async with store.lock:
        counts = await store.prune(now=TS + 20.0 + 30.0 * 86400.0, retention_s=7.0 * 86400.0)
        await store.commit()
    assert counts["situations"] == 1
    cur = await store.conn.execute("SELECT COUNT(*) FROM situation WHERE id=?", (sid,))
    assert int((await cur.fetchone())[0]) == 0  # type: ignore[index]


async def test_f44_repeated_prunes_do_not_raise_on_the_retained_situation(store: Store) -> None:
    """The regression the naive fix would have introduced.

    `feedback.situation_id` is `NOT NULL REFERENCES situation (id)` with no `ON DELETE` action, and
    `foreign_keys=ON`. Removing the `DELETE FROM feedback` line *without* also retaining the
    situation row makes every subsequent sweep raise `IntegrityError`, turning silent data loss
    into a maintenance loop that dies. Four passes, because one would not have caught it either.
    """
    async with store.lock:
        _sid, fid, _members = await _labelled_and_aged(store)
        await store.commit()
    for i in range(4):
        async with store.lock:
            await store.prune(now=TS + 20.0 + (30.0 + i) * 86400.0, retention_s=7.0 * 86400.0)
            await store.commit()
    cur = await store.conn.execute("SELECT COUNT(*) FROM feedback WHERE id=?", (fid,))
    assert int((await cur.fetchone())[0]) == 1  # type: ignore[index]
