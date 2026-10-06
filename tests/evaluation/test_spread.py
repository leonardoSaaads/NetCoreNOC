"""Incidents spread over time (ADR #442): the families, the regime, the weights and the measure.

The generator's half — the families stay one incident, keep away from the held-out line systems,
and the regime draws nothing at intensity zero — and the trainer's half: the time-gap balancing
keeps the total weight and lifts the rare bands, and `report.by_gap` counts what it says it counts.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from synth import dataset, report, train
from synth.catalogue import roles
from synth.compose import FAMILIES, StreamSpec, compose, derived_rng
from synth.emit import LINK_DOWN
from synth.estate import build_estate
from synth.evaluate import Outcome
from synth.families import Builder, hosts
from synth.spread import SPREAD_FAMILIES, SpreadRegime, bystander, stretch

#: The held-out optical families' own fault roles (`families.dwdm_*`, `optical_protection`).
OPTICAL_ROLES = (
    "optical_los",
    "amp_output_low",
    "amp_abnormal",
    "loss_of_frame",
    "protection_switch",
)


def _draw(family: str, seed: int) -> list:
    for attempt in range(40):
        estate = build_estate(derived_rng("spread-test", family, seed, attempt))
        if hosts(estate, FAMILIES[family]):
            break
    b = Builder(derived_rng("spread-draw", family, seed), estate, "k", family)
    FAMILIES[family].function(b)
    return b.events


@pytest.mark.parametrize("family", sorted(SPREAD_FAMILIES))
def test_a_spread_family_is_one_incident_over_minutes_without_an_optical_shape(family: str) -> None:
    # The vendors' own optical notifications; a role's IETF fallback (`linkDown`, the entity MIB's
    # configuration change) is what every family sends and proves nothing either way.
    optical = {
        oid
        for role in OPTICAL_ROLES
        for vendor, note in roles()[role].items()
        if vendor != "*"
        for oid in (note.oid, note.clear)
        if oid
    }
    spans = []
    for seed in range(12):
        events = _draw(family, seed)
        assert events, f"{family} drew nothing"
        assert {e.incident for e in events} == {"k"} and {e.family for e in events} == {family}
        assert not {e.trap_oid for e in events} & optical, "a held-out optical fault role was used"
        raises = sorted(e.t for e in events if not e.is_clear)
        spans.append(raises[-1] - raises[0])
    assert max(spans) >= 300.0, f"{family} never spread past five minutes: {spans}"


def test_the_spread_families_are_training_families_and_none_is_held_out() -> None:
    assert set(dataset.SPREAD) == set(SPREAD_FAMILIES) <= set(dataset.TRAIN_FAMILIES)
    held_out = set(dataset.HOLDOUT_OPTICAL) | set(dataset.HOLDOUT_PROTOCOL)
    assert not held_out & set(dataset.SPREAD)


def test_the_spread_splits_lean_on_the_spread_families() -> None:
    specs = dataset.specs(scale=1.0)
    for split in ("train_spread", "valid_spread", "test_spread"):
        for spec in specs[split]:
            weights = dict(spec.weights)
            assert spec.spread >= 0.6 and all(weights[f] > 1.0 for f in dataset.SPREAD)
    ordinary = [s.spread for s in specs["train"]]
    assert 0 < sum(1 for v in ordinary if v > 0) < len(ordinary), "a third, not none or all"


def test_stretch_multiplies_offsets_from_the_first_event() -> None:
    events = _draw("staged_link_failure", 1)
    before = [e.t for e in events]
    start = min(before)
    stretch(events, 3.0)
    assert [e.t for e in events] == pytest.approx([start + (t - start) * 3.0 for t in before])


def test_the_regime_draws_nothing_at_intensity_zero() -> None:
    rng = derived_rng("regime-zero")
    state = rng.getstate()
    regime = SpreadRegime(0.0)
    assert regime.stretch_factor(rng) == 1.0 and regime.bystanders(rng) == 0
    assert rng.getstate() == state, "an intensity of zero consumed a draw"


def test_bystanders_are_isolated_alerts_on_the_incidents_own_elements() -> None:
    spec = StreamSpec(
        "bystander-test",
        7,
        tuple(dataset.SPREAD),
        12.0,
        2.0,
        0.0,
        concurrency=0.0,
        recurrence=0.0,
        spread=1.0,
    )
    stream = compose(spec)
    lone = {k for k, f in stream.incidents.items() if "/b" in k}
    assert lone and all(stream.incidents[k] == "noise" for k in lone)
    sources: dict[str, set[str]] = {}
    for e in stream.events:
        sources.setdefault(e.incident, set()).add(e.source)
    incident_sources = set().union(*(v for k, v in sources.items() if k not in lone))
    for key in lone:
        assert sources.get(key, set()) <= incident_sources, "a bystander on an untouched element"
    assert not [k for k in compose(replace(spec, spread=0.0)).incidents if "/b" in k]


def test_a_vendor_that_names_its_far_end_carries_it_and_one_that_does_not_does_not() -> None:
    events = _draw("staged_link_failure", 3)
    estate_events = [e for e in events if e.trap_oid == LINK_DOWN and not e.is_clear]
    assert estate_events
    estate = build_estate(derived_rng("spread-test", "staged_link_failure", 3, 0))
    for flag in (True, False):
        el = next(iter(estate.elements.values()))
        el.names_far = flag
        b = Builder(derived_rng("far"), estate, "k", "f")
        b.say(el, ("link_down",), "ge-0/0/1", 0.0, far="192.0.2.77")
        values = [v for _o, _k, v in b.events[0].varbinds]
        assert ("192.0.2.77" in values) is flag
    b = Builder(derived_rng("lone"), estate, "n", "noise")
    bystander(b, next(iter(estate.elements.values())))
    assert len({e.incident for e in b.events}) == 1


def _row(dt: float, y: int, w: float = 1.0) -> dataset.Row:
    return dataset.Row((dt,) + (0.0,) * 14, y, w, "s", "i", "f", 0.0)


def test_balancing_keeps_the_total_and_lifts_the_rare_bands() -> None:
    rows = [_row(1.0, 1)] * 90 + [_row(600.0, 1)] * 2 + [_row(600.0, 0)] * 8
    out = train.balance_gaps(rows)
    assert sum(r.w for r in out) == pytest.approx(sum(r.w for r in rows))
    share = sum(r.w for r in out if r.x[0] >= 300 and r.y) / sum(r.w for r in out if r.y)
    assert share > 2 / 92, "the far positives did not rise"
    near = sum(r.w for r in out if r.x[0] < 10) / sum(r.w for r in out)
    assert near > sum(r.w for r in out if r.x[0] >= 300) / sum(r.w for r in out), (
        "the square root keeps the order of the bands"
    )


def test_by_gap_counts_same_incident_pairs_per_band() -> None:
    outcome = Outcome(
        "s",
        truth=["a", "a", "a", "b"],
        family=["f"] * 4,
        pred=[1, 1, 2, 1],
        ts=[0.0, 5.0, 700.0, 6.0],
        compared=[],
        concurrent={},
        clear=[False] * 4,
    )
    got = report.by_gap([outcome], seed=1)
    assert got["0-10s"] == {"grouped": 1.0, "pairs": 1.0}
    assert got["300-1800s"] == {"grouped": 0.0, "pairs": 2.0}, "both far pairs, both split"
