"""Role-based access control: the single source of truth for authorization.

**Two modules and a re-export, and the re-export is load-bearing.**

* :mod:`~netcorenoc.rbac.tables` — the compiled authority: `ROLE_RANK`, `PERMISSIONS`,
  `ROUTE_PERMISSIONS`, `PUBLIC_ROUTES`, `ROUTE_SCOPE`, `AUDITED_DENIED_PERMISSIONS`,
  `RECOVERY_CAPABILITIES`, `_CEILINGS`, **all of their prose**, and the three import-time asserts
  that constrain them.
* :mod:`~netcorenoc.rbac.policy` — the decisions computed from those tables: `ceiling`,
  `role_allows`, `permission_for`, `CapabilityPolicy`, `parse_capability_policy`,
  `resolve_capabilities`, `capability_policy_errors`.

**Every name below is re-exported by IDENTITY, never by copy** (v0.7.4, DECISIONS #96). This is not
a style note. Writing::

    PERMISSIONS = dict(tables.PERMISSIONS)      # WRONG

would leave every existing test green — equality holds at import — while creating exactly the
second source of authority this package exists to prevent. The two objects then drift the first
time anything mutates or shadows one, and `tests/test_declaration.py` already mutates
`rbac.ROUTE_PERMISSIONS` in a fixture, so that is not hypothetical.

Two tests hold the line, and both were shown to fail against a deliberately-copying `__init__.py`
before they were accepted:

* ``test_the_tables_are_re_exported_by_identity_not_by_copy`` — ``rbac.PERMISSIONS is
  rbac.tables.PERMISSIONS``, and the same for the other seven tables;
* ``test_no_module_but_tables_binds_an_authorization_table`` — no module under `rbac/` other than
  `tables.py` binds any of those names at module level. A local fallback or a shadowing definition
  is the same defect wearing a different shape.

The split changed where the code lives and nothing else: `netcorenoc.rbac` exports exactly what it
exported at v0.7.3, and no consumer learns that the tables and the resolver are now in different
files.
"""

from __future__ import annotations

from netcorenoc.crosscutting.rbac.policy import (
    CapabilityPolicy,
    capability_policy_errors,
    ceiling,
    parse_capability_policy,
    permission_for,
    resolve_capabilities,
    role_allows,
)
from netcorenoc.crosscutting.rbac.policy import _subject_sets as _subject_sets
from netcorenoc.crosscutting.rbac.tables import _CEILINGS as _CEILINGS
from netcorenoc.crosscutting.rbac.tables import (
    AUDITED_DENIED_PERMISSIONS,
    PERMISSIONS,
    PUBLIC_ROUTES,
    RECOVERY_CAPABILITIES,
    ROLE_RANK,
    ROUTE_PERMISSIONS,
    ROUTE_SCOPE,
)

__all__ = [
    "AUDITED_DENIED_PERMISSIONS",
    "PERMISSIONS",
    "PUBLIC_ROUTES",
    "RECOVERY_CAPABILITIES",
    "ROLE_RANK",
    "ROUTE_PERMISSIONS",
    "ROUTE_SCOPE",
    "CapabilityPolicy",
    "capability_policy_errors",
    "ceiling",
    "parse_capability_policy",
    "permission_for",
    "resolve_capabilities",
    "role_allows",
]
