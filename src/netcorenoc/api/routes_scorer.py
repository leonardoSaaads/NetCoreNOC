"""The v0.6.0 scoring seam's HTTP surface: read, preview, apply, roll back.

Reading the five parameters is `viewer+` and `unscoped` — they *explain* every grouping decision
and name no network element (SCOPE-0.6 §2). Writing or previewing them is a system-wide logic
change and is admin-only, with no editor delegation (DECISIONS #43).

`POST /api/scorer/preview` carries its own much tighter rate bucket on top of the per-client
limiter, because it is the one endpoint whose cost is worth more than a share of the general one
(F22). That bucket is built in `create_app` and arrives on `AppContext`; the perimeter's own
limiter is untouched.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request

from netcorenoc import auth, model_version, preview, scoring
from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.api.models import ScorerParamsIn, ScorerRollbackIn
from netcorenoc.api.perimeter import _client_ip

MAX_SCORER_HISTORY = 50
MAX_PROMOTION_HISTORY = 50

# `PREREGISTRATION-0.10.0.md` §2.2's registered floors, read here and never softened.
ASSERTING_BAGS_FLOOR = 50
ASSERTING_INCIDENTS_FLOOR = 30


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


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the scorer routes on `app`."""
    store, engine, security, guarded = ctx.store, ctx.engine, ctx.security, ctx.guarded
    preview_limiter = ctx.preview_limiter
    audit_row, write_txn = ctx.perimeter.audit_row, ctx.write_txn
    route = DeclaredRoutes(app)

    # -- the scoring seam (v0.6.0) -----------------------------------------------------

    def _tunable_scorer() -> scoring.AdditiveScorer:
        """The **five-parameter table the form edits**, post-fallback if degraded.

        Renamed from `_active_scorer` in v0.14.0, and the rename is the repair (F60). The old name
        claimed this was what the engine is scoring with; it is not, whenever a `model_version` is
        active. `scorer_config` and `model_version` are mutually exclusive by a database CHECK, so
        with a model version running `active_scorer_config()` is NULL and this returns the **coded
        defaults** — which the console then rendered as *"active configuration"*. That was a lie
        the screen presented as fact, and it predates the tree kinds: a promoted `logistic`
        champion produced it too.

        What is running is `_running_scorer()`. This is what an admin may retune, which is a
        different question and is now asked under a different name.
        """
        active = engine.correlator.scorer.active
        return active if isinstance(active, scoring.AdditiveScorer) else scoring.default_scorer()

    def _running_scorer() -> scoring.LinkScorer:
        """**What the engine is actually scoring with**, whatever kind it is."""
        return engine.correlator.scorer.active

    async def _running_identity() -> dict[str, Any]:
        """Who is deciding, said truthfully — the kind, the fingerprint, and the artefact if any.

        `kind` is derived from the running scorer's `scorer_id` against `SUPPORTED_KINDS` rather
        than from the `model_version` row, so a degraded fallback reports `additive` — which is
        what is running — instead of the kind of an artefact that failed to load.
        """
        running = _running_scorer()
        row = await store.active_model_version()
        kind = running.scorer_id if running.scorer_id in model_version.SUPPORTED_KINDS else "custom"
        return {
            "kind": kind,
            "scorer_id": running.scorer_id,
            "contract_version": running.contract_version,
            "params_hash": running.params_fingerprint(),
            "tunable": kind == model_version.KIND_ADDITIVE,
            "model_version": None
            if row is None
            else {
                "id": int(row["id"]),
                "kind": str(row["kind"]),
                "params_hash": str(row["params_hash"]),
                "challenger_run_id": row["challenger_run_id"],
                "created_at": row["created_at"],
                "created_by": row["created_by"],
                # The document itself, because the hyperparameters an operator wants to read are
                # inside it and `UI-0.13-DRAFT.md` §8 registers that *"exposing what is already
                # recorded is a far stronger design than inventing fields."* It is a parameter
                # set: it explains grouping and names no network element, which is the same
                # reasoning that makes the five additive numbers `viewer+` (SCOPE-0.6 §2).
                "params_document": str(row["params_document"]),
            },
        }

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
            running = await _running_identity()
        tunable = _tunable_scorer()
        safe = engine.correlator.scorer
        return {
            # **What is running**, and it is the running scorer's own identity even when that is
            # not an additive one (F60). `params`/`bounds` below describe the five-number table
            # the form edits, which is a different thing whenever `running.tunable` is false.
            "scorer_id": running["scorer_id"],
            "contract_version": running["contract_version"],
            "params_hash": running["params_hash"],
            "running": running,
            "supported_contract_version": scoring.CONTRACT_VERSION,
            "params": tunable.params(),
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
        active = _tunable_scorer()
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
