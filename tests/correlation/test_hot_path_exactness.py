"""ADR #448: the batch path got faster and **not one number moved**. Each optimisation, held to the
definition it replaced.

The hot-path rewrites are only acceptable because they are exact: `make eval` is byte-identical, and
so is every row of `situation`, `situation_alarm`, `link`, `alarm` and `dataset_pair` on a mixed
6 000-trap stream run through the old and the new engine. These tests keep each rewrite pinned to
its definition, so a later edit that changes a value fails here, by name, rather than as a moved
eval digest nobody can attribute.
"""

from __future__ import annotations

import math
import random

import pytest

from netcorenoc.engine.correlate.correlate import WindowAlarm
from netcorenoc.engine.correlate.episodes import EpisodeMemory
from netcorenoc.engine.correlate.features import vector, vectors
from netcorenoc.engine.correlate.learn import Learner
from netcorenoc.engine.correlate.rootcause import EARLIEST_BONUS, ROOT_SAMPLE, Member, Precedence
from netcorenoc.engine.operate.upkeep import SituationUpkeep

T = 1_800_000_000.0


def _alarm(rnd: random.Random, aid: int, ts: float) -> WindowAlarm:
    cls = rnd.randrange(6)  # few classes and elements, so the batch builder's reuse is exercised
    dev = rnd.randrange(5)
    oid = f"1.3.6.1.4.1.2011.5.{cls % 3}.{cls}"
    return WindowAlarm(
        aid,
        cls,
        dev,
        ts,
        rnd.choice((0, 0, 100 + dev)),  # level-0 or a promoted entity
        ".".join(oid.split(".")[:7]),
        tuple(oid.split(".")),
        rnd.randrange(-1, 5),
        rnd.randrange(4),
        f"10.0.0.{dev}",
        frozenset({f"10.0.0.{rnd.randrange(5)}"}) if rnd.random() < 0.3 else frozenset(),
    )


def _learned(rnd: random.Random) -> tuple[Learner, EpisodeMemory, list[WindowAlarm]]:
    """A learner and an episode memory that have seen a stream, and the stream."""
    learner, episodes = Learner(), EpisodeMemory()
    stream = [_alarm(rnd, i, T + i * rnd.uniform(1.0, 400.0)) for i in range(300)]
    stream.sort(key=lambda a: a.ts)
    for i, new in enumerate(stream):
        recent = stream[max(0, i - 20) : i]
        learner.observe_activation((new.class_id, new.device_id))
        learner.observe_pairs(
            (new.class_id, new.device_id), [(o.class_id, o.device_id) for o in recent], False
        )
        for old in recent:
            episodes.observe((new.device_id, new.class_id), (old.device_id, old.class_id), new.ts)
    return learner, episodes, stream


def _definition(
    new: WindowAlarm, old: WindowAlarm, learner: Learner, episodes: EpisodeMemory, burst: int
) -> tuple[float, ...]:
    """The v2 vector as `features.vector` computed it before ADR #448: one pair, no reuse."""
    dt = abs(new.ts - old.ts)
    ranks = (new.severity_rank, old.severity_rank)
    severity = 5 if min(ranks) < 0 else max(ranks)
    now = new.ts
    return (
        dt if dt < 3600.0 else 3600.0,
        1.0 if new.device_id == old.device_id else 0.0,
        1.0 if new.class_id == old.class_id else 0.0,
        float(sum(1 for _ in _shared(new.arcs, old.arcs))),
        learner.class_affinity(new.class_id, old.class_id),
        learner.entity_affinity(new.entity_id, new.device_id, old.entity_id, old.device_id),
        float(episodes.ne_prior(new.device_id, old.device_id, now)),
        float(episodes.cls_prior(new.class_id, old.class_id, now)),
        float(
            episodes.item_prior((new.device_id, new.class_id), (old.device_id, old.class_id), now)
        ),
        float(severity),
        float(burst),
        float(max(new.chatter, old.chatter)),
        float(max(episodes.degree(new.device_id), episodes.degree(old.device_id))),
        1.0 if (new.source in old.refs or old.source in new.refs) and new.source else 0.0,
        float(int(now // 3600) % 24),
    )


def _shared(a: tuple[str, ...], b: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    for x, y in zip(a, b, strict=False):
        if x != y or len(out) >= 16:
            break
        out.append(x)
    return out


@pytest.mark.parametrize("seed", range(5))
def test_the_batch_vectors_equal_the_per_pair_definition(seed: int) -> None:
    rnd = random.Random(seed)
    learner, episodes, stream = _learned(rnd)
    for new in stream[-40:]:
        olds = rnd.sample(stream, 60)
        burst = rnd.randrange(200)
        batch = vectors(new, olds, learner, episodes, burst)
        assert batch == [_definition(new, old, learner, episodes, burst) for old in olds]
        assert [vector(new, old, learner, episodes, burst) for old in olds] == batch


def test_the_fused_logit_is_bit_identical_to_the_standardised_sum(packaged_league: object) -> None:
    from netcorenoc.engine.model import league
    from netcorenoc.engine.model.linear import LinearScorer

    member = next(m for m in league.load().members if m.kind == "logistic_regression")
    scorer = member.scorer
    assert isinstance(scorer, LinearScorer)
    rnd = random.Random(7)
    for _ in range(2000):
        v = tuple(
            rnd.choice((0.0, -0.0, -1.0, 1e9, rnd.uniform(-50.0, 5000.0), rnd.random()))
            for _ in range(15)
        )
        expected = scorer.model.intercept
        for z, w in zip(scorer.standardised(v), scorer.model.weights, strict=True):
            expected += w * z
        got = scorer.logit(v)
        assert got == expected and math.copysign(1.0, got) == math.copysign(1.0, expected), v


def _reference_root(precedence: Precedence, members: list[Member]) -> int | None:
    """`pick_root` before ADR #448: sorted sample, the two named probabilities."""
    if not members:
        return None
    sample = sorted(members, key=lambda m: m.first_seen)[:ROOT_SAMPLE]
    best_id, best_score = sample[0].alarm_id, float("-inf")
    for index, candidate in enumerate(sample):
        score = -EARLIEST_BONUS * index
        for other in sample:
            if other.alarm_id == candidate.alarm_id:
                continue
            score += precedence.class_lead_prob(candidate.class_id, other.class_id)
            score += precedence.device_lead_prob(candidate.device_id, other.device_id)
        if score > best_score:
            best_id, best_score = candidate.alarm_id, score
    return best_id


@pytest.mark.parametrize("seed", range(5))
def test_the_root_and_its_kept_sample_match_the_definition(seed: int) -> None:
    rnd = random.Random(seed)
    precedence, upkeep = Precedence(), SituationUpkeep()
    members: list[Member] = []
    for step in range(400):
        roll = rnd.random()
        if roll < 0.75 or not members:  # an activation joins (ties on first_seen included)
            members.append(
                Member(step, rnd.randrange(8), rnd.randrange(6), float(rnd.randrange(60)))
            )
        elif roll < 0.85:  # an operator moves one out (the list mutates in place)
            members.remove(rnd.choice(members))
        elif roll < 0.9:  # a split replaces the list object
            members = list(members)
        else:  # a merge appends another situation's members
            members.extend(
                Member(
                    10_000 + step * 10 + k,
                    rnd.randrange(8),
                    rnd.randrange(6),
                    float(rnd.randrange(60)),
                )
                for k in range(rnd.randrange(1, 5))
            )
        for _ in range(rnd.randrange(4)):
            precedence.observe(
                (rnd.randrange(8), rnd.randrange(6)),
                (rnd.randrange(8), rnd.randrange(6)),
                rnd.random(),
            )
        sample = upkeep.earliest(1, members)
        assert sample == sorted(members, key=lambda m: m.first_seen)[:ROOT_SAMPLE]
        assert precedence.pick_root(members, sample) == _reference_root(precedence, members)
        assert precedence.pick_root(members) == _reference_root(precedence, members)
