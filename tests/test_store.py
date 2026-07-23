from __future__ import annotations

from pathlib import Path

from opticorr.events import QuarantinedPacket
from opticorr.store import EdgeRow, Store

import util


async def test_migrations_are_idempotent(tmp_path: Path) -> None:
    path = str(tmp_path / "m.db")
    for _ in range(2):
        s = Store(path)
        await s.open()
        await s.close()


async def test_ingest_dedups_by_fingerprint(store: Store) -> None:
    first = await store.ingest(util.event(ts=100.0))
    again = await store.ingest(util.event(ts=110.0))
    assert first.activated and first.count == 1
    assert not again.activated
    assert again.alarm_id == first.alarm_id and again.count == 2
    other = await store.ingest(util.event(instance="if9", ts=111.0))
    assert other.activated and other.alarm_id != first.alarm_id


async def test_clear_and_reraise_reactivates(store: Store) -> None:
    r = await store.ingest(util.event(ts=100.0))
    cleared = await store.clear_alarm(r.device_id, r.class_id, "", ts=105.0)
    assert cleared == r.alarm_id
    assert await store.clear_alarm(r.device_id, r.class_id, "", ts=106.0) is None
    reraise = await store.ingest(util.event(ts=110.0))
    assert reraise.activated and reraise.alarm_id == r.alarm_id and reraise.count == 2


async def test_vendor_resolved_on_class_and_device_rows(store: Store) -> None:
    await store.ingest(util.event(trap_oid=util.HUAWEI_TRAP, ts=1.0))
    cur = await store.conn.execute("SELECT vendor, name FROM alarm_class")
    row = await cur.fetchone()
    assert row is not None and row["vendor"] == "Huawei" and row["name"] is None
    await store.ingest(util.event(trap_oid="1.3.6.1.6.3.1.1.5.3", ts=2.0))
    cur = await store.conn.execute("SELECT name FROM alarm_class WHERE name IS NOT NULL")
    row = await cur.fetchone()
    assert row is not None and row["name"] == "linkDown"


async def test_quarantine_persists_raw(store: Store) -> None:
    pkt = QuarantinedPacket(source="10.9.9.9", raw=b"\xde\xad", reason="ber-decode-failed", ts=1.0)
    await store.quarantine_packet(pkt)
    cur = await store.conn.execute("SELECT source, raw, reason FROM quarantine")
    row = await cur.fetchone()
    assert row is not None and row["raw"] == b"\xde\xad"
    assert (await store.stats())["quarantined"] == 1


async def test_edges_roundtrip_and_version_bump(store: Store) -> None:
    rows = [EdgeRow("device", 1, 2, 0.8, 6.0, 3), EdgeRow("device", 1, 3, 0.2, 1.0, 3)]
    await store.upsert_edges(rows, ts=1.0)
    await store.upsert_edges([EdgeRow("device", 1, 2, 0.9, 7.0, 4)], ts=2.0)
    loaded = {(r.a_id, r.b_id): r for r in await store.load_edges("device")}
    assert loaded[(1, 2)].weight == 0.9 and loaded[(1, 2)].n == 7.0
    cur = await store.conn.execute("SELECT version FROM edge WHERE a_id=1 AND b_id=2")
    row = await cur.fetchone()
    assert row is not None and row["version"] == 2


async def test_meta_roundtrip(store: Store) -> None:
    assert await store.get_meta("g") is None
    await store.set_meta("g", "41")
    await store.set_meta("g", "42")
    assert await store.get_meta("g") == "42"


async def test_situation_lifecycle_and_merge(store: Store) -> None:
    a = await store.ingest(util.event(device="10.0.0.1", ts=1.0))
    b = await store.ingest(util.event(device="10.0.0.2", ts=2.0))
    s1 = await store.create_situation(ts=1.0)
    s2 = await store.create_situation(ts=2.0)
    await store.add_alarm_to_situation(s1, a.alarm_id)
    await store.add_alarm_to_situation(s2, b.alarm_id)
    await store.add_link(s2, a.alarm_id, b.alarm_id, 0.7, 0.2, 0.3, 0.2, ts=2.0)
    await store.merge_situations(s1, s2, ts=3.0)
    members = {m["id"] for m in await store.situation_members(s1)}
    assert members == {a.alarm_id, b.alarm_id}
    detail = await store.situation_detail(s1)
    assert detail is not None and len(detail["links"]) == 1
    assert detail["links"][0]["term_a"] == 0.3
    merged = await store.situation_detail(s2)
    assert merged is not None and merged["status"] == "merged"
    assert not await store.all_cleared(s1)
    await store.set_root(s1, a.alarm_id)
    await store.close_situation(s1, ts=4.0)
    detail = await store.situation_detail(s1)
    assert detail is not None
    assert detail["status"] == "closed" and detail["root_alarm_id"] == a.alarm_id


async def test_idle_open_situations(store: Store) -> None:
    s1 = await store.create_situation(ts=100.0)
    s2 = await store.create_situation(ts=100.0)
    await store.touch_situation(s2, ts=500.0)
    assert await store.idle_open_situations(cutoff=400.0) == [s1]


async def test_feedback_requires_existing_situation(store: Store) -> None:
    s1 = await store.create_situation(ts=1.0)
    assert await store.add_feedback(s1, "confirm", ts=2.0)
    assert not await store.add_feedback(999, "split", ts=2.0)


async def test_labels_and_read_models(store: Store) -> None:
    a = await store.ingest(util.event(device="10.0.0.1", ts=1.0))
    b = await store.ingest(util.event(device="10.0.0.2", trap_oid=util.HUAWEI_TRAP, ts=2.0))
    await store.set_label("device", a.device_id, "core-router-1", ts=3.0)
    await store.set_label("class", a.class_id, "LOS", ts=3.0)
    s1 = await store.create_situation(ts=3.0)
    await store.add_alarm_to_situation(s1, a.alarm_id)
    await store.add_alarm_to_situation(s1, b.alarm_id)
    detail = await store.situation_detail(s1)
    assert detail is not None
    by_ip = {al["device_ip"]: al for al in detail["alarms"]}
    assert by_ip["10.0.0.1"]["device_label"] == "core-router-1"
    assert by_ip["10.0.0.1"]["class_label"] == "LOS"
    assert by_ip["10.0.0.2"]["device_label"] is None
    await store.upsert_edges([EdgeRow("device", a.device_id, b.device_id, 0.9, 6.0, 1)], ts=4.0)
    graph = await store.graph_snapshot(min_edge_n=5.0)
    assert {n["ip"] for n in graph["nodes"]} == {"10.0.0.1", "10.0.0.2"}
    assert len(graph["edges"]) == 1
    assert (await store.graph_snapshot(min_edge_n=7.0))["edges"] == []
    listed = await store.list_situations(status="open", limit=10)
    assert listed[0]["id"] == s1 and listed[0]["alarm_count"] == 2
    assert await store.list_situations(status="closed", limit=10) == []
    classes = await store.list_classes()
    assert {c["oid"] for c in classes} == {util.CIENA_TRAP, util.HUAWEI_TRAP}
    stats = await store.stats()
    assert stats["devices"] == 2 and stats["active_alarms"] == 2


async def test_situation_detail_missing(store: Store) -> None:
    assert await store.situation_detail(12345) is None


async def test_prune_bounds_growth(store: Store) -> None:
    old = await store.ingest(util.event(device="10.0.0.1", ts=100.0))
    keep = await store.ingest(util.event(device="10.0.0.2", ts=100.0))
    await store.clear_alarm(old.device_id, old.class_id, "", ts=150.0)
    s_old = await store.create_situation(ts=100.0)
    await store.add_feedback(s_old, "confirm", ts=100.0)
    await store.close_situation(s_old, ts=200.0)
    s_open = await store.create_situation(ts=100.0)
    await store.add_alarm_to_situation(s_open, keep.alarm_id)
    await store.quarantine_packet(
        QuarantinedPacket(source="10.9.9.9", raw=b"x", reason="r", ts=100.0)
    )
    counts = await store.prune(now=100_000.0, retention_s=1_000.0)
    assert counts == {"situations": 1, "alarms": 1, "quarantine": 1}
    assert await store.situation_detail(s_old) is None
    assert (await store.stats())["open_situations"] == 1
    counts = await store.prune(now=100_000.0, retention_s=1_000.0)
    assert counts == {"situations": 0, "alarms": 0, "quarantine": 0}
