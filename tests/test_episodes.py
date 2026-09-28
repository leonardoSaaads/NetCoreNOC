"""Episode memory (`engine/correlate/episodes.py`): relatedness counted in separate occasions."""

from __future__ import annotations

from netcorenoc.engine.correlate import episodes as ep
from netcorenoc.engine.correlate.episodes import EpisodeMemory

T = 1_800_000_000.0


def test_a_burst_is_one_episode_and_does_not_vote_for_itself() -> None:
    """Three hundred co-occurrences inside one burst are one episode, and while that episode is
    open the prior count is zero — an incident's own alarms are not evidence for it."""
    mem = EpisodeMemory()
    for i in range(300):
        mem.observe((1, 7), (2, 7), T + i)
    assert mem.ne.counts[(1, 2)][0] == 1.0
    assert mem.ne_prior(1, 2, T + 300) == 0
    assert mem.item_prior((1, 7), (2, 7), T + 300) == 0


def test_a_later_occasion_is_a_new_episode() -> None:
    mem = EpisodeMemory()
    mem.observe((1, 7), (2, 8), T)
    later = T + ep.EPISODE_GAP_S + 1
    assert mem.ne_prior(1, 2, later) == 1, "the first occasion is prior evidence the next time"
    mem.observe((1, 7), (2, 8), later)
    assert mem.ne.counts[(1, 2)][0] == 2.0
    assert mem.ne_prior(1, 2, later + 5) == 1, "…and the current one still does not count"
    assert mem.cls_prior(7, 8, later + ep.EPISODE_GAP_S + 1) == 2


def test_same_element_is_not_a_pair() -> None:
    mem = EpisodeMemory()
    mem.observe((1, 7), (1, 8), T)
    assert mem.ne.counts == {}
    assert mem.ne_prior(1, 1, T) == 0
    assert mem.item.counts, "the item pair (element, class) is still remembered"


def test_neighbours_need_two_separate_occasions() -> None:
    mem = EpisodeMemory()
    mem.observe((1, 7), (2, 7), T)
    assert mem.neighbours(1) == []
    mem.observe((1, 7), (2, 7), T + ep.EPISODE_GAP_S + 1)
    assert mem.neighbours(1) == [2] and mem.neighbours(2) == [1]
    assert mem.degree(1) == 1


def test_prune_bounds_by_age_and_by_size(monkeypatch: object) -> None:
    mem = EpisodeMemory()
    mem.observe((1, 7), (2, 7), T)
    mem.prune(T + ep.MAX_AGE_S + 1)
    assert mem.ne.counts == {} and mem.partners == {}
    import pytest

    mp = pytest.MonkeyPatch()
    mp.setattr(ep, "MAX_PAIRS", 2)
    for i in range(5):
        mem.observe((10 + i, 7), (20 + i, 7), T + i)
    mem.prune(T + 10)
    assert len(mem.ne.counts) == 2
    assert set(mem.ne.counts) == {(13, 23), (14, 24)}, "the stalest go first"
    mp.undo()


def test_flush_and_load_round_trip() -> None:
    mem = EpisodeMemory()
    mem.observe((1, 7), (2, 8), T)
    mem.observe((1, 7), (2, 8), T + ep.EPISODE_GAP_S + 1)
    rows = mem.flush()
    assert mem.flush() == [], "flush drains"
    again = EpisodeMemory()
    again.load(rows)
    assert again.ne.counts == mem.ne.counts
    assert again.item.counts == mem.item.counts
    assert again.neighbours(1) == [2]
