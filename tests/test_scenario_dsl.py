"""C.1 — the declarative scenario DSL and the trap simulator (test/tooling assets).

Asserts the DSL is deterministic and produces valid corpus-format events, and that the simulator
CLI writes/emits a scenario. The DSL/sim are never imported by ``netcorenoc/`` (asserted below).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))

import scenario_dsl

REPO = Path(__file__).resolve().parent.parent


def test_scenario_build_is_deterministic() -> None:
    a = json.dumps(scenario_dsl.login_failure_burst().build(), sort_keys=True)
    b = json.dumps(scenario_dsl.login_failure_burst().build(), sort_keys=True)
    assert a == b


def test_scenario_events_are_valid_corpus_format() -> None:
    doc = scenario_dsl.chassis_card_containment().build()
    assert set(doc) == {"name", "description", "events"}
    for event in doc["events"]:
        assert {"delay", "source", "trap_oid", "varbinds", "truth"} <= event.keys()
        assert all({"oid", "kind", "value"} <= vb.keys() for vb in event["varbinds"])
        assert {"situation_key", "entity_key"} <= event["truth"].keys()
        # sysUpTime.0 is always prepended (real trap shape).
        assert event["varbinds"][0]["oid"] == scenario_dsl.SYS_UPTIME_OID


def test_all_registered_scenarios_build() -> None:
    for name, builder in scenario_dsl.SCENARIOS.items():
        doc = builder().build()
        assert doc["events"], name


def test_dsl_and_simulator_never_import_the_runtime() -> None:
    """Structural guarantee (§C): vendor OIDs live in the corpus/tooling, never in the engine."""
    for path in (REPO / "eval" / "scenario_dsl.py", REPO / "tools" / "trap_sim.py"):
        text = path.read_text()
        assert "import netcorenoc" not in text
        assert "from netcorenoc" not in text


def test_trap_sim_cli_writes_scenario(tmp_path: Path) -> None:
    out = tmp_path / "login.json"
    result = subprocess.run(
        [sys.executable, str(REPO / "tools" / "trap_sim.py"), "login_burst", "--write", str(out)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    doc = json.loads(out.read_text())
    assert doc["name"] == "security_login_burst"
    assert len(doc["events"]) == 12
