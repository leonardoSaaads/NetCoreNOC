"""`/api/activity/*`: the Timeline and the Overview's chart, bounded by the window (F155, ADR #381).

Item 14 of v0.22.0, measured on the lab before the repair: `/api/timeline?limit=1000` returned marks
spanning **124 seconds** under a control that said a day, because a row count truncated the window
and the axis followed the rows. Every read here is bounded by the window and nothing else, so:

* the axis is **proportional to time** — a raise ten seconds into an hour is in the first bucket and
  one fifty minutes in is in the fifth, however many rows there are;
* a burst of one trap on one element is **one row** with its count;
* every filter, the caller's scope included, is a WHERE clause.
"""

from __future__ import annotations

import asyncio

from netcorenoc.ingest.events import TrapEvent
from netcorenoc.ingest.receiver import QueueItem, parse_trap
from netcorenoc.main import Engine
from netcorenoc.store import Store

import authutil
import trap_replay
import util

BASE = 1_700_000_000.0
HOUR = 3600.0
TRAP = "1.3.6.1.4.1.1271.2.1.1"
OTHER = "1.3.6.1.4.1.1271.2.1.9"


def _trap(source: str, oid: str, port: int, ts: float) -> TrapEvent:
    varbinds = [{"oid": "1.3.6.1.4.1.1271.9.1", "kind": "str", "value": f"port-{port}"}]
    return parse_trap(source, trap_replay.encode_trap(oid, varbinds, "public", 1), ts)


async def _drive(engine: Engine, queue: asyncio.Queue[QueueItem], events: list[TrapEvent]) -> None:
    await util.drive(engine, queue, sorted(events, key=lambda e: e.ts))


async def _ne(store: Store, ip: str) -> int:
    async with store.lock:
        cur = await store.conn.execute("SELECT id FROM ne WHERE ip=?", (ip,))
        row = await cur.fetchone()
    assert row is not None, ip
    return int(row[0])


async def test_the_axis_is_proportional_to_time_not_to_rows(store: Store) -> None:
    engine, queue, _app = await authutil.make_env(store)
    early = [_trap("127.0.0.2", TRAP, port, BASE + 10 + port) for port in range(30)]
    late = [_trap("127.0.0.2", TRAP, 100, BASE + 50 * 60)]
    await _drive(engine, queue, early + late)
    async with store.lock:
        out = await store.activity_severity(since=BASE, until=BASE + HOUR, buckets=6, ne_ids=None)
    total = [sum(values) for values in zip(*out["series"].values(), strict=True)]
    assert out["bucket_s"] == 600.0
    assert total == [30, 0, 0, 0, 0, 1], total
    # Nothing the appliance could not grade was dropped: it is counted, under `unplaced`.
    assert sum(out["series"]["unplaced"]) == 31, out["series"]


async def test_a_rule_moves_a_band_at_read_time_without_touching_an_alarm(store: Store) -> None:
    engine, queue, _app = await authutil.make_env(store)
    await _drive(engine, queue, [_trap("127.0.0.2", TRAP, 1, BASE + 5)])
    async with store.lock:
        before = await store.activity_severity(
            since=BASE, until=BASE + HOUR, buckets=1, ne_ids=None
        )
        cur = await store.conn.execute("SELECT severity, severity_rank FROM alarm")
        alarm_before = [tuple(r) for r in await cur.fetchall()]
        await store.put_class_rule(
            oid="1.3.6.1.4.1.1271",
            subtree=True,
            name=None,
            severity="major",
            vendor=None,
            source="declared",
            origin=None,
            actor="test",
        )
        after = await store.activity_severity(since=BASE, until=BASE + HOUR, buckets=1, ne_ids=None)
        cur = await store.conn.execute("SELECT severity, severity_rank FROM alarm")
        alarm_after = [tuple(r) for r in await cur.fetchall()]
    assert before["series"]["unplaced"] == [1] and before["series"]["major"] == [0]
    assert after["series"]["major"] == [1] and after["series"]["unplaced"] == [0]
    assert alarm_before == alarm_after, "a catalogue rule rewrote a stored alarm"


async def test_a_burst_is_one_row_and_a_later_one_is_another(store: Store) -> None:
    engine, queue, _app = await authutil.make_env(store)
    burst = [_trap("127.0.0.2", TRAP, port, BASE + port * 8) for port in range(14)]
    later = [_trap("127.0.0.2", TRAP, 50, BASE + 40 * 60)]
    elsewhere = [_trap("127.0.0.3", OTHER, 1, BASE + 30)]
    await _drive(engine, queue, burst + later + elsewhere)
    async with store.lock:
        out = await store.activity_groups(
            since=BASE, until=BASE + HOUR, ne_ids=None, kinds=("raise",), limit=10
        )
    counts = sorted(group["n"] for group in out["groups"])
    assert counts == [1, 1, 14], out["groups"]
    assert out["total"] == 3 and out["marks"] == 16
    assert out["groups"][0]["last"] == BASE + 40 * 60, "bursts are not newest first"


async def test_every_filter_is_a_where_clause_and_scope_narrows_the_count(store: Store) -> None:
    engine, queue, _app = await authutil.make_env(store)
    await _drive(
        engine,
        queue,
        [_trap("127.0.0.2", TRAP, 1, BASE + 1), _trap("127.0.0.3", OTHER, 1, BASE + 2)],
    )
    a = await _ne(store, "127.0.0.2")
    b = await _ne(store, "127.0.0.3")
    async with store.lock:
        everything = await store.activity_groups(since=BASE, until=BASE + HOUR, ne_ids=None)
        one = await store.activity_groups(
            since=BASE, until=BASE + HOUR, ne_ids=None, device_ne_id=a
        )
        scoped = await store.activity_groups(since=BASE, until=BASE + HOUR, ne_ids=frozenset({b}))
        hidden = await store.activity_groups(
            since=BASE, until=BASE + HOUR, ne_ids=frozenset({b}), device_ne_id=a
        )
        lanes = await store.activity_lanes(
            since=BASE, until=BASE + HOUR, buckets=4, top=8, ne_ids=frozenset({b})
        )
    assert everything["total"] == 2
    assert {g["ne_id"] for g in one["groups"]} == {a}
    assert {g["ne_id"] for g in scoped["groups"]} == {b}
    # An element outside the scope answers what a nonexistent one answers: nothing.
    assert hidden["total"] == 0 and hidden["groups"] == []
    assert {lane["ne_id"] for lane in lanes["lanes"]} <= {b}, lanes


async def test_the_routes_clamp_the_window_and_page(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    viewer = await authutil.client_as(app, "viewer")
    try:
        body = (
            await viewer.get(
                "/api/activity/groups", params={"range_s": 10**9, "limit": 10**6, "offset": -5}
            )
        ).json()
        assert body["to"] - body["from"] <= 30 * 24 * HOUR + 1
        assert body["offset"] == 0
        sev = (await viewer.get("/api/activity/severity", params={"buckets": 10**6})).json()
        assert sev["buckets"] == 240
        bad = await viewer.get("/api/activity/groups", params={"kind": "everything"})
        assert bad.status_code == 422
    finally:
        await viewer.aclose()


async def test_a_fault_that_keeps_firing_is_counted_as_re_reported_not_as_nothing(
    store: Store,
) -> None:
    """Found in the live pass: a second cut re-fired alarms already active, which moves `last_seen`
    and raises nothing, and the Timeline said *"nothing was raised or cleared"* during an outage."""
    engine, queue, _app = await authutil.make_env(store)
    await _drive(engine, queue, [_trap("127.0.0.2", TRAP, 1, BASE)])
    await _drive(engine, queue, [_trap("127.0.0.2", TRAP, 1, BASE + 2 * HOUR)])
    async with store.lock:
        later = await store.activity_groups(since=BASE + HOUR, until=BASE + 3 * HOUR, ne_ids=None)
        first = await store.activity_groups(since=BASE - 1, until=BASE + 60, ne_ids=None)
        hidden = await store.activity_groups(
            since=BASE + HOUR, until=BASE + 3 * HOUR, ne_ids=frozenset({999_999})
        )
    assert later["total"] == 0 and later["repeated"] == 1, later
    # The control: in the window it was raised in, it is a raise and not a repeat.
    assert first["total"] == 1 and first["repeated"] == 0, first
    # Scoped like every other count here.
    assert hidden["repeated"] == 0
