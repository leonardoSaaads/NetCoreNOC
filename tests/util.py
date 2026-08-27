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
