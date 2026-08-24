"""What the generated network actually contains — **the measurement Gate 6 requires.**

> report, per shape, how many pairs fall within ±0.15 of the threshold, because a "pegadinha" that
> produces no near-threshold pair is a scenario with a name and no content.

That sentence is the whole reason this module exists. Six shapes with adversarial names prove
nothing; six shapes that measurably straddle the link threshold are the thing the release needs, and
the difference is only visible by scoring the pairs the correlator actually built and looking at the
distribution.

**This reads ground truth and that is legitimate here.** `PREREGISTRATION-0.14.0.md` §1: *"Ground
truth may measure the **simulator**; it may not enter any quantity the promotion gate reads."* Every
number this module prints is about the generator. None of it is reachable from `promotion.evaluate`,
and `tests/test_simulation.py` asserts that separation by parsing the tree rather than by promising.

Run it:

    python eval/simulation/measure.py --increments 2
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "eval"))
sys.path.insert(0, str(REPO_ROOT / "tools"))

from netcorenoc.events import TrapEvent  # noqa: E402
from netcorenoc.main import Engine  # noqa: E402
from netcorenoc.receiver import parse_trap  # noqa: E402
from netcorenoc.scorer_contract import LinkFeatures, LinkScore  # noqa: E402
from netcorenoc.store import Store  # noqa: E402
from simulation.generator import (  # noqa: E402
    INCREMENT_INCIDENTS,
    SHAPES,
    devices_of,
    generate,
    shape_of,
)

import trap_replay  # noqa: E402

BASE_TS = 1_700_000_000.0
# Increments are separated by more than `correlate.WINDOW_S` (120 s) so one increment's window
# cannot reach into the next. Inside an increment the incidents are concurrent and separated by NE.
INCREMENT_GAP_S = 150.0
NEAR_THRESHOLD = 0.15
NO_PRUNE_DAYS = 3650.0


class ScoreRecorder:
    """A `LinkScorer` that delegates and records `(device_a, device_b, score, linked)` per pair."""

    scorer_id = "simulation-recorder"
    contract_version = "1.0"

    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self.rows: list[tuple[float, bool]] = []
        self.pending: LinkFeatures | None = None

    def score(self, features: LinkFeatures) -> LinkScore:
        result: LinkScore = self.delegate.score(features)
        self.rows.append((result.score, result.linked))
        return result

    def params_fingerprint(self) -> str:
        return "simulation-recorder"


def _events(increment: int) -> list[TrapEvent]:
    """One increment through the wire encoder and the real parser, at a fixture epoch."""
    document = generate(increment)
    base = BASE_TS + increment * INCREMENT_GAP_S
    out: list[TrapEvent] = []
    for entry in sorted(document["events"], key=lambda e: float(e["delay"])):
        wire = trap_replay.encode_trap(entry["trap_oid"], entry["varbinds"], "public", 1)
        out.append(parse_trap(entry["source"], wire, base + float(entry["delay"])))
    return out


async def _drive(engine: Engine, queue: asyncio.Queue[Any], events: list[TrapEvent]) -> None:
    target = engine.processed + len(events)
    for item in events:
        queue.put_nowait(item)
    task = asyncio.create_task(engine.run())
    try:
        for _ in range(200_000):
            if engine.processed >= target and queue.empty():
                break
            await asyncio.sleep(0.001)
        await asyncio.sleep(0.05)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def measure(increments: int) -> dict[str, Any]:
    """Replay `increments` increments and report what the network contains.

    One engine, one store, one clock derived from `BASE_TS` — Appendix B's first trap, and the
    reason `tools/corpus_census.py` says the same thing at the top of its own file.
    """
    store = Store(":memory:")
    await store.open()
    try:
        queue: asyncio.Queue[Any] = asyncio.Queue()
        engine = Engine(store, queue)
        await engine.start()

        device_shape: dict[str, str] = {}
        for increment in range(increments):
            for device, incident in devices_of(increment).items():
                device_shape[device] = shape_of(incident)

        # A per-pair recorder that also knows which shape each side belongs to. The correlator hands
        # `LinkFeatures`, which carries device ids rather than addresses, so the join is made
        # afterwards over the alarm table — the ids are stable within one store.
        per_shape: dict[str, Counter[str]] = defaultdict(Counter)
        near_examples: dict[str, list[float]] = defaultdict(list)

        recorder = ScoreRecorder(engine.correlator.scorer.active)
        engine.correlator.set_scorer(recorder)

        now = BASE_TS
        for increment in range(increments):
            events = _events(increment)
            await _drive(engine, queue, events)
            now = max([now, *(event.ts for event in events)])
            await engine.maintenance(now + 1.0, retention_days=NO_PRUNE_DAYS)

        # Re-score every captured pair with the shape join, from the persisted dataset rows: those
        # carry the features the champion actually saw at decision time, which is the whole reason
        # `capture` exists (re-deriving them later would read a learner whose masses have moved).
        cursor = await store.conn.execute(
            "SELECT p.delta_t_s, p.class_affinity, p.entity_affinity, p.incumbent_linked,"
            "       da.ip AS dev_a, db.ip AS dev_b "
            "FROM dataset_pair p "
            "JOIN alarm a ON a.id = p.alarm_a JOIN device da ON da.id = a.device_id "
            "JOIN alarm b ON b.id = p.alarm_b JOIN device db ON db.id = b.device_id"
        )
        scorer = engine.correlator.scorer.active
        pairs = 0
        for row in await cursor.fetchall():
            delta, affinity, entity, linked, dev_a, dev_b = row
            shape_a = device_shape.get(str(dev_a))
            shape_b = device_shape.get(str(dev_b))
            if shape_a is None or shape_b is None:
                continue
            result = scorer.score(
                LinkFeatures(
                    delta_t_s=float(delta),
                    class_i=0,
                    class_j=1,
                    class_affinity=float(affinity),
                    ne_i=0,
                    ne_j=1 if dev_a != dev_b else 0,
                    entity_affinity=float(entity),
                )
            )
            pairs += 1
            for shape in {shape_a, shape_b}:
                bucket = per_shape[shape]
                bucket["pairs"] += 1
                bucket["linked"] += 1 if int(linked) else 0
                if abs(result.score - result.threshold) <= NEAR_THRESHOLD:
                    bucket["near"] += 1
                    if len(near_examples[shape]) < 5:
                        near_examples[shape].append(round(result.score, 4))

        cursor = await store.conn.execute("SELECT COUNT(*) FROM situation")
        situations = int((await cursor.fetchone())[0])  # type: ignore[index]
        cursor = await store.conn.execute("SELECT id, merged_into FROM situation ORDER BY id")
        edges = {int(r[0]): int(r[1]) for r in await cursor.fetchall() if r[1] is not None}
        merged = len(edges)
        # **The chain length, and it is the point of the merge-chain shape.** `DATA-LINEAGE.md` §4
        # records that no corpus holds a merge edge at all, so nothing before this release could
        # have verified that incident identity resolves TRANSITIVELY. A chain of length 1 is a
        # single `COALESCE`; a chain of length >= 2 is the case `incidents.resolve_all` exists for.
        longest = 0
        for start in edges:
            depth, node, seen = 0, start, set()
            while node in edges and node not in seen:
                seen.add(node)
                node = edges[node]
                depth += 1
            longest = max(longest, depth)
        cursor = await store.conn.execute("SELECT COUNT(*) FROM link")
        links = int((await cursor.fetchone())[0])  # type: ignore[index]
        cursor = await store.conn.execute("SELECT COUNT(*) FROM alarm")
        alarms = int((await cursor.fetchone())[0])  # type: ignore[index]

        return {
            "increments": increments,
            "incidents": increments * INCREMENT_INCIDENTS,
            "alarms": alarms,
            "situations": situations,
            "merged_situations": merged,
            "longest_merge_chain": longest,
            "links": links,
            "evaluated_pairs": pairs,
            "per_shape": {shape: dict(per_shape[shape]) for shape in SHAPES},
            "near_examples": {shape: near_examples[shape] for shape in SHAPES},
        }
    finally:
        await store.close()


def render(report: dict[str, Any]) -> str:
    """The gate's table. Every column is a count; nothing here is an average."""
    chain_note = (
        "(>= 2: transitive resolution is exercised)"
        if report["longest_merge_chain"] >= 2
        else "(< 2: NO transitive case)"
    )
    lines = [
        "===== what the generated network contains =====",
        f"  increments               : {report['increments']}",
        f"  incidents                : {report['incidents']}",
        f"  alarms                   : {report['alarms']}",
        f"  situations formed        : {report['situations']}",
        f"  of which merged away     : {report['merged_situations']}",
        f"  longest merge chain      : {report['longest_merge_chain']}  {chain_note}",
        f"  links written            : {report['links']}",
        f"  pairs evaluated          : {report['evaluated_pairs']}",
        "",
        f"===== pairs within +/-{NEAR_THRESHOLD} of the threshold, per shape =====",
        f"  {'shape':<24}{'pairs':>10}{'near':>8}{'near %':>9}{'linked':>9}",
    ]
    for shape in SHAPES:
        bucket = report["per_shape"].get(shape, {})
        pairs = bucket.get("pairs", 0)
        near = bucket.get("near", 0)
        share = f"{100.0 * near / pairs:.1f}" if pairs else "n/a"
        lines.append(f"  {shape:<24}{pairs:>10}{near:>8}{share:>9}{bucket.get('linked', 0):>9}")
    lines.append("")
    lines.append("  example near-threshold scores:")
    for shape in SHAPES:
        lines.append(f"    {shape:<24}{report['near_examples'].get(shape, [])}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--increments", type=int, default=2)
    args = parser.parse_args()
    report = asyncio.run(measure(args.increments))
    print(render(report))  # noqa: T201
    if report["longest_merge_chain"] < 2:
        print(  # noqa: T201
            "\n  !! the longest merge chain is "
            f"{report['longest_merge_chain']}, below the 2 the plan's §5.1 registers.\n"
            "  !! transitive incident resolution is not exercised by this corpus."
        )
        return 1
    empty = [shape for shape in SHAPES if not report["per_shape"].get(shape, {}).get("near", 0)]
    if empty:
        print(  # noqa: T201
            "\n  !! shapes with NO near-threshold pair: "
            + ", ".join(empty)
            + "\n  !! a scenario with a name and no content is not an adversarial shape."
        )
        return 1
    print("\n  Every registered shape produced near-threshold pairs.")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
