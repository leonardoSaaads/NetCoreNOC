"""The training pipeline's evidence rules (ADRs #408, #409): what is held out, from what, and how.

Two of Part VIII's injections live here, each with its control:

* *pairs from one incident split across train and test* —
  `test_an_incident_is_never_split_across_the_time_cut` and
  `test_no_stream_is_in_two_splits`;
* *a model evaluated only on the families it was trained on* —
  `test_held_out_families_are_never_trained_or_tuned_on` and
  `test_the_bar_checks_every_held_out_family`.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace

import pytest

from synth import dataset, report, train
from synth.compose import StreamSpec, compose
from synth.evaluate import Outcome, situation_metrics
from synth.record import Activation, StreamLog


@pytest.fixture(scope="module")
def specs() -> Iterator[dict[str, list[StreamSpec]]]:
    yield dataset.specs(scale=1.0)


def test_no_stream_is_in_two_splits(specs: dict[str, list[StreamSpec]]) -> None:
    """Streams are the unit, and an incident is stream-qualified: disjoint streams, disjoint
    incidents. Every random draw is keyed on the stream's **name** as well as its seed, so the
    per-split seeds may repeat (`train-000` and `valid-000` share one) without the streams being
    one stream twice — asserted by composing two specs that differ only in name."""
    names = [s.name for split in specs.values() for s in split]
    assert len(names) == len(set(names)), "a stream appears in two splits"
    one = replace(specs["train"][0], hours=1.0)
    other = replace(one, name="valid-000")
    a, b = compose(one), compose(other)
    assert [e.trap_oid for e in a.events] != [e.trap_oid for e in b.events] or [
        e.source for e in a.events
    ] != [e.source for e in b.events], "two names with one seed composed the same stream"


def test_held_out_families_are_never_trained_or_tuned_on(
    specs: dict[str, list[StreamSpec]],
) -> None:
    held_out = set(dataset.HOLDOUT_OPTICAL) | set(dataset.HOLDOUT_PROTOCOL)
    for split in [name for name in specs if not name.startswith("test_")]:
        for spec in specs[split]:
            assert not held_out & set(spec.families), f"{spec.name} trains on a held-out family"
    for split, families in (
        ("test_optical", dataset.HOLDOUT_OPTICAL),
        ("test_protocol", dataset.HOLDOUT_PROTOCOL),
    ):
        for spec in specs[split]:
            assert set(families) <= set(spec.families), f"{spec.name} lacks its held-out families"
    assert report.HELD_OUT == {
        "test_optical": dataset.HOLDOUT_OPTICAL,
        "test_protocol": dataset.HOLDOUT_PROTOCOL,
    }, "the evaluation does not score every held-out family on its own"


def test_the_bar_checks_every_held_out_family() -> None:
    """A bar with no `held_out` check would pass a model that breaks on every new family."""
    scopes = {scope for _q, scope, _k, _v in train.QUALITY_BAR}
    assert scopes == {"splits", "held_out"}
    evaluation = {
        "splits": {"test_iid": _arms(1.0, 1.0)},
        "held_out_families": {"bgp_flap": _arms(2.0, 1.0)},  # twice the formula's repair work
    }
    verdict = report.check_bar(evaluation, train.QUALITY_BAR)
    assert not verdict["passed"]
    assert any("held-out family bgp_flap: repair_gestures" in m for m in verdict["missed"])


def _arms(model_gestures: float, formula_gestures: float) -> dict[str, dict[str, dict[str, float]]]:
    base = {
        "pairwise_f1": 0.95,
        "ari": 0.95,
        "over_merge_rate": 0.05,
        "under_merge_rate": 0.08,
        "split_bag_intact_rate": 0.2,
        "asserted_negative_respected_rate": 0.85,
    }

    def arm(gestures: float) -> dict[str, dict[str, float]]:
        values = {**base, "repair_gestures": gestures}
        return {k: {"point": v, "low": v, "high": v} for k, v in values.items()}

    return {"model": arm(model_gestures), "formula": arm(formula_gestures)}


def test_the_bar_reads_each_kind_of_check() -> None:
    bar = (
        ("pairwise_f1", "splits", "min", 0.9),
        ("over_merge_rate", "splits", "diff_max", 0.01),
        ("repair_gestures", "splits", "ratio_max", 0.9),
    )
    good = report.check_bar({"splits": {"s": _arms(0.5, 1.0)}, "held_out_families": {}}, bar)
    assert good["passed"] and good["checked"] == 3
    bad = report.check_bar({"splits": {"s": _arms(0.95, 1.0)}, "held_out_families": {}}, bar)
    assert bad["missed"] == ["split s: repair_gestures ratio_max 0.9500, required 0.9"]


def _act(aid: int, ts: float, incident: str) -> Activation:
    return Activation(aid, ts, incident, "fam", True)


def test_an_incident_is_never_split_across_the_time_cut() -> None:
    """An incident starting before the 70 % mark and ending after it belongs wholly to the older
    side: it trains, and it is not scored in ``test_time``."""
    acts = [
        _act(1, 0.0, "a"),
        _act(2, 60.0, "b"),
        _act(3, 69.0, "c"),
        _act(4, 75.0, "c"),
        _act(5, 90.0, "d"),
        _act(6, 100.0, "d"),
    ]
    side = dataset.incident_side(acts, (0.0, 0.7))
    assert side == {"a": True, "b": True, "c": True, "d": False}
    log = StreamLog("s", ops=[("a", a) for a in acts])
    for act in acts[1:]:
        act.candidates = [(acts[0].alarm_id, True, (1.0,) * 15)]
    rows = dataset.training_rows([log], time_window=(0.0, 0.7))
    trained = {r.incident for r in rows}
    assert any(r.incident == "c" and r.ts == 75.0 for r in rows), (
        "the straddling incident lost its later pairs to the cut, so they would be scored as test"
    )
    assert "d" not in trained, "an incident of the newer part trained"


def test_repair_gestures_count_merges_and_moves() -> None:
    """Incident x split in two (one merge), and situation 2 also holds incident y (one move)."""
    outcome = Outcome(
        name="s",
        truth=["x", "x", "y"],
        family=["f", "f", "f"],
        pred=[1, 2, 2],
        ts=[0.0, 1.0, 2.0],
        compared=[],
        concurrent={},
    )
    m = situation_metrics([outcome])
    assert m["repair_gestures"] == pytest.approx(2 / 2)
    perfect = Outcome("s", ["x", "x", "y"], ["f"] * 3, [1, 1, 2], [0.0, 1.0, 2.0], [], {})
    assert situation_metrics([perfect])["repair_gestures"] == 0.0


def test_a_recording_captures_the_full_feature_vector() -> None:
    """The recorder runs the real engine with a probe model; if anything else decides — the
    formula, which reads no v2 vector — every candidate is recorded empty and training has nothing
    to learn from. That happened once (the probe was activated without choosing the site family);
    this is the guard."""
    from netcorenoc.engine.correlate.features import FEATURE_NAMES

    from synth.record import record

    spec = replace(dataset.specs(scale=1.0)["train"][0], hours=0.5)
    log = record(compose(spec))
    vectors = [v for a in log.activations() for _c, _w, v in a.candidates]
    assert vectors, "the recording scored no pair at all"
    assert {len(v) for v in vectors} == {len(FEATURE_NAMES)}


def test_grouping_is_chosen_under_the_bar_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR #420: the grouping is the fewest repair gestures among the settings that pass every
    check of the quality bar **on validation** — `splits` checks pooled, `held_out` checks per
    family. The first rule (pooled split-bag alone) chose a setting that already failed eleven of
    the bar's checks on validation, and the model missed the bar on test.

    Bars are built from the data so the test does not depend on a small recording's numbers: a
    bar every setting passes picks the fewest gestures; a bar that excludes exactly those settings
    picks the next; a per-family bar nothing can meet ships nothing."""
    from netcorenoc.engine.model import gam

    from modelutil import TEST_MODEL
    from synth.record import record

    logs = [record(compose(replace(s, hours=1.0))) for s in dataset.specs(scale=1.0)["valid"][:2]]
    scorer = gam.load(TEST_MODEL)
    monkeypatch.setattr(train, "JOIN_GRID", (-0.5, 0.5, 2.0))
    monkeypatch.setattr(train, "MERGE_GRID", (2.0, 4.0))
    monkeypatch.setattr(train, "PAIRS_GRID", (2,))

    monkeypatch.setattr(train, "QUALITY_BAR", (("pairwise_f1", "splits", "min", 0.0),))
    chosen, rows = train.tune_grouping({"valid": logs}, scorer)
    assert all(r["admissible"] == 1.0 for r in rows)
    fewest = min(r["repair_gestures"] for r in rows)
    pick = next(
        r
        for r in rows
        if r["join_bias"] == chosen.join_bias and r["merge_bias"] == chosen.merge_bias
    )
    assert pick["repair_gestures"] == fewest

    distinct = sorted({r["repair_gestures"] for r in rows})
    if len(distinct) > 1:  # exclude the cheapest settings through the bar, and only through it
        floor = ("repair_gestures", "splits", "min", distinct[1])
        monkeypatch.setattr(train, "QUALITY_BAR", (floor,))
        again, rows = train.tune_grouping({"valid": logs}, scorer)
        pick = next(
            r
            for r in rows
            if r["join_bias"] == again.join_bias and r["merge_bias"] == again.merge_bias
        )
        assert pick["repair_gestures"] == distinct[1]
        assert all(r["admissible"] == 0.0 for r in rows if r["repair_gestures"] == distinct[0])

    monkeypatch.setattr(train, "QUALITY_BAR", (("repair_gestures", "held_out", "ratio_max", -1.0),))
    with pytest.raises(SystemExit, match="passes the quality bar on validation"):
        train.tune_grouping({"valid": logs}, scorer)


def test_the_ablation_never_drops_what_the_formula_reads() -> None:
    """v0.29.0 (ADR #439): drop-one ablation measures each feature against all the others, so a
    group of features that carry one signal can each look dispensable and go together. On the
    v0.29.0 data that took `same_ne` with its two cousins, and every model split the corpus's fibre
    cut. The formula's three relations are kept whatever they measured; anything else still pays its
    way — the control below drops a feature that did not. The focused round (ADR #442) adds
    `cross_ref`, the relation candidate recall now proposes pairs by."""
    deltas = dict.fromkeys(train.CORE_FEATURES, 0.0) | {"entity_affinity": 0.0, "burst": 0.01}
    kept = train.kept_features(tuple(deltas), deltas)
    assert set(train.CORE_FEATURES) <= set(kept), kept
    assert "burst" in kept
    assert "entity_affinity" not in kept, "a feature below the threshold outside the core was kept"
    assert set(train.CORE_FEATURES) == {"dt", "same_ne", "same_class", "cross_ref"}
