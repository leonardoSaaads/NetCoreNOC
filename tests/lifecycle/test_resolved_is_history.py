"""ADR #506 — a resolved situation is the record of what happened, and nothing on it is operated.

An alarm is one row per (element, class, instance), re-activated when the element raises it again.
So once situation A has resolved, the same alarms can be raised again into a new situation B and be
members of both: A's membership is history, B's is the live one. The owner's report: A showed
"Clear all 10 active" and pressing it hand-cleared B's alarms.

What is pinned here: the bulk clear refuses a resolved situation (409) and touches nothing; A's
detail names the live situation that holds each re-raised member; a single clear of such an alarm
is recorded on B, the situation that holds it, and B resolves when its last member is cleared.
"""

from __future__ import annotations

from netcorenoc.store import Store

import authutil
import util

BASE = 1_700_000_000.0
#: Past the one-hour re-occurrence window, so the second replay is a new incident.
LATER = BASE + 7_200.0


async def _status(store: Store, sid: int) -> str:
    async with store.reading():
        cur = await store.conn.execute("SELECT status FROM situation WHERE id=?", (sid,))
        row = await cur.fetchone()
    assert row is not None
    return str(row[0])


async def _active(store: Store, ids: list[int]) -> set[int]:
    async with store.reading():
        cur = await store.conn.execute(
            "SELECT id FROM alarm WHERE status='active' AND id = ANY(?)", (ids,)
        )
        return {int(r[0]) for r in await cur.fetchall()}


async def test_a_resolved_situation_cannot_clear_the_alarms_raised_again_elsewhere(
    store: Store,
) -> None:
    engine, queue, app = await authutil.make_env(store)
    await util.drive(engine, queue, util.fixture_events("fiber_cut.json", BASE))
    live = [r for r in await store.list_situations(None, 100) if r["status"] != "resolved"]
    first = int(live[0]["id"])
    members = await store.situation_member_ids(first)
    client = await authutil.client_as(app, "editor")
    try:
        cleared = await client.post("/api/alarms/clear", json={"situation_id": first})
        assert cleared.status_code == 200, cleared.text
        assert await _status(store, first) == "resolved"
        assert not await _active(store, members)

        # The same element raises the same alarms again, an incident later.
        await util.drive(engine, queue, util.fixture_events("fiber_cut.json", LATER))
        again = await _active(store, members)
        assert again, "the replay raised none of the first situation's alarms again"

        detail = (await client.get(f"/api/situations/{first}")).json()
        held = {int(a["id"]): a["held_by"] for a in detail["alarms"]}
        holders = {held[aid] for aid in again}
        assert None not in holders, "a re-raised member does not say which situation holds it"
        assert first not in holders
        for holder in holders:
            assert await _status(store, int(holder)) != "resolved"

        refused = await client.post("/api/alarms/clear", json={"situation_id": first})
        assert refused.status_code == 409, refused.text
        assert await _active(store, members) == again, "the resolved situation cleared live alarms"

        # One alarm, cleared on its own: the gesture is the live holder's, not the history's.
        aid = sorted(again)[0]
        assert (await client.post(f"/api/alarms/{aid}/clear", json={})).status_code == 200
        async with store.reading():
            cur = await store.conn.execute(
                "SELECT situation_id FROM situation_event WHERE kind='manual_clear' "
                "AND alarm_id=? ORDER BY id DESC LIMIT 1",
                (aid,),
            )
            row = await cur.fetchone()
        assert row is not None and int(row[0]) == int(held[aid]), (row, held[aid])
    finally:
        await client.aclose()


async def test_a_live_situation_clears_only_the_members_it_holds(store: Store) -> None:
    """The bulk clear of a live situation reaches its own members — never one a newer one holds."""
    engine, queue, app = await authutil.make_env(store)
    await util.drive(engine, queue, util.fixture_events("fiber_cut.json", BASE))
    rows = await store.list_situations(None, 100)
    first = int(next(r for r in rows if r["status"] != "resolved")["id"])
    client = await authutil.client_as(app, "editor")
    try:
        assert (
            await client.post("/api/alarms/clear", json={"situation_id": first})
        ).status_code == 200
        await util.drive(engine, queue, util.fixture_events("fiber_cut.json", LATER))
        detail = (await client.get(f"/api/situations/{first}")).json()
        holders = {int(a["held_by"]) for a in detail["alarms"] if a["held_by"] is not None}
        taken = {int(a["id"]) for a in detail["alarms"] if a["held_by"] is not None}
        assert holders
        async with store.reading():
            assert not await store.held_member_ids(first) & taken, (
                "a resolved situation still holds members a live one took back"
            )
        for holder in holders:
            async with store.reading():
                held = await store.held_member_ids(holder)
                own = set(await store.situation_member_ids(holder))
            assert held == own, "a live situation does not hold its own members"
            answer = await client.post("/api/alarms/clear", json={"situation_id": holder})
            assert answer.status_code == 200, answer.text
            assert answer.json()["cleared"] == len(own), answer.json()
    finally:
        await client.aclose()
