"""The Python side of the DOM harness: availability, invocation, and the execution ledger.

This module is a **test tool**. It is not imported by anything under `src/`, it adds no runtime
dependency, and it is never packaged — `pyproject.toml`'s `package-data` names `ui/*` and
`migrations/*` and nothing here. `tests/test_build_step.py` asserts the tool/product distinction
structurally, because "Node as a test dependency" and "Node as a build step for shipped assets"
are the two sides of principle 6 and only one of them is permitted.

**The failure mode this module is shaped around is a harness that skips.** A green suite that
executed no DOM test is worse than no harness, because it looks like coverage. Three things guard
against it, and they fail for different reasons:

  1. :func:`availability` is a pure function of the environment, so `tests/test_dom_harness.py`
     can drive it into its unavailable branch and assert that the branch *reports* — the skip path
     is itself tested rather than assumed reachable.
  2. :func:`run_scenario` **refuses a result that carries no proof of execution**. Every scenario
     returns the output of `app.js`'s own `esc()`; a harness that stubbed everything and ran
     nothing cannot produce it, and the run raises instead of returning an empty green.
  3. :data:`EXECUTIONS` counts real Node invocations that cleared (2), and the gate documents
     quote **executed**, never collected.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
HARNESS_DIR = Path(__file__).resolve().parent / "domharness"
RUNNER = HARNESS_DIR / "run.mjs"

# The rule, not the number (ADR #166). Node 24 is the Active LTS line and Node 22 the Maintenance
# LTS supported into April 2027; Node 26 removes legacy APIs and enables Temporal by default, so a
# harness written against 26-only behaviour would not run on either LTS. The harness therefore
# requires `>= 22` and uses no version-specific API. The *detected* version is recorded in the gate
# evidence; this floor is what the code enforces.
NODE_MIN_MAJOR = 22

# What `app.js`'s own `esc()` returns for `'<a href="x">'`. Proof that app.js evaluated: the
# harness cannot produce this string without having run the file under test.
ESC_PROOF = "&lt;a href=&quot;x&quot;&gt;"

#: One entry per Node invocation that returned a proven execution of `ui/app.js`.
EXECUTIONS: list[str] = []


@dataclass(frozen=True)
class Availability:
    """Whether the harness can run here, and — when it cannot — a reason naming the requirement."""

    ok: bool
    reason: str
    version: str | None = None

    @property
    def skip_message(self) -> str:
        return self.reason


def availability(node: str | None = None, *, path: str | None = None) -> Availability:
    """Resolve whether a usable Node is present.

    A pure function of `PATH` and of the interpreter's own `--version`, so a test can drive both
    branches without needing a machine that lacks Node. That is the whole point: **ambiguity about
    whether the harness ran resolves to "it did not"**, and a skip path nobody has watched report
    is a skip path that can silently swallow the suite.

    `path=None` means "use the ambient PATH"; `path=""` means "search nowhere". The first version
    of this function wrote `path or os.environ["PATH"]`, which collapsed the two — so the caller
    that asked for an empty search path got the real one, and the unavailable branch was
    **unreachable from a test**. That is F51's class exactly (a guard whose scope silently widens),
    found here by the very test that exists to drive this branch.
    """
    search = os.environ.get("PATH", "") if path is None else path
    exe = node or shutil.which("node", path=search)
    if not exe:
        return Availability(
            ok=False,
            reason=(
                "the DOM harness requires Node.js >= "
                f"{NODE_MIN_MAJOR} on PATH and found no `node` interpreter. "
                "It is a TEST dependency only: nothing under src/ needs it, no runtime "
                "dependency was added, and the shipped UI still has no build step."
            ),
        )
    try:
        proc = subprocess.run(
            [exe, "--version"], capture_output=True, text=True, timeout=30, check=False
        )
    except OSError as exc:  # pragma: no cover - defensive; an unexecutable node on PATH
        return Availability(ok=False, reason=f"`{exe} --version` could not be executed: {exc}")
    raw = proc.stdout.strip()
    match = re.match(r"^v(\d+)\.", raw)
    if proc.returncode != 0 or not match:
        return Availability(
            ok=False,
            reason=f"`{exe} --version` did not report a version (got {raw!r})",
            version=raw,
        )
    if int(match.group(1)) < NODE_MIN_MAJOR:
        return Availability(
            ok=False,
            reason=(
                f"the DOM harness requires Node.js >= {NODE_MIN_MAJOR} and found {raw}. "
                "The reference target is the Active LTS line at the time of the release, which is "
                "a rule rather than a number (ADR #166)."
            ),
            version=raw,
        )
    return Availability(ok=True, reason=f"Node {raw}", version=raw)


class HarnessError(RuntimeError):
    """The harness ran and something went wrong — never a silent skip."""


def run_scenario(name: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Execute one scenario in a fresh Node process and return its result.

    Raises rather than skipping on every failure path, including "the result carried no proof that
    `ui/app.js` was evaluated". A scenario that returns an empty green is the thing this release
    exists to make impossible.
    """
    available = availability()
    if not available.ok:  # pragma: no cover - exercised via availability() in the skip-path test
        raise HarnessError(available.reason)

    with tempfile.TemporaryDirectory() as tmp:
        params_path = Path(tmp) / "params.json"
        params_path.write_text(json.dumps(params or {}, sort_keys=True), encoding="utf-8")
        proc = subprocess.run(
            ["node", str(RUNNER), name, str(params_path)],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(REPO_ROOT),
            # A fixed, minimal environment: no inherited locale or TZ can move a rendered date.
            env={"PATH": os.environ.get("PATH", ""), "TZ": "UTC", "LC_ALL": "C"},
            check=False,
        )
    if proc.returncode != 0 or not proc.stdout:
        raise HarnessError(
            f"scenario {name!r} exited {proc.returncode}\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
        )
    result: dict[str, Any] = json.loads(proc.stdout)
    if "error" in result:
        raise HarnessError(f"scenario {name!r} failed inside the harness:\n{result['error']}")
    if name not in {"selfTest", "environment"}:
        _require_proof(name, result)
    return result


def _require_proof(name: str, result: dict[str, Any]) -> None:
    proof = result.get("proof")
    if not proof:
        raise HarnessError(
            f"scenario {name!r} returned no execution proof: app.js may not have run"
        )
    if proof.get("escaped") != ESC_PROOF:
        raise HarnessError(
            f"scenario {name!r} did not execute ui/app.js: its own esc() returned "
            f"{proof.get('escaped')!r}, expected {ESC_PROOF!r}"
        )
    EXECUTIONS.append(name)


def executions() -> int:
    """How many Node runs proved they evaluated `ui/app.js`. The gate quotes this, not a count
    of collected tests."""
    return len(EXECUTIONS)
