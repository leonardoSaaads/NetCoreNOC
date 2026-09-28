"""Situation assignment (`engine/correlate/grouping.py`, ADR #405).

The property this release exists for is the second test: **one weak bridge no longer merges two
incidents**. Connected components merged on any single accepted link; correlation clustering merges
only when the mean of *all* cross evidence says so.
"""

from __future__ import annotations

from netcorenoc.engine.correlate.grouping import (
    MODE_CLUSTER,
    MODE_COMPONENTS,
    Grouper,
    GroupingParams,
    Placement,
    Scored,
)


def _cluster(**kw: float) -> Grouper:
    params = GroupingParams(
        join_bias=kw.get("join_bias", 0.0),
        merge_bias=kw.get("merge_bias", 1.0),
        merge_min_pairs=int(kw.get("merge_min_pairs", 3)),
    )
    return Grouper(MODE_CLUSTER, params)


def test_components_mode_is_connected_components() -> None:
    """Any accepted link joins; two situations bridged by links merge into the smaller id."""
    g = Grouper(MODE_COMPONENTS)
    sit_of = {1: 10, 2: 20, 3: 30}
    placement = g.place(
        [Scored(1, 0.2, True), Scored(2, 0.01, True), Scored(3, -0.4, False)], sit_of, None
    )
    assert placement == Placement(10, (20,))
    assert g.place([Scored(3, -0.1, False)], sit_of, None) == Placement(None, ())


def test_one_bridge_does_not_merge_two_incidents() -> None:
    """The `dual_incident` shape: incident A (situation 10) and B (situation 20) are concurrent.

    The newcomer is A's. It has one confident pair into B — the bridge — and several confident
    "no"s. Components would merge on the bridge; clustering joins A and leaves B alone, and the
    evidence it accumulated between them stays negative.
    """
    g = _cluster()
    sit_of = {1: 10, 2: 10, 3: 20, 4: 20, 5: 20}
    scored = [
        Scored(1, 3.0, True),
        Scored(2, 2.5, True),
        Scored(3, 2.0, True),  # the bridge
        Scored(4, -3.0, False),
        Scored(5, -2.5, False),
    ]
    placement = g.place(scored, sit_of, None)
    assert placement.join == 10
    assert placement.merge == ()
    sit_of[99] = 10
    g.commit(10, placement, scored, sit_of)
    held = g.cross[10][20]
    assert held[1] == 3 and held[0] < 0, "cross evidence counts the bridge AND the refusals"
    assert g.cross[20][10] is held, "the evidence is one object seen from both sides"


def test_consistent_cross_evidence_merges() -> None:
    """Both ends of one fibre cut opened two situations; the next alarm is confident about both."""
    g = _cluster(merge_min_pairs=3)
    sit_of = {1: 10, 2: 20, 3: 20, 4: 20}
    scored = [Scored(1, 3.0, True), Scored(2, 2.0, True), Scored(3, 2.2, True), Scored(4, 1.9, True)]
    placement = g.place(scored, sit_of, None)
    assert placement.join == 10
    assert placement.merge == (20,)


def test_join_is_by_mean_not_by_sum() -> None:
    """A hundred faint maybes into a storm must not outvote two confident pairs elsewhere."""
    g = _cluster()
    sit_of = {i: 10 for i in range(100)} | {500: 20, 501: 20}
    scored = [Scored(i, 0.05, True) for i in range(100)] + [Scored(500, 2.0, True), Scored(501, 1.8, True)]
    assert g.place(scored, sit_of, None).join == 20


def test_below_the_join_bias_opens_a_new_situation() -> None:
    g = _cluster(join_bias=0.5)
    assert g.place([Scored(1, 0.3, True)], {1: 10}, None).join is None


def test_a_reactivated_alarm_stays_in_its_own_open_situation() -> None:
    g = _cluster()
    placement = g.place([Scored(1, 4.0, True)], {1: 20, 7: 10}, own=10)
    assert placement.join == 10


def test_evidence_accumulates_until_a_merge_is_earned() -> None:
    """Two activations, each with one positive cross pair: neither alone reaches the pair floor."""
    g = _cluster(merge_min_pairs=2, merge_bias=0.5)
    sit_of = {1: 10, 2: 20}
    first = [Scored(1, 3.0, True), Scored(2, 2.0, True)]
    p1 = g.place(first, sit_of, None)
    assert p1.join == 10 and p1.merge == ()
    sit_of[3] = 10
    g.commit(10, p1, first, sit_of)
    second = [Scored(1, 3.0, True), Scored(2, 2.0, True)]
    p2 = g.place(second, sit_of, None)
    assert p2.merge == (20,)


def test_fold_merges_evidence_and_forget_removes_it() -> None:
    g = _cluster()
    sit_of = {1: 10, 2: 20, 3: 30}
    g.commit(10, Placement(10, ()), [Scored(2, 1.0, True), Scored(3, -1.0, False)], sit_of)
    g.commit(20, Placement(20, ()), [Scored(3, 2.0, True)], sit_of)
    g.merged(10, 20)
    assert 20 not in g.cross and 20 not in g.cross[30]
    assert g.cross[10][30] == [1.0, 2.0], "10-30 and 20-30 evidence became one"
    g.forget(10)
    assert 10 not in g.cross and 10 not in g.cross.get(30, {})
    g.prune(set())
    assert g.cross == {}
