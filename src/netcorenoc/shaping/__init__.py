"""Field shaping and resource scoping: the two presentation-authorization axes, and their consumer.

Route authorization (`rbac.py`) decides *which endpoints* a role may call. This package decides
*which fields within a response* a role may see, and *which rows* a principal may see at all. It is
the single place that shapes response bodies by role, never scattered ``if role ==`` checks.

**v0.7.4 — three modules, not two** (DECISIONS #95). `MODULE-ARCHITECTURE.md` §10.2 recorded the
seam as two axes; the AST showed three parts, and §10.2 is superseded in place:

* :mod:`~netcorenoc.shaping.fields` — **which fields**. A viewer running the read-only operations
  view does not need the raw source IP of every monitored element, the community-grouping tag, or
  the source IP a session connected from. Such fields are **coarsened** (an IP → its network) or
  **dropped** for roles below a declared minimum.
* :mod:`~netcorenoc.shaping.scope` — **which rows**. The stored, admin-managed visibility policy,
  and :func:`~netcorenoc.shaping.scope.visible_nes`, the one decision site. The F35 invariant lives
  there, with the code that depends on it.
* :mod:`~netcorenoc.shaping.project` — **applying a resolved scope to a response body**. Not a
  third *axis* but the **consumer** of the other two: each function takes a `Scope` produced by the
  scope axis and returns a body, which is the field axis's subject. Forcing it into either side
  would have been arbitrary, which is precisely what a two-way framing makes it look necessary.

The engine, the store, and the audit log always keep full fidelity — shaping is presentation-only.
New endpoints that return any protected field MUST pass their body through :func:`shape` (or a
helper here); a field with no rule is emitted unchanged, so add a rule when you add such a field.

⚠ **Scoping is a presentation control and is NOT tenant isolation.** Correlation still learns
across every NE, and a situation may still form across a boundary a principal cannot see — it is
then redacted, not prevented. See `docs/architecture/DESIGN.md` (v0.7.0) and `SCOPE-0.7.md`.

Every name below resolved from `netcorenoc.shaping` before v0.7.4 and still does: the enclosing
module changed, the code did not.
"""

from __future__ import annotations

# Private helpers are re-exported too, in the PEP 484 `as` form that marks an explicit re-export.
# Directive 8 is "every symbol in Phase 0's inventory keeps resolving from its original module
# path", and the honest reading of that is every module-level name `shaping.py` had — not only the
# thirteen the tree happens to reach today. Nothing outside this package uses them; if that stays
# true they cost one line each, and if it stops being true nobody has to notice.
from netcorenoc.shaping.fields import (
    _COARSEN as _COARSEN,
)
from netcorenoc.shaping.fields import (
    _DROP as _DROP,
)
from netcorenoc.shaping.fields import (
    FIELD_RULES,
    coarsen_ip,
    shape,
)
from netcorenoc.shaping.fields import (
    _allowed as _allowed,
)
from netcorenoc.shaping.fields import (
    _rank as _rank,
)
from netcorenoc.shaping.project import (
    _as_int as _as_int,
)
from netcorenoc.shaping.project import (
    filter_rows,
    project_graph,
    project_situation_detail,
)
from netcorenoc.shaping.scope import (
    _ADDRESS_CHARS as _ADDRESS_CHARS,
)
from netcorenoc.shaping.scope import (
    _GLOB_CHARS as _GLOB_CHARS,
)
from netcorenoc.shaping.scope import (
    DENY_ALL,
    UNRESTRICTED,
    Scope,
    ScopePolicy,
    is_scopable,
    parse_scope_policy,
    scope_policy_errors,
    visible_nes,
)
from netcorenoc.shaping.scope import (
    _can_never_match as _can_never_match,
)
from netcorenoc.shaping.scope import (
    _layers as _layers,
)
from netcorenoc.shaping.scope import (
    _matches as _matches,
)
from netcorenoc.shaping.scope import (
    _selector_lists as _selector_lists,
)

__all__ = [
    "DENY_ALL",
    "FIELD_RULES",
    "UNRESTRICTED",
    "Scope",
    "ScopePolicy",
    "coarsen_ip",
    "filter_rows",
    "is_scopable",
    "parse_scope_policy",
    "project_graph",
    "project_situation_detail",
    "scope_policy_errors",
    "shape",
    "visible_nes",
]
