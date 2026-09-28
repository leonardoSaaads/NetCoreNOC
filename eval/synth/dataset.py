"""The training set, its splits, and how it is built: pinned, parallel, cached, digested.

## The splits (ADR #408)

Every split is a list of **streams**, and a stream is never cut in two — so no incident, and no
pair, can appear on both sides of any split (Part V: *group by incident, never by pair*).

* ``train`` — 96 streams of the training families: what the model is fitted on.
* ``train_long`` — 12 streams of training families, 7-14 days, high recurrence: memory: first 70 %
  trains, last 30 % is ``test_time``.
* ``valid`` — 24 streams of training families: early stopping, grouping parameters, the search.
* ``test_iid`` — 32 streams of training families: new estates, familiar faults.
* ``test_optical`` — 24 streams of **held-out** optical families + context: DWDM degradation, line
  cuts, protection — never trained on.
* ``test_protocol`` — 24 streams of **held-out** routing-protocol families + context: BGP and OSPF
  flaps — never trained on.
* ``test_concurrency`` — 24 streams of training families, 80 % concurrency: the `dual_incident`
  failure at scale.

**Held-out families are held out of everything the model is fitted or tuned on**, including
validation: a family that informed early stopping has informed the model. The shipped artifact is
the one measured on them — it is *not* refitted on all families afterwards, because then the
held-out numbers would describe a different artifact.

## Building it

`python -m synth.dataset --build` composes every stream from its spec, records each through the
real appliance (`record.py`) in a pool of worker processes, and caches the logs under
``eval/synth/.cache/<digest>/``, where the digest covers the specs and the generator's source. A
cache hit is only ever the same bytes the build would produce.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from synth.compose import StreamSpec, compose, derived_rng  # noqa: E402
from synth.record import Activation, StreamLog, record  # noqa: E402

__all__ = [
    "HOLDOUT_OPTICAL",
    "HOLDOUT_PROTOCOL",
    "SEED",
    "SPLITS",
    "TRAIN_FAMILIES",
    "Row",
    "build",
    "digest",
    "load_split",
    "training_rows",
]

SEED = 2026
TRAIN_FAMILIES = (
    "gpon_fibre_cut",
    "onu_power_outage",
    "olt_card_failure",
    "olt_uplink_failure",
    "router_link_cut",
    "router_board_failure",
    "port_flapping",
    "ne_reboot",
    "planned_maintenance",
    "site_power_loss",
    "environment",
)
HOLDOUT_OPTICAL = ("dwdm_degradation", "dwdm_line_cut", "optical_protection")
HOLDOUT_PROTOCOL = ("bgp_flap", "ospf_flap")

CACHE = HERE / ".cache"


def _specs(split: str, count: int, seed: int) -> list[StreamSpec]:
    rng = derived_rng("split", split, seed)
    out: list[StreamSpec] = []
    for i in range(count):
        name = f"{split}-{i:03d}"
        s = seed * 1000 + i
        if split == "train_long":
            out.append(
                StreamSpec(
                    name,
                    s,
                    TRAIN_FAMILIES,
                    rng.uniform(168, 336),
                    rng.uniform(0.1, 0.25),
                    rng.uniform(0.5, 2.5),
                    concurrency=0.2,
                    recurrence=0.45,
                )
            )
            continue
        families: tuple[str, ...] = TRAIN_FAMILIES
        weights: tuple[tuple[str, float], ...] = ()
        concurrency = rng.uniform(0.1, 0.4)
        if split == "test_optical":
            families = TRAIN_FAMILIES + HOLDOUT_OPTICAL
            weights = tuple((f, 8.0) for f in HOLDOUT_OPTICAL)
        elif split == "test_protocol":
            families = TRAIN_FAMILIES + HOLDOUT_PROTOCOL
            weights = tuple((f, 8.0) for f in HOLDOUT_PROTOCOL)
        elif split == "test_concurrency":
            concurrency = 0.8
        out.append(
            StreamSpec(
                name,
                s,
                families,
                rng.uniform(4, 28),
                rng.uniform(0.6, 3.0),
                rng.uniform(1.0, 14.0),
                concurrency=concurrency,
                recurrence=0.08,
                weights=weights,
            )
        )
    return out


SPLITS: dict[str, int] = {
    "train": 96,
    "train_long": 12,
    "valid": 24,
    "test_iid": 32,
    "test_optical": 24,
    "test_protocol": 24,
    "test_concurrency": 24,
}


def specs(scale: float = 1.0, seed: int = SEED) -> dict[str, list[StreamSpec]]:
    """Every split's stream specs. ``scale`` shrinks the stream counts for tests and smoke runs."""
    return {split: _specs(split, max(1, round(n * scale)), seed) for split, n in SPLITS.items()}


#: The appliance code a recording runs through (`record.py` drives the real `Engine`), so a change
#: to what the engine computes is a new dataset rather than a stale cache.
RECORDING_SOURCES = ("engine", "ingest", "store", "migrations")


def digest(all_specs: dict[str, list[StreamSpec]]) -> str:
    """SHA-256 over the specs, the generator's source and the engine code a recording runs
    through: a cache key and a provenance line."""
    h = hashlib.sha256()
    h.update(
        json.dumps(
            {k: [asdict(s) for s in v] for k, v in all_specs.items()}, sort_keys=True
        ).encode()
    )
    for path in sorted((HERE).glob("*.py")):
        if path.name in {"dataset.py", "train.py", "report.py", "evaluate.py", "verify.py"}:
            continue  # consumers of the data, not producers of it
        h.update(path.name.encode())
        h.update(path.read_bytes())
    package = HERE.parent.parent / "src" / "netcorenoc"
    for sub in RECORDING_SOURCES:
        for path in sorted((package / sub).rglob("*")):
            if path.suffix in {".py", ".sql"} and "__pycache__" not in path.parts:
                h.update(str(path.relative_to(package)).encode())
                h.update(path.read_bytes())
    return h.hexdigest()


def _one(args: tuple[StreamSpec, str]) -> str:
    spec, target = args
    path = Path(target)
    if not path.exists():
        log = record(compose(spec))
        tmp = path.with_suffix(".tmp")
        log.dump(tmp)
        tmp.replace(path)
    return target


def build(scale: float = 1.0, seed: int = SEED, workers: int | None = None) -> Path:
    """Record every stream not already cached. Returns the cache directory."""
    all_specs = specs(scale, seed)
    root = CACHE / digest(all_specs)[:16]
    jobs: list[tuple[StreamSpec, str]] = []
    for split, items in all_specs.items():
        (root / split).mkdir(parents=True, exist_ok=True)
        for spec in items:
            jobs.append((spec, str(root / split / f"{spec.name}.json.gz")))
    todo = [j for j in jobs if not Path(j[1]).exists()]
    if todo:
        with ProcessPoolExecutor(max_workers=workers or os.cpu_count() or 2) as pool:
            for done, _ in enumerate(pool.map(_one, todo, chunksize=1), start=1):
                if done % 10 == 0 or done == len(todo):
                    print(f"  recorded {done}/{len(todo)}", file=sys.stderr)
    (root / "specs.json").write_text(
        json.dumps(
            {k: [asdict(s) for s in v] for k, v in all_specs.items()}, indent=1, sort_keys=True
        )
    )
    return root


def load_split(root: Path, split: str) -> Iterator[StreamLog]:
    for path in sorted((root / split).glob("*.json.gz")):
        yield StreamLog.load(path)


@dataclass(frozen=True)
class Row:
    """One training pair: the vector, the label, the weight, and where it came from."""

    x: tuple[float, ...]
    y: int
    w: float
    stream: str
    incident: str
    family: str
    ts: float


def incident_side(acts: list[Activation], window: tuple[float, float]) -> dict[str, bool]:
    """Per incident: does its **first** activation fall inside ``window`` (fractions of the stream's
    span)? The time-ordered split is by incident, never by activation."""
    start, end = acts[0].ts, acts[-1].ts
    span = max(end - start, 1.0)
    lo, hi = start + span * window[0], start + span * window[1]
    first: dict[str, float] = {}
    for act in acts:
        first.setdefault(act.incident, act.ts)
    return {incident: lo <= ts <= hi for incident, ts in first.items()}


def training_rows(
    logs: Iterator[StreamLog] | list[StreamLog],
    *,
    per_class: int = 6,
    seed: int = SEED,
    time_window: tuple[float, float] | None = None,
) -> list[Row]:
    """Labelled pairs, **one unit of weight per activation**, sampled without bias.

    Each activation keeps up to ``per_class`` same-incident and ``per_class`` different-incident
    candidates, drawn uniformly; each kept pair is weighted by (candidates of its class) / (kept of
    its class) / (all candidates), so every activation's positive and negative mass is exactly what
    it was before sampling and the activation sums to one. ``time_window`` is a fraction of the
    stream's span, ``(0.0, 0.7)`` for the older part — the time-ordered split — and it is applied
    **per incident, by its first activation**: an incident that straddles the cut belongs wholly
    to the side it started on, so no incident trains on its early pairs and is scored on its late
    ones (`incident_side`, which `report.evaluate` uses for ``test_time`` too).
    """
    out: list[Row] = []
    for log in logs:
        rng = derived_rng("rows", log.name, seed)
        acts = log.activations()
        if not acts:
            continue
        side = incident_side(acts, time_window) if time_window is not None else None
        truth_of: dict[int, str] = {}
        for act in acts:
            if side is not None and not side[act.incident]:
                truth_of[act.alarm_id] = act.incident
                continue
            _sample(act, truth_of, rng, per_class, out, log.name)
            truth_of[act.alarm_id] = act.incident
    return out


def _sample(
    act: Activation,
    truth_of: dict[int, str],
    rng: random.Random,
    per_class: int,
    out: list[Row],
    stream: str,
) -> None:
    pos: list[tuple[float, ...]] = []
    neg: list[tuple[float, ...]] = []
    for other, _w, vec in act.candidates:
        t = truth_of.get(other)
        if t is None:
            continue
        (pos if t == act.incident else neg).append(vec)
    total = len(pos) + len(neg)
    if total == 0:
        return
    for group_, label in ((pos, 1), (neg, 0)):
        if not group_:
            continue
        kept = group_ if len(group_) <= per_class else rng.sample(group_, per_class)
        weight = (len(group_) / len(kept)) / total
        for vec in kept:
            out.append(Row(vec, label, weight, stream, act.incident, act.family, act.ts))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--scale", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--workers", type=int, default=None)
    args = parser.parse_args()
    if args.build:
        root = build(args.scale, args.seed, args.workers)
        print(root)


if __name__ == "__main__":
    main()
