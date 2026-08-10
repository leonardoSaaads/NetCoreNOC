"""The evidence boundary (v0.9.2): F46, F47, and the guards that would notice if either returned.

`docs/architecture/EVIDENCE-BOUNDARY-0.9.2.md` is the argument. This is what holds it up.

Six sections, and each answers a different question:

* §1 **the partition** — the property that the old fixed-fixture identity could not express;
* §2 **reconciliation** — reported versus reconciled, with a control in each direction;
* §3 **hostile input** — every boundary the marked set has, driven over HTTP where possible;
* §4 **parity** — `learn.penalize`, and the response in status, body **and timing**;
* §5 **drift** — a row corrupted directly in the database is reported and **not corrected**;
* §6 **retention** — one clock, and what survives a prune pass.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import time
from typing import Any

import pytest

from netcorenoc import bias, labels, learn
from netcorenoc.capture import Exclusion, LabelContext, LabelScope
from netcorenoc.main import Engine
from netcorenoc.rootcause import Member
from netcorenoc.store import Store

import authutil

TS = 1_000_000.0
GHOST = 999_999_999

_NEXT = [0]


# --- §1 the partition, as a property rather than a fixture ---------------------------------------


def _partition(n: int, m: int) -> tuple[int, int, int]:
    """The three components of a partial split's pair partition: bag of `n`, `m` marked."""
    r = n - m
    return m * r, r * (r - 1) // 2, m * (m - 1) // 2


@pytest.mark.parametrize("n", [0, 1, 2, 3, 4, 9, 20, 60])
@pytest.mark.parametrize("m_reported", [0, 1, 2, 3, 9, 60, 512, 600])
def test_the_partition_is_non_negative_and_closed_over_the_reconciled_count(
    n: int, m_reported: int
) -> None:
    """**The invariant, made non-vacuous** (F46, DECISIONS #136).

    Driven through `labels._assertion` — the production expression — rather than over a number this
    test made up, so that reverting the reconciliation makes it **fail** rather than merely making
    it irrelevant. The bag is `1..n`; the reported marks are `1..m_reported`, so `m_reported - n` of
    them name nothing.

    Three assertions, and their strengths are deliberately unequal:

    1. ``0 <= m <= n`` — the half the old identity never had;
    2. every component ``>= 0`` — **the discriminating half**;
    3. the three sum to ``n(n-1)/2``.

    **(3) alone is an algebraic identity in `m` and `n` and CANNOT FAIL.** Substitute ``r = n - m``
    and expand: ``m`` cancels entirely, so the sum closes for every integer ``m``, including
    ``m > n``. At ``n = 4, m = 512`` it closes exactly while the asserted component is ``-260,096``.
    That is why migration `0010`'s "the arithmetic closes, which is what makes *and nothing else*
    checkable" was not the guarantee it read as, and why (1) and (2) exist.

    **And (2) is necessary but NOT sufficient**, which is what `docs/gates/v0.9.2-phase-0.md` §2a
    measured. At ``n = 60`` with thirty marks that name nothing, every component would be ``>= 0``
    and the sum would close while the label asserted **nothing at all**. No arithmetic property can
    discriminate that case; only the **intersection** can, which is why this test reads the
    reconciled count out of the production path instead of computing one.

    Deterministically generated — an enumerated grid, no RNG and no seed, so a failure names one
    ``(n, m_reported)`` and there is no run-to-run variance to argue about.
    """
    bag = list(range(1, n + 1))
    reported = list(range(1, m_reported + 1))
    fields = labels._assertion(Exclusion(reported), bag, None)
    m = fields["excluded_reconciled"] or 0

    asserted, within_rest, within_marked = _partition(n, m)

    assert 0 <= m <= n, "the reconciled count escaped its bag"
    assert asserted >= 0, "asserted negative pairs cannot be negative"
    assert within_rest >= 0, "unasserted pairs within the remainder cannot be negative"
    assert within_marked >= 0, "unasserted pairs within the marked set cannot be negative"
    assert asserted + within_rest + within_marked == n * (n - 1) // 2


@pytest.mark.parametrize("n,m", [(4, 10), (4, 512), (0, 5), (1, 3), (9, 100)])
def test_the_sum_identity_alone_closes_on_input_it_should_reject(n: int, m: int) -> None:
    """**The proof that (3) above is not a guard**, kept as a test so nobody re-promotes it.

    Every case here is one the shipped v0.9.1 code could produce and one the identity accepts
    silently. If a future release deletes the non-negativity assertions above and keeps only the
    sum, this test still passes — which is exactly the point: it documents that the sum has no
    discriminating power, so the sum can never be the reason anyone believes the corpus is sound.
    """
    asserted, within_rest, within_marked = _partition(n, m)
    assert asserted + within_rest + within_marked == n * (n - 1) // 2, (
        "the identity is algebraic in m and n; it closes even here"
    )
    assert min(asserted, within_rest, within_marked) < 0, (
        "this fixture is supposed to be a case the identity accepts and non-negativity rejects"
    )


# --- fixtures ------------------------------------------------------------------------------------


async def _situation(
    store: Store, engine: Engine | None, n: int, *, hidden_from: int | None = None
) -> tuple[int, list[int]]:
    """One situation with `n` members, each its own NE and device.

    Members from index `hidden_from` onward are addressed in `10.9.0.0/16`; the rest in
    `10.1.0.0/16`, which is the block the scope policy in §3 grants an editor.
    """
    alarms: list[int] = []
    async with store.lock:
        sid = await store.create_situation(TS, None)
        for idx in range(n):
            _NEXT[0] += 1
            u = _NEXT[0]
            block = "10.9" if hidden_from is not None and idx >= hidden_from else "10.1"
            ip = f"{block}.{u // 250}.{u % 250 + 1}"
            cur = await store.conn.execute(
                "INSERT INTO device (ip, first_seen, last_seen) VALUES (?, ?, ?) RETURNING id",
                (ip, TS, TS),
            )
            dev = int((await cur.fetchone())[0])  # type: ignore[index]
            cur = await store.conn.execute(
                "INSERT INTO ne (ip, first_seen, last_seen) VALUES (?, ?, ?) RETURNING id",
                (ip, TS, TS),
            )
            ne = int((await cur.fetchone())[0])  # type: ignore[index]
            cur = await store.conn.execute(
                "INSERT INTO alarm_class (oid, first_seen, last_seen) VALUES (?, ?, ?) "
                "RETURNING id",
                (f"1.3.6.1.4.1.99.{u}", TS, TS),
            )
            cls = int((await cur.fetchone())[0])  # type: ignore[index]
            cur = await store.conn.execute(
                "INSERT INTO alarm (device_id, class_id, instance, status, first_seen, last_seen, "
                "count, ne_id) VALUES (?, ?, '', 'active', ?, ?, 1, ?) RETURNING id",
                (dev, cls, TS, TS, ne),
            )
            aid = int((await cur.fetchone())[0])  # type: ignore[index]
            await store.add_alarm_to_situation(sid, aid)
            alarms.append(aid)
        await store.commit()
    if engine is not None:
        engine.members[sid] = [Member(a, 100 + i, 200 + i, TS) for i, a in enumerate(alarms)]
    return sid, alarms


SCOPE_POLICY = json.dumps({"version": 1, "roles": {"editor": ["10.1.0.0/16"]}})


async def _install_scope(store: Store) -> int:
    import hashlib

    async with store.lock:
        pid = await store.insert_governance_policy(
            "scope", SCOPE_POLICY, hashlib.sha256(SCOPE_POLICY.encode()).hexdigest(), "adm", TS, ""
        )
        await store.set_active_governance_policy("scope", pid, "adm", TS)
        await store.commit()
    return pid


async def _label(app: object, sid: int, ids: list[int], role: str = "editor") -> Any:
    client = await authutil.client_as(app, role)
    try:
        return await client.post(
            f"/api/situations/{sid}/feedback", json={"verdict": "split", "excluded_ids": ids}
        )
    finally:
        await client.aclose()


async def _row(store: Store, sid: int) -> dict[str, Any]:
    cur = await store.conn.execute(
        "SELECT member_count, excluded_count, excluded_reconciled, excluded_reconciled_source, "
        "excluded_reconciled_out_of_scope, excluded_truncated, scope_restricted, "
        "scope_redacted_members FROM feedback WHERE situation_id=?",
        (sid,),
    )
    row = await cur.fetchone()
    assert row is not None
    return dict(row)


# --- §2 reconciliation ---------------------------------------------------------------------------


async def test_reported_and_reconciled_differ_exactly_when_they_should(store: Store) -> None:
    """**The reconciliation, with a control in each direction** (F46).

    Equal where they should be equal — every marked id names a member of the bag — and different
    where they should differ, by exactly the number of ids that named nothing. A test that only
    showed them differing would not distinguish reconciliation from a constant.
    """
    engine, _q, app = await authutil.make_env(store)

    # CONTROL: an honest label. Reported and reconciled MUST be equal.
    sid, alarms = await _situation(store, engine, 4)
    assert (await _label(app, sid, alarms[:2])).status_code == 200
    honest = await _row(store, sid)
    assert honest["excluded_count"] == 2
    assert honest["excluded_reconciled"] == 2, "an honest label must reconcile to itself"
    assert honest["excluded_reconciled_source"] == "live"

    # The finding: ten ghosts on a four-member bag.
    sid2, _ = await _situation(store, engine, 4)
    assert (await _label(app, sid2, [GHOST + i for i in range(10)])).status_code == 200
    hostile = await _row(store, sid2)
    assert hostile["excluded_count"] == 10, "tier 1 still records what the client said, verbatim"
    assert hostile["excluded_reconciled"] == 0, "none of the ten named a member of the bag"

    # The QUIET case, which is the one that mattered: 30 ghosts on a 60-member bag produced +900
    # asserted negative pairs from zero real assertions.
    sid3, _ = await _situation(store, engine, 60)
    assert (await _label(app, sid3, [GHOST - i for i in range(30)])).status_code == 200
    quiet = await _row(store, sid3)
    assert quiet["excluded_count"] == 30
    assert quiet["excluded_reconciled"] == 0

    # The corpus-level consequence — that `asserted_negative_pairs` now totals 4 rather than
    # -259,084 — is Workstream 4's, and is asserted in
    # `test_the_corpus_total_is_derived_from_the_reconciled_count` once the consumers read tier 2.


async def test_the_reconciled_count_is_exactly_what_penalize_uses(store: Store) -> None:
    """The report and the learner read **one** quantity, so they cannot drift apart.

    `excluded_reconciled` is `len(Exclusion.marked_positions(bag))` — the same value
    `learn.penalize` has always resolved. Asserted directly rather than left as a comment, because
    "these two happen to agree" is a property that decays and "these two are the same expression" is
    one that does not.
    """
    engine, _q, app = await authutil.make_env(store)
    sid, alarms = await _situation(store, engine, 6)
    marks = [alarms[0], alarms[3], GHOST]
    assert (await _label(app, sid, marks)).status_code == 200

    row = await _row(store, sid)
    positions = Exclusion(marks).marked_positions(alarms)
    assert row["excluded_reconciled"] == len(positions) == 2


async def test_a_mark_repeated_is_one_mark(store: Store) -> None:
    """`feedback_exclusion` is keyed on `(feedback_id, position)`, so `[5, 5, 5]` is legal input.

    Three evidence rows, **one** reconciled mark — otherwise a one-member bag marked three times
    would reconcile to 3 against a `member_count` of 1, which the schema `CHECK` would refuse and
    which would turn a hostile label into a failed write.
    """
    engine, _q, app = await authutil.make_env(store)
    sid, alarms = await _situation(store, engine, 3)
    assert (await _label(app, sid, [alarms[0]] * 3)).status_code == 200

    row = await _row(store, sid)
    assert row["excluded_count"] == 3, "the report is recorded verbatim: three ids were sent"
    assert row["excluded_reconciled"] == 1, "they named one member"
    cur = await store.conn.execute("SELECT COUNT(*) FROM feedback_exclusion")
    row3 = await cur.fetchone()
    assert row3 is not None and int(row3[0]) == 3, "all three are stored as evidence"


# --- §3 hostile input, at every boundary the marked set has --------------------------------------


async def test_marking_members_of_a_different_situation_asserts_nothing(store: Store) -> None:
    """Real alarm ids, real members — **of somebody else's bag**. The closest thing to a plausible
    forgery, and it must reconcile to zero rather than to "these ids exist"."""
    engine, _q, app = await authutil.make_env(store)
    sid_a, _alarms_a = await _situation(store, engine, 4)
    sid_b, alarms_b = await _situation(store, engine, 4)

    assert (await _label(app, sid_a, alarms_b)).status_code == 200
    row = await _row(store, sid_a)
    assert row["excluded_count"] == 4, "four real ids were reported, and are recorded"
    assert row["excluded_reconciled"] == 0, "none of them is a member of THIS bag"
    assert sid_b  # the other situation is untouched and unlabelled
    cur = await store.conn.execute("SELECT COUNT(*) FROM feedback WHERE situation_id=?", (sid_b,))
    assert int((await cur.fetchone())[0]) == 0  # type: ignore[index]


async def test_marking_every_member_reconciles_to_the_whole_bag(store: Store) -> None:
    """`m = n`: a legal boundary. The assertion is `m * (n - m) = 0` pairs, which is the correct
    reading of "all nine of these do not belong" — not a statement about any pair."""
    engine, _q, app = await authutil.make_env(store)
    sid, alarms = await _situation(store, engine, 5)
    assert (await _label(app, sid, alarms)).status_code == 200

    row = await _row(store, sid)
    assert row["excluded_reconciled"] == row["member_count"] == 5
    async with store.lock:
        assert (await bias.collect(store))["asserted_negative_pairs"] == 0


async def test_an_empty_marked_list_records_no_assertion_at_all(store: Store) -> None:
    """`m = 0` with an empty list is a **plain** split, and NULL is how that is said.

    0 would be an assertion about an empty set, which is a different claim, and the two must stay
    distinguishable in the row alone.
    """
    engine, _q, app = await authutil.make_env(store)
    sid, _alarms = await _situation(store, engine, 4)
    assert (await _label(app, sid, [])).status_code == 200

    row = await _row(store, sid)
    assert row["excluded_count"] is None
    assert row["excluded_reconciled"] is None
    assert row["excluded_reconciled_source"] is None
    assert row["excluded_reconciled_out_of_scope"] is None


async def test_a_report_truncated_at_the_bound_reconciles_what_survived(store: Store) -> None:
    """At the truncation boundary the reconciled count is a **lower bound**, and the row says so
    through `excluded_truncated` (DECISIONS #135).

    600 ids sent, 512 kept. What must not happen is the v0.9.1 behaviour: `excluded_count = 512`
    multiplied against a four-member bag, which produced -260,096 asserted negative pairs from one
    request.

    **The fixture also fixes the ORDER of the two steps.** Two real members sit inside the kept
    prefix and a third sits at position 600, beyond the bound. Truncation happens first, so that
    third mark never reaches reconciliation and must not be counted; swapping the two steps would
    count it, and this is the only arrangement that can tell the orders apart.
    """
    engine, _q, app = await authutil.make_env(store)
    sid, alarms = await _situation(store, engine, 4)
    marks = [alarms[0], alarms[1]] + [GHOST - i for i in range(598)] + [alarms[2]]
    assert (await _label(app, sid, marks)).status_code == 200

    row = await _row(store, sid)
    assert row["excluded_count"] == 512, "tier 1: the bound, recorded rather than silently applied"
    assert row["excluded_truncated"] == 1
    assert row["excluded_reconciled"] == 2, (
        "a real member beyond the truncation bound was reconciled — truncation must come first"
    )


async def test_a_zero_member_bag_with_a_marking_reconciles_to_zero(store: Store) -> None:
    """`n = 0`: a verdict posted to an already-merged situation. The bag is empty and written as
    empty (v0.8.0), so every mark reconciles to nothing and the `CHECK` bound `0 <= m <= 0`
    holds."""
    engine, _q, app = await authutil.make_env(store)
    sid, alarms = await _situation(store, engine, 2)
    async with store.lock:
        await store.conn.execute("DELETE FROM situation_alarm WHERE situation_id=?", (sid,))
        await store.commit()
    engine.members.pop(sid, None)

    assert (await _label(app, sid, alarms)).status_code == 200
    row = await _row(store, sid)
    assert row["member_count"] == 0
    assert row["excluded_reconciled"] == 0


# --- §3b the scope of the marking (F47) ----------------------------------------------------------


async def test_a_mark_on_a_redacted_member_is_recorded_as_blind(store: Store) -> None:
    """**F47's repair.** The assertion is still accepted — this does not stop a scoped editor from
    making a blind assertion, it makes the assertion *legible*.

    Three marks: one visible member, one redacted member, one ghost. Two reconcile; one of those
    two was about a member the operator could not observe, and the row now says so.
    """
    engine, _q, app = await authutil.make_env(store)
    await _install_scope(store)
    sid, alarms = await _situation(store, engine, 6, hidden_from=3)
    visible, hidden = alarms[:3], alarms[3:]

    resp = await _label(app, sid, [visible[0], hidden[0], GHOST])
    assert resp.status_code == 200, "the response is unchanged; this is not a rejection"

    row = await _row(store, sid)
    assert row["scope_restricted"] == 1
    assert row["scope_redacted_members"] == 3, "how much of the BAG was hidden"
    assert row["excluded_count"] == 3
    assert row["excluded_reconciled"] == 2
    assert row["excluded_reconciled_out_of_scope"] == 1, "how much of the ASSERTION was blind"


async def test_zero_blind_marks_is_a_real_answer_and_not_a_silence(store: Store) -> None:
    """**Zero must be distinguishable from unknown** (DECISIONS #133).

    A restricted scope whose every reconciled mark was about a visible member records `0`, and that
    is a strong statement — stronger than the `NULL` a pre-`0011` row carries forever.
    """
    engine, _q, app = await authutil.make_env(store)
    await _install_scope(store)
    sid, alarms = await _situation(store, engine, 6, hidden_from=3)

    assert (await _label(app, sid, alarms[:2])).status_code == 200
    row = await _row(store, sid)
    assert row["scope_restricted"] == 1 and row["scope_redacted_members"] == 3
    assert row["excluded_reconciled_out_of_scope"] == 0, "a real zero, not an absence"


async def test_an_unrestricted_scope_records_zero_blind_marks(store: Store) -> None:
    """The clean population: nothing was hidden, so no mark could have been blind. Also the parity
    path — an appliance with no scope policy never touches the database to answer this."""
    engine, _q, app = await authutil.make_env(store)
    sid, alarms = await _situation(store, engine, 5)

    assert (await _label(app, sid, alarms[:2])).status_code == 200
    row = await _row(store, sid)
    assert row["scope_restricted"] == 0 and row["scope_redacted_members"] == 0
    assert row["excluded_reconciled_out_of_scope"] == 0


async def test_a_label_with_no_resolved_scope_records_unknown_not_zero(store: Store) -> None:
    """**The fabrication guard, at the level of one cell.**

    A verdict recorded through a path that resolved no scope cannot say whether the marks were
    observable, and `NULL` is the only honest answer. Writing `0` here would be inventing an
    observation — the same error as backfilling the column, one row at a time.
    """
    engine = Engine(store, asyncio.Queue())
    await engine.start()
    sid, alarms = await _situation(store, engine, 5)

    async with store.lock:
        await engine.apply_feedback(
            sid,
            "split",
            TS + 10.0,
            label=LabelContext(exclusion=Exclusion.accept(alarms[:2], None)),
        )
        await store.commit()

    row = await _row(store, sid)
    assert row["excluded_reconciled"] == 2, "tier 2 needs only the bag, and the bag is here"
    assert row["excluded_reconciled_out_of_scope"] is None, "tier 2 known, tier 3 unknown"


async def test_the_hidden_set_and_the_hidden_count_come_from_one_read(store: Store) -> None:
    """`scope_redacted_members` and `excluded_reconciled_out_of_scope` are derived from the **same**
    `Perimeter.hidden_member_ids` call (DECISIONS #137).

    Asserted structurally rather than by coincidence of values: the blind count can never exceed the
    redacted count, because the blind marks are a subset of the hidden members by construction. A
    second resolution is a second answer that can drift, and this is what would notice.
    """
    engine, _q, app = await authutil.make_env(store)
    await _install_scope(store)
    sid, alarms = await _situation(store, engine, 8, hidden_from=2)

    assert (await _label(app, sid, alarms[2:])).status_code == 200
    row = await _row(store, sid)
    assert row["excluded_reconciled_out_of_scope"] == 6
    assert row["excluded_reconciled_out_of_scope"] <= row["scope_redacted_members"]
    assert row["excluded_reconciled_out_of_scope"] <= row["excluded_reconciled"]


# --- §4 parity -----------------------------------------------------------------------------------


def _bag(n: int) -> list[tuple[int, int]]:
    return [(100 + i, 200 + i) for i in range(n)]


def _seed(members: list[tuple[int, int]]) -> learn.Learner:
    learner = learn.Learner()
    for i, a in enumerate(members):
        for b in members[i + 1 :]:
            learner.A.observe_pair(a[0], b[0], 1.0)
            if a[1] != b[1]:
                learner.E.observe_pair(a[1], b[1], 1.0)
    return learner


@pytest.mark.parametrize("size", [2, 5, 9, 21, 40])
@pytest.mark.parametrize("marked_count", [0, 1, 2, 5])
def test_penalize_is_byte_identical_to_v091(size: int, marked_count: int) -> None:
    """**The live learner did not change**, and this release may not have changed it by accident.

    `learn.penalize` was the one consumer that was already reading tier 2, through
    `LabelContext.marked_positions`. It is not touched by this release, and the proof is a direct
    comparison of the resulting matrices against a restatement of the v0.9.1 implementation — kept
    here rather than imported, so a future edit to `penalize` cannot silently redefine parity.
    """
    members = _bag(size)
    marked = frozenset(range(min(marked_count, size)))

    def v091_penalize(
        learner: learn.Learner, bag: list[tuple[int, int]], mk: frozenset[int]
    ) -> None:
        sample = bag[: learn.EPOCH_PAIR_CAP]
        pairs = [
            (a, b)
            for i, a in enumerate(sample)
            for j, b in enumerate(sample[i + 1 :], start=i + 1)
            if (i in mk) != (j in mk)
        ]
        for a, b in pairs:
            learner.A.scale_pair(a[0], b[0], learn.SPLIT_PENALTY)
            if a[1] != b[1]:
                learner.E.scale_pair(a[1], b[1], learn.SPLIT_PENALTY)

    new, old = _seed(members), _seed(members)
    new.penalize(members, marked)
    v091_penalize(old, members, marked)

    assert new.A.pairs == old.A.pairs
    assert new.E.pairs == old.E.pairs


async def test_a_ghost_marking_moves_no_matrix_cell(store: Store) -> None:
    """**The live learner reads tier 2, and this is what would notice if it stopped.**

    `LabelContext.marked_positions` intersects the reported ids with the server's own bag before
    `learn.penalize` ever sees them. Feed it a marking made entirely of ids that name nothing and
    **no cell may move** — a penalty applied on the strength of a client's say-so would be this
    release's defect appearing in the one place it never was.

    The control is the second half: the same shape of bag with a REAL marking must move exactly the
    cells `|marked| x |rest|` names, or the first half would pass on a learner that does nothing.
    """
    engine = Engine(store, asyncio.Queue())
    await engine.start()
    sid, _alarms = await _situation(store, engine, 6)
    learner = engine.learner
    members = engine.members[sid]
    for i, a in enumerate(members):
        for b in members[i + 1 :]:
            learner.A.observe_pair(a.class_id, b.class_id, 1.0)
    before = {k: v[0] for k, v in learner.A.pairs.items()}

    async with store.lock:
        await engine.apply_feedback(
            sid,
            "split",
            TS + 10.0,
            label=LabelContext(exclusion=Exclusion.accept([GHOST, GHOST - 1], None)),
        )
        await store.commit()
    assert {k: v[0] for k, v in learner.A.pairs.items()} == before, (
        "a marking of ids that name nothing moved a matrix cell"
    )

    # CONTROL: a real marking must move exactly |marked| x |rest| class cells.
    sid2, alarms2 = await _situation(store, engine, 6)
    members2 = engine.members[sid2]
    for i, a in enumerate(members2):
        for b in members2[i + 1 :]:
            learner.A.observe_pair(a.class_id, b.class_id, 1.0)
    before2 = {k: v[0] for k, v in learner.A.pairs.items()}
    async with store.lock:
        await engine.apply_feedback(
            sid2,
            "split",
            TS + 20.0,
            label=LabelContext(exclusion=Exclusion.accept(alarms2[:2], None)),
        )
        await store.commit()
    moved = {k for k, v in learner.A.pairs.items() if v[0] != before2.get(k)}
    assert len(moved) == 2 * 4, "the control moved no cells; the first half proves nothing"


async def test_the_response_is_unchanged_in_status_and_body(store: Store) -> None:
    """**Not an existence oracle**, and this release did not make it one.

    A real mark, a ghost mark, a mark on a member of another situation, and a mark on a redacted
    member all answer identically. Each goes to its own situation so that `(situation, verdict)`
    uniqueness is not what makes the later posts no-ops.
    """
    engine, _q, app = await authutil.make_env(store)
    await _install_scope(store)
    sids = []
    for _ in range(4):
        sid, alarms = await _situation(store, engine, 6, hidden_from=3)
        sids.append((sid, alarms))
    other = (await _situation(store, engine, 3))[1]

    cases = {
        "real": [sids[0][1][0]],
        "ghost": [GHOST],
        "another situation's member": [other[0]],
        "redacted member": [sids[3][1][5]],
    }
    seen = []
    for (sid, _alarms), (_name, marks) in zip(sids, cases.items(), strict=True):
        resp = await _label(app, sid, marks)
        seen.append((resp.status_code, resp.json()))

    assert seen == [(200, {"status": "recorded", "verdict": "split"})] * 4, seen


async def test_the_oracle_stays_closed_in_timing(store: Store) -> None:
    """The half a status-and-body test cannot cover.

    Reconciliation adds a set intersection over the bag and — on a restricted scope — one
    membership
    test per mark. Both are bounded by the bag, which is bounded by `MAX_CANDIDATES`, and neither
    depends on whether an id **exists**: a ghost and a real id both cost one hash lookup. The
    difference must stay unmeasurable against the HTTP and SQLite work that dominates the request.

    Medians over repeated posts, with a deliberately loose factor: this asserts *"no usable
    signal"*, not a benchmark, and a tight bound here would be a flaky test rather than a stronger
    claim.
    """
    engine, _q, app = await authutil.make_env(store)
    real_ms: list[float] = []
    ghost_ms: list[float] = []
    for i in range(12):
        sid_r, alarms_r = await _situation(store, engine, 8)
        sid_g, _alarms_g = await _situation(store, engine, 8)
        start = time.perf_counter()
        await _label(app, sid_r, [alarms_r[0]])
        real_ms.append((time.perf_counter() - start) * 1000)
        start = time.perf_counter()
        await _label(app, sid_g, [GHOST - i])
        ghost_ms.append((time.perf_counter() - start) * 1000)

    real, ghost = statistics.median(real_ms), statistics.median(ghost_ms)
    ratio = max(real, ghost) / max(min(real, ghost), 1e-9)
    assert ratio < 3.0, f"a timing signal distinguishes a real mark from a ghost: {real=} {ghost=}"


# --- §5 drift ------------------------------------------------------------------------------------


async def test_a_corrupted_row_is_reported_by_maintenance_and_not_corrected(store: Store) -> None:
    """**The drift check reports; it does not correct** (DECISIONS #134).

    The row is corrupted directly in the database — the only way to reach this state, since every
    write path computes the count rather than accepting one — and the maintenance sweep must
    *notice* and *say so* while leaving the corruption exactly where a reader can see it.

    A corrector would have made F46 invisible: every hostile row quietly repaired on the next pass,
    the reports looking right, and the write path staying broken indefinitely.
    """
    engine, _q, app = await authutil.make_env(store)
    sid, alarms = await _situation(store, engine, 6)
    assert (await _label(app, sid, alarms[:2])).status_code == 200
    assert (await _row(store, sid))["excluded_reconciled"] == 2

    # CONTROL: a healthy corpus reports no drift and raises no warning.
    async with store.lock:
        assert await store.reconciliation_drift() == []
    await engine.capture.verify_evidence(store)
    assert engine.capture.warnings() == []

    async with store.lock:
        await store.conn.execute(
            "UPDATE feedback SET excluded_reconciled=5 WHERE situation_id=?", (sid,)
        )
        await store.commit()

    async with store.lock:
        drift = await store.reconciliation_drift()
    assert len(drift) == 1
    assert drift[0]["stored"] == 5 and drift[0]["recomputed"] == 2

    await engine.capture.verify_evidence(store)
    warnings = engine.capture.warnings()
    assert len(warnings) == 1 and "NOTHING HAS BEEN CORRECTED" in warnings[0]

    # **And the row is untouched.** This is the assertion that makes the test about the decision
    # rather than about the query.
    assert (await _row(store, sid))["excluded_reconciled"] == 5


async def test_a_repeated_mark_is_not_reported_as_drift(store: Store) -> None:
    """**Ledger L3.** The drift query must count DISTINCT marks, exactly as the write path does.

    `feedback_exclusion` is keyed on `(feedback_id, position)`, so `[5, 5, 5]` is three legal rows
    and one mark. A drift check counting rows would report a healthy corpus as corrupt on every
    pass, which is worse than not checking: an operator who learns the warning is usually wrong
    stops reading it.
    """
    engine, _q, app = await authutil.make_env(store)
    sid, alarms = await _situation(store, engine, 4)
    assert (await _label(app, sid, [alarms[0]] * 3)).status_code == 200
    assert (await _row(store, sid))["excluded_reconciled"] == 1

    async with store.lock:
        assert await store.reconciliation_drift() == [], (
            "a repeated mark was reported as drift; the recomputation counted rows, not marks"
        )


async def test_the_maintenance_sweep_itself_runs_the_verification(store: Store) -> None:
    """**Ledger L13.** Driven through `capture.prune`, not through `verify_evidence` directly.

    A test that calls the verification by hand proves the query works and proves nothing about
    whether anything calls it. Deleting the call site left every other drift test green.
    """
    engine, _q, app = await authutil.make_env(store)
    sid, alarms = await _situation(store, engine, 6)
    assert (await _label(app, sid, alarms[:2])).status_code == 200
    async with store.lock:
        await store.conn.execute(
            "UPDATE feedback SET excluded_reconciled=5 WHERE situation_id=?", (sid,)
        )
        await store.commit()

    assert engine.capture.drift_rows == 0, "the control: nothing has swept yet"
    async with store.lock:
        await engine.capture.prune(store, TS + 1.0, engine.retention)
    assert engine.capture.drift_rows == 1, "the maintenance sweep did not run the verification"
    assert any("NOTHING HAS BEEN CORRECTED" in w for w in engine.capture.warnings())


async def test_a_redacted_member_marked_twice_is_one_blind_mark(store: Store) -> None:
    """**Ledger L15.** The blind count is taken over the RECONCILED marks, not over the report.

    The two agree on every input except a repeated mark, because the hidden set is a subset of the
    bag by construction — so this is the only fixture that can tell them apart, and without it the
    distinction is an equivalent mutant that nothing would notice.
    """
    engine, _q, app = await authutil.make_env(store)
    await _install_scope(store)
    sid, alarms = await _situation(store, engine, 6, hidden_from=3)

    assert (await _label(app, sid, [alarms[4], alarms[4], alarms[4]])).status_code == 200
    row = await _row(store, sid)
    assert row["excluded_count"] == 3, "three ids were reported, and are recorded verbatim"
    assert row["excluded_reconciled"] == 1
    assert row["excluded_reconciled_out_of_scope"] == 1, (
        "one redacted member, marked three times, is one blind mark"
    )


async def test_the_store_layer_refuses_exactly_at_the_boundary(store: Store) -> None:
    """**Ledger L6.** `n` is legal and `n + 1` is not, asserted at the boundary itself.

    The original test used `10` against a bag of `4`, which a precondition loosened by one still
    refuses — so it proved a bound existed without proving where it was.
    """
    engine, _q, _app = await authutil.make_env(store)
    sid, _alarms = await _situation(store, engine, 4)
    async with store.lock:
        recorded = await store.add_feedback(sid, "split", TS)
        assert recorded.id is not None
        await store.annotate_feedback(recorded.id, member_count=4, excluded_reconciled=4)
        with pytest.raises(ValueError, match="excluded_reconciled"):
            await store.annotate_feedback(recorded.id, member_count=4, excluded_reconciled=5)
        await store.rollback()


async def test_the_schema_refuses_a_reconciled_count_with_no_bag_size(store: Store) -> None:
    """**Ledger L5.** The CHECK's `member_count IS NOT NULL` clause, asserted against SQLite itself.

    The store layer refuses this first, so the constraint is unreachable through any code path —
    which is exactly why it needs a test that goes around the store. SQLite treats a CHECK
    evaluating to NULL as satisfied, so dropping that clause would let a reconciled count be written
    against a bag whose size was never recorded, and nothing above would have noticed.
    """
    import sqlite3

    engine, _q, _app = await authutil.make_env(store)
    sid, _alarms = await _situation(store, engine, 4)
    async with store.lock:
        recorded = await store.add_feedback(sid, "split", TS)
        assert recorded.id is not None
        await store.commit()

    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
        await store.conn.execute(
            "UPDATE feedback SET member_count=NULL, excluded_reconciled=0 WHERE id=?",
            (recorded.id,),
        )
    await store.rollback()

    # CONTROL: the same write with a bag size recorded is accepted, or the refusal above would
    # prove only that the column rejects everything.
    await store.conn.execute(
        "UPDATE feedback SET member_count=4, excluded_reconciled=0 WHERE id=?", (recorded.id,)
    )
    await store.rollback()


async def test_a_failing_verification_degrades_capture_and_never_the_maintenance_pass(
    store: Store,
) -> None:
    """The fail-safe, on the newest code path.

    A verification that raised would take the maintenance pass with it — and behind that pass sit
    the learned-state flush, the ingest-gap record and the retention sweep. So it degrades exactly
    as every other capture path does: counted, surfaced as an operator warning, and never re-raised.
    """

    class _Broken:
        async def reconciliation_drift(self) -> list[dict[str, Any]]:
            raise RuntimeError("disk is gone")

    engine, _q, _app = await authutil.make_env(store)
    before = engine.capture.errors
    await engine.capture.verify_evidence(_Broken())  # type: ignore[arg-type]
    assert engine.capture.errors == before + 1
    assert engine.capture.last_error == "RuntimeError"
    assert any("capture write(s) failed" in w for w in engine.capture.warnings())


async def test_a_disabled_capture_verifies_nothing(store: Store) -> None:
    """Capture off means capture off. The verification is part of capture, not part of maintenance,
    so an appliance that has turned the dataset off pays nothing for it and reports nothing."""
    engine, _q, _app = await authutil.make_env(store)
    engine.capture.enabled = False
    engine.capture.drift_rows = 0
    await engine.capture.verify_evidence(store)
    assert engine.capture.drift_rows == 0 and engine.capture.warnings() == []


async def test_the_store_layer_refuses_a_reconciled_count_that_is_not_one(store: Store) -> None:
    """Enforcement above the schema, so a violation reaching SQLite is unambiguously a bug above.

    Both directions, and a control: a legal annotation must be accepted, or the refusals prove only
    that the method rejects everything.
    """
    engine, _q, _app = await authutil.make_env(store)
    sid, _alarms = await _situation(store, engine, 4)
    async with store.lock:
        recorded = await store.add_feedback(sid, "split", TS)
        assert recorded.id is not None
        # CONTROL
        await store.annotate_feedback(recorded.id, member_count=4, excluded_reconciled=2)
        with pytest.raises(ValueError, match="excluded_reconciled"):
            await store.annotate_feedback(recorded.id, member_count=4, excluded_reconciled=10)
        with pytest.raises(ValueError, match="excluded_reconciled"):
            await store.annotate_feedback(recorded.id, member_count=4, excluded_reconciled=-1)
        with pytest.raises(ValueError, match="member_count"):
            await store.annotate_feedback(recorded.id, excluded_reconciled=1)
        with pytest.raises(ValueError, match="out_of_scope"):
            await store.annotate_feedback(
                recorded.id,
                member_count=4,
                excluded_reconciled=1,
                excluded_reconciled_out_of_scope=3,
            )
        await store.rollback()


# --- §6 retention, on ONE clock --------------------------------------------------------------


async def test_the_reconciled_count_survives_a_prune_pass_and_the_scope_record_with_it(
    store: Store,
) -> None:
    """**One clock**, and the availability asymmetry that decided DECISIONS #132.

    `prune()` collects the alarms of an aged closed situation, taking `alarm.ne_id` with them. What
    survives is the label, its exclusion rows, its server bag — and, because v0.9.2 computed
    them at
    write time, **both derived quantities**. Had tier 3 been left to a later join it would now be
    permanently unanswerable.
    """
    day = 86400.0
    label_at = TS + 10.0
    prune_at = TS + 20 * day  # cutoff = prune_at - 7d = TS + 13d

    engine, _q, app = await authutil.make_env(store)
    await _install_scope(store)
    sid, alarms = await _situation(store, engine, 6, hidden_from=3)
    assert (await _label(app, sid, [alarms[0], alarms[4]])).status_code == 200
    before = await _row(store, sid)
    assert before["excluded_reconciled"] == 2 and before["excluded_reconciled_out_of_scope"] == 1

    async with store.lock:
        await store.conn.execute(
            "UPDATE alarm SET status='cleared', cleared_at=?, last_seen=?", (label_at, label_at)
        )
        await store.close_situation(sid, label_at)
        await store.commit()
        await store.prune(prune_at, 7 * day)
        await store.commit()

    cur = await store.conn.execute("SELECT COUNT(*) FROM alarm")
    left = await cur.fetchone()
    assert left is not None and int(left[0]) == 0, "the pass collected something"
    cur = await store.conn.execute(
        "SELECT COUNT(*) FROM feedback_exclusion x JOIN alarm a ON a.id=x.alarm_id "
        "WHERE a.ne_id IS NOT NULL"
    )
    resolvable = await cur.fetchone()
    assert resolvable is not None and int(resolvable[0]) == 0, (
        "the input tier 3 would have needed for a later join is gone — which is the point"
    )

    after = await _row(store, sid)
    assert after == before, (
        "both derived quantities survive, because both were stored at write time"
    )

    # And tier 2 is still *recomputable* from surviving evidence, which is what makes the backfill
    # derivation rather than invention.
    async with store.lock:
        assert await store.reconciliation_drift() == []


async def test_the_scope_of_a_label_is_recorded_even_for_a_hidden_only_marking(
    store: Store,
) -> None:
    """Every reconciled mark blind, and the row says exactly that.

    The label is still accepted: `situation_in_scope` passed because at least one member is visible,
    which is the specified behaviour and not something this release changes.
    """
    engine, _q, app = await authutil.make_env(store)
    await _install_scope(store)
    sid, alarms = await _situation(store, engine, 5, hidden_from=1)

    assert (await _label(app, sid, alarms[1:])).status_code == 200
    row = await _row(store, sid)
    assert row["excluded_reconciled"] == 4
    assert row["excluded_reconciled_out_of_scope"] == 4, "all of it was blind"


async def test_the_label_scope_defaults_keep_an_unscoped_construction_honest() -> None:
    """`LabelScope()` with no arguments must not claim anything: unrestricted, nothing hidden, and
    an empty hidden set that is not confused with "no policy resolved"."""
    scope = LabelScope()
    assert scope.policy_id is None and scope.restricted is False
    assert scope.redacted_members == 0 and scope.hidden_members == frozenset()


# --- §7 the consumers ----------------------------------------------------------------------------


async def test_the_corpus_total_is_derived_from_the_reconciled_count(store: Store) -> None:
    """**F46's corpus-wide consumer** (`bias.py`), and the number this release exists to move.

    The same three labels Gate 0 §1 measured taking the total to -259,084: eight honest ones, a
    truncated 600-id marking on a four-member bag, and thirty ghosts on a sixty-member bag. The
    honest labels assert 8 x 2 x 7 = 112 pairs. The other two assert **nothing**, and the total must
    say so.
    """
    engine, _q, app = await authutil.make_env(store)
    for _ in range(8):
        sid, alarms = await _situation(store, engine, 9)
        assert (await _label(app, sid, alarms[:2])).status_code == 200
    async with store.lock:
        honest = await bias.collect(store)
    assert honest["asserted_negative_pairs"] == 112, "the control: eight honest labels"

    sid, _alarms = await _situation(store, engine, 4)
    assert (await _label(app, sid, [GHOST - i for i in range(600)])).status_code == 200
    sid, _alarms = await _situation(store, engine, 60)
    assert (await _label(app, sid, [GHOST - 700 - i for i in range(30)])).status_code == 200

    async with store.lock:
        after = await bias.collect(store)
    assert after["asserted_negative_pairs"] == 112, (
        "v0.9.1 read -259,084 here: -260,096 from the truncated marking and +900 from thirty "
        "ghosts, neither of which asserted a single pair"
    )
    # And the disagreement is now visible rather than silent.
    assert after["reported_vs_reconciled_rows"] == 2
    assert after["reported_vs_reconciled_marks"] == 512 + 30
    assert after["client_reported_marks"] == 16 + 512 + 30
    assert after["reconciled_marks"] == 16


async def test_the_per_channel_total_is_derived_independently(store: Store) -> None:
    """**F46's second consumer**, and it is a SEPARATE code path from the corpus-wide one.

    `bias.py`'s per-channel figure had its own copy of the defect, in its own SQL. A single test
    driving only the corpus-wide total would leave this path unguarded, so it is asserted here on
    its own — with both channels present, since the two are never averaged.
    """
    engine, _q, app = await authutil.make_env(store)
    sid, _alarms = await _situation(store, engine, 5)
    assert (await _label(app, sid, [GHOST, GHOST - 1, GHOST - 2])).status_code == 200

    sid2, alarms2 = await _situation(store, engine, 5)
    client = await authutil.client_as(app, "editor")
    try:
        closed = await client.post(
            f"/api/situations/{sid2}/close",
            json={"verdict": "split", "excluded_ids": [alarms2[0], GHOST - 9]},
        )
    finally:
        await client.aclose()
    assert closed.status_code == 200

    async with store.lock:
        channels = (await bias.collect(store))["by_channel"]
    assert channels["organic"]["asserted_negatives"] == 0, "three ghosts assert nothing"
    assert channels["organic"]["client_reported_marks"] == 3
    assert channels["close"]["asserted_negatives"] == 1 * (5 - 1), "one real mark of five"
    assert channels["close"]["client_reported_marks"] == 2


async def test_the_disagreement_is_the_first_number_the_report_prints(store: Store) -> None:
    """The report must put it first, and must name both tiers in words a reader can follow without
    holding `EVIDENCE-BOUNDARY-0.9.2.md`."""
    from netcorenoc import bias_report

    engine, _q, app = await authutil.make_env(store)
    sid, _alarms = await _situation(store, engine, 4)
    assert (await _label(app, sid, [GHOST, GHOST - 1])).status_code == 200
    async with store.lock:
        text = bias_report.render(await bias.collect(store))

    body = text.split("aggregate.\n", 1)[1]
    first = next(line for line in body.splitlines() if line.strip() and not line.startswith("--"))
    assert first.startswith("label rows: reported != reconciled"), first
    assert first.strip().endswith("1"), "two ghosts on one row: one row disagrees"
    assert "marks the CLIENT reported (untrusted)" in text
    assert "marks the SERVER reconciled" in text
    assert "ASSERTED NEGATIVE PAIRS (reconciled)" in text


async def test_dataset_stats_splits_the_bag_by_source(store: Store) -> None:
    """The operator's row-cost view says how much of `feedback_member` is authoritative.

    The cost is real either way; the composition is what an operator sizing retention needs, and a
    client can inflate one half of it by up to `MAX_CLIENT_MEMBERS` per label. The total is still
    printed, so nothing an operator read before has gone away.
    """
    engine, _q, app = await authutil.make_env(store)
    sid, alarms = await _situation(store, engine, 5)
    client = await authutil.client_as(app, "editor")
    try:
        resp = await client.post(
            f"/api/situations/{sid}/feedback",
            json={
                "verdict": "split",
                "member_ids": [alarms[0], GHOST],
                "excluded_ids": [alarms[1]],
            },
        )
    finally:
        await client.aclose()
    assert resp.status_code == 200

    stats = await store.dataset_stats()
    assert stats["feedback_member.server"] == 5, "the bag the engine wrote"
    assert stats["feedback_member.client"] == 2, "what the browser said it rendered"
    assert stats["feedback_member"] == 7, "and the total an operator already read"


async def test_a_report_over_a_backfilled_corpus_says_the_count_was_derived(store: Store) -> None:
    """`reconciled_backfilled` distinguishes a count `0011` derived from one the server wrote live.

    Two acts, two times, two actors. A report that could not tell them apart would let a reader
    treat a migration's arithmetic as an observation made at the verdict.
    """
    engine, _q, app = await authutil.make_env(store)
    sid, alarms = await _situation(store, engine, 5)
    assert (await _label(app, sid, alarms[:2])).status_code == 200

    async with store.lock:
        assert (await bias.collect(store))["reconciled_backfilled"] == 0
        await store.conn.execute(
            "UPDATE feedback SET excluded_reconciled_source='backfill' WHERE situation_id=?", (sid,)
        )
        await store.commit()
        assert (await bias.collect(store))["reconciled_backfilled"] == 1
