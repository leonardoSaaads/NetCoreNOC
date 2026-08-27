"""Shared test helpers."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from netcorenoc.events import TrapEvent, Varbind
from netcorenoc.main import Engine
from netcorenoc.receiver import QueueItem, parse_trap

import trap_replay

FIXTURES = Path(__file__).parent / "fixtures"
CORPUS = Path(__file__).resolve().parent.parent / "eval" / "corpus"
CIENA_TRAP = "1.3.6.1.4.1.1271.2.1.1"
HUAWEI_TRAP = "1.3.6.1.4.1.2011.5.104.1"

#: The scenarios the suite replays that also exist, labelled, in ``eval/corpus/``. Until v0.15.0
#: each was ALSO stored under ``tests/fixtures/`` — the same event stream twice, in opposite roles:
#: ``eval/corpus_gen.py`` generated the corpus from the fixture, and the fixture was exactly the
#: corpus with ``description`` removed and ``truth`` dropped from every event. Nothing compared the
#: two copies, and different gates replayed them (this suite the fixture, ``make eval`` the corpus),
#: so an edit to one was invisible to the other until somebody ran ``make corpus``. #205.
DERIVED_SCENARIOS = frozenset(
    {"background_noise.json", "fiber_cut.json", "flapping_noise.json", "olt_storm.json"}
)


def scenario(name: str) -> dict[str, Any]:
    """One labelled corpus scenario, with its labels removed — the unlabelled stream a replay sees.

    The strip is two halves and BOTH are load-bearing, which is asserted rather than assumed by
    ``test_scenarios.py::test_each_half_of_the_scenario_strip_is_load_bearing``: ``description``
    is a corpus-only key, and ``truth`` is the ground truth the engine must never be shown. A
    loader that dropped only one of them would hand the engine the answer.

    Returns a fresh document every call: a consumer that mutated a cached one would poison every
    later test in the process, which is the failure mode a module-level cache would introduce.
    """
    if name not in DERIVED_SCENARIOS:
        raise KeyError(f"{name!r} is not one of {sorted(DERIVED_SCENARIOS)}")
    return strip_labels(json.loads((CORPUS / name).read_text()))


def strip_labels(document: dict[str, Any]) -> dict[str, Any]:
    """``eval/corpus`` document → the unlabelled stream, as one function so that every caller
    strips the same two things."""
    out = {key: value for key, value in document.items() if key != "description"}
    out["events"] = [
        {key: value for key, value in event.items() if key != "truth"}
        for event in document["events"]
    ]
    return out


def event(
    device: str = "10.0.0.1",
    trap_oid: str = CIENA_TRAP,
    instance: str = "",
    ts: float | None = None,
    varbinds: list[Varbind] | None = None,
) -> TrapEvent:
    return TrapEvent(
        device=device,
        trap_oid=trap_oid,
        instance=instance,
        ts=time.time() if ts is None else ts,
        varbinds=varbinds or [],
    )


def fixture_events(name: str, base_ts: float) -> list[TrapEvent]:
    """Scenario → wire encoding → the real parser, with synthetic timestamps."""
    data = scenario(name)
    events = []
    for entry in sorted(data["events"], key=lambda e: float(e.get("delay", 0.0))):
        wire = trap_replay.encode_trap(entry["trap_oid"], entry.get("varbinds", []), "public", 1)
        events.append(parse_trap(entry["source"], wire, base_ts + float(entry["delay"])))
    return events


async def eventually(check: Callable[[], Awaitable[bool]], timeout: float = 5.0) -> None:
    """Poll an async condition until true or fail the test."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await check():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition not met within timeout")


async def run_engine_until(
    engine: Engine, queue: asyncio.Queue[QueueItem], count: int, timeout: float = 20.0
) -> None:
    """Run the engine loop until `count` events are processed and the queue drains."""
    task = asyncio.create_task(engine.run())

    async def done() -> bool:
        return engine.processed >= count and queue.empty()

    try:
        await eventually(done, timeout=timeout)
        await asyncio.sleep(0.05)  # let the final batch commit
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def drive(engine: Engine, queue: asyncio.Queue[QueueItem], events: list[TrapEvent]) -> None:
    """Feed events through the engine as one replay."""
    target = engine.processed + len(events)
    for item in events:
        queue.put_nowait(item)
    await run_engine_until(engine, queue, target)


def module_path(name: str) -> Path:
    """A runtime module's file, found by its **basename** anywhere under `src/netcorenoc`.

    v0.15.1 moved 56 modules into layer and domain directories, and several guards name the ones
    they cover as a list of basenames — which is what those lists are *about*: `shadow_render.py`
    is a module, not a location. Resolving the basename keeps each list correct across the move
    and across the next one.

    **It raises rather than returning None**, and that is the point. Every one of those guards
    read `PKG / name` and skipped with `if not path.exists(): continue`, so the move would have
    left them green while checking nothing — the failure mode that is worse than a red test
    because nobody looks at it. Ambiguity raises too: two modules with one basename would make
    "which file did this guard read?" unanswerable.

    `store/` and `api/` are **excluded**, because they are packages with their own naming space and
    always were: `store/promotion.py` is the SQL behind `engine/evaluation/promotion.py`, and
    `store/shadow.py` behind `engine/evaluation/shadow.py`. Every caller of this helper means the
    engine-side module; a caller that means the store one names it by path, as they already do.
    """
    pkg = Path(__file__).resolve().parent.parent / "src" / "netcorenoc"
    found = sorted(
        p
        for p in pkg.rglob(name)
        if p.is_file() and p.relative_to(pkg).parts[0] not in ("store", "api")
    )
    if len(found) != 1:
        raise AssertionError(
            f"{name!r} resolves to {len(found)} file(s) under src/netcorenoc: "
            f"{[str(p.relative_to(pkg)) for p in found]}. A guard that names a module which does "
            "not exist is a guard that checks nothing; a name that matches two is a guard nobody "
            "can read."
        )
    return found[0]


def imported_modules(path: Path) -> set[str]:
    """Every `netcorenoc` module name a file imports, by **basename**, read with `ast`.

    Both halves of a dotted path count, because both forms name the same module after v0.15.1:
    `from netcorenoc.engine.dataset import seal` names it as an alias, and
    `from netcorenoc.engine.dataset.seal import summary` names it as the last component. A reader
    that took `node.module.split(".")[1]` — which is what the guards here did while the package was
    flat — now yields `engine` for both, and a *"this module must not import the seal"* guard built
    on it would go on passing while checking nothing. Its control is what caught that.

    Deliberately a **superset**: an alias that is a function rather than a submodule is included
    too. Every caller asks "is X reachable from here", so erring wide errs strict.
    """
    import ast

    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "netcorenoc" or node.module.startswith("netcorenoc."):
                found.update(alias.name for alias in node.names)
                found.add(node.module.rsplit(".", 1)[-1])
        elif isinstance(node, ast.Import):
            found.update(
                alias.name.rsplit(".", 1)[-1]
                for alias in node.names
                if alias.name.startswith("netcorenoc.")
            )
    found.discard("netcorenoc")
    return found
