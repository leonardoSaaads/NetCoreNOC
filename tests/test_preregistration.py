"""The pre-registration hash guard (v0.9.0, Workstream 0).

`docs/analysis/PREREGISTRATION-0.9.0.md` was written in Phase 1, **before any model existed**, and
states what will be concluded under every outcome — including the outcome where nothing beats the
champion and the outcome where the data is insufficient. Its SHA-256 was recorded in
`docs/gates/v0.9.0-phase-1.md` at that moment. This test asserts the file still hashes to it, so
**editing the plan after seeing a result turns the suite red.**

This is the discipline the frozen `eval` baseline already applies to an *output*, applied instead to
a *claim*. `tests/test_eval.py` fails when the correlator's behaviour drifts; this fails when the
standard the correlator is being judged against drifts.

## What this guard does NOT prevent, stated plainly

* **It does not stop a future release writing a new plan.** v0.10.0 may pre-register whatever it
  likes; nothing here binds it, and nothing should — a plan that could never be replaced would
  freeze the project's methodology at whatever v0.9.0 happened to understand.
* **It does not detect a plan written loosely enough to accommodate any result.** A document whose
  §7 said "we will conclude that further work is warranted" under every branch would pass this test
  forever. The guard makes the plan **immutable**, not **honest**.
* **It does not prove the plan was written before the results.** It proves the plan has not changed
  since the hash was recorded. That the hash was recorded in Phase 1, in a gate document committed
  before any model code existed, is what carries the temporal claim — and that rests on the commit
  history, which is evidence of a different kind and is named here rather than conflated with this.

Honesty lives in §7 of the plan, where each outcome is given its conclusion in advance, and in the
fact that its most likely branch — insufficiency — was written by an author who had already measured
that it was the branch this release would land on.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PREREGISTRATION = REPO_ROOT / "docs" / "analysis" / "PREREGISTRATION-0.9.0.md"
GATE = REPO_ROOT / "docs" / "gates" / "v0.9.0-phase-1.md"

# Recorded in Phase 1, before any model was fitted. See docs/gates/v0.9.0-phase-1.md.
PREREGISTRATION_SHA256 = "bb5bff851588837aa07f21c54b5301f7ada5fec3f8017a5ca4e9d7f7da2cbaef"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_preregistration_exists() -> None:
    """A guard over a missing file passes vacuously, which is worse than no guard."""
    assert PREREGISTRATION.is_file(), f"{PREREGISTRATION} is missing"
    assert PREREGISTRATION.stat().st_size > 4000, "the plan is too small to state what it must"


def test_the_preregistration_has_not_been_edited() -> None:
    """**The guard.** The plan is what it was when the hash was recorded."""
    actual = _sha256(PREREGISTRATION)
    assert actual == PREREGISTRATION_SHA256, (
        "docs/analysis/PREREGISTRATION-0.9.0.md has changed since Phase 1.\n"
        f"  recorded: {PREREGISTRATION_SHA256}\n"
        f"  actual:   {actual}\n\n"
        "A pre-registered analysis plan is not edited after results exist. If the plan was wrong, "
        "say so in docs/security/SECURITY-REVIEW-0.9.0.md as an opinion for v0.10.0 — which is "
        "where §9 of the plan itself directs a disagreement. Changing this constant to make the "
        "suite green is the one thing this test exists to make visible."
    )


def test_the_gate_records_the_same_hash() -> None:
    """The hash lives in two places on purpose: this constant, and the Phase 1 gate evidence.

    One of them alone could be edited quietly in the same commit as the plan. Two, in different
    files with different reasons to exist, make that an obviously deliberate diff — the same
    two-sided discipline `DEBT_ALLOWLIST` uses (an entry may not be stale *and* may not be new).
    """
    assert GATE.is_file(), f"{GATE} is missing"
    assert PREREGISTRATION_SHA256 in GATE.read_text(encoding="utf-8"), (
        f"{GATE.name} does not record the pre-registration's SHA-256. The gate evidence is where "
        "the hash was first written down; if they disagree, one of them moved."
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
