"""The pre-registration hash guards (v0.9.0 Workstream 0; v0.10.0 Gate 0).

`docs/analysis/PREREGISTRATION-0.9.0.md` was written in Phase 1, **before any model existed**, and
states what will be concluded under every outcome — including the outcome where nothing beats the
champion and the outcome where the data is insufficient. Its SHA-256 was recorded in
`docs/gates/v0.9.0-phase-1.md` at that moment. This test asserts the file still hashes to it, so
**editing the plan after seeing a result turns the suite red.**

**v0.10.0 adds a second plan and does not replace the first.** `PREREGISTRATION-0.10.0.md` governs
the honest judge; `PREREGISTRATION-0.9.0.md` still governs the corpus that release reads, so
**both** are pinned. A guard that moved from one plan to the next would leave every earlier
release's standard of evidence unprotected the moment it stopped being current.

**v0.11.0 adds a third and replaces neither.** `PREREGISTRATION-0.11.0.md` governs promotion; the
floors it refuses against are v0.10.0's §2.2 and the corpus beneath them is v0.9.0's, so a promotion
refused today rests on all three documents at once. That is the literal reason the table grows
rather than rotates: v0.11.0's `INSUFFICIENT_EVIDENCE` cites a floor registered in a plan two
releases old, and a guard that had retired that plan would leave the cited floor editable by the
release that failed to clear it.

**v0.14.0 adds a fourth, and it is the first that governs a corpus this project WRITES.**
`PREREGISTRATION-0.14.0.md` fixes the degeneracy rules for three new model kinds, the attribution
method, the discrimination floor, and — the part with no precedent in the three above — the shape,
proportions, seed, labelling rule and stopping rule of a **simulated network this release
generates**. The other three plans constrain how an existing corpus may be read. This one also
constrains how a corpus may be *made*, and that is precisely the guard that matters most: tuning a
generator until the verdict comes out well is adaptive selection with reality as the knob, and
unlike a model's tuning it would be recorded nowhere. The hash is what makes "the shape was fixed
first" checkable.

**v0.16.0 adds a fifth, and all five govern corpora this release reads or produces.**
`PREREGISTRATION-0.16.0.md` registers what each of five operator gestures asserts and at what
granularity, that the floors do not move because a new channel exists, how operator confidence
enters, and that bag provenance is recorded and not consumed. It is pinned beside the other four
rather than in place of any: this release *feeds* the corpus v0.9.0's plan governs, is judged by
v0.10.0's verdict states, refuses against v0.11.0's floors, and shares a tree with the simulator
v0.14.0's plan constrains. All five are load-bearing at once, which is the literal reason this
table has only ever grown.

This is the discipline the frozen `eval` baseline already applies to an *output*, applied instead to
a *claim*. `tests/test_eval.py` fails when the correlator's behaviour drifts; this fails when the
standard the correlator is being judged against drifts.

## What these guards do NOT prevent, stated plainly

* **They do not stop a future release writing a new plan.** v0.11.0 may pre-register whatever it
  likes; nothing here binds it, and nothing should — a plan that could never be replaced would
  freeze the project's methodology at whatever v0.9.0 happened to understand.
* **They do not detect a plan written loosely enough to accommodate any result.** A document whose
  §7 said "we will conclude that further work is warranted" under every branch would pass this test
  forever. The guard makes a plan **immutable**, not **honest**.
* **They do not prove a plan was written before the results.** They prove it has not changed since
  its hash was recorded. That each hash was recorded in a gate document committed before any of that
  release's code existed is what carries the temporal claim — and that rests on the commit history,
  which is evidence of a different kind and is named here rather than conflated with this. v0.10.0
  strengthens that half specifically: its plan lands in a commit that changes **nothing else** and
  carries the annotated tag `v0.10.0-gate0`, so the moment of ratification is addressable
  independently of any later branch or rebase.

Honesty lives in §7 of each plan, where every outcome is given its conclusion in advance, and in the
fact that the most likely branch — insufficiency — was written by an author who had already measured
that it was the branch the release would land on.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PREREGISTRATION = REPO_ROOT / "docs" / "analysis" / "PREREGISTRATION-0.9.0.md"

# **v0.15.0: the second home moved** (DECISIONS #197, #204). Each hash used to live in the release's
# phase-gate document as well as in this file. Those documents are deleted; the four hashes were
# copied — not recomputed — from `docs/gates/{v0.9.0-phase-1,v0.10.0-phase-0,v0.11.0-phase-0,
# v0.14.0-phase-0}.md` at `3ecf237` into `docs/record.md`, which names the source of each.
#
# **What the two-sided discipline is for is unchanged**, and it is worth restating because moving a
# guard is exactly when its purpose gets lost: one home alone could be edited quietly in the same
# commit as the plan it guards. Two, in different files with different reasons to exist, make that
# an obviously deliberate diff. `docs/record.md` exists to say where the record went; a hash edited
# there to match an edited plan is a change to that file's own claim about history.
SECOND_HOME = REPO_ROOT / "docs" / "record.md"

# Recorded in Phase 1, before any model was fitted. See docs/gates/v0.9.0-phase-1.md.
PREREGISTRATION_SHA256 = "bb5bff851588837aa07f21c54b5301f7ada5fec3f8017a5ca4e9d7f7da2cbaef"

# Recorded in Gate 0, before any v0.10.0 code existed, in a commit that changed nothing else.
# See docs/gates/v0.10.0-phase-0.md §4.
PREREGISTRATION_0_10_0_SHA256 = "c03aef0181554c0c71482e57d03677f25964c3a5ac20a7bf1b1d74bff1ba1e01"

# Recorded in Gate 0, before any v0.11.0 code existed, in a commit that changed nothing else and
# carries the annotated tag `v0.11.0-gate0`. See docs/gates/v0.11.0-phase-0.md §1.
PREREGISTRATION_0_11_0_SHA256 = "e011ee6ad2367d44f2ede14cad7b072df598298f91ecc1a405744358b589d449"

# Recorded in Gate 0, before any v0.14.0 code existed, in a commit that changed nothing else and
# carries the annotated tag `v0.14.0-gate0`. See docs/gates/v0.14.0-phase-0.md §6.
PREREGISTRATION_0_14_0_SHA256 = "5607328a573d9a3c78374e47ba11e6dcff76f07c023b3f2e8174b6feed4d219f"

# Recorded in Gate 0, before any v0.16.0 code existed, in a commit that changed nothing else and
# carries the annotated tag `v0.16.0-gate0`. Its second home is `docs/record.md`, which is where
# the gate documents' half of the two-sided discipline went (DECISIONS #204).
PREREGISTRATION_0_16_0_SHA256 = "81aadc3b7a0695c0a6221a8302fb4e4e591f800a1cceeb89e6a52cca8ecca448"


@dataclass(frozen=True)
class Plan:
    """One pre-registered plan, its recorded hash, and the file that records it a second time."""

    release: str
    path: Path
    sha256: str
    second_home: Path


PLANS: tuple[Plan, ...] = (
    Plan("v0.9.0", PREREGISTRATION, PREREGISTRATION_SHA256, SECOND_HOME),
    Plan(
        "v0.10.0",
        REPO_ROOT / "docs" / "analysis" / "PREREGISTRATION-0.10.0.md",
        PREREGISTRATION_0_10_0_SHA256,
        SECOND_HOME,
    ),
    Plan(
        "v0.11.0",
        REPO_ROOT / "docs" / "analysis" / "PREREGISTRATION-0.11.0.md",
        PREREGISTRATION_0_11_0_SHA256,
        SECOND_HOME,
    ),
    Plan(
        "v0.14.0",
        REPO_ROOT / "docs" / "analysis" / "PREREGISTRATION-0.14.0.md",
        PREREGISTRATION_0_14_0_SHA256,
        SECOND_HOME,
    ),
    Plan(
        "v0.16.0",
        REPO_ROOT / "docs" / "analysis" / "PREREGISTRATION-0.16.0.md",
        PREREGISTRATION_0_16_0_SHA256,
        SECOND_HOME,
    ),
)

_IDS = [plan.release for plan in PLANS]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_every_plan_is_guarded() -> None:
    """Guard the guard.

    `PLANS` is what every parametrised test below iterates over, so a table that quietly lost an
    entry would retire a plan's guard without a single test going red — which is exactly the failure
    mode the parametrisation introduces and the reason this test exists beside it.

    The membership is asserted **exactly**, not by a minimum: `>= 2` would have let v0.11.0 drop
    v0.9.0's guard while adding its own and stay green, which is the shape of the retirement this
    test exists to make visible. Adding a plan is meant to require editing this line.
    """
    assert len(PLANS) == 5, f"expected all five plans to be pinned, found {_IDS}"
    assert _IDS == ["v0.9.0", "v0.10.0", "v0.11.0", "v0.14.0", "v0.16.0"]
    assert len({plan.sha256 for plan in PLANS}) == 5, "no two plans may share one hash"


@pytest.mark.parametrize("plan", PLANS, ids=_IDS)
def test_the_preregistration_exists(plan: Plan) -> None:
    """A guard over a missing file passes vacuously, which is worse than no guard."""
    assert plan.path.is_file(), f"{plan.path} is missing"
    assert plan.path.stat().st_size > 4000, "the plan is too small to state what it must"


@pytest.mark.parametrize("plan", PLANS, ids=_IDS)
def test_the_preregistration_has_not_been_edited(plan: Plan) -> None:
    """**The guard.** The plan is what it was when the hash was recorded."""
    actual = _sha256(plan.path)
    assert actual == plan.sha256, (
        f"{plan.path.relative_to(REPO_ROOT)} has changed since it was ratified.\n"
        f"  recorded: {plan.sha256}\n"
        f"  actual:   {actual}\n\n"
        "A pre-registered analysis plan is not edited after results exist. If the plan was wrong, "
        f"say so in the {plan.release} security review as an opinion for the NEXT release — which "
        "is where §9 of the plan itself directs a disagreement. Changing this constant to make the "
        "suite green is the one thing this test exists to make visible."
    )


@pytest.mark.parametrize("plan", PLANS, ids=_IDS)
def test_the_second_home_records_the_same_hash(plan: Plan) -> None:
    """Each hash lives in two places on purpose: this constant, and `docs/record.md`.

    One of them alone could be edited quietly in the same commit as the plan. Two, in different
    files with different reasons to exist, make that an obviously deliberate diff — the same
    two-sided discipline `DEBT_ALLOWLIST` uses (an entry may not be stale *and* may not be new).
    """
    assert plan.second_home.is_file(), f"{plan.second_home} is missing"
    assert plan.sha256 in plan.second_home.read_text(encoding="utf-8"), (
        f"{plan.second_home.name} does not record {plan.path.name}'s SHA-256. It is the second "
        "home the hash was moved to when the gate documents were deleted (DECISIONS #204); if the "
        "two disagree, one of them moved."
    )


def test_the_second_home_names_where_each_hash_came_from() -> None:
    """The move must not quietly become a re-derivation.

    `docs/record.md`'s hashes were **copied** out of the gate documents at `3ecf237`, not
    recomputed from the plans — a recomputation would agree with whatever the plans say today,
    which is the one thing this guard exists not to trust. So the record names the commit and the
    gate document each hash came from, and that provenance is asserted rather than trusted.
    """
    text = SECOND_HOME.read_text(encoding="utf-8")
    assert "3ecf237" in text, "the record does not name the commit the hashes were copied from"
    for gate in (
        "docs/gates/v0.9.0-phase-1.md",
        "docs/gates/v0.10.0-phase-0.md",
        "docs/gates/v0.11.0-phase-0.md",
        "docs/gates/v0.14.0-phase-0.md",
    ):
        assert gate in text, f"the record does not name {gate} as a hash's origin"


def test_the_plan_states_a_conclusion_for_every_registered_outcome() -> None:
    """§7 is the part that carries the value, so its shape is asserted rather than trusted.

    Not a check that the conclusions are *good* — no test can do that. A check that the section
    exists, that it enumerates outcomes, and that the two branches most likely to be quietly
    omitted are among them: the one where nothing beats the champion, and the one where the data
    is insufficient.
    """
    text = PREREGISTRATION.read_text(encoding="utf-8")
    assert "## 7. What will be concluded under each outcome" in text
    outcomes = [line for line in text.splitlines() if line.startswith("**7.")]
    assert len(outcomes) >= 8, f"§7 enumerates only {len(outcomes)} outcomes"
    lowered = text.lower()
    assert "insufficiency" in lowered, "no branch for an insufficient corpus"
    assert "no better than the champion" in lowered, "no branch for a challenger that does not win"
    assert "no promotion" in lowered


def test_the_0_10_0_plan_keeps_the_third_value_and_the_unspent_seal() -> None:
    """v0.10.0's §7, and the two claims its whole release rests on.

    Deliberately **not** the same assertions as v0.9.0's: the two plans register different things
    and a shared structural check would have to be weak enough to pass both, which is how a guard
    becomes a formality. What is asserted here is what v0.10.0 would be worthless without.

    * `INSUFFICIENT_EVIDENCE` must be present as a **terminal third value**, not an error path. A
      plan that dropped it would let *no evidence* be reported as *evidence of no difference*.
    * The seal must be **constructed and not spent**, at query count **0**. That is the release's
      headline discipline, and it is the one sentence a later release under pressure would quietly
      soften.
    """
    text = (REPO_ROOT / "docs" / "analysis" / "PREREGISTRATION-0.10.0.md").read_text(
        encoding="utf-8"
    )
    assert "## 7. What will be concluded under each outcome" in text
    outcomes = [line for line in text.splitlines() if line.startswith("**7.")]
    assert len(outcomes) == 10, f"§7 enumerates {len(outcomes)} outcomes, not the registered 10"

    assert "INSUFFICIENT_EVIDENCE` is terminal within its release" in text, (
        "the third verdict value is not registered as terminal"
    )
    for value in ("`BETTER`", "`NOT_BETTER`", "`INSUFFICIENT_EVIDENCE`"):
        assert value in text, f"{value} is not a registered verdict value"

    assert "CONSTRUCTS IT AND DOES NOT SPEND IT" in text, "the seal is not registered as unspent"
    assert "query count is 0" in text, "the query count is not registered at zero"
    assert "No promotion" in text, "the plan does not say v0.10.0 promotes nothing"

    # The invariant this release is the first to be tempted by, and the one §1 refuses to relax.
    assert "may be computed against `incumbent_linked`" in text


def test_the_0_11_0_plan_keeps_the_conditional_seal_and_the_two_distinct_refusals() -> None:
    """v0.11.0's §3 and §4, and the two claims a promotion gate would be worthless without.

    Deliberately **not** the same assertions as v0.10.0's, for the reason that test's own docstring
    gives: a structural check weak enough to pass every plan is a formality. What is asserted here
    is what this plan specifically would be worthless without.

    * **The seal policy is CONDITIONAL, and its evaluation order is registered.** "Spend it" buys a
      number that cannot resolve anything on twelve incidents and destroys the one-shot property;
      "do not require it" makes `Trigger.HOLDOUT_UNSPENT` decorative. The order — floors, then
      power, then the seal — is what makes the third option a rule rather than a preference, and it
      is the sentence a later release under pressure would quietly reorder.
    * **The two refusals are registered as opposite claims.** A plan that did not say so would let
      the build collapse them into one condition, which is the failure Part I of the build prompt
      names as the most likely way this release fails.
    * **The query count is predicted at 0 IN ADVANCE.** A plan that reported the count instead of
      predicting it would be describing an outcome, not registering one.
    """
    text = (REPO_ROOT / "docs" / "analysis" / "PREREGISTRATION-0.11.0.md").read_text(
        encoding="utf-8"
    )
    assert "## 6. What will be concluded under each outcome" in text
    outcomes = [line for line in text.splitlines() if line.startswith("**6.")]
    assert len(outcomes) == 9, f"§6 enumerates {len(outcomes)} outcomes, not the registered 9"

    # §3 — the conditional seal, and the order that makes it a rule.
    assert "the seal is read only after the" in text, "the seal policy is not conditional"
    assert (
        "**Evaluation order, registered:** floors first, power condition second, seal last." in text
    ), "the evaluation order is not registered"
    assert "v0.11.0's query count is 0" in text, "the query count is not predicted in advance"

    # §4 — the refusals, which may never be producible by one condition.
    assert "They are opposite claims and must never be producible by the same condition." in text
    for value in ("`INSUFFICIENT_EVIDENCE`", "`NOT_BETTER`", "`BETTER`"):
        assert value in text, f"{value} is not a registered verdict value"

    # §5 — five degeneracy rules, named before any fit existed to choose them to suit.
    assert "## 5. The logistic kind's degeneracy rules" in text
    for rule in (
        "**Finiteness**",
        "**Feature completeness**",
        "**Non-degenerate discrimination**",
        "**Threshold reachability**",
        "**Magnitude sanity**",
    ):
        assert rule in text, f"{rule} is not a registered degeneracy rule"
    # The en dash is written as an escape rather than as a literal: the plan uses U+2013 and the
    # assertion must match its bytes, but a literal here trips ruff's ambiguous-character rule
    # (RUF001) — which exists for good reason and is not worth a blanket `noqa` on this file.
    assert "**Rules 1\u20134 are floors and a deployment may not soften them.**" in text

    # §7 — the two stopping rules this release is most tempted to soften.
    assert (
        "**No promotion is applied without an admin**, and no configuration makes one automatic."
        in text
    )
    assert "may be computed against `incumbent_linked`" in text


def test_the_0_14_0_plan_keeps_the_generator_prohibitions_and_the_stopping_rule() -> None:
    """v0.14.0's §5 and §6, and the two claims its demonstration would be worthless without.

    Deliberately **not** the same assertions as the three above, for the reason those tests' own
    docstrings give: a structural check weak enough to pass every plan is a formality. What is
    asserted here is what makes a verdict on a corpus **this release generated** honest rather than
    manufactured.

    * **The generator's shape is fixed and may not move after a verdict is seen.** This is the one
      hazard with no precedent in the other three plans. A model's tuning is recorded in
      `params_document`; a generator retuned until the verdict came out well would be recorded
      nowhere, which makes it *worse* than tuning the model and invisible rather than merely
      adaptive.
    * **The stopping rule's failing branch is registered as a SUCCESS.** A loop that cannot stop
      without success is a loop that will manufacture one, and "ten increments and the floors are
      still unmet" has to be a shippable outcome *before* the first increment runs, or the tenth
      increment will find a reason to become the eleventh.
    * **The demonstration is registered as not being a claim**, and the ground-truth prohibition of
      §1 is extended to the simulator by name. The DSL knows every event's correct `situation_key`;
      that is a second source of truth of exactly the shape `incumbent_linked` has, and the
      invariant that forbids one forbids the other.
    """
    text = (REPO_ROOT / "docs" / "analysis" / "PREREGISTRATION-0.14.0.md").read_text(
        encoding="utf-8"
    )
    assert "## 8. What will be concluded under each outcome" in text
    outcomes = [line for line in text.splitlines() if line.startswith("**8.")]
    assert len(outcomes) == 7, f"§8 enumerates {len(outcomes)} outcomes, not the registered 7"

    # §1 — the inherited invariant, and its extension to the thing this release builds.
    assert "may be computed against `incumbent_linked`" in text
    assert (
        "**The simulation's ground truth is subject to the same prohibition, for the same reason.**"
        in text
    ), "the ground-truth prohibition is not extended to the simulator"

    # §5.4 — the generator does not move after a verdict is seen.
    assert "are fixed here and are not changed after any verdict is observed" in text
    assert "adaptive selection with the data-generating process" in text

    # §5.3 — the failing branch is a success, registered in advance.
    assert "**The second branch is a successful gate outcome and the report says so.**" in text
    assert "A loop that cannot stop" in text

    # §6 — the demonstration is not a claim, enforced three ways.
    assert "demonstration of the machinery and is never a claim about model quality" in text, (
        "the demonstration/claim distinction is not registered"
    )

    # §2 — the degeneracy rules, named before any fit existed to choose them to suit.
    for rule in ("**T1 ", "**T2 ", "**T3 ", "**T4 ", "**T5 ", "**T6 "):
        assert rule in text, f"tree rule {rule.strip('* ')} is not registered"
    for rule in ("**F1 ", "**F2 ", "**F3 ", "**F4 "):
        assert rule in text, f"forest rule {rule.strip('* ')} is not registered"
    for rule in ("**G1 ", "**G2 ", "**G3 ", "**G4 "):
        assert rule in text, f"boosting rule {rule.strip('* ')} is not registered"

    # §4 — the lower bound is discrimination, and it is hardening-only.
    assert "**The floor is hardening-only.**" in text
    assert "MIN_SCORE_SPREAD = 0.01" in text
    assert "may never be the only lower bound in effect" in text

    # §3 — the attribution method and the contract consequence it forces.
    assert "exact marginal (interventional) Shapley values" in text
    assert "sum(contributions) + base_value == score" in text


def test_the_0_16_0_plan_keeps_the_alarm_lifecycle_prohibition_and_the_gesture_unit() -> None:
    """v0.16.0's §1, §2 and §4 — the three claims a lifecycle release would be worthless without.

    Deliberately **not** the same assertions as the four above, for the reason those tests' own
    docstrings give: a structural check weak enough to pass every plan is a formality. What is
    asserted here is what makes a corpus fed by *operator gestures* honest rather than inflated.

    * **The alarm-lifecycle prohibition is registered, by name, as an extension of
      `incumbent_linked`'s.** A manual clear of a zombie alarm and a self-clear are facts about an
      alarm; letting either produce a link-training row would be a signal about a different question
      doing the work of a measurement about this one. It is the mistake this release is most likely
      to make, and a plan that did not name it would let the build make it silently.
    * **`asserting_bags` counts a GESTURE, not a pair.** One merge of two 200-member situations
      yields 40 000 cross pairs from one human decision. A census that counted pairs would move from
      0 to a triumphant number by changing its unit, which §7.3 registers in advance as the thing to
      suspect before celebrating.
    * **The confidence multiplier, its floor, and the fact that it is never folded into a stored
      weight.** `m(c) = 0.6 + 0.4c` is a convention chosen with no calibration data, and it is
      registered precisely so it cannot be re-chosen later to suit a result.
    """
    text = (REPO_ROOT / "docs" / "analysis" / "PREREGISTRATION-0.16.0.md").read_text(
        encoding="utf-8"
    )
    assert "## 7. What will be concluded under each outcome" in text
    outcomes = [line for line in text.splitlines() if line.startswith("**7.")]
    assert len(outcomes) == 6, f"§7 enumerates {len(outcomes)} outcomes, not the registered 6"

    # §1 — the inherited invariant, and the extension this release exists to respect.
    assert "may be computed against `incumbent_linked`" in text
    assert (
        "**The same prohibition extends, for the same reason, to any signal that is not an "
        "assertion about\n> a grouping.**" in text
    ), "the alarm-lifecycle prohibition is not registered as an extension of the same invariant"
    assert "they produce **no link-training row**" in text

    # §2 — the unit, which is what stops a design effect from looking like evidence.
    assert "`asserting_bags` counts a gesture, not a pair." in text
    assert "increments it\n> by **one**" in text
    assert "never pooled over pairs" in text

    # §4 — the multiplier, its floor, and where it may never be applied.
    assert "m(c) = 0.6 + 0.4" in text
    assert "confidence < 0.50 produces no training row" in text
    assert "folded into a stored `weight`" in text

    # §5 — recorded and not consumed, which is the one a build is most tempted to "improve".
    assert (
        "**Registered: neither enters any model, any metric that decides, or any floor, in "
        "v0.16.0.**" in text
    )

    # §3 and §8 — the floors do not move, whatever the census says.
    assert "**No floor is lowered because a new channel exists.**" in text
    assert "**No floor is lowered.**" in text
