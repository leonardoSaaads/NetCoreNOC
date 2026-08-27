"""The promotion surface: propose, and read what has been proposed (v0.11.0).

**The apply/rollback shape of `routes_scorer.py`, reused deliberately** — validate, write an
immutable row, move the pointer, write an audit row carrying `before` and `after`. Promotion is a
configuration change and this project has had change control for one since v0.6.0.

**One difference, and it is the v0.9.2 evidence boundary applied to promotion:**

> The server re-derives the verdict. The request body may name a `model_version_id`; **it may not
> assert a verdict.**

`PromotionIn` has no `verdict` field, no `metrics` field and no `floors_met` field. The enforcement
is that the fields do not exist, not that a handler remembers to ignore them.

## Why this is its own module

`routes_scorer.py` reached 457 lines against the 400-line guard once the promotion routes were
added, and MODULE-ARCHITECTURE §3 records that the `api` package is a decided shape rather than an
accident. The seam is real: `routes_scorer` owns the **additive parameter table an admin retunes by
hand**, and this owns **the artefact inventory and the decisions taken about it** — the same
distinction migration `0013` draws between `scorer_config` and `promotion`.

## No UI, and the reason is measured (DECISIONS #163)

Route plus CLI. The audit catalog is reopening in this same release, and the v0.7.5 defect — the
most expensive in this project's history — was a click gesture in a UI **no test executes to this
day**. A promotion screen where a mis-click swaps the correlator is that failure mode with a
strictly larger consequence.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request

from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.api.models import PromotionIn
from netcorenoc.crosscutting import auth
from netcorenoc.engine.dataset import incidents, seal
from netcorenoc.engine.evaluation import promotion, promotion_metrics
from netcorenoc.engine.evaluation.shadow_cv import power_at
from netcorenoc.engine.model import model_version
from netcorenoc.scorer_contract import LinkScorer

MAX_PROMOTION_HISTORY = 50

# `PREREGISTRATION-0.10.0.md` §2.2's registered floors, read here and never softened. `resolved =
# the more demanding of (project floor, deployment policy)`, and this release ships no policy that
# could move them in either direction.
ASSERTING_BAGS_FLOOR = 50
ASSERTING_INCIDENTS_FLOOR = 30


def _params_of(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """The active additive configuration, for the `before` half of the audit detail."""
    if row is None:
        return None
    return {"config_id": int(row["id"]), "params_hash": row["params_hash"]}


def _candidate_scorer(candidate: Mapping[str, Any]) -> LinkScorer:
    """The scorer the candidate row describes, or a 400 naming why it will not load (F59).

    `model_version.scorer_for` is **the** dispatch — the same one `scorer_lifecycle` uses to
    activate a row — so the model scored at the gate and the model that would run after the pointer
    moves are built by one function from one document. That identity is the whole repair; anything
    weaker leaves the two able to drift.

    A `ModelPayloadError` here is the candidate's own document failing its own kind's validator, so
    it is a 400 and not a 500: the request named a row this appliance cannot build, and saying which
    is more useful than a verdict about it.
    """
    try:
        return model_version.scorer_for(
            str(candidate["kind"]),
            str(candidate["contract_version"]),
            str(candidate["params_document"]),
        )
    except model_version.ModelPayloadError as exc:
        raise HTTPException(
            status_code=400, detail=f"the candidate model version will not load: {exc}"
        ) from exc


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the promotion routes on `app`."""
    store, engine, security, guarded = ctx.store, ctx.engine, ctx.security, ctx.guarded
    audit_row, write_txn = ctx.perimeter.audit_row, ctx.write_txn
    route = DeclaredRoutes(app)

    async def _derived_inputs(
        candidate: Mapping[str, Any],
    ) -> tuple[bool, list[int], int, int, promotion.Metrics, list[str]]:
        """**Everything the verdict depends on, derived by the server.** Never from a request.

        Returns (floors_met, incident ids, asserting bags, asserting incidents, metrics,
        unavailable).

        **v0.14.0 computes the four named quantities for real.** v0.11.0 returned them degenerate
        and said so, because this project's corpus supplied zero asserted negative pairs and
        *inventing a quantity whose input is gone is fabrication*. That is still the behaviour when
        the input is genuinely absent — `promotion_metrics.measure` returns degenerate intervals and
        a stated reason — but a corpus that does supply them now gets real numbers, for both arms,
        from one code path.

        ## The challenger arm is the CANDIDATE, and this is the repair of F59

        v0.11.0 scored `engine.shadow.scorer` — the model the engine happens to hold in shadow —
        while `propose_promotion` activates the `model_version_id` the **request** named. Nothing
        bound the two together: no check that the candidate's parameters are the shadow scorer's,
        and `params_hash` reaches `promotion.evaluate` only as a fold-materialisation key. So a
        promotion could be applied on the strength of metrics measured against a different model,
        which re-opens by the back door exactly the door `PromotionIn` closes at the front. The
        module docstring's own sentence is the standard it failed: *the request body may name a
        `model_version_id`; it may not assert a verdict* — and a verdict about a model nobody
        measured is worse than an asserted one, because it looks derived.

        So the arm measured is **the scorer the candidate row describes**, built by
        `model_version.scorer_for`, which is the one dispatch and covers all five kinds since
        v0.14.0. Before this release it covered two, which is part of why the gap survived: a
        `tree` candidate could not have been loaded here at all.

        A candidate whose document will not load is not scored against a substitute. It is refused,
        by the same `ModelPayloadError` the activation path would raise — because a model that
        cannot be built cannot be promoted, and discovering that at the gate is strictly better than
        discovering it when the pointer moves.
        """
        rows = await store.asserting_bag_rows()
        cur = await store.conn.execute("SELECT id, merged_into FROM situation ORDER BY id")
        situations = [(int(r[0]), r[1]) for r in await cur.fetchall()]
        resolved = incidents.resolve_all(
            [sid for sid, _ in situations],
            {sid: int(m) for sid, m in situations if m is not None},
        )
        cur = await store.conn.execute("SELECT situation_id FROM feedback ORDER BY situation_id")
        labelled = [int(r[0]) for r in await cur.fetchall()]
        incident_ids = sorted({resolved.incident_of.get(s, s) for s in labelled})
        asserting_incidents = len(
            {resolved.incident_of.get(int(r["situation_id"]), 0) for r in rows}
        )
        floors_met = (
            len(rows) >= ASSERTING_BAGS_FLOOR and asserting_incidents >= ASSERTING_INCIDENTS_FLOOR
        )
        measured = await promotion_metrics.measure(store, _candidate_scorer(candidate))
        return (
            floors_met,
            incident_ids,
            len(rows),
            asserting_incidents,
            measured.metrics,
            list(measured.unavailable),
        )

    @route.get("/api/promotion", dependencies=guarded)
    async def get_promotion() -> dict[str, Any]:
        """What has been proposed, and why each was refused. **Refusals included** — that is the
        audit question this table exists to answer."""
        async with store.lock:
            history = await store.list_promotions(MAX_PROMOTION_HISTORY)
            versions = await store.list_model_versions(MAX_PROMOTION_HISTORY)
            summary = await seal.summary(store)
        return {
            "promotions": [dict(row) for row in history],
            "model_versions": [dict(row) for row in versions],
            "active_model_version_id": engine.scorer_model_version_id,
            "active_config_id": engine.scorer_config_id,
            # §4.3(4): printed beside every holdout number this project ever publishes.
            "seal_query_count": summary.query_count,
            "plan_sha256": promotion.RATIFIED_PLAN_SHA256,
        }

    @route.post("/api/promotion")
    async def propose_promotion(
        body: PromotionIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """**Propose a promotion. The server decides.** Admin-only, and never automatic.

        The body names a `model_version_id`. Everything that decides — the floors, the power
        condition, the seal, the verdict, the metrics — is re-derived here. A refusal writes a
        `promotion` row exactly as an application does.
        """
        now = time.time()
        async with write_txn():
            candidate = await store.get_model_version(body.model_version_id)
            if candidate is None:
                raise HTTPException(status_code=404, detail="no such model version")

            (
                floors_met,
                incident_ids,
                bags,
                asserting,
                metrics,
                unavailable,
            ) = await _derived_inputs(candidate)
            # **The observed difference is derived, not assumed.** v0.11.0 passed a literal 0.0
            # because no quantity could be computed; with real metrics the power condition has to
            # see the difference it is deciding about, or `Trigger.POWER` fires on a corpus that
            # could in fact resolve it. The larger of the two headline differences is used, which
            # is the one the detection threshold has to clear.
            observed = max(
                abs(metrics.by_name(name).difference) for name in promotion.HEADLINE_QUANTITIES
            )
            decision = await promotion.evaluate(
                store,
                floors_met=floors_met,
                power=power_at(len(incident_ids), observed),
                metrics=metrics,
                incident_ids=incident_ids,
                params_hash=str(candidate["params_hash"]),
                plan_sha256=promotion.RATIFIED_PLAN_SHA256,
                release=__import__("netcorenoc").__version__,
                now=now,
                asserting_incidents=asserting,
                # The smallest side of the comparison, in INCIDENTS — the unit every clustered
                # estimate is expressed in. With no asserting incidents it is 0 and `THIN_SPLIT`
                # fires, which is correct: a split nobody reported has fewer than ten incidents on
                # one side of it. Passing it explicitly rather than leaving `evaluate`'s default is
                # what makes that a DERIVED zero instead of an unset parameter.
                smallest_side=asserting,
            )
            unavailable.extend(decision.unavailable)
            decision = promotion.Decision(
                judgement=decision.judgement,
                metrics=decision.metrics,
                evaluation_run_id=decision.evaluation_run_id,
                plan_sha256=decision.plan_sha256,
                unavailable=tuple(unavailable),
            )

            # A promotion may only be APPLIED with a challenger run behind it. The schema cannot
            # tell a registration from a proposal, so the rule lives here — and it is a refusal
            # with a reason, never a silent skip.
            missing_run = candidate["challenger_run_id"] is None
            applied = decision.may_apply and not missing_run
            reason: str | None = None
            if not decision.may_apply:
                reason = promotion.refusal_for(decision)
            elif missing_run:
                reason = promotion.missing_run_refusal(decision, body.model_version_id)

            before = _params_of(await store.active_scorer_config())
            promotion_id = await store.insert_promotion(
                model_version_id=body.model_version_id,
                verdict=decision.verdict.value,
                triggers=promotion.triggers_document(decision.judgement),
                metrics=decision.metrics.as_document(),
                evaluation_run_id=decision.evaluation_run_id,
                plan_sha256=decision.plan_sha256,
                query_count=decision.judgement.query_count,
                approved_by=principal.actor,
                decided_at=now,
                outcome="applied" if applied else "refused",
                refusal_reason=reason,
                unavailable=promotion.unavailable_document(decision),
            )
            # §6.8: a pointer move that fails must leave the promotion row and the active pointer
            # unchanged, so the failure is recorded rather than half-applied. **A promotion that
            # half-applied would be the worst state this system could reach.**
            if applied and not await store.set_active_model_version(
                body.model_version_id, principal.actor, now
            ):
                raise HTTPException(status_code=409, detail="the model version disappeared")
            await audit_row(
                request,
                principal,
                "promotion.applied" if applied else "promotion.refused",
                "ok",
                object_type="model_version",
                object_id=str(body.model_version_id),
                details={
                    "promotion_id": promotion_id,
                    "verdict": decision.verdict.value,
                    "triggers": [t.name for t in decision.judgement.triggers],
                    "before": before,
                    "after": (
                        {"model_version_id": body.model_version_id, "kind": candidate["kind"]}
                        if applied
                        else None
                    ),
                    "params_hash": candidate["params_hash"],
                    "evaluation_run_id": decision.evaluation_run_id,
                    "query_count": decision.judgement.query_count,
                    "asserting_bags": bags,
                    "note_len": len(body.note),
                },
            )
        return {
            "status": "applied" if applied else "refused",
            "promotion_id": promotion_id,
            "verdict": decision.verdict.value,
            "triggers": [t.name for t in decision.judgement.triggers],
            "reason": reason,
            "evaluation_run_id": decision.evaluation_run_id,
            "seal_query_count": decision.judgement.query_count,
            "unavailable": list(decision.unavailable),
        }
