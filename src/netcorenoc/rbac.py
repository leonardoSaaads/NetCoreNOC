"""Role-based access control: the single source of truth for authorization.

`PERMISSIONS` maps each capability to the minimum role that holds it; `ROUTE_PERMISSIONS`
maps each registered ``/api`` route (method, templated path) to the capability it requires.
Both the FastAPI security dependency (``api.py``) and the Phase-4 authorization-matrix and
fail-closed tests read these tables — there is never a second copy. A route absent from
`ROUTE_PERMISSIONS` (and not in `PUBLIC_ROUTES`) fails closed at runtime and fails CI.
"""

from __future__ import annotations

ROLE_RANK: dict[str, int] = {"viewer": 0, "editor": 1, "admin": 2}

# Capability -> minimum role that holds it. viewer ⊆ editor ⊆ admin.
PERMISSIONS: dict[str, str] = {
    # any authenticated principal
    "self.read": "viewer",
    # read / stream (viewer+)
    "stats.read": "viewer",
    "graph.read": "viewer",
    "classes.read": "viewer",
    "situations.read": "viewer",
    "timeline.read": "viewer",
    "events.stream": "viewer",
    "entities.read": "viewer",  # v0.3.0: entity tree + varbind profiler (inspectable)
    # v0.6.0: the active scorer id, its parameters, and the per-term contributions EXPLAIN
    # grouping — they are not a secret, so every authenticated role may read them.
    "scorer.read": "viewer",
    # operate (editor+)
    "feedback.write": "editor",
    "label.write": "editor",
    "situation.close": "editor",
    # administer (admin only)
    "users.manage": "admin",
    "entity.reset": "admin",  # v0.3.0: reset an NE's learned entity key (audited)
    "profile.reset": "admin",  # v0.3.0: wipe an NE's varbind profiler evidence (audited)
    "tokens.manage": "admin",
    "config.read": "admin",  # v0.4.0 (F9): reading runtime config is its own capability…
    "config.write": "admin",  # …distinct from the write that changes it (least privilege)
    "quarantine.read": "admin",
    "audit.read": "admin",
    "audit.export": "admin",
    "audit.prune": "admin",
    # v0.6.0: retuning the link formula is a system-wide logic change, not an operational one.
    # Admin only, with NO editor delegation — deliberately stricter than the "optionally editor"
    # phrasing of the v0.5.0 extensibility draft (SCOPE-0.6 §2, DECISIONS #43).
    "scorer.preview": "admin",  # bounded, read-only what-if over recent alarms
    "scorer.write": "admin",  # append a config, move the active pointer, roll back
}

# Route (METHOD, templated path) -> required capability. THE authorization map.
ROUTE_PERMISSIONS: dict[tuple[str, str], str] = {
    ("POST", "/api/logout"): "self.read",
    ("GET", "/api/me"): "self.read",
    ("POST", "/api/password"): "self.read",
    ("GET", "/api/stats"): "stats.read",
    ("GET", "/api/graph"): "graph.read",
    ("GET", "/api/classes"): "classes.read",
    ("GET", "/api/situations"): "situations.read",
    ("GET", "/api/situations/{sid}"): "situations.read",
    ("GET", "/api/timeline"): "timeline.read",
    ("GET", "/api/events"): "events.stream",
    ("GET", "/api/entities"): "entities.read",
    ("GET", "/api/entities/{ne_id}"): "entities.read",
    ("GET", "/api/state-clears"): "entities.read",
    ("POST", "/api/entities/{ne_id}/reset"): "entity.reset",
    ("POST", "/api/profiles/{ne_id}/reset"): "profile.reset",
    ("POST", "/api/situations/{sid}/feedback"): "feedback.write",
    ("POST", "/api/labels"): "label.write",
    ("POST", "/api/situations/{sid}/close"): "situation.close",
    ("GET", "/api/users"): "users.manage",
    ("POST", "/api/users"): "users.manage",
    ("DELETE", "/api/users/{uid}"): "users.manage",
    ("POST", "/api/users/{uid}/role"): "users.manage",
    ("GET", "/api/tokens"): "tokens.manage",
    ("POST", "/api/tokens"): "tokens.manage",
    ("DELETE", "/api/tokens/{tid}"): "tokens.manage",
    ("GET", "/api/config"): "config.read",
    ("POST", "/api/config"): "config.write",
    ("GET", "/api/scorer"): "scorer.read",
    ("POST", "/api/scorer/preview"): "scorer.preview",
    ("POST", "/api/scorer"): "scorer.write",
    ("POST", "/api/scorer/rollback"): "scorer.write",
    ("GET", "/api/quarantine"): "quarantine.read",
    ("GET", "/api/audit"): "audit.read",
    ("GET", "/api/audit/export"): "audit.export",
    ("POST", "/api/audit/prune"): "audit.prune",
}

# The only /api routes reachable without a resolved identity.
PUBLIC_ROUTES: frozenset[tuple[str, str]] = frozenset({("POST", "/api/login")})

# Sensitive capabilities whose *denied* (403) attempts are still audited. THIS is the single
# source of truth for the audited-denied set: ``api.py`` derives its "should this denial be
# audited?" decision from here (mapping each to a representative catalog action), and
# ``tests/test_rbac.py::test_f8_audited_denied_single_source`` fails CI if the two ever diverge.
AUDITED_DENIED_PERMISSIONS: frozenset[str] = frozenset(
    {
        "quarantine.read",
        "audit.read",
        "audit.export",
        "audit.prune",
        "users.manage",
        "tokens.manage",
        "config.read",  # reading the allowlist reveals network-security posture (F9)
        "config.write",
        # v0.6.0 (F21): a denied attempt to retune or preview the correlation formula is an
        # attempted system-wide logic change — worth a row even though it failed. `scorer.read`
        # is deliberately NOT here: it is viewer+, so a denial only means "unauthenticated".
        "scorer.preview",
        "scorer.write",
    }
)


def role_allows(role: str, permission: str) -> bool:
    """True if `role` holds `permission` (unknown role or permission ⇒ deny)."""
    required = PERMISSIONS.get(permission)
    if required is None or role not in ROLE_RANK:
        return False
    return ROLE_RANK[role] >= ROLE_RANK[required]


def permission_for(method: str, path: str) -> str | None:
    """Required capability for a route, or None if the route is not in the map."""
    return ROUTE_PERMISSIONS.get((method, path))
