"""Role-based access control: the single source of truth for authorization.

`PERMISSIONS` maps each capability to the minimum role that holds it; `ROUTE_PERMISSIONS`
maps each registered ``/api`` route (method, templated path) to the capability it requires.
Both the FastAPI security dependency (``api.py``) and the Phase-4 authorization-matrix and
fail-closed tests read these tables — there is never a second copy. A route absent from
`ROUTE_PERMISSIONS` (and not in `PUBLIC_ROUTES`) fails closed at runtime and fails CI.

**v0.7.0 — the compiled map is now explicitly the CEILING.** An admin may store a policy that
*narrows* what a role or an individual principal holds, and :func:`resolve_capabilities` is the one
function that computes the answer:

    resolved(principal) = ceiling(role) ∩ granted(role) ∩ granted(principal)

The compiled `PERMISSIONS` map is the **first operand**, and an intersection cannot exceed its
first operand. A stored policy naming a capability above a role's ceiling is therefore **inert** —
not "rejected", *inert* — however it reached the table: through the API, through a future second
write path, through a bad migration, or through ``sqlite3`` on a stolen or restored database file.
That is what makes escalation impossible by construction rather than forbidden by a check that only
guards the paths it happens to sit on (DECISIONS #53). The API does reject an above-ceiling write
with a 400, but that is a usability affordance so an admin learns immediately — it is not the
control.

Nothing here is cached on a session: the resolved set is computed per request from the live policy,
so a change lands on the next request without a restart.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

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
    # v0.7.0: governance. Who may do what, and who may see which NEs, is itself admin-only —
    # `config`-class, no delegation. Under `ceiling ∩ policy` that is structural rather than a
    # convention: no stored policy can move an admin-ceiling capability down to editor or viewer.
    "rbac.read": "admin",  # view the capability policy and each subject's resolved set
    "rbac.write": "admin",  # set, roll back, or clear the capability policy
    "scope.read": "admin",  # view the scoping policy and a principal's resolved NE set
    "scope.write": "admin",  # set, roll back, or clear the scoping policy
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
    ("GET", "/api/rbac"): "rbac.read",
    ("POST", "/api/rbac"): "rbac.write",
    ("GET", "/api/scope"): "scope.read",
    ("POST", "/api/scope"): "scope.write",
    ("GET", "/api/quarantine"): "quarantine.read",
    ("GET", "/api/audit"): "audit.read",
    ("GET", "/api/audit/export"): "audit.export",
    ("POST", "/api/audit/prune"): "audit.prune",
}

# The only /api routes reachable without a resolved identity.
PUBLIC_ROUTES: frozenset[tuple[str, str]] = frozenset({("POST", "/api/login")})

# Route (METHOD, templated path) -> its **visibility-scope posture**. v0.7.2.
#
# F34 existed because a route's scope posture was expressed *nowhere at all*: three editor write
# routes simply did not have one, and no table, test or reviewer could notice the omission. This
# is that missing declaration. It is **descriptive** in v0.7.2 — it records what each route already
# does after v0.7.1 — and `tests/test_declaration.py` asserts every entry against the route's
# observed behaviour. Making the perimeter *inject* the check from this table is a ROADMAP line,
# because injection changes control flow and control flow is behaviour (DECISIONS #80).
#
#   "scoped"      the response depends on the caller's resolved visibility scope: the route
#                 resolves scope and either filters what it returns or denies an out-of-scope
#                 target through the same 404 a nonexistent one would take (DECISIONS #60).
#   "unscoped"    the response does NOT depend on the caller's scope: a scoped and an unscoped
#                 caller of the same role receive the same body. Every entry carries its reason.
#   "admin_only"  the capability's minimum role is `admin`, and **admin is never scoped**
#                 (DECISIONS #58), so the question does not arise. This is a *derived* claim, not
#                 a second authority: the assertion below re-derives it from `PERMISSIONS` in both
#                 directions at import, so the two tables cannot disagree.
#
# `PUBLIC_ROUTES` is exempt from this table exactly as it is from `ROUTE_PERMISSIONS`, and the
# registration gate says so by consulting it rather than by finding nothing here.
ROUTE_SCOPE: dict[tuple[str, str], Literal["scoped", "unscoped", "admin_only"]] = {
    # Acts on the caller's own session; references no network element.
    ("POST", "/api/logout"): "unscoped",
    # Reports the caller's own scope summary (scoped?, ne_count), so the body follows the policy.
    ("GET", "/api/me"): "scoped",
    # Acts on the caller's own password; references no network element.
    ("POST", "/api/password"): "unscoped",
    ("GET", "/api/stats"): "scoped",
    ("GET", "/api/graph"): "scoped",
    # An alarm class is a *kind of trap*, not a network element, and the table carries no NE
    # reference. The count that would leak — "a device you cannot see just emitted a new trap
    # type" — is `stats.classes`, and that one is scoped.
    ("GET", "/api/classes"): "unscoped",
    ("GET", "/api/situations"): "scoped",
    ("GET", "/api/situations/{sid}"): "scoped",
    ("GET", "/api/timeline"): "scoped",
    ("GET", "/api/events"): "scoped",
    ("GET", "/api/entities"): "scoped",
    ("GET", "/api/entities/{ne_id}"): "scoped",
    # Learned state fields are keyed on (class, varbind OID) — a property of a trap type, not of
    # any NE. Same reasoning as `/api/classes`.
    ("GET", "/api/state-clears"): "unscoped",
    ("POST", "/api/entities/{ne_id}/reset"): "admin_only",
    ("POST", "/api/profiles/{ne_id}/reset"): "admin_only",
    ("POST", "/api/situations/{sid}/feedback"): "scoped",
    ("POST", "/api/labels"): "scoped",
    ("POST", "/api/situations/{sid}/close"): "scoped",
    ("GET", "/api/users"): "admin_only",
    ("POST", "/api/users"): "admin_only",
    ("DELETE", "/api/users/{uid}"): "admin_only",
    ("POST", "/api/users/{uid}/role"): "admin_only",
    ("GET", "/api/tokens"): "admin_only",
    ("POST", "/api/tokens"): "admin_only",
    ("DELETE", "/api/tokens/{tid}"): "admin_only",
    ("GET", "/api/config"): "admin_only",
    ("POST", "/api/config"): "admin_only",
    # The five parameters of the active scorer and its immutable history. They *explain* every
    # grouping decision and name no network element, so every authenticated role reads the same
    # numbers (SCOPE-0.6 §2).
    ("GET", "/api/scorer"): "unscoped",
    ("POST", "/api/scorer/preview"): "admin_only",
    ("POST", "/api/scorer"): "admin_only",
    ("POST", "/api/scorer/rollback"): "admin_only",
    ("GET", "/api/rbac"): "admin_only",
    ("POST", "/api/rbac"): "admin_only",
    ("GET", "/api/scope"): "admin_only",
    ("POST", "/api/scope"): "admin_only",
    ("GET", "/api/quarantine"): "admin_only",
    ("GET", "/api/audit"): "admin_only",
    ("GET", "/api/audit/export"): "admin_only",
    ("POST", "/api/audit/prune"): "admin_only",
}

assert set(ROUTE_SCOPE) == set(ROUTE_PERMISSIONS), (
    "ROUTE_SCOPE and ROUTE_PERMISSIONS must declare exactly the same routes: "
    f"only in ROUTE_SCOPE {sorted(set(ROUTE_SCOPE) - set(ROUTE_PERMISSIONS))}, "
    f"only in ROUTE_PERMISSIONS {sorted(set(ROUTE_PERMISSIONS) - set(ROUTE_SCOPE))}"
)
assert not [
    route
    for route, posture in ROUTE_SCOPE.items()
    if (posture == "admin_only") != (PERMISSIONS[ROUTE_PERMISSIONS[route]] == "admin")
], (
    "an `admin_only` posture is derived from PERMISSIONS, never asserted independently: a route "
    "is `admin_only` if and only if its capability's minimum role is `admin` (DECISIONS #58, #80)"
)

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
        # v0.7.0 (F27): a denied attempt to read or rewrite the perimeter itself is an attempted
        # privilege change — worth a row even though it failed, and for the same reason
        # `config.read` is here: knowing the shape of the policy is knowing the shape of the
        # defences.
        "rbac.read",
        "rbac.write",
        "scope.read",
        "scope.write",
    }
)

# v0.7.0 (DECISIONS #64): capabilities an admin can never lose to a stored policy.
#
# `ceiling ∩ policy` otherwise lets a *well-formed* policy remove `rbac.write` from the `admin`
# role, leaving no authenticated path to repair the perimeter — a hard lockout that the
# malformed-policy fallback would never catch, because the policy is not malformed. These are
# unioned back for admin inside `resolve_capabilities`, and because the set is a **subset of
# ceiling("admin")** the union cannot leave the ceiling: `resolved ⊆ ceiling(role)` still holds for
# every input, which is the invariant the property test asserts.
#
# Deliberately tiny, and the *recovery* surface only: governance may still take `users.manage`,
# `audit.read`, `config.write` or `scorer.write` away from an admin. It simply may not brick the
# appliance.
RECOVERY_CAPABILITIES: frozenset[str] = frozenset(
    {"self.read", "rbac.read", "rbac.write", "scope.read", "scope.write"}
)

# Per-role ceilings, computed once from the compiled map. This is exactly v0.6.0's `role_allows`
# expressed as a set, which is what lets the resolver be an intersection.
_CEILINGS: dict[str, frozenset[str]] = {
    role: frozenset(
        capability
        for capability, minimum in PERMISSIONS.items()
        if ROLE_RANK[role] >= ROLE_RANK[minimum]
    )
    for role in ROLE_RANK
}

assert _CEILINGS["admin"] >= RECOVERY_CAPABILITIES, (
    "RECOVERY_CAPABILITIES must be a subset of the admin ceiling, or unioning it back in "
    "resolve_capabilities() would breach the ceiling invariant (DECISIONS #64)"
)


def ceiling(role: str) -> frozenset[str]:
    """The maximum capability set `role` may **ever** hold. An unknown role holds nothing.

    This is the compiled authority: the first operand of every resolution, and the bound no stored
    policy can exceed.
    """
    return _CEILINGS.get(role, frozenset())


def role_allows(role: str, permission: str) -> bool:
    """True if `role`'s **ceiling** contains `permission` (unknown role or permission ⇒ deny).

    v0.6.0 semantics, unchanged and now expressed through :func:`ceiling` so the rank comparison
    lives in exactly one place. This answers "may this role *ever* hold it?", not "does this
    principal hold it right now?" — the latter is :func:`resolve_capabilities`, and callers making
    an authorization decision must use that one.
    """
    return permission in ceiling(role)


def permission_for(method: str, path: str) -> str | None:
    """Required capability for a route, or None if the route is not in the map."""
    return ROUTE_PERMISSIONS.get((method, path))


# -- the stored capability policy (v0.7.0) -------------------------------------------------


@dataclass(frozen=True)
class CapabilityPolicy:
    """A parsed capability policy: per-role and per-principal *restrictions* within the ceiling.

    An **absent** key means the subject "expresses no opinion" ⇒ no intersection ⇒ the ceiling ⇒
    parity. A key present with an **empty** set means "allow nothing" ⇒ intersect with ∅. The two
    are different statements and are stored differently (DECISIONS #54): the first is what an
    un-configured appliance looks like, the second is a deliberate choice.

    ``malformed`` marks a document that could not be understood. The resolver then falls back to
    the compiled ceiling — the shipped v0.6.0 baseline — rather than denying, because denying would
    lock out the admin who has to repair it (DECISIONS #55). ``reason`` is operator-facing text for
    the warning; it never contains policy content.
    """

    roles: dict[str, frozenset[str]]
    principals: dict[str, frozenset[str]]
    malformed: bool = False
    reason: str = ""


def _subject_sets(raw: Any) -> dict[str, frozenset[str]] | None:
    """``{subject: [capability, ...]}`` → parsed, or None if the shape is wrong."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        return None
    out: dict[str, frozenset[str]] = {}
    for subject, capabilities in raw.items():
        if not isinstance(subject, str) or not isinstance(capabilities, list):
            return None
        if not all(isinstance(c, str) for c in capabilities):
            return None
        out[subject] = frozenset(capabilities)
    return out


def parse_capability_policy(document: str) -> CapabilityPolicy:
    """Parse a stored capability document. **Never raises** — a bad document is `malformed`.

    Refusing to raise is the point: this runs on the authorization path, and an exception there
    would be an outage rather than a degradation. Anything unparseable, wrongly shaped, or of an
    unsupported version becomes a policy the resolver knows to ignore in favour of the ceiling.
    """
    try:
        parsed = json.loads(document)
    except (ValueError, TypeError):
        return CapabilityPolicy(
            {}, {}, malformed=True, reason="capability policy is not valid JSON"
        )
    if not isinstance(parsed, dict):
        return CapabilityPolicy({}, {}, malformed=True, reason="capability policy is not an object")
    version = parsed.get("version", 1)
    if version != 1:
        return CapabilityPolicy(
            {}, {}, malformed=True, reason=f"unsupported capability policy version {version!r}"
        )
    roles = _subject_sets(parsed.get("roles"))
    principals = _subject_sets(parsed.get("principals"))
    if roles is None or principals is None:
        return CapabilityPolicy(
            {},
            {},
            malformed=True,
            reason="capability policy roles/principals must map a subject to a list of strings",
        )
    return CapabilityPolicy(roles, principals)


def resolve_capabilities(
    role: str, principal_ref: str | None, policy: CapabilityPolicy | None
) -> frozenset[str]:
    """**THE** capability decision. `ceiling(role) ∩ granted(role) ∩ granted(principal)`.

    Every caller — the ``api.py`` security dependency, the ``/api/me`` affordance gate, and the
    generated authorization matrix — reads this one answer; there is no second decision site
    (F28).

    The guarantee, for **every** input including hostile ones::

        resolve_capabilities(role, ref, policy) ⊆ ceiling(role)

    holds because an intersection cannot exceed its first operand, and the one union is with
    `RECOVERY_CAPABILITIES`, itself a compiled subset of the admin ceiling (asserted at import).
    A policy row naming an above-ceiling capability changes nothing at all.

    `policy is None` (no policy stored) and `policy.malformed` both resolve to the ceiling — the
    shipped safe baseline, i.e. exactly v0.6.0 (DECISIONS #54, #55).
    """
    capabilities = ceiling(role)
    if policy is not None and not policy.malformed:
        granted_role = policy.roles.get(role)
        if granted_role is not None:
            capabilities &= granted_role
        if principal_ref is not None:
            granted_principal = policy.principals.get(principal_ref)
            if granted_principal is not None:
                capabilities &= granted_principal
    if role == "admin":
        capabilities |= RECOVERY_CAPABILITIES
    return capabilities


def capability_policy_errors(policy: CapabilityPolicy) -> list[str]:
    """Operator-facing problems with a policy an admin is *writing*. **Usability, not security.**

    The security guarantee is the intersection in :func:`resolve_capabilities`, which makes every
    problem below inert. Reporting them at write time simply means an admin finds out immediately
    that a line will do nothing, instead of discovering it later from behaviour.
    """
    problems: list[str] = []
    if policy.malformed:
        return [policy.reason or "policy is malformed"]
    for role, capabilities in policy.roles.items():
        if role not in ROLE_RANK:
            problems.append(f"unknown role {role!r}")
            continue
        for capability in sorted(capabilities):
            if capability not in PERMISSIONS:
                problems.append(f"unknown capability {capability!r} for role {role!r}")
            elif capability not in ceiling(role):
                problems.append(
                    f"capability {capability!r} is above the {role!r} ceiling and would have no "
                    f"effect (it requires {PERMISSIONS[capability]!r})"
                )
    for subject, capabilities in policy.principals.items():
        if not subject.startswith(("user:", "token:")):
            problems.append(f"principal {subject!r} must be 'user:<id>' or 'token:<id>'")
        for capability in sorted(capabilities):
            if capability not in PERMISSIONS:
                problems.append(f"unknown capability {capability!r} for principal {subject!r}")
    return problems
