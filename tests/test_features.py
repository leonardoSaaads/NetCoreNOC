"""The v2 pair feature vector (`engine/correlate/features.py`) and stage-one recall."""

from __future__ import annotations

from collections import deque

import pytest

from netcorenoc.engine.correlate import retrieval
from netcorenoc.engine.correlate.correlate import WindowAlarm
from netcorenoc.engine.correlate.episodes import EPISODE_GAP_S, EpisodeMemory
from netcorenoc.engine.correlate.features import (
    FEATURE_NAMES,
    MAX_DT_S,
    UNKNOWN_SEVERITY,
    common_arcs,
    vector,
)
from netcorenoc.engine.correlate.learn import Learner
from netcorenoc.engine.correlate.retrieval import CandidateIndex

T = 1_800_000_000.0


def _alarm(aid: int, cls: int, dev: int, ts: float, oid: str = "1.3.6.1.4.1.2011.5.25.219.2.2.3",
           sev: int = -1, chatter: int = 0) -> WindowAlarm:
    return WindowAlarm(aid, cls, dev, ts, 0, ".".join(oid.split(".")[:7]), tuple(oid.split(".")),
                       sev, chatter)


def test_common_arcs_respects_arc_boundaries() -> None:
    """Appendix B: `…2011.1.2` is not a prefix of `…2011.1.12`."""
    a = tuple("1.3.6.1.4.1.2011.1.2".split("."))
    b = tuple("1.3.6.1.4.1.2011.1.12".split("."))
    assert common_arcs(a, b) == 8
    assert common_arcs(a, a) == 9
    assert common_arcs((), a) == 0


def test_the_vector_is_in_feature_name_order_and_bounded() -> None:
    new = _alarm(2, 5, 1, T + 7200.0, sev=0)
    old = _alarm(1, 5, 1, T, sev=-1)
    v = vector(new, old, Learner(), EpisodeMemory(), burst=12)
    assert len(v) == len(FEATURE_NAMES)
    named = dict(zip(FEATURE_NAMES, v, strict=True))
    assert named["dt"] == MAX_DT_S, "capped at the recall horizon"
    assert named["same_ne"] == 1.0 and named["same_class"] == 1.0
    assert named["severity"] == UNKNOWN_SEVERITY, "unknown when either side is unplaced"
    assert named["burst"] == 12.0


def test_features_are_relations_never_identifiers() -> None:
    """Rename every element and class id consistently: not one feature may change.

    Migration 0008 rule 2 says keys are not features. This is the executable form: a vector that
    moved under a renaming would be carrying an identifier.
    """
    learner, mem = Learner(), EpisodeMemory()
    for k in range(3):
        mem.observe((1, 5), (2, 6), T + k * (EPISODE_GAP_S + 1))
    new, old = _alarm(20, 5, 1, T + 9000.0, sev=1), _alarm(10, 6, 2, T + 8990.0, sev=0)
    base = vector(new, old, learner, mem, burst=4)

    mem2 = EpisodeMemory()
    for k in range(3):
        mem2.observe((101, 505), (202, 606), T + k * (EPISODE_GAP_S + 1))
    renamed = vector(
        _alarm(920, 505, 101, T + 9000.0, sev=1), _alarm(910, 606, 202, T + 8990.0, sev=0),
        Learner(), mem2, burst=4,
    )
    assert renamed == base
    assert dict(zip(FEATURE_NAMES, base, strict=True))["ne_episodes"] == 3.0, "all three closed"


def test_recall_reaches_past_the_window_and_respects_its_bounds() -> None:
    idx = CandidateIndex()
    old = _alarm(1, 5, 1, T)
    idx.add(old)
    far = _alarm(2, 5, 1, T - 2 * retrieval.RING_S)
    idx.by_ne[1].appendleft(far)
    new = _alarm(3, 9, 1, T + 600.0)  # ten minutes later: outside any 120 s window
    got = idx.recall(new, live={1, 2}, neighbours=[], exclude=set())
    assert [a.alarm_id for a in got] == [1], "same element, ten minutes on, beyond the horizon excluded"
    assert idx.recall(new, live={2}, neighbours=[], exclude=set()) == [], "a cleared alarm is not live"
    assert idx.recall(new, live={1}, neighbours=[], exclude={1}) == [], "the window's own are skipped"


def test_recall_by_oid_parent_is_a_subtree_not_a_string_prefix() -> None:
    idx = CandidateIndex()
    sibling = _alarm(1, 5, 7, T, oid="1.3.6.1.4.1.2544.1.11.7.5.0.12")
    not_sibling = _alarm(2, 6, 8, T, oid="1.3.6.1.4.1.2544.1.11.7.5.0.1")
    idx.add(sibling)
    idx.add(not_sibling)
    new = _alarm(3, 9, 9, T + 60.0, oid="1.3.6.1.4.1.2544.1.11.7.5.0.16")
    got = {a.alarm_id for a in idx.recall(new, live={1, 2}, neighbours=[], exclude=set())}
    assert got == {1, 2}, "both are children of …7.5.0"
    other = _alarm(4, 9, 9, T + 60.0, oid="1.3.6.1.4.1.2544.1.11.7.5.01.16")
    assert idx.recall(other, live={1, 2}, neighbours=[], exclude=set()) == []


def test_recall_is_capped() -> None:
    idx = CandidateIndex()
    idx.by_ne[1] = deque(maxlen=10_000)
    for i in range(500):
        idx.by_ne[1].append(_alarm(i, 5, 1, T + i))
    new = _alarm(10_000, 5, 1, T + 600.0)
    got = idx.recall(new, live=set(range(500)), neighbours=[], exclude=set())
    assert len(got) == retrieval.PER_NE <= retrieval.MAX_RECALL
    idx.prune(T + 600.0 + retrieval.RING_S * 3)
    assert idx.by_ne == {}


@pytest.mark.parametrize("name", FEATURE_NAMES)
def test_every_feature_is_documented(name: str) -> None:
    from netcorenoc.engine.correlate import features

    assert f"``{name}``" in (features.__doc__ or "")
