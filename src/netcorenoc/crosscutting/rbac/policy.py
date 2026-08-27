"""The capability resolver and the stored-policy parser — the decisions, not the tables.

`tables.py` holds the compiled authority. This module computes answers from it and adds nothing to
it: every name it needs is imported, never redefined. That separation is the whole point of the
v0.7.4 split (DECISIONS #96) — the tables have one home, and a reader asking "where is this
decided?" finds one file.

**The guarantee, for every input including hostile ones:**

    resolve_capabilities(role, ref, policy) ⊆ ceiling(role)

holds because an intersection cannot exceed its first operand, and the one union is with
`RECOVERY_CAPABILITIES`, itself a compiled subset of the admin ceiling (asserted at import in
`tables.py`). A stored policy naming a capability above a role's ceiling is therefore **inert** —
not "rejected", *inert* — however it reached the table: through the API, through a future second
write path, through a bad migration, or through ``sqlite3`` on a stolen or restored database file.
That is what makes escalation impossible by construction rather than forbidden by a check that only
guards the paths it happens to sit on (DECISIONS #53).

Nothing here is cached on a session: the resolved set is computed per request from the live policy,
so a change lands on the next request without a restart.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from netcorenoc.crosscutting.rbac.tables import (
    _CEILINGS,
    PERMISSIONS,
    RECOVERY_CAPABILITIES,
    ROLE_RANK,
    ROUTE_PERMISSIONS,
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
