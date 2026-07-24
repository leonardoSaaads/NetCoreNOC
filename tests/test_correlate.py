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
    assert correlator._recent_live() == []  # tombstone is not offered as a candidate


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
