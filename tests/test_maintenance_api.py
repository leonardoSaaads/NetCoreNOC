"""`/api/maintenance-windows` — **the contract an agent will hold this appliance to** (Part III).

Every §10 injection that lives at the API boundary is here, each with the control that passes
beside it: the naive datetime, the existence oracle, the unconfirmed window, the agent-created
window taking effect, the viewer who must still see a marker, the visibility enforced in the render
rather than the query, and the city label stored in place of its zone.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from netcorenoc.api import models, models_maintenance
from netcorenoc.crosscutting import rbac
from netcorenoc.store import Store
from netcorenoc.store.maintenance_windows import CONFIRMATION_THRESHOLD_S

import authutil

BRASILIA = ZoneInfo("America/Sao_Paulo")
DAY = datetime(2026, 5, 14, tzinfo=BRASILIA)


def _at(epoch: float) -> str:
    """An epoch instant as RFC 3339 **in Brasília with its offset** — the only form this API takes.

    Used by the tests that must be in force at the moment the process is running, because a route
    asking `window_markers` about "now" reads the real clock and cannot be told a fixture's.
    """
    return datetime.fromtimestamp(epoch, BRASILIA).isoformat()


def _iso(hour: int, minute: int = 0) -> str:
    """A Brasília wall clock as RFC 3339 **with its offset**, the only form this API accepts."""
    return (DAY + timedelta(hours=hour, minutes=minute)).isoformat()


async def _seed_hosts(store: Store, *addresses: str) -> list[int]:
    ids = []
    async with store.lock:
        for address in addresses:
            ids.append(await store.ne_id(address, DAY.timestamp()))
        await store.commit()
    return ids


def _body(targets: list[int], **over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "span splice",
        "tz": "America/Sao_Paulo",
        "starts_at": _iso(10),
        "ends_at": _iso(12),
        "targets": targets,
        "rules": [],
    }
    body.update(over)
    return body


# -- the shape of the surface ------------------------------------------------------------------


def test_the_window_models_are_re_exported_by_identity_not_by_copy() -> None:
    """ADR #374. `models.py` stays the one list a reviewer reads, and there is one definition.

    The same guard `crosscutting/rbac/__init__.py` carries, for the same reason: a second
    definition of a request bound is a second place to change it, and the one that is forgotten is
    invisible until it is exploited.
    """
    for name in (
        "MaintenanceWindowIn",
        "MaintenanceWindowUpdateIn",
        "MaintenanceWindowPreviewIn",
        "CollectionRuleIn",
        "WindowExtendIn",
        "OrganizationIn",
    ):
        assert getattr(models, name) is getattr(models_maintenance, name), (
            f"{name} is a COPY in models.py rather than the one definition"
        )


def test_every_window_route_declares_a_capability_and_a_scope_posture() -> None:
    """The registration gate's own tables, checked for this resource by name.

    `create_app` already refuses an undeclared route at startup. This says *which* declarations
    this release is claiming, so a route quietly dropped from either table fails here by name
    rather than by a count somewhere else.
    """
    routes = [r for r in rbac.ROUTE_PERMISSIONS if "maintenance-windows" in r[1]]
    assert len(routes) == 9, f"expected nine window routes, found {sorted(routes)}"
    for route in routes:
        assert route in rbac.ROUTE_SCOPE, f"{route} declares no scope posture"
        assert rbac.ROUTE_SCOPE[route] == "scoped", (
            f"{route} is not scoped; a window names network elements"
        )
    assert rbac.ROUTE_PERMISSIONS[("GET", "/api/maintenance-windows")] == "mw.read"
    assert rbac.PERMISSIONS["mw.read"] == "viewer", (
        "prime directive 4: every role must be able to learn that a window exists"
    )
    assert rbac.PERMISSIONS["mw.confirm"] == "editor"
    assert {"mw.confirm", "mw.write"} <= set(rbac.PERMISSIONS), (
        "D6's approval must be a capability of its own, separable from the power to declare: "
        "`resolve_capabilities` is `ceiling n policy`, so two capabilities are what let a "
        "deployment grant scheduling without self-approval (ADR #370)"
    )


# -- RFC 3339, and the naive datetime that is refused -------------------------------------------


async def test_a_naive_datetime_is_refused_and_the_error_says_why(store: Store) -> None:
    """§10's injection. *"10:00"* means nothing without a place.

    An appliance that assumed UTC would accept this from an operator in Brasília and schedule the
    window for 07:00 their time — silently, and the work would happen outside it.
    """
    _engine, _q, app = await authutil.make_env(store)
    (ne,) = await _seed_hosts(store, "10.50.0.1")
    client = await authutil.client_as(app, "editor")
    naive = await client.post(
        "/api/maintenance-windows",
        json=_body([ne], starts_at="2026-05-14T10:00:00", ends_at="2026-05-14T12:00:00"),
    )
    assert naive.status_code == 422, naive.text
    assert "offset" in naive.text, f"the refusal does not say what was wrong: {naive.text}"

    # The control: the same instants WITH an offset are accepted.
    ok = await client.post("/api/maintenance-windows", json=_body([ne]))
    assert ok.status_code == 200, ok.text


async def test_a_city_label_is_refused_and_its_canonical_zone_is_named(store: Store) -> None:
    """§10's injection: a city label stored instead of its canonical zone.

    Three of the cities an operator actually names have **no IANA zone of their own**, and this is
    the boundary that says so — with the right answer in the message, because an agent corrects
    itself from the error it gets back.
    """
    _engine, _q, app = await authutil.make_env(store)
    (ne,) = await _seed_hosts(store, "10.50.0.1")
    client = await authutil.client_as(app, "editor")
    for bogus in ("America/Brasilia", "Asia/Beijing", "America/Washington", "Brasília"):
        reply = await client.post("/api/maintenance-windows", json=_body([ne], tz=bogus))
        assert reply.status_code == 422, f"{bogus} was accepted: {reply.text}"
    # The control, and the correction the message gives.
    ok = await client.post("/api/maintenance-windows", json=_body([ne]))
    assert ok.status_code == 200
    assert ok.json()["tz"] == "America/Sao_Paulo"


async def test_the_timezone_resource_resolves_a_city_to_its_zone(store: Store) -> None:
    """D2's whole point, from the client's side: search a city, store a zone."""
    _engine, _q, app = await authutil.make_env(store)
    client = await authutil.client_as(app, "viewer")
    reply = await client.get("/api/timezones", params={"q": "Bras"})
    assert reply.status_code == 200
    zones = reply.json()["zones"]
    assert zones and zones[0]["label"] == "Brasília"
    assert zones[0]["zone"] == "America/Sao_Paulo"
    assert zones[0]["offset"].startswith("UTC-"), zones[0]
    for city, zone in (("Beijing", "Asia/Shanghai"), ("Washington", "America/New_York")):
        hit = (await client.get("/api/timezones", params={"q": city})).json()["zones"][0]
        assert hit["zone"] == zone, f"{city} resolved to {hit['zone']}"


# -- D6 -----------------------------------------------------------------------------------------


async def test_a_window_over_six_hours_waits_for_a_human(store: Store) -> None:
    """D6, and the injection *"a MW over 6 h created by an agent taking effect unconfirmed"*."""
    _engine, _q, app = await authutil.make_env(store)
    (ne,) = await _seed_hosts(store, "10.50.0.1")
    client = await authutil.client_as(app, "editor")
    short = await client.post("/api/maintenance-windows", json=_body([ne]))
    assert short.json()["status"] != "pending_confirmation", (
        "a two-hour window asked for a confirmation D6 does not require"
    )

    long = await client.post("/api/maintenance-windows", json=_body([ne], ends_at=_iso(22)))
    assert long.status_code == 200, long.text
    assert long.json()["status"] == "pending_confirmation", long.json()
    assert long.json()["needs_confirmation"] is True
    assert CONFIRMATION_THRESHOLD_S < (22 - 10) * 3600, (
        "the fixture no longer exceeds D6's threshold, so this test proves nothing"
    )


async def test_an_agent_may_create_a_long_window_and_may_not_confirm_it(store: Store) -> None:
    """II.7's first edge, settled (ADR #370), and the marking Part III requires."""
    engine, _q, app = await authutil.make_env(store)
    (ne,) = await _seed_hosts(store, "10.50.0.1")
    token = await authutil.make_token(store, "agent", "editor")

    agent = authutil.new_client(app)
    agent.headers["Authorization"] = f"Bearer {token}"
    created = await agent.post("/api/maintenance-windows", json=_body([ne], ends_at=_iso(22)))
    assert created.status_code == 200, created.text
    wid = created.json()["id"]
    assert created.json()["created_by_agent"] is True, (
        "an agent-created window is not marked, so an operator reading the list cannot tell "
        "which entries a human wrote"
    )
    assert created.json()["status"] == "pending_confirmation"

    refused = await agent.post(f"/api/maintenance-windows/{wid}/confirm")
    assert refused.status_code == 403, refused.text
    assert "human" in refused.text

    # The control: a human editor may.
    human = await authutil.client_as(app, "editor")
    ok = await human.post(f"/api/maintenance-windows/{wid}/confirm")
    assert ok.status_code == 200, ok.text
    window = await store.maintenance_window(wid)
    assert window is not None and window["status"] == "scheduled"
    assert engine is not None


async def test_an_unconfirmed_window_is_not_in_the_structure_the_ingest_path_reads(
    store: Store,
) -> None:
    """§10's injection: an unconfirmed MW suppressing anything after its start.

    Asserted at the structure rather than at a trap, because *"it suppresses nothing"* is
    guaranteed by the window never reaching the index at all — which is cheaper than remembering
    to exclude it on every packet, and is the property worth pinning.
    """
    engine, _q, app = await authutil.make_env(store)
    (ne,) = await _seed_hosts(store, "10.50.0.1")
    client = await authutil.client_as(app, "editor")
    created = await client.post("/api/maintenance-windows", json=_body([ne], ends_at=_iso(22)))
    wid = created.json()["id"]
    # A sweep BEFORE the window's start: it is still pending, and it is not in the index.
    async with store.lock:
        await engine._maintenance_windows(DAY.timestamp() + 9 * 3600)
    assert ne not in engine.windows.by_ne, "an unconfirmed window reached the per-trap check"

    # A sweep AFTER its start, still unconfirmed: D6's safe failure. It does not become active —
    # it **expires**, visibly, and alarms keep flowing.
    async with store.lock:
        await engine._maintenance_windows(DAY.timestamp() + 11 * 3600)
    assert ne not in engine.windows.by_ne
    expired = await store.maintenance_window(wid)
    assert expired is not None and expired["status"] == "expired"

    # The control: a window confirmed BEFORE its start does reach the check.
    client = await authutil.client_as(app, "editor")
    second = await client.post("/api/maintenance-windows", json=_body([ne], ends_at=_iso(22)))
    sid = second.json()["id"]
    confirmed = await client.post(f"/api/maintenance-windows/{sid}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    async with store.lock:
        await engine._maintenance_windows(DAY.timestamp() + 11 * 3600)
    assert ne in engine.windows.by_ne, "a confirmed window did not reach the per-trap check"


# -- idempotency ---------------------------------------------------------------------------------


async def test_a_retry_with_the_same_key_returns_the_same_window(store: Store) -> None:
    """Part III. **An agent that times out and retries must not create two windows.**"""
    _engine, _q, app = await authutil.make_env(store)
    (ne,) = await _seed_hosts(store, "10.50.0.1")
    client = await authutil.client_as(app, "editor")
    body = _body([ne], idempotency_key="tower-south-2026-05-14")
    first = await client.post("/api/maintenance-windows", json=body)
    second = await client.post("/api/maintenance-windows", json=body)
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"], "the retry created a second window"
    assert first.json()["created"] is True and second.json()["created"] is False
    # `status` means the WINDOW's state on every call, never the envelope's. A `"status":
    # "exists"` envelope collided with the resource field and the resource field won the merge
    # silently, which is the kind of contract bug an agent finds in production.
    assert second.json()["status"] == first.json()["status"]
    listing = await client.get("/api/maintenance-windows")
    assert listing.json()["total"] == 1

    # The control: a different key is a different window.
    other = await client.post("/api/maintenance-windows", json=_body([ne], idempotency_key="other"))
    assert other.json()["id"] != first.json()["id"]


# -- the security rules ---------------------------------------------------------------------------


async def test_a_window_naming_an_out_of_scope_asset_reveals_nothing(store: Store) -> None:
    """§10's injection: a MW naming an out-of-scope asset revealing whether it exists.

    The two calls below must be **indistinguishable**: one names an element that exists and is
    hidden, the other names an id that does not exist at all. Same status, same counts, same body
    shape.
    """
    _engine, _q, app = await authutil.make_env(store)
    visible, hidden = await _seed_hosts(store, "10.50.0.1", "10.99.0.1")
    await authutil.set_scope(store, {"roles": {"editor": ["10.50.0.0/24"]}})

    client = await authutil.client_as(app, "editor")
    real_but_hidden = await client.post(
        "/api/maintenance-windows/preview", json=_body([visible, hidden])
    )
    nonexistent = await client.post(
        "/api/maintenance-windows/preview", json=_body([visible, 999_999])
    )
    assert real_but_hidden.status_code == nonexistent.status_code == 200
    assert real_but_hidden.json()["devices"] == nonexistent.json()["devices"] == 1, (
        "the preview counted an element the caller may not see"
    )
    # Everything except the field derived from `time.time()`, which differs by microseconds
    # between two calls and carries no information about the estate.
    left = {k: v for k, v in real_but_hidden.json().items() if k != "starts_in_s"}
    right = {k: v for k, v in nonexistent.json().items() if k != "starts_in_s"}
    assert left == right, (
        "a hidden element and a nonexistent one produced different answers — that is the "
        "existence oracle this rule exists to close"
    )

    created = await client.post("/api/maintenance-windows", json=_body([visible, hidden]))
    assert created.status_code == 200
    assert created.json()["target_count"] == 1, (
        "the window was created over an element the caller cannot see"
    )


async def test_a_viewer_sees_that_a_window_exists_and_not_what_it_is(store: Store) -> None:
    """**Prime directive 4 and the injection *"a viewer seeing no marker"*, in one test.**

    The existence is public because a host that goes quiet with no marker reads as healthy. The
    details follow `visibility`. Both halves are asserted, because either one alone is the defect.
    """
    _engine, _q, app = await authutil.make_env(store)
    (ne,) = await _seed_hosts(store, "10.50.0.1")
    editor = await authutil.client_as(app, "editor")
    created = await editor.post(
        "/api/maintenance-windows",
        json=_body([ne], name="rack move, south tower", visibility="editors"),
    )
    wid = created.json()["id"]

    viewer = await authutil.client_as(app, "viewer")
    listing = await viewer.get("/api/maintenance-windows")
    assert listing.status_code == 200, listing.text
    rows = listing.json()["windows"]
    assert len(rows) == 1, "a viewer cannot see that planned work exists at all"
    row = rows[0]
    assert row["redacted"] is True
    assert row["id"] == wid and row["status"] and row["ends_at"]
    assert "name" not in row, "an editors-only window leaked its name to a viewer"
    assert "owner_ref" not in row and "description" not in row

    detail = await viewer.get(f"/api/maintenance-windows/{wid}")
    assert detail.status_code == 404, "a viewer read an editors-only window in full"

    # The control: `everyone` visibility shows the viewer the name.
    editor = await authutil.client_as(app, "editor")
    await editor.post(
        f"/api/maintenance-windows/{wid}",
        json=_body([ne], name="rack move, south tower", visibility="everyone"),
    )
    viewer = await authutil.client_as(app, "viewer")
    row = (await viewer.get("/api/maintenance-windows")).json()["windows"][0]
    assert row["redacted"] is False and row["name"] == "rack move, south tower"


async def test_the_visibility_filter_is_in_the_query_and_not_in_the_render(store: Store) -> None:
    """§10's injection: visibility enforced in the render rather than the query.

    Asserted structurally, because a render-side filter passes every behavioural test until the
    day somebody adds a field. `window_markers` — what every role reads — is a **different
    statement selecting different columns**, and it names none of the fields `visibility` guards.
    """
    import inspect

    from netcorenoc.store import mw_reads

    source = inspect.getsource(mw_reads.MaintenanceReadMixin.window_markers)
    statement = source[source.index("SELECT") : source.index("ORDER BY")]
    for guarded in ("w.name", "w.description", "w.owner_ref", "w.visibility"):
        assert guarded not in statement, (
            f"the marker query selects {guarded}, so the redaction is happening after the read "
            "rather than in it (F35's shape, one resource over)"
        )
    assert "w.status" in statement and "w.ends_at" in statement, (
        "the marker query stopped returning what every role is entitled to see"
    )


async def test_the_preview_is_the_same_code_the_form_uses(store: Store) -> None:
    """Part III: *"the same code the form uses for its live preview"*, asserted as one answer."""
    _engine, _q, app = await authutil.make_env(store)
    (ne,) = await _seed_hosts(store, "10.50.0.1")
    client = await authutil.client_as(app, "editor")
    preview = await client.post("/api/maintenance-windows/preview", json=_body([ne]))
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["devices"] == 1
    assert body["needs_confirmation"] is False
    assert body["targets_with_no_rule"] == [ne], (
        "the preview does not say which targets would collect nothing, which is the one "
        "thing an operator needs to notice before they commit"
    )
    assert body["site_time"].startswith("2026-05-14T10:00:00")
    assert body["site_offset"] == "UTC-03:00"
    # It writes nothing.
    assert (await client.get("/api/maintenance-windows")).json()["total"] == 0


async def test_every_write_is_audited_with_the_actor_and_whether_it_was_an_agent(
    store: Store,
) -> None:
    """Part III's audit requirement, including the human/token distinction."""
    _engine, _q, app = await authutil.make_env(store)
    (ne,) = await _seed_hosts(store, "10.50.0.1")
    client = await authutil.client_as(app, "editor")
    wid = (await client.post("/api/maintenance-windows", json=_body([ne]))).json()["id"]
    upd = await client.post(f"/api/maintenance-windows/{wid}", json=_body([ne], name="renamed"))
    assert upd.status_code == 200, upd.text
    await client.post(f"/api/maintenance-windows/{wid}/cancel")

    async with store.lock:
        cur = await store.conn.execute(
            "SELECT action, actor, role, details FROM audit_log "
            "WHERE object_type='maintenance_window' ORDER BY id"
        )
        rows = [dict(r) for r in await cur.fetchall()]
    actions = [r["action"] for r in rows]
    assert actions == [
        "maintenance.window.create",
        "maintenance.window.update",
        "maintenance.window.cancel",
    ], actions
    assert all(r["role"] == "editor" for r in rows)
    # The audit writer stores compact JSON, so the needle carries no space.
    assert '"agent":false' in rows[0]["details"], rows[0]["details"]
    assert all(r["actor"] == "edt" for r in rows), "the audit rows do not name the actor"


async def test_an_unknown_status_filter_is_refused_by_name(store: Store) -> None:
    """An agent filtering on a status this appliance does not have has misunderstood the resource,
    and an empty list would look like *"there are none"*."""
    _engine, _q, app = await authutil.make_env(store)
    client = await authutil.client_as(app, "viewer")
    bad = await client.get("/api/maintenance-windows", params={"status": "paused"})
    assert bad.status_code == 422
    assert "paused" in bad.text and "pending_confirmation" in bad.text
    good = await client.get("/api/maintenance-windows", params={"status": "scheduled,active"})
    assert good.status_code == 200


async def test_the_openapi_schema_carries_the_enums_and_the_instant_format(store: Store) -> None:
    """Part III: *"an MCP tool generated from a vague schema is a vague tool."*"""
    _engine, _q, app = await authutil.make_env(store)
    client = authutil.new_client(app)
    schema = (await client.get("/openapi.json")).json()
    components = schema["components"]["schemas"]
    window = components["MaintenanceWindowIn"]["properties"]
    assert window["visibility"]["default"] == "editors"
    rule = components["CollectionRuleIn"]["properties"]
    kinds = rule["kind"]["enum"]
    assert sorted(kinds) == ["oid", "severity", "slot"], kinds
    assert window["starts_at"]["format"] == "date-time", (
        "an instant is documented as a bare number, so a generated tool cannot know it is a time"
    )


async def test_a_rule_that_carries_another_kinds_fields_is_refused(store: Store) -> None:
    """An agent that sent `{"kind": "oid", "severity_rank": 0}` meant something, and silently
    dropping half of it would give it a window that does not do what it asked for."""
    _engine, _q, app = await authutil.make_env(store)
    (ne,) = await _seed_hosts(store, "10.50.0.1")
    client = await authutil.client_as(app, "editor")
    mixed = await client.post(
        "/api/maintenance-windows",
        json=_body(
            [ne],
            rules=[
                {
                    "ne_id": ne,
                    "kind": "oid",
                    "oid_root": "1.3.6.1.4.1.2011",
                    "severity_rank": 0,
                }
            ],
        ),
    )
    assert mixed.status_code == 422, mixed.text
    assert "severity_rank" in mixed.text

    untargeted = await client.post(
        "/api/maintenance-windows",
        json=_body([ne], rules=[{"ne_id": 99999, "kind": "severity", "severity_rank": 0}]),
    )
    assert untargeted.status_code == 422, untargeted.text
    assert "never fire" in untargeted.text

    ok = await client.post(
        "/api/maintenance-windows",
        json=_body(
            [ne],
            rules=[
                {
                    "ne_id": ne,
                    "kind": "oid",
                    "oid_root": "1.3.6.1.4.1.2011",
                    "match_on": "varbind",
                }
            ],
        ),
    )
    assert ok.status_code == 200, ok.text


async def test_cancel_is_a_state_and_not_a_delete(store: Store) -> None:
    """Part III. The record of what was planned, and the audit row, both survive."""
    _engine, _q, app = await authutil.make_env(store)
    (ne,) = await _seed_hosts(store, "10.50.0.1")
    client = await authutil.client_as(app, "editor")
    wid = (await client.post("/api/maintenance-windows", json=_body([ne]))).json()["id"]
    assert (await client.post(f"/api/maintenance-windows/{wid}/cancel")).status_code == 200
    again = await client.post(f"/api/maintenance-windows/{wid}/cancel")
    assert again.status_code == 409, "cancelling twice was accepted"
    detail = await client.get(f"/api/maintenance-windows/{wid}")
    assert detail.status_code == 200, "a cancelled window was deleted rather than marked"
    assert detail.json()["status"] == "cancelled"
    assert detail.json()["name"] == "span splice"


async def test_the_organizations_resource_says_it_is_not_isolation(store: Store) -> None:
    """II.4. **The UI and the documentation say exactly that**, and so does the API.

    Said in the response rather than only in prose, because this is the field an agent would
    otherwise be entitled to read as a security boundary.
    """
    _engine, _q, app = await authutil.make_env(store)
    await _seed_hosts(store, "10.50.0.1")
    client = await authutil.client_as(app, "viewer")
    reply = await client.get("/api/organizations")
    assert reply.status_code == 200
    body = reply.json()
    assert body["isolation"] is False
    assert "NOT tenant isolation" in body["note"]
    organizations = body["organizations"]
    assert len(organizations) == 1 and organizations[0]["is_default"] is True
    assert organizations[0]["ne_count"] == 1, "the migration left an element attributed to nothing"


# -- IV.3: the marker reaches the screens an operator actually reads ---------------------------


async def test_a_viewer_sees_the_marker_on_a_device_under_planned_work(store: Store) -> None:
    """**IV.3, end to end.** *"Markers on every device and situation, visible to every role."*

    Until this test existed, `MaintenanceMark` was a component the console imported nowhere and
    `window_markers` was a query no route called — the whole feature was a well-documented
    component drawing nothing. That is exactly the shape of defect v0.16.3 recorded about
    `ne.label`: a field the console rendered and the API never served, permanent and invisible.

    Both halves are asserted, because either alone is the defect: an **editors-only** window must
    still put a marker on the device a viewer is looking at, and the marker must carry no name.
    """
    engine, _q, app = await authutil.make_env(store)
    (ne,) = await _seed_hosts(store, "10.60.0.1")
    editor = await authutil.client_as(app, "editor")
    # **Around the wall clock this process is actually running at**, not the fixture's May 2026.
    # `/api/entities` asks `window_markers` about `time.time()`, because a marker is a statement
    # about right now and a route that took the instant from a caller would let one be asked for.
    now = time.time()
    created = await editor.post(
        "/api/maintenance-windows",
        json=_body(
            [ne],
            name="rack move, south tower",
            visibility="editors",
            starts_at=_at(now - 600),
            ends_at=_at(now + 3600),
        ),
    )
    wid = created.json()["id"]
    assert created.status_code == 200, created.text
    async with store.lock:
        await engine._maintenance_windows(now)
        await store.commit()

    viewer = await authutil.client_as(app, "viewer")
    rows = (await viewer.get("/api/entities")).json()
    marked = [r for r in rows if int(r["id"]) == ne]
    assert marked, "the element vanished from /api/entities"
    marker = marked[0]["maintenance"]
    assert marker is not None, (
        "a viewer sees no marker on a device under planned work — the host reads as healthy, "
        "which is the failure prime directive 4 names"
    )
    assert marker["window_id"] == wid and marker["status"] == "active"
    assert "name" not in marker and "owner_ref" not in marker, (
        f"the marker leaked an editors-only window's details: {marker}"
    )

    detail = (await viewer.get(f"/api/entities/{ne}")).json()
    assert detail["maintenance"] is not None, "the detail view drops the marker the list carries"


async def test_the_marker_is_absent_when_no_window_is_in_force(store: Store) -> None:
    """The control. Without it, a route that hardcoded a marker would pass the test above."""
    _engine, _q, app = await authutil.make_env(store)
    (ne,) = await _seed_hosts(store, "10.60.0.2")
    viewer = await authutil.client_as(app, "viewer")
    rows = (await viewer.get("/api/entities")).json()
    assert next(r for r in rows if int(r["id"]) == ne)["maintenance"] is None, (
        "an element with no window on it is marked anyway, so the marker says nothing"
    )


# -- D1: an organization nothing can be put into is not an organization ------------------------


async def test_an_admin_can_move_an_element_to_another_organization(store: Store) -> None:
    """Without this route D1 is a column whose only reachable value is the seeded default.

    Three assertions, and the second and third are the ones worth having: an unknown organization
    is refused by **name and rule** so an agent can correct itself, and an unknown element answers
    exactly as `/api/entities/{ne_id}/reset` does, so neither route is an existence oracle.
    """
    _engine, _q, app = await authutil.make_env(store)
    (ne,) = await _seed_hosts(store, "10.61.0.1")
    admin = await authutil.client_as(app, "admin")
    made = await admin.post("/api/organizations", json={"name": "Norte Fibra", "slug": "norte"})
    assert made.status_code == 200, made.text
    org = made.json()["id"]

    moved = await admin.post(f"/api/entities/{ne}/organization", json={"organization_id": org})
    assert moved.status_code == 200, moved.text
    listing = {
        int(o["id"]): o for o in (await admin.get("/api/organizations")).json()["organizations"]
    }
    assert listing[org]["ne_count"] == 1, f"the element did not move: {listing}"

    bad = await admin.post(f"/api/entities/{ne}/organization", json={"organization_id": 9999})
    assert bad.status_code == 422 and "9999" in bad.text and "/api/organizations" in bad.text, (
        f"the refusal does not name the field's value and where to look: {bad.text}"
    )
    missing = await admin.post("/api/entities/99999/organization", json={"organization_id": org})
    assert missing.status_code == 404 and missing.json()["detail"] == "no such NE"


async def test_an_editor_may_not_move_an_element_between_organizations(store: Store) -> None:
    """`organizations.write` is `admin`: inventory structure, not operation."""
    _engine, _q, app = await authutil.make_env(store)
    (ne,) = await _seed_hosts(store, "10.61.0.2")
    editor = await authutil.client_as(app, "editor")
    refused = await editor.post(f"/api/entities/{ne}/organization", json={"organization_id": 1})
    assert refused.status_code == 403, refused.text
    assert rbac.PERMISSIONS["organizations.write"] == "admin"


def test_a_situation_is_marked_when_any_member_element_is_under_a_window() -> None:
    """The situation half of IV.3, over the projection the two situation routes share.

    **Any member, not every member.** A situation whose alarms come from three elements, one of
    which is suppressed, has incomplete counts — and telling the operator only when *all three*
    are suppressed would withhold the badge in exactly the mixed case where it matters.

    **Earliest-ending wins**, because the question the badge answers is *"when does this stop
    being incomplete?"* and the first answer an operator can act on is the soonest.
    """
    from netcorenoc.api.mw_shape import situation_marker

    markers = {
        7: {"window_id": 70, "status": "active", "ends_at_effective": 2_000.0},
        9: {"window_id": 90, "status": "active", "ends_at_effective": 1_000.0},
    }
    soonest = situation_marker({"id": 1}, {1: [7, 9, None]}, markers)
    assert soonest is not None and soonest["window_id"] == 90, (
        "the later window won, so an operator is told the wrong time"
    )
    assert situation_marker({"id": 2}, {2: [5, None]}, markers) is None, (
        "a situation with no member under a window is marked anyway"
    )
    # The free path: no window anywhere is one dict test and no member lookup at all.
    assert situation_marker({"id": 3}, {}, {}) is None
