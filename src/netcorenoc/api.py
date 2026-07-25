"""FastAPI application: identity, role-based authorization, tamper-evident audit, SSE.

Middleware order (per DESIGN v0.2): security headers (a real middleware) then, inside the
``security`` dependency applied to every protected ``/api`` route — origin/CSRF (cookie
mutations) → identity resolution (session cookie or Bearer token) → bootstrap gate →
RBAC (``rbac.py``, the single source) → per-client rate limit → handler. Handlers audit
every mutating action and every sensitive read (denied attempts included). The single-file
UI and its assets are served statically with no build step.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from netcorenoc import __version__, audit, auth, preview, rbac, scoring, shaping
from netcorenoc.learn import MIN_EDGE_N

if TYPE_CHECKING:
    from netcorenoc.main import Engine
    from netcorenoc.runtime import RuntimeConfig

UI_DIR = Path(__file__).parent / "ui"
UI_FILE = UI_DIR / "index.html"
RATE_CAPACITY = 30.0
RATE_REFILL = 10.0
# Preview re-partitions up to MAX_PREVIEW_ALARMS alarms, so it is the one endpoint whose cost is
# worth more than a share of the general bucket: its own, much tighter bucket (3 burst, then one
# roughly every 10 s) bounds it independently of the per-client limiter (F22).
PREVIEW_RATE_CAPACITY = 3.0
PREVIEW_RATE_REFILL = 0.1
MAX_LABEL_CHARS = 120
MAX_NOTE_CHARS = 500
MAX_SCORER_HISTORY = 50
MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})
SSE_HEARTBEAT_S = 15.0
SSE_UPDATE_S = 2.0
QUEUE_SATURATION = 0.9  # /readyz reports not-ready once the ingest queue passes this fraction

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
STATIC_ASSETS = {
    "app.js": "application/javascript",
    "style.css": "text/css",
    "vendor/d3.v7.min.js": "application/javascript",
    # RFC 9116 machine-readable security contact. Static, public, unauthenticated, additive to
    # this allowlist (not a new dynamic surface); it is served under the same CSP/security-headers
    # middleware and shipped in the package (ui/.well-known/security.txt).
    ".well-known/security.txt": "text/plain; charset=utf-8",
}
# Endpoints reachable while an account still owes a forced password change.
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


class FeedbackIn(BaseModel):
    verdict: Literal["confirm", "split"]


class LabelIn(BaseModel):
    kind: Literal["device", "class"]
    id: int
    label: str = Field(min_length=1, max_length=MAX_LABEL_CHARS)


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=auth.MAX_PASSWORD)
    new_password: str | None = Field(default=None, max_length=auth.MAX_PASSWORD)


class PasswordIn(BaseModel):
    old_password: str = Field(min_length=1, max_length=auth.MAX_PASSWORD)
    new_password: str = Field(min_length=1, max_length=auth.MAX_PASSWORD)


class UserIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=auth.MAX_PASSWORD)
    role: Literal["viewer", "editor", "admin"]


class RoleIn(BaseModel):
    role: Literal["viewer", "editor", "admin"]


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    role: Literal["viewer", "editor", "admin"]


class ConfigIn(BaseModel):
    allowlist: str = Field(max_length=1024)
    retention_days: float = Field(gt=0, le=3650)


class ScorerParamsIn(BaseModel):
    """A candidate parameter set. Pydantic bounds the *shape*; `scoring.validate_params` is the
    single semantic authority (it also rejects the degenerate combinations a range check misses),
    so both run and the precise reason always comes from the one validator."""

    w_t: float = Field(ge=0.0, le=1.0)
    w_a: float = Field(ge=0.0, le=1.0)
    w_e: float = Field(ge=0.0, le=1.0)
    tau_s: float = Field(gt=0.0, le=scoring.MAX_TAU_S)
    threshold: float = Field(ge=0.0, le=1.0)
    note: str = Field(default="", max_length=MAX_NOTE_CHARS)


class ScorerRollbackIn(BaseModel):
    config_id: int = Field(ge=1)


class QuietServer(uvicorn.Server):
    """Uvicorn server that leaves signal handling to the NetCoreNOC process."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _route_path(request: Request) -> str:
    route = request.scope.get("route")
    return getattr(route, "path", request.url.path)


def _params_of(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """The five parameters of a stored scorer config, for a before/after audit detail."""
    if row is None:
        return None
    return {
        "config_id": int(row["id"]),
        "w_t": row["w_t"],
        "w_a": row["w_a"],
        "w_e": row["w_e"],
        "tau_s": row["tau_s"],
        "threshold": row["threshold"],
    }


def create_app(
    engine: Engine,
    extra_stats: Callable[[], dict[str, Any]] | None = None,
    rate_capacity: float = RATE_CAPACITY,
    rate_refill: float = RATE_REFILL,
    throttle: auth.LoginThrottle | None = None,
    tls_enabled: bool = False,
    runtime: RuntimeConfig | None = None,
    warnings: Callable[[], list[str]] | None = None,
    preview_rate_capacity: float = PREVIEW_RATE_CAPACITY,
    preview_rate_refill: float = PREVIEW_RATE_REFILL,
) -> FastAPI:
    app = FastAPI(title="NetCoreNOC", version=__version__, docs_url=None, redoc_url=None)
    limiter = RateLimiter(rate_capacity, rate_refill)
    preview_limiter = RateLimiter(preview_rate_capacity, preview_rate_refill)
    throttle = throttle or auth.LoginThrottle()
    store = engine.store

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[..., Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    # -- audit helpers (all called while the caller holds store.lock) ------------------

    async def audit_row(
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
            store,
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

    # -- identity resolution (under the store lock; sessions/tokens touch the DB) -------

    async def resolve_identity(request: Request) -> auth.Principal | None:
        now = time.time()
        header = request.headers.get("authorization", "")
        bearer = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
        async with store.lock:
            if bearer:
                principal = await auth.resolve_bearer(store, bearer, now)
                await store.commit()
                return principal
            cookie = request.cookies.get(auth.COOKIE_NAME)
            if cookie:
                principal = await auth.resolve_session(store, cookie, now)
                await store.commit()
                return principal
        return None

    def csrf_ok(request: Request) -> bool:
        origin = request.headers.get("origin") or request.headers.get("referer")
        if not origin:
            return False
        if urlsplit(origin).netloc != request.headers.get("host"):
            return False
        return request.headers.get("x-netcorenoc-client") == "ui"

    async def security(request: Request) -> auth.Principal:
        method, path = request.method, _route_path(request)
        # (1) CSRF — cookie-authenticated mutations only.
        cookie_mutation = (
            method in MUTATING
            and bool(request.cookies.get(auth.COOKIE_NAME))
            and "authorization" not in request.headers
        )
        if cookie_mutation and not csrf_ok(request):
            raise HTTPException(status_code=403, detail="CSRF check failed")
        # (2) identity
        principal = await resolve_identity(request)
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication required")
        # (3) bootstrap gate
        if principal.must_change_password and (method, path) not in BOOTSTRAP_ALLOWED:
            raise HTTPException(status_code=403, detail="password change required")
        # (4) RBAC (single source of truth)
        permission = rbac.permission_for(method, path)
        if permission is None or not rbac.role_allows(principal.role, permission):
            # "Should this denial be audited?" is decided by the single rbac source.
            if permission in rbac.AUDITED_DENIED_PERMISSIONS:
                async with store.lock:
                    await audit_row(
                        request,
                        principal,
                        DENIED_ACTION[permission],
                        "denied",
                        object_type="route",
                        object_id=f"{method} {path}",
                    )
                    await store.commit()
            raise HTTPException(status_code=403, detail="insufficient role")
        # (5) rate limit
        if not limiter.allow(_client_ip(request), time.monotonic()):
            raise HTTPException(status_code=429, detail="rate limit exceeded")
        request.state.principal = principal
        return principal

    guarded = [Depends(security)]

    # -- public routes -----------------------------------------------------------------

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/readyz")
    async def readyz(response: Response) -> dict[str, str]:
        """Orchestrator readiness (§A.5): 200 only when the DB is reachable, migrations are
        applied, and the queue has headroom; 503 otherwise. Unauthenticated by design and so
        leaks no detail beyond ok/not-ok — the reasons live behind authenticated /api/stats."""
        ready = True
        try:
            async with store.lock:
                applied = await store.schema_version() == store.latest_schema_version()
            ready = applied
        except Exception:  # DB unreachable => not ready, never a 500
            ready = False
        queue = engine.queue
        if queue.maxsize and queue.qsize() >= queue.maxsize * QUEUE_SATURATION:
            ready = False  # saturated: cannot accept the load it is being sent
        if not ready:
            response.status_code = 503
            return {"status": "not ready"}
        return {"status": "ready"}

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(UI_FILE, media_type="text/html")

    def _asset_route(asset: str, media_type: str) -> None:
        async def serve() -> FileResponse:
            return FileResponse(UI_DIR / asset, media_type=media_type)

        app.add_api_route(f"/{asset}", serve, include_in_schema=False)

    for _asset, _media in STATIC_ASSETS.items():
        _asset_route(_asset, _media)

    def _cookie_kwargs() -> dict[str, Any]:
        return {"httponly": True, "samesite": "strict", "path": "/", "secure": tls_enabled}

    @app.post("/api/login")
    async def login(body: LoginIn, request: Request, response: Response) -> dict[str, Any]:
        source_ip = _client_ip(request)
        async with store.lock:
            outcome = await auth.perform_login(
                store,
                throttle,
                username=body.username,
                password=body.password,
                new_password=body.new_password,
                source_ip=source_ip,
                now=time.time(),
            )
            if outcome.status == "locked":
                await audit_row(
                    request,
                    None,
                    "login.lockout",
                    "denied",
                    actor=body.username,
                    object_type="user",
                    object_id=body.username,
                )
                await store.commit()
                raise HTTPException(status_code=429, detail=outcome.message)
            if outcome.status in ("fail", "policy"):
                await audit_row(
                    request,
                    None,
                    "login.fail",
                    "denied",
                    actor=body.username,
                    object_type="user",
                    object_id=body.username,
                    details={"reason": outcome.status},
                )
                await store.commit()
                if outcome.status == "policy":
                    raise HTTPException(status_code=400, detail=outcome.message)
                raise HTTPException(status_code=401, detail=outcome.message)
            if outcome.status == "must_change":
                await store.commit()
                return {"must_change_password": True}  # nosec B105 - response flag, not a secret
            assert outcome.principal is not None and outcome.session_token is not None
            if body.new_password:
                await audit_row(
                    request,
                    outcome.principal,
                    "password.change",
                    "ok",
                    object_type="user",
                    object_id=str(outcome.principal.user_id),
                )
            await audit_row(
                request,
                outcome.principal,
                "login.ok",
                "ok",
                object_type="user",
                object_id=str(outcome.principal.user_id),
            )
            await store.commit()
        response.set_cookie(auth.COOKIE_NAME, outcome.session_token, **_cookie_kwargs())
        return {
            "user": outcome.principal.actor,
            "role": outcome.principal.role,
            "must_change_password": False,  # nosec B105 - response flag, not a secret
        }

    @app.post("/api/logout")
    async def logout(
        request: Request, response: Response, principal: auth.Principal = Depends(security)
    ) -> dict[str, str]:
        cookie = request.cookies.get(auth.COOKIE_NAME)
        async with store.lock:
            if cookie:
                await store.delete_session(auth.hash_token(cookie))
            await audit_row(request, principal, "logout", "ok")
            await store.commit()
        response.delete_cookie(auth.COOKIE_NAME, path="/")
        return {"status": "logged out"}

    @app.get("/api/me")
    async def me(principal: auth.Principal = Depends(security)) -> dict[str, Any]:
        return {
            "user": principal.actor,
            "role": principal.role,
            "must_change_password": principal.must_change_password,
        }

    @app.post("/api/password")
    async def change_password(
        body: PasswordIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, str]:
        if principal.user_id is None:
            raise HTTPException(status_code=403, detail="service tokens have no password")
        policy = auth.validate_password(body.new_password)
        if policy is not None:
            raise HTTPException(status_code=400, detail=policy)
        async with store.lock:
            user = await store.get_user(principal.user_id)
            if user is None or not auth.verify_password(body.old_password, user["password_hash"]):
                await audit_row(
                    request,
                    principal,
                    "password.change",
                    "denied",
                    object_type="user",
                    object_id=str(principal.user_id),
                )
                await store.commit()
                raise HTTPException(status_code=400, detail="current password incorrect")
            await store.update_user_password(
                principal.user_id, auth.hash_password(body.new_password), time.time()
            )
            await store.revoke_user_sessions(principal.user_id)
            await audit_row(
                request,
                principal,
                "password.change",
                "ok",
                object_type="user",
                object_id=str(principal.user_id),
            )
            await store.commit()
        return {"status": "password changed; sign in again"}

    # -- read endpoints (viewer+) ------------------------------------------------------

    @app.get("/api/stats", dependencies=guarded)
    async def stats() -> dict[str, Any]:
        async with store.lock:
            out: dict[str, Any] = dict(await store.stats())
            out["ingest_gaps"] = await store.list_ingest_gaps(20)
        out["open_ingest_gaps"] = engine.gap.snapshot()
        out["latency_p95_s"] = round(engine.latency_p95(), 4)
        out["queue_depth"] = engine.queue.qsize()
        out["warnings"] = warnings() if warnings else []
        if extra_stats is not None:
            out.update(extra_stats())
        return out

    @app.get("/api/graph")
    async def graph(principal: auth.Principal = Depends(security)) -> dict[str, Any]:
        async with store.lock:
            snapshot = await store.graph_snapshot(min_edge_n=MIN_EDGE_N)
        return shaping.shape(snapshot, principal.role)  # coarsen device IPs below editor

    @app.get("/api/classes", dependencies=guarded)
    async def classes() -> list[dict[str, Any]]:
        async with store.lock:
            return await store.list_classes()

    @app.get("/api/situations", dependencies=guarded)
    async def situations(
        status: Literal["open", "closed", "merged"] | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        async with store.lock:
            return await store.list_situations(status, min(max(limit, 1), 500))

    @app.get("/api/situations/{sid}")
    async def situation(sid: int, principal: auth.Principal = Depends(security)) -> dict[str, Any]:
        async with store.lock:
            detail = await store.situation_detail(sid)
        if detail is None:
            raise HTTPException(status_code=404, detail="no such situation")
        # v0.6.0: every link carries its explanation as a typed, *named* term list — the same
        # three numbers, from one source (`LinkScore.terms`) rather than three ad-hoc columns.
        # The columns stay for compatibility and remain byte-identical (DECISIONS #50).
        for link in detail.get("links", []):
            link["terms"] = [
                {"name": "temporal", "contribution": link["term_t"]},
                {"name": "class_affinity", "contribution": link["term_a"]},
                {"name": "entity_affinity", "contribution": link["term_e"]},
            ]
        return shaping.shape(detail, principal.role)  # coarsen alarm device IPs below editor

    @app.get("/api/timeline")
    async def timeline(
        limit: int = 300, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        async with store.lock:
            marks = await store.timeline_marks(min(max(limit, 1), 1000))
        return {"marks": shaping.shape(marks, principal.role)}  # coarsen device IPs below editor

    # -- entity tree + varbind profiler (viewer+, inspectable) -------------------------

    @app.get("/api/entities")
    async def entities(principal: auth.Principal = Depends(security)) -> list[dict[str, Any]]:
        async with store.lock:
            nes = await store.list_ne()
            out: list[dict[str, Any]] = []
            for ne in nes:
                ents = await store.entities_for_ne(int(ne["id"]))
                out.append({**ne, "entity_count": len(ents), "entities": ents})
        return shaping.shape(out, principal.role)  # coarsen NE IPs below editor

    @app.get("/api/entities/{ne_id}")
    async def entity_detail(
        ne_id: int, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        async with store.lock:
            ne = next((n for n in await store.list_ne() if int(n["id"]) == ne_id), None)
            entities_rows = await store.entities_for_ne(ne_id) if ne else []
            profiles = await store.varbind_profiles_for_ne(ne_id) if ne else []
        if ne is None:
            raise HTTPException(status_code=404, detail="no such NE")
        # Live profiler judgement (fresher than the flushed rows), fully broken down so the
        # operator can see why a varbind is (or is not) the entity discriminator.
        candidates = [
            {
                "varbind_oid": c.varbind_oid,
                "r": round(c.r, 4),
                "x": round(c.x, 4),
                "d": round(c.d, 4),
                "score": round(c.score, 4),
                "n_obs": c.n_obs,
                "n_distinct": c.n_distinct,
                "meets_floor": c.meets_floor(),
            }
            for c in engine.profiler.candidates(ne_id)
        ]
        detail = {
            "ne": ne,
            "entities": entities_rows,
            "profiles": profiles,
            "candidates": candidates,
        }
        return shaping.shape(detail, principal.role)  # coarsen NE ip below editor

    @app.get("/api/state-clears", dependencies=guarded)
    async def state_clears() -> list[dict[str, Any]]:
        """Learned state fields (S9): which class, which varbind OID, and the raise/clear
        values — the state analogue of the entity/severity inspectability surface."""
        async with store.lock:
            return await store.list_state_clears()

    @app.post("/api/entities/{ne_id}/reset")
    async def reset_entity(
        ne_id: int, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, str]:
        """Admin recourse for a poisoned identity: forget the NE's learned entity/severity
        decision (history untouched). The next sweep re-decides from current evidence."""
        async with store.lock:
            if not any(int(n["id"]) == ne_id for n in await store.list_ne()):
                raise HTTPException(status_code=404, detail="no such NE")
            await engine.reset_entity(ne_id, time.time())
            await audit_row(
                request, principal, "entity.reset", "ok", object_type="ne", object_id=str(ne_id)
            )
            await store.commit()
        return {"status": "entity decision reset"}

    @app.post("/api/profiles/{ne_id}/reset")
    async def reset_profile(
        ne_id: int, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, str]:
        """Wipe the NE's profiler evidence as well, so identity and severity re-measure from
        scratch — the recovery when the accumulated evidence itself is poisoned."""
        async with store.lock:
            if not any(int(n["id"]) == ne_id for n in await store.list_ne()):
                raise HTTPException(status_code=404, detail="no such NE")
            await engine.reset_profile(ne_id, time.time())
            await audit_row(
                request, principal, "profile.reset", "ok", object_type="ne", object_id=str(ne_id)
            )
            await store.commit()
        return {"status": "profiler evidence reset"}

    # -- operate (editor+) -------------------------------------------------------------

    @app.post("/api/situations/{sid}/feedback")
    async def feedback(
        sid: int, body: FeedbackIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, str]:
        async with store.lock:
            recorded = await engine.apply_feedback(sid, body.verdict, time.time())
            if recorded:
                await audit_row(
                    request,
                    principal,
                    "feedback",
                    "ok",
                    object_type="situation",
                    object_id=str(sid),
                    details={"verdict": body.verdict},
                )
                await store.commit()
        if not recorded:
            raise HTTPException(status_code=404, detail="no such situation")
        return {"status": "recorded", "verdict": body.verdict}

    @app.post("/api/labels")
    async def set_label(
        body: LabelIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, str]:
        async with store.lock:
            await store.set_label(body.kind, body.id, body.label, time.time())
            await audit_row(
                request,
                principal,
                "label.set",
                "ok",
                object_type=body.kind,
                object_id=str(body.id),
                details={"label_len": len(body.label)},
            )
            await store.commit()
        return {"status": "labelled"}

    @app.post("/api/situations/{sid}/close")
    async def close_situation(
        sid: int, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, str]:
        async with store.lock:
            closed = await store.manual_close_situation(sid, time.time())
            if closed:
                await audit_row(
                    request,
                    principal,
                    "situation.close",
                    "ok",
                    object_type="situation",
                    object_id=str(sid),
                )
                await store.commit()
        if not closed:
            raise HTTPException(status_code=404, detail="no such open situation")
        engine.forget_situation(sid)
        return {"status": "closed"}

    # -- admin: users ------------------------------------------------------------------

    @app.get("/api/users", dependencies=guarded)
    async def list_users() -> list[dict[str, Any]]:
        async with store.lock:
            return await store.list_users()

    @app.post("/api/users")
    async def create_user(
        body: UserIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        policy = auth.validate_password(body.password)
        if policy is not None:
            raise HTTPException(status_code=400, detail=policy)
        async with store.lock:
            if await store.get_user_by_name(body.username) is not None:
                raise HTTPException(status_code=409, detail="username already exists")
            uid = await store.create_user(
                body.username, auth.hash_password(body.password), body.role, False, time.time()
            )
            await audit_row(
                request,
                principal,
                "user.create",
                "ok",
                object_type="user",
                object_id=str(uid),
                details={"username": body.username, "role": body.role},
            )
            await store.commit()
        return {"id": uid, "username": body.username, "role": body.role}

    @app.post("/api/users/{uid}/role")
    async def change_role(
        uid: int, body: RoleIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, str]:
        async with store.lock:
            if await store.get_user(uid) is None:
                raise HTTPException(status_code=404, detail="no such user")
            await store.set_user_role(uid, body.role, time.time())
            await store.revoke_user_sessions(uid)  # role change revokes sessions
            await audit_row(
                request,
                principal,
                "role.change",
                "ok",
                object_type="user",
                object_id=str(uid),
                details={"role": body.role},
            )
            await store.commit()
        return {"status": "role changed"}

    @app.delete("/api/users/{uid}")
    async def delete_user(
        uid: int, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, str]:
        if principal.user_id == uid:
            raise HTTPException(status_code=400, detail="cannot delete your own account")
        async with store.lock:
            if await store.get_user(uid) is None:
                raise HTTPException(status_code=404, detail="no such user")
            await store.revoke_user_sessions(uid)
            await store.delete_user(uid)
            await audit_row(
                request, principal, "user.delete", "ok", object_type="user", object_id=str(uid)
            )
            await store.commit()
        return {"status": "deleted"}

    # -- admin: service tokens ---------------------------------------------------------

    @app.get("/api/tokens", dependencies=guarded)
    async def list_tokens() -> list[dict[str, Any]]:
        async with store.lock:
            return await store.list_tokens()

    @app.post("/api/tokens")
    async def create_token(
        body: TokenIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        token_value = auth.new_session_token()
        async with store.lock:
            if await store.get_token(auth.hash_token(token_value)) is not None:
                raise HTTPException(status_code=500, detail="token collision")  # pragma: no cover
            try:
                tid = await store.create_token(
                    auth.hash_token(token_value), body.name, body.role, principal.actor, time.time()
                )
            except Exception as exc:  # duplicate name (UNIQUE)
                raise HTTPException(status_code=409, detail="token name already exists") from exc
            await audit_row(
                request,
                principal,
                "token.create",
                "ok",
                object_type="token",
                object_id=str(tid),
                details={"name": body.name, "role": body.role},
            )
            await store.commit()
        return {"id": tid, "name": body.name, "role": body.role, "token": token_value}

    @app.delete("/api/tokens/{tid}")
    async def revoke_token(
        tid: int, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, str]:
        async with store.lock:
            revoked = await store.revoke_token(tid, time.time())
            if revoked is None:
                raise HTTPException(status_code=404, detail="no such active token")
            await audit_row(
                request,
                principal,
                "token.revoke",
                "ok",
                object_type="token",
                object_id=str(tid),
                details={"name": revoked["name"]},
            )
            await store.commit()
        return {"status": "revoked"}

    # -- admin: config -----------------------------------------------------------------

    @app.get("/api/config", dependencies=guarded)
    async def get_config() -> dict[str, Any]:
        allowlist = runtime.allowlist if runtime else ""
        retention = runtime.retention_days if runtime else 0.0
        async with store.lock:
            saved_allow = await store.get_meta("config.allowlist")
            saved_ret = await store.get_meta("config.retention_days")
        return {
            "allowlist": saved_allow if saved_allow is not None else allowlist,
            "retention_days": float(saved_ret) if saved_ret is not None else retention,
        }

    @app.post("/api/config")
    async def set_config(
        body: ConfigIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, str]:
        async with store.lock:
            await store.set_meta("config.allowlist", body.allowlist)
            await store.set_meta("config.retention_days", str(body.retention_days))
            await audit_row(
                request,
                principal,
                "config.change",
                "ok",
                object_type="config",
                details={
                    "retention_days": body.retention_days,
                    "allowlist_entries": len([x for x in body.allowlist.split(",") if x.strip()]),
                },
            )
            await store.commit()
        if runtime is not None:
            runtime.apply_allowlist(body.allowlist)
            runtime.retention_days = body.retention_days
        return {"status": "saved"}

    # -- the scoring seam (v0.6.0) -----------------------------------------------------

    def _active_scorer() -> scoring.AdditiveScorer:
        """The parameters the engine is scoring with right now (post-fallback, if degraded)."""
        active = engine.correlator.scorer.active
        return active if isinstance(active, scoring.AdditiveScorer) else scoring.default_scorer()

    def _scorer_row(row: dict[str, Any]) -> dict[str, Any]:
        """One history row, projected. No secrets here — a parameter set explains grouping."""
        return {
            "id": int(row["id"]),
            "scorer_id": row["scorer_id"],
            "contract_version": row["contract_version"],
            "w_t": row["w_t"],
            "w_a": row["w_a"],
            "w_e": row["w_e"],
            "tau_s": row["tau_s"],
            "threshold": row["threshold"],
            "params_hash": row["params_hash"],
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "note": row["note"],
            "active": bool(row.get("active")),
        }

    @app.get("/api/scorer", dependencies=guarded)
    async def get_scorer() -> dict[str, Any]:
        """The active scorer, its parameters, the validation bounds, and the config history.

        Viewer+ by design: these numbers *explain* every grouping decision and are not a secret
        (SCOPE-0.6 §2). Writing them is a separate, admin-only capability."""
        async with store.lock:
            history = await store.list_scorer_configs(MAX_SCORER_HISTORY)
        active = _active_scorer()
        safe = engine.correlator.scorer
        return {
            "scorer_id": active.scorer_id,
            "contract_version": active.contract_version,
            "supported_contract_version": scoring.CONTRACT_VERSION,
            "params": active.params(),
            "params_hash": active.params_fingerprint(),
            "config_id": engine.scorer_config_id,
            "degraded": safe.degraded,
            "degraded_reason": safe.last_error,
            "bounds": {
                "min_tau_s": scoring.MIN_TAU_S,
                "max_tau_s": scoring.MAX_TAU_S,
                "min_weight_sum": scoring.MIN_WEIGHT_SUM,
                "min_threshold": scoring.MIN_THRESHOLD,
                "threshold_margin": scoring.THRESHOLD_MARGIN,
            },
            "preview_limits": {
                "max_alarms": preview.MAX_PREVIEW_ALARMS,
                "timeout_s": preview.PREVIEW_TIMEOUT_S,
            },
            "history": [_scorer_row(row) for row in history],
        }

    def _validated(body: ScorerParamsIn) -> None:
        """Semantic validation — bounds *and* degeneracy. A rejected set is never stored."""
        try:
            scoring.validate_params(body.w_t, body.w_a, body.w_e, body.tau_s, body.threshold)
        except scoring.ScorerParamsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/scorer/preview")
    async def preview_scorer(
        body: ScorerParamsIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """Read-only what-if: how would these parameters regroup my recent alarms?

        Bounded (alarm cap + hard wall-clock budget), rate-limited by its own tight bucket,
        admin-only, deterministic, and it writes nothing but its own audit row (F22). It runs
        entirely on the HTTP side and never touches the ingest path."""
        if not preview_limiter.allow(_client_ip(request), time.monotonic()):
            raise HTTPException(status_code=429, detail="preview rate limit exceeded")
        _validated(body)
        candidate = scoring.AdditiveScorer(
            w_t=body.w_t, w_a=body.w_a, w_e=body.w_e, tau_s=body.tau_s, threshold=body.threshold
        )
        async with store.lock:
            rows = await store.recent_alarms_for_preview(preview.MAX_PREVIEW_ALARMS)
        alarms = [
            preview.PreviewAlarm(
                alarm_id=int(r["id"]),
                class_id=int(r["class_id"]),
                ne_id=int(r["ne_id"]) if r["ne_id"] is not None else 0,
                entity_id=int(r["entity_id"]) if r["entity_id"] is not None else 0,
                ts=float(r["first_seen"]),
            )
            for r in rows
        ]
        active = _active_scorer()
        deadline = time.monotonic() + preview.PREVIEW_TIMEOUT_S
        try:
            before, links_before = preview.partition(
                alarms, active, engine.learner, deadline=deadline
            )
            after, links_after = preview.partition(
                alarms, candidate, engine.learner, deadline=deadline
            )
        except preview.PreviewTimeoutError as exc:
            raise HTTPException(
                status_code=503,
                detail="preview exceeded its time budget and was abandoned; no change was made",
            ) from exc
        delta = preview.diff_partitions(before, after)
        async with store.lock:
            await audit_row(
                request,
                principal,
                "scorer.preview",
                "ok",
                object_type="scorer_config",
                details={
                    "alarms": len(alarms),
                    "candidate_hash": candidate.params_fingerprint(),
                    "situations_before": delta["situations_before"],
                    "situations_after": delta["situations_after"],
                },
            )
            await store.commit()
        return {
            **delta,
            "alarms_considered": len(alarms),
            "alarms_cap": preview.MAX_PREVIEW_ALARMS,
            "links_before": links_before,
            "links_after": links_after,
            "active_params": active.params(),
            "candidate_params": candidate.params(),
            # Stated here, not only in the UI: an operator reading the raw API must see the limit.
            "caveat": (
                "Directional, not exhaustive: computed over the most recent "
                f"{len(alarms)} alarm(s) with the learned matrices held fixed, so it shows the "
                "immediate effect of the change, not where the system settles afterwards."
            ),
        }

    @app.post("/api/scorer")
    async def set_scorer(
        body: ScorerParamsIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """Append an immutable configuration and make it active. Audited; reversible in one call.

        Takes effect at the engine's next configuration reload point (the next maintenance pass),
        never mid-batch."""
        _validated(body)
        now = time.time()
        params_hash = scoring.params_hash(
            scoring.DEFAULT_SCORER_ID,
            scoring.CONTRACT_VERSION,
            body.w_t,
            body.w_a,
            body.w_e,
            body.tau_s,
            body.threshold,
        )
        async with store.lock:
            previous = await store.active_scorer_config()
            config_id = await store.insert_scorer_config(
                scoring.DEFAULT_SCORER_ID,
                scoring.CONTRACT_VERSION,
                body.w_t,
                body.w_a,
                body.w_e,
                body.tau_s,
                body.threshold,
                params_hash,
                principal.actor,
                now,
                body.note,
            )
            await store.set_active_scorer_config(config_id, principal.actor, now)
            await audit_row(
                request,
                principal,
                "scorer.config.update",
                "ok",
                object_type="scorer_config",
                object_id=str(config_id),
                details={
                    "action": "apply",
                    "before": _params_of(previous),
                    "after": {
                        "w_t": body.w_t,
                        "w_a": body.w_a,
                        "w_e": body.w_e,
                        "tau_s": body.tau_s,
                        "threshold": body.threshold,
                    },
                    "params_hash": params_hash,
                    "note_len": len(body.note),
                },
            )
            await store.commit()
        return {"status": "applied", "config_id": config_id, "params_hash": params_hash}

    @app.post("/api/scorer/rollback")
    async def rollback_scorer(
        body: ScorerRollbackIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """Point the active configuration at an earlier, immutable row. One call, no data lost."""
        now = time.time()
        async with store.lock:
            target = await store.get_scorer_config(body.config_id)
            if target is None:
                raise HTTPException(status_code=404, detail="no such scorer configuration")
            previous = await store.active_scorer_config()
            await store.set_active_scorer_config(body.config_id, principal.actor, now)
            await audit_row(
                request,
                principal,
                "scorer.config.update",
                "ok",
                object_type="scorer_config",
                object_id=str(body.config_id),
                details={
                    "action": "rollback",
                    "before": _params_of(previous),
                    "after": _params_of(target),
                    "params_hash": target["params_hash"],
                },
            )
            await store.commit()
        return {"status": "rolled back", "config_id": body.config_id}

    # -- admin: quarantine (the read itself is audited) --------------------------------

    @app.get("/api/quarantine")
    async def read_quarantine(
        request: Request, limit: int = 100, principal: auth.Principal = Depends(security)
    ) -> list[dict[str, Any]]:
        async with store.lock:
            rows = await store.list_quarantine(min(max(limit, 1), 500))
            await audit_row(
                request,
                principal,
                "quarantine.read",
                "ok",
                object_type="quarantine",
                details={"count": len(rows)},
            )
            await store.commit()
        return rows

    # -- admin: audit ------------------------------------------------------------------

    @app.get("/api/audit")
    async def read_audit(
        request: Request, limit: int = 200, principal: auth.Principal = Depends(security)
    ) -> list[dict[str, Any]]:
        async with store.lock:
            rows = await store.list_audit(min(max(limit, 1), 1000))
            await audit_row(request, principal, "audit.read", "ok", details={"count": len(rows)})
            await store.commit()
        return rows

    @app.get("/api/audit/export")
    async def export_audit(
        request: Request, principal: auth.Principal = Depends(security)
    ) -> Response:
        async with store.lock:
            lines, final_hash = await audit.export_ndjson(store)
            await audit_row(
                request, principal, "audit.export", "ok", details={"final_hash": final_hash}
            )
            await store.commit()
        body = "\n".join(lines) + ("\n" if lines else "")
        return Response(
            content=body,
            media_type="application/x-ndjson",
            headers={"X-Audit-Final-Hash": final_hash},
        )

    @app.post("/api/audit/prune")
    async def prune_audit(
        request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        async with store.lock:
            retention_days = float(
                await store.get_meta("config.audit_retention_days") or engine.audit_retention_days
            )
            removed = await store.prune_audit(time.time() - retention_days * 86400.0)
            await audit_row(
                request,
                principal,
                "prune.manual",
                "ok",
                object_type="audit_log",
                details={"removed": removed},
            )
            await store.commit()
        return {"status": "pruned", "removed": removed}

    # -- SSE: primary live-update path -------------------------------------------------

    @app.get("/api/events")
    async def events(principal: auth.Principal = Depends(security)) -> StreamingResponse:
        async def snapshot() -> str:
            async with store.lock:
                stats_out: dict[str, Any] = dict(await store.stats())
                graph_out = await store.graph_snapshot(min_edge_n=MIN_EDGE_N)
                sits = await store.list_situations("open", 50)
            stats_out["latency_p95_s"] = round(engine.latency_p95(), 4)
            stats_out["queue_depth"] = engine.queue.qsize()
            stats_out["warnings"] = warnings() if warnings else []
            if extra_stats is not None:
                stats_out.update(extra_stats())
            # Shape the live stream by the subscriber's role, exactly like the polled endpoints.
            payload = {
                "stats": stats_out,
                "graph": shaping.shape(graph_out, principal.role),
                "situations": shaping.shape(sits, principal.role),
            }
            return "event: update\ndata: " + json.dumps(payload) + "\n\n"

        async def gen() -> AsyncIterator[str]:
            yield ": connected\n\n"
            yield await snapshot()
            last_beat = time.monotonic()
            while True:
                await asyncio.sleep(SSE_UPDATE_S)
                yield await snapshot()
                now = time.monotonic()
                if now - last_beat >= SSE_HEARTBEAT_S:
                    last_beat = now
                    yield ": heartbeat\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    return app
