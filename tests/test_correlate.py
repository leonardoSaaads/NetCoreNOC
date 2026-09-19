from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from netcorenoc.engine.correlate.correlate import (
    LINK_THRESHOLD,
    MAX_LINKS_PER_ALARM,
    W_A,
    W_E,
    W_T,
    CorrelationResult,
    Correlator,
    WindowAlarm,
    contribution_of,
)
from netcorenoc.engine.correlate.learn import MIN_EDGE_N, Learner
from netcorenoc.engine.correlate.scoring import AdditiveScorer, LinkFeatures


def wa(alarm_id: int, class_id: int, device_id: int, ts: float) -> WindowAlarm:
    return WindowAlarm(alarm_id, class_id, device_id, ts)


def trained_learner(device_a: int = 10, device_b: int = 11) -> Learner:
    """A learner whose E edge between two devices is trusted and strong."""
    learner = Learner()
    for _ in range(int(MIN_EDGE_N) + 2):
        learner.observe_activation((1, device_a))
        learner.observe_activation((2, device_b))
        learner.observe_pairs((1, device_a), [(2, device_b)], storm=False)
    return learner


def test_same_device_links_when_close_in_time() -> None:
    correlator, learner = Correlator(), Learner()
    correlator.process(wa(1, 1, 10, ts=100.0), learner)
    result = correlator.process(wa(2, 2, 10, ts=105.0), learner)
    assert len(result.links) == 1
    link = result.links[0]
    assert link.other.alarm_id == 1
    assert link.term_e == W_E  # same device ⇒ E = 1
    assert link.term_a == 0.0  # nothing learned yet
    assert abs(link.score - (link.term_t + link.term_a + link.term_e)) < 1e-12


def test_same_device_does_not_link_when_far_in_time() -> None:
    correlator, learner = Correlator(), Learner()
    correlator.process(wa(1, 1, 10, ts=100.0), learner)
    result = correlator.process(wa(2, 2, 10, ts=130.0), learner)
    assert result.links == []  # 0.3·e^(-1) + 0.35 ≈ 0.46 < 0.5


def test_cross_device_cold_start_never_links() -> None:
    correlator, learner = Correlator(), Learner()
    correlator.process(wa(1, 1, 10, ts=100.0), learner)
    result = correlator.process(wa(2, 2, 11, ts=100.1), learner)
    assert result.links == []  # honest ignorance: max possible score is w_t = 0.3


def test_cross_device_links_after_learned_topology() -> None:
    correlator = Correlator()
    learner = trained_learner()
    correlator.process(wa(1, 1, 10, ts=100.0), learner)
    result = correlator.process(wa(2, 2, 11, ts=105.0), learner)
    assert len(result.links) == 1
    assert result.links[0].term_e > 0.3  # learned E edge carries the link


def test_window_eviction_after_120s() -> None:
    correlator, learner = Correlator(), Learner()
    correlator.process(wa(1, 1, 10, ts=100.0), learner)
    result = correlator.process(wa(2, 2, 10, ts=221.0), learner)
    assert result.considered == []
    assert len(correlator.window) == 1


def test_remove_drops_cleared_alarm() -> None:
    # v0.3.0 (S1): removal is O(1) via the index; the deque entry becomes a tombstone that
    # is skipped as a candidate and cleared on eviction. The *live* set is the index.
    correlator, learner = Correlator(), Learner()
    correlator.process(wa(1, 1, 10, ts=100.0), learner)
    correlator.remove(1)
    correlator.remove(1)  # idempotent
    assert len(correlator.index) == 0  # no live alarms
    assert correlator._recent_live(100.0) == []  # tombstone is not offered as a candidate


def test_reactivation_replaces_window_entry() -> None:
    correlator, learner = Correlator(), Learner()
    correlator.process(wa(1, 1, 10, ts=100.0), learner)
    result = correlator.process(wa(1, 1, 10, ts=110.0), learner)
    assert result.links == []  # an alarm never links to itself
    assert len(correlator.index) == 1  # exactly one live entry for the reactivated alarm
    assert correlator.index[1].ts == 110.0  # and it is the most recent activation


def test_candidates_are_bounded() -> None:
    correlator, learner = Correlator(max_candidates=10), Learner()
    for i in range(30):
        correlator.process(wa(i, 1, 10, ts=100.0 + i * 0.01), learner)
    result = correlator.process(wa(99, 1, 10, ts=101.0), learner)
    assert len(result.considered) == 10


def test_storm_flag_at_window_occupancy() -> None:
    correlator, learner = Correlator(), Learner()
    for i in range(50):
        correlator.process(wa(i, 1, 10, ts=100.0 + i * 0.01), learner)
    result = correlator.process(wa(99, 1, 10, ts=101.0), learner)
    assert result.storm is True


def test_window_cap_evicts_oldest_and_counts_overflow() -> None:
    # §5.6: the absolute MAX_WINDOW_ALARMS cap forces out the oldest live alarms and counts
    # each as a window-overflow drop, so a burst inside the window stays bounded.
    correlator, learner = Correlator(max_window=5), Learner()
    for i in range(20):
        correlator.process(wa(i, 1, 10, ts=100.0 + i * 0.001), learner)  # all inside the window
    # Eviction runs at the top of process(), so the window holds at most max_window + 1.
    assert len(correlator.index) <= correlator.max_window + 1  # never grows unbounded
    assert len(correlator.window) <= correlator.max_window + 1  # deque bounded too
    assert correlator.take_overflow() >= 10  # the shed live alarms were counted as a gap
    assert correlator.take_overflow() == 0  # the counter resets when read


def test_tombstones_do_not_count_toward_overflow() -> None:
    # A removed (cleared) alarm evicted later is a tombstone, not a live drop.
    correlator, learner = Correlator(max_window=3), Learner()
    for i in range(3):
        correlator.process(wa(i, 1, 10, ts=100.0 + i * 0.001), learner)
    correlator.remove(0)  # clear the oldest; it becomes a tombstone
    correlator.process(wa(3, 1, 10, ts=100.01), learner)  # forces eviction of the tombstone
    assert correlator.take_overflow() == 0  # a tombstone shed is not a lost live alarm


@given(
    dt=st.floats(min_value=0.0, max_value=120.0),
    class_a=st.integers(min_value=1, max_value=5),
    class_b=st.integers(min_value=1, max_value=5),
    device_a=st.integers(min_value=10, max_value=12),
    device_b=st.integers(min_value=10, max_value=12),
)
def test_score_is_bounded_and_decomposes(
    dt: float, class_a: int, class_b: int, device_a: int, device_b: int
) -> None:
    correlator = Correlator()
    learner = trained_learner()
    new = wa(1, class_a, device_a, ts=1000.0 + dt)
    old = wa(2, class_b, device_b, ts=1000.0)
    score, term_t, term_a, term_e = correlator.score(new, old, learner)
    assert 0.0 <= score <= W_T + W_A + W_E + 1e-9
    assert 0.0 <= term_t <= W_T and 0.0 <= term_a <= W_A and 0.0 <= term_e <= W_E
    assert abs(score - (term_t + term_a + term_e)) < 1e-12
    assert 0.0 < LINK_THRESHOLD < 1.0


@given(dt_near=st.floats(0.0, 60.0), gap=st.floats(0.1, 60.0))
def test_score_decays_monotonically_with_time(dt_near: float, gap: float) -> None:
    correlator, learner = Correlator(), Learner()
    old = wa(2, 2, 10, ts=1000.0)
    near = wa(1, 1, 10, ts=1000.0 + dt_near)
    far = wa(1, 1, 10, ts=1000.0 + dt_near + gap)
    assert correlator.score(near, old, learner)[0] >= correlator.score(far, old, learner)[0]


# --- v0.7.0 S0: the preview/engine candidate-selection close-out -------------------------
#
# v0.6.0 shipped the windowing/candidate-selection rule twice — once in `Correlator._recent_live`
# and once inside `preview.partition` — with its own copies of the window length and the cap. The
# two agreed, but nothing held them together, so a change to `WINDOW_S` alone would have left the
# what-if replaying a different window from the engine it claims to predict. These tests pin the
# unification (DECISIONS #61) at both levels: the *rule* is one function, and the *result* is the
# engine's actual situation partition.


def _engine_partition(alarms: list[WindowAlarm], learner: Learner) -> set[frozenset[int]]:
    """Drive a real `Correlator` over `alarms` and return its connected components.

    This is the engine's own grouping logic — `process()` per alarm, union-find over the accepted
    links — reproduced here rather than mocked, so what preview is compared against is what the
    engine in fact does with these alarms.
    """
    correlator = Correlator()
    parent = {a.alarm_id: a.alarm_id for a in alarms}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for alarm in alarms:
        result = correlator.process(alarm, learner)
        for link in result.links:
            ra, rb = find(alarm.alarm_id), find(link.other.alarm_id)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)

    groups: dict[int, set[int]] = {}
    for alarm_id in parent:
        groups.setdefault(find(alarm_id), set()).add(alarm_id)
    return {frozenset(members) for members in groups.values()}


def _preview_partition(alarms: list[WindowAlarm], learner: Learner) -> set[frozenset[int]]:
    from netcorenoc.engine.correlate import preview
    from netcorenoc.engine.correlate.scoring import default_scorer

    snapshot = [
        preview.PreviewAlarm(a.alarm_id, a.class_id, a.device_id, a.entity_id, a.ts) for a in alarms
    ]
    labels, _links = preview.partition(snapshot, default_scorer(), learner)
    groups: dict[int, set[int]] = {}
    for alarm_id, component in labels.items():
        groups.setdefault(component, set()).add(alarm_id)
    return {frozenset(members) for members in groups.values()}


def test_preview_reproduces_the_engine_partition() -> None:
    """THE close-out gate: preview's partition **is** the engine's partition on the same alarms.

    Not "the same windowing" — the same *situations*, member for member. The alarms straddle both
    edges that matter: pairs inside the ~21 s cold-start link radius, pairs beyond it, and a gap
    wider than the 120 s window so the partition has more than one component to get wrong.
    """
    learner = trained_learner()
    alarms = [
        wa(1, 1, 10, ts=1000.0),
        wa(2, 2, 10, ts=1002.0),  # same NE, 2 s  -> links
        wa(3, 1, 11, ts=1004.0),  # trained cross-NE pair -> links
        wa(4, 3, 12, ts=1050.0),  # unrelated NE, 46 s later -> its own situation
        wa(5, 4, 13, ts=1400.0),  # beyond the window entirely -> its own situation
        wa(6, 4, 13, ts=1401.0),  # same NE, 1 s -> links to 5
    ]
    engine = _engine_partition(alarms, learner)
    assert engine == _preview_partition(alarms, learner)
    assert len(engine) > 1, "a single component would make this assertion vacuous"


@given(
    gaps=st.lists(st.sampled_from([0.0, 1.0, 5.0, 25.0, 130.0]), min_size=2, max_size=40),
    devices=st.lists(st.sampled_from([10, 11, 12]), min_size=2, max_size=40),
)
def test_preview_reproduces_the_engine_partition_over_generated_streams(
    gaps: list[float], devices: list[int]
) -> None:
    """The same equality over generated streams: inter-arrival gaps drawn to straddle the link
    radius and the window edge, on a mix of NEs including a trained cross-NE pair."""
    learner = trained_learner()
    n = min(len(gaps), len(devices))
    ts = 1000.0
    alarms: list[WindowAlarm] = []
    for i in range(n):
        ts += gaps[i]
        alarms.append(wa(i + 1, (i % 3) + 1, devices[i], ts=ts))
    assert _engine_partition(alarms, learner) == _preview_partition(alarms, learner)


def test_preview_and_engine_share_one_selection_implementation() -> None:
    """Structural, not behavioural: there must be exactly one candidate-selection rule.

    A parity test alone is what v0.6.0 could have had — it proves agreement *today*. This asserts
    the two callers cannot drift: both go through `correlate.select_candidates`, and preview's
    bounds are the engine's constants rather than copies of their values.
    """
    import inspect

    from netcorenoc.engine.correlate import correlate, preview

    assert preview.PREVIEW_WINDOW_S is correlate.WINDOW_S
    assert preview.PREVIEW_MAX_CANDIDATES is correlate.MAX_CANDIDATES

    for func in (correlate.Correlator._recent_live, preview.partition):
        assert "select_candidates" in inspect.getsource(func), (
            f"{func.__qualname__} must select candidates through correlate.select_candidates"
        )


def test_select_candidates_skips_tombstones_and_honours_the_window_and_cap() -> None:
    """The helper's own contract, including the one difference between its two callers."""
    from netcorenoc.engine.correlate.correlate import select_candidates

    window = [wa(i, 1, 10, ts=1000.0 + i) for i in range(1, 11)]

    # No liveness set (preview's case): every entry qualifies, newest-first, capped, chronological.
    assert [a.alarm_id for a in select_candidates(window, now=1010.0, max_candidates=3)] == [
        8,
        9,
        10,
    ]
    # A liveness set (the engine's case): tombstones are skipped, and the cap counts live entries.
    live = {1, 2, 3, 10}
    assert [
        a.alarm_id for a in select_candidates(window, now=1010.0, max_candidates=3, live=live)
    ] == [2, 3, 10]
    # The window boundary keeps `now - ts <= window_s` and drops past it — the v0.6.0 predicate
    # exactly, byte for byte: inclusive at the edge, exclusive one tick beyond.
    assert [a.alarm_id for a in select_candidates(window, now=1010.0, window_s=3.0)] == [
        7,
        8,
        9,
        10,
    ]  # ts 1007 is exactly 3.0 s old -> kept
    assert [a.alarm_id for a in select_candidates(window, now=1010.0, window_s=2.9)] == [8, 9, 10]
    assert select_candidates(window, now=2000.0, window_s=10.0) == []


# --- v0.8.0 S1: `process()` returns what it already computed ------------------------------
#
# The parity bar for this change is the highest in the release, because `correlate.py` is the one
# file whose behaviour `make eval` measures directly. The change is additive on the RETURN VALUE
# and nothing else; these tests are what make that a fact rather than a claim.


# **What was here, and why it went** (v0.18.0).
#
# `test_score_link_body_is_unchanged_by_the_capture_change` hashed the SOURCE TEXT of
# `Correlator.score_link` and `Correlator.features` against the v0.7.5 tree, to police one
# sentence of v0.8.0's build prompt: *"v0.8.0 permits exactly one change to correlate.py."*
#
# **It was protecting a release-scope rule, and that release is ten releases past.** The v0.18.0
# brief withdraws byte-pinning of the trap path in terms — *"its bytes are no longer frozen"* —
# and `features` legitimately changes here to compute the `same_oid_root` that
# `PREREGISTRATION-0.9.0.md` §2.3 registered in v0.9.0. A pin whose only remedy is "recompute the
# hash" catches nothing on a release that is supposed to change the file.
#
# The claim worth keeping is not "these bytes did not move" but "the arithmetic is still the
# arithmetic", and that is a behaviour, so it is asserted as one below.


def test_the_three_term_formula_is_unchanged_for_every_pair_the_gate_does_not_touch() -> None:
    """**The correlation-parity anchor, as behaviour** (v0.18.0).

    v0.18.0 changes one thing about the score: a pair on two different network elements whose
    trap OIDs sit in different enterprise subtrees no longer receives the learned entity-affinity
    term (F76). *Every other pair must compute exactly what v0.5.0 computed* — same three
    products, same left-to-right sum, same strict `>` comparison — because float addition is not
    associative and a last-bit difference either side of the threshold is a different grouping.

    Checked against the formula written out independently here, rather than against a hash of the
    implementation's source: a hash says the text is the same, this says the *number* is.
    """
    import math

    scorer = AdditiveScorer()
    cases = [
        # (delta_t, class_affinity, entity_affinity, ne_i, ne_j, same_oid_root)
        (0.0, 0.0, 1.0, 5, 5, None),  # same element, root unknown — the v0.5.0 default path
        (3.0, 0.4, 0.9, 5, 5, False),  # same element: the gate must not fire
        (3.0, 0.4, 0.9, 5, 6, True),  # same subtree: the gate must not fire
        (12.5, 0.83, 0.77, 5, 6, None),  # unknown subtree: the gate must not fire
        (60.0, 0.1, 0.2, 1, 2, True),
        (0.5, 1.0, 1.0, 9, 9, True),
    ]
    for delta_t, class_aff, entity_aff, ne_i, ne_j, same_root in cases:
        features = LinkFeatures(
            delta_t_s=delta_t,
            class_i=1,
            class_j=2,
            class_affinity=class_aff,
            ne_i=ne_i,
            ne_j=ne_j,
            entity_affinity=entity_aff,
            same_oid_root=same_root,
        )
        expected = 0.3 * math.exp(-abs(delta_t) / 30.0) + 0.35 * class_aff + 0.35 * entity_aff
        result = scorer.score(features)
        assert result.score == expected, (
            f"the formula moved for {features}: {result.score!r} != {expected!r}"
        )
        assert result.linked == (expected > 0.5)
        # The three printed terms must sum to the printed score, or the explanation is a lie.
        assert math.isclose(sum(t.contribution for t in result.terms), result.score, rel_tol=1e-12)


def test_evaluated_carries_every_candidate_and_links_is_unchanged() -> None:
    """`evaluated` is a superset of `links`, in candidate order, and `links` is untouched.

    The two lists answer different questions and the release turns on not confusing them:
    `links` is what the engine persists (sorted by score, truncated at MAX_LINKS_PER_ALARM);
    `evaluated` is what the scorer looked at (candidate order, untruncated, rejections included).
    """
    learner = Learner()
    c = Correlator()
    for i in range(1, 9):
        c.process(wa(i, class_id=i, device_id=10, ts=1000.0 + i), learner)
    result = c.process(wa(99, class_id=99, device_id=10, ts=1009.0), learner)

    assert [e.other.alarm_id for e in result.evaluated] == [
        a.alarm_id for a in result.considered
    ], "evaluated must follow candidate order, one entry per candidate"
    assert len(result.evaluated) == len(result.considered)
    assert len(result.links) <= MAX_LINKS_PER_ALARM

    accepted = [e for e in result.evaluated if e.result.linked]
    assert len(accepted) >= len(result.links), "links cannot exceed what the scorer accepted"
    # Every persisted link appears in `evaluated` carrying the identical score object.
    by_id = {e.other.alarm_id: e.result for e in result.evaluated}
    for link in result.links:
        assert by_id[link.other.alarm_id] is link.result


def test_evaluated_records_rejected_pairs_that_links_discards() -> None:
    """The population v0.7.5 threw away: scored, rejected, and now recorded.

    A rejection needs a pair the scorer will refuse, so the two alarms are far apart in time on
    unrelated classes and devices — the temporal term decays and both affinities are zero on a cold
    learner, so the score cannot clear the 0.5 threshold.
    """
    learner = Learner()
    c = Correlator()
    c.process(wa(1, class_id=1, device_id=10, ts=1000.0), learner)
    result = c.process(wa(2, class_id=2, device_id=20, ts=1100.0), learner)

    assert result.considered, "the first alarm must still be a candidate"
    assert result.links == [], "a cold learner 100 s apart on another device must not link"
    assert len(result.evaluated) == 1
    rejected = result.evaluated[0]
    assert rejected.result.linked is False
    assert rejected.result.score < rejected.result.threshold
    # The score exists and is a real number — this is exactly what v0.7.5 computed and discarded.
    assert 0.0 <= rejected.result.score < 1.0


def test_truncated_links_are_derivable_from_what_process_returns() -> None:
    """`dataset_pair.truncated` needs no new field: it is `linked and not in links`.

    Phase 0 measured that truncation, not rejection, is 94% of what the engine discards — so telling
    the two apart is load-bearing, and doing it without another field keeps the change additive.
    """
    learner = Learner()
    c = Correlator()
    # Same device and class throughout: entity affinity is 1.0 (same NE), so every pair links and
    # the accepted count comfortably exceeds MAX_LINKS_PER_ALARM.
    for i in range(1, 10):
        c.process(wa(i, class_id=5, device_id=10, ts=1000.0 + i * 0.1), learner)
    result = c.process(wa(50, class_id=5, device_id=10, ts=1001.0), learner)

    accepted = [e for e in result.evaluated if e.result.linked]
    kept = {link.other.alarm_id for link in result.links}
    truncated = [e for e in accepted if e.other.alarm_id not in kept]

    assert len(accepted) > MAX_LINKS_PER_ALARM, "fixture must actually exceed the cap"
    assert len(result.links) == MAX_LINKS_PER_ALARM
    assert len(truncated) == len(accepted) - MAX_LINKS_PER_ALARM
    # A truncated pair is an ACCEPTED link the cap dropped — not a rejection.
    for pair in truncated:
        assert pair.result.linked is True


def test_correlation_result_still_constructs_without_evaluated() -> None:
    """The field is defaulted, so every pre-v0.8.0 construction site keeps working.

    `preview.py` and the tests build `CorrelationResult` by hand; a required field would have made
    this an API break dressed as an addition.
    """
    empty = CorrelationResult(links=[], considered=[], storm=False)
    assert empty.evaluated == []


# --- F76: two incidents on disjoint elements, kept apart by the subtree gate -------------------


def _feat(**kw: object) -> LinkFeatures:
    base: dict[str, object] = {
        "delta_t_s": 1.0,
        "class_i": 1,
        "class_j": 2,
        "class_affinity": 0.0,
        "ne_i": 1,
        "ne_j": 2,
        "entity_affinity": 0.9,
        "same_oid_root": None,
    }
    base.update(kw)
    return LinkFeatures(**base)  # type: ignore[arg-type]


def test_learned_cross_element_affinity_is_refused_across_enterprise_subtrees() -> None:
    """**F76's repair, at the one expression that implements it.**

    Two alarms on different network elements whose trap OIDs live in different enterprise
    subtrees are co-occurring, not related. The entity term is what F58/F61 measured to be cheap
    — six ordinary alarms clear `MIN_EDGE_N` and the affinity is 0.833 — so it is the term the
    gate removes, and only for that pair shape.
    """
    scorer = AdditiveScorer()
    blocked = scorer.score(_feat(same_oid_root=False))
    assert contribution_of(blocked, "entity_affinity") == 0.0
    assert not blocked.linked, blocked.score


def test_the_gate_does_not_touch_same_element_or_same_subtree_pairs() -> None:
    """Three controls, because a gate that fired too widely would look identical on `dual_incident`
    and would quietly stop the appliance correlating anything across two devices."""
    scorer = AdditiveScorer()
    # Same element: `E` is structural there, not learned, and must survive.
    same_element = scorer.score(_feat(same_oid_root=False, ne_i=7, ne_j=7))
    assert contribution_of(same_element, "entity_affinity") == pytest_approx(0.35 * 0.9)
    # Same subtree across elements: the ordinary cross-device correlation the product exists for.
    same_subtree = scorer.score(_feat(same_oid_root=True))
    assert contribution_of(same_subtree, "entity_affinity") == pytest_approx(0.35 * 0.9)
    # Unknown subtree: an alarm built without a root must behave exactly as it did before v0.18.0.
    unknown = scorer.score(_feat(same_oid_root=None))
    assert contribution_of(unknown, "entity_affinity") == pytest_approx(0.35 * 0.9)


def test_class_affinity_is_never_gated_so_a_recurring_pair_keeps_a_route_to_linking() -> None:
    """The gate narrows one term, not the appliance's ability to learn.

    A genuinely recurring cross-vendor pair accumulates **class** affinity, which is not gated.
    Without this the gate would be a permanent taxonomy rule rather than a prior, and
    "structure emerges from the stream" would stop being true across vendors.
    """
    scorer = AdditiveScorer()
    strong = scorer.score(_feat(same_oid_root=False, class_affinity=1.0, delta_t_s=0.0))
    assert contribution_of(strong, "class_affinity") == pytest_approx(0.35)
    assert strong.score == pytest_approx(0.3 + 0.35)
    assert strong.linked, "a fully-learned class pair must still link across subtrees"


def test_the_window_alarm_carries_the_root_and_features_compares_it() -> None:
    """The plumbing, end to end through `Correlator.features` rather than by construction."""
    learner = Learner()
    ciena = WindowAlarm(1, 10, 100, 1000.0, oid_root="1.3.6.1.4.1.1271")
    juniper = WindowAlarm(2, 11, 200, 1001.0, oid_root="1.3.6.1.4.1.2636")
    ciena2 = WindowAlarm(3, 12, 300, 1002.0, oid_root="1.3.6.1.4.1.1271")
    rootless = WindowAlarm(4, 13, 400, 1003.0)
    assert Correlator.features(ciena, juniper, learner).same_oid_root is False
    assert Correlator.features(ciena, ciena2, learner).same_oid_root is True
    assert Correlator.features(ciena, rootless, learner).same_oid_root is None


def pytest_approx(value: float) -> object:
    import pytest

    return pytest.approx(value)
