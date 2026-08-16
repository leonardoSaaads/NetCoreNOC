"""The instrument, tested before anything is measured with it (v0.12.0, Workstream 1).

`ui/app.js` is 52 738 bytes and until this release **no test executed it**. Phase 0 demonstrated
that by hand: an `app.js` no JavaScript engine can parse left all 1302 tests green. This module is
the harness that would have failed, and this file tests the harness rather than the UI — the five
captured invariants live in `test_ui_invariants.py`.

**The failure this file exists to prevent is a harness that skips.** A suite reporting "green" over
zero executed DOM tests is worse than no harness, because it reads like coverage. So:

* the skip path is itself driven and asserted to *report* (§1), rather than being assumed reachable;
* every scenario must return proof that `app.js` evaluated, and `domdriver` raises without it;
* determinism is checked across two runs **and two processes**, because a harness whose output
  moves cannot support a byte-comparison of anything;
* the harness's own DOM is conformance-tested, because a DOM that got `append` or `removeChild`
  wrong would make an invariant pass for a property of the instrument.

**What this file does not cover**: anything about the UI's behaviour, appearance or correctness. It
asserts that the instrument works. A green here with a red in `test_ui_invariants.py` is the
expected shape when the UI is wrong; a green here with nothing in that file would be an instrument
measuring nothing.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

import domdriver

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS = REPO_ROOT / "tests" / "domharness"

AVAILABLE = domdriver.availability()
requires_node = pytest.mark.skipif(not AVAILABLE.ok, reason=AVAILABLE.skip_message)


def dom_test[F: Callable[..., Any]](fn: F) -> F:
    """Mark a test as one that actually drives Node, and skip it loudly when Node is absent.

    The `dom` marker is what lets a gate quote **executed** DOM tests rather than collected ones:
    `make dom` runs exactly this set, and its "N passed" / "N skipped" line is the number the gate
    records. Two decorators rather than one composite so the skip reason stays attached.
    """
    return cast("F", pytest.mark.dom(requires_node(fn)))


# --- §1 the skip path, driven ------------------------------------------------------------------
#
# These four run everywhere, Node or no Node. They are the reason a skipped suite cannot pass as a
# green one.


def test_availability_reports_a_reason_naming_the_requirement_when_node_is_absent() -> None:
    """The skip path, **executed**, not assumed.

    `availability()` is a pure function of PATH precisely so this test can drive it into the
    branch a machine without Node would take. Pointing it at an empty PATH is the whole
    reproduction: no `node`, therefore not `ok`, therefore a reason that names the requirement so
    a reader of the skip line knows what to install.

    What this does NOT prove: that the message is *seen*. A skip nobody reads is a skip that did
    not work, which is why the gate documents quote executions rather than trusting this.
    """
    absent = domdriver.availability(path="")
    assert absent.ok is False
    assert "Node.js" in absent.reason and str(domdriver.NODE_MIN_MAJOR) in absent.reason
    assert "TEST dependency" in absent.reason, (
        "the skip message must say Node is a test dependency, or a reader will conclude the "
        "product gained a runtime requirement"
    )


def test_availability_refuses_a_node_below_the_floor(tmp_path: Path) -> None:
    """A too-old interpreter is refused **with its version named**, not silently accepted.

    The stand-in reports `v18.20.4`, an interpreter that really existed and really is below the
    floor. Accepting it would be the worse failure: the harness would run and produce results on a
    runtime the rule does not cover.
    """
    fake = tmp_path / "node"
    fake.write_text("#!/bin/sh\necho v18.20.4\n", encoding="utf-8")
    fake.chmod(0o755)
    verdict = domdriver.availability(node=str(fake))
    assert verdict.ok is False
    assert verdict.version == "v18.20.4"
    assert ">= 22" in verdict.reason


def test_availability_accepts_the_floor_itself(tmp_path: Path) -> None:
    """The control for the test above: the refusal must be about the version, not about the
    stand-in being a shell script. A `v22.0.0` reply from the same mechanism is accepted."""
    fake = tmp_path / "node"
    fake.write_text("#!/bin/sh\necho v22.0.0\n", encoding="utf-8")
    fake.chmod(0o755)
    verdict = domdriver.availability(node=str(fake))
    assert verdict.ok is True, verdict.reason
    assert verdict.version == "v22.0.0"


def test_the_node_requirement_is_a_rule_not_a_pinned_version() -> None:
    """ADR #166. A harness pinned to an exact version is a harness the build environment cannot
    run: this repository's CI, `flake.nix` and the maintainer's machine are three different
    runtimes. The floor is a major-version minimum and nothing in the harness may pin beyond it.
    """
    sources = [p.read_text(encoding="utf-8") for p in HARNESS.glob("*.mjs")]
    sources.append((REPO_ROOT / "tests" / "domdriver.py").read_text(encoding="utf-8"))
    for source in sources:
        assert "engines" not in source, "an exact engine pin appeared in the harness"
    assert domdriver.NODE_MIN_MAJOR == 22


# --- §2 the harness runs, and proves it ran ----------------------------------------------------


@dom_test
def test_the_harness_evaluates_the_ui_module_graph_and_can_prove_it() -> None:
    """**The assertion this whole release is built to make possible**, one release on.

    `proof.escaped` is the output of the UI's own `esc()` — but v0.13.0's UI is an ES module
    graph, so it is no longer read off a global. It is called on the **evaluated namespace of
    `app/dom.js`**, the same module instance the running console imported. That cannot be produced
    without linking and evaluating the real graph, which makes the proof strictly stronger than
    v0.12.0's: it additionally proves the graph linked.

    `modulesEvaluated` counts the whole graph, **including the two vendored framework assets whose
    bytes `CHECKSUMS.txt` pins**. The framework a browser would run is the framework this ran.

    What this does NOT prove: that the console is *correct*, or that the DOM it ran against behaves
    like a browser. Those are `test_ui_invariants.py` and `§3` respectively.
    """
    result = domdriver.run_scenario("boot", {"routes": _minimal_routes()})
    assert result["proof"]["escaped"] == domdriver.ESC_PROOF
    # The entry point, the infrastructure modules, seventeen views, and the two vendored assets.
    assert result["proof"]["modulesEvaluated"] >= 30, result["proof"]
    assert result["proof"]["mounted"] is True, "the console rendered nothing into #root"
    assert result["appVisible"] is True, "the console did not reach its authenticated view"


def test_a_scenario_that_did_not_execute_app_js_is_refused_rather_than_returned() -> None:
    """The anti-vacuity rule, as code: a result without proof raises instead of passing.

    Driven through `_require_proof` directly, because the only way to obtain a proofless result
    from the real runner would be to break the runner — and a test that needs the thing under test
    broken in order to run is not a test. It carries no `dom` marker for the same reason it exists:
    the anti-vacuity rule has to hold on a machine with no Node at all.
    """
    with pytest.raises(domdriver.HarnessError, match=r"did not execute ui/app\.js"):
        domdriver._require_proof("boot", {"proof": {"escaped": "", "functionsDefined": 0}})
    with pytest.raises(domdriver.HarnessError, match="no execution proof"):
        domdriver._require_proof("boot", {})


# --- §3 the harness's own DOM ------------------------------------------------------------------


@dom_test
def test_the_harness_dom_passes_its_own_conformance_suite() -> None:
    """`selftest.mjs`, run as a test rather than as a thing someone remembers to run.

    A hand-written DOM is a fresh way to measure nothing: if `append` copied instead of moving, or
    `removeChild` destroyed instead of detaching, the v0.7.5 invariant would pass for a property
    of the instrument. Each check below is a semantic a captured invariant depends on.

    What this does NOT prove: that the DOM matches a browser anywhere else. It is a bounded
    implementation and `dom.mjs` says so; the injected-defect demonstrations in
    `../docs/gates/v0.12.0-guard-demonstrations.md` are the other half of the evidence.
    """
    result = domdriver.run_scenario("selfTest")
    failed = [r for r in result["results"] if not r["ok"]]
    assert not failed, "\n".join(f"{r['name']}: {r.get('detail')}" for r in failed)
    # v0.12.0 shipped 17; v0.13.0 adds five for the DOM additions the framework needs (dom.mjs
    # names all five and says why each one's absence produced a WRONG result rather than an
    # obvious one). The floor moves up with the suite, because a floor that never moves stops
    # being a ratchet.
    assert result["passed"] >= 22, "the conformance suite shrank; a smaller suite guards less"


# --- §4 determinism ----------------------------------------------------------------------------


@dom_test
def test_two_runs_in_two_processes_produce_identical_results() -> None:
    """Determinism across **two invocations and two processes**, which is the project's standard
    for anything a gate quotes (`make eval`, the census, the three reports).

    Every source of drift is pinned rather than tolerated: the clock is fixed, `toLocale*` is
    ISO-derived, no timer ever fires, and the subprocess runs under a fixed `TZ` and `LC_ALL`. A
    harness whose output moved between runs could not support a byte comparison of anything.

    What this does NOT prove: determinism across Node **versions**. Two majors could order object
    keys differently in principle; the gate records the detected version for that reason.
    """
    params = {"routes": _minimal_routes()}
    first = domdriver.run_scenario("boot", params)
    second = domdriver.run_scenario("boot", params)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


@dom_test
def test_the_harness_binds_no_port_and_reaches_no_network() -> None:
    """Isolation: the harness must not depend on the Python process or on anything outside it.

    `fetch` and `EventSource` are the only network surfaces `app.js` has, and both are replaced by
    recording doubles in `env.mjs`. This asserts the substitution structurally — no source file in
    the harness may name a real transport — because a harness that quietly reached a live server
    would make its results depend on what was running at the time.
    """
    for source_file in sorted(HARNESS.glob("*.mjs")):
        source = source_file.read_text(encoding="utf-8")
        for forbidden in ("node:http", "node:net", "node:https", "createServer", "listen("):
            assert forbidden not in source, f"{source_file.name} reaches for {forbidden}"


# --- §5 the harness is wired in, and its cost is counted ---------------------------------------


def test_make_qa_runs_the_dom_harness() -> None:
    """It must not be a thing someone remembers to run.

    `make qa` -> `test` -> pytest, which collects this file and `test_ui_invariants.py` like any
    other. What could still go wrong is the *reporting*: on a machine without Node these become
    skips, and a reader of "1300 passed" would not know. The `dom` marker exists so the gate can
    quote `pytest -m dom` separately, and `Makefile`'s `dom` target makes that a named command.

    **The prerequisite list is parsed, not matched as a literal** (v0.13.0). The first version
    asserted the exact string `"qa: lint typecheck deadcode test eval"`, which is the trap
    Appendix B names: a guard scoped by a literal fails on a change it has no opinion about. It
    duly failed when `scan` was added to `qa` — a change that makes the gate *stronger* — while
    what this test actually claims is only that `test` is one of `qa`'s prerequisites. That claim
    is now stated directly, so the recipe can grow without this test objecting, and can still not
    quietly drop the step that runs the harness.
    """
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    qa_lines = [line for line in makefile.splitlines() if line.startswith("qa:")]
    assert len(qa_lines) == 1, f"expected exactly one `qa:` target, found {len(qa_lines)}"
    prerequisites = qa_lines[0].split(":", 1)[1].split()
    assert "test" in prerequisites, (
        f"`make qa` no longer runs `test`, so nothing collects the DOM harness: {prerequisites}"
    )
    assert "eval" in prerequisites, (
        f"`make qa` no longer runs `eval`, so a correlation regression would not be gated: "
        f"{prerequisites}"
    )
    assert "\ndom:\n" in makefile, "Makefile has no `dom` target for reporting executed DOM tests"
    config = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'markers = ["dom' in config or "dom:" in config, "the `dom` marker is not registered"


def test_the_harness_is_a_test_tool_and_never_a_runtime_dependency() -> None:
    """Principle 5, at the boundary this release could most easily have crossed.

    Node is a **test** dependency. Nothing under `src/` may import the harness, reference it, or
    require a JavaScript runtime to run the appliance — and no harness file may be packaged.
    """
    config = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    dependencies = config.split("dependencies = [", 1)[1].split("]", 1)[0]
    assert dependencies.count('"') == 10, f"the runtime dependency list changed: {dependencies}"
    for name in ("node", "jsdom", "playwright", "puppeteer"):
        assert name not in dependencies.lower()
    package_data = config.split("[tool.setuptools.package-data]", 1)[1].split("[tool.", 1)[0]
    assert "domharness" not in package_data and ".mjs" not in package_data
    src = REPO_ROOT / "src" / "netcorenoc"
    for module in sorted(src.rglob("*.py")):
        source = module.read_text(encoding="utf-8")
        assert "domharness" not in source and "domdriver" not in source, module


@dom_test
def test_the_detected_runtime_is_reportable_for_the_gate() -> None:
    """The gate records the **detected** version, not the pinned one (ADR #166), so the harness
    has to be able to say what it ran on."""
    reported = domdriver.run_scenario("environment")
    assert reported["node"].startswith("v")
    assert int(reported["node"][1:].split(".")[0]) >= domdriver.NODE_MIN_MAJOR
    assert reported["tz"] == "UTC", "the subprocess did not get the pinned timezone"


@dom_test
def test_node_is_invoked_as_a_subprocess_and_not_through_a_shell() -> None:
    """Least privilege at the harness boundary: fixed argv, no shell, a minimal environment.

    The scenario name and parameters come from test code, but the parameters carry captured
    server responses — including, in the escaping scenario, deliberately hostile strings. Those
    reach Node as a JSON **file**, never as a command line.
    """
    source = (REPO_ROOT / "tests" / "domdriver.py").read_text(encoding="utf-8")
    assert "shell=True" not in source
    assert "params_path" in source and "json.dumps" in source
    assert 'env={"PATH"' in source, "the subprocess inherits the ambient environment"


def _minimal_routes() -> dict[str, object]:
    """A hand-written boot table for the harness's own tests.

    Deliberately NOT the captured server responses: this file tests the instrument, and a failure
    here should mean the instrument broke, not that a server shape moved. `test_ui_invariants.py`
    uses the real captures, which is where that coupling belongs.
    """
    return {
        "/api/me": {
            "json": {
                "user": "harness",
                "role": "viewer",
                "capabilities": ["self.read", "stats.read", "graph.read", "situations.read"],
                "scope": {"scoped": False, "ne_count": None},
                "must_change_password": False,
            }
        },
        "/api/stats": {
            "json": {
                "devices": 0,
                "classes": 0,
                "active_alarms": 0,
                "open_situations": 0,
                "latency_p95_s": 0.0,
                "warnings": [],
                "ingest_gaps": [],
                "open_ingest_gaps": [],
            }
        },
        "/api/graph": {"json": {"nodes": [], "edges": []}},
        "/api/situations?limit=50&status=open": {"json": []},
    }


def test_the_harness_tree_holds_no_installed_dependency() -> None:
    """The harness needs Node and nothing else — no `npm install`, no lockfile, no vendored tree.

    This is not merely tidiness. A harness that needed `npm install` would be a harness that skips
    on a machine without network, which is the failure mode this release is most likely to die of,
    and it would put a `package.json` in a repository whose sixth principle forbids one.
    `test_build_step.py` is the tree-wide version of this; here it is scoped to the instrument.
    """
    assert not list(HARNESS.rglob("package*.json"))
    assert not list(HARNESS.rglob("node_modules"))
    for source_file in sorted(HARNESS.glob("*.mjs")):
        source = source_file.read_text(encoding="utf-8")
        for line in source.splitlines():
            if line.startswith("import ") and " from " in line:
                target = line.split(" from ", 1)[1].strip().strip('";')
                assert target.startswith("node:") or target.startswith("./"), (
                    f"{source_file.name} imports {target!r}: the harness may use Node's standard "
                    "library and its own files, and nothing else"
                )


def test_node_absent_is_a_skip_and_never_a_silent_pass() -> None:
    """The shape of the failure mode, asserted on the mark itself.

    If Node were missing, `requires_node` must produce a *skip carrying the reason*. A bare
    `pytest.mark.skipif(..., reason="")`, or a try/except that swallowed the absence and returned
    an empty result, would both read as green.
    """
    assert requires_node.kwargs["reason"], "the skip mark carries no reason"
    assert requires_node.name == "skipif", "requires_node is not a conditional skip"
    assert len(requires_node.args) == 1, "the skip condition is not a single predicate"
    if shutil.which("node") is None:  # pragma: no cover - only on a machine without Node
        assert AVAILABLE.ok is False, "no node on PATH, yet the harness reported itself available"


def test_the_harness_never_writes_into_the_repository() -> None:
    """A test tool that left artefacts behind would put them in front of the principle-6 guard,
    and the two would then disagree about what the tree contains."""
    before = subprocess.run(
        ["git", "status", "--porcelain"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout
    if AVAILABLE.ok:
        domdriver.run_scenario("boot", {"routes": _minimal_routes()})
    after = subprocess.run(
        ["git", "status", "--porcelain"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout
    assert before == after, f"the harness changed the working tree:\n{after}"
