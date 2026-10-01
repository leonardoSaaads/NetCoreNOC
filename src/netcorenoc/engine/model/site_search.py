"""The in-product search: admin-started, stoppable, in its own process, recorded per trial.

v0.26.0 (ADR #413). Part V: *"hyperparameter search lives in the product … admin-configurable in
the console with a stop button, running where the maintenance loop runs, never blocking ingestion
or HTTP."* The fit is pure-Python arithmetic, so a thread would hold the GIL for tenths of a
second at a time and slow ingestion down with it. So the fit runs in a **separate process**
(`multiprocessing`, spawn context): the event loop only moves rows in, reads trials out, and writes
them — each step a short transaction on the maintenance cadence.

The lifecycle, one `search_run` row per search:

1. an admin starts one (`POST /api/search`) — a row with status ``running`` and the budget;
2. the next maintenance tick reads the site's labelled pairs, splits them by time, and starts the
   worker; too little labelled data refuses the run (``refused``) with the reason;
3. every tick drains finished trials into `search_trial`;
4. when the worker returns its best model, the tick judges it against the shipped model
   (`site.judge`), registers it as a `model_version` of kind `gam`, and finishes the run with the
   judgement. **It never activates it**: switching to the site model is an admin's act, and the
   server re-derives the verdict when they ask;
5. ``POST /api/search/stop`` marks the run ``stopped``; the next tick stops the worker.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import queue
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from netcorenoc.engine.correlate.features import FEATURE_NAMES
from netcorenoc.engine.correlate.scorer_contract import CONTRACT_VERSION
from netcorenoc.engine.model import gam, gam_fit, league, search, shipped, site, site_labels

if TYPE_CHECKING:  # pragma: no cover - type-only
    from multiprocessing.process import BaseProcess

    from netcorenoc.engine.operate.engine import Engine

__all__ = ["DEFAULT_BUDGET", "MIN_BAGS", "Runner"]

log = logging.getLogger("netcorenoc")

DEFAULT_BUDGET = search.Budget(trials=6, min_rounds=20, max_rounds=120, eta=3, seconds=1800.0)
MIN_BAGS = 4  # below this there is nothing to fit, and the run is refused rather than attempted


def _worker(payload: dict[str, Any], out: Any, stop: Any) -> None:  # pragma: no cover - subprocess
    """The child process: build the data, run the search, report trials and the final fit."""
    try:
        base = gam.validate(payload["base"])
        feats = base.features

        def ds(rows: list[list[float]]) -> gam_fit.Dataset:
            picks = [payload["names"].index(f) for f in feats]
            return gam_fit.Dataset.from_rows(
                feats, [r[2:] for r in rows], [int(r[0]) for r in rows], [r[1] for r in rows], picks
            )

        train, valid = ds(payload["train"]), ds(payload["valid"])
        runner = search.Search(
            train,
            valid,
            search.Budget(**payload["budget"]),
            seed=int(payload["seed"]),
            stop=stop.is_set,
            on_trial=lambda t: out.put(("trial", t.as_dict())),
            base=base,
        )
        runner.run()
        best = runner.best()
        if best is None or stop.is_set():
            out.put(("done", None))
            return
        final = gam_fit.fit(
            train,
            valid,
            search._params(best.params, payload["budget"]["max_rounds"], int(payload["seed"])),
            edges={s.feature: s.edges for s in base.shapes},
            base=base,
            threshold=base.threshold,
            grouping=dict(base.grouping),
            stop=stop.is_set,
        )
        out.put(("done", {"document": final.document, "best": best.as_dict()}))
    except Exception as exc:  # reported, never raised across the process boundary
        out.put(("error", f"{type(exc).__name__}: {exc}"))


@dataclass
class _Job:
    run_id: int
    process: BaseProcess
    out: Any
    stop: Any
    train: list[site.SiteRow]
    test: list[site.SiteRow]
    started: float


class Runner:
    """Owns at most one worker. Driven by :meth:`tick` from the maintenance loop."""

    def __init__(self) -> None:
        self.job: _Job | None = None
        self._ctx = mp.get_context("spawn")

    @property
    def busy(self) -> bool:
        return self.job is not None

    async def tick(self, engine: Engine, now: float) -> None:
        """One step. Caller holds ``store.lock`` and commits."""
        store = engine.store
        runs = await store.search_runs(limit=1)
        run = runs[0] if runs else None
        if self.job is not None:
            await self._drain(engine, now, run)
            return
        if run is None or run["status"] != "running":
            return
        await self._start(engine, now, run)

    async def _start(self, engine: Engine, now: float, run: dict[str, Any]) -> None:
        store = engine.store
        try:
            base = shipped.load()
        except Exception as exc:
            await store.finish_search_run(
                int(run["id"]), "refused", now, f"no shipped model: {exc}"
            )
            return
        pairs = await site_labels.label_pairs(store)
        features = await store.pair_features([int(p["pair_id"]) for p in pairs])
        rows = site.rows_from(pairs, features)
        bags = {r.bag for r in rows}
        if len(bags) < MIN_BAGS:
            await store.finish_search_run(
                int(run["id"]),
                "refused",
                now,
                f"{len(bags)} labelled bag(s) carry the v2 features; at least {MIN_BAGS} are "
                "needed to fit anything. Label situations from the console and start the search "
                "again.",
                rows=len(rows),
            )
            return
        train, test = site.split_by_time(rows)
        fit_rows, valid_rows = (
            site.split_by_time(train) if len({r.bag for r in train}) > 2 else (train, [])
        )
        budget = {
            **asdict(DEFAULT_BUDGET),
            **{k: v for k, v in run["budget"].items() if k in asdict(DEFAULT_BUDGET)},
        }
        payload = {
            "base": base.document,
            "names": list(FEATURE_NAMES),
            "train": [[r.y, r.w, *r.x] for r in fit_rows],
            "valid": [[r.y, r.w, *r.x] for r in (valid_rows or fit_rows)],
            "budget": budget,
            "seed": int(run["seed"]),
        }
        out = self._ctx.Queue()
        stop = self._ctx.Event()
        process = self._ctx.Process(target=_worker, args=(payload, out, stop), daemon=True)
        process.start()
        self.job = _Job(int(run["id"]), process, out, stop, train, test, now)

    async def _drain(self, engine: Engine, now: float, run: dict[str, Any] | None) -> None:
        job = self.job
        assert job is not None
        store = engine.store
        if run is None or int(run["id"]) != job.run_id or run["status"] == "stopped":
            job.stop.set()
        while True:
            try:
                kind, body = job.out.get_nowait()
            except queue.Empty:
                break
            if kind == "trial":
                await store.add_search_trial(job.run_id, body)
            elif kind == "error":
                await store.finish_search_run(job.run_id, "failed", now, str(body))
                self._reap()
                return
            else:
                await self._finish(engine, now, body, stopped=job.stop.is_set())
                self._reap()
                return
        if not job.process.is_alive() and job.out.empty():
            await store.finish_search_run(job.run_id, "failed", now, "the search process exited")
            self._reap()

    async def _finish(
        self, engine: Engine, now: float, body: dict[str, Any] | None, *, stopped: bool
    ) -> None:
        job = self.job
        assert job is not None
        store = engine.store
        if body is None:
            await store.finish_search_run(
                job.run_id,
                "stopped" if stopped else "failed",
                now,
                "stopped before a model was fitted" if stopped else "no trial finished",
            )
            return
        base = shipped.load()
        candidate = gam.load(body["document"], scorer_id="site")
        rows = job.train + job.test
        bench = site.benchmark_rows([list(r) for r in league.benchmark_rows()])
        verdict = site.judge(base.scorer, candidate, rows, job.test, bench)
        mv = await store.insert_model_version(
            kind=gam.KIND,
            contract_version=CONTRACT_VERSION,
            params_document=body["document"],
            params_hash=gam.fingerprint(body["document"]),
            challenger_run_id=None,
            created_by="search",
            created_at=now,
            note=f"site-adapted from {base.ref} by search run {job.run_id}: {verdict.verdict}",
        )
        await store.finish_search_run(
            job.run_id,
            "stopped" if stopped else "done",
            now,
            verdict.reason,
            model_version_id=mv,
            rows=len(rows),
            judgement=verdict.as_dict(),
        )

    def _reap(self) -> None:
        job = self.job
        self.job = None
        if job is None:
            return
        job.stop.set()
        job.process.join(timeout=5)
        if job.process.is_alive():  # pragma: no cover - a wedged worker
            job.process.terminate()
