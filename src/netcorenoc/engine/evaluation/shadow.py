"""Shadow mode's lifecycle: the online sampler, the offline reconstruction, and the slow loop.

**A challenger runs beside the champion and writes its opinion where nobody acts on it.** Nothing in
this module reaches a situation, a link, the UI, an operator, `learn.penalize()` or the active
scorer, and `tests/test_challenger.py` asserts that by parsing the tree rather than by reading it.

## The two mechanisms, and why both ship (DECISIONS #119)

* **Offline reconstruction** — recompute the opinion from `dataset_pair`. Measures model quality
  at no ingest cost. **Cannot** measure training/serving skew, *by construction*: recomputing from
  the stored features is tautologically consistent with them.
* **Online shadow** — score live in the engine and write a **sample**. Measures real per-call
  latency and real behaviour under real traffic. Says nothing further about quality.

**Their divergence is the skew test.** The pre-registered expectation is **0.0000 %**, compared with
`==` on the float rather than a tolerance, and a non-zero rate is a defect rather than a number to
widen a threshold around.

## Where each half runs, and the discipline that goes with it

* :meth:`Shadow.observe` runs **on the ingest path**, inside the batch lock the engine already
  holds, once per activation, bounded by the sample rate and by a fixed buffer. It scores and
  **buffers**; it never writes. Fail-safe on the `Capture` model: an error degrades shadow mode,
  is counted, is surfaced as an operator warning, and **can never fail ingestion**.
* :meth:`Shadow.train` runs in `maintenance_loop`, **after `maintenance()` has returned and
  released the lock** (DECISIONS #118) — because Phase 0 measured that no point inside
  `maintenance()` runs outside `store.lock`, and the lock it holds is the same object
  `_commit_batch` takes. The lock is taken only to read rows and, separately, to write the result;
  **the fit itself holds nothing**.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from netcorenoc.census import corpus_stats, resolve_identity
from netcorenoc.engine.model.challenger import (
    CHALLENGER_SCORER_ID,
    Coefficients,
    LogisticScorer,
    sigmoid,
)
from netcorenoc.engine.model.training import LabelledPair, assess, derive, fit, resolve_floors
from netcorenoc.scoring import CONTRACT_VERSION, LinkFeatures

if TYPE_CHECKING:  # pragma: no cover - type-only, no runtime edge (tests/test_layers.py)
    from netcorenoc.correlate import CorrelationResult, WindowAlarm
    from netcorenoc.store import Store

log = logging.getLogger("netcorenoc")

__all__ = ["FLOORS_META_KEY", "SHADOW_MAX_ROWS", "Shadow", "corpus_stats"]

# The deployment's evidence-floor policy, absent by default. `meta` is how this product already
# persists operator configuration (DECISIONS #111/#114); no route, no capability, no new HTTP
# surface for a value no scoped principal may read anyway.
FLOORS_META_KEY = "config.evidence_floors"
# The deployment's sampling rate, absent by default. **The deployment chooses the rate and the
# duration, not whether online shadow ever runs** — a model that was never measured inside the
# engine must not be promotable two releases later.
SAMPLE_META_KEY = "config.shadow_sample_rate"

# One opinion in every N activations. Low enough that the ingest cost is a rounding error against
# the pair rows capture already writes, and Phase 5 proves that with a measurement in rows, bytes
# and microseconds rather than claiming it.
DEFAULT_SAMPLE_RATE = 0.01
# The in-memory buffer between the ingest path and the maintenance flush. Bounded by construction,
# like everything else on this path: a full buffer drops the sample and counts the drop rather than
# growing. Dropping a sample costs a data point; growing without bound costs the process.
MAX_BUFFERED = 2000
# `shadow_opinion`'s row cap. Grows with traffic times sample rate, so the bound is rows and not
# days — a quiet week and a storm produce wildly different volumes over the same dates.
SHADOW_MAX_ROWS = 200_000


@dataclass
class Shadow:
    """Engine-side shadow state, with its own failure accounting.

    Ships **enabled**, like capture and for the same reason: the value of the measurement compounds
    with time. What it *does* while enabled is score a sample and buffer the result — the challenger
    it holds is untrained until a fit succeeds, and an untrained challenger abstains.
    """

    enabled: bool = True
    scorer: LogisticScorer = field(default_factory=LogisticScorer)
    run_id: int | None = None
    sample_rate: float = DEFAULT_SAMPLE_RATE
    errors: int = 0
    last_error: str = ""
    warnings_extra: list[str] = field(default_factory=list)
    sampled: int = 0
    dropped: int = 0
    scored: int = 0
    # Wall-clock nanoseconds spent inside `scorer.score()` on the ingest path, so the online cost is
    # a MEASUREMENT rather than a claim. Read by the report; never used in a decision.
    score_ns: int = 0
    _buffer: list[tuple[Any, ...]] = field(default_factory=list)
    # A deterministic counter, not a random draw. `1 in N` by counting is reproducible in a test
    # and in a replay; a PRNG here would make the ingest path's behaviour unreproducible, and this
    # project has spent nine releases making replays exact.
    _seen: int = 0

    def warnings(self) -> list[str]:
        """Operator warnings, in `db_error_warnings`' shape."""
        out = list(self.warnings_extra)
        if self.dropped:
            # **Measured, not hypothetical.** At `sample_rate = 1.0` over the eval corpus the buffer
            # fills long before the flush and 43 474 of 45 474 opinions are dropped. The bound is
            # working — but an operator who set 1.0 expecting every pair must be told they did not
            # get one, or they will read a 2 000-row sample as a census.
            out.append(
                f"{self.dropped} shadow opinion(s) were dropped because the in-memory buffer "
                f"({MAX_BUFFERED}) filled between maintenance flushes. The sample is smaller than "
                "the configured rate implies; lower config.shadow_sample_rate to make the sample "
                "representative rather than truncated. Nothing else was affected."
            )
        if self.errors:
            out.append(
                f"{self.errors} shadow-mode operation(s) failed (last: {self.last_error}). "
                "Shadow mode is degraded; correlation and ingestion were unaffected, and the "
                "built-in scorer decides everything as always."
            )
        return out

    def _degrade(self, exc: Exception) -> None:
        """The only place a shadow error is handled, and it never re-raises.

        Exactly `Capture._degrade`'s contract: the caller's next statement is the engine's, and the
        trap must still land. A shadow feature that could drop traps would be this release killing
        ingestion by an indirect route.
        """
        self.errors += 1
        self.last_error = type(exc).__name__
        log.warning("shadow mode failed (%s); ingestion unaffected", self.last_error)

    # -- the online half, on the ingest path ---------------------------------------------------

    def observe(
        self, entry: WindowAlarm, outcome: CorrelationResult, situation_id: int | None
    ) -> None:
        """Score a **sample** of this activation's evaluated pairs, and buffer the opinions.

        Called from `Engine._process` after `capture.record`, so it is the last thing on the path
        and no decision follows it. Bounded three ways: by `MAX_CANDIDATES` (the evaluated list),
        by the sample rate, and by `MAX_BUFFERED`.

        **Nothing is written here.** The flush happens in the maintenance pass, under the lock that
        pass already holds, so the ingest path pays for one `score()` per sampled pair and nothing
        else. The champion's `class_affinity` and `entity_affinity` are read back from the terms it
        emitted — never re-derived from the learner, whose masses have already moved by now, which
        is `capture._value_of`'s argument one module over.

        `same_oid_root` is written as **0**: it is registered in the analysis plan and not
        implemented, because `LinkFeatures` carries no trap OID and a feature that cannot be served
        is a feature that guarantees skew. The column exists so a later release can populate it
        without a migration; this release records honestly that it did not.
        """
        if not self.enabled or self.run_id is None:
            return
        try:
            for pair in outcome.evaluated:
                self._seen += 1
                if self.sample_rate <= 0.0 or self._seen % max(1, round(1.0 / self.sample_rate)):
                    continue
                if len(self._buffer) >= MAX_BUFFERED:
                    self.dropped += 1
                    continue
                other = pair.other
                delta_t = abs(entry.ts - other.ts)
                features = LinkFeatures(
                    delta_t_s=delta_t,
                    class_i=entry.class_id,
                    class_j=other.class_id,
                    class_affinity=_value_of(pair.result, "class_affinity"),
                    ne_i=entry.device_id,
                    ne_j=other.device_id,
                    entity_affinity=_value_of(pair.result, "entity_affinity"),
                )
                started = time.perf_counter_ns()
                verdict = self.scorer.score(features)
                self.score_ns += time.perf_counter_ns() - started
                self.scored += 1
                self._buffer.append(
                    (
                        self.run_id,
                        other.alarm_id,
                        entry.alarm_id,
                        situation_id,
                        delta_t,
                        features.class_affinity,
                        features.entity_affinity,
                        0,  # same_oid_root: registered, not implemented — see the docstring
                        sigmoid(verdict.score),
                        1 if verdict.linked else 0,
                        1 if pair.result.linked else 0,
                        entry.ts,
                    )
                )
                self.sampled += 1
        except Exception as exc:  # a shadow write must never take ingestion down
            self._degrade(exc)

    async def flush(self, store: Store) -> None:
        """Write the buffered opinions. Caller holds ``store.lock`` (the maintenance pass does)."""
        if not self._buffer:
            return
        rows, self._buffer = self._buffer, []
        try:
            await store.add_shadow_opinions(rows)
            await store.prune_shadow(SHADOW_MAX_ROWS)
        except Exception as exc:
            self._degrade(exc)

    # -- the slow loop, off the lock -----------------------------------------------------------

    async def train(self, store: Store, now: float, lock: Any) -> dict[str, Any]:
        """One training attempt, **off the batch lock** (DECISIONS #118).

        The shape, and every part of it is load-bearing:

        1. take ``lock`` to read the labelled corpus and the deployment policy — bounded statements;
        2. **release it**, then assess sufficiency and, if it holds, fit. The fit holds no lock and
           yields to the event loop between iterations, so it can neither block a batch nor stall
           the loop;
        3. take ``lock`` again to write the run row and flush the sampled opinions.

        Returns the run document. **A run row is written whether or not anything was fitted**:
        insufficiency is a result, and one that no schema could record would be one no report could
        state.
        """
        from netcorenoc import __version__

        try:
            async with lock:
                raw_floors = await store.get_meta(FLOORS_META_KEY)
                raw_rate = await store.get_meta(SAMPLE_META_KEY)
                bags = await store.labelled_bags()
                raw_pairs = await store.labelled_pairs()
                identity = await resolve_identity(store, bags, raw_pairs)
                pairs = [labelled_pair(row) for row in raw_pairs]
                capture_run = await store.latest_capture_run()
            self.sample_rate = _sample_rate(raw_rate)
            floors, warning = resolve_floors(raw_floors)
            self.warnings_extra = [warning] if warning else []
            stats = corpus_stats(bags, identity)
            verdict = assess(stats, floors)

            outcomes: dict[str, Any] = {}
            if verdict.ok:
                for policy in ("A", "B"):
                    rows, diagnostics = derive(pairs, policy)
                    coefficients, fit_diagnostics = await fit(rows)
                    outcomes[policy] = {
                        "coefficients": coefficients,
                        **diagnostics,
                        **fit_diagnostics,
                    }

            document = {
                "started_at": now,
                "netcorenoc_version": __version__,
                "scorer_id": CHALLENGER_SCORER_ID,
                "contract_version": CONTRACT_VERSION,
                "derivation_policy": "A" if verdict.ok else "none",
                "train_oldest_at": stats.oldest_label_at,
                "train_newest_at": stats.newest_label_at,
                "train_rows": outcomes["A"]["rows"] if verdict.ok else 0,
                "train_bags": stats.bags,
                "train_split_bags": stats.split_bags,
                "train_mixed_bags": stats.mixed_bags,
                "train_incidents": stats.incidents,
                "train_operators": stats.operators,
                "thresholds": floors.to_json(),
                "sufficient": 1 if verdict.ok else 0,
                "insufficiency": None if verdict.ok else verdict.to_json(),
                "sample_rate": self.sample_rate,
                "capture_run_id": None if capture_run is None else int(capture_run["id"]),
            }
            if verdict.ok:
                chosen: Coefficients = outcomes["A"]["coefficients"]
                document["params_fingerprint"] = LogisticScorer(chosen).params_fingerprint()
                document["coefficients"] = chosen.to_json()
                document["iterations"] = outcomes["A"]["iterations"]
                document["learning_rate"] = outcomes["A"]["learning_rate"]
                document["fit_seconds"] = outcomes["A"]["fit_seconds"]
                # The ACTIVE-IN-SHADOW model is policy A's. B is fitted and reported and is never
                # the one that scores, because the two must not silently swap between runs.
                self.scorer = LogisticScorer(chosen)

            async with lock:
                self.run_id = await store.open_challenger_run(**document)
                await self.flush(store)
                await store.commit()
            return {"run": document, "stats": stats, "verdict": verdict, "policies": outcomes}
        except Exception as exc:  # training degrades training, never ingestion
            self._degrade(exc)
            return {}


def _sample_rate(raw: str | None) -> float:
    """The deployment's sampling rate, clamped into `[0, 1]`. Unreadable falls back to the default.

    Zero is a legitimate value and means *"buffer nothing online"*; it does not mean *"switch shadow
    mode off"*, and the difference matters — the offline reconstruction still runs, so quality is
    still measured, and only the skew test loses its subject. That is a deployment's to choose.
    """
    if raw is None:
        return DEFAULT_SAMPLE_RATE
    try:
        return min(1.0, max(0.0, float(raw)))
    except (TypeError, ValueError):
        return DEFAULT_SAMPLE_RATE


def _value_of(result: Any, name: str) -> float:
    """A named term's **input value**, as the champion actually saw it.

    `capture._value_of`'s rule and its reasoning, one module over: read back from the terms rather
    than re-derived, because the learner's masses have already moved by the time this runs.
    """
    for term in result.terms:
        if term.name == name:
            return float(term.value)
    return 0.0


def labelled_pair(row: dict[str, Any]) -> LabelledPair:
    """One row of `store.labelled_pairs` as the typed record training consumes."""
    return LabelledPair(
        pair_id=int(row["pair_id"]),
        feedback_id=int(row["feedback_id"]),
        verdict=str(row["verdict"]),
        incident=int(row["incident"]),
        delta_t_s=float(row["delta_t_s"]),
        class_affinity=float(row["class_affinity"]),
        entity_affinity=float(row["entity_affinity"]),
        incumbent_linked=bool(int(row["incumbent_linked"])),
        evaluated_at=float(row["evaluated_at"]),
        label_at=float(row["label_at"]),
    )
