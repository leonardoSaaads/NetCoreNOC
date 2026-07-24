from __future__ import annotations

from netcorenoc.learn import (
    MIN_EDGE_N,
    SPLIT_PENALTY,
    STORM_DAMPING,
    ClearPairLearner,
    Learner,
    Matrix,
)
from netcorenoc.store import Store

import util


def feed_exclusive_pair(matrix: Matrix, a: int, b: int, times: int) -> None:
    for _ in range(times):
        matrix.observe_occurrence(a)
        matrix.observe_occurrence(b)
        matrix.observe_pair(a, b)


def test_npmi_zero_when_unseen() -> None:
    matrix = Matrix("class")
    assert matrix.npmi(1, 2) == 0.0
    matrix.observe_occurrence(1)
    matrix.observe_occurrence(2)
    assert matrix.npmi(1, 2) == 0.0  # occurrences alone are not association


def test_npmi_high_for_exclusive_cooccurrence() -> None:
    matrix = Matrix("class")
    feed_exclusive_pair(matrix, 1, 2, times=6)
    assert matrix.npmi(1, 2) > 0.8  # perfect association, shrunk only by n/(n+1)


def test_npmi_single_cooccurrence_is_weak_evidence() -> None:
    matrix = Matrix("class")
    matrix.observe_occurrence(1)
    matrix.observe_occurrence(2)
    matrix.observe_pair(1, 2)
    assert matrix.npmi(1, 2) <= 0.5  # one observation is never proof


def test_npmi_discriminates_signal_from_noise() -> None:
    matrix = Matrix("class")
    feed_exclusive_pair(matrix, 1, 2, times=8)
    for noise_class in range(10, 40):  # frequent unrelated classes
        matrix.observe_occurrence(noise_class)
    matrix.observe_pair(1, 30)  # one accidental co-occurrence
    strong, weak = matrix.npmi(1, 2), matrix.npmi(1, 30)
    assert strong > 0.8
    assert 0.0 <= weak < 0.4
    assert matrix.npmi(10, 11) == 0.0


def test_forgetting_decays_masses_per_epoch() -> None:
    matrix = Matrix("class")
    feed_exclusive_pair(matrix, 1, 2, times=4)
    before = matrix.pair_mass(1, 2)
    for _ in range(10):
        matrix.tick()
    after = matrix.pair_mass(1, 2)
    assert after < before
    assert abs(after - before * 0.95**10) < 1e-9


def test_scale_pair_halves_mass() -> None:
    matrix = Matrix("class")
    feed_exclusive_pair(matrix, 1, 2, times=4)
    before = matrix.pair_mass(1, 2)
    matrix.scale_pair(1, 2, SPLIT_PENALTY)
    assert matrix.pair_mass(1, 2) == before * SPLIT_PENALTY
    matrix.scale_pair(7, 8, SPLIT_PENALTY)  # unknown pair is a no-op
    assert matrix.pair_mass(7, 8) == 0.0


def test_state_roundtrip_preserves_scores() -> None:
    matrix = Matrix("class")
    feed_exclusive_pair(matrix, 1, 2, times=6)
    matrix.observe_occurrence(3)
    edges = matrix.flush()
    restored = Matrix("class")
    restored.load_state(matrix.state(), edges)
    assert restored.npmi(1, 2) == matrix.npmi(1, 2)
    assert restored.pair_mass(1, 2) == matrix.pair_mass(1, 2)


def test_storm_damping_reduces_update_weight() -> None:
    learner = Learner()
    learner.observe_pairs((1, 10), [(2, 11)], storm=False)
    learner.observe_pairs((3, 12), [(4, 13)], storm=True)
    assert learner.A.pair_mass(1, 2) == 1.0
    assert learner.A.pair_mass(3, 4) == STORM_DAMPING


def test_device_affinity_gating_and_same_device() -> None:
    learner = Learner()
    assert learner.device_affinity(5, 5) == 1.0
    for _ in range(int(MIN_EDGE_N) - 1):
        learner.E.observe_occurrence(1)
        learner.E.observe_occurrence(2)
        learner.E.observe_pair(1, 2)
    assert learner.device_affinity(1, 2) == 0.0  # below the trust threshold
    learner.E.observe_pair(1, 2)
    assert learner.device_affinity(1, 2) > 0.75


def test_entity_affinity_three_branches() -> None:
    from netcorenoc.learn import SAME_NE_AFFINITY

    learner = Learner()
    # same entity -> 1.0 (regardless of NE)
    assert learner.entity_affinity(7, 3, 7, 3) == 1.0
    # same NE, different entity -> the structural SAME_NE_AFFINITY (0.8)
    assert learner.entity_affinity(7, 3, 8, 3) == SAME_NE_AFFINITY
    # different NE, untrusted -> 0.0 until n >= MIN_EDGE_N
    assert learner.entity_affinity(7, 3, 8, 4) == 0.0
    for _ in range(int(MIN_EDGE_N)):
        learner.E.observe_occurrence(3)
        learner.E.observe_occurrence(4)
        learner.E.observe_pair(3, 4)
    assert learner.entity_affinity(7, 3, 8, 4) > 0.0  # learned NE-level edge now trusted

    # Parity: before any promotion the entity is the NE (entity==device), so entity_affinity
    # reduces to device_affinity exactly.
    assert learner.entity_affinity(3, 3, 4, 4) == learner.device_affinity(3, 4)
    assert learner.entity_affinity(3, 3, 3, 3) == 1.0


def test_same_class_cross_device_affinity_is_learned_not_assumed() -> None:
    learner = Learner()
    assert learner.class_affinity(1, 1) == 0.0  # honest cold start
    for _ in range(6):
        learner.A.observe_occurrence(1)
        learner.A.observe_pair(1, 1)
    assert learner.class_affinity(1, 1) > 0.0


def test_learn_epoch_reinforces_and_ages() -> None:
    learner = Learner()
    members = [(1, 10), (2, 11), (3, 10)]
    learner.learn_epoch(members)
    assert learner.A.epoch == 1
    assert learner.A.pair_mass(1, 2) == 1.0
    assert learner.E.pair_mass(10, 11) == 1.0  # distinct pair reinforced once
    assert learner.E.pair_mass(10, 10) == 0.0  # same-device pairs are not statistics


def test_observe_pairs_dedups_per_activation() -> None:
    learner = Learner()
    others = [(2, 11), (2, 11), (2, 12)]  # class 2 twice on device 11, once on 12
    learner.observe_pairs((1, 10), others, storm=False)
    assert learner.A.pair_mass(1, 2) == 1.0  # one observation per distinct class
    assert learner.E.pair_mass(10, 11) == 1.0
    assert learner.E.pair_mass(10, 12) == 1.0


def test_learn_epoch_damps_storm_situations() -> None:
    learner = Learner()
    members = [(c, 10) for c in range(60)]
    learner.learn_epoch(members)
    assert learner.A.pair_mass(0, 1) == STORM_DAMPING


def test_penalize_halves_the_grouped_pairs() -> None:
    learner = Learner()
    learner.learn_epoch([(1, 10), (2, 11)])
    learner.penalize([(1, 10), (2, 11)])
    assert learner.A.pair_mass(1, 2) == SPLIT_PENALTY
    assert learner.E.pair_mass(10, 11) == SPLIT_PENALTY


def test_clear_pair_learned_from_strict_alternation() -> None:
    clears = ClearPairLearner()
    for class_id in (1, 2, 1, 2):
        clears.observe(7, "if1", class_id)
    assert clears.raise_to_clear == {1: 2}
    assert clears.clear_to_raise == {2: 1}


def test_clear_pair_ignores_duplicates_within_alternation() -> None:
    clears = ClearPairLearner()
    for class_id in (1, 1, 2, 1, 1, 2):
        clears.observe(7, "if1", class_id)
    assert clears.raise_to_clear == {1: 2}


def test_clear_pair_third_class_restarts_tracking() -> None:
    clears = ClearPairLearner()
    for class_id in (1, 2, 9, 1, 2, 1, 2):
        clears.observe(7, "if1", class_id)
    assert clears.raise_to_clear == {}  # 9 reset; only 1.5 cycles since


def test_clear_pair_needs_two_full_cycles() -> None:
    clears = ClearPairLearner()
    for class_id in (1, 2, 1):
        clears.observe(7, "if1", class_id)
    assert clears.raise_to_clear == {}


def test_clear_pair_register_refuses_conflicts() -> None:
    clears = ClearPairLearner()
    clears.register(1, 2)
    clears.register(1, 3)  # raise already paired
    clears.register(4, 2)  # clear already paired
    assert clears.raise_to_clear == {1: 2}


async def test_learner_save_and_load_roundtrip(store: Store) -> None:
    learner = Learner()
    for _ in range(6):
        learner.observe_activation((1, 10))
        learner.observe_activation((2, 11))
        learner.observe_pairs((1, 10), [(2, 11)], storm=False)
    learner.clears.register(5, 6)
    await learner.save(store, ts=1.0)
    await store.commit()
    restored = Learner()
    await restored.load(store)
    assert restored.A.npmi(1, 2) == learner.A.npmi(1, 2)
    assert restored.device_affinity(10, 11) == learner.device_affinity(10, 11)
    assert restored.clears.clear_to_raise == {6: 5}


async def test_matrix_persistence_is_versioned(store: Store) -> None:
    learner = Learner()
    learner.observe_pairs((1, 10), [(2, 11)], storm=False)
    await learner.save(store, ts=1.0)
    learner.observe_pairs((1, 10), [(2, 11)], storm=False)
    await learner.save(store, ts=2.0)
    await store.commit()
    cur = await store.conn.execute("SELECT version FROM edge WHERE kind='class'")
    row = await cur.fetchone()
    assert row is not None and row["version"] == 2


def test_util_event_helper_defaults() -> None:
    assert util.event().trap_oid == util.CIENA_TRAP
