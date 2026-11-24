"""ADR #505 — the live stream carries every live state, however much was resolved since.

The lab's console counted 49 new situations in its header while the New tab said the network was
quiet: the stream carried the 50 latest situations of every state, and an appliance that resolves
a lot pushed its older new ones out of that window. Each live state now brings its own latest.
"""

from __future__ import annotations

import time

from netcorenoc.api.routes import events
from netcorenoc.store import Store

import util


async def test_older_new_situations_survive_a_window_full_of_resolved_ones(store: Store) -> None:
    now = time.time()
    async with store.lock:
        old_new = []
        for i in range(3):
            sid = await store.create_situation(now - 3600 + i, None)
            alarm = await store.ingest(util.event(device=f"10.1.0.{i + 1}", ts=now - 3600 + i))
            await store.add_alarm_to_situation(sid, alarm.alarm_id)
            await store.conn.execute(
                "UPDATE situation SET status='new', updated_at=to_timestamp(?) WHERE id=?",
                (now - 3600 + i, sid),
            )
            old_new.append(sid)
        for i in range(events.LIVE_PER_STATE + 10):
            sid = await store.create_situation(now - 60 + i, None)
            await store.conn.execute(
                "UPDATE situation SET status='resolved', updated_at=to_timestamp(?) WHERE id=?",
                (now - 60 + i, sid),
            )
        await store.commit()
    async with store.reading():
        latest = await store.list_situations(None, events.LIVE_PER_STATE, None)
        live = await events.live_situations(store, None)
    assert not {int(r["id"]) for r in latest} & set(old_new), "the control: the old list lost them"
    ids = [int(r["id"]) for r in live]
    assert set(old_new) <= set(ids), "every live state brings its own latest"
    assert len(ids) == len(set(ids)), "a row in two answers is listed once"
    stamps = [(r["updated_at"], r["id"]) for r in live]
    assert stamps == sorted(stamps, reverse=True), "newest first, as before"
    resolved = sum(1 for r in live if r["status"] == "resolved")
    assert resolved == events.LIVE_PER_STATE, "the recent resolved ones the Resolved tab shows"
