"""The HTTP layer: identity, role-based authorization, tamper-evident audit, SSE, static UI.

`api` became a **package** in v0.7.2 (DECISIONS #79). That is an internal change and is invisible
to every caller: this module re-exports every symbol that was previously reachable as
``netcorenoc.api.X``, so ``import netcorenoc.api`` and ``from netcorenoc.api import create_app``
behave exactly as they did when the package was one 1 752-line file.

Where things live — and `MODULE-ARCHITECTURE.md` §3 is the authority:

* :mod:`~netcorenoc.api.perimeter` — **the security boundary**. Read this one first if you are
  reviewing security: CSRF, identity resolution, the bootstrap gate, capability resolution, scope
  resolution, the rate limit, the audit-row helper, the write-transaction boundary, and the
  route-declaration gate, in one file a reviewer can finish in one sitting.
* :mod:`~netcorenoc.api.app` — `create_app` and nothing else: build the perimeter, build the
  context, register the middleware, call each route module's ``register()``.
* :mod:`~netcorenoc.api.context` — `AppContext`, the frozen record every route module receives.
* :mod:`~netcorenoc.api.models` — every pydantic request model.
* ``routes_*`` — the nine route groups, each a single ``register(app, ctx)`` function.
"""

from __future__ import annotations

from netcorenoc.api.app import create_app
from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes, UndeclaredRouteError
from netcorenoc.api.governance_cache import GovernancePolicies
from netcorenoc.api.models import (
    MAX_LABEL_CHARS,
    MAX_NOTE_CHARS,
    ConfigIn,
    FeedbackIn,
    LabelIn,
    LoginIn,
    PasswordIn,
    PolicyIn,
    QuietServer,
    RoleIn,
    ScorerParamsIn,
    ScorerRollbackIn,
    TokenIn,
    UserIn,
)
from netcorenoc.api.perimeter import (
    BOOTSTRAP_ALLOWED,
    CSP,
    DENIED_ACTION,
    MUTATING,
    PREVIEW_RATE_CAPACITY,
    PREVIEW_RATE_REFILL,
    RATE_CAPACITY,
    RATE_REFILL,
    SECURITY_HEADERS,
    Perimeter,
    RateLimiter,
)
from netcorenoc.api.routes.events import SSE_HEARTBEAT_S, SSE_UPDATE_S
from netcorenoc.api.routes.governance import MAX_POLICY_HISTORY
from netcorenoc.api.routes.scorer import MAX_SCORER_HISTORY
from netcorenoc.api.routes.static import QUEUE_SATURATION, STATIC_ASSETS, UI_DIR, UI_FILE

__all__ = [
    "BOOTSTRAP_ALLOWED",
    "CSP",
    "DENIED_ACTION",
    "MAX_LABEL_CHARS",
    "MAX_NOTE_CHARS",
    "MAX_POLICY_HISTORY",
    "MAX_SCORER_HISTORY",
    "MUTATING",
    "PREVIEW_RATE_CAPACITY",
    "PREVIEW_RATE_REFILL",
    "QUEUE_SATURATION",
    "RATE_CAPACITY",
    "RATE_REFILL",
    "SECURITY_HEADERS",
    "SSE_HEARTBEAT_S",
    "SSE_UPDATE_S",
    "STATIC_ASSETS",
    "UI_DIR",
    "UI_FILE",
    "AppContext",
    "ConfigIn",
    "DeclaredRoutes",
    "FeedbackIn",
    "GovernancePolicies",
    "LabelIn",
    "LoginIn",
    "PasswordIn",
    "Perimeter",
    "PolicyIn",
    "QuietServer",
    "RateLimiter",
    "RoleIn",
    "ScorerParamsIn",
    "ScorerRollbackIn",
    "TokenIn",
    "UndeclaredRouteError",
    "UserIn",
    "create_app",
]
