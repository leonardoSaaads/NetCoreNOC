"""The merge picker's candidates, and the rule that only a confirmed grouping is open (ADR #489).

`GET /api/situations/{sid}/related?status=new|open` ranks the situations of one status by devices
in common, the same customer and how close they started; it never names the situation itself nor
one with no member left. Restructuring — a split, a merge — keeps a situation `new`; only *Confirm
grouping* opens it.
"""

from __future__ import annotations

from typing import Any

from netcorenoc.api.routes.related import rank
from netcorenoc.store import Store

import authutil
import util

BASE = 1_700_000_000.0
SURE = 0.8


async def _status(store: Store, sid: int) -> str:
    rows = {int(r["id"]): r for r in await store.list_situations(None, 200)}
    return str(rows[sid]["status"])


async def test_restructuring_keeps_new_and_the_picker_ranks_by_what_is_shared(store: Store) -> None:
    engine, queue, app = await authutil.make_env(store)
    await util.drive(engine, queue, util.fixture_events("fiber_cut.json", BASE))
    live = [r for r in await store.list_situations(None, 100) if r["status"] != "resolved"]
    source = int(live[0]["id"])
    members = await store.situation_member_ids(source)
    assert len(members) >= 4
    client = await authutil.client_as(app, "editor")
    try:
        split = await client.post(
            f"/api/situations/{source}/split", json={"alarm_ids": members[:2], "confidence": SURE}
        )
        assert split.status_code == 200, split.text
        made = next(
            int(r["id"])
            for r in await store.list_situations(None, 100)
            if r["status"] != "resolved" and int(r["id"]) != source
        )
        # ADR #489: a split builds a grouping nobody has confirmed — both sides stay `new`.
        assert await _status(store, source) == "new"
        assert await _status(store, made) == "new"

        picked = await client.get(f"/api/situations/{source}/related?status=new")
        assert picked.status_code == 200, picked.text
        rows: list[dict[str, Any]] = picked.json()
        ids = [int(r["id"]) for r in rows]
        assert made in ids and source not in ids, ids
        assert all(r["status"] == "new" for r in rows), rows
        assert {"shared_devices", "same_customer", "gap_s"} <= set(rows[0]), rows[0]
        opened = await client.get(f"/api/situations/{source}/related?status=open")
        assert opened.status_code == 200 and opened.json() == [], opened.text

        # A merge into a `new` situation leaves it `new`; the one merged away is no candidate.
        merged = await client.post(
            f"/api/situations/{source}/merge", json={"from_situation_id": made, "confidence": SURE}
        )
        assert merged.status_code == 200, merged.text
        assert await _status(store, source) == "new", "a merge opened the situation it grew"
        assert await _status(store, made) == "resolved"
        again = await client.get(f"/api/situations/{made}/related?status=new")
        assert again.status_code == 200
        assert source not in [
            int(r["id"])
            for r in (await client.get(f"/api/situations/{source}/related?status=new")).json()
        ]

        # Only Confirm grouping opens it.
        detail = (await client.get(f"/api/situations/{source}")).json()
        confirm = await client.post(
            f"/api/situations/{source}/feedback",
            json={"verdict": "confirm", "member_ids": [a["id"] for a in detail["alarms"]]},
        )
        assert confirm.status_code == 200, confirm.text
        assert await _status(store, source) == "open"
    finally:
        await client.aclose()


async def test_an_unknown_situation_is_not_found(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    client = await authutil.client_as(app, "viewer")
    try:
        missing = await client.get("/api/situations/987654/related?status=new")
        assert missing.status_code == 404, missing.text
        bad = await client.get("/api/situations/1/related?status=resolved")
        assert bad.status_code == 422, "only New and Open situations are merge candidates"
    finally:
        await client.aclose()


def test_the_rank_is_devices_then_customer_then_time() -> None:
    subject = {"id": 1, "created_at": 1000.0}
    rows = [
        {"id": 2, "created_at": 1010.0},  # close in time, nothing shared
        {"id": 3, "created_at": 5000.0},  # one device in common, far in time
        {"id": 4, "created_at": 1500.0},  # same customer only
    ]
    nes = {1: {10, 11}, 2: {20}, 3: {11}, 4: {30}}
    orgs = {10: 7, 11: 7, 20: 8, 30: 7}
    ranked = rank(subject, rows, nes, orgs)
    assert [r["id"] for r in ranked] == [3, 4, 2]
    assert ranked[0]["shared_devices"] == 1 and ranked[1]["same_customer"] is True
    assert ranked[2]["gap_s"] == 10
