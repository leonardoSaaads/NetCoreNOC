"""C.3 (security-event correlation, P0) and C.2 (network-fault breadth, P1), driven through the
real ingest path via the declarative DSL. These are engine-driven assertions rather than scored
eval-corpus additions (DECISIONS #37) so the frozen eval baseline stays intact.

Security events reported over SNMP (authenticationFailure and vendor login-failure traps) are
ordinary alarms: they flow through the same engine and must correlate like any other trap.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from netcorenoc.ingest.events import TrapEvent
from netcorenoc.ingest.receiver import QueueItem, parse_trap
from netcorenoc.main import Engine
from netcorenoc.store import Store

import trap_replay
import util

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))
import scenario_dsl

BASE = 1_700_000_000.0


def _events(doc: dict[str, Any], base_ts: float) -> list[TrapEvent]:
    """DSL doc -> wire encoding -> the real parser (exactly the corpus/replay path)."""
    out: list[TrapEvent] = []
    for event in sorted(doc["events"], key=lambda e: float(e["delay"])):
        wire = trap_replay.encode_trap(event["trap_oid"], event["varbinds"], "public", 1)
        out.append(parse_trap(event["source"], wire, base_ts + float(event["delay"])))
    return out


async def _open_situations(store: Store) -> list[dict[str, Any]]:
    return await store.list_situations("open", 100)


async def test_coordinated_login_burst_groups_into_one_situation(store: Store) -> None:
    """C.3: a burst of SNMP authenticationFailure traps from one device must correlate into a
    single situation — a coordinated login attempt seen as one incident, not N unrelated alarms."""
    queue: asyncio.Queue[QueueItem] = asyncio.Queue()
    engine = Engine(store, queue)
    await engine.start()
    doc = scenario_dsl.login_failure_burst(count=12, spacing=0.4).build()
    await util.drive(engine, queue, _events(doc, BASE))

    sits = await _open_situations(store)
    assert len(sits) == 1, f"expected one situation for the burst, got {len(sits)}"
    assert sits[0]["alarm_count"] == 12  # every attempt is a distinct alarm in the one situation
    # The alarm class is the standard authenticationFailure OID (a real security event over SNMP).
    classes = await store.list_classes()
    assert any(c["oid"] == scenario_dsl.AUTH_FAILURE_OID for c in classes)


async def test_chassis_card_failure_contains_its_ports_in_one_situation(store: Store) -> None:
    """C.2: a line-card failure and the ports it takes down are one situation (same NE burst)."""
    queue: asyncio.Queue[QueueItem] = asyncio.Queue()
    engine = Engine(store, queue)
    await engine.start()
    doc = scenario_dsl.chassis_card_containment(ports=6).build()
    await util.drive(engine, queue, _events(doc, BASE))

    sits = await _open_situations(store)
    assert len(sits) == 1
    assert sits[0]["alarm_count"] == 7  # the card + its 6 ports


async def test_cross_device_bgp_flap_stays_separate_until_affinity_is_learned(
    store: Store,
) -> None:
    """C.2 breadth + honesty: two NEs reporting the same adjacency-down at cold start do NOT merge
    — cross-device correlation is *learned* (co-occurrence), never assumed. This is the property
    the whole affinity model exists to earn."""
    queue: asyncio.Queue[QueueItem] = asyncio.Queue()
    engine = Engine(store, queue)
    await engine.start()
    doc = scenario_dsl.bgp_adjacency_flap().build()
    await util.drive(engine, queue, _events(doc, BASE))

    sits = await _open_situations(store)
    assert len(sits) == 2  # two singletons cold; a learned edge would later merge them
