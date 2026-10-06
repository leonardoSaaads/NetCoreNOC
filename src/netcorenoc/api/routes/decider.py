"""What decides links, how autonomous it is, and how it is judged — v0.26.0's HTTP surface.

Five groups, one module, because the console's two redesigned screens read them together:

* ``/api/decider`` — which family decides (shipped model, site-adapted model, additive formula),
  the shipped model's provenance, and the history of switches. Switching is admin-only and needs a
  reason; switching to a **site** model re-derives its judgement on the server, and only a
  `BETTER` verdict is accepted — the request can name a mode, never assert a verdict.
* ``/api/autonomy`` — status and settings (every role reads the status: the top bar shows it on
  every screen), admin-only settings, and ``/api/autonomy/stop``, **the kill switch**, which an
  editor may press: stopping autonomy is cheap to grant, starting it is not.
* ``/api/situations/{sid}/severity`` — an operator's severity for a situation, scoped like every
  other gesture on one.
* ``/api/search`` — start, stop and read the in-product hyperparameter search.
* ``/api/judge`` — the Judge screen's data: the shipped model's measured numbers (**generated
  data**), this site's search runs and verdicts (**site data**), and the live monitor. Each block
  names its dataset, so the console can never put the two claims on one unlabelled axis.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request

from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.api.league_view import brief_block, decider_payload, finite, member_block
from netcorenoc.api.models import AutonomyIn, DeciderIn, SearchIn, SituationSeverityIn
from netcorenoc.api.shipped_view import search_blocked
from netcorenoc.crosscutting import auth
from netcorenoc.engine.model import site, site_labels, site_search
from netcorenoc.engine.operate import autonomy

__all__ = ["register"]


def register(app: FastAPI, ctx: AppContext) -> None:
    store, engine, security, guarded = ctx.store, ctx.engine, ctx.security, ctx.guarded
    audit_row, write_txn, scope_for = ctx.perimeter.audit_row, ctx.write_txn, ctx.scope_for
    situation_in_scope = ctx.perimeter.situation_in_scope
    audit_scope_denial = ctx.perimeter.audit_scope_denial
    route = DeclaredRoutes(app)

    # -- the decider -------------------------------------------------------------------------

    @route.get("/api/decider", dependencies=guarded)
    async def get_decider() -> dict[str, Any]:
        """Who decides links now: the league's champion, the order it was chosen from, the pin, and
        every switch. `viewer+`, unscoped: it is about models and names no network element."""
        async with store.lock:
            history = await store.decider_history(20)
            pinned = await store.decider_pin()
            decisions = await store.league_decisions(10)
        return {**decider_payload(engine, pinned), "history": history, "decisions": decisions}

    @route.post("/api/decider")
    async def set_decider(
        body: DeciderIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """Pin one member of the league, or hand the choice back to the judge. Admin
        (`decider.write`); effective at the next reload. The formula and the site mode are retired
        as deciders (ADR #425) and are refused with the reason."""
        if body.mode != "shipped":
            raise HTTPException(
                409,
                f"the {body.mode!r} mode is retired: the league's judge chooses among the models, "
                "and an admin may pin one of them",
            )
        members = engine.league_members
        if body.pin is not None and (members is None or members.by_ref(body.pin) is None):
            raise HTTPException(409, f"{body.pin} is not a member of this appliance's league")
        now = time.time()
        async with write_txn():
            await store.set_decider_pin(body.pin, principal.actor, now, body.reason)
            await audit_row(
                request,
                principal,
                "decider.set",
                "ok",
                object_type="decider",
                object_id=body.pin or "judge",
                details={"pin": body.pin, "reason": body.reason},
            )
        return {
            "pin": body.pin,
            "effective": "at the next maintenance pass, within seconds",
        }

    # -- autonomy ----------------------------------------------------------------------------

    @route.get("/api/autonomy", dependencies=guarded)
    async def get_autonomy() -> dict[str, Any]:
        """Status for every screen's top bar, and the settings. `viewer+`, unscoped: counts and
        switches, no situation named."""
        async with store.lock:
            state = await autonomy.status(engine)
            settings = autonomy.Settings.from_row(await store.autonomy_setting())
            history = await store.autonomy_history(10)
        return {**state, "settings": settings.values(), "history": history}

    @route.get("/api/autonomy/decisions", dependencies=guarded)
    async def autonomy_decisions() -> dict[str, Any]:
        """Every recent autonomous act with its explanation and verdict. Admin (it names
        situations across every scope)."""
        async with store.lock:
            rows = await store.autonomy_decisions(limit=100)
        return {"decisions": rows}

    @route.post("/api/autonomy")
    async def set_autonomy(
        body: AutonomyIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """Switch grades on or off and set the self-suspension trigger. Admin."""
        now = time.time()
        values = body.model_dump(exclude={"reason"})
        values = {k: (int(v) if isinstance(v, bool) else v) for k, v in values.items()}
        async with write_txn():
            new_id = await store.set_autonomy(values, principal.actor, now, body.reason)
            await audit_row(
                request,
                principal,
                "autonomy.set",
                "ok",
                object_type="autonomy",
                object_id=str(new_id),
                details={**values, "reason": body.reason},
            )
        return {"id": new_id, **values}

    @route.post("/api/autonomy/stop")
    async def stop_autonomy(
        request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """**The kill switch.** Every grade off, now, attributed. Editor+ (`autonomy.stop`).
        Idempotent: when nothing is on, it writes nothing."""
        now = time.time()
        async with write_txn():
            current = autonomy.Settings.from_row(await store.autonomy_setting())
            if not current.enabled:
                # Already off: pressing the kill switch again changes nothing and writes nothing,
                # so two operators hitting it in the same second leave one row, not two.
                return {"id": None, "active": False}
            off = current.values(grouping=0, naming=0, closing=0, severity=0)
            new_id = await store.set_autonomy(off, principal.actor, now, "stopped from the console")
            await audit_row(
                request,
                principal,
                "autonomy.stop",
                "ok",
                object_type="autonomy",
                object_id=str(new_id),
                details={"was": list(current.enabled)},
            )
        return {"id": new_id, "active": False}

    @route.post("/api/situations/{sid}/severity")
    async def situation_severity(
        sid: int,
        body: SituationSeverityIn,
        request: Request,
        principal: auth.Principal = Depends(security),
    ) -> dict[str, str]:
        """An operator's severity for a situation. Scoped: out of scope and absent are one 404."""
        scope = await scope_for(principal)
        if not await situation_in_scope(sid, scope):
            await audit_scope_denial(
                request, principal, "situation.severity", "situation", str(sid)
            )
            raise HTTPException(404, "no such situation")
        async with write_txn():
            await store.set_situation_severity(sid, body.severity, principal.actor)
            await audit_row(
                request,
                principal,
                "situation.severity",
                "ok",
                object_type="situation",
                object_id=str(sid),
                details={"severity": body.severity},
            )
        return {"severity": body.severity}

    # -- the search --------------------------------------------------------------------------

    @route.get("/api/search", dependencies=guarded)
    async def get_search() -> dict[str, Any]:
        """Recent searches and every trial of the newest. `viewer+`, unscoped. ``blocked`` is why a
        search cannot start on this build (no shipped model to adapt, #422), else ``None``."""
        async with store.lock:
            runs = await store.search_runs(10)
            trials = await store.search_trials(int(runs[0]["id"])) if runs else []
        return {
            "runs": runs,
            "trials": trials,
            "busy": engine.search_runner.busy,
            "blocked": search_blocked(),
            "defaults": {
                "trials": site_search.DEFAULT_BUDGET.trials,
                "max_rounds": site_search.DEFAULT_BUDGET.max_rounds,
                "minutes": int(site_search.DEFAULT_BUDGET.seconds // 60),
            },
        }

    @route.post("/api/search")
    async def start_search(
        body: SearchIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """Start a search. Admin (`search.write`). One at a time: a second is a 409. A search
        adapts the shipped model, so a build that carries none refuses here, with the reason, rather
        than accepting a run the runner would refuse a tick later (#422)."""
        now = time.time()
        blocked = search_blocked()
        if blocked is not None:
            raise HTTPException(409, blocked)
        async with write_txn():
            runs = await store.search_runs(1)
            if runs and runs[0]["status"] == "running":
                raise HTTPException(409, "a search is already running; stop it first")
            budget = {
                "trials": body.trials,
                "max_rounds": body.max_rounds,
                "min_rounds": max(10, body.max_rounds // 9),
                "eta": 3,
                "seconds": float(body.minutes * 60),
            }
            run_id = await store.open_search_run(
                by=principal.actor, seed=body.seed, budget=budget, at=now
            )
            await audit_row(
                request,
                principal,
                "search.start",
                "ok",
                object_type="search_run",
                object_id=str(run_id),
                details=budget,
            )
        return {"run_id": run_id, "status": "running"}

    @route.post("/api/search/stop")
    async def stop_search(
        request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """Stop the running search. Admin. The worker stops at its next round."""
        now = time.time()
        async with write_txn():
            runs = await store.search_runs(1)
            if not runs or runs[0]["status"] != "running":
                raise HTTPException(409, "no search is running")
            run_id = int(runs[0]["id"])
            await store.finish_search_run(run_id, "stopped", now, f"stopped by {principal.actor}")
            await audit_row(
                request,
                principal,
                "search.stop",
                "ok",
                object_type="search_run",
                object_id=str(run_id),
                details={},
            )
        return {"run_id": run_id, "status": "stopped"}

    # -- the judge ---------------------------------------------------------------------------

    @route.get("/api/judge", dependencies=guarded)
    async def get_judge(brief: bool = False) -> dict[str, Any]:
        """The Judge screen: every member's measurements (**generated**), the slow loop's paired
        comparisons on this site's labels (**site**), and the two loops' counters (**live**).

        ``brief`` (v0.29.0) is the Overview's form: who decides, each member's ROC and the live
        shadow — a few KiB and no read of the site's labels, against ~240 KiB for the full body.
        """
        if brief:
            async with store.lock:
                pinned_ref = await store.decider_pin()
            league_members = engine.league_members
            return {
                **decider_payload(engine, pinned_ref),
                "models": []
                if league_members is None
                else [brief_block(m) for m in league_members.members],
                "shadow": finite(engine.league_shadow.snapshot()),
            }
        async with store.lock:
            runs = await store.search_runs(10)
            trials = await store.search_trials(int(runs[0]["id"])) if runs else []
            pairs = await site_labels.label_pairs(store)
            features = await store.pair_features([int(p["pair_id"]) for p in pairs])
            pinned = await store.decider_pin()
            decisions = await store.league_decisions(20)
            proposals = await store.proposal_stats()
        rows = site.rows_from(pairs, features)
        _train, test = site.split_by_time(rows)
        members = engine.league_members
        latency = engine.latency_us
        return {
            **decider_payload(engine, pinned),
            "models": []
            if members is None
            else [member_block(m, latency.get(m.ref)) for m in members.members],
            "judgement": finite(engine.league_view),
            "decisions": decisions,
            "site": finite(
                {
                    "dataset": "site",
                    "evidence": site.evidence(rows, test),
                    "proposals": proposals,
                    "runs": runs,
                    "trials": trials,
                    "labelled_pairs_without_features": len(pairs)
                    - sum(1 for p in pairs if int(p["pair_id"]) in features),
                }
            ),
            "shadow": finite(engine.league_shadow.snapshot()),
            "live": {"dataset": "live", **engine.monitor.snapshot()},
        }
