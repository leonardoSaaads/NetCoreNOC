from __future__ import annotations

from opticorr.rootcause import Member, Precedence
from opticorr.store import Store


def member(alarm_id: int, class_id: int, device_id: int, ts: float) -> Member:
    return Member(alarm_id, class_id, device_id, ts)


def test_lead_prob_defaults_to_half() -> None:
    precedence = Precedence()
    assert precedence.class_lead_prob(1, 2) == 0.5
    assert precedence.class_lead_prob(1, 1) == 0.5


def test_observe_accumulates_directional_wins() -> None:
    precedence = Precedence()
    for _ in range(3):
        precedence.observe(lead=(1, 10), lag=(2, 11))
    precedence.observe(lead=(2, 11), lag=(1, 10))
    assert precedence.class_lead_prob(1, 2) == 0.75
    assert precedence.class_lead_prob(2, 1) == 0.25
    assert precedence.device_lead_prob(10, 11) == 0.75


def test_observe_skips_self_pairs() -> None:
    precedence = Precedence()
    precedence.observe(lead=(1, 10), lag=(1, 10))
    assert precedence.class_wins == {} and precedence.device_wins == {}


def test_pick_root_earliest_when_nothing_learned() -> None:
    precedence = Precedence()
    members = [member(1, 1, 10, ts=5.0), member(2, 2, 10, ts=1.0), member(3, 3, 10, ts=9.0)]
    assert precedence.pick_root(members) == 2


def test_pick_root_prefers_learned_precedence_over_arrival_order() -> None:
    precedence = Precedence()
    for _ in range(10):
        precedence.observe(lead=(1, 10), lag=(2, 10))
    members = [member(7, 2, 10, ts=0.0), member(8, 1, 10, ts=2.0)]
    assert precedence.pick_root(members) == 8  # class 1 leads despite arriving later


def test_pick_root_empty_and_singleton() -> None:
    precedence = Precedence()
    assert precedence.pick_root([]) is None
    assert precedence.pick_root([member(4, 1, 10, ts=0.0)]) == 4


def test_pick_root_bounded_sample() -> None:
    precedence = Precedence()
    members = [member(i, i, 10, ts=float(i)) for i in range(60)]
    assert precedence.pick_root(members) == 0


async def test_save_and_load_roundtrip(store: Store) -> None:
    precedence = Precedence()
    for _ in range(4):
        precedence.observe(lead=(1, 10), lag=(2, 11))
    await precedence.save(store, ts=1.0)
    await store.commit()
    restored = Precedence()
    await restored.load(store)
    assert restored.class_lead_prob(1, 2) == 1.0
    assert restored.device_lead_prob(10, 11) == 1.0
    precedence.observe(lead=(1, 10), lag=(2, 11))
    await precedence.save(store, ts=2.0)  # dirty set drained: only new deltas written
    await store.commit()
    cur = await store.conn.execute("SELECT version FROM edge WHERE kind='lead_class'")
    row = await cur.fetchone()
    assert row is not None and row["version"] == 2
