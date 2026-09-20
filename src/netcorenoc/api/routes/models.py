"""The models, as an operator asks about them: *who is deciding, is it learning, what is missing?*

## Why this route exists

Everything it serves was already in the database and none of it was reachable as one answer. An
operator who wanted to know whether confirming groupings was achieving anything had to read the
Judge screen (which said the corpus was insufficient), the Labelling screen (which said the figure
was *"deliberately not computed here"* and pointed at a `make` target needing shell access), and
then run that target. Three screens, a shell, and a report — for *"is this working?"*.

The answer is four numbers against four floors, a curve, and a name. This route is those.

## What it is not

**It is not evidence and it cannot promote anything.** Reading it writes nothing. The handover
below goes through `POST /api/promotion` unchanged: the server re-derives the floors, the power
condition, the sealed holdout and the verdict, and an admin's click is the human gesture. This
route makes that path *visible*; it does not make it shorter.

## The one new write, and why it does not widen the perimeter

`POST /api/models/register` turns a challenger run the appliance itself fitted into a
`model_version`. Until v0.19.0 the only way to do that was the CLI, on the stated ground that *the
thing which could put a new model in front of your traffic must not be reachable from the network*
— and that ground is honoured here rather than set aside. **The request body names a run id and
nothing else.** The coefficients are read out of the appliance's own `challenger_run` row; there
is no field in which a caller could assert a parameter, a kind or a contract version, so a request
cannot introduce a model the appliance did not fit. That is the same construction
`POST /api/promotion` already uses for its verdict, applied one table earlier.

The CLI path stays, unchanged, for a model fitted somewhere else. That one still cannot be reached
from the network, which is the part of the posture that was doing the work.
"""

from __future__ import annotations

import json
import time
from typing import Any, cast

from fastapi import Depends, FastAPI, HTTPException, Request

from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.api.models import ModelRegisterIn
from netcorenoc.crosscutting import auth
from netcorenoc.engine.correlate import scoring
from netcorenoc.engine.dataset.census import CorpusStats, corpus_stats, resolve_identity
from netcorenoc.engine.evaluation.shadow import FLOORS_META_KEY
from netcorenoc.engine.model import model_version
from netcorenoc.engine.model.sufficiency import Floors, resolve_floors

#: How many past fits the learning curve's history carries. A chart, not a ledger.
MAX_RUNS = 20

#: The four floors, in the order the console shows them, with the word an operator uses for each.
#: `PREREGISTRATION-0.10.0.md` §2.2 registered the numbers; these are only their names.
FLOOR_LABELS: tuple[tuple[str, str, str], ...] = (
    # "marked wrong" and not "corrected": this floor counts bags an operator **rejected**, and an
    # operator who has been confirming correct groupings all week will otherwise watch it sit at
    # zero without being told that confirmations are not what it counts.
    ("split_bags", "train_split_bags", "groupings you marked wrong"),
    ("mixed_bags", "train_mixed_bags", "groupings partly right"),
    ("incidents", "train_incidents", "separate incidents judged"),
    ("operators", "train_operators", "people who judged"),
)


def evidence_of(run: dict[str, Any] | None, stats: CorpusStats, floors: Floors) -> dict[str, Any]:
    """The four floors, what the corpus has against each **right now**, and whether they are met.

    **Counted live, and not read off the last training run.** Reading the row was the first cut
    and driving it found the defect: training runs every `TRAIN_EVERY_TICKS` maintenance ticks —
    five minutes — so an operator who judged a grouping and looked at the bar saw it unmoved, for
    the same five minutes, with nothing on screen saying why. A progress bar that lags the action
    it measures does not show progress; it shows staleness, and the operator concludes their work
    achieved nothing. That is precisely the thing this card exists to disprove.

    **It is still one implementation.** `corpus_stats` is the function `Shadow.train` calls, over
    the rows `store.labelled_bags` returns, stamped through the identity map `resolve_identity`
    builds — the same three, called in the same order. This route counts nothing itself, so the
    console and the challenger cannot drift apart; if they ever disagreed, one of them would be
    reading a different corpus, not applying a different rule.
    """
    have = {
        "split_bags": stats.split_bags,
        "mixed_bags": stats.mixed_bags,
        "incidents": stats.incidents,
        "operators": stats.operators,
    }
    items: list[dict[str, Any]] = []
    met = True
    for key, _column, label in FLOOR_LABELS:
        got, need = int(have[key]), int(getattr(floors, key))
        met = met and got >= need
        items.append({"key": key, "label": label, "have": got, "need": need})
    return {
        "floors": items,
        "met": met,
        # When the appliance last *trained* against this corpus, which is a different fact from
        # what the corpus holds now and is labelled as one. `None` and never `0`: a corpus nobody
        # has trained against is not one that was trained against at the epoch.
        "trained_at": run.get("started_at") if run else None,
    }


def learning_of(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """The loss curve of the newest run that actually fitted, plus the final loss of each.

    A run with `sufficient = 0` fitted nothing, so it has no curve and contributes no point. Those
    runs are still counted, because *"we tried nine times and never had enough evidence"* is the
    answer to a question an operator is entitled to ask.
    """
    fitted = [r for r in runs if r.get("loss_trace")]
    latest = fitted[0] if fitted else None
    trace: list[float] = []
    if latest:
        try:
            trace = [float(v) for v in json.loads(latest["loss_trace"])]
        except (TypeError, ValueError, json.JSONDecodeError):
            trace = []  # a malformed trace is no curve, never a curve of zeroes
    return {
        "attempts": len(runs),
        "fits": len(fitted),
        "trace": trace,
        "stride": (latest or {}).get("loss_trace_stride"),
        "iterations": (latest or {}).get("iterations"),
        "final_loss": trace[-1] if trace else None,
        "first_loss": trace[0] if trace else None,
        "fitted_at": (latest or {}).get("started_at"),
        "rows": (latest or {}).get("train_rows"),
        # The id an admin registers. Carried so the console's button needs no second call to
        # find out what it would be acting on.
        "run_id": (latest or {}).get("id"),
    }


#: The last corpus census, and the marker of the corpus it was taken over.
#:
#: **A cache, because the census is not cheap and the Overview is read constantly.** Counting the
#: corpus reads every labelled pair — 221 359 rows on the appliance this was measured on, 1.4 s —
#: and it runs under `store.lock`, so doing it per page load would put a second and a half of
#: ingestion stall behind every operator who opened the console.
#:
#: **The marker is what makes it still live.** It is the row count and highest id of the two
#: tables a judgement writes, which is two aggregate queries the planner answers from indexes. A
#: verdict changes it, so the very next read recomputes and the operator sees their own click
#: counted; nothing else changes it, so every other read is free. The alternative — a TTL — would
#: have made the bar lag the click by exactly the interval chosen, which is the defect this card
#: exists to avoid.
_CENSUS: dict[str, Any] = {"marker": None, "stats": None}


async def _corpus_marker(store: Any) -> tuple[int, int, int, int]:
    """`(feedback rows, newest feedback id, gesture rows, newest gesture id)`.

    Both tables, because a verdict and a gesture both change the counts and either alone would
    let the other go unnoticed. `MAX(id)` as well as `COUNT(*)`: retention can prune rows, and a
    corpus that lost one row and gained one would otherwise look unchanged.
    """
    cur = await store.conn.execute(
        "SELECT (SELECT COUNT(*) FROM feedback), (SELECT COALESCE(MAX(id), 0) FROM feedback), "
        "       (SELECT COUNT(*) FROM situation_event), "
        "       (SELECT COALESCE(MAX(id), 0) FROM situation_event)"
    )
    row = await cur.fetchone()
    return (int(row[0]), int(row[1]), int(row[2]), int(row[3]))


async def _corpus(store: Any) -> CorpusStats:
    """The census, recomputed only when a judgement has changed the corpus since the last one.

    The three calls are `Shadow.train`'s own, in its order — this module counts nothing itself,
    so the console and the challenger cannot apply different rules. The caller holds `store.lock`.
    """
    marker = await _corpus_marker(store)
    cached = _CENSUS["stats"]
    if cached is not None and _CENSUS["marker"] == marker:
        return cast(CorpusStats, cached)
    bags = await store.labelled_bags()
    pairs = await store.labelled_pairs()
    gestures = await store.gesture_positive_pairs()
    identity = await resolve_identity(store, bags, pairs + gestures)
    stats = corpus_stats(bags, identity)
    _CENSUS["marker"], _CENSUS["stats"] = marker, stats
    return stats


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the model routes on `app`."""
    store, engine, security, guarded = ctx.store, ctx.engine, ctx.security, ctx.guarded
    audit_row, write_txn = ctx.perimeter.audit_row, ctx.write_txn
    route = DeclaredRoutes(app)

    @route.get("/api/models", dependencies=guarded)
    async def get_models() -> dict[str, Any]:
        """**Who is deciding, whether it is learning, and what is missing.** `viewer+`, unscoped.

        Unscoped for the same reason `/api/scorer` and `/api/correlation` are: every figure is a
        statement about arithmetic and about counts of the operator's own judgements. Nothing here
        names a network element, so there is nothing for visibility scoping to scope.
        """
        async with store.lock:
            runs = await store.challenger_runs(MAX_RUNS)
            versions = await store.list_model_versions(MAX_RUNS)
            raw_floors = await store.get_meta(FLOORS_META_KEY)
            stats = await _corpus(store)
        floors, _warning = resolve_floors(raw_floors)
        rows = [dict(r) for r in runs]
        newest = rows[0] if rows else None
        learning = learning_of(rows)
        evidence = evidence_of(newest, stats, floors)
        registered = [dict(v) for v in versions]
        # A run is registerable once it fitted and is not already registered — the console needs
        # both facts to decide whether its button does anything, and deriving them here keeps the
        # two ends from disagreeing about it.
        already = {v.get("challenger_run_id") for v in registered}
        return {
            "deciding": {
                "kind": "model" if engine.scorer_model_version_id else "formula",
                "model_version_id": engine.scorer_model_version_id,
                "config_id": engine.scorer_config_id,
                "automatic": bool(engine.scorer_model_version_id),
            },
            "learning": learning,
            "evidence": evidence,
            "registered": len(registered),
            "registerable_run_id": (
                learning["run_id"]
                if learning["run_id"] is not None and learning["run_id"] not in already
                else None
            ),
            # The newest registered artefact, which is what a handover would name. Derived here
            # rather than left to the console to pick, so the button and the route agree about
            # which version the click means — `list_model_versions` is newest-first.
            "promotable_version_id": (
                int(registered[0]["id"])
                if registered and registered[0]["id"] != engine.scorer_model_version_id
                else None
            ),
        }

    @route.post("/api/models/register")
    async def register_fit(
        body: ModelRegisterIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """**Turn one of this appliance's own fits into a registerable artefact.** Admin-only.

        Registering is not promoting. This moves no pointer and puts nothing in front of traffic;
        it makes a `model_version` that `POST /api/promotion` can then be asked about, and that
        request is judged on its own evidence exactly as before.

        The body names `challenger_run_id`. The coefficients come from that row.

        **The admin check is the route table's**, not a line here. `model.register` has minimum
        role `admin` in `PERMISSIONS`, and the perimeter enforces that before this body runs —
        the same construction `POST /api/promotion` uses. A second check in the handler would be
        a second place the answer could be got wrong.
        """
        now = time.time()
        async with write_txn():
            run = await store.challenger_run(body.challenger_run_id)
            if run is None:
                raise HTTPException(404, "no such training run")
            document = run["coefficients"]
            if not document:
                raise HTTPException(
                    409,
                    "that run fitted nothing, so there are no coefficients to register. A run "
                    "fits only once the corpus clears the registered floors.",
                )
            kind = "logistic"
            try:
                model_version.validate_document(kind, scoring.CONTRACT_VERSION, document)
            except model_version.ModelPayloadError as exc:
                # The appliance's own fit failing its own validator is a defect in the fit, not a
                # bad request, and saying so is more useful than a 400 that blames the caller.
                raise HTTPException(
                    500, f"this appliance's own fit is not loadable: {exc}"
                ) from exc
            new_id = await store.insert_model_version(
                kind=kind,
                contract_version=scoring.CONTRACT_VERSION,
                params_document=document,
                params_hash=model_version.params_hash(kind, scoring.CONTRACT_VERSION, document),
                challenger_run_id=int(run["id"]),
                created_by=principal.actor,
                created_at=now,
                note="registered from the console",
            )
            await audit_row(
                request,
                principal,
                "model.registered",
                "ok",
                object_type="model_version",
                object_id=str(new_id),
                details={
                    "challenger_run_id": int(run["id"]),
                    "kind": kind,
                    "params_hash": model_version.params_hash(
                        kind, scoring.CONTRACT_VERSION, document
                    ),
                    "registered_at": now,
                },
            )
        return {"model_version_id": new_id, "kind": kind, "challenger_run_id": int(run["id"])}
