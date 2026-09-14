"""The evaluation harness is a gate, so it must itself be trustworthy.

Three properties are asserted here:

1. **Determinism** — two consecutive replays of the whole corpus produce byte-identical
   metrics. A non-deterministic harness cannot gate anything.
2. **Non-regression** — the three headline metrics (``pairwise_f1``, ``ari``,
   ``entity_accuracy``) never fall below the frozen v0.2.0 baseline by more than the
   published tolerance. This is the check ``make qa`` runs on every algorithmic change.
3. **Corpus coverage** — the labelled corpus actually exercises the phenomena this version
   targets: PON proxying, multi-level containment, decoy varbinds, dual incidents, v1 traps.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

EVAL = Path(__file__).resolve().parent.parent / "eval"
sys.path.insert(0, str(EVAL))

import harness  # noqa: E402

BASELINE = json.loads((EVAL / "baselines" / "v0.2.0.json").read_text())


async def test_harness_is_deterministic() -> None:
    first = await harness.run_all()
    second = await harness.run_all()
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


async def test_no_regression_against_frozen_baseline() -> None:
    current = await harness.run_all()
    base = BASELINE["aggregate"]
    for metric in harness.GATE_METRICS:
        cur = current["aggregate"][metric]
        assert cur >= base[metric] - harness.GATE_TOLERANCE, (
            f"{metric} regressed: {cur:.4f} < {base[metric]:.4f} - {harness.GATE_TOLERANCE}"
        )


async def test_cold_mode_reproduces_the_v020_baseline() -> None:
    """Cold/parity mode (no promotion) reproduces the frozen v0.2.0 output byte-for-byte on
    every existing fixture — the mechanical parity gate (prime directive 3). camera_nvr is the
    one legitimate exception: v0.2.0 quarantined its v1 traps, v0.3.0 ingests them (S2)."""
    cold = await harness.run_all(promote=False)
    for name, expected in BASELINE["scenarios"].items():
        if name == "camera_nvr":
            continue
        assert cold["scenarios"][name] == expected, f"cold-mode {name} diverged from v0.2.0"


async def test_learning_mode_lifts_entity_accuracy() -> None:
    """The headline: with promotion on, entity attribution improves dramatically over the
    proxied storms while grouping (pairwise_f1) never regresses."""
    learned = await harness.run_all(promote=True)
    agg = learned["aggregate"]
    assert agg["entity_accuracy"] > 0.3  # up from the frozen baseline's 0.032
    assert agg["pairwise_f1"] >= BASELINE["aggregate"]["pairwise_f1"] - harness.GATE_TOLERANCE
    # The PON dying-gasp storm is the clearest win: ONUs learned from their varbinds.
    assert learned["scenarios"]["pon_dying_gasp"]["entity_accuracy"] > 0.5


def test_baseline_is_frozen_and_complete() -> None:
    """The committed baseline covers every corpus scenario and every reported metric."""
    corpus = {p.stem for p in (EVAL / "corpus").glob("*.json")}
    assert BASELINE["scenarios"].keys() == corpus
    for metric in ("pairwise_f1", "ari", "entity_accuracy", "dedup_ratio", "over_merge_rate"):
        assert metric in BASELINE["aggregate"]


def test_corpus_covers_the_targeted_phenomena() -> None:
    corpus = {p.stem for p in (EVAL / "corpus").glob("*.json")}
    for required in (
        "pon_dying_gasp",  # proxied reporting: entity in varbinds
        "pon_pon_port_down",  # multi-level containment
        "chassis_card_fail",  # proxied reporting, two-level containment
        "decoy_varbinds",  # timestamp / sequence / constant decoys
        "dual_incident",  # over-merge guard
        "camera_nvr",  # SNMPv1 archetype
    ):
        assert required in corpus, f"corpus is missing {required}"

    v1 = json.loads((EVAL / "corpus" / "camera_nvr.json").read_text())
    assert any(int(e.get("version", 2)) == 1 for e in v1["events"]), "camera_nvr needs v1 traps"

    decoy = json.loads((EVAL / "corpus" / "decoy_varbinds.json").read_text())
    oids = {vb["oid"] for e in decoy["events"] for vb in e["varbinds"]}
    assert len(oids) >= 4, "decoy scenario must present several competing varbinds"


def test_baseline_shows_the_v020_weaknesses_to_be_improved() -> None:
    """Sanity: the frozen baseline records the failures v0.3.0 exists to fix — proxied
    entity attribution near zero, and the instance heuristic leaving dedup poor."""
    agg = BASELINE["aggregate"]
    assert agg["entity_accuracy"] < 0.2, "baseline entity attribution should be poor (proxying)"
    assert agg["pairwise_f1"] > 0.9, "baseline grouping is already good; v0.3.0 must not regress it"


@pytest.mark.parametrize("scenario", ["pon_dying_gasp", "olt_storm", "chassis_card_fail"])
def test_proxied_scenarios_attribute_to_the_ne_in_the_baseline(scenario: str) -> None:
    """Under v0.2.0 the proxied storms attribute every alarm to the reporting NE, so their
    per-scenario entity accuracy is far below one — the gap v0.3.0 closes."""
    assert BASELINE["scenarios"][scenario]["entity_accuracy"] < 0.5


# --- what the corpus is, pinned (v0.17.0, DECISIONS #327) ----------------------------------------
#
# `make corpus` regenerates `eval/corpus/*.json` from `eval/corpus_gen.py`, and `make eval` gates on
# what that directory holds. Nothing compared the two, so a regeneration that changed a scenario was
# an invisible edit: the frozen baseline would be re-measured against a different corpus and the
# `make eval` hash would move for a reason nobody had to state.
#
# **This is the instrument the eval hash is not.** Measured in v0.17.0's Phase 0, `make eval`'s
# stdout hash is insensitive to a 50 % perturbation of the class-affinity term, because no link on
# this corpus crossed the threshold differently — a snapshot of *aggregate metrics* can only see a
# change that moves one. A digest over the corpus bytes is insensitive to nothing.
#
# **When a release legitimately grows the corpus** it updates these three constants in the same
# commit, beside the `make eval-baseline REASON="…"` entry that re-cuts the baseline (#324) — the
# reviewable-line-in-a-diff discipline `TRAP_PATH_HASHES`, `UI_HASHES` and `SRC_TREE_DIGEST` use.

#: SHA-256 over `eval/corpus/*.json`, path hashed alongside contents, ordered by the POSIX string:
#:
#:     for each path in sorted order:  update(path); update(b"\0"); update(sha256(contents))
#:
#: The path is in the digest, so **renaming a scenario file moves it** even when every byte of every
#: file is unchanged. That matters here more than it does for `src/`: `harness.run_all` enumerates
#: this directory with `sorted(CORPUS_DIR.glob("*.json"))`, so the filenames are the replay order.
CORPUS_DIGEST = "85f73f07eb7d9878a7c6d4801a4f3df622b4d6e953bf87e95e989aeda3cfef9a"
CORPUS_SCENARIOS = 10
CORPUS_EVENTS = 3159


def _corpus_digest() -> tuple[str, int, int]:
    """`(digest, scenario count, total event count)` over the labelled corpus."""
    import hashlib

    corpus = EVAL / "corpus"
    paths = sorted(p.relative_to(corpus).as_posix() for p in corpus.glob("*.json"))
    digest = hashlib.sha256()
    events = 0
    for relative in paths:
        digest.update(relative.encode())
        digest.update(b"\0")
        raw = (corpus / relative).read_bytes()
        digest.update(hashlib.sha256(raw).digest())
        events += len(json.loads(raw).get("events", []))
    return digest.hexdigest(), len(paths), events


def test_the_corpus_is_byte_identical_to_its_pin() -> None:
    """**The gate.** `make corpus` cannot change the gate's subject without a reviewable line.

    A red here means `eval/corpus/` moved. If that was intended — a scenario added, a label
    corrected — update the three constants above **and** re-cut the baseline with
    `make eval-baseline REASON="…"`, which records both digests and what moved (#324). If it was not
    intended, someone ran `make corpus` against a generator that no longer reproduces the shipped
    corpus, and that is a defect in the generator rather than a new corpus.
    """
    digest, scenarios, events = _corpus_digest()
    assert scenarios == CORPUS_SCENARIOS, (
        f"{scenarios} corpus scenarios; the pin records {CORPUS_SCENARIOS}. A scenario was added "
        "or removed, which changes what the frozen baseline is a baseline OF."
    )
    assert events == CORPUS_EVENTS, (
        f"{events} corpus events; the pin records {CORPUS_EVENTS}. The scenario count is the "
        "same, so a scenario's contents moved — which the count alone would not have shown."
    )
    assert digest == CORPUS_DIGEST, (
        f"the corpus moved.\n  pinned: {CORPUS_DIGEST}\n  actual: {digest}\n\n"
        "Both the count and the event total matched, so this is a change inside a scenario that "
        "preserved how many events it holds — a relabelled root, a changed varbind, a different "
        "source address. Exactly the edit an aggregate-metrics snapshot can miss."
    )


def test_the_corpus_digest_is_sensitive_to_a_single_byte(tmp_path: Path) -> None:
    """The control. A digest that ignored contents, or ignored paths, would pin nothing.

    Both halves are asserted, because they fail differently: a digest over contents alone would let
    two scenarios swap filenames — and filenames are the replay order, since `run_all` globs this
    directory — while a digest over paths alone would let any scenario's body be rewritten.
    """
    import hashlib

    def digest(files: dict[str, bytes]) -> str:
        out = hashlib.sha256()
        for name in sorted(files):
            out.update(name.encode())
            out.update(b"\0")
            out.update(hashlib.sha256(files[name]).digest())
        return out.hexdigest()

    base = {"a.json": b'{"name": "a"}', "b.json": b'{"name": "b"}'}
    assert digest(base) != digest({**base, "a.json": b'{"name": "a "}'}), "contents are not in it"
    assert digest(base) != digest({"a2.json": base["a.json"], "b.json": base["b.json"]}), (
        "paths are not in the digest, so two scenarios could swap names and change the replay order"
    )


def test_the_corpus_pin_names_the_scenarios_the_harness_will_replay() -> None:
    """The pin covers the set `harness.run_all` actually enumerates, not a directory beside it.

    `run_all` uses `sorted(CORPUS_DIR.glob("*.json"))`. If the two ever read different places the
    pin would be guarding a corpus nobody replays, which is the F112 shape — a rule whose scope is
    narrower than the thing it claims to cover.
    """
    replayed = sorted(p.name for p in harness.CORPUS_DIR.glob("*.json"))
    pinned = sorted(p.name for p in (EVAL / "corpus").glob("*.json"))
    assert replayed == pinned, f"the harness replays {replayed}; the pin covers {pinned}"
    assert len(replayed) == CORPUS_SCENARIOS
