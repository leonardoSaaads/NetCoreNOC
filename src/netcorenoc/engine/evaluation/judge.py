"""The judge's verdict: **three values, and the type makes the third unavoidable.**

v0.10.0, Workstream 5. `PREREGISTRATION-0.10.0.md` §6 registers it.

> *"The challenger is not better"* and *"this corpus cannot tell"* are **opposite claims**, and a
> binary type collapses them into one.

That collapse is how a v0.11.0 promotion gate would come to treat *no evidence* as *evidence of no
difference*. So the return type is an `enum` with three members and no boolean anywhere: a `bool`
with a comment, an `Optional[bool]`, or a pair of booleans all permit a caller to write
`if not better:` and silently take the insufficient branch as a negative result.

**One honest limit on how structural this is.** Python gives every enum member a default
truthiness, so `if verdict:` is `True` for all three — including `INSUFFICIENT_EVIDENCE`. The type
cannot make that a `TypeError`, and pretending otherwise would be the kind of claim this project
does not make. What the type *does* achieve is that there is no member whose name reads as a
negative result, so a caller must write `is Verdict.BETTER` or `is Verdict.NOT_BETTER` and cannot
obtain either by negating the other. The remaining gap is closed by a test
(`test_no_caller_treats_the_verdict_as_a_boolean`), and it is named here as a test rather than
described as a property of the language.

## `training.Sufficiency` is a different object

`Sufficiency` is two-valued and is about **the corpus**. This is about **the comparison**. They are
not merged and neither wraps the other, because the judge's verdict may be `INSUFFICIENT_EVIDENCE`
for reasons that have nothing to do with the corpus floors — an unspent seal being the one this
release actually returns.

## What v0.11.0 must do, specified here and implemented there

> **Promotion must refuse on `INSUFFICIENT_EVIDENCE` and on `NOT_BETTER` by different code paths
> with different messages**, so no future reader can mistake one for the other.

Specified in `CHAMPION-CHALLENGER-0.11-DRAFT.md`. **v0.10.0 builds no promotion mechanism**, and
must not make one easier by leaving a half-built one behind.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from netcorenoc.engine.evaluation.shadow_cv import Power

__all__ = ["Judgement", "Trigger", "Verdict", "judge"]


class Verdict(Enum):
    """The three values. **Terminal within a release**: no later analysis may convert one."""

    BETTER = "BETTER"
    NOT_BETTER = "NOT_BETTER"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class Trigger(Enum):
    """Every `INSUFFICIENT_EVIDENCE` condition of §6.2, each named so the report can say which.

    An enum rather than free strings because §6.2 registers a **closed** list, and a release that
    could add a reason by writing a new string could also drop one by not writing it. Each is
    individually fired by its own test, on a corpus that fires it **and no other**.
    """

    FLOOR_UNMET = "a §2 floor is unmet"
    HOLDOUT_UNSPENT = "the sealed holdout has not been spent"
    THIN_SPLIT = "fewer than 10 incidents on one side of a reported split"
    POWER = "the minimum detectable difference at this n exceeds the observed difference"
    COVERAGE_EXCLUSIONS = "bags excluded for coverage would change a floor evaluation if included"
    UNKNOWN_POPULATION = "unknown-population rows would change the verdict if included"
    TRUNCATED_LOAD_BEARING = "truncated bags are load-bearing for a floor"
    UNSOUND_CHAINS = "a merge chain contains a cycle or does not terminate"
    PRE_V080_MERGES = "pre-v0.8.0 merges exceed 10% of asserting incidents"

    # §7.6 and §7.7 are NOT_BETTER conditions rather than insufficiency, and they live in
    # `Judgement.notes` rather than here. Mixing them in would let a reader take "the model buries
    # splits" — a measured, adverse result — for "we could not tell", which is the collapse this
    # whole type exists to prevent, one level in.


MIN_INCIDENTS_PER_SIDE = 10
PRE_V080_SHARE = 0.10


@dataclass(frozen=True)
class Judgement:
    """The verdict, why, and the two numbers §2.5 requires be emitted **together**."""

    verdict: Verdict
    triggers: tuple[Trigger, ...] = ()
    notes: tuple[str, ...] = ()
    # §2.5's structural mitigation: a floor evaluation may NEVER be emitted without the detection
    # threshold for the same `n` beside it. Both are carried on one object so a renderer cannot
    # print one and forget the other, and `tests/test_judge.py` asserts the pairing.
    floors_met: bool = False
    detectable_difference: float = 1.0
    incidents: int = 0
    observed_difference: float = 0.0
    query_count: int = 0
    projection: str = "undefined"
    diagnostics: dict[str, int] = field(default_factory=dict)

    @property
    def decisive(self) -> bool:
        """True only for `BETTER` / `NOT_BETTER`. **Never** a stand-in for "the challenger won"."""
        return self.verdict is not Verdict.INSUFFICIENT_EVIDENCE


def judge(
    *,
    floors_met: bool,
    holdout_spent: bool,
    power: Power,
    smallest_side: int,
    coverage_excluded: int,
    unknown_rows: int,
    truncated_load_bearing: bool,
    unsound_chains: int,
    pre_v080_merges: int,
    asserting_incidents: int,
    difference_excludes_zero: bool,
    favours_challenger: bool,
    split_bags_buried: bool = False,
    separates_everything: bool = False,
    query_count: int = 0,
    projection: str = "undefined",
    diagnostics: dict[str, int] | None = None,
) -> Judgement:
    """Apply §6.2. **Every trigger is evaluated; none short-circuits.**

    Collecting all of them rather than returning on the first is deliberate: the report says *which*
    conditions fired, and a build that returned early would make the second reason invisible — so an
    operator fixing the first would find the verdict unchanged and no explanation for it.

    The order of the checks is the plan's order, so a reader can follow §6.2 down the page.
    """
    triggers: list[Trigger] = []
    notes: list[str] = []

    if not floors_met:
        triggers.append(Trigger.FLOOR_UNMET)
    if not holdout_spent:
        triggers.append(Trigger.HOLDOUT_UNSPENT)
    if smallest_side < MIN_INCIDENTS_PER_SIDE:
        triggers.append(Trigger.THIN_SPLIT)
    # THE POWER CONDITION. A trigger, never a floor: no deployment may harden or disable it (§2.5).
    #
    # Read from `Power.sufficient` rather than re-derived here. The first version of this function
    # inlined `abs(observed) <= detectable`, which is a SECOND implementation of the condition —
    # and a second implementation is how the number the report prints and the number the verdict
    # uses come to disagree with nothing going red. The dead-code gate found it, which is a better
    # outcome than a reviewer would have managed.
    if not power.sufficient:
        triggers.append(Trigger.POWER)
    if coverage_excluded > 0:
        triggers.append(Trigger.COVERAGE_EXCLUSIONS)
    if unknown_rows > 0:
        triggers.append(Trigger.UNKNOWN_POPULATION)
    if truncated_load_bearing:
        triggers.append(Trigger.TRUNCATED_LOAD_BEARING)
    # §7.9. Affected incidents are `unknown`, and an identity nobody can resolve is not evidence.
    if unsound_chains > 0:
        triggers.append(Trigger.UNSOUND_CHAINS)
    # §7.10. A pre-v0.8.0 merge LOOKS independent and is not, and no column distinguishes it.
    if asserting_incidents > 0 and pre_v080_merges > PRE_V080_SHARE * asserting_incidents:
        triggers.append(Trigger.PRE_V080_MERGES)

    if triggers:
        return Judgement(
            verdict=Verdict.INSUFFICIENT_EVIDENCE,
            triggers=tuple(triggers),
            notes=tuple(notes),
            floors_met=floors_met,
            detectable_difference=power.detectable,
            incidents=power.incidents,
            observed_difference=power.observed_difference,
            query_count=query_count,
            projection=projection,
            diagnostics=dict(diagnostics or {}),
        )

    # Past this point the corpus could decide something. §7.6 and §7.7 are the two ways a challenger
    # that looks good on the headline rates is nonetheless NOT BETTER, and both are adverse
    # MEASUREMENTS rather than absences of evidence — which is why they are notes on a NOT_BETTER
    # rather than triggers of insufficiency.
    if split_bags_buried:
        notes.append(
            "§7.6: split_bag_intact_rate is high — the model BURIES splits. v0.9.0's measured "
            "policy-B failure repeating, and this number produced the verdict."
        )
    if separates_everything:
        notes.append(
            "§7.7: asserted_negative_respected_rate is near 1.0 while under_merge_rate is also "
            "high — the challenger is separating everything, so it respects every assertion for "
            "free. The two numbers must be read together."
        )

    better = difference_excludes_zero and favours_challenger and not notes
    return Judgement(
        verdict=Verdict.BETTER if better else Verdict.NOT_BETTER,
        notes=tuple(notes),
        floors_met=floors_met,
        detectable_difference=power.detectable,
        incidents=power.incidents,
        observed_difference=power.observed_difference,
        query_count=query_count,
        projection=projection,
        diagnostics=dict(diagnostics or {}),
    )
