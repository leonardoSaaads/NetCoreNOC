"""**The HTTP security boundary.** Read this file first if you are reviewing security.

Everything here decides *whether a request may proceed*; nothing here decides *what it returns*.
That is the whole criterion (DECISIONS #76), and it is why the write-side scope check lives beside
the read-side one even though the two sat four hundred lines apart in v0.7.1's `api.py`.

The order the steps run, and what the perimeter does and does not own, are described once in
`docs/architecture/DESIGN.md` § "v0.7.2 — the perimeter as a named component". They are not
restated here, so there is one description to keep true.

The perimeter owns no domain rule and makes no authorization decision of its own: capability
resolution is `rbac.resolve_capabilities` and scope resolution is `shaping.visible_nes`, both
called, never reimplemented. Route modules **receive** the bound helpers below on `AppContext`;
none of them may implement one.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, Response

from netcorenoc import audit, auth, rbac, shaping
from netcorenoc.api.governance_cache import GovernancePolicies

if TYPE_CHECKING:
    from netcorenoc.store import Store

RATE_CAPACITY = 30.0
RATE_REFILL = 10.0
# Preview re-partitions up to MAX_PREVIEW_ALARMS alarms, so it is the one endpoint whose cost is
# worth more than a share of the general bucket: its own, much tighter bucket (3 burst, then one
# roughly every 10 s) bounds it independently of the per-client limiter (F22).
PREVIEW_RATE_CAPACITY = 3.0
PREVIEW_RATE_REFILL = 0.1
MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})

CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
    "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'"
)
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}
BOOTSTRAP_ALLOWED = frozenset(
    {("POST", "/api/logout"), ("GET", "/api/me"), ("POST", "/api/password")}
)
# Presentation layer for the audited-denied set: each capability in
# ``rbac.AUDITED_DENIED_PERMISSIONS`` (the single source) maps to the representative catalog
# action logged on a denied (403) attempt. The keys must exactly equal that set — asserted at
# import (below) and by ``tests/test_rbac.py::test_f8_audited_denied_single_source`` so the two
# tables can never drift (F8).
DENIED_ACTION = {
    "quarantine.read": "quarantine.read",
    "audit.read": "audit.read",
    "audit.export": "audit.export",
    "audit.prune": "prune.manual",
    "users.manage": "user.update",
    "tokens.manage": "token.create",
    "config.read": "config.change",  # a denied config *access* is logged under the config action
    "config.write": "config.change",
    # v0.6.0 (F21): a denied attempt to retune or preview the correlation formula is an attempted
    # system-wide logic change and is recorded under the action it tried to perform.
    "scorer.preview": "scorer.preview",
    "scorer.write": "scorer.config.update",
    # v0.7.0 (F27): a denied attempt to read or rewrite the perimeter is an attempted privilege
    # change, recorded under the policy action it tried to perform.
    "rbac.read": "rbac.policy.update",
    "rbac.write": "rbac.policy.update",
    "scope.read": "scope.policy.update",
    "scope.write": "scope.policy.update",
}
# Fail fast at import if the presentation mapping drifts from the authorization source of truth.
assert set(DENIED_ACTION) == set(rbac.AUDITED_DENIED_PERMISSIONS), (
    "DENIED_ACTION keys must equal rbac.AUDITED_DENIED_PERMISSIONS (single source of truth)"
)


class RateLimiter:
    """Token bucket per client address; deliberately small and in-memory."""

    def __init__(self, capacity: float, refill: float) -> None:
        self.capacity = capacity
        self.refill = refill
        self.buckets: dict[str, tuple[float, float]] = {}

    def allow(self, key: str, now: float) -> bool:
        tokens, last = self.buckets.get(key, (self.capacity, now))
        tokens = min(self.capacity, tokens + (now - last) * self.refill)
        if len(self.buckets) > 4096:
            self.buckets.clear()
        if tokens < 1.0:
            self.buckets[key] = (tokens, now)
            return False
        self.buckets[key] = (tokens - 1.0, now)
        return True


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _route_path(request: Request) -> str:
    route = request.scope.get("route")
    return getattr(route, "path", request.url.path)


class Perimeter:
    """The security boundary as one object, constructed once per application.

    A class rather than free functions because the eleven closures this replaces captured
    ``store``, ``governance``, ``limiter`` and ``warnings`` from ``create_app``'s scope: free
    functions would each have grown a parameter, every call site would have changed, and the proof
    that no handler body moved would have been lost with them (DECISIONS #77). The bodies below are
    the v0.7.1 closure bodies character for character, under exactly one substitution — a captured
    free variable became an attribute of ``self``.

    Constructible outside :func:`~netcorenoc.api.app.create_app`, which is convenient for tests and
    is a second way to instantiate the authorization machinery. It holds no state a caller could not
    already reach through ``store``, so this grants nothing; it is named in
    ``docs/security/SECURITY-REVIEW-0.7.2.md`` rather than left to be discovered.
    """

    def __init__(
        self,
        store: Store,
        *,
        rate_capacity: float,
        rate_refill: float,
        warnings: Callable[[], list[str]] | None,
    ) -> None:
        self._store = store
        self.governance = GovernancePolicies(store)
        self._limiter = RateLimiter(rate_capacity, rate_refill)
        self._warnings = warnings

    def all_warnings(self) -> list[str]:
        """Operator warnings from the process plus any from an unreadable governance policy."""
        return [*(self._warnings() if self._warnings else []), *self.governance.warnings()]

    async def security_headers(
        self, request: Request, call_next: Callable[..., Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    async def audit_row(
        self,
        request: Request,
        principal: auth.Principal | None,
        action: str,
        outcome: str,
        *,
        actor: str | None = None,
        role: str | None = None,
        object_type: str | None = None,
        object_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        await audit.write_event(
            self._store,
            ts=time.time(),
            actor=actor if actor is not None else (principal.actor if principal else "-"),
            role=role if role is not None else (principal.role if principal else None),
            source_ip=_client_ip(request),
            action=action,
            outcome=outcome,
            object_type=object_type,
            object_id=object_id,
            details=details,
        )

    @contextlib.asynccontextmanager
    async def write_txn(self) -> AsyncIterator[None]:
        """**THE** write boundary: one mutation, one transaction, one audit row.

        `Store` holds a single `aiosqlite` connection shared by the engine task and every API
        request, so a `commit()` from *any* caller commits everything pending on it, whoever issued
        it. v0.7.0's `main.py` rolled back on its batch path and `api.py` rolled back nowhere: a
        handler that mutated and then raised left the statement pending, and the **next commit from
        an unrelated caller adopted it**. The mutation landed with no audit row, which contradicts
        F31's "every change is attributable".

        Used by every mutating handler, so the discipline is implemented once rather than repeated
        as a `try/except` in twenty places — twenty copies is twenty chances to forget, and the one
        that is forgotten is invisible until it is exploited (DECISIONS #73).

        A handler that must make an audit row durable *even though the request fails* — a denied
        login, a rejected password change — still commits explicitly before raising. The rollback
        below then has nothing left to undo, so the two compose without an exemption.

        Note for v0.7.2 (DECISIONS #74): this belongs to the perimeter block and moves to
        `perimeter.py` with `scope_for`, `audit_row`, and the security dependency.
        """
        async with self._store.lock:
            try:
                yield
            except BaseException:
                await self._store.rollback()
                raise
            await self._store.commit()

    async def resolve_identity(self, request: Request) -> auth.Principal | None:
        now = time.time()
        header = request.headers.get("authorization", "")
        bearer = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
        async with self._store.lock:
            if bearer:
                principal = await auth.resolve_bearer(self._store, bearer, now)
                await self._store.commit()
                return principal
            cookie = request.cookies.get(auth.COOKIE_NAME)
            if cookie:
                principal = await auth.resolve_session(self._store, cookie, now)
                await self._store.commit()
                return principal
        return None

    def csrf_ok(self, request: Request) -> bool:
        origin = request.headers.get("origin") or request.headers.get("referer")
        if not origin:
            return False
        if urlsplit(origin).netloc != request.headers.get("host"):
            return False
        return request.headers.get("x-netcorenoc-client") == "ui"

    async def flush_governance_fallbacks(self, request: Request) -> None:
        """Audit each newly-detected malformed policy once. Caller must not hold ``store.lock``."""
        while self.governance.pending_fallbacks:
            kind, reason = self.governance.pending_fallbacks.pop(0)
            async with self._store.lock:
                await self.audit_row(
                    request,
                    None,
                    "governance.fallback",
                    "error",
                    actor="system",
                    role=None,
                    object_type="governance_policy",
                    object_id=kind,
                    details={"kind": kind, "reason": reason},
                )
                await self._store.commit()

    async def security(self, request: Request) -> auth.Principal:
        method, path = request.method, _route_path(request)
        # (1) CSRF — cookie-authenticated mutations only.
        cookie_mutation = (
            method in MUTATING
            and bool(request.cookies.get(auth.COOKIE_NAME))
            and "authorization" not in request.headers
        )
        if cookie_mutation and not self.csrf_ok(request):
            raise HTTPException(status_code=403, detail="CSRF check failed")
        # (2) identity
        principal = await self.resolve_identity(request)
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        # (3) bootstrap gate
        if principal.must_change_password and (method, path) not in BOOTSTRAP_ALLOWED:
            raise HTTPException(status_code=403, detail="password change required")
        # (4) RBAC (single source of truth). v0.7.0: the capability set is resolved per request as
        # `ceiling(role) ∩ stored policy` — the compiled map is the first operand, so no stored
        # policy can put a capability here that `PERMISSIONS` does not already grant this role
        # (DECISIONS #53). This is the ONLY authorization decision in the file; `role_allows` is
        # deliberately not called here any more, because it answers the ceiling question rather
        # than the "does this principal hold it right now?" question.
        async with self._store.lock:
            await self.governance.load()
        await self.flush_governance_fallbacks(request)
        capabilities = rbac.resolve_capabilities(
            principal.role, principal.ref, self.governance.capability
        )
        request.state.capabilities = capabilities
        permission = rbac.permission_for(method, path)
        if permission is None or permission not in capabilities:
            # "Should this denial be audited?" is decided by the single rbac source.
            if permission in rbac.AUDITED_DENIED_PERMISSIONS:
                async with self._store.lock:
                    await self.audit_row(
                        request,
                        principal,
                        DENIED_ACTION[permission],
                        "denied",
                        object_type="route",
                        object_id=f"{method} {path}",
                    )
                    await self._store.commit()
            raise HTTPException(status_code=403, detail="insufficient role")
        # (5) rate limit
        if not self._limiter.allow(_client_ip(request), time.monotonic()):
            raise HTTPException(status_code=429, detail="rate limit exceeded")
        request.state.principal = principal
        return principal

    async def scope_for(self, principal: auth.Principal) -> shaping.Scope:
        """Resolve this principal's visible-NE set. **The** scope decision site.

        Short-circuits before touching the database for an admin (never scoped) and for an
        appliance with no scope policy (parity), which is why an un-configured install pays nothing
        for this feature and every read path below runs its unmodified v0.6.0 query.
        """
        if not shaping.is_scopable(principal.role) or self.governance.scope is None:
            return shaping.UNRESTRICTED
        if self.governance.scope.malformed:
            return shaping.DENY_ALL  # fail closed; the admin who must fix it is never scoped
        async with self._store.lock:
            nes = await self._store.list_ne_for_scope()
        return shaping.visible_nes(principal.role, principal.ref, self.governance.scope, nes)

    async def situation_in_scope(self, sid: int, scope: shaping.Scope) -> bool:
        """Is any member alarm of this situation in scope? The write-side membership test.

        Deliberately the **same predicate** `project_situation_detail` uses to decide whether a
        situation is visible at all, reused rather than restated, so a read and a write can never
        disagree about what "yours" means. A second copy would drift one-directionally and silently.

        Returns before touching the database when the scope is unrestricted, so an appliance with
        no policy pays nothing for the perimeter — and so this stays the one place the question is
        answered, rather than every caller re-deciding when to ask it.
        """
        if scope.unrestricted:
            return True
        async with self._store.lock:
            members = await self._store.situation_member_ne(sid)
        return any(scope.allows_ne(ne_id) for ne_id in members.values())

    async def hidden_member_ids(self, sid: int, scope: shaping.Scope) -> frozenset[int]:
        """**Which** of this situation's members the principal could not see (v0.8.0 §5.5).

        The scope fingerprint's third field, and — from v0.9.2 — the input to its fourth.
        `situation_in_scope` answers *"may they label it at all?"*; this answers *"which part of it
        was hidden when they did?"*, and the second question is what makes the label interpretable
        later: a verdict over four visible members of a nine-member situation is a statement about
        four, and a *mark* on one of the other five is an assertion the operator was not in a
        position to make (F47).

        **The set rather than the count, and that is the whole change in v0.9.2.** `LabelScope`
        needs both `scope_redacted_members` (how much of the bag was hidden) and
        `excluded_reconciled_out_of_scope` (how much of the *assertion* was blind), and the two must
        come from **one** read of one predicate. Two reads are two answers that can drift, which is
        the reasoning `situation_in_scope` and this method already share `situation_member_ne` and
        `scope.allows_ne` for (DECISIONS #59, #137) — so the count is `len()` of this, at the one
        call site that needs it, rather than a second query.

        Empty for an unrestricted scope, without touching the database — the parity path stays free.
        """
        if scope.unrestricted:
            return frozenset()
        async with self._store.lock:
            members = await self._store.situation_member_ne(sid)
        return frozenset(
            alarm_id for alarm_id, ne_id in members.items() if not scope.allows_ne(ne_id)
        )

    async def audit_scope_denial(
        self,
        request: Request,
        principal: auth.Principal,
        action: str,
        object_type: str,
        object_id: str,
    ) -> None:
        """Record a scope-denied write. The caller still receives the indistinguishable 404.

        The denial is audited under the action the caller attempted, so a scoped principal probing
        the write surface leaves a trail even though the response tells them nothing.
        """
        async with self.write_txn():
            await self.audit_row(
                request,
                principal,
                action,
                "denied",
                object_type=object_type,
                object_id=object_id,
                details={"reason": "out of visibility scope"},
            )
