"""The simulated network (v0.14.0, Phase 6).

`PREREGISTRATION-0.14.0.md` §5.1 fixes the generator's shape, its proportions and its seed **before
any corpus was generated and any verdict was seen**, and §5.4 forbids changing any of them
afterwards. This file is what makes that enforceable rather than promised: the proportions are
asserted against the plan's own table, the seed is asserted to be the registered one, and the
generator is asserted byte-identical across two processes.

**The separation test is the one that matters most.** §1 extends the `incumbent_linked` prohibition
to the simulator: *"A label the machine produced does not judge the machine, whether the machine is
the champion or the generator."* The generator knows every event's correct `situation_key`. That is
a second source of truth of exactly the shape `incumbent_linked` has, and the invariant that forbids
one forbids the other — so it is asserted by parsing the runtime package, not by reading it.
"""

from __future__ import annotations

import ast
import json
import subprocess  # nosec B404 - runs this interpreter on a literal script, no shell, no input
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PKG = REPO_ROOT / "src" / "netcorenoc"
sys.path.insert(0, str(REPO_ROOT / "eval"))

from simulation.generator import (  # noqa: E402
    INCREMENT_INCIDENTS,
    SEED,
    SHAPE_QUOTA,
    SHAPES,
    generate,
    shape_of,
)

# The plan's §5.1 table, transcribed as percentages. **Not imported from the generator** — a test
# that read the quota it is checking would agree with itself whatever the quota became.
REGISTERED_SHARES = {
    "independent_overlap": 30,
    "spread_beyond_tau": 20,
    "storm_concealing": 15,
    "flap_during_incident": 15,
    "merge_chain": 10,
    "quiet_noise": 10,
}


def test_the_proportions_are_the_registered_ones() -> None:
    """§5.1's table, exactly, and exact in **every** increment rather than on average.

    A sampled proportion would be right in expectation and wrong in each increment, and since §5.4
    forbids changing the shape after a verdict is seen, "right on average" would be a shape nobody
    could reproduce.
    """
    quota = dict(SHAPE_QUOTA)
    assert set(quota) == set(REGISTERED_SHARES)
    for shape, share in REGISTERED_SHARES.items():
        assert quota[shape] * 100 == share * INCREMENT_INCIDENTS, shape
    assert sum(quota.values()) == INCREMENT_INCIDENTS


def test_every_increment_holds_the_quota_exactly() -> None:
    for increment in range(5):
        first = increment * INCREMENT_INCIDENTS
        counts = dict.fromkeys(SHAPES, 0)
        for offset in range(INCREMENT_INCIDENTS):
            counts[shape_of(first + offset)] += 1
        assert counts == dict(SHAPE_QUOTA), increment


def test_the_seed_is_the_registered_one() -> None:
    """§5.1: *"a fixed seed recorded in the gate"*. It is recorded in two places on purpose."""
    assert SEED == 20_140_000
    gate = REPO_ROOT / "docs" / "gates" / "v0.14.0-phase-6.md"
    assert gate.is_file(), "the gate that records the seed is missing"
    assert str(SEED) in gate.read_text(encoding="utf-8"), (
        "the gate does not record the generator's seed; §5.1 requires it to"
    )


def test_the_generator_is_byte_identical_across_two_processes() -> None:
    """Across processes, for `test_challenger.py`'s reason: hash randomisation, dict ordering and
    import order are per-process and a within-process repeat sees none of them."""
    script = (
        "import json,sys;"
        f"sys.path.insert(0, {str(REPO_ROOT / 'eval')!r});"
        "from simulation.generator import generate;"
        "print(json.dumps([generate(0), generate(3)], sort_keys=True))"
    )
    out = subprocess.run(  # nosec B603 - this interpreter, a literal script, no shell
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    assert json.loads(out.stdout) == json.loads(
        json.dumps([generate(0), generate(3)], sort_keys=True)
    )


def test_the_simulation_is_not_inside_the_frozen_corpus_directory() -> None:
    """Gate 0 premise 5: `eval/harness.py` globs `eval/corpus/*.json`, so anything written there
    moves the frozen `eval` hash. The simulation is a **sibling** and writes no JSON at all."""
    corpus = REPO_ROOT / "eval" / "corpus"
    simulation = REPO_ROOT / "eval" / "simulation"
    assert simulation.is_dir() and corpus.is_dir()
    assert corpus not in simulation.parents and simulation != corpus
    assert sorted(p.name for p in corpus.glob("*.json")) == [
        "background_noise.json",
        "camera_nvr.json",
        "chassis_card_fail.json",
        "decoy_varbinds.json",
        "dual_incident.json",
        "fiber_cut.json",
        "flapping_noise.json",
        "olt_storm.json",
        "pon_dying_gasp.json",
        "pon_pon_port_down.json",
    ], "eval/corpus/ has gained or lost a scenario; the frozen eval hash has moved"


def test_the_generated_network_carries_ground_truth_on_every_event() -> None:
    """The DSL's own contract, and the thing §1 then forbids the gate from reading."""
    document = generate(0)
    assert document["events"]
    for event in document["events"]:
        assert "truth" in event
        assert event["truth"]["situation_key"]
        assert event["truth"]["entity_key"]


# -- the separation, asserted by parsing rather than by promising -------------------------------


def test_no_runtime_module_can_reach_the_simulator() -> None:
    """**§1's prohibition, made structural.**

    The generator's `situation_key` is a second source of truth of exactly the shape
    `incumbent_linked` has. The invariant that forbids one forbids the other, so no module under
    `src/netcorenoc/` may import the simulation package at all — not the promotion gate, not the
    judge, not the estimator, not anything they call.

    Checked by parsing the tree, because reading it is what let `model_version.py` reach the
    challenger for three releases without anyone noticing (F57).
    """
    offenders: list[str] = []
    for path in sorted(PKG.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""] + [alias.name for alias in node.names]
            for name in names:
                if "simulation" in name or "scenario_dsl" in name:
                    offenders.append(f"{path.relative_to(PKG)} imports {name!r}")
    assert not offenders, (
        "a runtime module can reach the simulator:\n  "
        + "\n  ".join(offenders)
        + "\n\nThe generator knows the correct situation_key of every event. A label the machine "
        "produced does not judge the machine (PREREGISTRATION-0.14.0.md §1)."
    )


@pytest.mark.parametrize("word", ["situation_key", "entity_key", "is_root"])
def test_no_promotion_path_module_mentions_a_ground_truth_field(word: str) -> None:
    """The belt to the previous test's braces: the four modules the gate actually reads must not
    even name the simulator's truth fields, so a copy-paste of one would be a failing diff.

    The three names are the DSL's own `truth` keys. The bare word *truth* is deliberately **not**
    among them: `judge.py` uses "truthiness" in a paragraph about `Verdict` being an enum, and a
    substring check that fired on that would be a guard nobody could keep green — the shape of
    F51's `_SKIP_DIRS`, one release later.
    """
    for name in ("promotion.py", "judge.py", "shadow_cv.py", "evaluation_folds.py"):
        source = (PKG / name).read_text(encoding="utf-8")
        assert word not in source, f"{name} mentions {word!r}"
