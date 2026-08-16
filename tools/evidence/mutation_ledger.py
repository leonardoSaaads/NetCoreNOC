"""A mutation ledger over v0.13.0's own surface. **Reports the survivor list, not the ratio.**

A ratio is a number nobody can act on. A survivor is a specific, named change that no test in this
repository notices, and it is either a gap worth closing or a property nobody claimed — and saying
which is the whole value of running this.

Each mutant is a small semantic edit to code THIS RELEASE wrote. The suite run against each is the
whole UI-facing set, so a mutant that survives survived everything that could plausibly see it.
"""

from __future__ import annotations

import json
import shutil

# B404 is suppressed on the import below: subprocess is how this tool runs the suite against each
# mutant and reads the tree back afterwards. Both call sites pass a fixed argv with `shell=False`
# and an absolute executable.
import subprocess  # nosec B404
import sys
from pathlib import Path

#: Derived from this file's location rather than pinned to the build container's absolute path,
#: which is what the first version did — making the reproduction command in the gate document
#: false on every machine but one.
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".demos"

#: The interpreter running this script, not an assumed `.venv/bin/python`.
PY = sys.executable

#: Resolved once to an absolute path (bandit B607): a partial name is resolved against `PATH` at
#: call time, so whichever directory comes first on `PATH` decides which `git` runs.
GIT = shutil.which("git")

#: The suite each mutant is run against.
#:
#: **`test_api.py` is here because leaving it out made the ledger lie.** The two Python mutants
#: below live in `routes_admin.py` and `routes_static.py`, and the tests that kill them are HTTP
#: tests in `test_api.py`. Without it the ledger reported `routes_admin.py: precedence …` as a
#: SURVIVOR while the repository killed it — so the documented figure and the tool's own output
#: disagreed, and the tool was the one that was wrong. A mutation ledger whose suite does not
#: include the tests that cover the mutated file is not measuring the repository; it is measuring
#: its own SUITE list.
SUITE = [
    "tests/test_ui_invariants.py",
    "tests/test_security_ui.py",
    "tests/test_dom_harness.py",
    "tests/test_build_step.py",
    "tests/test_supply_chain.py",
    "tests/test_architecture.py",
    "tests/test_declaration.py",
    "tests/test_api.py",
]

MUTANTS = [
    (
        "router.js: an unknown view resolves to the default instead of reporting unknown",
        "app/router.js",
        '  if (!view) return { kind: "unknown", viewId };',
        '  if (!view) return { kind: "view", view: VIEWS[0], params: [], query: new URLSearchParams() };',
    ),
    (
        "router.js: `missing` computed against the wrong direction",
        "app/router.js",
        "  const missing = required.filter((capability) => !capabilitySet.has(capability));",
        "  const missing = required.filter((capability) => capabilitySet.has(capability));",
    ),
    (
        "router.js: reachableViews ignores `hidden`",
        "app/router.js",
        "    if (view.hidden) return false;",
        "    if (false) return false;",
    ),
    (
        "router.js: a view with several capabilities passes if it holds ANY",
        "app/router.js",
        "    return requirementsOf(view).every((capability) => capabilitySet.has(capability));",
        "    return requirementsOf(view).some((capability) => capabilitySet.has(capability));",
    ),
    (
        "session.js: canEdit() returns true unconditionally",
        "app/session.js",
        '  return can("feedback.write") || can("label.write") || can("situation.close");',
        "  return true;",
    ),
    (
        "session.js: a scoped operator's badge is suppressed",
        "app/session.js",
        "  if (!scope || !scope.scoped) return null;",
        "  return null;",
    ),
    (
        "store.js: collapse leaves the held payload behind",
        "app/store.js",
        "  state.held.delete(sid);\n  // The marker must go",
        "  // The marker must go",
    ),
    (
        "store.js: withheld counter never increments",
        "app/store.js",
        "    state.withheld.set(sid, (state.withheld.get(sid) ?? 0) + 1);",
        "    state.withheld.set(sid, 0);",
    ),
    (
        "store.js: reset() leaves the previous principal's expanded cards",
        "app/store.js",
        "  state.expanded.clear();",
        "  void 0;",
    ),
    (
        "parameters.js: the threshold ceiling ignores the margin",
        "app/parameters.js",
        "  const ceiling = weightSum - bounds.threshold_margin;",
        "  const ceiling = weightSum + bounds.threshold_margin;",
    ),
    (
        "parameters.js: a non-numeric submission is accepted",
        "app/parameters.js",
        "  if (!Number.isFinite(value)) {",
        "  if (false) {",
    ),
    (
        "widgets.js: the Loader fetches in the constructor rather than on mount",
        "app/widgets.js",
        "  componentDidMount() { this.reload(); }",
        "  componentDidMount() { }",
    ),
    (
        "widgets.js: Refused renders nothing at all",
        "app/widgets.js",
        '  return html`<div class="state state-refused" role="alert">',
        '  return html`<div class="state state-hidden" role="alert">',
    ),
    (
        "theme.js: the cookie gains a second value",
        "app/theme.js",
        "    `${name}=${encodeURIComponent(value)}; Path=/; Max-Age=31536000; SameSite=Strict`;",
        "    `${name}=${encodeURIComponent(value)}; Path=/; Max-Age=31536000; SameSite=Strict`;\n"
        "  globalThis.document.cookie = `ncn_seen=1; Path=/`;",
    ),
    (
        "sidebar.js: an empty group still renders its heading",
        "app/sidebar.js",
        "      if (inGroup.length) rendered.push(",
        "      if (true) rendered.push(",
    ),
    (
        "situations.js: `member_ids` reports the live list rather than the held one",
        "app/views/situations.js",
        "    const body = { verdict: kind, member_ids: detail.alarms.map((a) => a.id),",
        "    const body = { verdict: kind, member_ids: [],",
    ),
    (
        "registry.js: the account view stops being hidden from navigation",
        "app/registry.js",
        '    id: "account", label: "Your account", glyph: "◐", group: null, hidden: true,',
        '    id: "account", label: "Your account", glyph: "◐", group: null, hidden: false,',
    ),
]

PY_MUTANTS = [
    (
        # The anchor was wrong in the first version — it named a dict comprehension the file does
        # not contain — so this mutant was recorded as "NOT APPLIED — anchor missing" and has never
        # actually been measured. An injection that does not apply measures nothing, which is
        # Appendix B's trap and is why the ledger prints the reason instead of a tick.
        "routes_static.py: the vendored assets stop being served",
        "src/netcorenoc/api/routes_static.py",
        '    **dict.fromkeys(_VENDOR_ASSETS, "application/javascript"),\n',
        "",
    ),
    (
        "routes_admin.py: precedence reports the effective value as the environment default",
        "src/netcorenoc/api/routes_admin.py",
        '                "allowlist": {"env": env.allowlist, "override": saved_allow},',
        '                "allowlist": {"env": saved_allow, "override": saved_allow},',
    ),
    (
        # The third mutant `v0.13.0-phase-7.md` §7.2 says was found while closing the second: the
        # same defect one field over. It was closed at the time but never added to this table, so
        # the tool could not reproduce the figure the document quoted.
        "routes_admin.py: precedence reports the retention override as the environment default",
        "src/netcorenoc/api/routes_admin.py",
        '                    "env": env.retention_days,',
        '                    "env": float(saved_ret) if saved_ret is not None else '
        "env.retention_days,",
    ),
]


def run() -> str:
    # B603: `shell=False` with a fixed argv — `PY` is `sys.executable`, the flags are literals
    # and `SUITE` is a module constant of test paths. No operator input reaches this.
    proc = subprocess.run(  # nosec B603
        [PY, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider", *SUITE],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    tail = [line for line in proc.stdout.splitlines() if " passed" in line or " failed" in line]
    return tail[-1] if tail else proc.stdout.strip()[-200:]


def git(*args: str) -> str:
    """Run git by absolute path and return its stdout."""
    if GIT is None:
        raise RuntimeError(
            "git was not found on PATH. The ledger reads the tree back after every mutant is "
            "reverted, and a ledger that cannot check that is not evidence."
        )
    # B603: fixed argv, `shell=False`, `GIT` absolute, every argument a literal.
    return subprocess.run(  # nosec B603
        [GIT, *args], cwd=str(ROOT), capture_output=True, text=True, check=True
    ).stdout


def main() -> None:
    # Created here rather than at import, and created at all: the first version wrote
    # `.demos/mutants.json` into a directory only `guard_demonstrations.py` created, so running
    # this tool on its own raised `FileNotFoundError` after a nine-minute measurement.
    OUT.mkdir(exist_ok=True)
    ledger = []
    for title, relative, old, new in [
        (t, f"src/netcorenoc/ui/{r}", o, n) for t, r, o, n in MUTANTS
    ] + PY_MUTANTS:
        target = ROOT / relative
        original = target.read_text(encoding="utf-8")
        if old not in original:
            ledger.append(
                {"title": title, "status": "NOT APPLIED — anchor missing", "file": relative}
            )
            print(f"SKIP  {title}", flush=True)
            continue
        target.write_text(original.replace(old, new, 1), encoding="utf-8")
        outcome = run()
        target.write_text(original, encoding="utf-8")
        killed = "failed" in outcome
        ledger.append(
            {
                "title": title,
                "file": relative,
                "outcome": outcome,
                "status": "killed" if killed else "SURVIVED",
            }
        )
        print(f"{'kill ' if killed else 'LIVE '} {title}\n        {outcome}", flush=True)

    (OUT / "mutants.json").write_text(json.dumps(ledger, indent=2))
    survivors = [m for m in ledger if m["status"] != "killed"]
    print(f"\n{len(ledger) - len(survivors)}/{len(ledger)} killed.")
    print("\nSURVIVORS, by name:")
    for m in survivors:
        print(f"  - {m['file']}: {m['title']}")
    status = git("status", "--porcelain", "src", "tests").strip()
    print(f"\ntree after: {status or '(clean)'}")
    sys.exit(0 if not status else 1)


main()
