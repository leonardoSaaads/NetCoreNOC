from __future__ import annotations

from pathlib import Path

from netcorenoc.ingest.events import QuarantinedPacket
from netcorenoc.store import EdgeRow, Store

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


async def test_class_vendor_and_name_are_derived_from_the_oid_not_stored_beside_it(
    store: Store,
) -> None:
    """v0.16.3: the same two facts, **served** rather than stored (DECISIONS #280).

    This asserted `SELECT vendor, name FROM alarm_class` until `0016` dropped both columns as
    stored derivations — identical to `trap_name(oid)` / `vendor_of(oid)` for 48 of 48 classes on
    a real corpus, with the `oid` they came from in the same row. The property that mattered was
    never that the values were in a column; it was that a reader gets them, so that is what is
    checked, and it now holds without a writer having to remember.

    The `device` half of the old name is deliberately gone: `device.vendor` is never written by
    anything and never was (F105).
    """
    await store.ingest(util.event(trap_oid=util.HUAWEI_TRAP, ts=1.0))
    by_oid = {r["oid"]: r for r in await store.list_classes()}
    assert by_oid[util.HUAWEI_TRAP]["vendor"] == "Huawei"
    assert by_oid[util.HUAWEI_TRAP]["name"] is None  # a vendor trap has no standard name

    await store.ingest(util.event(trap_oid="1.3.6.1.6.3.1.1.5.3", ts=2.0))
    by_oid = {r["oid"]: r for r in await store.list_classes()}
    assert by_oid["1.3.6.1.6.3.1.1.5.3"]["name"] == "linkDown"
    assert by_oid["1.3.6.1.6.3.1.1.5.3"]["vendor"] is None  # not an enterprise arc

    cur = await store.conn.execute("PRAGMA table_info(alarm_class)")
    columns = {str(r[1]) for r in await cur.fetchall()}
    assert not columns & {"name", "vendor"}, (
        f"alarm_class still stores {sorted(columns & {'name', 'vendor'})} — both are pure "
        "functions of the `oid` in the same row, which is what `0008`'s first rule forbids."
    )


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
    # v0.16.0: `merged` moved from `status` to `resolution` — the one historical value migration
    # `0014` could map exactly, because this statement is what wrote it (DECISIONS #253).
    merged = await store.situation_detail(s2)
    assert merged is not None
    assert merged["status"] == "resolved" and merged["resolution"] == "merged"
    assert not await store.all_cleared(s1)
    await store.set_root(s1, a.alarm_id)
    # **v0.16.2 (DECISIONS #274): the appliance's own close REFUSES here.** Both members are still
    # active, and `open -> resolved` requires that none is. Until this release this call resolved
    # the situation and recorded `idle`, which removed a live alarm from every live view — the
    # defect that release is named for. The invariant is in the UPDATE's own WHERE clause, so this
    # is a no-op rather than a caller's omission.
    await store.close_situation(s1, ts=4.0)
    detail = await store.situation_detail(s1)
    assert detail is not None
    assert detail["root_alarm_id"] == a.alarm_id
    assert (detail["status"], detail["resolution"]) == ("new", None), (
        "the appliance resolved a situation that still holds an active alarm"
    )
    # The control, in the same test and against the same row: clear both members and the identical
    # call resolves it. Without this arm the assertion above would also pass against a
    # `close_situation` that had simply stopped working.
    for alarm_id in (a.alarm_id, b.alarm_id):
        await store.conn.execute(
            "UPDATE alarm SET status='cleared', cleared_at=? WHERE id=?", (5.0, alarm_id)
        )
    await store.close_situation(s1, ts=6.0)
    detail = await store.situation_detail(s1)
    assert detail is not None
    assert (detail["status"], detail["resolution"]) == ("resolved", "self_cleared")


async def test_idle_open_situations(store: Store) -> None:
    """**What the sweep may resolve**: live, untouched, and holding nothing that is still on.

    Four arms, and two of them are controls. Without the fresh one this measures `updated_at`
    rather than the rule; without the cleared one a sweep that had simply stopped working would
    score green.
    """
    stale_active = await store.create_situation(ts=100.0)
    fresh_active = await store.create_situation(ts=100.0)
    stale_cleared = await store.create_situation(ts=100.0)
    empty = await store.create_situation(ts=100.0)
    for sid, device, active in (
        (stale_active, "10.0.0.1", True),
        (fresh_active, "10.0.0.2", True),
        (stale_cleared, "10.0.0.3", False),
    ):
        raised = await store.ingest(util.event(device=device, ts=100.0))
        await store.add_alarm_to_situation(sid, raised.alarm_id)
        if not active:
            await store.conn.execute(
                "UPDATE alarm SET status='cleared', cleared_at=? WHERE id=?",
                (100.0, raised.alarm_id),
            )
    await store.touch_situation(stale_active, ts=100.0)
    await store.touch_situation(stale_cleared, ts=100.0)
    await store.touch_situation(empty, ts=100.0)
    await store.touch_situation(fresh_active, ts=500.0)

    resolvable = await store.idle_open_situations(cutoff=400.0)
    active_and_idle = await store.idle_active_situations(cutoff=400.0)
    assert resolvable == [stale_cleared, empty]
    assert active_and_idle == [stale_active]
    # Disjoint, and neither is empty: a partition with an unreachable arm is a state that does not
    # exist, which is the risk DECISIONS #274 takes on by deriving rather than storing.
    assert not set(resolvable) & set(active_and_idle)
    assert resolvable and active_and_idle
    # And together they are exactly the idle live population — asserted against `all_cleared`,
    # which is the method that answered this question all along and that the old query never asked.
    for sid in (stale_active, stale_cleared, empty):
        assert (sid in resolvable) is await store.all_cleared(sid)


async def test_feedback_requires_existing_situation(store: Store) -> None:
    s1 = await store.create_situation(ts=1.0)
    # v0.7.1 (F36): `add_feedback` reports `exists` and `inserted` rather than a bare bool — a
    # repeat of the same verdict exists but does not insert, which is what bounds its effect on
    # learned state.
    #
    # v0.8.0 asserts the two fields by name rather than comparing the whole tuple. The result also
    # carries the new row's `id` now (so the dataset annotation can target it without a second
    # SELECT), and a positional comparison would have to be rewritten every time the record grows —
    # which is a test asserting a *shape* when what it means to assert is a *decision*.
    first = await store.add_feedback(s1, "confirm", ts=2.0)
    assert (first.exists, first.inserted) == (True, True)
    assert first.id is not None

    repeat = await store.add_feedback(s1, "confirm", ts=3.0)
    assert (repeat.exists, repeat.inserted) == (True, False)  # idempotent
    assert repeat.id is None, "a no-op insert has no row to report"

    correction = await store.add_feedback(s1, "split", ts=4.0)
    assert (correction.exists, correction.inserted) == (True, True)  # a correction applies

    missing = await store.add_feedback(999, "split", ts=2.0)
    assert (missing.exists, missing.inserted) == (False, False)
    assert missing.id is None


async def test_labels_and_read_models(store: Store) -> None:
    a = await store.ingest(util.event(device="10.0.0.1", ts=1.0))
    b = await store.ingest(util.event(device="10.0.0.2", trap_oid=util.HUAWEI_TRAP, ts=2.0))
    # v0.16.3: `ne`, and keyed on the alarm's OWN ne id rather than on its device id — the
    # two agree on every database anyone has and nothing makes them (DECISIONS #281).
    await store.set_label("ne", await store.ne_id("10.0.0.1", 1.0), "core-router-1", ts=3.0)
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
    # The correlator creates `new`, not `open` (DECISIONS #254).
    listed = await store.list_situations(status="new", limit=10)
    assert listed[0]["id"] == s1 and listed[0]["alarm_count"] == 2
    assert await store.list_situations(status="resolved", limit=10) == []
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
    # Deliberately UNLABELLED. v0.8.0 labelled this situation and asserted it was collected, which
    # is exactly the behaviour F44 says is wrong — a label is not operational data. The labelled
    # case now belongs to tests/test_dataset.py::test_f44_*, which asserts the opposite outcome.
    s_old = await store.create_situation(ts=100.0)
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


# --- the two SQL fragments, and the guards their comments promise -------------------------


#: The runtime package. `util.module_path` deliberately excludes `store/` and `api/`, so this
#: guard resolves the root the way `tests/apisource.py` does — from the imported package — rather
#: than by a path written as text, which is what F92 and F98 are each about.
def _runtime_sources() -> list[tuple[Path, str]]:
    """Every runtime module's text. Walked, never listed — F92's lesson and F98's."""
    import netcorenoc

    pkg = Path(netcorenoc.__file__).resolve().parent
    return [(p, p.read_text(encoding="utf-8")) for p in sorted(pkg.rglob("*.py"))]


def test_every_live_situation_query_uses_the_one_fragment() -> None:
    """`LIVE` is written once and nothing spells it out a second time.

    **This guard was cited by `store/situations.py` from v0.16.0 and did not exist** (F101). The
    comment on `LIVE` said this module is read to assert the fragment is not restated; nothing
    read it, so the single-source claim was a promise rather than a property for two releases.

    Spelling the states out a second time is not a style complaint. `LIVE` is what a v0.16.0
    reader had to widen in six places at once when the correlator started creating `new`, and a
    seventh copy is the one that gets missed — which would silently exclude every untriaged
    situation from whichever query held it.
    """
    from netcorenoc.store import situations

    spelled = [
        (path, text.count(situations.LIVE))
        for path, text in _runtime_sources()
        if situations.LIVE in text
    ]
    assert len(spelled) == 1, (
        f"the live-state fragment is written in more than one module: {spelled}"
    )
    path, count = spelled[0]
    assert path.name == "situations.py", path
    assert count == 1, (
        f"{path.name} writes the live-state fragment {count} times. It is a module constant so "
        "that there is one of it; a second copy is the one a later release forgets to widen."
    )


async def test_the_active_member_predicate_agrees_with_all_cleared(store: Store) -> None:
    """`HAS_ACTIVE` and `all_cleared` are two expressions of one question (v0.16.2, #274).

    The correlated subquery answers it for a population and the method answers it for a row, and
    they are separate SQL. Two expressions of one question are two chances to answer it
    differently — which is the shape of the defect this release repairs, one level down: the sweep
    had a method that answered its question and asked a query that did not.

    Driven over every arm that distinguishes them, **including the empty bag**, which is the input
    that makes `all_cleared` answer True about a situation nothing ever cleared.
    """
    from netcorenoc.store.situations import HAS_ACTIVE

    empty = await store.create_situation(ts=1.0)
    active = await store.create_situation(ts=1.0)
    cleared = await store.create_situation(ts=1.0)
    mixed = await store.create_situation(ts=1.0)
    for sid, device, on in (
        (active, "10.1.0.1", True),
        (cleared, "10.1.0.2", False),
        (mixed, "10.1.0.3", True),
        (mixed, "10.1.0.4", False),
    ):
        raised = await store.ingest(util.event(device=device, ts=1.0))
        await store.add_alarm_to_situation(sid, raised.alarm_id)
        if not on:
            await store.conn.execute(
                "UPDATE alarm SET status='cleared', cleared_at=? WHERE id=?", (2.0, raised.alarm_id)
            )
    cur = await store.conn.execute(
        f"SELECT id FROM situation WHERE {HAS_ACTIVE}"  # nosec B608 - module literal
    )
    by_fragment = {int(r[0]) for r in await cur.fetchall()}
    for sid in (empty, active, cleared, mixed):
        assert (sid in by_fragment) is not await store.all_cleared(sid), (
            f"the fragment and all_cleared disagree about situation {sid}"
        )
    # Both answers are exercised, or the agreement above is agreement about one case.
    assert by_fragment == {active, mixed}
