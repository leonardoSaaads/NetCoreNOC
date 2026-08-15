"""A mutation ledger over v0.13.0's own surface. **Reports the survivor list, not the ratio.**

A ratio is a number nobody can act on. A survivor is a specific, named change that no test in this
repository notices, and it is either a gap worth closing or a property nobody claimed — and saying
which is the whole value of running this.

Each mutant is a small semantic edit to code THIS RELEASE wrote. The suite run against each is the
whole UI-facing set, so a mutant that survives survived everything that could plausibly see it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path("/home/user/NetCoreNOC")
PY = str(ROOT / ".venv" / "bin" / "python")

SUITE = [
    "tests/test_ui_invariants.py",
    "tests/test_security_ui.py",
    "tests/test_dom_harness.py",
    "tests/test_build_step.py",
    "tests/test_supply_chain.py",
    "tests/test_architecture.py",
    "tests/test_declaration.py",
]

MUTANTS = [
    ("router.js: an unknown view resolves to the default instead of reporting unknown",
     "app/router.js", '  if (!view) return { kind: "unknown", viewId };',
     '  if (!view) return { kind: "view", view: VIEWS[0], params: [], query: new URLSearchParams() };'),
    ("router.js: `missing` computed against the wrong direction",
     "app/router.js", "  const missing = required.filter((capability) => !capabilitySet.has(capability));",
     "  const missing = required.filter((capability) => capabilitySet.has(capability));"),
    ("router.js: reachableViews ignores `hidden`",
     "app/router.js", "    if (view.hidden) return false;", "    if (false) return false;"),
    ("router.js: a view with several capabilities passes if it holds ANY",
     "app/router.js", "    return requirementsOf(view).every((capability) => capabilitySet.has(capability));",
     "    return requirementsOf(view).some((capability) => capabilitySet.has(capability));"),
    ("session.js: canEdit() returns true unconditionally",
     "app/session.js", '  return can("feedback.write") || can("label.write") || can("situation.close");',
     "  return true;"),
    ("session.js: a scoped operator's badge is suppressed",
     "app/session.js", "  if (!scope || !scope.scoped) return null;", "  return null;"),
    ("store.js: collapse leaves the held payload behind",
     "app/store.js", "  state.held.delete(sid);\n  // The marker must go",
     "  // The marker must go"),
    ("store.js: withheld counter never increments",
     "app/store.js", "    state.withheld.set(sid, (state.withheld.get(sid) ?? 0) + 1);",
     "    state.withheld.set(sid, 0);"),
    ("store.js: reset() leaves the previous principal's expanded cards",
     "app/store.js", "  state.expanded.clear();", "  void 0;"),
    ("parameters.js: the threshold ceiling ignores the margin",
     "app/parameters.js", "  const ceiling = weightSum - bounds.threshold_margin;",
     "  const ceiling = weightSum + bounds.threshold_margin;"),
    ("parameters.js: a non-numeric submission is accepted",
     "app/parameters.js", "  if (!Number.isFinite(value)) {", "  if (false) {"),
    ("widgets.js: the Loader fetches in the constructor rather than on mount",
     "app/widgets.js", "  componentDidMount() { this.reload(); }", "  componentDidMount() { }"),
    ("widgets.js: Refused renders nothing at all",
     "app/widgets.js", '  return html`<div class="state state-refused" role="alert">',
     '  return html`<div class="state state-hidden" role="alert">'),
    ("theme.js: the cookie gains a second value",
     "app/theme.js", "    `${name}=${encodeURIComponent(value)}; Path=/; Max-Age=31536000; SameSite=Strict`;",
     "    `${name}=${encodeURIComponent(value)}; Path=/; Max-Age=31536000; SameSite=Strict`;\n"
     "  globalThis.document.cookie = `ncn_seen=1; Path=/`;"),
    ("sidebar.js: an empty group still renders its heading",
     "app/sidebar.js", "      if (inGroup.length) rendered.push(",
     "      if (true) rendered.push("),
    ("situations.js: `member_ids` reports the live list rather than the held one",
     "app/views/situations.js", "    const body = { verdict: kind, member_ids: detail.alarms.map((a) => a.id),",
     "    const body = { verdict: kind, member_ids: [],"),
    ("registry.js: the account view stops being hidden from navigation",
     "app/registry.js", '    id: "account", label: "Your account", glyph: "◐", group: null, hidden: true,',
     '    id: "account", label: "Your account", glyph: "◐", group: null, hidden: false,'),
]

PY_MUTANTS = [
    ("routes_static.py: the vendored assets stop being served",
     "src/netcorenoc/api/routes_static.py",
     '    **{name: "application/javascript" for name in _VENDOR_ASSETS},\n', ""),
    ("routes_admin.py: precedence reports the effective value as the environment default",
     "src/netcorenoc/api/routes_admin.py",
     '                "allowlist": {"env": env.allowlist, "override": saved_allow},',
     '                "allowlist": {"env": saved_allow, "override": saved_allow},'),
]


def run() -> str:
    proc = subprocess.run(
        [PY, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider", *SUITE],
        cwd=str(ROOT), capture_output=True, text=True, timeout=900, check=False,
    )
    tail = [line for line in proc.stdout.splitlines() if " passed" in line or " failed" in line]
    return tail[-1] if tail else proc.stdout.strip()[-200:]


def main() -> None:
    ledger = []
    for title, relative, old, new in (
        [(t, f"src/netcorenoc/ui/{r}", o, n) for t, r, o, n in MUTANTS] + PY_MUTANTS
    ):
        target = ROOT / relative
        original = target.read_text(encoding="utf-8")
        if old not in original:
            ledger.append({"title": title, "status": "NOT APPLIED — anchor missing", "file": relative})
            print(f"SKIP  {title}", flush=True)
            continue
        target.write_text(original.replace(old, new, 1), encoding="utf-8")
        outcome = run()
        target.write_text(original, encoding="utf-8")
        killed = "failed" in outcome
        ledger.append({
            "title": title, "file": relative, "outcome": outcome,
            "status": "killed" if killed else "SURVIVED",
        })
        print(f"{'kill ' if killed else 'LIVE '} {title}\n        {outcome}", flush=True)

    Path(ROOT / ".demos" / "mutants.json").write_text(json.dumps(ledger, indent=2))
    survivors = [m for m in ledger if m["status"] != "killed"]
    print(f"\n{len(ledger) - len(survivors)}/{len(ledger)} killed.")
    print("\nSURVIVORS, by name:")
    for m in survivors:
        print(f"  - {m['file']}: {m['title']}")
    status = subprocess.run(["git", "status", "--porcelain", "src", "tests"], cwd=str(ROOT),
                            capture_output=True, text=True, check=True).stdout.strip()
    print(f"\ntree after: {status or '(clean)'}")
    sys.exit(0 if not status else 1)


main()
