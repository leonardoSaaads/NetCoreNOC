"""Reproduce every league member's published numbers: ``make train-verify``.

The maintainer's check that each document in the package is the one its manifest describes, and
that its numbers are real. It loads the league **the way the appliance does** — `league.load()`,
the installed package's files, validated as data — rebuilds the generated dataset from its seed
(``eval/synth/.cache`` is reused when present, re-recorded when not), evaluates every member on
every test split and held-out family, and compares each number with that member's manifest.

Equality, not tolerance: generation, recording, grouping and the bootstrap are all seeded, so the
same code produces the same numbers, and a difference is a finding. It never writes a document and
never reads a validation stream. The scorecard is re-checked too, but a member that misses the bar
is still a member (ADR #426): the exit status reports differences, not the bar.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import netcorenoc  # noqa: E402
from netcorenoc.engine.model import league  # noqa: E402

from synth import dataset, report  # noqa: E402
from synth.train import QUALITY_BAR  # noqa: E402


def differences(published: Any, measured: Any, at: str = "") -> list[str]:
    """Every leaf where the two differ. NaN equals NaN: an undefined number stays undefined."""
    if isinstance(published, dict) and isinstance(measured, dict):
        out: list[str] = []
        for key in sorted(set(published) | set(measured)):
            if key not in published or key not in measured:
                out.append(f"{at}/{key}: present on one side only")
            else:
                out.extend(differences(published[key], measured[key], f"{at}/{key}"))
        return out
    if isinstance(published, list) and isinstance(measured, list):
        if len(published) != len(measured):
            return [f"{at}: {len(published)} items published, {len(measured)} measured"]
        return [
            d
            for i, (p, m) in enumerate(zip(published, measured, strict=True))
            for d in differences(p, m, f"{at}[{i}]")
        ]
    floats = isinstance(published, float) and isinstance(measured, float)
    if floats and math.isnan(published) and math.isnan(measured):
        return []
    if published == measured:
        return []
    return [f"{at}: published {published!r}, measured {measured!r}"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Reproduce every league member's numbers.")
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--kinds", default="", help="comma-separated kinds; default: every member")
    args = parser.parse_args()
    t0 = time.time()
    packaged = league.load()
    print(f"netcorenoc {netcorenoc.__version__} from {Path(netcorenoc.__file__).parent}")
    for kind, reason in packaged.refused:
        print(f"REFUSED {kind}: {reason}")
    wanted = {k for k in args.kinds.split(",") if k}
    members = [m for m in packaged.members if not wanted or m.kind in wanted]
    if not members:
        print("no league member to verify")
        return 1
    failed = bool(packaged.refused)
    root: Path | None = None
    for member in members:
        provenance = member.manifest["provenance"]
        print(f"member {member.ref} (manifest and document agree; validated as data)")
        if root is None:
            root = dataset.build(float(provenance["scale"]), int(provenance["seed"]), args.workers)
        if root.name != provenance["dataset_digest"]:
            print(
                f"  DATASET DIFFERS: rebuilt {root.name}, trained on {provenance['dataset_digest']}"
                " — the generator or recording code changed since training; numbers may differ",
            )
        # Every kind answers the scorer contract `report.evaluate` reads (`logit`, `explain`,
        # `model.grouping`); it is annotated with the GAM's class, which predates the league.
        scorer: Any = member.scorer
        measured = json.loads(json.dumps(report.evaluate(root, scorer, int(provenance["seed"]))))
        found = differences(member.manifest["evaluation"], measured)
        verdict = report.check_bar(measured, QUALITY_BAR)
        print(
            f"  {len(found)} differences from the manifest; scorecard "
            f"{'meets' if verdict['passed'] else 'misses'} the bar ({verdict['checked']} checks)"
        )
        for line in found[:40]:
            print(f"    {line}")
        failed = failed or bool(found)
    print(f"verified {len(members)} member(s) in {time.time() - t0:.0f}s")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
