"""The partial split (v0.9.1): what it asserts, what it deliberately does not, and its bounds.

**The release's whole thesis is a negative claim**, so most of what is asserted here is what the
product does *not* record: the pairs inside the remainder, the pairs inside the marked set, and the
remainder's cohesion are all left **unasserted**, because the operator did not speak about them.

Four properties, and each is a section:

* §1 **semantics** — a partial split asserts exactly `|marked| x |rest|` negatives and nothing else;
* §2 **parity** — with no exclusion, every path is v0.9.0's;
* §3 **bounds** — the penalised cell count with an exclusion can never exceed the count without one;
* §4 **the oracle** — a marked id that does not exist, or that a scoped editor cannot see, changes
  nothing in status, body or timing. That write path produced F34, F35 and F39; it does not get to
  produce a fourth.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest

from netcorenoc.capture import Exclusion, LabelContext
from netcorenoc.learn import EPOCH_PAIR_CAP, SPLIT_PENALTY, Learner, _pair
from netcorenoc.main import Engine
from netcorenoc.rootcause import Member
from netcorenoc.store import Store

import authutil

TS = 1_000_000.0


def _bag(n: int) -> list[tuple[int, int]]:
    """A heterogeneous bag: every member a distinct (class, device), which is the shape an
    operator actually reads on a card. The corpus's own split bags are singletons and storms
    (Gate 0 §2.1), so this release's semantics have to be proved on a purpose-built fixture."""
    return [(100 + i, 200 + i) for i in range(n)]


def _seed(members: list[tuple[int, int]]) -> Learner:
    learner = Learner()
    for i, a in enumerate(members):
        for b in members[i + 1 :]:
            learner.A.observe_pair(a[0], b[0], 1.0)
            if a[1] != b[1]:
                learner.E.observe_pair(a[1], b[1], 1.0)
    return learner


def _scaled(
    learner: Learner, before_a: dict[Any, Any], before_e: dict[Any, Any]
) -> tuple[set[Any], set[Any]]:
    """Which matrix cells actually moved."""
    return (
        {k for k in before_a if learner.A.pairs[k][0] != before_a[k][0]},
        {k for k in before_e if learner.E.pairs[k][0] != before_e[k][0]},
    )


# --- §1 semantics ------------------------------------------------------------------------------


def test_a_partial_split_asserts_marked_by_rest_and_nothing_else() -> None:
    """**The release's central claim, as arithmetic.**

    Nine members, two marked. The operator has said those two do not belong with the other seven.
    They have NOT said the other seven belong together, and they have NOT said the two belong to
    each other — and the identity below is what makes "nothing else" checkable rather than merely
    promised:

        m*r  +  r(r-1)/2  +  m(m-1)/2   =   n(n-1)/2
        14   +     21     +      1      =      36
    """
    members = _bag(9)
    marked = frozenset({0, 1})
    m, r, n = len(marked), len(members) - len(marked), len(members)

    asserted = {(i, j) for i in range(n) for j in range(i + 1, n) if (i in marked) != (j in marked)}
    within_rest = {
        (i, j) for i in range(n) for j in range(i + 1, n) if i not in marked and j not in marked
    }
    within_marked = {
        (i, j) for i in range(n) for j in range(i + 1, n) if i in marked and j in marked
    }

    assert len(asserted) == m * r == 14
    assert len(within_rest) == r * (r - 1) // 2 == 21
    assert len(within_marked) == m * (m - 1) // 2 == 1
    # Nothing is unaccounted for, and nothing is counted twice.
    assert asserted | within_rest | within_marked == {
        (i, j) for i in range(n) for j in range(i + 1, n)
    }
    assert not (asserted & within_rest) and not (asserted & within_marked)
    assert len(asserted) + len(within_rest) + len(within_marked) == n * (n - 1) // 2 == 36


def test_penalize_touches_exactly_the_asserted_pairs_and_no_others() -> None:
    """The same claim, one level down: `penalize` moves the asserted cells and leaves the rest.

    With every member a distinct (class, device), one member pair is one class cell and one device
    cell, so the counts are directly comparable — which is precisely why the fixture is built that
    way rather than taken from the corpus.
    """
    members = _bag(9)
    marked = frozenset({0, 1})
    learner = _seed(members)
    before_a, before_e = dict(learner.A.pairs), dict(learner.E.pairs)

    learner.penalize(members, marked)
    moved_a, moved_e = _scaled(learner, before_a, before_e)

    expected = {
        _pair(members[i][0], members[j][0])
        for i in range(9)
        for j in range(i + 1, 9)
        if (i in marked) != (j in marked)
    }
    assert moved_a == expected
    assert len(moved_a) == 14, "|marked| x |rest| = 2 x 7"
    assert len(moved_e) == 14

    # And the untouched cells are untouched — the remainder is UNASSERTED, not asserted positive.
    for i in range(2, 9):
        for j in range(i + 1, 9):
            key = _pair(members[i][0], members[j][0])
            assert learner.A.pairs[key][0] == before_a[key][0], (
                "a pair inside the remainder moved; the operator said nothing about it"
            )
    inside_marked = _pair(members[0][0], members[1][0])
    assert learner.A.pairs[inside_marked][0] == before_a[inside_marked][0], (
        "the pair inside the marked set moved; the operator said nothing about it either"
    )


def test_marking_every_member_asserts_nothing_because_there_is_no_rest() -> None:
    """A degenerate case that must not silently become "penalise everything".

    If `rest` is empty then `|marked| x |rest| = 0`, and zero asserted pairs is the correct
    reading of "these nine members all do not belong" — which is not a statement about any pair.
    """
    members = _bag(9)
    learner = _seed(members)
    before_a, before_e = dict(learner.A.pairs), dict(learner.E.pairs)
    learner.penalize(members, frozenset(range(9)))
    assert _scaled(learner, before_a, before_e) == (set(), set())


# --- §2 parity ---------------------------------------------------------------------------------


def test_a_plain_split_is_byte_identical_to_v090() -> None:
    """**No exclusion, no change.** The overwhelming majority of splits will keep arriving without
    one, and a release that quietly altered them would have changed the meaning of every label
    already in the corpus (DECISIONS #125).

    Compared against the v0.9.0 implementation, restated here rather than imported so that a future
    edit to `penalize` cannot silently redefine what parity means.
    """
    members = _bag(9)

    def v090_penalize(learner: Learner, bag: list[tuple[int, int]]) -> None:
        sample = bag[:EPOCH_PAIR_CAP]
        class_pairs = {_pair(a[0], b[0]) for i, a in enumerate(sample) for b in sample[i + 1 :]}
        device_pairs = {
            _pair(a[1], b[1]) for i, a in enumerate(sample) for b in sample[i + 1 :] if a[1] != b[1]
        }
        for a_id, b_id in class_pairs:
            learner.A.scale_pair(a_id, b_id, SPLIT_PENALTY)
        for a_id, b_id in device_pairs:
            learner.E.scale_pair(a_id, b_id, SPLIT_PENALTY)

    new, old = _seed(members), _seed(members)
    new.penalize(members)  # marked=None
    v090_penalize(old, members)

    assert new.A.pairs == old.A.pairs
    assert new.E.pairs == old.E.pairs
    assert len(new.A.pairs) == 36, "the plain path still spans every pair of the bag"


# --- §3 bounds ---------------------------------------------------------------------------------


@pytest.mark.parametrize("size", [2, 3, 9, 21, 40])
@pytest.mark.parametrize("marked_count", [0, 1, 2, 5])
def test_an_exclusion_never_penalises_more_than_no_exclusion(size: int, marked_count: int) -> None:
    """**The bound, and it is provable rather than measured** (DECISIONS #125).

    The asserted pairs are a SUBSET of the pairs `penalize` visits without an exclusion, so the
    distinct cells they span are subsets too, and the penalised count can never exceed today's.
    Checked across bag sizes either side of `EPOCH_PAIR_CAP`, because the cap is applied before
    marking and that is exactly where an off-by-one would hide.
    """
    members = _bag(size)
    marked = frozenset(range(min(marked_count, size)))

    plain = _seed(members)
    before_a, before_e = dict(plain.A.pairs), dict(plain.E.pairs)
    plain.penalize(members)
    plain_a, plain_e = _scaled(plain, before_a, before_e)

    partial = _seed(members)
    before_a2, before_e2 = dict(partial.A.pairs), dict(partial.E.pairs)
    partial.penalize(members, marked)
    part_a, part_e = _scaled(partial, before_a2, before_e2)

    assert part_a <= plain_a, "an asserted class cell that a plain split would not have touched"
    assert part_e <= plain_e, "an asserted device cell that a plain split would not have touched"
    assert len(part_a) + len(part_e) <= len(plain_a) + len(plain_e)


def test_a_mark_beyond_the_pair_cap_asserts_nothing_rather_than_everything() -> None:
    """`EPOCH_PAIR_CAP` samples the first twenty members BEFORE marking is considered, so a mark
    on member 300 of a 500-member bag survives into no asserted pair.

    Resolving to **nothing penalised** rather than to *everything* penalised is the §10 rule —
    ambiguity about what was asserted resolves to less — and it is also the direction the bound
    above requires.
    """
    members = _bag(60)
    learner = _seed(members)
    before_a, before_e = dict(learner.A.pairs), dict(learner.E.pairs)
    learner.penalize(members, frozenset({55}))  # outside the first EPOCH_PAIR_CAP members
    assert _scaled(learner, before_a, before_e) == (set(), set())


# --- the engine path ---------------------------------------------------------------------------


_NEXT = [0]  # a monotonic allocator, so two situations in one test never collide on device.ip


async def _situation(store: Store, engine: Engine, n: int) -> tuple[int, list[int]]:
    """One situation with `n` members, each a distinct device and class."""
    alarms: list[int] = []
    async with store.lock:
        sid = await store.create_situation(TS, None)
        for _i in range(n):
            _NEXT[0] += 1
            unique = _NEXT[0]
            cur = await store.conn.execute(
                "INSERT INTO device (ip, first_seen, last_seen) VALUES (?, ?, ?) RETURNING id",
                (f"10.{unique // 250}.{unique % 250}.1", TS, TS),
            )
            dev = int((await cur.fetchone())[0])  # type: ignore[index]
            cur = await store.conn.execute(
                "INSERT INTO alarm_class (oid, first_seen, last_seen) VALUES (?, ?, ?) "
                "RETURNING id",
                (f"1.3.6.1.4.1.99.{unique}", TS, TS),
            )
            cls = int((await cur.fetchone())[0])  # type: ignore[index]
            cur = await store.conn.execute(
                "INSERT INTO alarm (device_id, class_id, instance, status, first_seen, last_seen, "
                "count) VALUES (?, ?, '', 'active', ?, ?, 1) RETURNING id",
                (dev, cls, TS, TS),
            )
            aid = int((await cur.fetchone())[0])  # type: ignore[index]
            await store.add_alarm_to_situation(sid, aid)
            alarms.append(aid)
        await store.commit()
    engine.members[sid] = [Member(aid, 100 + i, 200 + i, TS) for i, aid in enumerate(alarms)]
    return sid, alarms


async def test_the_exclusion_is_recorded_exactly_as_made(store: Store) -> None:
    """End to end: the marked members land in `feedback_exclusion`, the label row carries the
    counts that make the assertion interpretable, and the remainder stays **unasserted**."""
    engine = Engine(store, asyncio.Queue())
    await engine.start()
    sid, alarms = await _situation(store, engine, 9)

    async with store.lock:
        await engine.apply_feedback(
            sid,
            "split",
            TS + 10.0,
            principal_ref="alice",
            role="editor",
            label=LabelContext(exclusion=Exclusion.accept(alarms[:2], None)),
        )
        await store.commit()

    cur = await store.conn.execute(
        "SELECT id, member_count, excluded_count, excluded_truncated, remainder_together "
        "FROM feedback"
    )
    row = dict(next(iter(await cur.fetchall())))
    assert row["member_count"] == 9
    assert row["excluded_count"] == 2
    assert row["excluded_truncated"] == 0
    assert row["remainder_together"] is None, "the remainder was NOT asserted, and must read so"
    assert await store.feedback_exclusion(int(row["id"])) == alarms[:2]


async def test_a_confirm_carrying_an_exclusion_records_no_exclusion(store: Store) -> None:
    """A `confirm` already asserts every pair positive, so an exclusion on one is a
    **contradiction** rather than evidence, and it is dropped (DECISIONS #124).

    The request still succeeds; nothing about its outcome changes. Ambiguity about what the
    operator asserted resolves to *less*.
    """
    engine = Engine(store, asyncio.Queue())
    await engine.start()
    sid, alarms = await _situation(store, engine, 5)

    async with store.lock:
        recorded = await engine.apply_feedback(
            sid,
            "confirm",
            TS + 10.0,
            label=LabelContext(exclusion=Exclusion.accept(alarms[:2], True)),
        )
        await store.commit()

    assert recorded.exists and recorded.inserted, "the verdict itself is unaffected"
    cur = await store.conn.execute(
        "SELECT excluded_count, remainder_together FROM feedback WHERE verdict='confirm'"
    )
    row = dict(next(iter(await cur.fetchall())))
    assert row["excluded_count"] is None and row["remainder_together"] is None
    assert await store.feedback_exclusion(recorded.id or 0) == []


async def test_a_changed_verdict_replaces_the_marking_rather_than_merging_it(
    store: Store,
) -> None:
    """A corrected verdict re-posts (F36), and the second post's marking is the one that matches
    its row. A position-by-position replace would leave the tail of a longer earlier marking
    behind, and the row would then assert negatives the operator had **withdrawn**.
    """
    engine = Engine(store, asyncio.Queue())
    await engine.start()
    sid, alarms = await _situation(store, engine, 6)

    async with store.lock:
        first = await engine.apply_feedback(
            sid, "split", TS + 1.0, label=LabelContext(exclusion=Exclusion.accept(alarms[:4], None))
        )
        await store.commit()
    assert first.id is not None
    assert len(await store.feedback_exclusion(first.id)) == 4

    # The same (situation, verdict) again, marking fewer members.
    async with store.lock:
        await store.add_feedback_exclusion(first.id, alarms[:1])
        await store.commit()
    assert await store.feedback_exclusion(first.id) == alarms[:1], "the withdrawn marks survived"


# --- §4 the existence oracle -------------------------------------------------------------------


async def test_a_marked_id_that_does_not_exist_changes_nothing(store: Store) -> None:
    """**Not an existence oracle**, in status, body and timing.

    `excluded_ids` is client-supplied on a write path that has already produced F34, F35 and F39.
    An id naming nothing — or naming something the principal cannot see — is recorded exactly as
    reported and changes nothing observable. It simply asserts nothing, because there is no pair
    for it to be about.
    """
    engine, _queue, app = await authutil.make_env(store)
    sid, alarms = await _situation(store, engine, 5)
    ghost = 999_999_999

    # A DIFFERENT situation for each post, so the (situation, verdict) uniqueness does not make
    # the second one a no-op for reasons unrelated to what is being tested.
    sid2, _alarms2 = await _situation(store, engine, 5)
    client = await authutil.client_as(app, "editor")
    try:
        real = await client.post(
            f"/api/situations/{sid}/feedback",
            json={"verdict": "split", "excluded_ids": [alarms[0]]},
        )
        fake = await client.post(
            f"/api/situations/{sid2}/feedback",
            json={"verdict": "split", "excluded_ids": [ghost]},
        )
    finally:
        await client.aclose()

    assert real.status_code == fake.status_code == 200
    assert real.json() == fake.json() == {"status": "recorded", "verdict": "split"}

    # And the ghost is recorded VERBATIM — evidence, not validation.
    cur = await store.conn.execute("SELECT f.id FROM feedback f WHERE f.situation_id=?", (sid2,))
    fid = int((await cur.fetchone())[0])  # type: ignore[index]
    assert await store.feedback_exclusion(fid) == [ghost]


async def test_the_oracle_is_closed_in_timing_too(store: Store) -> None:
    """The half a status-and-body test cannot cover.

    A marked id that exists resolves to a position in the server's bag and a few set operations; one
    that does not resolves to nothing. The difference must not be measurable against the HTTP and
    SQLite work that dominates the request. Compared as medians over repeated posts, and with a
    deliberately loose factor: this asserts *"no usable signal"*, not a benchmark, and a tight
    bound here would be a flaky test rather than a stronger claim.
    """
    engine, _queue, app = await authutil.make_env(store)
    ghost = 999_999_999

    async def timed(exists: bool) -> float:
        sid, alarms = await _situation(store, engine, 9)
        marked = alarms[:3] if exists else [ghost, ghost + 1, ghost + 2]
        client = await authutil.client_as(app, "editor")
        try:
            start = time.perf_counter()
            resp = await client.post(
                f"/api/situations/{sid}/feedback",
                json={"verdict": "split", "excluded_ids": marked},
            )
            elapsed = time.perf_counter() - start
        finally:
            await client.aclose()
        assert resp.status_code == 200
        return elapsed

    real = sorted([await timed(True) for _ in range(7)])[3]
    fake = sorted([await timed(False) for _ in range(7)])[3]
    ratio = max(real, fake) / max(min(real, fake), 1e-9)
    assert ratio < 3.0, (
        f"existence is measurable in timing: real={real:.6f}s fake={fake:.6f}s ratio={ratio:.2f}"
    )


# --- the close channel (v0.9.1, Workstream 2) --------------------------------------------------


async def test_a_close_recorded_verdict_carries_the_close_channel(store: Store) -> None:
    """**The channel test.** A verdict given while resolving a situation is a different population
    from one given while browsing, and the two must never be blended (DECISIONS #126)."""
    engine, _queue, app = await authutil.make_env(store)
    sid, _alarms = await _situation(store, engine, 4)
    sid2, _a2 = await _situation(store, engine, 4)

    client = await authutil.client_as(app, "editor")
    try:
        card = await client.post(f"/api/situations/{sid}/feedback", json={"verdict": "confirm"})
        closed = await client.post(f"/api/situations/{sid2}/close", json={"verdict": "confirm"})
    finally:
        await client.aclose()

    assert card.status_code == 200 and closed.status_code == 200
    assert closed.json() == {"status": "closed"}

    cur = await store.conn.execute(
        "SELECT situation_id, acquisition_channel FROM feedback ORDER BY situation_id"
    )
    channels = {int(r[0]): r[1] for r in await cur.fetchall()}
    assert channels[sid] == "organic", "a card-recorded verdict is organic"
    assert channels[sid2] == "close", "a close-recorded verdict is close"


async def test_closing_without_a_verdict_is_unchanged(store: Store) -> None:
    """The half that must NOT move. Closing without judging stays exactly as easy as it was: no
    body, an empty body, and a body with a null verdict all behave as v0.9.0 did, and none of them
    writes a label."""
    engine, _queue, app = await authutil.make_env(store)
    ids = [(await _situation(store, engine, 3))[0] for _ in range(3)]

    client = await authutil.client_as(app, "editor")
    try:
        no_body = await client.post(f"/api/situations/{ids[0]}/close")
        empty = await client.post(f"/api/situations/{ids[1]}/close", json={})
        explicit = await client.post(f"/api/situations/{ids[2]}/close", json={"verdict": None})
    finally:
        await client.aclose()

    for resp in (no_body, empty, explicit):
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"status": "closed"}
    cur = await store.conn.execute("SELECT COUNT(*) FROM feedback")
    row = await cur.fetchone()
    assert row is not None and int(row[0]) == 0, "closing without judging wrote a label"


async def test_a_close_carrying_a_verdict_records_the_same_label_a_card_would(
    store: Store,
) -> None:
    """A label acquired through `close` must be **identical in every respect but its channel** —
    same bag, same digest, same coverage, same scope fingerprint, same exclusion discipline.

    That identity is what the channel column's value depends on: if the two differed in some other
    way nobody intended, a per-channel comparison would be measuring the difference rather than
    the population.
    """
    engine, _queue, app = await authutil.make_env(store)
    sid, alarms = await _situation(store, engine, 5)
    sid2, alarms2 = await _situation(store, engine, 5)

    body = {"verdict": "split", "member_ids": None, "excluded_ids": None}
    client = await authutil.client_as(app, "editor")
    try:
        await client.post(
            f"/api/situations/{sid}/feedback", json={**body, "excluded_ids": alarms[:2]}
        )
        await client.post(
            f"/api/situations/{sid2}/close", json={**body, "excluded_ids": alarms2[:2]}
        )
    finally:
        await client.aclose()

    cur = await store.conn.execute(
        "SELECT situation_id, verdict, member_count, excluded_count, excluded_truncated, "
        "coverage, capture_provenance, scope_restricted, acquisition_channel "
        "FROM feedback ORDER BY situation_id"
    )
    rows = [dict(r) for r in await cur.fetchall()]
    assert len(rows) == 2
    card, closed = rows
    assert card["acquisition_channel"] == "organic"
    assert closed["acquisition_channel"] == "close"
    for field in (
        "verdict",
        "member_count",
        "excluded_count",
        "excluded_truncated",
        "coverage",
        "capture_provenance",
        "scope_restricted",
    ):
        assert card[field] == closed[field], f"the two channels disagree on {field}"
    assert closed["excluded_count"] == 2, "the close path records the exclusion too"


async def test_a_close_with_a_verdict_needs_feedback_write_as_well(store: Store) -> None:
    """`feedback.write` and `situation.close` are **distinct capabilities**, and a stored policy
    may grant one while restricting the other. A close carrying a verdict needs both.

    Refused rather than silently stripped of its verdict: dropping a judgement without saying so is
    the failure this release exists to end. Closing WITHOUT a verdict is unaffected.
    """
    from netcorenoc.crosscutting import rbac

    engine, _queue, app = await authutil.make_env(store)
    sid, _alarms = await _situation(store, engine, 3)
    sid2, _a2 = await _situation(store, engine, 3)

    # A policy that keeps `situation.close` and drops `feedback.write` — reachable, because
    # `resolve_capabilities` is `ceiling ∩ policy`.
    kept = sorted(rbac.PERMISSIONS.keys() - {"feedback.write"})
    document = json.dumps(
        {"version": 1, "roles": {"editor": kept}}, sort_keys=True, separators=(",", ":")
    )
    async with store.lock:
        policy_id = await store.insert_governance_policy("rbac", document, "h" * 64, None, 0.0, "")
        await store.set_active_governance_policy("rbac", policy_id, None, 0.0)
        await store.commit()

    client = await authutil.client_as(app, "editor")
    try:
        with_verdict = await client.post(
            f"/api/situations/{sid}/close", json={"verdict": "confirm"}
        )
        without = await client.post(f"/api/situations/{sid2}/close", json={})
    finally:
        await client.aclose()

    assert with_verdict.status_code == 403, "a verdict was recorded without `feedback.write`"
    assert without.status_code == 200, "closing without a verdict must be unaffected"
    cur = await store.conn.execute("SELECT COUNT(*) FROM feedback")
    row = await cur.fetchone()
    assert row is not None and int(row[0]) == 0


async def test_marking_nothing_records_no_assertion_rather_than_an_empty_one(
    store: Store,
) -> None:
    """`excluded_count` is **NULL, never 0** — migration `0010` states that "0 cannot occur", and
    until this test nothing checked it (v0.9.1's test audit, seed L4).

    The distinction is the release's whole discipline in one column. *"The operator marked
    nothing"* is a **plain** split, which asserts the bag is at least two situations and nothing
    about any pair. A stored 0 would say something different and weaker-sounding but strictly
    stronger: *"the operator made an exclusion assertion, and it was empty"* — a claim nobody made,
    and one a later release could reasonably read as "the operator considered the members and
    excluded none of them".

    The shipped UI cannot produce this (it sends `excluded_ids` only when something is ticked), so
    the path is reachable only by a direct API call — which is exactly the kind of input this write
    path is required to handle without inventing meaning.
    """
    engine, _queue, app = await authutil.make_env(store)
    sid, _alarms = await _situation(store, engine, 4)

    client = await authutil.client_as(app, "editor")
    try:
        resp = await client.post(
            f"/api/situations/{sid}/feedback", json={"verdict": "split", "excluded_ids": []}
        )
    finally:
        await client.aclose()

    assert resp.status_code == 200, resp.text
    cur = await store.conn.execute("SELECT id, excluded_count FROM feedback")
    row = dict(next(iter(await cur.fetchall())))
    assert row["excluded_count"] is None, (
        "an empty marked set was recorded as an assertion about nothing (0) rather than as no "
        "assertion (NULL); `0010` says 0 cannot occur"
    )
    assert await store.feedback_exclusion(int(row["id"])) == []
