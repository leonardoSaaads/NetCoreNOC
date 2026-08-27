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
    """§5.1: *"a fixed seed recorded in the gate"*. It is recorded in two places on purpose.

    **v0.15.0: the second place moved** (DECISIONS #197, #204). The gate documents are deleted; the
    number was copied from `docs/gates/v0.14.0-phase-6.md` §1 at `3ecf237` into `docs/record.md`,
    which is now the second file. What the guard is for is unchanged: one home alone could be edited
    quietly in the same commit as the generator, two in different files make that a deliberate diff.
    """
    assert SEED == 20_140_000
    record = REPO_ROOT / "docs" / "record.md"
    assert record.is_file(), "the file that records the seed is missing"
    assert str(SEED) in record.read_text(encoding="utf-8"), (
        "docs/record.md does not record the generator's seed; §5.1 requires a second home for it"
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


# -- the loop's discipline (v0.14.0, Phase 7) ----------------------------------------------------
#
# The loop stopped at ten increments with the floors unmet, which `PREREGISTRATION-0.14.0.md` §5.3
# registers **in advance** as one of two successful outcomes and §8.3 names. The hazard in that
# branch is not the outcome; it is that a later edit quietly turns the ceiling into a target, or
# softens a floor, or lets the report call the shortfall something other than a shortfall. Each
# test below pins one of those.


def test_the_increment_ceiling_is_the_registered_ten() -> None:
    """§5.3: *"ten increments have been generated without every floor being met"*.

    A constant rather than a parameter, and asserted here rather than trusted, because a loop whose
    stopping rule can be raised from the command line has no stopping rule. The plan's own sentence
    is the reason: **a loop that cannot stop without success is a loop that will manufacture one.**
    """
    from simulation import drive

    assert drive.MAX_INCREMENTS == 10
    assert "--increments" not in Path(drive.__file__).read_text(encoding="utf-8")


def test_the_loop_reads_the_servers_floors_and_never_its_own() -> None:
    """The floors the loop reports against are **the ones the verdict is decided by**.

    `routes_promotion._derived_inputs` computes `floors_met` from `ASSERTING_BAGS_FLOOR` and
    `ASSERTING_INCIDENTS_FLOOR`. A driver carrying its own copy of 50 and 30 would keep reporting
    success after the server's had moved, which is the class of defect `census()`'s own docstring
    calls "a query written for a report" one level down.
    """
    from netcorenoc.api import routes_promotion
    from simulation import drive

    assert drive.FLOORS == {
        "asserting_bags": routes_promotion.ASSERTING_BAGS_FLOOR,
        "asserting_incidents": routes_promotion.ASSERTING_INCIDENTS_FLOOR,
    }
    assert drive.FLOORS == {"asserting_bags": 50, "asserting_incidents": 30}


def test_the_shortfall_is_per_floor_and_never_a_single_number() -> None:
    """§5.3: *"After each increment it computes the census and reports, **per unmet floor**, the
    shortfall."* One number for two floors would say a demonstration was 40 short of something.
    """
    from simulation import drive

    assert drive.shortfall({"asserting_bags": 10, "asserting_incidents": 10}) == {
        "asserting_bags": 40,
        "asserting_incidents": 20,
    }
    assert drive.shortfall({"asserting_bags": 50, "asserting_incidents": 30}) == {}
    # A floor that is exceeded is met, not negative.
    assert drive.shortfall({"asserting_bags": 99, "asserting_incidents": 99}) == {}


def test_the_labelling_rule_is_the_one_registered_before_the_verdict() -> None:
    """§5.2's decision function, pinned as a property rather than as a call.

    The rule is *one truth key -> confirm; two or more -> split, marking the minority key*. §5.4
    forbids changing it now that a verdict has been observed, so this test exists to make a change
    a failing diff rather than a quiet edit. It reads the source because the rule is a branch and
    not a value: there is no constant to compare against.
    """
    source = (REPO_ROOT / "eval" / "simulation" / "labelling.py").read_text(encoding="utf-8")
    assert 'verdict = "split"' in source
    assert 'verdict = "confirm"' in source
    assert "min(by_key.values(), key=lambda ids: (len(ids), ids[0]))" in source
    assert "if len(by_key) > 1:" in source


def test_the_three_principals_are_distinct_and_round_robin() -> None:
    """§5.2: three distinct principals, **none contributing more than 60 % of bags.**

    Round-robin over three gives 33.3 % each whatever the corpus size — the operator-concentration
    floor of `PREREGISTRATION-0.9.0.md` satisfied by construction rather than by luck. Two
    principals would give 50 % each and still pass the ceiling, which is why the count is asserted
    and not just the ceiling.
    """
    from simulation.labelling import PRINCIPALS

    assert len(PRINCIPALS) == 3
    assert len(set(PRINCIPALS)) == 3
    assert 100.0 / len(PRINCIPALS) <= 60.0


def test_the_diagnosis_supports_no_conclusion_and_says_so() -> None:
    """§9: an additional observation *"may never support a conclusion in §8"*.

    `diagnose.py` measures why the census reads what it reads. Nothing it computes may be read by
    the loop's stopping decision, so `shortfall` and `census` must not mention it — asserted by
    parsing `drive.py`, because the two live in one file and an accidental reference would be one
    line.
    """
    from simulation import drive

    tree = ast.parse(Path(drive.__file__).read_text(encoding="utf-8"))
    deciding = {"shortfall", "census"}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name in deciding:
            body = ast.dump(node)
            assert "diagnose" not in body, f"{node.name}() reads the diagnosis"


def test_the_transport_rewrite_is_a_bijection_on_this_corpus() -> None:
    """The demonstration host cannot bind `10.0.0.0/8`, so the harness sends from `127.a.b.c`.

    It is a change to the **transport** and not to the corpus, and that claim is only true if the
    mapping is injective over the addresses the generator actually produces — otherwise two devices
    would arrive as one NE and the appliance would see a different network. Asserted over every
    device in an increment rather than argued from the second octet's range.
    """
    from simulation.appliance import from_wire, to_wire
    from simulation.generator import devices_of

    devices = sorted(devices_of(0))
    assert len(devices) > 1
    wire = [to_wire(ip) for ip in devices]
    assert len(set(wire)) == len(set(devices)), "the rewrite collapsed two devices onto one address"
    assert all(from_wire(to_wire(ip)) == ip for ip in devices)
    assert all(address.startswith("127.") for address in wire)
