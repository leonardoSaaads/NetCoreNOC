"""Shared test helpers."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from netcorenoc.events import TrapEvent, Varbind
from netcorenoc.main import Engine
from netcorenoc.receiver import QueueItem, parse_trap

import trap_replay

FIXTURES = Path(__file__).parent / "fixtures"
CIENA_TRAP = "1.3.6.1.4.1.1271.2.1.1"
HUAWEI_TRAP = "1.3.6.1.4.1.2011.5.104.1"


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
    """Fixture → wire encoding → the real parser, with synthetic timestamps."""
    data = json.loads((FIXTURES / name).read_text())
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
