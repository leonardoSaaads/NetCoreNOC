"""Active alarms over time, the Top 10, the Timeline's narrow filters and the inventory (v0.23.0).

The defect that started this (#393): the Overview's chart counted RAISES per bucket, so ten
critical alarms raised at once and still active drew a spike and then zero — the picture of an
incident that resolved itself. What is asserted here is the property that repairs it: an alarm is
counted in every bucket its interval `[first_seen, cleared_at)` covers, a clear takes it away, and
the last point is the number active now.
"""

from __future__ import annotations

from netcorenoc.store import Store
from netcorenoc.store.narrow import Narrow
from test_activity import BASE, HOUR, OTHER, TRAP, _drive, _ne, _trap

import authutil


async def _rule(store: Store, oid: str, severity: str) -> None:
    async with store.lock:
        await store.put_class_rule(
            oid=oid,
            subtree=False,
            name=None,
            severity=severity,
            vendor=None,
            source="declared",
            origin=None,
            actor="test",
        )


async def _clear(store: Store, ip: str, at: float, n: int) -> None:
    """Clear `n` of an element's active alarms at `at`, the way the clear path records it."""
    async with store.lock:
        await store.conn.execute(
            "UPDATE alarm SET status='cleared', cleared_at=? WHERE id IN (SELECT a.id FROM alarm a "
            "JOIN ne n ON n.id=a.ne_id WHERE n.ip=? AND a.status='active' ORDER BY a.id LIMIT ?)",
            (at, ip, n),
        )
        await store.conn.commit()


async def test_a_burst_still_active_is_counted_in_every_later_bucket(store: Store) -> None:
    engine, queue, _app = await authutil.make_env(store)
    await _drive(engine, queue, [_trap("127.0.0.2", TRAP, p, BASE + 60 + p) for p in range(10)])
    await _rule(store, TRAP, "critical")
    async with store.lock:
        out = await store.activity_active(since=BASE, until=BASE + HOUR, buckets=6, ne_ids=None)
    # Raises-per-bucket would read [10, 0, 0, 0, 0, 0]: the defect.
    assert out["series"]["critical"] == [10] * 6, out["series"]
    assert out["measure"].startswith("active")


async def test_a_clear_takes_an_alarm_away_from_the_bucket_it_falls_in(store: Store) -> None:
    engine, queue, _app = await authutil.make_env(store)
    await _drive(engine, queue, [_trap("127.0.0.2", TRAP, p, BASE + 60 + p) for p in range(10)])
    await _rule(store, TRAP, "critical")
    await _clear(store, "127.0.0.2", BASE + 25 * 60, 4)
    async with store.lock:
        out = await store.activity_active(since=BASE, until=BASE + HOUR, buckets=6, ne_ids=None)
        census = await store.severity_census()
    assert out["series"]["critical"] == [10, 10, 6, 6, 6, 6], out["series"]
    # The last point is the number active now, which is what the severity card says.
    # (The census keys its placed counts by rank; critical is rank 0.)
    assert out["series"]["critical"][-1] == census["placed"].get("0", 0) == census["active"] == 6


async def test_alarms_raised_before_the_window_are_its_baseline(store: Store) -> None:
    engine, queue, _app = await authutil.make_env(store)
    await _drive(engine, queue, [_trap("127.0.0.2", TRAP, p, BASE + p) for p in range(3)])
    await _rule(store, TRAP, "major")
    async with store.lock:
        out = await store.activity_active(
            since=BASE + HOUR, until=BASE + 2 * HOUR, buckets=4, ne_ids=None
        )
    assert out["series"]["major"] == [3, 3, 3, 3], out["series"]


async def test_the_top_ten_ranks_by_the_severities_chosen(store: Store) -> None:
    engine, queue, _app = await authutil.make_env(store)
    majors = [_trap("127.0.0.2", OTHER, p, BASE + 10 + p) for p in range(5)]
    criticals = [_trap("127.0.0.3", TRAP, p, BASE + 20 + p) for p in range(2)]
    await _drive(engine, queue, majors + criticals)
    await _rule(store, TRAP, "critical")
    await _rule(store, OTHER, "major")
    a = await _ne(store, "127.0.0.2")
    b = await _ne(store, "127.0.0.3")
    async with store.lock:
        critical = await store.activity_top(
            since=BASE, until=BASE + HOUR, buckets=4, ne_ids=None, bands=("critical",)
        )
        both = await store.activity_top(
            since=BASE, until=BASE + HOUR, buckets=4, ne_ids=None, bands=("critical", "major")
        )
        scoped = await store.activity_top(
            since=BASE, until=BASE + HOUR, buckets=4, ne_ids=frozenset({a}), bands=("major",)
        )
    # Critical only: the element with majors has nothing to rank and is not a row.
    assert [r["ne_id"] for r in critical["top"]] == [b]
    assert critical["top"][0]["count"] == 2 and critical["top"][0]["trend"][-1] == 2
    # Critical + major: five beats two.
    assert [r["ne_id"] for r in both["top"]] == [a, b]
    assert both["top"][0]["now"]["major"] == 5
    # Scope is a WHERE clause: an element outside it is not ranked.
    assert [r["ne_id"] for r in scoped["top"]] == [a]


async def test_the_narrow_filters_are_where_clauses_on_arc_boundaries(store: Store) -> None:
    engine, queue, _app = await authutil.make_env(store)
    # `TRAP + "0"` shares TRAP's text prefix but is not below it: an arc boundary must hold.
    sibling = TRAP + "0"
    await _drive(
        engine,
        queue,
        [
            _trap("127.0.0.2", TRAP, 1, BASE + 5),
            _trap("127.0.0.2", TRAP + ".7", 2, BASE + 6),
            _trap("127.0.0.2", sibling, 3, BASE + 7),
        ],
    )
    async with store.lock:
        branch = await store.activity_groups(
            since=BASE, until=BASE + HOUR, ne_ids=None, narrow=Narrow(oid=TRAP)
        )
        active = await store.activity_active(
            since=BASE, until=BASE + HOUR, buckets=1, ne_ids=None, narrow=Narrow(oid=TRAP)
        )
        nowhere = await store.activity_groups(
            since=BASE, until=BASE + HOUR, ne_ids=None, narrow=Narrow(organization_id=10**6)
        )
    assert {g["class_oid"] for g in branch["groups"]} == {TRAP, TRAP + ".7"}, branch["groups"]
    assert sum(values[-1] for values in active["series"].values()) == 2
    assert nowhere["total"] == 0


async def test_a_situation_narrows_the_timeline_to_its_own_alarms(store: Store) -> None:
    engine, queue, _app = await authutil.make_env(store)
    await _drive(
        engine,
        queue,
        [_trap("127.0.0.2", TRAP, 1, BASE + 5), _trap("127.0.0.3", OTHER, 1, BASE + 4000)],
    )
    async with store.lock:
        cur = await store.conn.execute(
            "SELECT sa.situation_id, COUNT(*) FROM situation_alarm sa GROUP BY sa.situation_id "
            "ORDER BY sa.situation_id LIMIT 1"
        )
        row = await cur.fetchone()
        assert row is not None, "the fixture formed no situation"
        sid, members = row
        out = await store.activity_groups(
            since=BASE, until=BASE + 2 * HOUR, ne_ids=None, narrow=Narrow(situation_id=int(sid))
        )
    assert out["marks"] == members, out


async def test_the_inventory_counts_by_band_and_is_scoped(store: Store) -> None:
    engine, queue, _app = await authutil.make_env(store)
    await _drive(
        engine,
        queue,
        [_trap("127.0.0.2", TRAP, p, BASE + p) for p in range(3)]
        + [_trap("127.0.0.3", OTHER, 1, BASE + 9)],
    )
    await _rule(store, TRAP, "critical")
    a = await _ne(store, "127.0.0.2")
    async with store.lock:
        everything = await store.inventory(None, now=BASE + 60)
        scoped = await store.inventory(frozenset({a}), now=BASE + 60)
        nothing = await store.inventory(frozenset(), now=BASE + 60)
        later = await store.inventory(None, now=BASE + 3 * 86400)
    rows = {r["ip"]: r for r in everything["elements"]}
    assert rows["127.0.0.2"]["bands"]["critical"] == 3 and rows["127.0.0.2"]["active"] == 3
    assert everything["totals"]["elements"] == 2 and everything["totals"]["critical"] == 1
    assert everything["totals"]["alarming"] == 2
    assert [r["ne_id"] for r in scoped["elements"]] == [a] and scoped["totals"]["elements"] == 1
    assert nothing["elements"] == [] and nothing["totals"]["elements"] == 0
    # Silence is measured against the clock asked about, not the one the fixture was written at.
    assert everything["totals"]["silent"] == 0 and later["totals"]["silent"] == 2


async def test_components_404_out_of_scope_exactly_as_a_missing_element(store: Store) -> None:
    engine, queue, app = await authutil.make_env(store)
    await _drive(engine, queue, [_trap("127.0.0.2", TRAP, p, BASE + p) for p in range(3)])
    a = await _ne(store, "127.0.0.2")
    admin = await authutil.client_as(app, "admin")
    try:
        ok = await admin.get(f"/api/elements/{a}/components")
        missing = await admin.get("/api/elements/999999/components")
        inventory = await admin.get("/api/inventory")
        top = await admin.get("/api/activity/top", params={"bands": "critical,nonsense"})
    finally:
        await admin.aclose()
    assert ok.status_code == 200 and ok.json()["ne_id"] == a
    assert missing.status_code == 404
    assert inventory.status_code == 200 and inventory.json()["totals"]["elements"] == 1
    assert top.status_code == 422
