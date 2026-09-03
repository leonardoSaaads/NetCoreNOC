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

from netcorenoc.api.governance_cache import GovernancePolicies
from netcorenoc.api.perimeter import Perimeter, RateLimiter
from netcorenoc.crosscutting import auth, shaping
from netcorenoc.engine.dataset import capture

if TYPE_CHECKING:
    from netcorenoc.crosscutting.runtime import RuntimeConfig
    from netcorenoc.main import Engine
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

    async def label_context(
        self,
        sid: int,
        scope: shaping.Scope,
        channel: str,
        *,
        member_ids: list[int] | None = None,
        updated_at: float | None = None,
        excluded_ids: list[int] | None = None,
        remainder_together: bool | None = None,
    ) -> capture.LabelContext:
        """Everything a verdict records except the verdict, built once for **every** route that
        writes one.

        A method on the context rather than a closure in one route module, because v0.16.0 gives it
        a third and fourth caller: a `move` and an `operator_split` write a label through exactly
        this path, and the whole value of the channel column depends on a label acquired through a
        restructuring gesture being **identical apart from its channel** to one acquired on a card.
        Two copies would be two chances for them to differ in some way nobody intended.

        `scope` is the one the caller's 404 decision already used, so the record cannot disagree
        with the decision that produced it.
        """
        # v0.9.2 (F47): ONE read, and both scope facts derived from it. `hidden_member_ids` reuses
        # the same `situation_member_ne` + `scope.allows_ne` pair the 404 decision used, so the
        # count of hidden members and the identity of the hidden members are one answer rather than
        # two that can drift (DECISIONS #137).
        hidden = await self.perimeter.hidden_member_ids(sid, scope)
        return capture.LabelContext(
            # v0.8.0 §5.5: the scope fingerprint. A scoped editor labels a **partial view** and
            # cannot say which part, so without this the label is uninterpretable — and the noise is
            # *systematic*, because it correlates with the policy, so it does not average out.
            capture.LabelScope(
                policy_id=self.governance.scope_id,
                restricted=not scope.unrestricted,
                redacted_members=len(hidden),
                hidden_members=hidden,
            ),
            # §5.4b: the client's report, bounded and never rejected. `accept` can only truncate,
            # and records that it did. No id here is looked up, compared, or validated.
            capture.ClientFingerprint.accept(member_ids, updated_at)
            if member_ids is not None
            else None,
            # v0.9.1: **which members do not belong**, under the identical discipline — bounded,
            # never rejected, never used to validate the existence of anything. Absence means "the
            # operator marked nothing", which is a plain `split`, and never a guess.
            #
            # v0.16.0: a `move` supplies the one alarm that is leaving and an `operator_split` the
            # set that is, so the assertion each records is *marked-by-rest negative and nothing
            # else* — which is precisely what the operator did.
            capture.Exclusion.accept(excluded_ids, remainder_together)
            if excluded_ids is not None
            else None,
            channel,
        )
