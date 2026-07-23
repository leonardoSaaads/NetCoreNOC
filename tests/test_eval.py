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
