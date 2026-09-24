"""Four principals driven across the whole maintenance surface (v0.21.1).

    anonymous · viewer · editor · admin · an agent holding a service token

`tests/test_maintenance_api.py` asserts the **contract** — what a window is, what an instant must
look like, what the preview counts. This asserts **who**. They are separate files because they
fail for separate reasons: a broken contract is a bug in the resource, and a broken matrix is a
bug in the authorization table, and a suite that mixes them makes a reviewer read both to learn
which one moved.

## Why a matrix and four days, rather than one or the other

The matrix is exhaustive and shallow: every route, every principal, one question — *were they
refused?* It is the part that catches a route added without a capability, and it is the part that
would have caught nothing this release, because v0.21.0's authorization table was right.

The four narratives are the opposite. Each one is a role doing its actual job from end to end,
and that is what found F151, F152 and F153: a viewer whose list said *"showing 1 of 3"*, an
editor who read back a window they could not open, and an auditor reading `.update` on a log
where nobody had updated anything. None of those is a refusal; all of them are the behaviour
around a refusal, which is the half a permission matrix cannot see.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from netcorenoc.store import Store

import authutil

BRASILIA = ZoneInfo("America/Sao_Paulo")

#: Every principal this file drives. `None` is anonymous; `"agent"` is a service token of editor
#: rank, which is the credential Phase 2 reaches this appliance with.
PRINCIPALS = ("anonymous", "viewer", "editor", "admin", "agent")


def _in(seconds: float) -> str:
    """An instant `seconds` from now, RFC 3339 **in Brasília with its offset** — the only form.

    Relative to the real clock rather than to a fixed day, because half of what is asserted here
    turns on a window being in force *now*: a route that asks `window_markers` about the present
    reads the process clock and cannot be handed a fixture's.
    """
    return datetime.fromtimestamp(time.time() + seconds, BRASILIA).isoformat()


def _body(targets: list[int], **over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "card swap",
        "tz": "America/Sao_Paulo",
        "starts_at": _in(3600),
        "ends_at": _in(3600 * 3),
        "targets": targets,
        "rules": [],
    }
    body.update(over)
    return body


async def _hosts(store: Store, *addresses: str) -> list[int]:
    ids = []
    async with store.lock:
        for address in addresses:
            ids.append(await store.ne_id(address, time.time()))
        await store.commit()
    return ids


async def _client(app: object, store: Store, who: str) -> httpx.AsyncClient:
    """One authenticated client per principal, through the credential that principal really uses.

    The agent presents a **bearer token** rather than a faked `Principal`, because two behaviours
    in this feature turn on `Principal.is_token` — an agent-created window is marked, and an agent
    may not confirm one over six hours — and a test that mocked the principal would be asserting
    against its own mock.
    """
    if who == "anonymous":
        return authutil.new_client(app)
    if who == "agent":
        client = authutil.new_client(app)
        token = await authutil.make_token(store, "bot", "editor")
        client.headers["Authorization"] = f"Bearer {token}"
        return client
    return await authutil.client_as(app, who)


# -- the matrix ----------------------------------------------------------------------------------


#: `(method, path, body, principals who may)`. The path carries `{wid}` and `{ne}`, filled from
#: the fixture below. **Who may** is the claim; what the route then answers is not this test's
#: business, which is why a 409 counts as permitted — the caller got past the door.
READERS = frozenset({"viewer", "editor", "admin", "agent"})
WRITERS = frozenset({"editor", "admin", "agent"})
HUMANS = frozenset({"editor", "admin"})
ADMINS = frozenset({"admin"})

SURFACE: tuple[tuple[str, str, str | None, frozenset[str]], ...] = (
    ("GET", "/api/maintenance-windows", None, READERS),
    ("GET", "/api/maintenance-windows/{wid}", None, READERS),
    ("POST", "/api/maintenance-windows/preview", "body", READERS),
    ("POST", "/api/maintenance-windows", "body", WRITERS),
    ("POST", "/api/maintenance-windows/{wid}", "body", WRITERS),
    ("POST", "/api/maintenance-windows/{wid}/confirm", None, HUMANS),
    ("POST", "/api/maintenance-windows/{wid}/extend", "extend", WRITERS),
    ("POST", "/api/maintenance-windows/{wid}/cancel", None, WRITERS),
    ("POST", "/api/maintenance-windows/{wid}/end", None, WRITERS),
    ("GET", "/api/organizations", None, READERS),
    ("POST", "/api/organizations", "org", ADMINS),
    ("POST", "/api/entities/{ne}/organization", "ne_org", ADMINS),
    ("GET", "/api/timezones", None, READERS),
)

#: `confirm` is the one row where the refusal is not the capability table's. An agent holds
#: `mw.confirm` by rank and is turned away by D6's human gesture (ADR #370), so the matrix would
#: read it as a missing capability. It is asserted where it belongs — in the agent's own day.
CONFIRM_IS_THE_AGENTS_OWN_STORY = "/api/maintenance-windows/{wid}/confirm"


@pytest.mark.parametrize("who", PRINCIPALS)
async def test_every_maintenance_route_answers_each_principal_the_way_the_table_says(
    store: Store, who: str
) -> None:
    """The whole surface, one principal, one question: **were they refused?**

    Refused is 401 or 403 and nothing else. Anything further — a 409 on a window that needs no
    confirming, a 422 on an extend that moves nothing — means the caller was let in, which is
    what this asserts and all it asserts.
    """
    _engine, _q, app = await authutil.make_env(store)
    (ne,) = await _hosts(store, "10.50.0.1")
    author = await authutil.client_as(app, "editor")
    made = await author.post("/api/maintenance-windows", json=_body([ne], visibility="everyone"))
    assert made.status_code == 200, made.text
    wid = made.json()["id"]

    client = await _client(app, store, who)
    bodies = {
        "body": _body([ne], visibility="everyone"),
        "extend": {"ends_at": _in(3600 * 4)},
        "org": {"name": "Tower South", "slug": f"tower-{who}"},
        "ne_org": {"organization_id": 1},
    }
    for method, template, body_key, may in SURFACE:
        if template == CONFIRM_IS_THE_AGENTS_OWN_STORY and who == "agent":
            continue
        path = template.format(wid=wid, ne=ne)
        body = bodies[body_key] if isinstance(body_key, str) else None
        resp = await client.request(method, path, json=body)
        refused = resp.status_code in (401, 403)
        assert refused is (who not in may), (
            f"{who} {method} {path} answered {resp.status_code}: "
            f"{'refused' if refused else 'permitted'} where the table says "
            f"{'permitted' if who in may else 'refused'} — {resp.text[:200]}"
        )
        if who == "anonymous":
            assert resp.status_code in (401, 403), f"anonymous reached {method} {path}"


# -- four days at work ---------------------------------------------------------------------------


async def test_a_viewer_reads_planned_work_and_changes_nothing(store: Store) -> None:
    """The viewer's whole day: see that work exists, see the marker, write nothing.

    Prime directive 4 is the interesting half. A viewer is refused every write **and is still
    told the window is there**, because a host that goes quiet with no marker reads as healthy —
    which is exactly how a maintenance feature hides an outage.
    """
    _engine, _q, app = await authutil.make_env(store)
    (ne,) = await _hosts(store, "10.50.0.1")
    editor = await authutil.client_as(app, "editor")
    wid = (
        await editor.post("/api/maintenance-windows", json=_body([ne], starts_at=_in(-60)))
    ).json()["id"]

    viewer = await authutil.client_as(app, "viewer")
    listed = await viewer.get("/api/maintenance-windows")
    assert listed.status_code == 200
    (row,) = listed.json()["windows"]
    assert row["id"] == wid and row["redacted"] is True, row
    assert "name" not in row, "an editors-only window gave a viewer its name"
    assert row["status"] == "active" and row["target_count"] == 1

    # The details are a 404 — the same answer a window that does not exist gives.
    assert (await viewer.get(f"/api/maintenance-windows/{wid}")).status_code == 404
    assert (await viewer.get("/api/maintenance-windows/999999")).status_code == 404

    # And the marker on the device, which is the whole of what a viewer is owed. `/api/entities`
    # answers a **bare list** — the shape F149 turned on, and the reason `mwdraft.js` normalises
    # at the boundary instead of trusting a fixture's spelling of it.
    entities = await viewer.get("/api/entities")
    assert isinstance(entities.json(), list), entities.text
    marked = [e for e in entities.json() if e.get("maintenance")]
    assert len(marked) == 1 and marked[0]["maintenance"]["window_id"] == wid, entities.text
    assert "name" not in marked[0]["maintenance"], "the marker carried the window's name"

    for method, path, body in (
        ("POST", "/api/maintenance-windows", _body([ne])),
        ("POST", f"/api/maintenance-windows/{wid}", _body([ne])),
        ("POST", f"/api/maintenance-windows/{wid}/cancel", None),
        ("POST", f"/api/maintenance-windows/{wid}/end", None),
        ("POST", "/api/organizations", {"name": "X", "slug": "x"}),
    ):
        assert (await viewer.request(method, path, json=body)).status_code == 403, path


async def test_an_editor_schedules_a_window_and_runs_it_to_the_end(store: Store) -> None:
    """The editor's day, in the order the console walks it: preview, create, extend, end.

    One test rather than five, because the sequence is the thing: v0.21.0 passed every step of
    this in isolation and the form could not complete it (F149).
    """
    _engine, _q, app = await authutil.make_env(store)
    first, second = await _hosts(store, "10.50.0.1", "10.50.0.2")
    editor = await authutil.client_as(app, "editor")

    dry = await editor.post("/api/maintenance-windows/preview", json=_body([first, second]))
    assert dry.status_code == 200, dry.text
    assert dry.json()["devices"] == 2 and dry.json()["needs_confirmation"] is False
    assert dry.json()["targets_with_no_rule"] == sorted([first, second])

    created = await editor.post(
        "/api/maintenance-windows",
        json=_body([first, second], starts_at=_in(-60), ends_at=_in(3600)),
    )
    assert created.status_code == 200, created.text
    wid, window = created.json()["id"], created.json()
    assert window["created"] is True and window["created_by_agent"] is False
    assert window["status"] == "active" and window["owner_role"] == "editor"

    card = await editor.get(f"/api/maintenance-windows/{wid}")
    assert card.status_code == 200
    assert len(card.json()["targets"]) == 2
    assert card.json()["ledger"] is None, "an editor read the ledger, which is `mw.ledger`/admin"

    longer = await editor.post(
        f"/api/maintenance-windows/{wid}/extend", json={"ends_at": _in(3600 * 8)}
    )
    assert longer.status_code == 200, longer.text
    assert longer.json()["needs_confirmation"] is True, (
        "an extend past six hours did not re-arm D6, so a window an operator believes is running "
        "would be running without anybody having agreed to it"
    )
    assert longer.json()["window_status"] == "pending_confirmation"

    assert (await editor.post(f"/api/maintenance-windows/{wid}/confirm")).status_code == 200
    ended = await editor.post(f"/api/maintenance-windows/{wid}/end")
    assert ended.status_code == 200, ended.text
    assert ended.json()["ended"] is True
    again = await editor.post(f"/api/maintenance-windows/{wid}/end")
    assert again.status_code == 409, "a window ended twice"


async def test_an_admin_does_the_two_things_an_editor_cannot(store: Store) -> None:
    """Inventory structure and the ledger. Both are admin, and for different reasons.

    An organization is **attribution** an admin writes (ADR #366); the ledger is a per-element
    record that is not a view of the network and must not become one.
    """
    _engine, _q, app = await authutil.make_env(store)
    (ne,) = await _hosts(store, "10.50.0.1")
    admin = await authutil.client_as(app, "admin")

    made = await admin.post("/api/organizations", json={"name": "Tower South", "slug": "south"})
    assert made.status_code == 200, made.text
    org = made.json()["id"]
    twice = await admin.post("/api/organizations", json={"name": "Again", "slug": "south"})
    assert twice.status_code == 409 and "south" in twice.text, twice.text

    moved = await admin.post(f"/api/entities/{ne}/organization", json={"organization_id": org})
    assert moved.status_code == 200, moved.text
    assert (
        await admin.post(f"/api/entities/{ne}/organization", json={"organization_id": 9999})
    ).status_code == 422
    assert (
        await admin.post("/api/entities/999999/organization", json={"organization_id": org})
    ).status_code == 404

    listed = {o["slug"]: o for o in (await admin.get("/api/organizations")).json()["organizations"]}
    assert listed["south"]["ne_count"] == 1, listed
    assert listed["south"]["is_default"] is False

    wid = (
        await admin.post("/api/maintenance-windows", json=_body([ne], organization_id=org))
    ).json()["id"]
    card = await admin.get(f"/api/maintenance-windows/{wid}")
    assert card.json()["organization_name"] == "Tower South"
    assert card.json()["ledger"] is not None, "an admin could not read the ledger"


async def test_an_agent_declares_work_and_a_human_agrees_to_it(store: Store) -> None:
    """The token's day, and D6's second edge (ADR #370): an agent may not agree to its own window.

    The marking is the other half. An operator reading a list of planned work is entitled to know
    which entries a human wrote, in the response **and** in the audit row, which is the record
    that cannot be edited afterwards.
    """
    _engine, _q, app = await authutil.make_env(store)
    (ne,) = await _hosts(store, "10.50.0.1")
    agent = await _client(app, store, "agent")

    created = await agent.post("/api/maintenance-windows", json=_body([ne], ends_at=_in(3600 * 9)))
    assert created.status_code == 200, created.text
    wid = created.json()["id"]
    assert created.json()["created_by_agent"] is True
    assert created.json()["status"] == "pending_confirmation"

    refused = await agent.post(f"/api/maintenance-windows/{wid}/confirm")
    assert refused.status_code == 403 and "human" in refused.text, refused.text

    retry = await agent.post(
        "/api/maintenance-windows", json=_body([ne], ends_at=_in(3600 * 9), idempotency_key="k-1")
    )
    again = await agent.post(
        "/api/maintenance-windows", json=_body([ne], ends_at=_in(3600 * 9), idempotency_key="k-1")
    )
    assert retry.json()["id"] == again.json()["id"]
    assert again.json()["created"] is False, "a retried create made a second window"

    human = await authutil.client_as(app, "editor")
    assert (await human.post(f"/api/maintenance-windows/{wid}/confirm")).status_code == 200

    async with store.lock:
        cur = await store.conn.execute(
            "SELECT action, role, details FROM audit_log WHERE object_id=? "
            "AND object_type='maintenance_window' ORDER BY id",
            (str(wid),),
        )
        rows = [dict(r) for r in await cur.fetchall()]
    assert [r["action"] for r in rows] == [
        "maintenance.window.create",
        "maintenance.window.confirm",
    ], rows
    assert '"agent":true' in rows[0]["details"], rows[0]["details"]
    assert json.loads(rows[1]["details"])["created_by_agent"] is True


# -- what the four days found ----------------------------------------------------------------------


async def test_the_total_counts_the_windows_the_caller_can_actually_see(store: Store) -> None:
    """F151. *"Showing 1 of 3"* is a count of the windows a scoped viewer may **not** see.

    The list drops a window naming nothing in the caller's scope — correctly, it is not their
    window at all — and until v0.21.1 the total beside it was an unscoped `COUNT(*)`. The
    difference between the two numbers was the estate the scope exists to withhold.
    """
    _engine, _q, app = await authutil.make_env(store)
    mine, theirs = await _hosts(store, "10.50.0.1", "10.99.0.1")
    admin = await authutil.client_as(app, "admin")
    for target in (mine, theirs, theirs):
        assert (
            await admin.post(
                "/api/maintenance-windows", json=_body([target], visibility="everyone")
            )
        ).status_code == 200
    await authutil.set_scope(store, {"roles": {"viewer": ["10.50.0.0/24"]}})

    viewer = await authutil.client_as(app, "viewer")
    body = (await viewer.get("/api/maintenance-windows")).json()
    assert len(body["windows"]) == 1, body["windows"]
    assert body["total"] == 1, (
        f"the page showed 1 window and the total said {body['total']} — the difference is the "
        "count of windows this viewer was just prevented from seeing"
    )
    assert (await admin.get("/api/maintenance-windows")).json()["total"] == 3


async def test_the_total_follows_every_filter_and_not_only_the_status(store: Store) -> None:
    """F151's other half, which has nothing to do with scope and everything to do with the screen.

    `?ne_id=` is the filter the device card uses. The count ignored it, so a device with one
    window reported *"showing 1 of 3"* to an unscoped admin — the console's own honesty claim,
    false on the one screen that makes it.
    """
    _engine, _q, app = await authutil.make_env(store)
    one, two = await _hosts(store, "10.50.0.1", "10.50.0.2")
    admin = await authutil.client_as(app, "admin")
    for target in (one, two, two):
        await admin.post("/api/maintenance-windows", json=_body([target]))

    assert (await admin.get("/api/maintenance-windows")).json()["total"] == 3
    for query, expected in (
        ({"ne_id": one}, 1),
        ({"ne_id": two}, 2),
        ({"status": "scheduled"}, 3),
        ({"status": "cancelled"}, 0),
        ({"organization_id": 1}, 3),
        ({"organization_id": 9999}, 0),
        ({"mine": True}, 3),
    ):
        body = (await admin.get("/api/maintenance-windows", params=query)).json()
        assert body["total"] == expected == len(body["windows"]), (
            f"{query} listed {len(body['windows'])} and totalled {body['total']}"
        )


async def test_an_idempotency_key_does_not_hand_back_a_window_out_of_scope(store: Store) -> None:
    """F152. The retry answer was the one window route that skipped the scope check.

    The key is unique across the appliance rather than per caller, so a scoped editor who replays
    somebody else's key was handed the name, the owner and the organization of work over elements
    `GET /{wid}` answers 404 for. The fix is the public half: the key is taken, the window exists,
    and prime directive 4 already makes both of those public.
    """
    _engine, _q, app = await authutil.make_env(store)
    mine, theirs = await _hosts(store, "10.50.0.1", "10.99.0.1")
    admin = await authutil.client_as(app, "admin")
    hidden = await admin.post(
        "/api/maintenance-windows",
        json=_body([theirs], name="core router swap", idempotency_key="shared-key"),
    )
    assert hidden.status_code == 200, hidden.text
    wid = hidden.json()["id"]
    await authutil.set_scope(store, {"roles": {"editor": ["10.50.0.0/24"]}})

    editor = await authutil.client_as(app, "editor")
    assert (await editor.get(f"/api/maintenance-windows/{wid}")).status_code == 404
    replay = await editor.post(
        "/api/maintenance-windows", json=_body([mine], idempotency_key="shared-key")
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["created"] is False and replay.json()["id"] == wid
    assert replay.json()["redacted"] is True, replay.json()
    for leaked in ("name", "owner_ref", "organization_name", "description"):
        assert leaked not in replay.json(), (
            f"the idempotency replay disclosed {leaked!r} of a window this caller cannot open"
        )


async def test_a_scope_denial_is_audited_under_the_action_that_was_attempted(
    store: Store,
) -> None:
    """F153. Every denial on this resource was filed as an attempted `maintenance.window.update`.

    The perimeter's contract is that a denial is recorded *"under the action the caller
    attempted, so a scoped principal probing the write surface leaves a trail"*. A trail that
    says `update` for a read, a confirm and an end is not that trail: it reads as a principal
    trying to change things, which is the finding an auditor would act on.
    """
    _engine, _q, app = await authutil.make_env(store)
    mine, theirs = await _hosts(store, "10.50.0.1", "10.99.0.1")
    admin = await authutil.client_as(app, "admin")
    wid = (await admin.post("/api/maintenance-windows", json=_body([theirs]))).json()["id"]
    await authutil.set_scope(store, {"roles": {"editor": ["10.50.0.0/24"]}})

    editor = await authutil.client_as(app, "editor")
    assert mine  # the element this editor may see; the window names the other one
    for method, path in (
        ("GET", f"/api/maintenance-windows/{wid}"),
        ("POST", f"/api/maintenance-windows/{wid}/confirm"),
        ("POST", f"/api/maintenance-windows/{wid}/cancel"),
        ("POST", f"/api/maintenance-windows/{wid}/end"),
        ("POST", f"/api/maintenance-windows/{wid}/extend"),
    ):
        body = {"ends_at": _in(3600 * 4)} if path.endswith("extend") else None
        assert (await editor.request(method, path, json=body)).status_code == 404

    async with store.lock:
        cur = await store.conn.execute(
            "SELECT action, outcome FROM audit_log WHERE outcome='denied' "
            "AND object_type='maintenance_window' ORDER BY id"
        )
        actions = [str(r["action"]) for r in await cur.fetchall()]
    assert actions == [
        "maintenance.window.read",
        "maintenance.window.confirm",
        "maintenance.window.cancel",
        "maintenance.window.end",
        "maintenance.window.extend",
    ], actions


async def test_a_scope_too_large_to_bind_still_counts_what_the_page_shows(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `MAX_SCOPE_PARAMS` fallback, both halves of it, driven by shrinking the bound.

    An estate with more elements in one scope than SQLite will bind cannot carry the scope in the
    WHERE clause. `window_markers` and `severity_census` answer that by filtering in Python rather
    than truncating the id list — which would answer a different question quietly — and this
    resource does the same in two places: the handler drops the rows and
    `_count_windows_over_a_huge_scope` counts over id pairs.

    Driven by patching the bound down rather than by seeding 30 001 elements, because the branch
    is what is under test and the number is not. Without this the fallback is code nobody has run.
    """
    from netcorenoc.store import mw_reads

    _engine, _q, app = await authutil.make_env(store)
    mine, theirs = await _hosts(store, "10.50.0.1", "10.99.0.1")
    admin = await authutil.client_as(app, "admin")
    for target in (mine, theirs, theirs):
        await admin.post("/api/maintenance-windows", json=_body([target], visibility="everyone"))
    # A window naming nothing: in every scope, including one too large to bind.
    await admin.post("/api/maintenance-windows", json=_body([], visibility="everyone"))
    await authutil.set_scope(store, {"roles": {"viewer": ["10.50.0.0/24"]}})
    monkeypatch.setattr(mw_reads, "MAX_SCOPE_PARAMS", 0)

    viewer = await authutil.client_as(app, "viewer")
    body = (await viewer.get("/api/maintenance-windows")).json()
    assert len(body["windows"]) == 2, body["windows"]
    assert body["total"] == 2, (
        f"the oversized-scope fallback counted {body['total']} where the page showed "
        f"{len(body['windows'])}"
    )
