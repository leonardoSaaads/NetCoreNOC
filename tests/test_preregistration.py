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
GATE = REPO_ROOT / "docs" / "gates" / "v0.9.0-phase-1.md"

# Recorded in Phase 1, before any model was fitted. See docs/gates/v0.9.0-phase-1.md.
PREREGISTRATION_SHA256 = "bb5bff851588837aa07f21c54b5301f7ada5fec3f8017a5ca4e9d7f7da2cbaef"

# Recorded in Gate 0, before any v0.10.0 code existed, in a commit that changed nothing else.
# See docs/gates/v0.10.0-phase-0.md §4.
PREREGISTRATION_0_10_0_SHA256 = "c03aef0181554c0c71482e57d03677f25964c3a5ac20a7bf1b1d74bff1ba1e01"


@dataclass(frozen=True)
class Plan:
    """One pre-registered plan, its recorded hash, and the gate document that recorded it."""

    release: str
    path: Path
    sha256: str
    gate: Path


PLANS: tuple[Plan, ...] = (
    Plan("v0.9.0", PREREGISTRATION, PREREGISTRATION_SHA256, GATE),
    Plan(
        "v0.10.0",
        REPO_ROOT / "docs" / "analysis" / "PREREGISTRATION-0.10.0.md",
        PREREGISTRATION_0_10_0_SHA256,
        REPO_ROOT / "docs" / "gates" / "v0.10.0-phase-0.md",
    ),
)

_IDS = [plan.release for plan in PLANS]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_both_plans_are_guarded() -> None:
    """Guard the guard.

    `PLANS` is what every parametrised test below iterates over, so a table that quietly lost an
    entry would retire a plan's guard without a single test going red — which is exactly the failure
    mode the parametrisation introduces and the reason this test exists beside it.
    """
    assert len(PLANS) == 2, f"expected both plans to be pinned, found {_IDS}"
    assert _IDS == ["v0.9.0", "v0.10.0"]
    assert len({plan.sha256 for plan in PLANS}) == 2, "two plans cannot share one hash"


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
def test_the_gate_records_the_same_hash(plan: Plan) -> None:
    """Each hash lives in two places on purpose: this constant, and the gate evidence.

    One of them alone could be edited quietly in the same commit as the plan. Two, in different
    files with different reasons to exist, make that an obviously deliberate diff — the same
    two-sided discipline `DEBT_ALLOWLIST` uses (an entry may not be stale *and* may not be new).
    """
    assert plan.gate.is_file(), f"{plan.gate} is missing"
    assert plan.sha256 in plan.gate.read_text(encoding="utf-8"), (
        f"{plan.gate.name} does not record the pre-registration's SHA-256. The gate evidence is "
        "where the hash was first written down; if they disagree, one of them moved."
    )


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
