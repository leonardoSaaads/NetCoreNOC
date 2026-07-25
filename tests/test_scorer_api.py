"""The v0.6.0 scorer HTTP surface: the privilege boundary (F21) and preview (F22).

`/api/scorer` is viewer+ (the parameters *explain* grouping and are not a secret);
`/api/scorer/preview`, `/api/scorer` (POST) and `/api/scorer/rollback` are **admin only, with no
editor delegation** — retuning the link formula is a system-wide logic change.
"""

from __future__ import annotations

import httpx
import pytest

from netcorenoc import preview, rbac, scoring
from netcorenoc.api import create_app
from netcorenoc.main import Engine
from netcorenoc.store import Store

import authutil
import util

BASE = 1_700_000_000.0
GOOD = {"w_t": 0.4, "w_a": 0.3, "w_e": 0.3, "tau_s": 45.0, "threshold": 0.55, "note": "wider"}


async def _env(
    store: Store, fixture: str = "fiber_cut.json", **kwargs: object
) -> tuple[Engine, object]:
    """An engine with a real, grouped scenario behind it, plus its app."""
    engine = Engine(store, __import__("asyncio").Queue())
    await engine.start()
    await authutil.make_users(store)
    await util.drive(engine, engine.queue, util.fixture_events(fixture, BASE))
    app = create_app(
        engine,
        rate_capacity=100000.0,
        preview_rate_capacity=kwargs.pop("preview_rate_capacity", 100000.0),  # type: ignore[arg-type]
        **kwargs,  # type: ignore[arg-type]
    )
    return engine, app


# Tables a *successful authenticated request* touches no matter what it does, so their movement
# proves nothing about preview: `audit_log` (preview is required to record itself — asserted
# separately, exactly one row), `session` (identity resolution slides last_seen_at/idle_expiry on
# every authenticated call), and `sqlite_sequence` (a counter for those two). Every other table —
# link, situation, situation_alarm, alarm, edge, meta, scorer_config, scorer_active,
# varbind_profile, state_clear — is compared row-for-row, so a single write anywhere that matters
# fails this test.
_NOISY = ("audit_log", "session", "sqlite_sequence")


async def _snapshot(store: Store, *, skip: tuple[str, ...] = _NOISY) -> dict[str, object]:
    """Every row of every table except the named ones — the "did anything change?" oracle."""
    cur = await store.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = [str(r[0]) for r in await cur.fetchall() if str(r[0]) not in skip]
    out: dict[str, object] = {}
    for table in tables:
        cur = await store.conn.execute(f"SELECT * FROM {table}")  # nosec B608 - name from schema
        out[table] = [tuple(row) for row in await cur.fetchall()]
    return out


# -- the RBAC map itself -------------------------------------------------------------------


def test_f21_scorer_capabilities_are_admin_only_with_no_delegation() -> None:
    """F21: deliberately stricter than the v0.5.0 draft's "optionally editor, if the admin
    delegates". There is no delegation mechanism in v0.6.0, and these two are not delegable."""
    assert rbac.PERMISSIONS["scorer.preview"] == "admin"
    assert rbac.PERMISSIONS["scorer.write"] == "admin"
    assert not rbac.role_allows("editor", "scorer.preview")
    assert not rbac.role_allows("editor", "scorer.write")
    assert not rbac.role_allows("viewer", "scorer.write")
    # Reading is allowed to everyone: the parameters explain grouping; they are not a secret.
    assert rbac.PERMISSIONS["scorer.read"] == "viewer"
    assert rbac.role_allows("viewer", "scorer.read")
    # A denied write/preview is worth an audit row; a denied *read* only means unauthenticated.
    assert {"scorer.preview", "scorer.write"} <= rbac.AUDITED_DENIED_PERMISSIONS
    assert "scorer.read" not in rbac.AUDITED_DENIED_PERMISSIONS


def test_f21_every_scorer_route_is_mapped() -> None:
    for route in (
        ("GET", "/api/scorer"),
        ("POST", "/api/scorer"),
        ("POST", "/api/scorer/preview"),
        ("POST", "/api/scorer/rollback"),
    ):
        assert route in rbac.ROUTE_PERMISSIONS, route


# -- the read path (viewer+) ----------------------------------------------------------------


@pytest.mark.parametrize("role", ["viewer", "editor", "admin"])
async def test_scorer_read_is_allowed_to_every_role(store: Store, role: str) -> None:
    _engine, app = await _env(store)
    client = await authutil.client_as(app, role)
    try:
        body = (await client.get("/api/scorer")).json()
    finally:
        await client.aclose()
    assert body["scorer_id"] == "additive"
    assert body["contract_version"] == "1.0"
    assert body["params"] == scoring.AdditiveScorer().params()
    assert body["config_id"] == 1
    assert body["degraded"] is False
    assert body["bounds"]["min_tau_s"] == scoring.MIN_TAU_S
    assert body["preview_limits"]["max_alarms"] == preview.MAX_PREVIEW_ALARMS
    assert [row["id"] for row in body["history"]] == [1]
    assert body["history"][0]["active"] is True


async def test_scorer_read_requires_a_credential(store: Store) -> None:
    _engine, app = await _env(store)
    async with authutil.new_client(app) as anon:
        assert (await anon.get("/api/scorer")).status_code == 401


# -- F21: the privilege boundary ---------------------------------------------------------------


@pytest.mark.parametrize("role", ["viewer", "editor"])
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/api/scorer/preview", GOOD),
        ("POST", "/api/scorer", GOOD),
        ("POST", "/api/scorer/rollback", {"config_id": 1}),
    ],
)
async def test_f21_non_admin_cannot_preview_write_or_roll_back(
    store: Store, role: str, method: str, path: str, body: dict[str, object]
) -> None:
    _engine, app = await _env(store)
    client = await authutil.client_as(app, role)
    try:
        assert (await client.request(method, path, json=body)).status_code == 403
    finally:
        await client.aclose()


async def test_f21_denied_attempts_are_audited(store: Store) -> None:
    """A refused attempt to change system-wide correlation logic is still worth a record."""
    _engine, app = await _env(store)
    editor = await authutil.client_as(app, "editor")
    try:
        await editor.post("/api/scorer", json=GOOD)
        await editor.post("/api/scorer/preview", json=GOOD)
    finally:
        await editor.aclose()
    async with store.lock:
        rows = [(r["action"], r["outcome"], r["role"]) for r in await store.audit_all()]
    assert ("scorer.config.update", "denied", "editor") in rows
    assert ("scorer.preview", "denied", "editor") in rows


async def test_f21_config_change_grants_no_capability(store: Store) -> None:
    """A scoring configuration is not an authorization input: applying one changes no role and
    escalates no live session. Belt and braces against the class of bug where a stored 'config'
    surface quietly becomes a policy surface."""
    _engine, app = await _env(store)
    admin = await authutil.client_as(app, "admin")
    editor = await authutil.client_as(app, "editor")
    try:
        assert (await admin.post("/api/scorer", json=GOOD)).status_code == 200
        # The editor's live session is unchanged in both directions: still no scorer.write…
        assert (await editor.post("/api/scorer", json=GOOD)).status_code == 403
        # …and still exactly the role it had.
        assert (await editor.get("/api/me")).json()["role"] == "editor"
        assert (await editor.get("/api/users")).status_code == 403
    finally:
        await admin.aclose()
        await editor.aclose()


# -- F20 over HTTP: an invalid set is a 4xx and is never stored -------------------------------


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"w_t": 0.3, "w_a": 0.35, "w_e": 0.35, "tau_s": 30.0, "threshold": 0.0}, 400),
        ({"w_t": 0.3, "w_a": 0.35, "w_e": 0.35, "tau_s": 30.0, "threshold": 1.0}, 400),
        ({"w_t": 0.02, "w_a": 0.02, "w_e": 0.02, "tau_s": 30.0, "threshold": 0.02}, 400),
        ({"w_t": 0.3, "w_a": 0.35, "w_e": 0.35, "tau_s": 0.5, "threshold": 0.5}, 400),
        ({"w_t": 0.3, "w_a": 0.35, "w_e": 0.35, "tau_s": 99999.0, "threshold": 0.5}, 422),
        ({"w_t": -1.0, "w_a": 0.35, "w_e": 0.35, "tau_s": 30.0, "threshold": 0.5}, 422),
    ],
)
async def test_f20_invalid_parameters_are_rejected_and_never_stored(
    store: Store, body: dict[str, float], expected: int
) -> None:
    """Pydantic bounds the shape (422); `scoring.validate_params` is the semantic authority and
    catches the degenerate combinations a range check misses (400). Either way: no row."""
    _engine, app = await _env(store)
    admin = await authutil.client_as(app, "admin")
    try:
        response = await admin.post("/api/scorer", json=body)
        assert response.status_code == expected, response.text
        if expected == 400:
            assert len(response.json()["detail"]) > 20  # a precise reason, not "invalid"
    finally:
        await admin.aclose()
    async with store.lock:
        cur = await store.conn.execute("SELECT COUNT(*) FROM scorer_config")
        row = await cur.fetchone()
    assert row is not None and row[0] == 1  # only the seed


async def test_f20_invalid_preview_parameters_are_rejected_too(store: Store) -> None:
    _engine, app = await _env(store)
    admin = await authutil.client_as(app, "admin")
    try:
        bad = {"w_t": 0.3, "w_a": 0.35, "w_e": 0.35, "tau_s": 30.0, "threshold": 0.0}
        assert (await admin.post("/api/scorer/preview", json=bad)).status_code == 400
    finally:
        await admin.aclose()


# -- apply, rollback, provenance ---------------------------------------------------------------


async def test_apply_appends_a_row_moves_the_pointer_and_audits_before_after(
    store: Store,
) -> None:
    engine, app = await _env(store)
    admin = await authutil.client_as(app, "admin")
    try:
        applied = await admin.post("/api/scorer", json=GOOD)
        assert applied.status_code == 200
        config_id = applied.json()["config_id"]
        read = (await admin.get("/api/scorer")).json()
    finally:
        await admin.aclose()
    assert config_id == 2
    assert [row["id"] for row in read["history"]] == [2, 1]
    assert read["history"][0]["active"] is True and read["history"][1]["active"] is False
    assert read["history"][0]["created_by"] == "adm" and read["history"][0]["note"] == "wider"

    async with store.lock:
        active = await store.active_scorer_config()
        rows = await store.audit_all()
    assert active is not None and int(active["id"]) == config_id
    entry = next(r for r in rows if r["action"] == "scorer.config.update")
    assert entry["outcome"] == "ok" and entry["object_id"] == str(config_id)
    assert '"action":"apply"' in entry["details"]
    assert '"tau_s":30.0' in entry["details"]  # before
    assert '"tau_s":45.0' in entry["details"]  # after

    # The engine picks it up at the documented reload point, not before.
    active_scorer = engine.correlator.scorer.active
    assert isinstance(active_scorer, scoring.AdditiveScorer) and active_scorer.tau_s == 30.0
    await engine.maintenance(BASE + 1, retention_days=365.0)
    active_scorer = engine.correlator.scorer.active
    assert isinstance(active_scorer, scoring.AdditiveScorer) and active_scorer.tau_s == 45.0


async def test_rollback_is_one_call_and_loses_nothing(store: Store) -> None:
    engine, app = await _env(store)
    admin = await authutil.client_as(app, "admin")
    try:
        new_id = (await admin.post("/api/scorer", json=GOOD)).json()["config_id"]
        await engine.maintenance(BASE + 1, retention_days=365.0)
        rolled = await admin.post("/api/scorer/rollback", json={"config_id": 1})
        assert rolled.status_code == 200
        history = (await admin.get("/api/scorer")).json()["history"]
    finally:
        await admin.aclose()
    # History is immutable: the rolled-back-from row is still there, just no longer active.
    assert [row["id"] for row in history] == [new_id, 1]
    assert [row["active"] for row in history] == [False, True]

    await engine.maintenance(BASE + 2, retention_days=365.0)
    active = engine.correlator.scorer.active
    assert isinstance(active, scoring.AdditiveScorer)
    assert active.params() == scoring.AdditiveScorer().params()

    async with store.lock:
        rows = await store.audit_all()
    rollback_row = next(
        r for r in rows if r["action"] == "scorer.config.update" and '"rollback"' in r["details"]
    )
    assert rollback_row["object_id"] == "1"


async def test_rollback_to_an_unknown_config_is_404(store: Store) -> None:
    _engine, app = await _env(store)
    admin = await authutil.client_as(app, "admin")
    try:
        response = await admin.post("/api/scorer/rollback", json={"config_id": 4242})
        assert response.status_code == 404
    finally:
        await admin.aclose()


async def test_new_situations_record_the_new_configuration(store: Store) -> None:
    """Provenance follows the active pointer: situations opened after a change carry the new id."""
    engine, app = await _env(store)
    admin = await authutil.client_as(app, "admin")
    try:
        new_id = (await admin.post("/api/scorer", json=GOOD)).json()["config_id"]
    finally:
        await admin.aclose()
    await engine.maintenance(BASE + 1, retention_days=365.0)
    await util.drive(engine, engine.queue, util.fixture_events("olt_storm.json", BASE + 5000))
    async with store.lock:
        cur = await store.conn.execute(
            "SELECT DISTINCT scorer_config_id FROM situation ORDER BY scorer_config_id"
        )
        ids = [int(r[0]) for r in await cur.fetchall()]
    assert ids == [1, new_id]  # the old situations kept theirs; the new ones carry the new id


# -- F22: preview ------------------------------------------------------------------------------


async def test_f22_preview_returns_a_structural_delta(store: Store) -> None:
    _engine, app = await _env(store)
    admin = await authutil.client_as(app, "admin")
    try:
        body = (await admin.post("/api/scorer/preview", json=GOOD)).json()
    finally:
        await admin.aclose()
    for key in (
        "situations_before",
        "situations_after",
        "merged",
        "split",
        "links_before",
        "links_after",
        "alarms_considered",
        "caveat",
    ):
        assert key in body, key
    assert body["alarms_considered"] > 0
    assert body["alarms_cap"] == preview.MAX_PREVIEW_ALARMS
    assert body["active_params"] == scoring.AdditiveScorer().params()
    assert body["candidate_params"]["tau_s"] == 45.0
    assert "directional" in body["caveat"].lower()


async def test_f22_preview_detects_a_merge_and_a_shatter(store: Store) -> None:
    """The answer must be *useful*: a permissive set visibly widens grouping and a strict one
    visibly shatters it, on the caller's own data."""
    # background_noise is the right shape for this: unrelated alarms that the default scorer
    # keeps in a dozen separate groups, so both directions of the change are visible.
    _engine, app = await _env(store, fixture="background_noise.json")
    admin = await authutil.client_as(app, "admin")
    try:
        wide = await admin.post(
            "/api/scorer/preview",
            json={"w_t": 0.34, "w_a": 0.33, "w_e": 0.33, "tau_s": 3600.0, "threshold": 0.02},
        )
        strict = await admin.post(
            "/api/scorer/preview",
            json={"w_t": 0.34, "w_a": 0.33, "w_e": 0.33, "tau_s": 1.0, "threshold": 0.99},
        )
    finally:
        await admin.aclose()
    wide_body, strict_body = wide.json(), strict.json()
    assert wide_body["situations_after"] < wide_body["situations_before"]
    assert wide_body["merged"], "a near-zero threshold must show groups merging"
    assert wide_body["links_after"] > wide_body["links_before"]
    assert strict_body["situations_after"] > strict_body["situations_before"]
    assert strict_body["links_after"] == 0  # nothing links at all


async def test_f22_preview_is_deterministic_across_two_runs(store: Store) -> None:
    _engine, app = await _env(store)
    admin = await authutil.client_as(app, "admin")
    try:
        first = (await admin.post("/api/scorer/preview", json=GOOD)).json()
        second = (await admin.post("/api/scorer/preview", json=GOOD)).json()
    finally:
        await admin.aclose()
    assert first == second


async def test_f22_preview_mutates_nothing(store: Store) -> None:
    """F22: read-only. Every table is compared row-for-row; `audit_log` is excluded because
    preview is *required* to record itself, and that row is asserted separately."""
    _engine, app = await _env(store)
    admin = await authutil.client_as(app, "admin")  # log in FIRST: a session row is not preview
    try:
        async with store.lock:
            before = await _snapshot(store)
            audit_before = len(await store.audit_all())
        assert (await admin.post("/api/scorer/preview", json=GOOD)).status_code == 200
        async with store.lock:
            after = await _snapshot(store)
            rows = await store.audit_all()
    finally:
        await admin.aclose()
    assert after == before, "preview changed persistent state"
    # The tables that would prove a violation are all in the comparison above.
    for critical in ("link", "situation", "situation_alarm", "alarm", "edge", "scorer_config"):
        assert critical in before, f"{critical} missing from the snapshot — assertion is vacuous"
    previews = [r for r in rows if r["action"] == "scorer.preview"]
    assert len(previews) == 1 and previews[0]["outcome"] == "ok"
    assert len(rows) - audit_before == 1  # preview wrote exactly one row: its own


async def test_f22_preview_discloses_no_new_fields(store: Store) -> None:
    """Aggregate structure only: no alarm payload, no varbind, no device address — nothing the
    caller could not already read through `shaping.py`."""
    _engine, app = await _env(store)
    admin = await authutil.client_as(app, "admin")
    try:
        raw = (await admin.post("/api/scorer/preview", json=GOOD)).text
    finally:
        await admin.aclose()
    for leaked in ("127.0.0.", "varbind", "community", "instance", "oid", "1.3.6.1"):
        assert leaked not in raw, f"preview response leaked {leaked!r}"


async def test_f22_preview_is_bounded_by_the_alarm_cap(store: Store) -> None:
    """The cap is enforced at the SQL level, so the work is bounded before it is done."""
    _engine, _app = await _env(store)
    async with store.lock:
        rows = await store.recent_alarms_for_preview(3)
    assert len(rows) == 3
    assert [r["first_seen"] for r in rows] == sorted(r["first_seen"] for r in rows)


async def test_f22_preview_is_bounded_by_a_wall_clock_budget(store: Store) -> None:
    """A deadline already in the past aborts the partition rather than returning half an answer:
    a partial regrouping presented as a preview would be worse than no preview."""
    _engine, _app = await _env(store)
    async with store.lock:
        rows = await store.recent_alarms_for_preview(preview.MAX_PREVIEW_ALARMS)
    alarms = [
        preview.PreviewAlarm(
            alarm_id=int(r["id"]),
            class_id=int(r["class_id"]),
            ne_id=int(r["ne_id"] or 0),
            entity_id=int(r["entity_id"] or 0),
            ts=float(r["first_seen"]),
        )
        for r in rows
    ]
    from netcorenoc.learn import Learner

    with pytest.raises(preview.PreviewTimeoutError):
        preview.partition(alarms, scoring.AdditiveScorer(), Learner(), deadline=0.0)


async def test_f22_preview_has_its_own_tight_rate_limit(store: Store) -> None:
    """Preview is the one endpoint whose cost warrants more than a share of the general bucket."""
    _engine, app = await _env(store, preview_rate_capacity=2.0, preview_rate_refill=0.0)
    admin = await authutil.client_as(app, "admin")
    try:
        statuses = [
            (await admin.post("/api/scorer/preview", json=GOOD)).status_code for _ in range(4)
        ]
    finally:
        await admin.aclose()
    assert statuses[:2] == [200, 200]
    assert statuses[2:] == [429, 429]


async def test_f22_preview_timeout_is_reported_not_faked(store: Store) -> None:
    """When the budget is blown the caller gets a 503 saying nothing changed — never a truncated
    answer dressed up as a complete one."""
    _engine, app = await _env(store)
    admin = await authutil.client_as(app, "admin")
    original = preview.PREVIEW_TIMEOUT_S
    try:
        preview.PREVIEW_TIMEOUT_S = -1.0  # the deadline is already past
        response = await admin.post("/api/scorer/preview", json=GOOD)
    finally:
        preview.PREVIEW_TIMEOUT_S = original
        await admin.aclose()
    assert response.status_code == 503
    assert "no change was made" in response.json()["detail"]


async def test_f22_preview_does_not_disturb_the_running_engine(store: Store) -> None:
    """Preview is off the ingest path and holds the learned matrices fixed: running one changes
    neither the live scorer nor the learner."""
    engine, app = await _env(store)
    before_params = engine.correlator.scorer.active.params_fingerprint()
    before_epoch = (engine.learner.A.epoch, engine.learner.E.epoch)
    admin = await authutil.client_as(app, "admin")
    try:
        await admin.post("/api/scorer/preview", json=GOOD)
    finally:
        await admin.aclose()
    assert engine.correlator.scorer.active.params_fingerprint() == before_params
    assert (engine.learner.A.epoch, engine.learner.E.epoch) == before_epoch
    assert engine.scorer_config_id == 1


# -- headers on the new routes -----------------------------------------------------------------


async def test_scorer_routes_carry_the_security_headers(store: Store) -> None:
    _engine, app = await _env(store)
    admin = await authutil.client_as(app, "admin")
    try:
        responses: list[httpx.Response] = [
            await admin.get("/api/scorer"),
            await admin.post("/api/scorer/preview", json=GOOD),
            await admin.post("/api/scorer", json=GOOD),
        ]
    finally:
        await admin.aclose()
    for response in responses:
        assert response.headers["Content-Security-Policy"].startswith("default-src 'none'")
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Referrer-Policy"] == "no-referrer"
        assert response.headers["Cache-Control"] == "no-store"
