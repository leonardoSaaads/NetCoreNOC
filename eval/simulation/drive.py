"""The chain's **ten-increment census**, in one process, on its own database.

`PREREGISTRATION-0.14.0.md` §5.3's loop:

> 1. Boot a real appliance on an **empty** database, real UDP, migrations applied at boot.
> 2. Replay the generated network. Situations form.
> 3. **Label through `POST /api/situations/{sid}/feedback`** — the route the console calls, never by
>    writing to the store.
> 4. Shadow mode samples; the challenger trains in the maintenance pass.
> 5. Run the census, the judge and the promotion gate.
> 6. If the verdict is `INSUFFICIENT_EVIDENCE`, report which floor and by how much, generate more of
>    exactly what is missing, and repeat.
> 7. When the floors clear and the gate returns a verdict, an admin approves a promotion.
> 8. The champion changes. **Verify by observation** that subsequent situations carry the new
>    model's provenance.

## What this module is, said plainly, because the difference matters

`drive_http.py` is steps 1 and 3 **literally**: `python -m netcorenoc.main` in a subprocess, traps
over a UDP socket, labels over TCP through the route, with a bearer token and an audit row. This
module is the same loop **in process** — the same `parse_trap` on the same wire bytes, the same
`engine.apply_feedback` the route calls, the same `Exclusion` reconciliation — and it exists because
ten increments over a real socket is half an hour of wall clock: the receiver stamps a trap on
**arrival**, so `spread_beyond_tau`'s 95-second gap is 95 seconds of real time and no time scale can
compress it without changing the registered shape.

So the ten-increment census is this module's, the real-surface witness is `drive_http.py`'s, and
`docs/gates/v0.14.0-phase-7.md` §2 **compares them** rather than asserting they agree. What this
path does not exercise is the socket, the allowlist, the community tagging, authentication, RBAC,
request validation, scope resolution and the audit row.

## The two prohibitions, and where each is enforced

**The simulator's ground truth never enters the promotion path** (§1). `labelling.py` reads it —
that is how a simulated operator decides what to mark — but what it produces is a *label*, and
nothing downstream of a label can see a `situation_key`. `tests/test_simulation.py` asserts that
separation by parsing every runtime module, not by promising it.

**Labelling goes through `apply_feedback`, never a write to the store** (§5.2). A seeded row would
bypass the `source = 'server'` reconciliation that is F46's repair and F48's demonstration — the
label would carry an `excluded_count` nobody derived, and every floor counted from it would be
counting the client's word.

## The stopping rule is the plan's and this module cannot soften it

Ten increments, and *"the second branch is a successful gate outcome and the report says so."* There
is no configuration here that raises that ceiling: `MAX_INCREMENTS` is a module constant and the
loop returns its shortfall rather than continuing. **A loop that cannot stop without success is a
loop that will manufacture one.**

Run it:

    python eval/simulation/drive.py --db /tmp/demo.db --kinds tree,forest,gradient_boosting
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "eval"))
sys.path.insert(0, str(REPO_ROOT / "tools"))

from netcorenoc import boosting, forest, model_version, scoring, tree  # noqa: E402
from netcorenoc.api.routes_promotion import (  # noqa: E402
    ASSERTING_BAGS_FLOOR,
    ASSERTING_INCIDENTS_FLOOR,
)
from netcorenoc.events import TrapEvent  # noqa: E402
from netcorenoc.main import Engine  # noqa: E402
from netcorenoc.receiver import parse_trap  # noqa: E402
from netcorenoc.store import Store  # noqa: E402
from netcorenoc.training import TrainingRow  # noqa: E402
from simulation import diagnose  # noqa: E402
from simulation.generator import INCREMENT_INCIDENTS, SEED, generate  # noqa: E402
from simulation.labelling import label_increment, truth_of  # noqa: E402

import trap_replay  # noqa: E402

BASE_TS = 1_700_000_000.0
INCREMENT_GAP_S = 150.0
NO_PRUNE_DAYS = 3650.0

# §5.3, and it is a ceiling rather than a target.
MAX_INCREMENTS = 10

# The hyperparameters each kind is fitted with. Mechanism-class settings; they are recorded in
# `params_document` and therefore in `params_hash`, which is what `UI-0.13-DRAFT.md` §8 requires.
HYPERS: dict[str, dict[str, Any]] = {
    "tree": {"max_depth": 4, "min_samples_leaf": 20, "criterion": "gini", "threshold": 0.5},
    "forest": {
        "n_estimators": 6,
        "max_depth": 4,
        "min_samples_leaf": 20,
        "mtry": 3,
        "seed": SEED,
        "threshold": 0.5,
    },
    "gradient_boosting": {
        "n_rounds": 8,
        "learning_rate": 0.2,
        "max_depth": 3,
        "min_samples_leaf": 20,
        "threshold": 0.5,
    },
}


def _events(increment: int) -> list[TrapEvent]:
    """One increment through the wire encoder and the real parser."""
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


async def training_rows(store: Store) -> list[TrainingRow]:
    """The labelled corpus as training rows, under derivation policy **A**.

    `training.derive` is the one implementation and this calls it, so the rows a kind is fitted on
    are the rows the logistic challenger would have been fitted on — the comparison is between
    models, not between two ways of building a dataset.
    """
    from netcorenoc.census import resolve_identity
    from netcorenoc.shadow import labelled_pair
    from netcorenoc.training import derive

    # **`resolve_identity` first, and it is not optional.** It is what puts `incident` on each pair
    # row, and `labelled_pair` reads that key: a `KeyError` here was the first version of this
    # function, which had skipped the call. `shadow.Shadow.run` takes exactly these four steps in
    # exactly this order, so the rows a kind is fitted on are the rows the production path fits on.
    bags = await store.labelled_bags()
    raw = await store.labelled_pairs()
    await resolve_identity(store, bags, raw)
    derived, _diagnostics = derive([labelled_pair(row) for row in raw], "A")
    return derived


async def fit_kind(kind: str, rows: list[TrainingRow]) -> dict[str, Any]:
    """Fit one kind at its registered hyperparameters and return its `params_document` payload."""
    module = {"tree": tree, "forest": forest, "gradient_boosting": boosting}[kind]
    return await module.fit_document(rows, **HYPERS[kind])  # type: ignore[no-any-return]


def params_hash_of(kind: str, payload: dict[str, Any]) -> str:
    """The artefact's fingerprint, over the **canonical** text and not over a dict.

    `model_version.canonical_object` is the serialisation the store holds, so a hash computed over
    any other rendering of the same numbers would name a document that is not the one registered.
    The three new kinds are not flat mappings of floats — a tree is a list of nodes — so it is
    `canonical_object` here and never `canonical_document`.
    """
    return model_version.params_hash(
        kind, scoring.CONTRACT_VERSION, model_version.canonical_object(payload)
    )


async def census(store: Store) -> dict[str, int]:
    """The floors, with the judge's own expression. **Never a query written for a report.**"""
    from netcorenoc import incidents

    rows = await store.asserting_bag_rows()
    cursor = await store.conn.execute("SELECT id, merged_into FROM situation ORDER BY id")
    situations = [(int(r[0]), r[1]) for r in await cursor.fetchall()]
    resolved = incidents.resolve_all(
        [sid for sid, _ in situations], {sid: int(m) for sid, m in situations if m is not None}
    )
    cursor = await store.conn.execute("SELECT situation_id, verdict FROM feedback")
    labelled = [(int(r[0]), str(r[1])) for r in await cursor.fetchall()]
    cursor = await store.conn.execute(
        "SELECT COUNT(DISTINCT principal_ref) FROM feedback WHERE principal_ref IS NOT NULL"
    )
    operators = int((await cursor.fetchone())[0])  # type: ignore[index]
    return {
        "bags": len(labelled),
        "split_bags": sum(1 for _s, v in labelled if v == "split"),
        "confirm_bags": sum(1 for _s, v in labelled if v == "confirm"),
        "incidents": len({resolved.incident_of.get(s, s) for s, _v in labelled}),
        "operators": operators,
        "asserting_bags": len(rows),
        "asserting_incidents": len(
            {resolved.incident_of.get(int(r["situation_id"]), 0) for r in rows}
        ),
    }


# **The server's own floors, imported rather than transcribed.** `census()` above says "never a
# query written for a report", and a floor written for a report is the same mistake one line on: a
# loop that carried its own copy of 50 and 30 would keep reporting success after the server's
# `_derived_inputs` had moved. `routes_promotion` is where the verdict reads them, so it is where
# these come from.
FLOORS = {
    "asserting_bags": ASSERTING_BAGS_FLOOR,
    "asserting_incidents": ASSERTING_INCIDENTS_FLOOR,
}


def shortfall(counted: dict[str, int]) -> dict[str, int]:
    """Per unmet floor, by how much. §5.3: the loop reports this after every increment."""
    return {name: floor - counted[name] for name, floor in FLOORS.items() if counted[name] < floor}


async def run(db_path: str, kinds: tuple[str, ...], verbose: bool = True) -> dict[str, Any]:
    """The loop. Returns the report; **stops at `MAX_INCREMENTS` whether or not it succeeded.**"""
    store = Store(db_path)
    await store.open()
    history: list[dict[str, Any]] = []
    try:
        queue: asyncio.Queue[Any] = asyncio.Queue()
        engine = Engine(store, queue)
        await engine.start()
        now = BASE_TS
        counted: dict[str, int] = {}
        for increment in range(MAX_INCREMENTS):
            events = _events(increment)
            await _drive(engine, queue, events)
            now = max([now, *(event.ts for event in events)])
            await engine.maintenance(now + 1.0, retention_days=NO_PRUNE_DAYS)
            labelled = await label_increment(
                engine, store, truth_of(increment), now + 100.0, increment * INCREMENT_INCIDENTS
            )
            await store.commit()
            counted = await census(store)
            missing = shortfall(counted)
            history.append(
                {
                    "increment": increment,
                    "labelled": labelled,
                    "census": counted,
                    "shortfall": missing,
                }
            )
            if verbose:
                print(  # noqa: T201
                    f"  increment {increment:>2}: bags {counted['bags']:>4} "
                    f"asserting {counted['asserting_bags']:>4}/{FLOORS['asserting_bags']} "
                    f"incidents {counted['asserting_incidents']:>4}/"
                    f"{FLOORS['asserting_incidents']}"
                    + (f"   shortfall {missing}" if missing else "   FLOORS MET")
                )
            if not missing:
                break
        # §9's "additional observations": *why* the census reads what it reads. Computed after the
        # loop has stopped, from the store, and read by nothing that decides anything.
        done = len(history)
        # The union over every increment run, not the last one's: the census spans every situation
        # the store holds, and a truth map covering one increment would report every older bag as
        # unresolved — which `bag_census` counts as its own key and would show as spurious mixing.
        all_truth: dict[tuple[str, str], str] = {}
        for increment in range(done):
            all_truth.update(truth_of(increment))
        bags = await diagnose.bag_census(store, all_truth)
        links = await diagnose.cross_incident_links(store, done)
        mass = await diagnose.storm_mass(store)
        if verbose:
            print("\n" + diagnose.render(bags, links, mass))  # noqa: T201

        # **Step 4, and it runs whatever the floors did.** §8.3: *"the release ships the three kinds
        # without the end-to-end proof"* — so a kind that cannot be fitted on the corpus this loop
        # produced is a fact the gate has to state, and the only way to know is to fit it. The
        # `params_hash` is what a registration would carry, and `degenerate` is the per-kind rule of
        # §2 answering for itself rather than being asserted here.
        rows = await training_rows(store)
        fits: dict[str, dict[str, Any]] = {}
        if verbose:
            print(f"\n  fitted on {len(rows)} training rows, policy A:")  # noqa: T201
        for one in kinds:
            fitted = await fit_kind(one, rows)
            fits[one] = {"params_hash": params_hash_of(one, fitted), "params_document": fitted}
            if verbose:
                print(f"    {one:<20}{fits[one]['params_hash']}")  # noqa: T201
        return {
            "kinds": list(kinds),
            "training_rows": len(rows),
            "fits": fits,
            "increments": done,
            "history": history,
            "census": counted,
            "shortfall": shortfall(counted),
            "floors_met": not shortfall(counted),
            "observations": {
                "bags_formed": len(bags.sizes),
                "pure_bags": bags.pure,
                "mixed_bags": bags.mixed,
                "largest_bag": bags.largest,
                "largest_bag_truth_keys": max(bags.truth_keys, default=0),
                "links": links,
                "ne_mass": mass,
            },
        }
    finally:
        await store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="/tmp/netcorenoc-demo.db")  # nosec B108
    parser.add_argument(
        "--kinds",
        default=",".join(sorted(HYPERS)),
        help="comma-separated kinds to fit on the corpus the loop produced",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    Path(args.db).unlink(missing_ok=True)
    print(f"===== the chain, on an EMPTY database at {args.db} =====")  # noqa: T201
    kinds = tuple(name.strip() for name in args.kinds.split(",") if name.strip())
    unknown = [name for name in kinds if name not in HYPERS]
    if unknown:
        print(f"unknown kind(s): {unknown}; known: {sorted(HYPERS)}", file=sys.stderr)  # noqa: T201
        return 2
    report = asyncio.run(run(args.db, kinds))
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))  # noqa: T201
    if report["floors_met"]:
        print(f"\n  FLOORS MET after {report['increments']} increment(s).")  # noqa: T201
        return 0
    print(  # noqa: T201
        f"\n  {MAX_INCREMENTS} increments without every floor met. Shortfall: "
        f"{report['shortfall']}.\n"
        "  PREREGISTRATION-0.14.0.md §5.3: this is a SUCCESSFUL gate outcome, and §8.3 registers\n"
        "  it in advance. The demonstration is incomplete and the release says so."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
