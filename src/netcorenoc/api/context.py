"""`AppContext` — the frozen record every route module receives.

The mechanism that lets a 1 752-line file become nine route modules **without editing a single
handler body** (DECISIONS #78). Each `register()` rebinds the fields it uses to local names as its
first statement:

    def register(app: FastAPI, ctx: AppContext) -> None:
        store, engine, security, guarded = ctx.store, ctx.engine, ctx.security, ctx.guarded
        audit_row, scope_for = ctx.perimeter.audit_row, ctx.scope_for

        @route.get("/api/stats")
        async def stats(principal: auth.Principal = Depends(security)) -> dict[str, Any]:
            ...  # body identical to v0.7.1, character for character

That rebinding block is **mandatory**, not stylistic. Rewriting the call sites to
``ctx.audit_row(...)`` would touch all forty handlers and forfeit the handler-hash parity proof,
which is the most valuable artefact this release leaves behind. Bind only the names a module
actually uses — `ruff` fails on one bound and unused, so the block cannot rot.

**`audit_row` is bound from `ctx.perimeter`, not carried as a field** (DECISIONS #86). Its five
keyword-only parameters cannot be spelled in a ``Callable[...]``, so a field would have to be typed
``Callable[..., Awaitable[None]]`` and `mypy --strict` would stop checking the arguments at all
twenty-five audit call sites — in the one helper where a silently-wrong keyword is least
acceptable. Reaching through `ctx.perimeter` keeps the exact bound-method signature. The same
applies to `situation_in_scope` and `audit_scope_denial`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastapi import Request

from netcorenoc import auth, shaping
from netcorenoc.api.governance_cache import GovernancePolicies
from netcorenoc.api.perimeter import Perimeter, RateLimiter

if TYPE_CHECKING:
    from netcorenoc.main import Engine
    from netcorenoc.runtime import RuntimeConfig
    from netcorenoc.store import Store


@dataclass(frozen=True, slots=True)
class AppContext:
    """Everything the nine route modules need, resolved once in `create_app`.

    Frozen because a route module must not be able to swap a perimeter helper for its own: the
    single-decision-site guarantee (DECISIONS #76) would otherwise hold only by convention.
    """

    engine: Engine
    store: Store
    perimeter: Perimeter
    # The security dependency and the three helpers below are `perimeter`'s bound methods,
    # exposed under the names the handlers already call them by.
    security: Callable[[Request], Awaitable[auth.Principal]]
    guarded: list[Any]
    scope_for: Callable[[auth.Principal], Awaitable[shaping.Scope]]
    all_warnings: Callable[[], list[str]]
    write_txn: Callable[[], AbstractAsyncContextManager[None]]
    governance: GovernancePolicies
    # `create_app` parameters the handlers close over.
    preview_limiter: RateLimiter
    throttle: auth.LoginThrottle
    extra_stats: Callable[[], dict[str, Any]] | None
    runtime: RuntimeConfig | None
    tls_enabled: bool
