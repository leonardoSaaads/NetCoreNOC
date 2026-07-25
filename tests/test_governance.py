"""v0.7.0 governance: the capability ceiling, visibility scoping, and their failure modes.

Findings F27-F33 of `docs/security/SECURITY-REVIEW-0.7.md`. The two properties this file exists
to prove:

1. **No stored policy can grant a capability outside a role's compiled ceiling** — property-based
   over generated and adversarial policies, and over rows written straight into the table behind
   the API's back.
2. **Nothing changes when no policy is stored** — the governance-parity gate.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sqlite3
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from netcorenoc import api, audit, auth, rbac, shaping
from netcorenoc.store import Store

import authutil
import util

BASE = 3_000_000.0


# --- helpers ------------------------------------------------------------------------------


async def _seed_network(store: Store) -> None:
    """Two NEs with alarms, in one situation, so scoping has a mixed-membership case to redact."""
    async with store.lock:
        for ip in ("10.0.0.1", "10.9.9.9"):
            await store.device_id(ip, BASE)
            ne_id = await store.ne_id(ip, BASE)
            await store.entity_level0(ne_id, ip, BASE)
        await store.commit()


async def _write_policy(
    client: httpx.AsyncClient, kind: str, document: dict[str, object]
) -> httpx.Response:
    return await client.post(f"/api/{kind}", json={"document": document, "note": "test"})


async def _store_policy_directly(store: Store, kind: str, document: dict[str, object]) -> int:
    """Insert and activate a policy **bypassing the API entirely**.

    This is the adversary who has the database file, or a future second write path, or a bad
    migration. Under the ceiling-intersection model it must change nothing about what a role can
    reach beyond its ceiling (DECISIONS #53).
    """
    text = json.dumps(document, sort_keys=True, separators=(",", ":"))
    async with store.lock:
        policy_id = await store.insert_governance_policy(kind, text, "x" * 64, None, BASE, "")
        await store.set_active_governance_policy(kind, policy_id, None, BASE)
        await store.commit()
    return policy_id


# --- F27: privilege escalation via a stored policy is structurally impossible ---------------


@settings(max_examples=250, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    role_grants=st.dictionaries(
        st.sampled_from(["viewer", "editor", "admin", "root", "", "ADMIN"]),
        st.lists(
            st.sampled_from(
                [*sorted(rbac.PERMISSIONS), "users.manage", "audit.read", "not.a.capability", ""]
            ),
            max_size=8,
        ),
        max_size=4,
    ),
    principal_grants=st.dictionaries(
        st.sampled_from(["user:1", "token:1", "user:999", "*", "admin"]),
        st.lists(st.sampled_from([*sorted(rbac.PERMISSIONS), "audit.prune"]), max_size=6),
        max_size=3,
    ),
)
def test_f27_no_generated_policy_resolves_above_the_ceiling(
    role_grants: dict[str, list[str]], principal_grants: dict[str, list[str]]
) -> None:
    """THE ceiling invariant, property-based: `resolved ⊆ ceiling(role)`, for every input.

    The strategies deliberately include capabilities a role must never hold (`users.manage` and
    `audit.read` for a viewer), unknown roles, unknown capabilities, empty strings, and a
    wildcard-looking principal — none of which is special-cased anywhere, because the intersection
    makes them all inert rather than requiring a rule per case.
    """
    document = json.dumps({"version": 1, "roles": role_grants, "principals": principal_grants})
    policy = rbac.parse_capability_policy(document)
    for role in ("viewer", "editor", "admin"):
        for ref in (None, "user:1", "token:1", "user:999"):
            resolved = rbac.resolve_capabilities(role, ref, policy)
            above = resolved - rbac.ceiling(role)
            assert not above, f"{role}/{ref} resolved outside its ceiling: {sorted(above)}"


def test_f27_a_policy_naming_an_above_ceiling_capability_is_inert() -> None:
    """The concrete case: handing `viewer` every admin capability changes nothing."""
    document = json.dumps({"version": 1, "roles": {"viewer": sorted(rbac.PERMISSIONS)}})
    policy = rbac.parse_capability_policy(document)
    resolved = rbac.resolve_capabilities("viewer", None, policy)
    assert resolved == rbac.ceiling("viewer")
    for capability in ("users.manage", "audit.read", "config.write", "scorer.write", "rbac.write"):
        assert capability not in resolved


def test_f27_a_policy_can_only_narrow() -> None:
    """Removing works, re-enabling up to the ceiling works, going above it does not."""
    narrowed = rbac.parse_capability_policy(
        json.dumps({"version": 1, "roles": {"editor": ["stats.read"]}})
    )
    assert rbac.resolve_capabilities("editor", None, narrowed) == frozenset({"stats.read"})

    restored = rbac.parse_capability_policy(
        json.dumps({"version": 1, "roles": {"editor": sorted(rbac.ceiling("editor"))}})
    )
    assert rbac.resolve_capabilities("editor", None, restored) == rbac.ceiling("editor")


async def test_f27_policy_written_directly_to_the_db_cannot_escalate(store: Store) -> None:
    """The adversary who bypasses the API: a hand-written row grants a viewer nothing.

    This is the case a write-time validation check cannot cover, and the reason the guarantee is
    an intersection instead (DECISIONS #53).
    """
    _engine, _queue, app = await authutil.make_env(store)
    await _store_policy_directly(
        store,
        "rbac",
        {"version": 1, "roles": {"viewer": ["users.manage", "audit.read", "rbac.write"]}},
    )
    viewer = await authutil.client_as(app, "viewer")
    try:
        for method, path in (
            ("GET", "/api/users"),
            ("GET", "/api/audit"),
            ("GET", "/api/rbac"),
        ):
            resp = await viewer.request(method, path)
            assert resp.status_code == 403, (path, resp.status_code)
    finally:
        await viewer.aclose()


async def test_f27_rbac_and_scope_write_are_admin_only(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    for role in ("viewer", "editor"):
        client = await authutil.client_as(app, role)
        try:
            for path in ("/api/rbac", "/api/scope"):
                assert (await client.get(path)).status_code == 403
                assert (await client.post(path, json={"clear": True})).status_code == 403
        finally:
            await client.aclose()


async def test_f27_denied_governance_attempts_are_audited(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    viewer = await authutil.client_as(app, "viewer")
    try:
        await viewer.get("/api/rbac")
        await viewer.get("/api/scope")
    finally:
        await viewer.aclose()
    async with store.lock:
        rows = [(r["action"], r["outcome"]) for r in await store.audit_all()]
    assert ("rbac.policy.update", "denied") in rows
    assert ("scope.policy.update", "denied") in rows


def test_f27_recovery_capabilities_stay_within_the_admin_ceiling() -> None:
    """DECISIONS #64: the one union in the resolver may not breach the bound it protects."""
    assert rbac.ceiling("admin") >= rbac.RECOVERY_CAPABILITIES
    stripped = rbac.parse_capability_policy(json.dumps({"version": 1, "roles": {"admin": []}}))
    resolved = rbac.resolve_capabilities("admin", None, stripped)
    assert resolved == rbac.RECOVERY_CAPABILITIES
    assert resolved <= rbac.ceiling("admin")
    # An admin stripped to nothing keeps exactly the repair surface — and nothing else.
    assert "users.manage" not in resolved and "audit.read" not in resolved
    assert "rbac.write" in resolved and "scope.write" in resolved


# --- F28: one decision site --------------------------------------------------------------


def test_f28_no_role_comparison_outside_rbac() -> None:
    """No authorization decision may be made by comparing a role anywhere but in `rbac.py`.

    `shaping.py` legitimately compares *rank* for field coarsening (a presentation rule, not an
    authorization one) and checks `role == "admin"` for the never-scoped exemption, so it is
    excluded; `api.py` is the file that must never grow a second decision.
    """
    import inspect

    source = inspect.getsource(api)
    for forbidden in ('role == "admin"', "role == 'admin'", "ROLE_RANK[", "role_allows("):
        assert forbidden not in source, (
            f"api.py contains {forbidden!r}: authorization must go through "
            "rbac.resolve_capabilities(), never a role comparison (F28)"
        )
    assert "rbac.resolve_capabilities(" in source


async def test_f28_scope_policy_grants_no_capability(store: Store) -> None:
    """Scope and authorization are independent: being in scope is not being authorized."""
    _engine, _queue, app = await authutil.make_env(store)
    await _seed_network(store)
    await _store_policy_directly(
        store, "scope", {"version": 1, "roles": {"viewer": ["10.0.0.0/8"]}}
    )
    viewer = await authutil.client_as(app, "viewer")
    try:
        assert (await viewer.get("/api/situations")).status_code == 200  # still viewer-readable
        assert (await viewer.get("/api/users")).status_code == 403  # still not an admin
        assert (await viewer.get("/api/audit")).status_code == 403
    finally:
        await viewer.aclose()


# --- F29: fail-safe in both directions, and never a lockout --------------------------------


async def test_f29_malformed_capability_policy_falls_back_to_the_ceiling(store: Store) -> None:
    """Capabilities fail *safe*, not closed: to the shipped baseline, with a warning and a row."""
    _engine, _queue, app = await authutil.make_env(store)
    await _store_policy_directly(store, "rbac", {"version": 1, "roles": "not-a-mapping"})
    editor = await authutil.client_as(app, "editor")
    admin = await authutil.client_as(app, "admin")
    try:
        # Everything an editor could do before, it can still do — v0.6.0 behaviour exactly.
        assert (await editor.get("/api/situations")).status_code == 200
        assert (await editor.get("/api/users")).status_code == 403  # and still no more than that
        stats = (await admin.get("/api/stats")).json()
        assert any("capability policy could not be read" in w for w in stats["warnings"])
        # The admin can always reach the repair path.
        assert (await admin.get("/api/rbac")).json()["malformed"] is True
        assert (await admin.post("/api/rbac", json={"clear": True})).status_code == 200
    finally:
        await editor.aclose()
        await admin.aclose()
    async with store.lock:
        rows = [(r["action"], r["outcome"]) for r in await store.audit_all()]
    assert ("governance.fallback", "error") in rows


async def test_f29_malformed_scope_policy_denies_viewer_and_editor(store: Store) -> None:
    """Scope fails *closed*: viewers and editors see nothing, and admin is untouched."""
    engine, queue, app = await authutil.make_env(store)
    await util.drive(engine, queue, util.fixture_events("fiber_cut.json", BASE))
    await _store_policy_directly(store, "scope", {"version": 1, "roles": ["wrong", "shape"]})
    viewer = await authutil.client_as(app, "viewer")
    admin = await authutil.client_as(app, "admin")
    try:
        assert (await viewer.get("/api/situations")).json() == []
        assert (await viewer.get("/api/entities")).json() == []
        assert (await viewer.get("/api/graph")).json()["nodes"] == []
        assert (await viewer.get("/api/stats")).json()["devices"] == 0
        # Admin is never scoped — which is what makes denying viewers safe.
        assert (await admin.get("/api/entities")).json() != []
        assert (await admin.get("/api/scope")).json()["malformed"] is True
        assert (await admin.post("/api/scope", json={"clear": True})).status_code == 200
        assert (await viewer.get("/api/entities")).json() != []  # repaired on the next request
    finally:
        await viewer.aclose()
        await admin.aclose()


async def test_f29_admin_cannot_be_locked_out_by_a_well_formed_policy(store: Store) -> None:
    """The lockout the malformed-policy fallback would never catch (DECISIONS #64)."""
    _engine, _queue, app = await authutil.make_env(store)
    await _store_policy_directly(store, "rbac", {"version": 1, "roles": {"admin": []}})
    admin = await authutil.client_as(app, "admin")
    try:
        # Stripped of everything, an admin retains exactly the repair surface...
        assert (await admin.get("/api/rbac")).status_code == 200
        assert (await admin.get("/api/scope")).status_code == 200
        # ...and nothing more, so this is a real restriction and not a special case that ignores
        # the policy.
        assert (await admin.get("/api/users")).status_code == 403
        assert (await admin.get("/api/audit")).status_code == 403
        # And the repair works.
        assert (await admin.post("/api/rbac", json={"clear": True})).status_code == 200
        assert (await admin.get("/api/users")).status_code == 200
    finally:
        await admin.aclose()


# --- F30: session staleness ----------------------------------------------------------------


async def test_f30_revoked_capability_does_not_survive_an_open_session(store: Store) -> None:
    """A capability taken away lands on the very next request of an already-open session."""
    _engine, _queue, app = await authutil.make_env(store)
    editor = await authutil.client_as(app, "editor")
    admin = await authutil.client_as(app, "admin")
    try:
        assert (await editor.get("/api/situations")).status_code == 200
        resp = await _write_policy(
            admin,
            "rbac",
            {"version": 1, "roles": {"editor": ["self.read", "stats.read"]}},
        )
        assert resp.status_code == 200, resp.text
        # Same cookie, same session, no re-login: the next request is already governed.
        assert (await editor.get("/api/situations")).status_code == 403
        assert (await editor.get("/api/stats")).status_code == 200
    finally:
        await editor.aclose()
        await admin.aclose()


async def test_f30_scope_change_takes_effect_on_the_next_request(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    await _seed_network(store)
    viewer = await authutil.client_as(app, "viewer")
    admin = await authutil.client_as(app, "admin")
    try:
        assert len((await viewer.get("/api/entities")).json()) == 2
        await _write_policy(admin, "scope", {"version": 1, "roles": {"viewer": ["10.0.0.1"]}})
        assert len((await viewer.get("/api/entities")).json()) == 1
        await admin.post("/api/scope", json={"clear": True})
        assert len((await viewer.get("/api/entities")).json()) == 2
    finally:
        await viewer.aclose()
        await admin.aclose()


# --- F31: provenance and audit integrity ---------------------------------------------------


async def test_f31_policy_history_is_append_only_and_survives_a_clear(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")
    try:
        await _write_policy(admin, "rbac", {"version": 1, "roles": {"viewer": ["self.read"]}})
        await admin.post("/api/rbac", json={"clear": True})
        body = (await admin.get("/api/rbac")).json()
        assert body["configured"] is False  # back to the shipped baseline...
        assert len(body["history"]) == 1  # ...but the record of what was active survives
    finally:
        await admin.aclose()
    async with store.lock:
        for sql in (
            "UPDATE governance_policy SET document='{}' WHERE id=1",
            "DELETE FROM governance_policy WHERE id=1",
        ):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                await store.conn.execute(sql)
                await store.conn.commit()
        await store.conn.rollback()
    assert (await audit.verify_chain(store)).ok


async def test_f31_every_policy_change_is_attributable(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")
    try:
        await _write_policy(admin, "rbac", {"version": 1, "roles": {"viewer": ["self.read"]}})
        await _write_policy(admin, "scope", {"version": 1, "roles": {"viewer": ["10.0.0.0/8"]}})
        await admin.post("/api/rbac", json={"clear": True})
    finally:
        await admin.aclose()
    async with store.lock:
        rows = [dict(r) for r in await store.audit_all()]
    policy_rows = [r for r in rows if r["action"].endswith(".policy.update")]
    assert len(policy_rows) == 3
    for row in policy_rows:
        details = json.loads(row["details"])
        assert row["actor"] == authutil.ROLE_USER["admin"] and row["role"] == "admin"
        assert details["action"] in ("applied", "rolled back", "cleared")
        assert "before" in details and "after" in details


async def test_f31_prune_does_not_touch_the_policy_tables(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    admin = await authutil.client_as(app, "admin")
    try:
        await _write_policy(admin, "rbac", {"version": 1, "roles": {"viewer": ["self.read"]}})
    finally:
        await admin.aclose()
    async with store.lock:
        await store.prune(BASE + 10_000_000.0, retention_s=1.0)
        await store.prune_audit(BASE + 10_000_000.0)
        cur = await store.conn.execute("SELECT COUNT(*) FROM governance_policy")
        row = await cur.fetchone()
    assert row is not None and row[0] == 1


# --- F32: scope bypass and existence disclosure --------------------------------------------


async def test_f32_out_of_scope_detail_is_indistinguishable_from_nonexistent(
    store: Store,
) -> None:
    """No existence oracle: status, body, and headers are identical for the two cases."""
    engine, queue, app = await authutil.make_env(store)
    await util.drive(engine, queue, util.fixture_events("fiber_cut.json", BASE))
    admin = await authutil.client_as(app, "admin")
    viewer = await authutil.client_as(app, "viewer")
    try:
        sid = (await admin.get("/api/situations")).json()[0]["id"]
        # Scope the viewer to an address range that matches nothing in the fixture.
        await _write_policy(admin, "scope", {"version": 1, "roles": {"viewer": ["203.0.113.0/24"]}})
        out_of_scope = await viewer.get(f"/api/situations/{sid}")
        nonexistent = await viewer.get("/api/situations/999999")
        assert out_of_scope.status_code == nonexistent.status_code == 404
        assert out_of_scope.json() == nonexistent.json()
        # The same for an NE detail.
        ne_out = await viewer.get("/api/entities/1")
        ne_missing = await viewer.get("/api/entities/999999")
        assert ne_out.status_code == ne_missing.status_code == 404
        assert ne_out.json() == ne_missing.json()
    finally:
        await admin.aclose()
        await viewer.aclose()


async def test_f32_redacted_members_disclose_no_identifier(store: Store) -> None:
    """A mixed-membership situation redacts to a count and classes — no id, address, or key."""
    engine, queue, app = await authutil.make_env(store)
    await util.drive(engine, queue, util.fixture_events("fiber_cut.json", BASE))
    admin = await authutil.client_as(app, "admin")
    viewer = await authutil.client_as(app, "viewer")
    try:
        sits = (await admin.get("/api/situations")).json()
        sid = max(sits, key=lambda s: s["alarm_count"])["id"]
        detail = (await admin.get(f"/api/situations/{sid}")).json()
        ips = sorted({a["device_ip"] for a in detail["alarms"]})
        assert len(ips) > 1, "need a multi-NE situation for this to mean anything"
        visible_ip, *hidden_ips = ips

        await _write_policy(admin, "scope", {"version": 1, "roles": {"viewer": [visible_ip]}})
        scoped = await viewer.get(f"/api/situations/{sid}")
        assert scoped.status_code == 200
        body = scoped.json()
        assert body["redacted_members"]["count"] > 0
        # Nothing identifying an out-of-scope NE appears anywhere in the body, at any depth.
        blob = json.dumps(body)
        for hidden in hidden_ips:
            assert hidden not in blob, f"out-of-scope address {hidden} leaked into the response"
        # Links to redacted members are withheld, so no alarm id survives as a dangling reference.
        visible_ids = {a["id"] for a in body["alarms"]}
        for link in body["links"]:
            assert link["alarm_a"] in visible_ids and link["alarm_b"] in visible_ids
    finally:
        await admin.aclose()
        await viewer.aclose()


async def test_f32_aggregates_are_computed_over_the_in_scope_set(store: Store) -> None:
    """No volume oracle: out-of-scope activity cannot move a scoped viewer's counters."""
    engine, queue, app = await authutil.make_env(store)
    await util.drive(engine, queue, util.fixture_events("fiber_cut.json", BASE))
    admin = await authutil.client_as(app, "admin")
    viewer = await authutil.client_as(app, "viewer")
    try:
        global_stats = (await admin.get("/api/stats")).json()
        entities = (await admin.get("/api/entities")).json()
        one_ip = entities[0]["ip"]
        await _write_policy(admin, "scope", {"version": 1, "roles": {"viewer": [one_ip]}})
        scoped = (await viewer.get("/api/stats")).json()
        assert scoped["devices"] == 1 < global_stats["devices"]
        assert scoped["active_alarms"] < global_stats["active_alarms"]
        assert scoped["classes"] <= global_stats["classes"]
        assert scoped["open_situations"] <= global_stats["open_situations"]
    finally:
        await admin.aclose()
        await viewer.aclose()


async def test_f32_every_ne_bearing_read_is_filtered(store: Store) -> None:
    """The sweep: no NE-bearing read path may forget the filter."""
    engine, queue, app = await authutil.make_env(store)
    await util.drive(engine, queue, util.fixture_events("fiber_cut.json", BASE))
    admin = await authutil.client_as(app, "admin")
    viewer = await authutil.client_as(app, "viewer")
    try:
        entities = (await admin.get("/api/entities")).json()
        assert len(entities) > 1
        one_ip = entities[0]["ip"]
        hidden_ips = {e["ip"] for e in entities[1:]}
        await _write_policy(admin, "scope", {"version": 1, "roles": {"viewer": [one_ip]}})
        for path in ("/api/entities", "/api/graph", "/api/timeline", "/api/situations"):
            blob = json.dumps((await viewer.get(path)).json())
            for hidden in hidden_ips:
                assert hidden not in blob, f"{path} leaked out-of-scope address {hidden}"
        # A graph edge needs BOTH ends in scope, or it would draw a line to an invisible node.
        graph = (await viewer.get("/api/graph")).json()
        node_ids = {n["id"] for n in graph["nodes"]}
        for edge in graph["edges"]:
            assert edge["a_id"] in node_ids and edge["b_id"] in node_ids
    finally:
        await admin.aclose()
        await viewer.aclose()


async def test_f32_admin_is_never_scoped(store: Store) -> None:
    engine, queue, app = await authutil.make_env(store)
    await util.drive(engine, queue, util.fixture_events("fiber_cut.json", BASE))
    admin = await authutil.client_as(app, "admin")
    try:
        before = (await admin.get("/api/entities")).json()
        # A policy that names admin explicitly is rejected as meaningless, and one that scopes
        # every role still leaves admin whole.
        rejected = await _write_policy(admin, "scope", {"version": 1, "roles": {"admin": []}})
        assert rejected.status_code == 400
        await _store_policy_directly(
            store, "scope", {"version": 1, "roles": {"admin": [], "viewer": []}}
        )
        assert (await admin.get("/api/entities")).json() == before
    finally:
        await admin.aclose()


def test_f32_scoping_is_not_tenant_isolation_is_documented() -> None:
    """The isolation over-claim control: the limit must be stated where operators will read it."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    for relative in (
        "docs/scope/SCOPE-0.7.md",
        "docs/architecture/DESIGN.md",
        "docs/security/threat-model.md",
        "README.md",
        "MIGRATION.md",
        "src/netcorenoc/ui/index.html",
        "src/netcorenoc/shaping.py",
    ):
        # Normalise typesetting before matching: strip Markdown/HTML emphasis and collapse
        # whitespace, so "**NOT** tenant isolation" and a line-wrapped "NOT tenant\n isolation"
        # both count. The claim is what must be present; how it is formatted is not the control.
        raw = (root / relative).read_text(encoding="utf-8")
        text = re.sub(r"\s+", " ", re.sub(r"[*_`]|</?[a-z]+>", "", raw))
        assert re.search(r"\bnot tenant isolation\b", text, re.IGNORECASE), (
            f"{relative} must state that visibility scoping is NOT tenant isolation"
        )


# --- F33: the hot path gained nothing ------------------------------------------------------


def test_f33_receiver_does_not_import_the_governance_modules() -> None:
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "src/netcorenoc/receiver.py").read_text(
        encoding="utf-8"
    )
    for forbidden in ("rbac", "shaping", "governance"):
        assert forbidden not in source, f"receiver.py must not reference {forbidden!r} (F33)"


def test_f33_datagram_received_gained_nothing() -> None:
    """The v0.6.0 F24 assertion, extended with the governance identifiers."""
    import inspect

    from netcorenoc.receiver import TrapReceiver

    source = inspect.getsource(TrapReceiver.datagram_received)
    for forbidden in (
        "rbac",
        "shaping",
        "scope",
        "capabilit",
        "policy",
        "await",
        "lock",
        "execute",
    ):
        assert forbidden not in source, (
            f"datagram_received must not contain {forbidden!r}: ingestion is sacred (F33)"
        )


def test_f33_engine_and_learning_are_untouched_by_governance() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "src/netcorenoc"
    for module in ("main.py", "learn.py", "correlate.py", "scoring.py", "rootcause.py"):
        source = (root / module).read_text(encoding="utf-8")
        for forbidden in ("resolve_capabilities", "visible_nes", "governance_policy"):
            assert forbidden not in source, (
                f"{module} references {forbidden!r}: governance is HTTP-side only (F33)"
            )


# --- the governance-parity gate ------------------------------------------------------------


async def test_empty_policy_resolves_exactly_to_the_v060_behaviour(store: Store) -> None:
    """With no policy stored, the resolver agrees with v0.6.0's `role_allows` for every pair.

    This is the parity gate stated as an equality rather than as a spot check: it is what lets the
    authorization matrix keep generating its expectations from `role_allows` and still describe
    what the server actually enforces.
    """
    await authutil.make_env(store)
    for role in rbac.ROLE_RANK:
        resolved = rbac.resolve_capabilities(role, None, None)
        for capability in rbac.PERMISSIONS:
            assert (capability in resolved) == rbac.role_allows(role, capability), (
                role,
                capability,
            )


async def test_no_governance_rows_on_a_fresh_database(store: Store) -> None:
    """A fresh install carries no policy, so it behaves exactly as v0.6.0 did."""
    async with store.lock:
        assert await store.active_governance_ids() == {}
        cur = await store.conn.execute("SELECT COUNT(*) FROM governance_policy")
        row = await cur.fetchone()
    assert row is not None and row[0] == 0


def test_unset_and_empty_are_different_statements() -> None:
    """DECISIONS #54: "no policy" and "a policy allowing nothing" must not collapse together."""
    unset = rbac.parse_capability_policy(json.dumps({"version": 1, "roles": {}}))
    empty = rbac.parse_capability_policy(json.dumps({"version": 1, "roles": {"viewer": []}}))
    assert rbac.resolve_capabilities("viewer", None, unset) == rbac.ceiling("viewer")
    assert rbac.resolve_capabilities("viewer", None, empty) == frozenset()

    unset_scope = shaping.parse_scope_policy(json.dumps({"version": 1, "roles": {}}))
    empty_scope = shaping.parse_scope_policy(json.dumps({"version": 1, "roles": {"viewer": []}}))
    nes = [{"id": 1, "ip": "10.0.0.1", "label": None}]
    assert shaping.visible_nes("viewer", None, unset_scope, nes).unrestricted
    resolved = shaping.visible_nes("viewer", None, empty_scope, nes)
    assert not resolved.unrestricted and resolved.ne_ids == frozenset()


# --- the scope model itself ----------------------------------------------------------------


NES = [
    {"id": 1, "ip": "10.0.0.1", "label": "core-1"},
    {"id": 2, "ip": "10.0.1.5", "label": None},
    {"id": 3, "ip": "192.168.4.9", "label": "lab-a"},
]


@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        ("ne:2", {2}),
        ("10.0.0.1", {1}),
        ("10.0.0.0/16", {1, 2}),
        ("192.168.0.0/16", {3}),
        ("core-*", {1}),
        ("lab-*", {3}),
        ("*", {1, 2, 3}),
        ("nonsense", set()),
        ("10.0.0.0/99", set()),  # an unparseable CIDR hides NEs rather than revealing them
    ],
)
def test_scope_selector_forms(selector: str, expected: set[int]) -> None:
    policy = shaping.parse_scope_policy(json.dumps({"version": 1, "roles": {"viewer": [selector]}}))
    assert shaping.visible_nes("viewer", None, policy, NES).ne_ids == frozenset(expected)


def test_scope_layers_union_and_an_unset_layer_expresses_no_opinion() -> None:
    """DECISIONS #63, the four cases that reading the spec literally would get wrong."""

    def resolve(document: dict[str, object]) -> shaping.Scope:
        return shaping.visible_nes(
            "viewer", "user:7", shaping.parse_scope_policy(json.dumps(document)), NES
        )

    # Both unset -> everything (parity).
    assert resolve({"version": 1}).unrestricted
    # Role set only -> the role's set.
    assert resolve({"version": 1, "roles": {"viewer": ["ne:1"]}}).ne_ids == frozenset({1})
    # Principal set only -> the principal's set. A literal union with "all" would return
    # everything here, making a per-principal restriction impossible to express.
    assert resolve({"version": 1, "principals": {"user:7": ["ne:3"]}}).ne_ids == frozenset({3})
    # Both set -> the union (the "additionally sees the lab range" case).
    assert resolve(
        {"version": 1, "roles": {"viewer": ["ne:1"]}, "principals": {"user:7": ["ne:3"]}}
    ).ne_ids == frozenset({1, 3})


def test_per_principal_capability_restriction_never_widens() -> None:
    """The P1 layer is an intersection too, so it can only take away."""
    policy = rbac.parse_capability_policy(
        json.dumps(
            {
                "version": 1,
                "roles": {"editor": sorted(rbac.ceiling("editor"))},
                "principals": {"user:7": ["stats.read", "users.manage", "audit.read"]},
            }
        )
    )
    resolved = rbac.resolve_capabilities("editor", "user:7", policy)
    assert resolved == frozenset({"stats.read"})  # the admin capabilities named are inert
    # A different principal of the same role is unaffected by user:7's restriction.
    assert rbac.resolve_capabilities("editor", "user:8", policy) == rbac.ceiling("editor")


# --- malformed-policy handling, exhaustively -----------------------------------------------
#
# These are the branches that run when something has already gone wrong, so they are the ones
# least likely to be exercised by accident and the most costly to get wrong: a parser that raised
# on the authorization path would turn a bad row into an outage, and one that silently returned an
# empty policy would look like "no policy" and quietly fall back to the ceiling without a warning.


@pytest.mark.parametrize(
    ("document", "fragment"),
    [
        ("not json at all", "not valid JSON"),
        ("[1, 2, 3]", "not an object"),
        ('"a string"', "not an object"),
        ("null", "not an object"),
        ('{"version": 2}', "unsupported capability policy version"),
        ('{"version": "1"}', "unsupported capability policy version"),
        ('{"version": 1, "roles": []}', "must map a subject to a list"),
        ('{"version": 1, "roles": {"viewer": "stats.read"}}', "must map a subject to a list"),
        ('{"version": 1, "roles": {"viewer": [1, 2]}}', "must map a subject to a list"),
        ('{"version": 1, "principals": 7}', "must map a subject to a list"),
        ('{"version": 1, "principals": {"user:1": {"a": 1}}}', "must map a subject to a list"),
    ],
)
def test_malformed_capability_documents_never_raise_and_fall_back(
    document: str, fragment: str
) -> None:
    policy = rbac.parse_capability_policy(document)
    assert policy.malformed and fragment in policy.reason
    # The fail-safe: every role keeps exactly its compiled ceiling — nobody gains, nobody is
    # locked out, and the reason is operator-facing text rather than policy content.
    for role in rbac.ROLE_RANK:
        assert rbac.resolve_capabilities(role, "user:1", policy) == rbac.ceiling(role)
    assert rbac.capability_policy_errors(policy) == [policy.reason]


@pytest.mark.parametrize(
    ("document", "fragment"),
    [
        ("{oops", "not valid JSON"),
        ("42", "not an object"),
        ('{"version": 9}', "unsupported scope policy version"),
        ('{"version": 1, "roles": "everything"}', "must map a subject to a list"),
        ('{"version": 1, "roles": {"viewer": [7]}}', "must map a subject to a list"),
        ('{"version": 1, "principals": {"user:1": "10.0.0.0/8"}}', "must map a subject to a list"),
    ],
)
def test_malformed_scope_documents_never_raise_and_deny(document: str, fragment: str) -> None:
    policy = shaping.parse_scope_policy(document)
    assert policy.malformed and fragment in policy.reason
    # The opposite fail-safe: viewer/editor see nothing, admin is untouched.
    for role in ("viewer", "editor"):
        resolved = shaping.visible_nes(role, "user:1", policy, NES)
        assert not resolved.unrestricted and resolved.ne_ids == frozenset()
    assert shaping.visible_nes("admin", "user:1", policy, NES).unrestricted
    assert shaping.scope_policy_errors(policy) == [policy.reason]


def test_write_time_validation_names_the_problem_without_being_the_control() -> None:
    """`capability_policy_errors` is a usability affordance; the intersection is the control."""
    policy = rbac.parse_capability_policy(
        json.dumps(
            {
                "version": 1,
                "roles": {"viewer": ["users.manage", "not.a.capability"], "wizard": ["self.read"]},
                "principals": {"alice": ["self.read"], "user:1": ["nope.nope"]},
            }
        )
    )
    problems = rbac.capability_policy_errors(policy)
    joined = " | ".join(problems)
    assert "above the 'viewer' ceiling" in joined  # named, and inert either way
    assert "unknown capability 'not.a.capability'" in joined
    assert "unknown role 'wizard'" in joined
    assert "principal 'alice' must be" in joined
    assert "unknown capability 'nope.nope'" in joined
    # And the policy that produced all those complaints still cannot escalate anyone.
    for role in rbac.ROLE_RANK:
        assert rbac.resolve_capabilities(role, "user:1", policy) <= rbac.ceiling(role)


def test_scope_write_time_validation_flags_admin_and_bad_principals() -> None:
    policy = shaping.parse_scope_policy(
        json.dumps(
            {"version": 1, "roles": {"admin": [], "wizard": []}, "principals": {"bob": ["ne:1"]}}
        )
    )
    joined = " | ".join(shaping.scope_policy_errors(policy))
    assert "role 'admin' cannot be scoped" in joined
    assert "unknown role 'wizard'" in joined
    assert "principal 'bob' must be" in joined


def test_a_well_formed_policy_reports_no_problems() -> None:
    good = rbac.parse_capability_policy(
        json.dumps({"version": 1, "roles": {"viewer": ["stats.read"]}, "principals": {}})
    )
    assert rbac.capability_policy_errors(good) == []
    good_scope = shaping.parse_scope_policy(
        json.dumps({"version": 1, "roles": {"viewer": ["10.0.0.0/8"]}})
    )
    assert shaping.scope_policy_errors(good_scope) == []


def test_an_unknown_role_resolves_to_nothing() -> None:
    """Deny-by-default survives governance: a role outside `ROLE_RANK` holds no capability."""
    assert rbac.ceiling("wizard") == frozenset()
    assert rbac.resolve_capabilities("wizard", None, None) == frozenset()
    policy = rbac.parse_capability_policy(
        json.dumps({"version": 1, "roles": {"wizard": sorted(rbac.PERMISSIONS)}})
    )
    assert rbac.resolve_capabilities("wizard", None, policy) == frozenset()


# --- F30 (continued): the live stream re-evaluates, it does not trust its connect-time grant ---
#
# httpx's ASGITransport cannot deliver partial bodies of an infinite response, so — exactly as
# `test_shaping.py::test_sse_stream_graph_is_shaped_for_viewer` already does — the app is driven
# directly and body chunks are collected until enough update events have arrived.


class _StopSSEError(Exception):
    """Break out of the infinite SSE generator once enough update events are captured."""


async def _sse_updates(
    app: object,
    cookie: str,
    *,
    want: int,
    between: Callable[[], Awaitable[None]] | None = None,
) -> list[dict[str, Any]]:
    """Collect `want` SSE update payloads, optionally running `between()` after the first.

    `between` is how a policy is written *while the stream is open*, which is the whole point: a
    connection that keeps serving its connect-time grant is a standing exemption from the policy.
    """
    scope_env: dict[str, Any] = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "path": "/api/events",
        "raw_path": b"/api/events",
        "query_string": b"",
        "headers": [
            (b"cookie", f"{auth.COOKIE_NAME}={cookie}".encode()),
            (b"host", b"testserver"),
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "scheme": "http",
    }
    updates: list[dict[str, Any]] = []
    buffer = b""
    sent_request = False
    never = asyncio.Event()

    async def receive() -> dict[str, Any]:
        nonlocal sent_request
        if not sent_request:
            sent_request = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await never.wait()  # a real client keeps the connection open
        return {"type": "http.disconnect"}  # pragma: no cover

    async def send(message: dict[str, Any]) -> None:
        nonlocal buffer
        if message["type"] != "http.response.body":
            return
        buffer += message.get("body", b"")
        while b"\n\n" in buffer:
            block, buffer = buffer.split(b"\n\n", 1)
            for line in block.decode().splitlines():
                if line.startswith("data: "):
                    updates.append(json.loads(line[6:]))
                    if len(updates) == 1 and between is not None:
                        await between()
                    if len(updates) >= want:
                        raise _StopSSEError

    with contextlib.suppress(_StopSSEError, TimeoutError):
        await asyncio.wait_for(app(scope_env, receive, send), timeout=15.0)  # type: ignore[operator]
    return updates


async def test_f30_sse_reevaluates_scope_on_every_event(store: Store) -> None:
    """A stream opened *before* a scope existed must not keep pushing unfiltered snapshots.

    The security dependency runs once, at connect time. Without per-event re-resolution a
    long-lived SSE connection would be a standing exemption from the policy — the one place a
    perimeter change could go unnoticed for as long as the browser stayed open.
    """
    engine, queue, app = await authutil.make_env(store)
    await util.drive(engine, queue, util.fixture_events("fiber_cut.json", BASE))
    admin = await authutil.client_as(app, "admin")
    viewer = await authutil.client_as(app, "viewer")
    cookie = viewer.cookies.get(auth.COOKIE_NAME)
    await viewer.aclose()
    assert cookie is not None
    try:
        entities = (await admin.get("/api/entities")).json()
        assert len(entities) > 1
        one_ip = entities[0]["ip"]
        hidden_ips = {e["ip"] for e in entities[1:]}

        async def apply_scope() -> None:
            resp = await _write_policy(
                admin, "scope", {"version": 1, "roles": {"viewer": [one_ip]}}
            )
            assert resp.status_code == 200, resp.text

        updates = await _sse_updates(app, cookie, want=2, between=apply_scope)
        assert len(updates) == 2, "expected an update before and after the policy write"
        before, after = updates
        assert len(before["graph"]["nodes"]) > len(after["graph"]["nodes"])
        blob = json.dumps(after)
        for hidden in hidden_ips:
            assert hidden not in blob, f"SSE leaked out-of-scope address {hidden}"
    finally:
        await admin.aclose()


async def test_f30_sse_ends_when_the_streaming_capability_is_revoked(store: Store) -> None:
    """Revoking `events.stream` mid-connection ends the stream, not serving a stale grant."""
    engine, queue, app = await authutil.make_env(store)
    await util.drive(engine, queue, util.fixture_events("fiber_cut.json", BASE))
    admin = await authutil.client_as(app, "admin")
    viewer = await authutil.client_as(app, "viewer")
    cookie = viewer.cookies.get(auth.COOKIE_NAME)
    await viewer.aclose()
    assert cookie is not None
    try:

        async def revoke() -> None:
            resp = await _write_policy(
                admin, "rbac", {"version": 1, "roles": {"viewer": ["self.read", "stats.read"]}}
            )
            assert resp.status_code == 200, resp.text

        # Ask for two updates; only the first can arrive, because the generator returns as soon
        # as the re-resolved capability set no longer contains `events.stream`.
        updates = await _sse_updates(app, cookie, want=2, between=revoke)
        assert len(updates) == 1, "the stream kept emitting after the capability was revoked"
    finally:
        await admin.aclose()
