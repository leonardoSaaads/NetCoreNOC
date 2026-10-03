"""The two acknowledgements that change what is shown and never what is known (v0.22.0).

Both items the brief marked **push back** are here, each asserted as the refinement it became:

* **Item 1 — dismissing a warning is an honest snooze, not a delete** (ADR #387). Per user, for an
  interval, keyed on the warning's text so a changed warning returns. **A security-posture warning's
  snooze always expires** and an admin can always see who snoozed one: security posture is never
  silently invisible.
* **Item 8 — the "outlived a maintenance window" marker stays, correctly scoped** (ADR #388). It is
  drawn on an alarm that is still active, was surfaced from a window and has not recurred since; an
  operator who has seen it can acknowledge it; the alarm itself is untouched.
"""

from __future__ import annotations

from typing import Any

from netcorenoc.crosscutting import posture
from netcorenoc.store import Store
from netcorenoc.store.attention import notice_digest

import authutil
import util

BASE = 1_700_000_000.0
OPERATIONAL = "The trap queue is 80% full."
DAY = 24 * 3600.0


def _warnings() -> list[str]:
    return [posture.ALLOWLIST_EMPTY, OPERATIONAL]


async def _user_id(store: Store, username: str) -> int:
    async with store.lock:
        cur = await store.conn.execute("SELECT id FROM user WHERE username=?", (username,))
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


# --- item 1 --------------------------------------------------------------------------------------


async def test_a_security_warning_cannot_be_snoozed_until_it_changes(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store, warnings=_warnings)
    viewer = await authutil.client_as(app, "viewer")
    try:
        notices = (await viewer.get("/api/notices")).json()["notices"]
        security = next(n for n in notices if n["security"])
        operational = next(n for n in notices if not n["security"])
        refused = await viewer.post(
            "/api/notices/snooze", json={"digest": security["digest"], "mode": "change"}
        )
        assert refused.status_code == 422, refused.text
        # The control: the same mode is accepted for an operational warning.
        ok = await viewer.post(
            "/api/notices/snooze", json={"digest": operational["digest"], "mode": "change"}
        )
        assert ok.status_code == 200 and ok.json()["until"] is None, ok.text
        timed = await viewer.post(
            "/api/notices/snooze", json={"digest": security["digest"], "mode": "7d"}
        )
        assert timed.status_code == 200 and timed.json()["until"] is not None
        after = {n["digest"]: n for n in (await viewer.get("/api/notices")).json()["notices"]}
        # Snoozed is a state of the row, not a disappearance: both warnings are still listed.
        assert after[security["digest"]]["snoozed"]["mode"] == "7d"
        assert after[operational["digest"]]["snoozed"]["mode"] == "change"
    finally:
        await viewer.aclose()


async def test_a_snoozed_security_warning_expires_and_the_admin_sees_who_snoozed_it(
    store: Store,
) -> None:
    _engine, _queue, app = await authutil.make_env(store, warnings=_warnings)
    viewer_id = await _user_id(store, authutil.ROLE_USER["viewer"])
    async with store.lock:
        await store.snooze_notice(
            user_id=viewer_id, text=posture.ALLOWLIST_EMPTY, security=True, mode="24h", now=BASE
        )
        await store.commit()
        assert await store.snoozes_for(viewer_id, BASE + DAY - 1)
        assert await store.snoozes_for(viewer_id, BASE + DAY + 1) == {}, "it did not expire"

    admin = await authutil.client_as(app, "admin")
    editor = await authutil.client_as(app, "editor")
    viewer = await authutil.client_as(app, "viewer")
    try:
        await viewer.post(
            "/api/notices/snooze",
            json={"digest": notice_digest(posture.ALLOWLIST_EMPTY), "mode": "24h"},
        )
        seen = (await admin.get("/api/notices")).json()["security_snoozes"]
        assert seen and seen[0]["username"] == authutil.ROLE_USER["viewer"], seen
        assert (await editor.get("/api/notices")).json()["security_snoozes"] is None
    finally:
        for client in (admin, editor, viewer):
            await client.aclose()


async def test_a_changed_warning_returns_and_a_digest_nobody_is_shown_is_refused(
    store: Store,
) -> None:
    current = [OPERATIONAL]
    _engine, _queue, app = await authutil.make_env(store, warnings=lambda: list(current))
    viewer = await authutil.client_as(app, "viewer")
    try:
        digest = notice_digest(OPERATIONAL)
        assert (
            await viewer.post("/api/notices/snooze", json={"digest": digest, "mode": "change"})
        ).status_code == 200
        current[0] = "The trap queue is 95% full."
        fresh = (await viewer.get("/api/notices")).json()["notices"]
        assert fresh[0]["snoozed"] is None, "a changed warning stayed snoozed"
        # No oracle: a digest of text this appliance is not showing is refused, not stored.
        ghost = await viewer.post(
            "/api/notices/snooze", json={"digest": notice_digest("secret"), "mode": "24h"}
        )
        assert ghost.status_code == 404
        # Restore is the caller's own; a digest they never snoozed is the same 404.
        assert (await viewer.delete(f"/api/notices/snooze/{digest}")).status_code == 200
        assert (await viewer.delete(f"/api/notices/snooze/{digest}")).status_code == 404
    finally:
        await viewer.aclose()

    async with store.lock:
        cur = await store.conn.execute(
            "SELECT action FROM audit_log WHERE action LIKE 'notice.%' ORDER BY id"
        )
        actions = [str(r[0]) for r in await cur.fetchall()]
    assert actions == ["notice.snooze", "notice.unsnooze"], actions


# --- item 8 --------------------------------------------------------------------------------------


async def _surfaced(store: Store, engine: Any, queue: Any) -> tuple[int, int, int]:
    """A situation whose first alarm was surfaced from a window: `(sid, alarm_id, window_id)`."""
    from netcorenoc.store.maintenance_windows import WindowDraft

    await util.drive(engine, queue, util.fixture_events("fiber_cut.json", BASE))
    async with store.lock:
        cur = await store.conn.execute(
            "SELECT a.id, a.ne_id, sa.situation_id FROM alarm a "
            "JOIN situation_alarm sa ON sa.alarm_id=a.id WHERE a.status='active' ORDER BY a.id"
        )
        alarm_id, ne_id, sid = (int(v) for v in tuple(await cur.fetchone() or ()))
        draft = WindowDraft(
            name="splice",
            description="",
            organization_id=await store.default_organization_id(),
            tz="UTC",
            starts_at=BASE - 7200,
            ends_at=BASE - 60,
            all_day=False,
            patch_s=0.0,
            ledger_enabled=True,
            visibility="editors",
            owner_ref="user:1",
            owner_role="editor",
            created_by_agent=False,
            needs_confirmation=False,
            status="ended",
            idempotency_key=None,
        )
        wid = await store.create_maintenance_window(draft, BASE - 7200)
        await store.set_window_targets(wid, [ne_id])
        await store.conn.execute(
            "UPDATE alarm SET surfaced_from_window_id=?, surfaced_at=last_seen WHERE id=?",
            (wid, alarm_id),
        )
        await store.commit()
    return sid, alarm_id, wid


async def _marker(store: Store, sid: int, alarm_id: int) -> int | None:
    async with store.lock:
        detail = await store.situation_detail(sid)
    assert detail is not None
    row = next(m for m in detail["alarms"] if int(m["id"]) == alarm_id)
    marker = row["outlived_window_id"]
    return None if marker is None else int(marker)


async def test_the_outlived_marker_is_drawn_until_acknowledged_and_the_alarm_is_untouched(
    store: Store,
) -> None:
    engine, queue, app = await authutil.make_env(store)
    sid, alarm_id, wid = await _surfaced(store, engine, queue)
    assert await _marker(store, sid, alarm_id) == wid, "a fault that outlived a window is hidden"

    viewer = await authutil.client_as(app, "viewer")
    editor = await authutil.client_as(app, "editor")
    try:
        assert (await viewer.post(f"/api/alarms/{alarm_id}/outlived/ack")).status_code == 403
        done = await editor.post(f"/api/alarms/{alarm_id}/outlived/ack")
        assert done.status_code == 200, done.text
        assert (await editor.post(f"/api/alarms/{alarm_id}/outlived/ack")).status_code == 409
        assert (await editor.post("/api/alarms/999999/outlived/ack")).status_code == 404
    finally:
        await viewer.aclose()
        await editor.aclose()

    assert await _marker(store, sid, alarm_id) is None
    async with store.lock:
        cur = await store.conn.execute(
            "SELECT status, surfaced_from_window_id FROM alarm WHERE id=?", (alarm_id,)
        )
        status, surfaced = tuple(await cur.fetchone() or ())
    assert status == "active" and surfaced == wid, "the acknowledgement changed the alarm"


async def test_the_marker_stops_when_the_fault_recurs_or_clears(store: Store) -> None:
    """Scoped, not permanent: once the fault has been seen again after the window, it is a fault
    of now, and the marker that says *"this is left over from planned work"* no longer applies."""
    engine, queue, _app = await authutil.make_env(store)
    sid, alarm_id, wid = await _surfaced(store, engine, queue)
    assert await _marker(store, sid, alarm_id) == wid
    async with store.lock:
        await store.conn.execute(
            "UPDATE alarm SET last_seen=last_seen + 60 WHERE id=?", (alarm_id,)
        )
        await store.commit()
    assert await _marker(store, sid, alarm_id) is None, "a recurrence kept the marker"
    async with store.lock:
        await store.conn.execute(
            "UPDATE alarm SET last_seen=surfaced_at, status='cleared' WHERE id=?", (alarm_id,)
        )
        await store.commit()
    assert await _marker(store, sid, alarm_id) is None, "a cleared alarm kept the marker"
