"""Reproduce the shipped model's published numbers: ``make train-verify``.

The maintainer's check that the artifact in the package is the one the manifest describes, and that
its numbers are real. It loads the model **the way the appliance does** — `shipped.load()`, the
installed package's two files, validated as data — rebuilds the generated dataset from its seed
(``eval/synth/.cache`` is reused when present, re-recorded when not), evaluates the model and the
formula on every test split and held-out family, and compares each number with the manifest.

Equality, not tolerance: generation, recording, grouping and the bootstrap are all seeded, so the
same code produces the same numbers, and a difference is a finding. It never writes the artifact
and never reads a validation stream.
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
from netcorenoc.engine.model import shipped  # noqa: E402

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
    parser = argparse.ArgumentParser(description="Reproduce the shipped model's numbers.")
    parser.add_argument("--workers", type=int, default=None)
    args = parser.parse_args()
    t0 = time.time()
    model = shipped.load()
    provenance = model.manifest["provenance"]
    print(f"netcorenoc {netcorenoc.__version__} from {Path(netcorenoc.__file__).parent}")
    print(f"artifact {model.ref} (manifest and document agree; validated as data)")
    root = dataset.build(float(provenance["scale"]), int(provenance["seed"]), args.workers)
    if root.name != provenance["dataset_digest"]:
        print(
            f"DATASET DIFFERS: rebuilt {root.name}, trained on {provenance['dataset_digest']}"
            " — the generator or recording code changed since training; numbers may differ",
        )
    measured = json.loads(json.dumps(report.evaluate(root, model.scorer, int(provenance["seed"]))))
    found = differences(model.manifest["evaluation"], measured)
    verdict = report.check_bar(measured, QUALITY_BAR)
    print(
        f"evaluated in {time.time() - t0:.0f}s: {len(found)} differences from the manifest; "
        f"quality bar {'passed' if verdict['passed'] else 'MISSED'} "
        f"({verdict['checked']} checks)"
    )
    for line in found[:40]:
        print(f"  {line}")
    return 0 if not found and verdict["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
