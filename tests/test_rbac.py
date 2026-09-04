"""Phase 4.1 — authorization matrix and fail-closed enforcement.

Expectations are generated from `rbac.py` (the single source), so the test cannot drift
from the enforcement it checks: every registered `/api` route is exercised for every
role, anonymous included, and the outcome must match `role_allows`. A route missing from
the map fails the suite (deny-by-default), and 403 is proven to precede 404 (no oracle).
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from netcorenoc import api
from netcorenoc.crosscutting import audit, rbac
from netcorenoc.store import Store

import authutil

# Concrete substitutions for templated path params (ids chosen to not exist).
PARAMS = {"{sid}": "999999", "{uid}": "999999", "{tid}": "999999", "{aid}": "999999"}
# Minimal valid bodies so business validation never masks the authorization outcome.
BODIES: dict[tuple[str, str], dict[str, object]] = {
    ("POST", "/api/password"): {"old_password": "x" * 12, "new_password": "y" * 12},
    ("POST", "/api/situations/{sid}/feedback"): {"verdict": "confirm"},
    ("POST", "/api/labels"): {"kind": "device", "id": 1, "label": "x"},
    # v0.16.0. The five operator gestures. Ids that do not exist, and a confidence above the
    # registered floor: the probe must reach the authorization decision and stop there, so the
    # bodies are valid in shape and impossible in fact.
    ("POST", "/api/situations/{sid}/move"): {
        "alarm_id": 999999,
        "to_situation_id": 999998,
        "confidence": 0.9,
    },
    ("POST", "/api/situations/{sid}/merge"): {"from_situation_id": 999998, "confidence": 0.9},
    ("POST", "/api/situations/{sid}/split"): {"alarm_ids": [999999], "confidence": 0.9},
    ("POST", "/api/situations/{sid}/name"): {"name": "probe"},
    ("POST", "/api/alarms/{aid}/clear"): {},
    ("POST", "/api/users"): {"username": "probe", "password": "probe-pw-1234", "role": "viewer"},
    ("POST", "/api/users/{uid}/role"): {"role": "viewer"},
    ("POST", "/api/tokens"): {"name": "probe", "role": "viewer"},
    ("POST", "/api/config"): {"allowlist": "", "retention_days": 7},
    # v0.8.0. `preview` is left at its default (True) deliberately: the authorization probe
    # must not reach the destructive branch, and the default IS the safety property.
    ("POST", "/api/dataset/retention"): {
        "sink_days": 21,
        "sink_rows": 1000,
        "training_days": 365,
        "audit_days": 730,
    },
}


def _concrete(path: str) -> str:
    for token, value in PARAMS.items():
        path = path.replace(token, value)
    return path


async def _status(client: httpx.AsyncClient, method: str, path: str, key: tuple[str, str]) -> int:
    if path == "/api/events":
        # The SSE stream never ends; the security dependency raises 401/403 *before*
        # streaming, so an authorized caller hangs (no socket timeout under ASGITransport).
        # A timeout therefore means "authorization passed" — report it as 200.
        try:
            resp = await asyncio.wait_for(client.get(_concrete(path)), timeout=0.6)
            return resp.status_code
        except (TimeoutError, httpx.ReadError):
            return 200
    async with client.stream(method, _concrete(path), json=BODIES.get(key)) as resp:
        return resp.status_code


async def test_authorization_matrix(store: Store) -> None:
    _engine, _queue, app = await authutil.make_env(store)
    failures: list[str] = []
    for (method, path), permission in rbac.ROUTE_PERMISSIONS.items():
        key = (method, path)
        for role in (None, "viewer", "editor", "admin"):
            # A fresh login per case: some routes (logout, password) revoke the caller's
            # own session, so clients must not be shared across routes.
            client = await authutil.client_as(app, role)
            try:
                status = await _status(client, method, path, key)
            finally:
                await client.aclose()
            if role is None:
                ok, want = status == 401, "401"
            elif not rbac.role_allows(role, permission):
                ok, want = status == 403, "403"
            else:
                ok, want = status not in (401, 403), "not 401/403"
            if not ok:
                failures.append(f"{method} {path} as {role}: got {status}, want {want}")
    assert not failures, "authorization mismatches:\n" + "\n".join(failures)


async def test_every_api_route_is_in_the_permission_map(store: Store) -> None:
    """Fail-closed: any registered /api route not mapped (and not public) fails CI."""
    _engine, _queue, app = await authutil.make_env(store)
    unmapped = []
    for route in app.routes:  # type: ignore[attr-defined]
        path = getattr(route, "path", "")
        if not path.startswith("/api"):
            continue
        for method in getattr(route, "methods", set()) or set():
            if method in ("HEAD", "OPTIONS"):
                continue
            key = (method, path)
            if key in rbac.PUBLIC_ROUTES:
                continue
            if key not in rbac.ROUTE_PERMISSIONS:
                unmapped.append(f"{method} {path}")
    assert not unmapped, f"unmapped /api routes (fail closed): {unmapped}"


def test_permission_map_only_references_known_permissions() -> None:
    for _route, permission in rbac.ROUTE_PERMISSIONS.items():
        assert permission in rbac.PERMISSIONS, permission


def test_f8_audited_denied_single_source() -> None:
    """F8: the audited-denied set has ONE source of truth (`rbac`); `api.DENIED_ACTION` is only
    its presentation mapping and may never diverge. Each mapped catalog action must be real."""
    assert set(api.DENIED_ACTION) == set(rbac.AUDITED_DENIED_PERMISSIONS)
    for permission, action in api.DENIED_ACTION.items():
        assert permission in rbac.PERMISSIONS, permission
        assert action in audit.ACTIONS, action


def test_f9_config_read_is_its_own_least_privilege_capability() -> None:
    """F9: reading runtime config is gated by `config.read`, not the write capability, and a
    denied read is still audited (the allowlist reveals network-security posture)."""
    assert rbac.permission_for("GET", "/api/config") == "config.read"
    assert rbac.permission_for("POST", "/api/config") == "config.write"
    assert rbac.PERMISSIONS["config.read"] == "admin"  # still least-privilege: admin only
    assert not rbac.role_allows("editor", "config.read")
    assert "config.read" in rbac.AUDITED_DENIED_PERMISSIONS


async def test_403_precedes_404_no_existence_oracle(store: Store) -> None:
    """An under-privileged caller gets 403 for a missing resource (not 404) — no oracle;
    the authorized caller gets 404, proving authorization runs before resource lookup."""
    _engine, _queue, app = await authutil.make_env(store)
    editor = await authutil.client_as(app, "editor")
    admin = await authutil.client_as(app, "admin")
    try:
        # DELETE /api/users/{uid} requires admin. uid 999999 does not exist.
        assert (await editor.delete("/api/users/999999")).status_code == 403
        assert (await admin.delete("/api/users/999999")).status_code == 404
    finally:
        await editor.aclose()
        await admin.aclose()


# --- v0.7.0: the same matrix, with a governance policy active ---------------------------


async def _activate_capability_policy(store: Store, document: dict[str, object]) -> None:
    import json

    text = json.dumps(document, sort_keys=True, separators=(",", ":"))
    async with store.lock:
        policy_id = await store.insert_governance_policy("rbac", text, "h" * 64, None, 0.0, "")
        await store.set_active_governance_policy("rbac", policy_id, None, 0.0)
        await store.commit()


async def test_authorization_matrix_with_a_policy_active(store: Store) -> None:
    """The generated matrix again, but with a stored policy narrowing every role.

    The expectation is regenerated from the **resolver** rather than from `role_allows`, so this
    checks what v0.7.0 actually enforces. The policy takes `situations.read` and `timeline.read`
    away from viewer and editor and strips admin to a small set; every route must follow the
    resolved answer, and no route may become reachable that the ceiling did not already allow.
    """
    _engine, _queue, app = await authutil.make_env(store)
    policy_doc: dict[str, object] = {
        "version": 1,
        "roles": {
            "viewer": ["self.read", "stats.read", "graph.read", "events.stream"],
            "editor": ["self.read", "stats.read", "situations.read", "feedback.write"],
            "admin": ["self.read", "users.manage", "audit.read", "rbac.read", "rbac.write"],
        },
    }
    await _activate_capability_policy(store, policy_doc)
    policy = rbac.parse_capability_policy(
        __import__("json").dumps(policy_doc, sort_keys=True, separators=(",", ":"))
    )

    failures: list[str] = []
    for (method, path), permission in rbac.ROUTE_PERMISSIONS.items():
        key = (method, path)
        for role in ("viewer", "editor", "admin"):
            resolved = rbac.resolve_capabilities(role, None, policy)
            # The ceiling is never exceeded, whatever the policy said.
            assert resolved <= rbac.ceiling(role)
            client = await authutil.client_as(app, role)
            try:
                status = await _status(client, method, path, key)
            finally:
                await client.aclose()
            if permission in resolved:
                ok, want = status not in (401, 403), "not 401/403"
            else:
                ok, want = status == 403, "403"
            if not ok:
                failures.append(f"{method} {path} as {role}: got {status}, want {want}")
    assert not failures, "authorization mismatches under policy:\n" + "\n".join(failures)


async def test_a_policy_never_makes_a_route_reachable_that_the_ceiling_forbids(
    store: Store,
) -> None:
    """The inverse sweep: grant every role every capability and nothing new opens up."""
    _engine, _queue, app = await authutil.make_env(store)
    await _activate_capability_policy(
        store,
        {"version": 1, "roles": {role: sorted(rbac.PERMISSIONS) for role in rbac.ROLE_RANK}},
    )
    failures: list[str] = []
    for (method, path), permission in rbac.ROUTE_PERMISSIONS.items():
        for role in ("viewer", "editor"):
            if rbac.role_allows(role, permission):
                continue  # already allowed by the ceiling; nothing to prove here
            client = await authutil.client_as(app, role)
            try:
                status = await _status(client, method, path, (method, path))
            finally:
                await client.aclose()
            if status != 403:
                failures.append(f"{method} {path} as {role}: got {status}, want 403")
    assert not failures, "a policy opened a route above the ceiling:\n" + "\n".join(failures)


# --- v0.7.4: the split must not create a second source of authority ----------------------

# The eight tables `rbac/tables.py` owns. Re-exporting any of them by **copy** rather than by
# reference would leave every test above green and create exactly the second source of truth this
# package exists to prevent — the two objects diverge the first time anything mutates or shadows
# one, and `tests/test_declaration.py` already mutates `rbac.ROUTE_PERMISSIONS` in a fixture.
AUTHORITY_TABLES = (
    "ROLE_RANK",
    "PERMISSIONS",
    "ROUTE_PERMISSIONS",
    "PUBLIC_ROUTES",
    "ROUTE_SCOPE",
    "AUDITED_DENIED_PERMISSIONS",
    "RECOVERY_CAPABILITIES",
    "_CEILINGS",
)


@pytest.mark.parametrize("name", AUTHORITY_TABLES)
def test_the_tables_are_re_exported_by_identity_not_by_copy(name: str) -> None:
    """**Equality is not enough; require identity** (v0.7.4, DECISIONS #96).

    `rbac.PERMISSIONS == rbac.tables.PERMISSIONS` would hold for a copy too, at import, forever
    green and quietly wrong. Only one object can be the authority, and `is` is the only operator
    that says so.

    Shown to fail against a deliberately-copying `__init__.py` before being accepted — see
    `docs/gates/v0.7.4-phase-4.md` §2.
    """
    from netcorenoc.crosscutting.rbac import tables

    exported = getattr(rbac, name)
    owned = getattr(tables, name)
    assert exported is owned, (
        f"rbac.{name} is not rbac.tables.{name} — it is a copy. A copy is a second source of "
        "authority: the two drift the moment either is mutated or shadowed. Re-export the name, "
        "do not rebuild the container."
    )


def test_no_module_but_tables_binds_an_authorization_table() -> None:
    """The same defect wearing a different shape: a local fallback or a shadowing definition.

    Identity holds against whatever `__init__.py` happens to import. It would still hold if
    `policy.py` defined its own `PERMISSIONS` and used that internally — the tables would agree at
    import and the resolver would read the wrong one. So: **no module under `rbac/` other than
    `tables.py` may bind any of these names at module level.**

    Parsed from the AST rather than grepped, so an assignment inside a function body (a genuine
    local) does not trip it and a module-level one cannot hide behind formatting.
    """
    import ast
    from pathlib import Path

    pkg = Path(rbac.__file__).resolve().parent
    offenders: list[str] = []
    for path in sorted(pkg.glob("*.py")):
        if path.name == "tables.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:  # module level only — nested scopes are not bindings of the table
            targets: list[str] = []
            if isinstance(node, ast.Assign):
                targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                targets = [node.target.id]
            for name in targets:
                if name in AUTHORITY_TABLES:
                    offenders.append(f"{path.name}:{node.lineno} binds {name}")
    assert not offenders, (
        "module(s) under rbac/ binding an authorization table outside tables.py:\n  "
        + "\n  ".join(offenders)
        + "\n\ntables.py is the single source of authority. Import the name; never rebind it."
    )


def test_every_capability_names_the_role_it_was_designed_for() -> None:
    """**The whole capability table, pinned** (v0.9.1's test audit, seeds A1 and A2).

    Until this test, `PERMISSIONS["config.read"] == "admin"` was the *only* role assignment anything
    asserted. The seeded-defect audit moved `feedback.write` and `situation.close` from `editor` to
    `viewer` — granting every read-only account the ability to write labels and close situations —
    and **all 958 tests stayed green** (`docs/gates/v0.9.1-test-audit.md`, seeds A1/A2).

    Nothing downstream noticed because everything downstream is derived: `role_allows`, the
    resolver, the route table and the generated authorization matrix all read `PERMISSIONS` and
    agree with whatever it says. A table that is the source of truth for every derived check needs
    its own assertion, or the derivation is measuring itself.

    This is deliberately a **whole-table** expectation rather than a spot check on the two the audit
    happened to seed. A capability added without a line here fails, which is the point: adding one
    is a decision about least privilege, and it should cost a reviewer's attention.
    """
    assert rbac.PERMISSIONS == {
        # any authenticated principal
        "self.read": "viewer",
        # read / stream (viewer+)
        "stats.read": "viewer",
        "graph.read": "viewer",
        "classes.read": "viewer",
        "situations.read": "viewer",
        "timeline.read": "viewer",
        "events.stream": "viewer",
        "entities.read": "viewer",
        "scorer.read": "viewer",
        # operate (editor+)
        "feedback.write": "editor",
        "label.write": "editor",
        "situation.close": "editor",
        # v0.16.0 (DECISIONS #256): four powers, four capabilities. `feedback.write` records an
        # opinion; these restructure the record, and `ceiling ∩ policy` lets a deployment grant one
        # without the other only because they are separate names.
        "situation.move": "editor",
        "situation.merge": "editor",
        "situation.split": "editor",
        # A zombie clear is a fact about an ALARM, and the capability's name says so.
        "alarm.clear": "editor",
        # administer (admin only)
        "users.manage": "admin",
        "entity.reset": "admin",
        "profile.reset": "admin",
        "tokens.manage": "admin",
        "config.read": "admin",
        "config.write": "admin",
        "quarantine.read": "admin",
        "audit.read": "admin",
        "audit.export": "admin",
        "audit.prune": "admin",
        "scorer.preview": "admin",
        "scorer.write": "admin",
        "promotion.read": "viewer",
        "promotion.write": "admin",
        "rbac.read": "admin",
        "rbac.write": "admin",
        "scope.read": "admin",
        "scope.write": "admin",
    }, (
        "the capability table changed. Every derived check — role_allows, the resolver, the "
        "route table, the authorization matrix — reads this dict and will agree with whatever it "
        "says, so a capability quietly moved to a lower role is invisible everywhere else. If the "
        "change is intended, update this expectation deliberately."
    )
