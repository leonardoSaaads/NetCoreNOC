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
import hashlib
import json
import time
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from netcorenoc import __version__, audit, auth, preview, rbac, scoring, shaping
from netcorenoc.api import (
    routes_admin,
    routes_auth,
    routes_operate,
    routes_read,
    routes_static,
)
from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.api.models import (
    PolicyIn,
    ScorerParamsIn,
    ScorerRollbackIn,
)
from netcorenoc.api.perimeter import (
    PREVIEW_RATE_CAPACITY,
    PREVIEW_RATE_REFILL,
    RATE_CAPACITY,
    RATE_REFILL,
    Perimeter,
    RateLimiter,
    _client_ip,
)
from netcorenoc.learn import MIN_EDGE_N

if TYPE_CHECKING:
    from netcorenoc.main import Engine
    from netcorenoc.runtime import RuntimeConfig

# v0.7.2: `api` is a package one level below `netcorenoc`, so the UI lives one directory further
# up than it did when this was `netcorenoc/api.py`. The extra `.parent` is a consequence of the
# move, not a change to what is served: the resolved path is byte-identical to v0.7.1's, and
# `tests/test_security_txt.py` / `tests/test_deploy.py` assert the served files from it.
UI_DIR = Path(__file__).parent.parent / "ui"
UI_FILE = UI_DIR / "index.html"
MAX_SCORER_HISTORY = 50
MAX_POLICY_HISTORY = 50
SSE_HEARTBEAT_S = 15.0
SSE_UPDATE_S = 2.0
QUEUE_SATURATION = 0.9  # /readyz reports not-ready once the ingest queue passes this fraction
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
    preview_limiter = RateLimiter(preview_rate_capacity, preview_rate_refill)
    throttle = throttle or auth.LoginThrottle()
    store = engine.store

    # The whole HTTP security boundary, built once. Its methods are aliased to the names the
    # handlers below already call them by, so no handler body changes (DECISIONS #77, #78).
    perimeter = Perimeter(
        store, rate_capacity=rate_capacity, rate_refill=rate_refill, warnings=warnings
    )
    governance = perimeter.governance
    all_warnings = perimeter.all_warnings
    audit_row = perimeter.audit_row
    write_txn = perimeter.write_txn
    security = perimeter.security
    scope_for = perimeter.scope_for
    app.middleware("http")(perimeter.security_headers)

    # THE registration path: refuses a route rbac.py has not been told about, while the
    # application is being built rather than per request (DECISIONS #80).
    route = DeclaredRoutes(app)
    guarded = [Depends(security)]

    # Everything the route modules need, resolved once. Each `register()` rebinds the fields it
    # uses to local names as its first statement, which is what lets every handler body below stay
    # textually identical to v0.7.1 (DECISIONS #78).
    ctx = AppContext(
        engine=engine,
        store=store,
        perimeter=perimeter,
        security=security,
        guarded=guarded,
        scope_for=scope_for,
        all_warnings=all_warnings,
        write_txn=write_txn,
        governance=governance,
        preview_limiter=preview_limiter,
        throttle=throttle,
        extra_stats=extra_stats,
        runtime=runtime,
        tls_enabled=tls_enabled,
    )

    # Registered in the order the routes were declared at v0.7.1, which is what fixes FastAPI's
    # path-matching precedence (tests/test_architecture.py::test_route_table_order_is_unchanged).
    routes_static.register(app, ctx)

    routes_auth.register(app, ctx)
    routes_read.register(app, ctx)

    routes_operate.register(app, ctx)
    routes_admin.register(app, ctx)
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

    @route.get("/api/scorer", dependencies=guarded)
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

    @route.post("/api/scorer/preview")
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
        async with write_txn():
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

    @route.post("/api/scorer")
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
        async with write_txn():
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
        return {"status": "applied", "config_id": config_id, "params_hash": params_hash}

    @route.post("/api/scorer/rollback")
    async def rollback_scorer(
        body: ScorerRollbackIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """Point the active configuration at an earlier, immutable row. One call, no data lost."""
        now = time.time()
        async with write_txn():
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
        return {"status": "rolled back", "config_id": body.config_id}

    # -- admin: governance (v0.7.0) ----------------------------------------------------
    #
    # Two kinds, one shape. Each GET returns the active document, the resolved effect, and the
    # immutable history; each POST does exactly one of apply / rollback / clear, and every one of
    # the three is audited with before and after.

    def _policy_row(row: dict[str, Any]) -> dict[str, Any]:
        """One history row, projected. A policy is not a secret — it *is* the perimeter."""
        return {
            "id": int(row["id"]),
            "kind": row["kind"],
            "document": row["document"],
            "doc_hash": row["doc_hash"],
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "note": row["note"],
            "active": bool(row.get("active")),
        }

    async def _active_policy(kind: str) -> dict[str, Any] | None:
        active = await store.active_governance_ids()
        policy_id = active.get(kind)
        if policy_id is None:
            return None
        return await store.get_governance_policy(policy_id)

    def _canonical(document: dict[str, Any]) -> tuple[str, str]:
        """Canonical JSON plus its sha256 — the exact bytes `doc_hash` commits to."""
        text = json.dumps(document, sort_keys=True, separators=(",", ":"))
        return text, hashlib.sha256(text.encode("utf-8")).hexdigest()

    async def _write_policy(
        kind: str,
        body: PolicyIn,
        request: Request,
        principal: auth.Principal,
        action: str,
        problems: Callable[[str], list[str]],
    ) -> dict[str, Any]:
        """Apply, roll back, or clear one policy kind. The single write path for both kinds."""
        now = time.time()
        async with write_txn():
            before = await _active_policy(kind)
            before_summary = (
                {"policy_id": int(before["id"]), "doc_hash": before["doc_hash"]} if before else None
            )
            if body.clear:
                await store.clear_active_governance_policy(kind)
                after_summary: dict[str, Any] | None = None
                outcome = "cleared"
                policy_id = None
            elif body.policy_id is not None:
                target = await store.get_governance_policy(body.policy_id)
                if target is None or target["kind"] != kind:
                    raise HTTPException(status_code=404, detail="no such policy version")
                await store.set_active_governance_policy(kind, body.policy_id, principal.actor, now)
                after_summary = {"policy_id": body.policy_id, "doc_hash": target["doc_hash"]}
                outcome = "rolled back"
                policy_id = body.policy_id
            else:
                if body.document is None:
                    raise HTTPException(
                        status_code=400, detail="provide `document`, `policy_id`, or `clear`"
                    )
                text, doc_hash = _canonical(body.document)
                # Usability, NOT the security control: the resolver's intersection already makes
                # an above-ceiling entry inert. Rejecting here only means an admin finds out
                # immediately that a line will do nothing (DECISIONS #53).
                found = problems(text)
                if found:
                    raise HTTPException(status_code=400, detail="; ".join(found))
                policy_id = await store.insert_governance_policy(
                    kind, text, doc_hash, principal.actor, now, body.note
                )
                await store.set_active_governance_policy(kind, policy_id, principal.actor, now)
                after_summary = {"policy_id": policy_id, "doc_hash": doc_hash}
                outcome = "applied"
            await audit_row(
                request,
                principal,
                action,
                "ok",
                object_type="governance_policy",
                object_id=str(policy_id) if policy_id is not None else None,
                details={
                    "kind": kind,
                    "action": outcome,
                    "before": before_summary,
                    "after": after_summary,
                    "note_len": len(body.note),
                },
            )
        return {"status": outcome, "policy_id": policy_id}

    def _capability_problems(text: str) -> list[str]:
        return rbac.capability_policy_errors(rbac.parse_capability_policy(text))

    def _scope_problems(text: str) -> list[str]:
        return shaping.scope_policy_errors(shaping.parse_scope_policy(text))

    @route.get("/api/rbac", dependencies=guarded)
    async def get_rbac_policy() -> dict[str, Any]:
        """The capability policy, what it resolves to per role, and the immutable history.

        `ceiling` is shipped alongside `resolved` so an admin can see the wall as well as where
        they are standing: a capability in `ceiling` but not in `resolved` was taken away by
        policy, and one absent from `ceiling` can never be granted at all.
        """
        async with store.lock:
            await governance.load()
            history = await store.list_governance_policies("rbac", MAX_POLICY_HISTORY)
            active = await _active_policy("rbac")
        policy = governance.capability
        return {
            "kind": "rbac",
            "active": _policy_row(active) if active else None,
            "configured": active is not None,
            "malformed": bool(policy is not None and policy.malformed),
            "malformed_reason": policy.reason if policy is not None and policy.malformed else "",
            "ceiling": {role: sorted(rbac.ceiling(role)) for role in rbac.ROLE_RANK},
            "resolved": {
                role: sorted(rbac.resolve_capabilities(role, None, policy))
                for role in rbac.ROLE_RANK
            },
            "recovery_capabilities": sorted(rbac.RECOVERY_CAPABILITIES),
            "all_capabilities": sorted(rbac.PERMISSIONS),
            "history": [_policy_row(row) for row in history],
        }

    @route.post("/api/rbac")
    async def set_rbac_policy(
        body: PolicyIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """Apply, roll back, or clear the capability policy. Audited; reversible in one call.

        Nothing written here can escalate: the resolver intersects with the compiled ceiling, so
        the worst a hostile document achieves is taking capabilities away — and never the admin's
        recovery set (DECISIONS #64), so this endpoint stays reachable to undo it.
        """
        return await _write_policy(
            "rbac", body, request, principal, "rbac.policy.update", _capability_problems
        )

    @route.get("/api/scope", dependencies=guarded)
    async def get_scope_policy() -> dict[str, Any]:
        async with store.lock:
            await governance.load()
            history = await store.list_governance_policies("scope", MAX_POLICY_HISTORY)
            active = await _active_policy("scope")
            nes = await store.list_ne_for_scope()
        policy = governance.scope
        resolved = {
            role: sorted(shaping.visible_nes(role, None, policy, nes).ne_ids)
            for role in rbac.ROLE_RANK
            if role != "admin"
        }
        return {
            "kind": "scope",
            "active": _policy_row(active) if active else None,
            "configured": active is not None,
            "malformed": bool(policy is not None and policy.malformed),
            "malformed_reason": policy.reason if policy is not None and policy.malformed else "",
            "ne_count": len(nes),
            "resolved_ne_ids": resolved,
            "admin_is_never_scoped": True,
            "not_tenant_isolation": (
                "Visibility scoping is a presentation control, NOT tenant isolation. Correlation "
                "still learns across every network element, and a situation may still form across "
                "a boundary a principal cannot see — its members are then hidden from them, not "
                "prevented from correlating."
            ),
            "history": [_policy_row(row) for row in history],
        }

    @route.post("/api/scope")
    async def set_scope_policy(
        body: PolicyIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """Apply, roll back, or clear the visibility scope. Audited; reversible in one call."""
        return await _write_policy(
            "scope", body, request, principal, "scope.policy.update", _scope_problems
        )

    # -- admin: quarantine (the read itself is audited) --------------------------------

    @route.get("/api/quarantine")
    async def read_quarantine(
        request: Request, limit: int = 100, principal: auth.Principal = Depends(security)
    ) -> list[dict[str, Any]]:
        async with write_txn():
            rows = await store.list_quarantine(min(max(limit, 1), 500))
            await audit_row(
                request,
                principal,
                "quarantine.read",
                "ok",
                object_type="quarantine",
                details={"count": len(rows)},
            )
        return rows

    # -- admin: audit ------------------------------------------------------------------

    @route.get("/api/audit")
    async def read_audit(
        request: Request, limit: int = 200, principal: auth.Principal = Depends(security)
    ) -> list[dict[str, Any]]:
        async with write_txn():
            rows = await store.list_audit(min(max(limit, 1), 1000))
            await audit_row(request, principal, "audit.read", "ok", details={"count": len(rows)})
        return rows

    @route.get("/api/audit/export")
    async def export_audit(
        request: Request, principal: auth.Principal = Depends(security)
    ) -> Response:
        async with write_txn():
            lines, final_hash = await audit.export_ndjson(store)
            await audit_row(
                request, principal, "audit.export", "ok", details={"final_hash": final_hash}
            )
        body = "\n".join(lines) + ("\n" if lines else "")
        return Response(
            content=body,
            media_type="application/x-ndjson",
            headers={"X-Audit-Final-Hash": final_hash},
        )

    @route.post("/api/audit/prune")
    async def prune_audit(
        request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        async with write_txn():
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
        return {"status": "pruned", "removed": removed}

    # -- SSE: primary live-update path -------------------------------------------------

    @route.get("/api/events")
    async def events(principal: auth.Principal = Depends(security)) -> StreamingResponse:
        """The live stream, re-authorized and re-scoped on **every event**.

        A long-lived stream is the one place a perimeter change could go unnoticed: the security
        dependency runs once, at connect time, so a connection opened before a policy was written
        would otherwise keep pushing unfiltered snapshots for as long as it stayed open. Each
        snapshot therefore re-reads the live policy, re-resolves both the capability set and the
        scope, and **ends the stream** if `events.stream` has since been revoked (F30).
        """

        async def snapshot() -> str | None:
            async with store.lock:
                await governance.load()
            capabilities = rbac.resolve_capabilities(
                principal.role, principal.ref, governance.capability
            )
            if "events.stream" not in capabilities:
                return None  # revoked mid-stream: stop sending, rather than serve a stale grant
            scope = await scope_for(principal)
            async with store.lock:
                stats_out: dict[str, Any] = dict(
                    await store.stats()
                    if scope.unrestricted
                    else await store.scoped_stats(scope.ne_ids, scope.ips)
                )
                graph_out = await store.graph_snapshot(min_edge_n=MIN_EDGE_N)
                # F38 applies to the stream too: truncating globally would make a scoped
                # subscriber's live list a function of traffic they cannot see.
                sits = await store.list_situations(
                    "open", 50, None if scope.unrestricted else scope.ne_ids
                )
                members = (
                    {}
                    if scope.unrestricted
                    else await store.situation_member_nes([int(s["id"]) for s in sits])
                )
            stats_out["latency_p95_s"] = round(engine.latency_p95(), 4)
            stats_out["queue_depth"] = engine.queue.qsize()
            stats_out["warnings"] = all_warnings()
            if extra_stats is not None:
                stats_out.update(extra_stats())
            if not scope.unrestricted:
                graph_out = shaping.project_graph(graph_out, scope)
                scoped_sits = []
                for row in sits:
                    member_nes = members.get(int(row["id"]), [])
                    shown = sum(1 for ne_id in member_nes if scope.allows_ne(ne_id))
                    if shown:
                        scoped_sits.append(
                            {**row, "alarm_count": shown, "redacted_count": len(member_nes) - shown}
                        )
                sits = scoped_sits
            # Shape the live stream by the subscriber's role, exactly like the polled endpoints.
            payload = {
                "stats": stats_out,
                "graph": shaping.shape(graph_out, principal.role),
                "situations": shaping.shape(sits, principal.role),
            }
            return "event: update\ndata: " + json.dumps(payload) + "\n\n"

        async def gen() -> AsyncIterator[str]:
            yield ": connected\n\n"
            first = await snapshot()
            if first is None:
                return
            yield first
            last_beat = time.monotonic()
            while True:
                await asyncio.sleep(SSE_UPDATE_S)
                event = await snapshot()
                if event is None:
                    return
                yield event
                now = time.monotonic()
                if now - last_beat >= SSE_HEARTBEAT_S:
                    last_beat = now
                    yield ": heartbeat\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    return app
