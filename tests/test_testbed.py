"""The lab's boundaries, as tests (v0.17.0, DECISIONS #329-#334).

`testbed/` makes pointing a trap generator at a running appliance a one-line command. Four things
must stay true about that, and none of them is true because nobody has tried:

1. **The product never imports the lab** (prime directive 7). The layer guard in
   `test_layers.py` reasons about `netcorenoc.*` imports and therefore cannot see `testbed`
   at all, so the assertion lives here.
2. **A scenario cannot carry ground truth** (prime directive 5). Not "does not" — *cannot*: the
   loader refuses a `truth` key at any depth, so generated traffic has no label to leak.
3. **The lab cannot reach production's port or volume** (prime directive 8).
4. **A scenario replays reproducibly**, or the lab is a story rather than an experiment.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTBED = REPO_ROOT / "testbed"
PKG = REPO_ROOT / "src" / "netcorenoc"

sys.path.insert(0, str(TESTBED))

from ne import control, scenario  # noqa: E402

# --- 1. the product does not import the lab ------------------------------------------------


#: Every name that would mean "the product is reaching into the lab". Derived where it can be:
#: the top-level modules and packages that actually exist under `testbed/`, plus the directory
#: itself, so adding a module to the lab extends this set without anyone editing it.
def _lab_importables() -> set[str]:
    names = {"testbed"}
    for path in sorted(TESTBED.rglob("*.py")):
        relative = path.relative_to(TESTBED)
        names.add(relative.parts[0].removesuffix(".py"))
    return names - {""}


def test_the_lab_offers_something_to_import() -> None:
    """Guard the guard: an empty set would make the assertion below vacuous."""
    names = _lab_importables()
    assert {"testbed", "ne", "control", "run_local"} <= names, names


def test_no_runtime_module_imports_the_testbed() -> None:
    """**Prime directive 7.** The lab is a consumer of the product, never the reverse.

    `test_layers.py` cannot catch this: its `_imports` helper collects only `netcorenoc.*` names, so
    `import ne.agent` inside `src/` would be invisible to it — the layer table has no row for a
    directory outside the package. Hence a guard of its own, over the AST rather than over text, so
    a name inside a docstring or a comment is not a false positive.
    """
    lab = _lab_importables()
    offenders: list[str] = []
    for path in sorted(PKG.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                offenders += [
                    f"{path.relative_to(PKG)}:{node.lineno}: import {alias.name}"
                    for alias in node.names
                    if alias.name.split(".")[0] in lab
                ]
            elif (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.split(".")[0] in lab
            ):
                offenders.append(
                    f"{path.relative_to(PKG)}:{node.lineno}: from {node.module} import …"
                )
    assert not offenders, (
        "runtime module(s) importing the testbed:\n  "
        + "\n  ".join(offenders)
        + "\n\nThe lab drives the product over UDP and HTTP, exactly as a real network does. An "
        "import would make the shipped wheel depend on a directory MANIFEST.in prunes, which is "
        "F12 with a new cause."
    )


def test_the_testbed_is_pruned_from_the_sdist_and_the_image() -> None:
    """The other half of directive 7: the lab must not ship, so it cannot be imported at runtime."""
    manifest = (REPO_ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    assert "prune testbed" in manifest, (
        "MANIFEST.in must `prune testbed` — the lab is a development tree like tests/, eval/ and "
        "tools/, and an sdist that carried it would ship a trap generator to an operator."
    )
    dockerignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert "testbed/" in dockerignore, ".dockerignore must exclude testbed/ from the build context"


# --- 2. a scenario cannot carry ground truth ----------------------------------------------


def test_every_shipped_scenario_loads() -> None:
    """And there is at least one, or the guards below check nothing."""
    names = scenario.available()
    assert names, "the testbed ships no scenarios"
    for name in names:
        loaded = scenario.load(name)
        assert loaded.hosts, f"{name} has no hosts"
        assert loaded.phases, f"{name} has no phases"


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"truth": {"situation_key": "x"}}, id="top-level truth"),
        pytest.param(
            {"phases": {"cut": {"events": [{"host": "a", "truth": {"is_root": True}}]}}},
            id="truth inside an event, which is where the corpus keeps it",
        ),
        pytest.param({"hosts": [{"id": "a", "expected": "one situation"}]}, id="expected"),
        pytest.param({"phases": {"cut": {"events": [{"situation_key": "fiber"}]}}}, id="bare key"),
        pytest.param({"a": [{"b": [{"is_root": False}]}]}, id="nested three deep"),
    ],
)
def test_a_scenario_carrying_truth_is_refused(tmp_path: Path, payload: dict[str, object]) -> None:
    """**Prime directive 5, demonstrated red.** Generated truth may not exist here at all.

    The second case is the one that matters: `eval/corpus/*.json` keeps `truth` **inside each
    event**, so a top-level-only check would accept a fully labelled corpus file copied into
    `testbed/scenarios/`. That is the mistake this walk is shaped to catch.
    """
    path = tmp_path / "leaky.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(scenario.TruthInScenarioError) as exc:
        scenario.load(path)
    assert "PREREGISTRATION-0.10.0" in str(exc.value), "the refusal must cite why"


def test_a_real_corpus_scenario_is_refused_verbatim() -> None:
    """The injection with no synthetic payload at all: the shipped labelled corpus, as it is.

    A guard tested only against hand-written fixtures is a guard tested against its author's
    imagination. `eval/corpus/fiber_cut.json` is the actual file someone would copy.
    """
    with pytest.raises(scenario.TruthInScenarioError):
        scenario.load(REPO_ROOT / "eval" / "corpus" / "fiber_cut.json")


def test_the_shipped_scenarios_carry_no_truth_and_the_check_is_load_bearing() -> None:
    """The control. If `_reject_truth` were a no-op every test above would still pass by raising
    nothing, so the walk is exercised directly on a payload that must raise and one that must not.
    """
    scenario._reject_truth({"phases": {"cut": {"events": [{"host": "a", "at_s": 0}]}}})
    with pytest.raises(scenario.TruthInScenarioError):
        scenario._reject_truth({"phases": {"cut": {"events": [{"truth": {}}]}}})
    assert set(scenario.FORBIDDEN_KEYS) >= {"truth", "situation_key", "entity_key", "is_root"}


# --- 3. the lab cannot reach production's port or volume -----------------------------------


def _lab_compose() -> str:
    return (TESTBED / "docker-compose.yml").read_text(encoding="utf-8")


def _active_lines(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def test_the_lab_compose_is_a_separate_file_from_the_production_recipe() -> None:
    """Decision 8: the root recipe is hardened and asserted by `test_deploy.py`. The lab is its own
    file so relaxing something here cannot relax it there."""
    assert (TESTBED / "docker-compose.yml").is_file()
    assert (REPO_ROOT / "docker-compose.yml").is_file()
    assert _lab_compose() != (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")


def test_the_lab_states_what_it_relaxes_on_its_first_line() -> None:
    """Decision 8's other half. A lab that relaxes hardening silently teaches the wrong posture."""
    first = _lab_compose().splitlines()[0]
    assert "LAB, NOT THE PRODUCT" in first.upper(), (
        f"the first line does not say what this file is: {first!r}"
    )
    head = "\n".join(_lab_compose().splitlines()[:40])
    for expected in ("relaxes", "read_only", "1162", "8088", "netcorenoc-testbed-data"):
        assert expected in head, f"the preamble does not mention {expected!r}"


def test_the_lab_cannot_bind_a_production_port() -> None:
    """**Prime directive 8.** 162 and 8080 belong to the appliance an operator is running."""
    active = _active_lines(_lab_compose())
    assert '"162:162/udp"' not in active, "the lab may not publish the production trap port"
    assert '"8080:8080"' not in active, "the lab may not publish the production HTTP port"
    assert "127.0.0.1:8088:8080" in active, (
        "the lab publishes 8088 on loopback only: a different port so it cannot collide with "
        "a real appliance, and loopback so it is not reachable from another host"
    )


def test_the_lab_cannot_write_the_production_database() -> None:
    """**Prime directive 8.** A lab that shares the production volume is not a lab."""
    active = _active_lines(_lab_compose())
    assert "netcorenoc-testbed-data" in active
    production = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "netcorenoc-data:/home/netcorenoc" in production, (
        "the production volume, quoted here for contrast"
    )
    for line in active.splitlines():
        stripped = line.strip().lstrip("- ")
        assert not stripped.startswith("netcorenoc-data:"), (
            f"the lab mounts the production volume: {line!r}"
        )


def test_the_lab_keeps_the_hardening_it_did_not_say_it_relaxed() -> None:
    """Relaxing is allowed; relaxing *silently* is not. What the preamble omits must still hold."""
    active = _active_lines(_lab_compose())
    assert active.count("no-new-privileges:true") == 3, "every lab service forbids escalation"
    assert active.count("cap_drop:") == 3 and active.count("- ALL") == 3
    assert "cap_add:" not in active, (
        "the lab adds no capability back — it uses port 1162, so it needs none, which is tighter "
        "than the production recipe's single deliberate CAP_NET_BIND_SERVICE"
    )
    assert "read_only: true" in active, "the appliance under test keeps its immutable rootfs"
    assert "privileged" not in active and "network_mode: host" not in active


def test_the_ne_image_does_not_copy_the_product_in() -> None:
    """Directive 7 again, one layer down: an image that cannot see `src/` cannot import it."""
    # **Active lines only.** The first draft of this test read the whole file and failed on the
    # comment that says "No fastapi, no uvicorn" — a guard matching the prose that explains it
    # rather than the instruction that does it. Same family as F119: read what the file *does*.
    dockerfile = _active_lines((TESTBED / "Dockerfile.ne").read_text(encoding="utf-8"))
    copies = [line for line in dockerfile.splitlines() if line.strip().startswith("COPY ")]
    assert copies, "the NE image copies nothing; this test is asserting nothing"
    for line in copies:
        assert " src" not in line and "src/" not in line, f"the NE image copies the product: {line}"
    installs = [line for line in dockerfile.splitlines() if "pip install" in line]
    assert installs, "the NE image installs nothing; this test is asserting nothing"
    joined = "\n".join(installs)
    assert "pysnmp" in joined, "the NE needs the PDU encoder's dependency"
    for forbidden in ("fastapi", "uvicorn", "aiosqlite"):
        assert forbidden not in joined, f"an NE is a sender; it does not need {forbidden}"


# --- 4. a scenario replays reproducibly ---------------------------------------------------


def test_two_loads_of_a_scenario_are_identical() -> None:
    """A storm whose shape changes between runs is a lab nobody can compare two runs of.

    `per_onu` fan-out is the part that could drift — it spreads events over `spread_s`, and a random
    spread would make every cut a different cut.
    """
    first, second = scenario.load("pon_fiber_cut"), scenario.load("pon_fiber_cut")
    assert first == second
    assert [e.at_s for e in first.phases["cut"].events] == [
        e.at_s for e in second.phases["cut"].events
    ]


def test_the_fan_out_reaches_every_onu_exactly_once_per_event() -> None:
    """The storm is a storm: one trap per ONU, not one trap with an ONU in it."""
    scen = scenario.load("pon_fiber_cut")
    olt_a = scen.host("olt-a")
    los = [
        event
        for event in scen.phases["cut"].events
        if event.host == "olt-a" and event.trap_oid.endswith("2011.6.128.1.1.2.2")
    ]
    assert len(los) == len(olt_a.onus), f"{len(los)} LOS traps for {len(olt_a.onus)} ONUs"
    named = {vb["value"] for event in los for vb in event.varbinds if vb["value"] in olt_a.onus}
    assert named == set(olt_a.onus), "each ONU must appear in exactly one trap"
    assert not any("{onu}" in vb["value"] for event in los for vb in event.varbinds), (
        "an unsubstituted placeholder reached the wire"
    )


def test_the_two_hosts_have_distinct_addresses() -> None:
    """The lab's whole claim. Two hosts at one address is one host."""
    scen = scenario.load("pon_fiber_cut")
    assert len(scen.hosts) >= 2
    assert len(set(scen.addresses)) == len(scen.hosts)


def test_a_scenario_with_two_hosts_at_one_address_is_refused(tmp_path: Path) -> None:
    """…and it is refused rather than merely unlikely."""
    path = tmp_path / "same.json"
    path.write_text(
        json.dumps(
            {
                "name": "same",
                "hosts": [
                    {"id": "a", "address": "127.0.0.9", "enterprise": 1},
                    {"id": "b", "address": "127.0.0.9", "enterprise": 1},
                ],
                "phases": {},
            }
        )
    )
    with pytest.raises(ValueError, match="share an address"):
        scenario.load(path)


def test_the_scenario_carries_a_severity_varbind_the_appliance_cannot_place() -> None:
    """What this release owes v0.17.1 (V.5): a format that can carry a severity, and an appliance
    that says so rather than inventing one.

    The varbind is X.733 perceived severity at RFC 3877's ALARM-MIB arc, and its tokens are exactly
    `known_oids.SEVERITY_VOCAB`. The appliance still renders every alarm **unplaced**, because
    `severity.py` will not confirm a ranking that observed lifetimes have not validated. Measured on
    the lab: `severity_census` returns `unplaced` equal to `active`, with `placed` empty.
    """
    from netcorenoc.ingest import known_oids

    scen = scenario.load("pon_fiber_cut")
    severity_oid = "1.3.6.1.2.1.118.1.2.2.1.4"
    values = {
        vb["value"]
        for phase in scen.phases.values()
        for event in phase.events
        for vb in event.varbinds
        if vb["oid"] == severity_oid
    }
    assert values, f"no scenario event carries the severity varbind {severity_oid}"
    unknown = {v for v in values if known_oids.severity_rank(v) is None}
    assert not unknown, (
        f"the scenario uses severity tokens the bundled vocabulary does not hold: {unknown}. "
        "The point of carrying X.733 values is that they are the vocabulary already shipped."
    )


# --- the live control ---------------------------------------------------------------------


def test_the_phase_survives_a_round_trip_and_bumps_its_sequence(tmp_path: Path) -> None:
    """`cut` twice is two cuts, and an agent must be able to tell. The sequence is how."""
    assert control.read(tmp_path) == control.Phase("steady", 0), "a missing file rests at steady"
    first = control.write("cut", tmp_path)
    assert (first.name, first.seq) == ("cut", 1)
    second = control.write("cut", tmp_path)
    assert (second.name, second.seq) == ("cut", 2), "re-cutting must be visible"
    assert control.read(tmp_path) == second


def test_an_unknown_phase_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown phase"):
        control.write("explode", tmp_path)


def test_a_corrupt_phase_file_rests_at_steady(tmp_path: Path) -> None:
    """The lab's first second must not be its most fragile: a half-written file reads as steady."""
    (tmp_path / "phase").write_text("cut\n")
    (tmp_path / "phase.seq").write_text("not a number\n")
    assert control.read(tmp_path).name == "steady"
