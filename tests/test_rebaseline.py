"""The re-baseline mechanism (v0.17.0, DECISIONS #324).

`make eval` hashes its own stdout and that hash has held at `c2e8a0ce…` since v0.7.0. That is what
makes it useful to a refactor — *did this change correlation behaviour when I did not mean to?* —
and it is **not** a reason the corpus may never grow. Before this release the only way to grow it
was to overwrite `eval/baselines/v0.2.0.json` by hand, which is an edit no reviewer can tell apart
from a behaviour change that was papered over.

So the re-cut is a target, it **refuses to run without a stated reason**, and it appends the digest
it replaced beside the digest it wrote. These are the assertions that make those three sentences
properties of the tree rather than claims in a comment.

**Why the refusal is tested through `main` rather than through `make`**: the check has to fire
*before* the two-minute replay, or a caller who forgets the reason learns so after it. Driving
`main` directly is what pins that ordering — a check moved below `run_all` would make this test slow
rather than red, so the runtime is the assertion.
"""

from __future__ import annotations

import json
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
# `conftest.py` puts `tools/` on the path; the harness lives in `eval/`, and `test_eval.py` reaches
# it the same way. Done here too so this module is importable on its own.
sys.path.insert(0, str(REPO_ROOT / "eval"))

import harness  # noqa: E402


def _doc(**aggregate: Any) -> dict[str, Any]:
    return {"harness_version": 1, "aggregate": aggregate, "scenarios": {}}


# --- the refusal ---------------------------------------------------------------------------


def test_write_baseline_without_a_reason_is_refused(tmp_path: Path) -> None:
    """**The injection this mechanism exists to fail.** No reason, no re-cut."""
    target = tmp_path / "baseline.json"
    with pytest.raises(SystemExit) as exit_info:
        harness.main(["--write-baseline", str(target)])
    assert exit_info.value.code == 2, "argparse refuses with exit 2"
    assert not target.exists(), "a refused re-baseline must write nothing at all"


@pytest.mark.parametrize("blank", ["", "   ", "\t", "\n"])
def test_a_blank_reason_is_not_a_reason(tmp_path: Path, blank: str) -> None:
    """Whitespace satisfies `if not args.reason` in the naive spelling and says nothing."""
    target = tmp_path / "baseline.json"
    with pytest.raises(SystemExit):
        harness.main(["--write-baseline", str(target), "--reason", blank])
    assert not target.exists()


def test_the_refusal_happens_before_the_replay(tmp_path: Path, monkeypatch: Any) -> None:
    """**The ordering, as a test rather than as an intention.**

    The replay takes about two minutes. If the reason check sat below it, a caller who forgot the
    reason would wait out the whole run to be told — and this file would get slower rather than
    redder, which is the failure mode that hides in a timing change nobody measures.
    """
    called = False

    def _explode(*_args: Any, **_kwargs: Any) -> Any:  # pragma: no cover - must never run
        nonlocal called
        called = True
        raise AssertionError("run_all was reached despite a missing --reason")

    monkeypatch.setattr(harness, "run_all", _explode)
    with pytest.raises(SystemExit):
        harness.main(["--write-baseline", str(tmp_path / "b.json")])
    assert not called, "the reason must be checked before the corpus is replayed"


# --- what a re-cut records -----------------------------------------------------------------


def test_a_recut_records_both_digests_the_reason_and_what_moved(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The whole contract of a re-cut, in one pass over the log it appends."""
    log = tmp_path / "REBASELINE-LOG.md"
    monkeypatch.setattr(harness, "REBASELINE_LOG", log)

    target = tmp_path / "baseline.json"
    old = json.dumps(_doc(pairwise_f1=0.5, traps_ingested=10), indent=2, sort_keys=True) + "\n"
    target.write_text(old)
    new = json.dumps(_doc(pairwise_f1=0.9, traps_ingested=10), indent=2, sort_keys=True) + "\n"

    assert harness._rebaseline(target, new, "two PON scenarios joined the corpus") == 0
    assert target.read_text() == new, "the new baseline must actually be written"

    entry = log.read_text(encoding="utf-8")
    assert "two PON scenarios joined the corpus" in entry, "the reason is the point"
    assert sha256(old.encode()).hexdigest() in entry, "the replaced digest"
    assert sha256(new.encode()).hexdigest() in entry, "the written digest"
    # …and the direction. `old -> new`, never the reverse: a log that reads backwards tells the
    # next reader the corpus shrank when it grew.
    assert "pairwise_f1: 0.5 -> 0.9" in entry
    # A metric that did not move must not be listed as one that did.
    assert "traps_ingested" not in entry.split("metrics that moved")[1]


def test_the_log_keeps_every_earlier_entry(tmp_path: Path, monkeypatch: Any) -> None:
    """Append-only. A re-cut that overwrote the log would erase the audit it exists to create."""
    log = tmp_path / "REBASELINE-LOG.md"
    monkeypatch.setattr(harness, "REBASELINE_LOG", log)
    target = tmp_path / "baseline.json"
    target.write_text(json.dumps(_doc(ari=0.1), indent=2, sort_keys=True) + "\n")

    harness._rebaseline(target, json.dumps(_doc(ari=0.2), sort_keys=True) + "\n", "first")
    harness._rebaseline(target, json.dumps(_doc(ari=0.3), sort_keys=True) + "\n", "second")

    entry = log.read_text(encoding="utf-8")
    assert "first" in entry and "second" in entry, "both reasons survive"
    assert entry.index("first") < entry.index("second"), "oldest first, so the log reads forwards"
    assert entry.count("**Reason**") == 2


def test_the_first_cut_says_so_rather_than_inventing_a_digest(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A baseline that never existed has no digest, and the log says so rather than hashing ""."""
    monkeypatch.setattr(harness, "REBASELINE_LOG", tmp_path / "log.md")
    target = tmp_path / "fresh.json"
    harness._rebaseline(target, json.dumps(_doc(ari=1.0), indent=2) + "\n", "first ever cut")
    entry = (tmp_path / "log.md").read_text(encoding="utf-8")
    assert "(none: first cut)" in entry
    assert "none" in entry and "metrics that moved**: none" in entry


# --- the delta writer, on its own ----------------------------------------------------------


def test_moved_metrics_does_not_round_a_real_difference_into_invisibility() -> None:
    """**The defect this was written with and corrected before it shipped.**

    The first version rendered values with `_fmt`, the delta table's 4-decimal formatter. The
    real v0.2.0 baseline holds `pairwise_f1 = 0.999955` against a current `0.999958` — a genuine
    move that `_fmt` prints as `1.0000 -> 1.0000`, so the log would have listed a metric as
    changed on a line showing two identical numbers. `repr` is the honest renderer here.
    """
    moved = harness._moved_metrics(_doc(pairwise_f1=0.999955), _doc(pairwise_f1=0.999958))
    assert moved == ["pairwise_f1: 0.999955 -> 0.999958"]
    assert "1.0000 -> 1.0000" not in moved[0]


def test_moved_metrics_reports_appearance_and_disappearance() -> None:
    """A metric added or removed between baselines is a move, and iterating one side hides half."""
    assert harness._moved_metrics(_doc(a=1), _doc(a=1, b=2)) == ["b: None -> 2"]
    assert harness._moved_metrics(_doc(a=1, b=2), _doc(a=1)) == ["b: 2 -> None"]


def test_moved_metrics_is_empty_when_nothing_moved() -> None:
    """The control: a writer that reported everything would make the entry meaningless."""
    assert harness._moved_metrics(_doc(a=1, b=2.5), _doc(a=1, b=2.5)) == []


# --- the target, and the documentation that names it ---------------------------------------


def test_the_makefile_target_demands_a_reason() -> None:
    """`make eval-baseline` with no `REASON` must fail in make, before Python is reached.

    Asserted against the recipe's text because the alternative is shelling out to `make`, which a
    test suite should not need a build tool to answer.
    """
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    recipe = makefile.split("eval-baseline:", 1)[1].split("\n\n", 1)[0]
    assert "test -n" in recipe and "REASON" in recipe, (
        "the eval-baseline recipe must refuse an empty REASON before invoking the harness"
    )
    assert "--reason" in recipe, "and must pass it through to the harness"


def test_the_rebaseline_log_path_sits_beside_the_baselines_it_describes() -> None:
    """A log a reader has to be told about is a log nobody reads."""
    assert harness.REBASELINE_LOG.parent == harness.BASELINE.parent
    assert harness.REBASELINE_LOG.name.endswith(".md")
