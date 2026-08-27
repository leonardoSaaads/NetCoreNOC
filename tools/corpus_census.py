"""The corpus census: what the promotion gate will decide, on real data, stated in advance.

**A command rather than a quoted number.** v0.10.0 ran its census as a throwaway script and recorded
the output in a gate document; that made the figures checkable once. v0.11.0 needs more than that:
`PREREGISTRATION-0.11.0.md` §6.1 predicts the release's headline outcome from these numbers, and the
verification phase requires the census **byte-identical across two runs and two processes**. A
number that only exists inside a Markdown file cannot be re-run.

## The method, unchanged from v0.10.0 Phase 1 §4 and v0.9.0 Phase 0 §2(b)

Every `eval/corpus` scenario replayed through **one** `Engine`, one store, scenarios separated by an
hour of synthetic time; then every situation the replay formed given a verdict under the declared
mechanical policy:

    every third situation `split`, the rest `confirm`; three operators round-robin;
    every fifth label under a restricting scope.

**ONE CLOCK THROUGHOUT.** Appendix B's first trap: driving traffic at a fixture epoch while running
maintenance at wall-clock made everything appear 990 days old and produced a wrong conclusion. Every
timestamp here derives from `BASE`, including maintenance.

## Two properties that make the output evidence rather than a report

**Events go through the wire encoder and the real parser**, exactly as `tests/util.fixture_events`
does. A census built from hand-made `TrapEvent`s would be counting a different corpus from the one
`make eval` replays, and the two could drift with nothing going red.

**The counting is `Store.asserting_bag_rows`** — the judge's own expression, the one the promotion
gate reads. A census with its own query would be measuring the query. This is F46's lesson applied
to a census: every threshold counts the quantity the *server* derived.

## The control, because a zero is the most dangerous measurement here

*"Measuring nothing and concluding CLOSED"* is on this repository's record: a probe once
compared `None -> None`, observed nothing, and would have reported the finding closed. So the
headline zero is paired with a control corpus — the identical policy, plus *a `split` on a bag of
four or more members also marks its first two* — which must come back **non-zero through the same
code**.

It **exits non-zero if the control is empty**, because at that point the real corpus's zero would
be a property of the query rather than of the corpus, and printing it would be the error this
control exists to catch.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from pathlib import Path
from typing import Any

from netcorenoc.engine.dataset import incidents
from netcorenoc.engine.dataset.labels import Exclusion, LabelContext, LabelScope
from netcorenoc.events import TrapEvent
from netcorenoc.main import Engine
from netcorenoc.receiver import parse_trap
from netcorenoc.store import Store

import trap_replay

REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS = REPO_ROOT / "eval" / "corpus"

# Every timestamp derives from this. No wall clock anywhere in this file.
BASE = 1_700_000_000.0
HOUR = 3600.0
LABEL_EPOCH = BASE + 100_000.0
# Far beyond any synthetic span here, so the prune never fires: this census measures the corpus, not
# the retention policy, and a pass that silently collected labelled situations would report a
# smaller corpus with no indication why.
NO_PRUNE_DAYS = 3650.0

OPERATORS = ("alice", "bob", "carol")
SPLIT_EVERY = 3
SCOPE_EVERY = 5
CONTROL_MIN_MEMBERS = 4

# The registered floors, so the verdict this file prints is the plan's and not a fresh opinion.
ASSERTING_BAGS_FLOOR = 50
ASSERTING_INCIDENTS_FLOOR = 30


def scenario_events(path: Path, base: float) -> list[TrapEvent]:
    """Fixture -> wire encoding -> the real parser, as `tests/util.fixture_events` does."""
    data = json.loads(path.read_text(encoding="utf-8"))
    events: list[TrapEvent] = []
    for entry in sorted(data["events"], key=lambda e: float(e.get("delay", 0.0))):
        wire = trap_replay.encode_trap(entry["trap_oid"], entry.get("varbinds", []), "public", 1)
        events.append(parse_trap(entry["source"], wire, base + float(entry["delay"])))
    return events


async def _drain(engine: Engine, queue: asyncio.Queue[Any], target: int) -> None:
    """Run the engine loop until `target` events have been processed and the queue is empty."""
    task = asyncio.create_task(engine.run())
    try:
        for _ in range(20_000):
            if engine.processed >= target and queue.empty():
                break
            await asyncio.sleep(0.001)
        await asyncio.sleep(0.05)  # let the final batch commit
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def replay(store: Store) -> tuple[Engine, list[int]]:
    """Every corpus scenario through one engine, an hour of synthetic time apart."""
    queue: asyncio.Queue[Any] = asyncio.Queue()
    engine = Engine(store, queue)
    await engine.start()
    now = BASE
    for index, path in enumerate(sorted(CORPUS.glob("*.json"))):
        events = scenario_events(path, BASE + index * HOUR)
        target = engine.processed + len(events)
        for item in events:
            queue.put_nowait(item)
        await _drain(engine, queue, target)
        now = max([now, *(event.ts for event in events)])
        await engine.maintenance(now + 1.0, retention_days=NO_PRUNE_DAYS)
    await engine.maintenance(now + 2.0, retention_days=NO_PRUNE_DAYS)
    cur = await store.conn.execute("SELECT id FROM situation ORDER BY id")
    return engine, [int(row[0]) for row in await cur.fetchall()]


async def label_all(engine: Engine, store: Store, situations: list[int], *, control: bool) -> None:
    """The declared mechanical policy, applied through the real label write path."""
    for index, sid in enumerate(situations):
        verdict = "split" if index % SPLIT_EVERY == SPLIT_EVERY - 1 else "confirm"
        cur = await store.conn.execute(
            "SELECT alarm_id FROM situation_alarm WHERE situation_id=? ORDER BY alarm_id", (sid,)
        )
        bag = [int(row[0]) for row in await cur.fetchall()]

        exclusion = None
        if control and verdict == "split" and len(bag) >= CONTROL_MIN_MEMBERS:
            # THE CONTROL'S ONE ADDITION.
            exclusion = Exclusion.accept(bag[:2], None)

        label = LabelContext(
            scope=LabelScope(policy_id=None, restricted=index % SCOPE_EVERY == SCOPE_EVERY - 1),
            client=None,
            exclusion=exclusion,
            channel="organic",
        )
        async with store.lock:
            await engine.apply_feedback(
                sid,
                verdict,
                LABEL_EPOCH + index,
                principal_ref=OPERATORS[index % len(OPERATORS)],
                role="editor",
                label=label,
            )


async def census(store: Store, tag: str) -> dict[str, int]:
    """Count the corpus, print it, and return the figures the verdict is derived from."""
    cur = await store.conn.execute("SELECT COUNT(*) FROM situation")
    situations = int((await cur.fetchone())[0])  # type: ignore[index]
    cur = await store.conn.execute("SELECT verdict, COUNT(*) FROM feedback GROUP BY verdict")
    verdicts = {str(row[0]): int(row[1]) for row in await cur.fetchall()}
    cur = await store.conn.execute(
        "SELECT COALESCE(coverage,'unrecorded') AS c, COUNT(*) FROM feedback GROUP BY c ORDER BY c"
    )
    coverage = {str(row[0]): int(row[1]) for row in await cur.fetchall()}
    cur = await store.conn.execute(
        "SELECT COALESCE(SUM(excluded_reconciled),0), COALESCE(SUM(member_count),0) FROM feedback"
    )
    totals = await cur.fetchone()
    reconciled, members = int(totals[0]), int(totals[1])  # type: ignore[index]

    # Merge-aware incident identity — the same resolution the seal and the estimator use.
    cur = await store.conn.execute("SELECT id, merged_into FROM situation ORDER BY id")
    rows = [(int(r[0]), r[1]) for r in await cur.fetchall()]
    resolved = incidents.resolve_all(
        [sid for sid, _ in rows], {sid: int(m) for sid, m in rows if m is not None}
    )
    cur = await store.conn.execute("SELECT situation_id FROM feedback ORDER BY situation_id")
    labelled = [int(row[0]) for row in await cur.fetchall()]

    # THE JUDGE'S OWN CODE. Not a query written for this document.
    asserting = await store.asserting_bag_rows()
    contributions = []
    for row in asserting:
        marked = int(row["excluded_reconciled"])
        contributions.append(marked * (int(row["member_count"]) - marked))
    figures = {
        "situations": situations,
        "bags": len(labelled),
        "confirm": verdicts.get("confirm", 0),
        "split": verdicts.get("split", 0),
        "incidents": len({resolved.incident_of.get(s, s) for s in labelled}),
        "incidents_one_hop": len({resolved.one_hop_of.get(s, s) for s in labelled}),
        "unsound_chains": len(resolved.unsound_situations),
        "members": members,
        "reconciled": reconciled,
        "unknown_rows": await store.unknown_scope_rows(),
        "asserted_negative_pairs": sum(contributions),
        "max_from_one_bag": max(contributions, default=0),
        "asserting_bags": len(asserting),
        "asserting_incidents": len(
            {resolved.incident_of.get(int(row["situation_id"]), 0) for row in asserting}
        ),
    }

    print(f"===== {tag} =====")
    print(f"  situations formed          : {figures['situations']}")
    print(f"  labelled bags              : {figures['bags']}")
    split = f"{figures['confirm']} / {figures['split']}"
    print(f"  confirm / split            : {split}")
    print(f"  merge-aware incidents      : {figures['incidents']}")
    print(f"  one-hop incidents          : {figures['incidents_one_hop']}")
    print(f"  unsound chains             : {figures['unsound_chains']}")
    print(f"  coverage distribution      : {coverage}")
    marks = f"{figures['members']} / {figures['reconciled']}"
    print(f"  members / reconciled marks : {marks}")
    print(f"  unknown-population rows    : {figures['unknown_rows']}")
    print(f"  asserted_negative_pairs    : {figures['asserted_negative_pairs']}")
    print(f"  max contributed by one bag : {figures['max_from_one_bag']}")
    print(f"  ASSERTING_BAGS (floor {ASSERTING_BAGS_FLOOR})  : {figures['asserting_bags']}")
    print(
        f"  ASSERTING_INCIDENTS (fl {ASSERTING_INCIDENTS_FLOOR}): {figures['asserting_incidents']}"
    )
    return figures


async def run(*, control: bool, tag: str) -> dict[str, int]:
    store = Store(":memory:")
    await store.open()
    try:
        engine, situations = await replay(store)
        await label_all(engine, store, situations, control=control)
        return await census(store, tag)
    finally:
        await store.close()


async def main() -> int:
    real = await run(control=False, tag="THE REAL CORPUS (the declared mechanical policy)")
    print()
    control = await run(
        control=True, tag="THE CONTROL (same policy; a split of >= 4 also marks its first two)"
    )
    print()

    bags_met = real["asserting_bags"] >= ASSERTING_BAGS_FLOOR
    incidents_met = real["asserting_incidents"] >= ASSERTING_INCIDENTS_FLOOR
    print("===== what the gate will decide, stated in advance =====")
    print(
        f"  asserting_bags      {real['asserting_bags']:>6}  against a floor of "
        f"{ASSERTING_BAGS_FLOOR}  -> {'MET' if bags_met else 'UNMET'}"
    )
    print(
        f"  asserting_incidents {real['asserting_incidents']:>6}  against a floor of "
        f"{ASSERTING_INCIDENTS_FLOOR}  -> {'MET' if incidents_met else 'UNMET'}"
    )
    if not (bags_met and incidents_met):
        print("  => Trigger.FLOOR_UNMET fires. Verdict INSUFFICIENT_EVIDENCE.")
        print(
            "  => Under the plan's section 3 order (floors, then power, then seal) "
            "THE SEAL IS NOT READ."
        )
        print("  => v0.11.0's predicted query count: 0.")

    print()
    print("===== the control, which had to come back non-zero =====")
    print(
        f"  asserting_bags      {control['asserting_bags']:>6}   "
        f"(real corpus: {real['asserting_bags']})"
    )
    print(
        f"  asserted_neg_pairs  {control['asserted_negative_pairs']:>6}   "
        f"(real corpus: {real['asserted_negative_pairs']})"
    )
    if control["asserting_bags"] == 0:
        print(
            "  !! THE CONTROL IS ZERO TOO — the census code cannot see an assertion.\n"
            "  !! The real corpus's zero would be a property of the QUERY, not the corpus."
        )
        return 1
    print(
        "  The census code CAN see an assertion. The real corpus's zero is therefore a property\n"
        "  of the corpus, not of the query."
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
