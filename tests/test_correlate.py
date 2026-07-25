from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from netcorenoc.correlate import (
    LINK_THRESHOLD,
    W_A,
    W_E,
    W_T,
    Correlator,
    WindowAlarm,
)
from netcorenoc.learn import MIN_EDGE_N, Learner


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
    from netcorenoc import preview
    from netcorenoc.scoring import default_scorer

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

    from netcorenoc import correlate, preview

    assert preview.PREVIEW_WINDOW_S is correlate.WINDOW_S
    assert preview.PREVIEW_MAX_CANDIDATES is correlate.MAX_CANDIDATES

    for func in (correlate.Correlator._recent_live, preview.partition):
        assert "select_candidates" in inspect.getsource(func), (
            f"{func.__qualname__} must select candidates through correlate.select_candidates"
        )


def test_select_candidates_skips_tombstones_and_honours_the_window_and_cap() -> None:
    """The helper's own contract, including the one difference between its two callers."""
    from netcorenoc.correlate import select_candidates

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
