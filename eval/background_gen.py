"""Regenerate the registered attribution background set, and check the shipped one against it.

`PREREGISTRATION-0.14.0.md` §3 fixes the set **before any model existed**:

> the feature vectors of the `MAX_CANDIDATES`-bounded evaluation set of the fixed eval corpus,
> deduplicated and sorted, from which a deterministic sample of at most 256 rows is drawn by taking
> every `k`-th row.

`src/netcorenoc/background.py` ships those rows as a constant, because `model_version` must stay
pure and `eval/` is not in the wheel. **A constant that nothing can re-derive is a number somebody
typed**, so this is the command that re-derives it — beside `corpus_gen.py`, which is the same kind
of thing for the same reason: a deterministic generator whose output is checked rather than trusted.

    python eval/background_gen.py            # print the module's data block
    python eval/background_gen.py --check    # compare against the shipped constant, exit non-zero

It is **not** a test. Regenerating means replaying all ten scenarios through a real engine, which is
about a minute; `tests/test_attribution.py` asserts the shape and this asserts the provenance.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import math
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "tools"))

from netcorenoc.engine.correlate.scorer_contract import (  # noqa: E402
    LinkFeatures,
    LinkScore,
    feature_vector,
)
from netcorenoc.events import TrapEvent  # noqa: E402
from netcorenoc.main import Engine  # noqa: E402
from netcorenoc.receiver import parse_trap  # noqa: E402
from netcorenoc.store import Store  # noqa: E402

import trap_replay  # noqa: E402

BASE_TS = 1_700_000_000.0
HOUR = 3600.0
CORPUS_DIR = REPO_ROOT / "eval" / "corpus"
MAX_BACKGROUND = 256
NO_PRUNE_DAYS = 3650.0


def scenario_events(path: Path, base: float) -> list[TrapEvent]:
    """Fixture -> wire encoding -> the real parser, as `tools/corpus_census.py` does."""
    data = json.loads(path.read_text(encoding="utf-8"))
    events: list[TrapEvent] = []
    for entry in sorted(data["events"], key=lambda e: float(e.get("delay", 0.0))):
        wire = trap_replay.encode_trap(entry["trap_oid"], entry.get("varbinds", []), "public", 1)
        events.append(parse_trap(entry["source"], wire, base + float(entry["delay"])))
    return events


class Recorder:
    """A `LinkScorer` that delegates and records every `LinkFeatures` the correlator built."""

    scorer_id = "background-recorder"
    contract_version = "1.0"

    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self.seen: list[LinkFeatures] = []

    def score(self, features: LinkFeatures) -> LinkScore:
        self.seen.append(features)
        return self.delegate.score(features)  # type: ignore[no-any-return]

    def params_fingerprint(self) -> str:
        return "background-recorder"


async def _drive(engine: Engine, queue: asyncio.Queue[Any], events: list[TrapEvent]) -> None:
    target = engine.processed + len(events)
    for item in events:
        queue.put_nowait(item)
    task = asyncio.create_task(engine.run())
    try:
        for _ in range(60_000):
            if engine.processed >= target and queue.empty():
                break
            await asyncio.sleep(0.001)
        await asyncio.sleep(0.05)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def collect() -> list[LinkFeatures]:
    """Every `LinkFeatures` the correlator evaluated over the whole corpus, in one engine."""
    store = Store(":memory:")
    await store.open()
    try:
        queue: asyncio.Queue[Any] = asyncio.Queue()
        engine = Engine(store, queue)
        await engine.start()
        recorder = Recorder(engine.correlator.scorer.active)
        engine.correlator.set_scorer(recorder)
        now = BASE_TS
        for index, path in enumerate(sorted(CORPUS_DIR.glob("*.json"))):
            events = scenario_events(path, BASE_TS + index * HOUR)
            await _drive(engine, queue, events)
            now = max([now, *(event.ts for event in events)])
            await engine.maintenance(now + 1.0, retention_days=NO_PRUNE_DAYS)
        return recorder.seen
    finally:
        await store.close()


async def regenerate() -> tuple[list[tuple[float, float, float]], int, int, int]:
    """`(rows, evaluated pairs, distinct vectors, stride)` — the plan's §3 rule, applied."""
    seen = await collect()
    distinct = {feature_vector(f.delta_t_s, f.class_affinity, f.entity_affinity) for f in seen}
    ordered = sorted(distinct)
    stride = max(1, math.ceil(len(ordered) / MAX_BACKGROUND))
    return ordered[::stride], len(seen), len(ordered), stride


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="compare against the shipped constant")
    args = parser.parse_args()
    rows, pairs, distinct, stride = asyncio.run(regenerate())

    if not args.check:
        print(f"# evaluated pairs      : {pairs}", file=sys.stderr)  # noqa: T201
        print(f"# distinct vectors     : {distinct}", file=sys.stderr)  # noqa: T201
        print(f"# stride k             : {stride}", file=sys.stderr)  # noqa: T201
        print("BACKGROUND: tuple[tuple[float, float, float], ...] = (")  # noqa: T201
        for row in rows:
            print(f"    ({row[0]!r}, {row[1]!r}, {row[2]!r}),")  # noqa: T201
        print(")")  # noqa: T201
        return 0

    from netcorenoc.engine.model import background

    shipped_pairs = background.CORPUS_EVALUATED_PAIRS
    shipped_distinct = background.CORPUS_DISTINCT_VECTORS
    print(f"  evaluated pairs   regenerated {pairs:>8}   shipped {shipped_pairs:>8}")  # noqa: T201
    print(f"  distinct vectors  regenerated {distinct:>8}   shipped {shipped_distinct:>8}")  # noqa: T201
    shipped_stride = background.BACKGROUND_STRIDE
    shipped_rows = len(background.BACKGROUND)
    print(f"  stride k          regenerated {stride:>8}   shipped {shipped_stride:>8}")  # noqa: T201
    print(f"  rows              regenerated {len(rows):>8}   shipped {shipped_rows:>8}")  # noqa: T201
    problems = []
    if pairs != background.CORPUS_EVALUATED_PAIRS:
        problems.append("evaluated pairs")
    if distinct != background.CORPUS_DISTINCT_VECTORS:
        problems.append("distinct vectors")
    if stride != background.BACKGROUND_STRIDE:
        problems.append("stride")
    if tuple(rows) != background.BACKGROUND:
        problems.append("rows")
    if problems:
        print(f"  MISMATCH in: {', '.join(problems)}")  # noqa: T201
        return 1
    print("  The shipped background set IS the corpus's, by the rule the plan registered.")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
