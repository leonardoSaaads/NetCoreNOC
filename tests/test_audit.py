"""Phase 4.5 — audit catalog completeness, hash chain, tamper detection, retention."""

from __future__ import annotations

import sqlite3

import httpx
import pytest

from netcorenoc.api import create_app
from netcorenoc.crosscutting import audit
from netcorenoc.engine.correlate.correlate import Correlator, WindowAlarm
from netcorenoc.engine.correlate.scoring import LinkFeatures, LinkScore
from netcorenoc.main import Engine
from netcorenoc.store import Store

import authutil
import util

BASE = 2_000_000.0


class _FailingScorer:
    """Test-only: proves the fail-safe path emits its catalog action (see test_scoring.py)."""

    scorer_id = "test-failing"
    contract_version = "1.0"

    def score(self, features: LinkFeatures) -> LinkScore:
        raise RuntimeError("deliberate failure")

    def params_fingerprint(self) -> str:
        return "failing"


async def _actions(store: Store) -> list[tuple[str, str]]:
    async with store.lock:
        rows = await store.audit_all()
    return [(r["action"], r["outcome"]) for r in rows]


async def _drive_every_action(store: Store) -> tuple[Engine, httpx.AsyncClient]:
    """Exercise a flow that produces every catalog action, and return admin client."""
    engine = Engine(store, __import__("asyncio").Queue())
    await engine.start()
    await authutil.make_users(store)
    # Ingest a real situation so feedback/close have a target.
    await util.drive(engine, engine.queue, util.fixture_events("fiber_cut.json", BASE))
    # Also quarantine one malformed packet so the quarantine viewer has content.
    async with store.lock:
        from netcorenoc.ingest.receiver import quarantine_packet

        await store.quarantine_packet(quarantine_packet("10.0.0.9", b"\x30\x05bad", "ber", BASE))
        await store.commit()
    app = create_app(engine, rate_capacity=100000.0)

    admin = await authutil.client_as(app, "admin")  # login.ok
    sid = (await admin.get("/api/situations")).json()[0]["id"]
    await admin.post(f"/api/situations/{sid}/feedback", json={"verdict": "confirm"})  # feedback
    await admin.post("/api/labels", json={"kind": "ne", "id": 1, "label": "core-1"})  # label.set
    await admin.delete("/api/labels/ne/1")  # label.clear
    made = await admin.post(
        "/api/users", json={"username": "temp", "password": "temp-pw-123456", "role": "viewer"}
    )
    uid = made.json()["id"]  # user.create
    await admin.post(f"/api/users/{uid}/role", json={"role": "editor"})  # role.change
    await admin.delete(f"/api/users/{uid}")  # user.delete
    tok = await admin.post("/api/tokens", json={"name": "svc", "role": "viewer"})
    tid = tok.json()["id"]  # token.create
    await admin.delete(f"/api/tokens/{tid}")  # token.revoke
    await admin.post("/api/config", json={"allowlist": "10.0.0.0/8", "retention_days": 5})  # config
    await admin.post(f"/api/situations/{sid}/close")  # situation.close
    await admin.get("/api/quarantine")  # quarantine.read
    await admin.get("/api/audit")  # audit.read
    await admin.get("/api/audit/export")  # audit.export
    await admin.post("/api/audit/prune")  # prune.manual

    # v0.6.0 — the scoring seam. All three new actions are driven here, exactly like the rest.
    await admin.post(  # scorer.preview
        "/api/scorer/preview",
        json={"w_t": 0.4, "w_a": 0.3, "w_e": 0.3, "tau_s": 45.0, "threshold": 0.55},
    )
    await admin.post(  # scorer.config.update (apply)
        "/api/scorer",
        json={"w_t": 0.4, "w_a": 0.3, "w_e": 0.3, "tau_s": 45.0, "threshold": 0.55, "note": "x"},
    )
    await admin.post("/api/scorer/rollback", json={"config_id": 1})  # scorer.config.update

    # v0.7.0 — governance. Both policy actions are driven here, exactly like the rest, and both
    # are then cleared so the rest of this flow runs against the shipped baseline.
    await admin.post(  # rbac.policy.update (apply)
        "/api/rbac",
        json={"document": {"version": 1, "roles": {"viewer": ["self.read"]}}, "note": "x"},
    )
    await admin.post("/api/rbac", json={"clear": True})  # rbac.policy.update (clear)
    await admin.post(  # scope.policy.update (apply)
        "/api/scope",
        json={"document": {"version": 1, "roles": {"viewer": ["10.0.0.0/8"]}}, "note": "x"},
    )
    await admin.post("/api/scope", json={"clear": True})  # scope.policy.update (clear)
    # scorer.fallback: a scorer that raises degrades the engine to the coded defaults, and the
    # maintenance pass records it once with the system actor.
    engine.correlator.set_scorer(_FailingScorer())
    engine.correlator.scorer.score(
        Correlator.features(
            WindowAlarm(1, 1, 1, ts=BASE), WindowAlarm(2, 2, 1, ts=BASE + 1), engine.learner
        )
    )
    await engine.maintenance(BASE + 20, retention_days=365.0)

    # password.change: a throwaway user changes its own password.
    await admin.post(
        "/api/users", json={"username": "pwu", "password": "pw-user-123456", "role": "viewer"}
    )
    async with authutil.new_client(app) as pwc:
        await pwc.post("/api/login", json={"username": "pwu", "password": "pw-user-123456"})
        await pwc.post(
            "/api/password",
            json={"old_password": "pw-user-123456", "new_password": "pw-user-new-9999"},
        )  # password.change

    # A denied sensitive read (viewer -> quarantine), before we poison the source IP.
    viewer = await authutil.client_as(app, "viewer")
    await viewer.get("/api/quarantine")  # quarantine.read / denied
    await viewer.aclose()

    # Do the failed-login + lockout burst LAST — it locks this source IP (shared across the
    # in-process clients), so nothing after it may need to authenticate.
    async with authutil.new_client(app) as bad:
        for _ in range(6):  # 5 x login.fail, then login.lockout
            await bad.post("/api/login", json={"username": "adm", "password": "x" * 12})
    return engine, admin


# Actions _drive_every_action produces (plus the completeness test's own logout). The one
# not produced here — user.update — is asserted in test_findings.
EXPECTED_ACTIONS = {
    "login.ok",
    "login.fail",
    "login.lockout",
    "logout",
    "password.change",
    "user.create",
    "user.delete",
    "role.change",
    "token.create",
    "token.revoke",
    "config.change",
    "feedback",
    "label.set",
    # v0.16.3: withdrawing a declaration is its own action rather than a `label.set` carrying a
    # flag, so an auditor asking who removed an operator's severity does not have to read a
    # details blob to find out that a row recorded a removal (DECISIONS #284).
    "label.clear",
    "situation.close",
    "prune.manual",
    "quarantine.read",
    "audit.read",
    "audit.export",
    "scorer.preview",
    "scorer.config.update",
    "scorer.fallback",
    # v0.7.0 — governance. `governance.fallback` is a *system* action emitted only when a stored
    # policy will not parse; it is driven and asserted in
    # test_governance.py::test_f29_malformed_capability_policy_falls_back_to_the_ceiling rather
    # than here, because this flow deliberately stores only well-formed policies.
    "rbac.policy.update",
    "scope.policy.update",
}


async def test_audit_catalog_completeness(store: Store) -> None:
    _engine, admin = await _drive_every_action(store)
    # logout at the end produces logout; do it before dumping.
    await admin.post("/api/logout")
    await admin.aclose()
    produced = {action for action, _ in await _actions(store)}
    missing = EXPECTED_ACTIONS - produced
    assert not missing, f"catalog actions never emitted: {missing}"
    # denied sensitive read recorded with outcome=denied
    assert ("quarantine.read", "denied") in await _actions(store)


async def test_login_events_have_expected_outcomes(store: Store) -> None:
    _engine, admin = await _drive_every_action(store)
    await admin.aclose()
    rows = await _actions(store)
    assert ("login.ok", "ok") in rows
    assert ("login.fail", "denied") in rows
    assert ("login.lockout", "denied") in rows


async def test_audit_chain_verifies_after_a_busy_session(store: Store) -> None:
    _engine, admin = await _drive_every_action(store)
    await admin.aclose()
    result = await audit.verify_chain(store)
    assert result.ok, result.render()
    assert result.checked > 10


async def test_audit_tamper_is_detected_at_the_exact_row(store: Store) -> None:
    async with store.lock:
        for action in ("login.ok", "feedback", "logout"):
            await audit.write_event(
                store,
                ts=1.0,
                actor="a",
                role="admin",
                source_ip="-",
                action=action,
                outcome="ok",
                details={},
            )
        await store.commit()
    # Tamper with row 2 the way an attacker with raw file access would: drop the guard first.
    raw = sqlite3.connect(store._path)
    raw.execute("DROP TRIGGER audit_log_no_update")
    raw.execute("UPDATE audit_log SET actor='mallory' WHERE id=2")
    raw.commit()
    raw.close()
    result = await audit.verify_chain(store)
    assert not result.ok
    assert result.first_broken_id == 2


async def test_audit_log_is_append_only(store: Store) -> None:
    async with store.lock:
        await audit.write_event(
            store,
            ts=1.0,
            actor="a",
            role="admin",
            source_ip="-",
            action="login.ok",
            outcome="ok",
            details={},
        )
        await store.commit()
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            await store.conn.execute("UPDATE audit_log SET actor='x' WHERE id=1")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            await store.conn.execute("DELETE FROM audit_log WHERE id=1")


def test_redact_details_drops_secret_keys() -> None:
    cleaned = audit.redact_details(
        {
            "password": "hunter2",
            "token": "abc",
            "community": "public",
            "note": "kept",
            "role": "admin",
        }
    )
    assert cleaned == {"note": "kept", "role": "admin"}


async def test_audit_details_never_contain_secret_values(store: Store) -> None:
    _engine, admin = await _drive_every_action(store)
    # Capture a real service-token value and confirm it never lands in an audit detail.
    created = await admin.post("/api/tokens", json={"name": "leaktest", "role": "viewer"})
    token_value = created.json()["token"]
    await admin.aclose()
    async with store.lock:
        rows = await store.audit_all()
    joined = " ".join(r["details"] for r in rows)
    assert "temp-pw-123456" not in joined  # a created user's password
    assert authutil.PW not in joined  # any login password
    assert token_value not in joined  # a service-token value


async def test_audit_excluded_from_general_prune_but_retained_by_admin_prune(store: Store) -> None:
    async with store.lock:
        # An ancient audit row (older than any retention horizon).
        await audit.write_event(
            store,
            ts=1.0,
            actor="a",
            role="admin",
            source_ip="-",
            action="login.ok",
            outcome="ok",
            details={},
        )
        await store.commit()
        # The general prune must not touch audit_log.
        await store.prune(now=BASE, retention_s=1.0)
        rows_after_general = await store.audit_all()
    assert len(rows_after_general) == 1  # general prune left the audit row intact
    async with store.lock:
        removed = await store.prune_audit(cutoff=BASE)  # admin retention prune
        await store.commit()
        remaining = await store.audit_all()
    assert removed == 1 and remaining == []
