"""Re-run the two mutants the first ledger did not settle: one skipped, one that survived."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path("/home/user/NetCoreNOC")
PY = str(ROOT / ".venv" / "bin" / "python")
SUITE = [
    "tests/test_api.py",
    "tests/test_ui_invariants.py",
    "tests/test_security_ui.py",
    "tests/test_supply_chain.py",
    "tests/test_declaration.py",
]

MUTANTS = [
    # The first ledger's anchor did not match — a probe failure, not a survivor. This one is
    # anchored on a string that is actually in the file.
    ("routes_static.py: the vendored assets stop being served",
     "src/netcorenoc/api/routes_static.py",
     '    "vendor/preact-10.29.8.module.js",', "    "),
    # The genuine survivor from the first ledger, re-run against the test written to kill it.
    ("routes_admin.py: precedence reports the override as the environment default",
     "src/netcorenoc/api/routes_admin.py",
     '                "allowlist": {"env": env.allowlist, "override": saved_allow},',
     '                "allowlist": {"env": saved_allow, "override": saved_allow},'),
    # And the other half of the same field, which the new test also has to cover.
    ("routes_admin.py: the retention override is reported as the environment default",
     "src/netcorenoc/api/routes_admin.py",
     '                    "env": env.retention_days,',
     '                    "env": float(saved_ret) if saved_ret is not None else env.retention_days,'),
]


def run() -> str:
    proc = subprocess.run(
        [PY, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider", *SUITE],
        cwd=str(ROOT), capture_output=True, text=True, timeout=900, check=False,
    )
    tail = [line for line in proc.stdout.splitlines() if " passed" in line or " failed" in line]
    return tail[-1] if tail else proc.stdout.strip()[-200:]


for title, relative, old, new in MUTANTS:
    target = ROOT / relative
    original = target.read_text(encoding="utf-8")
    if old not in original:
        print(f"SKIP  {title} (anchor still missing)")
        continue
    target.write_text(original.replace(old, new, 1), encoding="utf-8")
    outcome = run()
    target.write_text(original, encoding="utf-8")
    print(f"{'kill ' if 'failed' in outcome else 'LIVE '} {title}\n        {outcome}", flush=True)

status = subprocess.run(["git", "status", "--porcelain", "src"], cwd=str(ROOT),
                        capture_output=True, text=True, check=True).stdout.strip()
print(f"tree after: {status or '(clean)'}")
sys.exit(0 if not status else 1)
