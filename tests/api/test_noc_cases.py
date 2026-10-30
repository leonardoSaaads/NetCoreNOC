"""NOC → Cases through the HTTP API and the case loop (ADRs #459-#461).

What the NOC asked of a case, each answered by driving the real routes and the real loop:

* a situation that becomes `open` has a case, in the same transaction; the loop puts it in the NOC
  queue, and the take clock counts from the instant it opened — met, or late by how much;
* only someone who works cases takes one; only its owner edits, sets pending or resolves it;
* pending needs the complete customer update and a next update in the future; when that update
  comes, or a person posts on the feed, the case is back in the NOC queue — an agent's post is not;
* a feed post is read as its visibility says: `internal` by the NOC, `specified` by the people it
  names, `all` by everyone — and a viewer is shown networks, not addresses, and no names;
* each organization may have its own executive summary;
* resolving closes the situation, and clears its alarms only when verified clear on the device;
* a situation that resolves on the broker's side resolves an untaken case, and asks an owner to
  validate; a case outside the caller's scope is a 404, like one that does not exist;
* a case hears its alarms (ADR #474): what cleared, raised again or joined since it last looked is
  an event on its feed, the first look says nothing, and a pending case whose fault came back is
  back in the NOC queue while a case being worked stays with its owner.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from netcorenoc.api.models_cases import StatusIn
from netcorenoc.engine.noc import case_loop, cases
from netcorenoc.store import Store

import authutil
import util


async def _open_situation(
    store: Store, devices: tuple[str, ...] = ("10.0.0.1",), severity: str | None = "critical"
) -> int:
    """A situation of one alarm per device, opened the way an operator opens one."""
    now = time.time()
    async with store.lock:
        sid = await store.create_situation(now, None)
        for n, device in enumerate(devices):
            alarm = await store.ingest(util.event(device=device, instance=str(n), ts=now))
            await store.add_alarm_to_situation(sid, alarm.alarm_id)
        if severity is not None:
            await store.conn.execute("UPDATE situation SET severity=? WHERE id=?", (severity, sid))
        await store.promote_situation(sid, now, "promote")
        await store.commit()
    return sid


async def _clients(store: Store) -> tuple[Any, dict[str, httpx.AsyncClient]]:
    _engine, _queue, app = await authutil.make_env(store)
    return app, {
        role: await authutil.client_as(app, role) for role in ("viewer", "editor", "admin")
    }


async def _close(clients: dict[str, httpx.AsyncClient]) -> None:
    for client in clients.values():
        await client.aclose()


COMPLETE = {
    "title": "Fiber cut core-1 → core-2",
    "description": "LOS on 1/1/3 at 10.0.0.1 since 09:12.",
    "executive_summary": "Link down between two core sites",
    "current_analysis": "Rx power -40 dBm on 10.0.0.1 port 1/1/3: no light.",
    "noc_next_step": "Check the far end's Tx and the patch panel.",
    "customer_next_step": "Schedule a maintenance window for a technician.",
}


async def test_an_opened_situation_is_queued_and_the_take_clock_runs(store: Store) -> None:
    sid = await _open_situation(store)
    async with store.reading():
        case = await store.noc_case(sid)
    assert case is not None and case["status"] == "new" and case["priority"] == "critical"
    moved = await case_loop.tick(store)
    assert moved["dispatched"] == 1
    _app, clients = await _clients(store)
    try:
        queue = (await clients["viewer"].get("/api/noc/cases", params={"view": "queue"})).json()
        row = next(c for c in queue["cases"] if c["id"] == sid)
        assert row["status"] == "queue" and row["clocks"]["take"]["state"] == "running"
        assert row["clocks"]["take"]["target_s"] == 25 * 60
        assert queue["counts"]["queue"] >= 1 and "pending_customer" in queue["statuses"]
        # A viewer reads the case; taking it is the NOC's.
        assert (await clients["viewer"].post(f"/api/noc/cases/{sid}/take")).status_code == 403
        taken = await clients["editor"].post(f"/api/noc/cases/{sid}/take")
        assert taken.status_code == 200, taken.text
        body = taken.json()
        assert body["status"] == "working" and body["owner"]["you"] is True
        assert body["clocks"]["take"]["state"] == "met"
        assert body["clocks"]["document"]["state"] == "running"
        # ADR #470: the customer update is the NOC's — another worker keeps it current without
        # taking the case — but its status stays the owner's; an admin may act on any case.
        token = await authutil.make_token(store, "second-worker", "editor")
        other = authutil.new_client(_app)
        other.headers["Authorization"] = f"Bearer {token}"
        helped = await other.post(f"/api/noc/cases/{sid}", json={"title": "x"})
        assert helped.status_code == 200 and "edit" in helped.json()["actions"]
        assert "pend" not in helped.json()["actions"]
        refused = await other.post(
            f"/api/noc/cases/{sid}/status", json={"status": "pending_customer"}
        )
        assert refused.status_code == 403 and "take the case first" in refused.json()["detail"]
        await other.aclose()
        allowed = await clients["admin"].post(f"/api/noc/cases/{sid}", json={"title": "y"})
        assert allowed.status_code == 200
    finally:
        await _close(clients)


async def test_a_late_take_reports_the_overrun(store: Store) -> None:
    sid = await _open_situation(store, severity="major")
    await case_loop.tick(store)
    async with store.lock:
        await store.conn.execute(
            "UPDATE noc_case_cycle SET entered_at = entered_at - interval '90 minutes' "
            "WHERE situation_id = ?",
            (sid,),
        )
        await store.commit()
    _app, clients = await _clients(store)
    try:
        before = (await clients["editor"].get(f"/api/noc/cases/{sid}")).json()
        assert before["clocks"]["take"]["state"] == "late"
        assert 29 * 60 < before["clocks"]["take"]["overrun_s"] < 31 * 60
        after = (await clients["editor"].post(f"/api/noc/cases/{sid}/take")).json()
        assert after["clocks"]["take"]["state"] == "missed"
        # ADR #470: the KPIs are a lead's — an admin's, a named editor's, an agent's.
        closed = await clients["editor"].get("/api/noc/cases/kpis")
        assert closed.status_code == 403 and "NOC lead" in closed.json()["detail"]
        kpis = (await clients["admin"].get("/api/noc/cases/kpis")).json()
        assert kpis["take"]["missed"] >= 1 and kpis["take"]["overrun_s"] >= 29 * 60
        assert kpis["by_priority"]["major"]["missed"] >= 1
    finally:
        await _close(clients)


async def test_pending_needs_the_customer_update_and_returns_to_the_queue(store: Store) -> None:
    sid = await _open_situation(store)
    await case_loop.tick(store)
    _app, clients = await _clients(store)
    editor = clients["editor"]
    try:
        await editor.post(f"/api/noc/cases/{sid}/take")
        early = await editor.post(
            f"/api/noc/cases/{sid}/status", json={"status": "pending_customer"}
        )
        assert early.status_code == 409 and "title" in early.json()["detail"]
        past = await editor.post(f"/api/noc/cases/{sid}", json={"next_update_at": time.time() - 5})
        assert past.status_code == 400
        when = "2099-01-01T00:00:00Z"
        filled = await editor.post(
            f"/api/noc/cases/{sid}", json={**COMPLETE, "next_update_at": when}
        )
        assert filled.status_code == 200, filled.text
        assert (
            filled.json()["missing"] == [] and filled.json()["clocks"]["document"]["state"] == "met"
        )
        naive = await editor.post(
            f"/api/noc/cases/{sid}", json={"next_update_at": "2099-01-01T00:00"}
        )
        assert naive.status_code == 422, "an instant without an offset names no instant"
        stale = await editor.post(f"/api/noc/cases/{sid}", json={"title": "y", "version": 1})
        assert stale.status_code == 409 and "version" in stale.json()["detail"]
        guessed = await editor.post(
            f"/api/noc/cases/{sid}/status", json={"status": "Waiting on the field team"}
        )
        assert guessed.status_code == 422, "a status outside the vocabulary is refused"
        pend = await editor.post(
            f"/api/noc/cases/{sid}/status", json={"status": "pending_maintenance"}
        )
        assert pend.status_code == 200 and pend.json()["status"] == "pending_maintenance"
        # An agent's post does not requeue; a person's does.
        token = await authutil.make_token(store, "noc-agent", "editor")
        agent = authutil.new_client(_app)
        agent.headers["Authorization"] = f"Bearer {token}"
        said = await agent.post(
            f"/api/noc/cases/{sid}/feed", json={"body": "Window booked.", "visibility": "internal"}
        )
        assert said.status_code == 200 and said.json()["requeued"] is False
        await agent.aclose()
        posted = await clients["viewer"].post(
            f"/api/noc/cases/{sid}/feed",
            json={"body": "Our technician can go Friday 10:00.", "visibility": "all"},
        )
        assert posted.status_code == 200 and posted.json()["requeued"] is True
        case = (await editor.get(f"/api/noc/cases/{sid}")).json()
        assert case["status"] == "queue" and case["owner"] is None
        assert [c["reason"] for c in case["cycles"]] == ["opened", "customer_post"]
        # Taken again and pending again; this time the next update brings it back.
        await editor.post(f"/api/noc/cases/{sid}/take")
        await editor.post(
            f"/api/noc/cases/{sid}/status",
            json={"status": "pending_customer", "next_update_at": time.time() + 3600},
        )
        moved = await case_loop.tick(store, now=lambda: time.time() + 7200)
        assert moved["requeued"] == 1
        case = (await editor.get(f"/api/noc/cases/{sid}")).json()
        assert case["status"] == "queue" and case["cycles"][-1]["reason"] == "next_update"
    finally:
        await _close(clients)


async def test_each_post_is_read_as_its_visibility_says(store: Store) -> None:
    sid = await _open_situation(store)
    await case_loop.tick(store)
    _app, clients = await _clients(store)
    viewer, editor, admin = clients["viewer"], clients["editor"], clients["admin"]
    try:
        await editor.post(f"/api/noc/cases/{sid}/take")
        await editor.post(
            f"/api/noc/cases/{sid}/feed",
            json={"body": "Internal: suspect the SFP on 10.0.0.1.", "visibility": "internal"},
        )
        people = (await editor.get("/api/noc/people")).json()["people"]
        viewer_id = next(p["id"] for p in people if p["username"] == authutil.ROLE_USER["viewer"])
        await editor.post(
            f"/api/noc/cases/{sid}/feed",
            json={"body": "For you only.", "visibility": "specified", "readers": [viewer_id]},
        )
        await editor.post(
            f"/api/noc/cases/{sid}/feed",
            json={"body": "We are checking 10.0.0.1 (core-1).", "visibility": "all"},
        )
        seen = (await viewer.get(f"/api/noc/cases/{sid}/feed")).json()
        bodies = [p["body"] for p in seen["posts"] if p["kind"] == "post"]
        assert bodies == ["We are checking 10.0.0.0/24 (core-1).", "For you only."]
        assert all(p["author"]["name"] in ("NOC engineer", "NetCoreNOC") for p in seen["posts"])
        assert all(p["author"]["ref"] is None for p in seen["posts"])
        assert seen["can_post"] == ["all"]
        noc = (await editor.get(f"/api/noc/cases/{sid}/feed")).json()
        assert any("10.0.0.1" in p["body"] for p in noc["posts"] if p["visibility"] == "internal")
        assert any(p["kind"] == "event" for p in noc["posts"]), "the history is in the feed"
        assert len((await admin.get(f"/api/noc/cases/{sid}/feed")).json()["posts"]) == len(
            noc["posts"]
        )
        refused = await viewer.post(
            f"/api/noc/cases/{sid}/feed", json={"body": "x", "visibility": "internal"}
        )
        assert refused.status_code == 403
        nobody = await editor.post(
            f"/api/noc/cases/{sid}/feed",
            json={"body": "x", "visibility": "specified", "readers": [999999]},
        )
        assert nobody.status_code == 400
        assert (await viewer.get("/api/noc/people")).status_code == 403
    finally:
        await _close(clients)


async def test_each_organization_may_have_its_own_executive_summary(store: Store) -> None:
    sid = await _open_situation(store, devices=("10.0.0.1", "10.0.9.9"))
    await case_loop.tick(store)
    async with store.lock:
        acme = await store.create_organization("Acme Telecom", "acme", time.time())
        cur = await store.conn.execute("SELECT id FROM ne WHERE ip = '10.0.9.9'::inet")
        row = await cur.fetchone()
        assert row is not None
        await store.set_ne_organization(int(row[0]), acme)
        await store.commit()
    _app, clients = await _clients(store)
    editor = clients["editor"]
    try:
        await editor.post(f"/api/noc/cases/{sid}/take")
        done = await editor.post(
            f"/api/noc/cases/{sid}",
            json={
                "executive_summary": "Core link down",
                "summaries": {str(acme): "Your Recife link"},
            },
        )
        assert done.status_code == 200, done.text
        orgs = {o["name"]: o for o in done.json()["organizations"]}
        assert orgs["Acme Telecom"]["reads"] == "Your Recife link"
        assert all(o["reads"] == "Core link down" for n, o in orgs.items() if n != "Acme Telecom")
        stray = await editor.post(f"/api/noc/cases/{sid}", json={"summaries": {"424242": "x"}})
        assert stray.status_code == 400
    finally:
        await _close(clients)


async def test_resolving_closes_the_situation_and_clears_only_when_verified(store: Store) -> None:
    kept = await _open_situation(store, devices=("10.0.0.1",))
    cleared = await _open_situation(store, devices=("10.0.0.2",))
    await case_loop.tick(store)
    _app, clients = await _clients(store)
    editor = clients["editor"]
    try:
        for sid in (kept, cleared):
            await editor.post(f"/api/noc/cases/{sid}/take")
            await editor.post(f"/api/noc/cases/{sid}", json=COMPLETE)
        unknown = await editor.post(
            f"/api/noc/cases/{kept}/resolve", json={"cause": "gremlins", "solution": "x"}
        )
        assert unknown.status_code == 400
        first = await editor.post(
            f"/api/noc/cases/{kept}/resolve",
            json={"cause": "carrier_fiber_cut", "solution": "Spliced."},
        )
        assert first.status_code == 200, first.text
        assert first.json()["closed"] == {"cleared": 0, "situation_closed": True}
        second = await editor.post(
            f"/api/noc/cases/{cleared}/resolve",
            json={
                "cause": "cleared_before_isolation",
                "solution": "No alarm on the device.",
                "alarms_verified_clear": True,
            },
        )
        assert second.json()["closed"] == {"cleared": 1, "situation_closed": True}
        async with store.reading():
            rows = {
                int(r["id"]): r
                for r in await (
                    await store.conn.execute(
                        "SELECT id, status, resolution FROM situation WHERE id = ANY(?)",
                        (sorted((kept, cleared)),),
                    )
                ).fetchall()
            }
            active = await store.noc_case_members_active(kept)
        assert rows[kept]["resolution"] == "operator" and active, "alarms left as they were"
        assert rows[cleared]["resolution"] == "manual_clear"
        case = (await editor.get(f"/api/noc/cases/{kept}")).json()
        assert case["status"] == "resolved" and case["resolution"]["cause"] == "carrier_fiber_cut"
        # A day later, untouched, it is closed.
        await case_loop.tick(store, now=lambda: time.time() + 2 * 86400)
        assert (await editor.get(f"/api/noc/cases/{kept}")).json()["status"] == "closed"
        actions = {r["action"] for r in await _audit(store)}
        assert {"noc.case.take", "noc.case.update", "noc.case.resolve", "alarm.clear"} <= actions
    finally:
        await _close(clients)


async def test_a_situation_resolving_on_its_own_resolves_or_asks_to_validate(store: Store) -> None:
    untaken = await _open_situation(store, devices=("10.0.1.1",))
    owned = await _open_situation(store, devices=("10.0.1.2",))
    await case_loop.tick(store)
    _app, clients = await _clients(store)
    try:
        await clients["editor"].post(f"/api/noc/cases/{owned}/take")
        async with store.lock:
            for sid in (untaken, owned):
                await store.resolve_situation(sid, "self_cleared", time.time())
            await store.commit()
        moved = await case_loop.tick(store)
        assert moved == {**moved, "resolved": 1, "validate": 1}
        a = (await clients["viewer"].get(f"/api/noc/cases/{untaken}")).json()
        assert a["status"] == "resolved" and a["resolution"]["how"] == "cleared"
        assert a["clocks"]["take"]["state"] == "withdrawn"
        b = (await clients["editor"].get(f"/api/noc/cases/{owned}")).json()
        assert b["status"] == "validate" and b["owner"]["you"] is True
        # The feed says why in words, never the Broker's code (ADR #476).
        feed = (await clients["editor"].get(f"/api/noc/cases/{owned}/feed")).json()["posts"]
        said = next(p["body"] for p in feed if p["body"].startswith("The situation resolved"))
        assert "every alarm cleared" in said and "self_cleared" not in said
    finally:
        await _close(clients)


async def test_a_case_outside_the_callers_scope_does_not_exist(store: Store) -> None:
    inside = await _open_situation(store, devices=("10.0.0.1",))
    outside = await _open_situation(store, devices=("192.0.2.7",))
    await case_loop.tick(store)
    _app, clients = await _clients(store)
    try:
        await authutil.set_scope(store, {"roles": {"viewer": ["10.0.0.0/24"]}})
        viewer = clients["viewer"]
        assert (await viewer.get(f"/api/noc/cases/{inside}")).status_code == 200
        assert (await viewer.get(f"/api/noc/cases/{outside}")).status_code == 404
        assert (await viewer.get(f"/api/noc/cases/{outside}/feed")).status_code == 404
        listed = {c["id"] for c in (await viewer.get("/api/noc/cases")).json()["cases"]}
        assert inside in listed and outside not in listed
        assert (await viewer.get("/api/noc/cases/424242")).status_code == 404
    finally:
        await _close(clients)


async def _audit(store: Store) -> list[dict[str, Any]]:
    async with store.reading():
        return await store.audit_all()


def test_the_pending_statuses_an_agent_reads_are_the_ones_accepted() -> None:
    """The schema lists exactly the statuses a case may wait in, so an agent reading the tool over
    MCP picks one instead of learning the vocabulary from a refusal (ADR #476)."""
    schema = StatusIn.model_json_schema()["properties"]["status"]
    assert set(schema["enum"]) == set(cases.PENDING)
    assert "carrier" in schema["description"]


async def _alarm_events(client: httpx.AsyncClient, sid: int) -> list[str]:
    posts = (await client.get(f"/api/noc/cases/{sid}/feed")).json()["posts"]
    return [p["body"] for p in posts if p["body"].startswith("Its alarms on the Broker")]


async def test_a_case_hears_its_alarms_and_a_pending_one_comes_back(store: Store) -> None:
    sid = await _open_situation(store, devices=("10.0.0.1", "10.0.0.2"))
    await case_loop.tick(store)
    _app, clients = await _clients(store)
    editor = clients["editor"]
    try:
        await editor.post(f"/api/noc/cases/{sid}/take")
        when = time.time() + 3600
        await editor.post(f"/api/noc/cases/{sid}", json={**COMPLETE, "next_update_at": when})
        pend = await editor.post(
            f"/api/noc/cases/{sid}/status", json={"status": "pending_fiber_provider"}
        )
        assert pend.status_code == 200, pend.text
        await case_loop.tick(store)
        assert await _alarm_events(editor, sid) == [], "the first look is a baseline"

        async with store.lock:
            cur = await store.conn.execute(
                "SELECT a.id FROM alarm a JOIN ne n ON n.id = a.ne_id WHERE n.ip = ?",
                ("10.0.0.1",),
            )
            row = await cur.fetchone()
            assert row is not None
            first = int(row[0])
            await store.conn.execute(
                "UPDATE alarm SET status = 'cleared', cleared_at = now() WHERE id = ?", (first,)
            )
            await store.commit()
        moved = await case_loop.tick(store)
        assert moved["told"] == 1 and moved["requeued"] == 0, "a clear is told, not requeued"
        said = await _alarm_events(editor, sid)
        assert len(said) == 1 and "cleared: " in said[0] and "10.0.0.1 (0)" in said[0]
        case = (await editor.get(f"/api/noc/cases/{sid}")).json()
        assert case["status"] == "pending_fiber_provider"

        # The fault comes back while the case waits on the provider: back in the NOC queue.
        async with store.lock:
            await store.ingest(util.event(device="10.0.0.1", instance="0"))
            await store.commit()
        moved = await case_loop.tick(store)
        assert moved["requeued"] == 1
        said = await _alarm_events(editor, sid)
        assert len(said) == 2 and "raised again: " in said[0], said
        case = (await editor.get(f"/api/noc/cases/{sid}")).json()
        assert case["status"] == "queue" and case["cycles"][-1]["reason"] == "alarm"
        assert case["cycles"][-1]["reason_label"].startswith("An alarm of its situation")

        # Worked, a new alarm joins: the owner is told and keeps the case.
        await editor.post(f"/api/noc/cases/{sid}/take")
        async with store.lock:
            alarm = await store.ingest(util.event(device="10.0.0.3", instance="7"))
            await store.add_alarm_to_situation(sid, alarm.alarm_id)
            await store.commit()
        moved = await case_loop.tick(store)
        assert moved["told"] == 1 and moved["requeued"] == 0
        said = await _alarm_events(editor, sid)
        assert "joined: " in said[0] and "10.0.0.3 (7)" in said[0]
        assert (await editor.get(f"/api/noc/cases/{sid}")).json()["status"] == "working"
        assert (await case_loop.tick(store))["told"] == 0, "nothing new, nothing said"

        # A flap between two looks — raised and cleared again before the loop read it — is heard
        # by the alarm's count, not lost because it was off at both looks.
        async def clear(alarm_id: int) -> None:
            await store.conn.execute(
                "UPDATE alarm SET status = 'cleared', cleared_at = now() WHERE id = ?", (alarm_id,)
            )

        async with store.lock:
            await clear(alarm.alarm_id)
            await store.commit()
        assert (await case_loop.tick(store))["told"] == 1
        async with store.lock:
            await store.ingest(util.event(device="10.0.0.3", instance="7"))
            await clear(alarm.alarm_id)
            await store.commit()
        assert (await case_loop.tick(store))["told"] == 1
        said = await _alarm_events(editor, sid)
        assert "raised again: " in said[0] and "cleared since" in said[0], said[0]
        # The case's own history: the NOC's, like every event — never a customer's to read.
        assert await _alarm_events(clients["viewer"], sid) == []
    finally:
        await _close(clients)
