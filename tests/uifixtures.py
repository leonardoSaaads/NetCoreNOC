"""Real server responses, captured per role, for the DOM harness to serve to `ui/app.js`.

The harness stubs the network. **The shapes it serves are not invented**: they are captured from
the running FastAPI app over a real corpus, through the same ASGI client the rest of the suite
uses, at the exact URLs `app.js` requests. A hand-written fixture would let the harness and the
server drift apart silently, and two of the five captured invariants are statements about the
*contract between them* — a fixture that paraphrased the server would make them worthless.

What is still a stub, said plainly: the **responses to writes**. A `POST …/feedback` is recorded
and answered `200`, never applied. This harness characterises what the client SENDS; what the
server does with it is `tests/test_feedback*.py`'s job and is unaffected by this release.
"""

from __future__ import annotations

from typing import Any

import httpx

from netcorenoc.main import Engine
from netcorenoc.store import Store

import authutil
import util

BASE = 1_700_000_000.0

#: The payload `tests/test_security_ui.py` already pushes through the ingest path, reused verbatim
#: so the two guards are about the same string.
HOSTILE = "<img src=x onerror=alert(1)><script>alert(document.cookie)</script>\"'>"

#: Captured once per session and reused. The corpus is deterministic and the capture is read-only
#: apart from the labelling pass, which is why the hostile capture is taken last.
_CACHE: dict[str, dict[str, Any]] = {}


async def all_routes(store: Store) -> dict[str, dict[str, Any]]:
    """Every role's captured route table, built once.

    Order is load-bearing: the hostile capture writes labels through the real route, so it runs
    after the clean ones. Reversing it would make every "clean" table carry the payload and the
    escaping invariant would be asserting against itself.
    """
    if _CACHE:
        return _CACHE
    _engine, app = await corpus(store)
    for role in ("viewer", "editor", "admin"):
        _CACHE[role] = await capture(app, role)
    _CACHE["editor_hostile"] = await capture(app, "editor", hostile_label=HOSTILE)
    return _CACHE


#: Every GET `app.js` issues, keyed by the capability its route needs, so a role's captured table
#: contains exactly the routes that role may call. Paths carry the query strings `app.js` builds.
CLIENT_GETS: list[tuple[str, str]] = [
    ("self.read", "/api/me"),
    ("stats.read", "/api/stats"),
    ("graph.read", "/api/graph"),
    ("situations.read", "/api/situations?limit=50"),
    ("timeline.read", "/api/timeline?limit=300"),
    # v0.20.0: the Overview's activity chart reads the **bucketed** form over the range the
    # operator picked. Two entries, because the default range is what the screen asks for on
    # mount and a fixture that carried only the marks would leave the chart in its absent state.
    ("timeline.read", "/api/timeline?buckets=24&range_s=7200"),
    ("entities.read", "/api/entities"),
    ("entities.read", "/api/state-clears"),
    ("users.manage", "/api/users"),
    ("tokens.manage", "/api/tokens"),
    ("config.read", "/api/config"),
    ("scorer.read", "/api/scorer"),
    # v0.18.0: the correlation-health line on Situations reads this on mount. Captured
    # for every other entry's reason — a screen that reads a route the fixture does not
    # carry renders its error state in the harness, which is honest and is not the
    # screen anybody is testing.
    ("correlation.read", "/api/correlation"),
    ("rbac.read", "/api/rbac"),
    ("scope.read", "/api/scope"),
    ("quarantine.read", "/api/quarantine?limit=100"),
    ("audit.read", "/api/audit?limit=200"),
    # v0.13.0: three routes that had no UI surface before this release and now have one
    # (UI-0.13-DRAFT §4). They are captured for the same reason as every other entry — a screen
    # driven against a 404 renders its error state, and a test that read that as the screen's
    # behaviour would be measuring the fixture.
    ("classes.read", "/api/classes"),
    ("promotion.read", "/api/promotion"),
    ("config.read", "/api/dataset/retention"),
    # v0.21.0: the Maintenance screen reads the upcoming list on mount, and the form reads
    # the organizations and the zones. Captured for every other entry's reason — a screen
    # driven against a 404 renders its error state, and a test that read that as the screen's
    # behaviour would be measuring the fixture.
    ("mw.read", "/api/maintenance-windows?limit=5"),
    ("organizations.read", "/api/organizations"),
    ("timezones.read", "/api/timezones?q="),
    # v0.22.0: the persisted host series and the activity reads the Overview and the Timeline
    # make on mount, at their default windows; the bell's notices; the Trap catalogue's first page,
    # its imported-row count and the root of its branch browser.
    ("stats.read", "/api/resources?range_s=7200&buckets=24"),
    ("timeline.read", "/api/activity/severity?buckets=24&range_s=7200"),
    ("timeline.read", "/api/activity/lanes?range_s=3600&buckets=48&top=8"),
    ("timeline.read", "/api/activity/groups?range_s=3600&kind=both&limit=25&offset=0"),
    ("stats.read", "/api/notices"),
    ("classes.read", "/api/catalogue?limit=25&offset=0"),
    ("classes.read", "/api/catalogue/rules?source=imported&limit=1"),
    ("classes.read", "/api/catalogue/tree?node=1.3.6.1.4.1"),
    # v0.23.0: the Overview's active-alarm chart and its Top 10, the Entities inventory, and the
    # Timeline's filter options (the inventory again, and the catalogue's names).
    ("timeline.read", "/api/activity/active?buckets=24&range_s=7200"),
    ("timeline.read", "/api/activity/top?range_s=7200&buckets=24&limit=10&bands=critical,major"),
    ("entities.read", "/api/inventory"),
    ("classes.read", "/api/catalogue?limit=200"),
]

#: Writes the harness answers without applying. The value is what the real route returns on success.
CLIENT_WRITES: dict[str, dict[str, Any]] = {
    "POST /api/labels": {"status": 200, "json": {"ok": True}},
    "POST /api/logout": {"status": 200, "json": {"ok": True}},
    "POST /api/password": {"status": 200, "json": {"status": "password changed"}},
}


async def corpus(store: Store) -> tuple[Engine, Any]:
    """An engine and app holding the fiber-cut fixture — a real, multi-alarm situation."""
    engine, queue, app = await authutil.make_env(store)
    await util.drive(engine, queue, util.fixture_events("fiber_cut.json", BASE))
    return engine, app


async def capture(app: Any, role: str, *, hostile_label: str | None = None) -> dict[str, Any]:
    """Capture the real responses `role` would receive, as a harness route table."""
    client = await authutil.client_as(app, role)
    try:
        if hostile_label is not None:
            await _label_everything(app, hostile_label)
        routes: dict[str, Any] = dict(CLIENT_WRITES)
        for _capability, path in CLIENT_GETS:
            response = await client.get(path)
            if response.status_code != 200:
                # A role that may not call it simply has no canned answer; the harness then
                # records the request and returns 404 — which is what invariant 5 measures.
                continue
            routes[path] = {"status": 200, "json": response.json()}
        await _capture_situations(client, routes)
        await _capture_elements(client, routes)
        return routes
    finally:
        await client.aclose()


async def _capture_elements(client: httpx.AsyncClient, routes: dict[str, Any]) -> None:
    """v0.22.0: the three reads the graph's element panel makes, for every node on the graph.

    Captured from the real routes for the reason every other entry is: a panel driven against a
    404 renders its error state, and a test reading that would be measuring the fixture.
    """
    graph = routes.get("/api/graph")
    if graph is None:
        return
    for node in graph["json"].get("nodes", []):
        ne = node["id"]
        for path in (
            f"/api/elements/{ne}",
            f"/api/situations?ne_id={ne}&limit=30",
            f"/api/activity/groups?range_s=86400&ne_id={ne}&limit=5",
            f"/api/elements/{ne}/components",
            f"/api/activity/active?range_s=86400&buckets=24&ne_id={ne}",
        ):
            response = await client.get(path)
            if response.status_code == 200:
                routes[path] = {"status": 200, "json": response.json()}


async def _capture_situations(client: httpx.AsyncClient, routes: dict[str, Any]) -> None:
    listing = routes.get("/api/situations?limit=50")
    if listing is None:
        return
    for situation in listing["json"]:
        sid = situation["id"]
        detail = await client.get(f"/api/situations/{sid}")
        if detail.status_code == 200:
            routes[f"/api/situations/{sid}"] = {"status": 200, "json": detail.json()}
        routes[f"POST /api/situations/{sid}/feedback"] = {"status": 200, "json": {"ok": True}}
        routes[f"POST /api/situations/{sid}/close"] = {"status": 200, "json": {"ok": True}}
        # v0.16.0: the four gestures a situation card can send. Stubbed 200s, exactly as the two
        # above are — the harness records what the console POSTS, and the server's own behaviour is
        # `tests/test_api.py`'s subject.
        for gesture in ("move", "merge", "split", "name", "promote"):
            routes[f"POST /api/situations/{sid}/{gesture}"] = {"status": 200, "json": {"ok": True}}


async def _label_everything(app: Any, label: str) -> None:
    """Push `label` through the real label route onto every network element and alarm class.

    Deliberately the real write path: the escaping invariant is worthless if the hostile string is
    injected into a fixture rather than travelling the route an operator's input actually takes.

    **Every response is checked, and that is F117** (v0.16.7). This helper posted `kind="device"`
    and threw the response away. `0016` renamed that kind to `ne` in v0.16.3 and `LabelIn.kind` is
    a `Literal["ne", "class", "severity"]`, so every one of these writes had been a **422 for four
    releases** — silently. Measured: every node and every entity came back with `label=None` while
    the alarm classes carried the payload, so invariant 4's own control (*"did the payload reach
    the DOM at all?"*) stayed green on half the coverage it claimed. A fixture that ignores a
    status code is a fixture that can stop doing its job without anything going red.
    """
    admin = await authutil.client_as(app, "admin")
    try:
        graph = (await admin.get("/api/graph")).json()
        for node in graph["nodes"]:
            posted = await admin.post(
                "/api/labels", json={"kind": "ne", "id": node["id"], "label": label}
            )
            assert posted.status_code == 200, (
                f"the hostile label did not reach NE {node['id']}: "
                f"{posted.status_code} {posted.text[:200]}"
            )
        for cls in (await admin.get("/api/classes")).json():
            posted = await admin.post(
                "/api/labels", json={"kind": "class", "id": cls["id"], "label": label}
            )
            assert posted.status_code == 200, (
                f"the hostile label did not reach class {cls['id']}: "
                f"{posted.status_code} {posted.text[:200]}"
            )
    finally:
        await admin.aclose()


def largest_situation(routes: dict[str, Any]) -> tuple[int, int]:
    """The (sid, member count) of the biggest captured situation — the one worth splitting."""
    listing = routes["/api/situations?limit=50"]["json"]
    best = max(listing, key=lambda s: s["alarm_count"])
    return best["id"], best["alarm_count"]


def member_ids(routes: dict[str, Any], sid: int) -> list[int]:
    """The alarm ids in the order the detail render puts them on screen."""
    return [a["id"] for a in routes[f"/api/situations/{sid}"]["json"]["alarms"]]
