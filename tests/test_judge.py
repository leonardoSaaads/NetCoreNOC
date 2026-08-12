"""The estimator, the fourth metric and the three-valued verdict (v0.10.0, Workstreams 3-5).

Everything here is proved on **purpose-built fixtures**, and that is not a shortcut. Gate 1 measured
that the fullest corpus this repository can construct supplies **zero asserted negative pairs**, has
**every merge chain one hop deep**, and has an **empty `checked` scope population** — so the judge,
the fourth metric and the blind-fraction rule are each mechanisms the corpus cannot exercise at all.
v0.9.1 met the same wall with its exclusion set and answered it the same way.

Each `INSUFFICIENT_EVIDENCE` trigger is fired by a corpus that fires **it and no other**, which is
the only way to know the trigger is reachable rather than merely present.
"""

from __future__ import annotations

import ast
from itertools import pairwise
from pathlib import Path

from netcorenoc import shadow_cv, shadow_eval
from netcorenoc.judge import Judgement, Trigger, Verdict, judge

REPO_ROOT = Path(__file__).resolve().parent.parent
PKG = REPO_ROOT / "src" / "netcorenoc"

# A corpus that fires NOTHING. Every trigger test perturbs exactly one field of this.
CLEAN: dict[str, object] = {
    "floors_met": True,
    "holdout_spent": True,
    "power": shadow_cv.Power(incidents=400, detectable=0.05, observed_difference=0.30),
    "smallest_side": 200,
    "coverage_excluded": 0,
    "unknown_rows": 0,
    "truncated_load_bearing": False,
    "unsound_chains": 0,
    "pre_v080_merges": 0,
    "asserting_incidents": 400,
    "difference_excludes_zero": True,
    "favours_challenger": True,
}


def _judge(**overrides: object) -> Judgement:
    return judge(**{**CLEAN, **overrides})  # type: ignore[arg-type]


# --- the clean baseline, which is the control for every trigger test ---------------------------


def test_the_clean_corpus_fires_no_trigger_and_can_reach_better() -> None:
    """**THE CONTROL for this whole file.**

    Every test below perturbs exactly one field of `CLEAN` and asserts one trigger fires. If `CLEAN`
    itself fired something — or could never reach `BETTER` — each of those tests would pass for the
    wrong reason and the trigger set would be untested.
    """
    outcome = _judge()
    assert outcome.triggers == ()
    assert outcome.verdict is Verdict.BETTER
    assert outcome.decisive


# --- every §6.2 trigger, one at a time -----------------------------------------------------------


def test_every_registered_trigger_is_reachable() -> None:
    """Guard the guard: a `Trigger` member no test fires would be a registered condition that can
    never happen, which is indistinguishable from a condition somebody forgot to wire."""
    fired: set[Trigger] = set()
    for override in (
        {"floors_met": False},
        {"holdout_spent": False},
        {"smallest_side": 9},
        {"power": shadow_cv.Power(incidents=400, detectable=0.05, observed_difference=0.01)},
        {"coverage_excluded": 1},
        {"unknown_rows": 1},
        {"truncated_load_bearing": True},
        {"unsound_chains": 1},
        {"pre_v080_merges": 41, "asserting_incidents": 400},
    ):
        fired.update(_judge(**override).triggers)
    assert fired == set(Trigger), f"unreachable trigger(s): {set(Trigger) - fired}"


def test_an_unmet_floor_fires_only_the_floor_trigger() -> None:
    outcome = _judge(floors_met=False)
    assert outcome.triggers == (Trigger.FLOOR_UNMET,)
    assert outcome.verdict is Verdict.INSUFFICIENT_EVIDENCE


def test_an_unspent_holdout_fires_only_the_holdout_trigger() -> None:
    """**The trigger v0.10.0 itself returns.** The seal is constructed and not spent, so this fires
    on every real run of this release — which is why §7.1 is the expected branch."""
    outcome = _judge(holdout_spent=False)
    assert outcome.triggers == (Trigger.HOLDOUT_UNSPENT,)


def test_a_thin_split_fires_only_the_thin_split_trigger() -> None:
    assert _judge(smallest_side=9).triggers == (Trigger.THIN_SPLIT,)
    assert _judge(smallest_side=10).triggers == (), "10 is the registered boundary, and it passes"


def test_the_power_condition_fires_only_the_power_trigger() -> None:
    """§2.5. **A trigger and not a floor**, and the one that makes the normative floors safe."""
    weak = shadow_cv.Power(incidents=400, detectable=0.05, observed_difference=0.01)
    assert _judge(power=weak).triggers == (Trigger.POWER,)


def test_a_difference_exactly_equal_to_the_threshold_is_insufficient() -> None:
    """Ambiguity about whether the data suffices resolves to **"it does not"** (Part VIII).

    The threshold is the smallest difference detectable at 80 % power, so equality is the boundary
    case rather than a pass.
    """
    equal = shadow_cv.Power(incidents=400, detectable=0.05, observed_difference=0.05)
    assert _judge(power=equal).triggers == (Trigger.POWER,)


def test_coverage_exclusions_fire_only_their_trigger() -> None:
    assert _judge(coverage_excluded=1).triggers == (Trigger.COVERAGE_EXCLUSIONS,)


def test_unknown_population_rows_fire_only_their_trigger() -> None:
    assert _judge(unknown_rows=1).triggers == (Trigger.UNKNOWN_POPULATION,)


def test_truncated_load_bearing_bags_fire_only_their_trigger() -> None:
    assert _judge(truncated_load_bearing=True).triggers == (Trigger.TRUNCATED_LOAD_BEARING,)


def test_an_unsound_merge_chain_fires_only_its_trigger() -> None:
    """§7.9. An identity nobody can resolve is not evidence."""
    assert _judge(unsound_chains=1).triggers == (Trigger.UNSOUND_CHAINS,)


def test_pre_v080_merges_fire_only_above_the_registered_share() -> None:
    """§7.10, and the boundary is asserted in both directions so the threshold is not vacuous."""
    assert _judge(pre_v080_merges=41, asserting_incidents=400).triggers == (
        Trigger.PRE_V080_MERGES,
    )
    assert _judge(pre_v080_merges=40, asserting_incidents=400).triggers == ()


def test_every_trigger_is_collected_rather_than_short_circuited() -> None:
    """The report says WHICH conditions fired. A build that returned on the first would make the
    second invisible, so an operator fixing one would find the verdict unchanged and unexplained."""
    outcome = _judge(floors_met=False, holdout_spent=False, unsound_chains=2)
    assert set(outcome.triggers) == {
        Trigger.FLOOR_UNMET,
        Trigger.HOLDOUT_UNSPENT,
        Trigger.UNSOUND_CHAINS,
    }


# --- the third value is terminal and cannot be collapsed ------------------------------------------


def test_insufficient_evidence_is_never_not_better() -> None:
    """**The whole reason the type has three values.**

    *"The challenger is not better"* and *"this corpus cannot tell"* are opposite claims.
    """
    thin = _judge(floors_met=False)
    # Compared through a set rather than with `is not`: after the first assertion mypy narrows the
    # value to a literal and flags `is not Verdict.NOT_BETTER` as a non-overlapping check — which
    # is the type system agreeing with the point being made, in a form that fails `--strict`.
    assert thin.verdict in {Verdict.INSUFFICIENT_EVIDENCE}
    assert thin.verdict not in {Verdict.NOT_BETTER, Verdict.BETTER}
    assert not thin.decisive


def test_no_caller_treats_the_verdict_as_a_boolean() -> None:
    """The honest half of the type's claim.

    Python gives every enum member a default truthiness, so `if verdict:` is `True` for all three
    — including `INSUFFICIENT_EVIDENCE`. The type cannot make that a `TypeError`. What it *can* do
    is offer no member whose name reads as a negative result, so a caller must name the value it
    means; this test closes the remaining gap by requiring that no module in the package branches on
    a bare `Verdict`.
    """
    assert bool(Verdict.INSUFFICIENT_EVIDENCE) is True, (
        "if this ever became False, `if verdict:` would silently take the insufficient branch as a "
        "negative result — which is the collapse the three-valued type exists to prevent"
    )
    offenders: list[str] = []
    for path in sorted(PKG.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.If)
                and isinstance(node.test, ast.Name)
                and ("verdict" in node.test.id or "judgement" in node.test.id)
            ):
                offenders.append(f"{path.relative_to(PKG)}:{node.lineno}")
    assert not offenders, f"a bare verdict is used as a condition at {offenders}"


def test_not_better_is_reachable_and_is_not_insufficiency() -> None:
    """**CONTROL.** A judge that could never return `NOT_BETTER` would make the two-vs-three
    distinction decorative."""
    outcome = _judge(difference_excludes_zero=False)
    assert outcome.verdict is Verdict.NOT_BETTER
    assert outcome.decisive


def test_burying_splits_is_not_better_rather_than_insufficient() -> None:
    """§7.6. An adverse MEASUREMENT is not an absence of evidence, and the report says which number
    produced the verdict."""
    outcome = _judge(split_bags_buried=True)
    assert outcome.verdict is Verdict.NOT_BETTER
    assert any("§7.6" in note for note in outcome.notes)
    assert not outcome.triggers, "a measured failure must not be reported as insufficiency"


def test_separating_everything_is_not_better() -> None:
    """§7.7. A challenger that separates everything respects every assertion **for free**."""
    outcome = _judge(separates_everything=True)
    assert outcome.verdict is Verdict.NOT_BETTER
    assert any("§7.7" in note for note in outcome.notes)


# --- floors and the detection threshold are emitted together -------------------------------------


def test_a_floor_evaluation_is_never_emitted_without_its_detection_threshold() -> None:
    """§2.5's structural mitigation, asserted on the type.

    A reader who sees *"floors met"* must not be able to read *"the evaluation is trustworthy"*.
    Both numbers live on one object, so a renderer cannot print one and forget the other — and
    `Judgement` has no constructor path that yields `floors_met` without `detectable_difference`.
    """
    fields = Judgement.__dataclass_fields__
    assert "floors_met" in fields and "detectable_difference" in fields
    assert "incidents" in fields, "the threshold is meaningless without the n it was computed at"
    outcome = _judge()
    assert outcome.detectable_difference > 0.0
    assert outcome.incidents > 0


def test_the_renderer_prints_the_threshold_wherever_it_prints_the_floors() -> None:
    """The pairing, asserted where it can actually be broken: the renderer."""
    source = (PKG / "shadow_render.py").read_text(encoding="utf-8")
    if "floors_met" not in source and "floor" not in source:
        return
    assert "detectable" in source or "detection" in source, (
        "shadow_render.py prints a floor evaluation and no detection threshold. §2.5: the two are "
        "emitted together, because 'floors met' must not be readable as 'this evaluation decides'."
    )


# --- the estimator -------------------------------------------------------------------------------


def test_no_incident_spans_two_folds() -> None:
    """**The fold-integrity test**, on a fixture where a row-wise split WOULD split an incident.

    Five incidents, each with several bags. A row-wise split would scatter one incident's bags
    across folds; the grouped assignment cannot, because it never sees a row.
    """
    incidents = [10, 20, 30, 40, 50]
    rows = [(incident, bag) for incident in incidents for bag in range(4)]
    folds = shadow_cv.assign_folds(incidents)
    per_incident: dict[int, set[int]] = {}
    for incident, _bag in rows:
        per_incident.setdefault(incident, set()).add(folds[incident])
    assert all(len(f) == 1 for f in per_incident.values()), (
        f"an incident spans two folds: {per_incident}"
    )
    # CONTROL: the assignment must actually SPREAD incidents, or "no incident spans two folds"
    # would be satisfied by putting everything in fold 0.
    assert len(set(folds.values())) > 1, (
        "every incident landed in one fold; the split is degenerate"
    )


def test_the_fold_assignment_is_deterministic_and_order_independent() -> None:
    assert shadow_cv.assign_folds([3, 1, 2]) == shadow_cv.assign_folds([2, 3, 1])
    assert shadow_cv.assign_folds([1, 2, 3], repeat=0) != shadow_cv.assign_folds(
        [1, 2, 3], repeat=1
    ), "three repetitions must see three different partitions"


def test_the_cluster_bootstrap_is_deterministic() -> None:
    per_cluster = {i: [1.0 if i % 3 else 0.0] for i in range(40)}
    first = shadow_cv.cluster_bootstrap(per_cluster)
    second = shadow_cv.cluster_bootstrap(per_cluster)
    assert first == second


def test_the_bootstrap_resamples_incidents_and_not_observations() -> None:
    """The design effect is the whole point. One incident contributing 100 bags must not narrow the
    interval the way 100 incidents would."""
    concentrated = {0: [1.0] * 100, 1: [0.0] * 100}
    spread = {i: [1.0 if i % 2 else 0.0] for i in range(200)}
    assert (
        shadow_cv.cluster_bootstrap(concentrated).width > shadow_cv.cluster_bootstrap(spread).width
    ), "resampling observations rather than clusters would invert this"


def test_one_cluster_gives_a_degenerate_interval_rather_than_a_narrow_one() -> None:
    """A zero-width interval is obviously unusable; 0.001 would look like precision."""
    interval = shadow_cv.cluster_bootstrap({7: [0.5, 0.5]})
    assert interval.width == 0.0
    assert interval.clusters == 1


def test_the_detection_threshold_falls_as_n_rises_and_matches_the_plan_at_large_n() -> None:
    """§3.1's table, at the two sample sizes this build could reproduce.

    The two it could **not** — 12 and 37 — are DECISIONS #142 and are asserted here at the values
    this build actually measures, so the disagreement is pinned rather than papered over.
    """
    assert round(shadow_cv.minimum_detectable_difference(120), 2) == 0.17  # plan: 0.16
    assert round(shadow_cv.minimum_detectable_difference(300), 2) == 0.10  # plan: 0.10  ✓
    assert round(shadow_cv.minimum_detectable_difference(37), 2) == 0.30  # plan: 0.25  ✗
    assert round(shadow_cv.minimum_detectable_difference(12), 2) == 0.52  # plan: 0.42  ✗
    thresholds = [shadow_cv.minimum_detectable_difference(n) for n in (12, 37, 120, 300)]
    assert all(a > b for a, b in pairwise(thresholds))


def test_the_power_condition_moves_with_the_data() -> None:
    """**CONTROL.** A condition that could never be satisfied would make every verdict
    insufficient for a reason that was really a bug."""
    assert not shadow_cv.power_at(37, 0.05).sufficient
    assert shadow_cv.power_at(3000, 0.05).sufficient


# --- the fourth metric ---------------------------------------------------------------------------


def _bag(
    fid: int,
    incident: int,
    members: list[int],
    marked: list[int],
    hidden: tuple[int, ...] = (),
    coverage: str = "full",
) -> shadow_eval.AssertingBag:
    return shadow_eval.AssertingBag(
        feedback_id=fid,
        incident=incident,
        members=tuple(members),
        marked=frozenset(marked),
        hidden=frozenset(hidden),
        coverage=coverage,
    )


def test_the_rate_is_per_bag_and_never_pooled_over_pairs() -> None:
    """§2.6(d). **The property a build gets wrong.**

    One storm with 250 marks on 501 members contributes 62 750 pairs; a small clean bag contributes
    3. Pooled, the storm's rate would BE the corpus's rate. Averaged over bags, the two count once
    each — so a storm that respects nothing and a bag that respects everything average to 0.5.
    """
    storm = _bag(1, 10, list(range(60)), list(range(30)))
    small = _bag(2, 20, [900, 901, 902, 903], [900])
    # The storm's partition unites everything (respects nothing); the small bag's separates all.
    components = {
        1: dict.fromkeys(range(60), 0),
        2: {900: 0, 901: 1, 902: 2, 903: 3},
    }
    per_incident, diagnostics = shadow_eval.asserted_negative_respected_rate(
        [storm, small], components
    )
    assert per_incident == {10: [0.0], 20: [1.0]}
    assert diagnostics["bags_scored"] == 2
    mean = sum(v for values in per_incident.values() for v in values) / 2
    assert mean == 0.5, "the mean over bags; a pooled rate would be dominated by the storm"
    # And the pooled rate, computed here only to show it differs:
    pooled = diagnostics["pairs_respected"] / diagnostics["observable_pairs"]
    assert pooled < 0.01, f"the pooled rate is {pooled:.4f} — the storm wearing the corpus's name"


def test_bags_with_no_or_empty_coverage_are_excluded_counted_and_reported() -> None:
    """§2.6(c). Such a bag yields an all-singleton partition in which **every** asserted pair is
    satisfied for free — v0.9.0's measured policy-B failure in a new place."""
    for coverage in ("none", "empty"):
        bag = _bag(1, 10, [1, 2, 3], [1], coverage=coverage)
        per_incident, diagnostics = shadow_eval.asserted_negative_respected_rate(
            [bag],
            {1: {1: 0, 2: 1, 3: 2}},
        )
        assert per_incident == {}, f"a {coverage}-coverage bag was scored"
        assert diagnostics["bags_excluded"] == 1
        assert diagnostics["bags_scored"] == 0


def test_a_full_coverage_bag_is_scored() -> None:
    """**CONTROL for the exclusion.** If nothing were ever scored, the exclusion test would pass
    for the wrong reason."""
    bag = _bag(1, 10, [1, 2, 3], [1], coverage="full")
    per_incident, diagnostics = shadow_eval.asserted_negative_respected_rate(
        [bag],
        {1: {1: 0, 2: 1, 3: 2}},
    )
    assert per_incident == {10: [1.0]}
    assert diagnostics["bags_scored"] == 1


def test_only_observable_pairs_are_counted() -> None:
    """§2.4, and the corrected §10 arithmetic: a pair is unobservable if **either** end is hidden.

    `n=4, m=1, h=1, b=0` → observable = `(1-0) * ((4-1) - (1-0))` = **2**, not 3.
    """
    bag = _bag(1, 10, [1, 2, 3, 4], [1], hidden=(4,))
    pairs = bag.observable_pairs()
    assert len(pairs) == 2, f"expected 2 observable pairs, got {pairs}"
    assert (1, 4) not in pairs


def test_a_bag_whose_every_asserted_pair_is_blind_is_excluded_rather_than_scored_one() -> None:
    """Scoring it 1.0 would be the free-perfect-number failure one level down."""
    bag = _bag(1, 10, [1, 2], [1], hidden=(2,))
    per_incident, diagnostics = shadow_eval.asserted_negative_respected_rate(
        [bag],
        {1: {1: 0, 2: 1}},
    )
    assert per_incident == {}
    assert diagnostics["bags_excluded"] == 1


def test_the_champion_and_the_challenger_go_through_one_code_path() -> None:
    """§2.6(d). **A challenger number with no champion number beside it is not a comparison.**

    Asserted structurally: the function takes the partition as an argument, so there is no way to
    give the champion a different scoring rule — the caller can only give it different components.
    """
    bags = [_bag(1, 10, [1, 2, 3], [1])]
    champion = shadow_eval.asserted_negative_respected_rate(bags, {1: dict.fromkeys([1, 2, 3], 0)})
    challenger = shadow_eval.asserted_negative_respected_rate(bags, {1: {1: 0, 2: 1, 3: 1}})
    assert champion[0] == {10: [0.0]}
    assert challenger[0] == {10: [1.0]}


def test_the_four_metrics_are_never_composed() -> None:
    """§5 and §2.6(d): four named quantities, **never combined**. Asserted structurally.

    A composite would reverse the project's standing refusal under a different name, so the guard is
    that no module multiplies, averages or sums two of them together.
    """
    names = {
        "over_merge_rate",
        "under_merge_rate",
        "split_bag_intact_rate",
        "asserted_negative_respected_rate",
    }
    offenders: list[str] = []
    for path in sorted(PKG.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.BinOp):
                continue
            used = {
                sub.attr if isinstance(sub, ast.Attribute) else getattr(sub, "id", "")
                for sub in ast.walk(node)
            }
            if len(used & names) >= 2:
                offenders.append(f"{path.relative_to(PKG)}:{node.lineno}")
    assert not offenders, (
        f"two of the four named metrics are combined in an expression at {offenders}. They are "
        "four quantities for the reason the third exists: over-merge and under-merge have "
        "different costs, this project has not measured them, and a weighted sum would encode an "
        "unmade product decision as a metric."
    )
