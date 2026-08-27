"""Feedback-dataset capture: turning one correlation decision into rows, and never failing ingest.

**Why this is its own module and not part of `engine.py`.** `engine.py` is on `COHESION_EXEMPT`
because the whole ingest path must be readable in one place, and its recorded ceiling is its exact
current size — it has no headroom. More importantly the exemption covers *the ingest reasoning*, not
any code that happens to land nearby: capture is a **consumer** of the correlation decision, not a
participant in it, and putting it beside the batch lock would make the file larger without making
the invariant more auditable. `engine.py` gains call sites and nothing else.

**Where this runs, and where it must never run.** Engine-side, under the batch lock the engine
already holds, in the transaction the batch already opened. **Nothing here is reachable from
`receiver.datagram_received`** — prime directive 1, and `tests/test_layers.py` plus the engine's own
structure are what keep it that way.

**The fail-safe, which is the whole reason this module has a `Capture` object at all.**

    A capture failure degrades capture. It can never fail ingestion.

Exactly as `SafeScorer` degrades scoring: the error is caught, counted, and surfaced through
operator warnings, and the trap is still ingested. A dataset feature that could drop traps would be
the release killing ingestion by an indirect route, which is the failure prime directive 1 names.
The alternative — letting a `sqlite3` error propagate — would roll back the *whole batch*, so one
malformed varbind blob could cost 500 traps.

**Security.** Every row written here is a scope bypass by construction: correlation sees the whole
estate, so these rows contain every NE, entity and raw varbind, ungoverned. No route below `admin`
may read them (`docs/architecture/FEEDBACK-DATASET-0.8-DRAFT.md` §5a).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from netcorenoc.engine.correlate.correlate import (
    MAX_CANDIDATES,
    MAX_LINKS_PER_ALARM,
    WINDOW_S,
    CorrelationResult,
    WindowAlarm,
)
from netcorenoc.engine.correlate.scoring import LINK_THRESHOLD, LinkScore
from netcorenoc.engine.dataset.labels import (
    MAX_CLIENT_MEMBERS,
    ClientFingerprint,
    Exclusion,
    LabelContext,
    LabelScope,
    member_digest,
    record_label,
    server_bag,
)
from netcorenoc.engine.dataset.retention_policy import (
    RETENTION_META_KEY,
    TIER_NAMES,
    RetentionPolicy,
)

if TYPE_CHECKING:  # pragma: no cover - type-only, no runtime edge (tests/test_layers.py)
    from netcorenoc.engine.correlate.learn import Learner
    from netcorenoc.events import TrapEvent
    from netcorenoc.store import Store

log = logging.getLogger("netcorenoc")

# Re-exported so `engine.py` and the API keep one import site for "the capture surface", while
# the verdict-path code lives in `labels.py`. See that module for why the split is by *path*.
__all__ = [
    "MAX_CLIENT_MEMBERS",
    "RETENTION_META_KEY",
    "TIER_NAMES",
    "Capture",
    "ClientFingerprint",
    "Exclusion",
    "LabelContext",
    "LabelScope",
    "RetentionPolicy",
    "member_digest",
    "record_label",
    "server_bag",
]


def _value_of(result: LinkScore, name: str) -> float:
    """A named term's **input value** — `A`, `E` — as the scorer actually saw it.

    This is the counterpart of `correlate.contribution_of`, which reads `.contribution`
    (`weight * value`). The dataset needs the **value**: dividing the weight back out of a
    contribution **fails when the weight is zero**, and zero is a legal, supported setting an admin
    may choose (`FEEDBACK-DATASET-0.8-DRAFT.md` §3.2).

    Reading it from `result.terms` rather than re-asking the learner is the point, and it is not a
    micro-optimisation. `Engine._process` calls `learner.observe_activation` and
    `learner.observe_pairs` between `correlator.process()` and this capture, so **the masses have
    already moved** by the time these rows are built. Re-deriving `A` and `E` here would record
    numbers the scorer never saw — a dataset of fabricated features, indistinguishable from real
    ones. `TermContribution` carried the value all along; v0.7.5 simply dropped it at the
    persistence boundary.

    Degrades to 0.0 for a scorer that does not emit the term, exactly as `contribution_of` does,
    rather than mis-labelling someone else's number.
    """
    for term in result.terms:
        if term.name == name:
            return term.value
    return 0.0


@dataclass
class Capture:
    """Engine-side capture, with its own failure accounting.

    ``enabled`` ships **on**: the value of this dataset compounds with time, and a deployment that
    discovers its importance six months in has lost six months that cannot be reconstructed. The
    dual bound (§7) is what makes that safe without a magic traps/day number, and `dataset stats`
    is what keeps the operator from being blind about the cost.
    """

    enabled: bool = True
    run_id: int | None = None
    errors: int = 0
    last_error: str = ""
    # alarm_id -> dataset_observation.id for the observations written in this process's lifetime.
    # Bounded by pruning it against the correlator's own window on every activation, so it cannot
    # outgrow the window it mirrors.
    _observations: dict[int, int] = field(default_factory=dict)
    # What the audit sweep has destroyed in this process's lifetime, by row kind. Deleting a label
    # is the most consequential thing this product does, so the count is kept and surfaced rather
    # than discarded the way `store.prune`'s is.
    audit_swept: dict[str, int] = field(default_factory=dict)

    # Rows whose stored reconciled count disagreed with a recomputation from the child tables, as
    # of the last maintenance sweep. **Never corrected** (DECISIONS #134) — see `verify_evidence`.
    drift_rows: int = 0

    def warnings(self) -> list[str]:
        """Persistent operator warnings: capture degradation, and evidence drift."""
        out: list[str] = []
        if self.errors:
            out.append(
                f"{self.errors} feedback-dataset capture write(s) failed "
                f"(last: {self.last_error}); those pairs are not in the dataset. Ingestion "
                "was unaffected. Check disk space."
            )
        if self.drift_rows:
            out.append(
                f"{self.drift_rows} label row(s) carry a reconciled exclusion count that "
                "disagrees with the stored evidence. NOTHING HAS BEEN CORRECTED: a "
                "disagreement means a write path is wrong, and repairing the row would "
                "destroy the evidence of that. Run "
                "`netcorenoc dataset bias` for the count and read "
                "docs/architecture/EVIDENCE-BOUNDARY-0.9.2.md §4.2."
            )
        return out

    def _degrade(self, exc: Exception) -> None:
        """Record a capture failure. **This is the only place a capture error is handled**, and it
        never re-raises: the caller's next statement is the engine's, and the trap must still land.
        """
        self.errors += 1
        self.last_error = type(exc).__name__
        log.warning("feedback-dataset capture failed (%s); ingestion unaffected", self.last_error)

    async def open_run(
        self,
        store: Store,
        *,
        now: float,
        scorer_config_id: int | None,
        scorer_params_hash: str | None,
        learner: Learner,
        retention: RetentionPolicy,
    ) -> None:
        """Open a capture run, or reuse the current one if nothing that defines it has changed.

        A run is the set of constants a period of capture shares, so it is re-opened when the
        scorer configuration or the retention policy moves — which is what makes pair rows on either
        side of an admin's retune distinguishable. Nothing else in the schema would record that.
        """
        if not self.enabled:
            return
        from netcorenoc import __version__

        try:
            current = await store.latest_capture_run()
            key = (scorer_config_id, scorer_params_hash, *retention.as_key())
            if current is not None and self.run_id is not None and _run_key(current) == key:
                return
            self.run_id = await store.open_capture_run(
                started_at=now,
                netcorenoc_version=__version__,
                scorer_config_id=scorer_config_id,
                scorer_params_hash=scorer_params_hash,
                window_s=WINDOW_S,
                max_candidates=MAX_CANDIDATES,
                max_links_per_alarm=MAX_LINKS_PER_ALARM,
                link_threshold=LINK_THRESHOLD,
                retention_sink_days=retention.sink_days,
                retention_sink_rows=retention.sink_rows,
                retention_training_days=retention.training_days,
                retention_audit_days=retention.audit_days,
                a_epoch=learner.A.epoch,
                e_epoch=learner.E.epoch,
            )
        except Exception as exc:
            self._degrade(exc)

    async def record(
        self,
        store: Store,
        entry: WindowAlarm,
        event: TrapEvent,
        outcome: CorrelationResult,
        learner: Learner,
        *,
        ne_id: int,
        count: int,
        severity: str | None,
        severity_rank: int | None,
        instance: str,
        situation_id: int | None,
    ) -> None:
        """Capture one activation: its observation row, and one row per **evaluated** pair.

        Called from `Engine._process` **after** `_assign_situation`, so `situation_id` is the
        situation the alarm actually landed in rather than the one it was heading for.

        The five objects are positional and the per-trap facts are keyword, so the call site in
        `engine.py` stays short: that file is `COHESION_EXEMPT` at a shrink-only ceiling, and every
        line capture spends there is a line the ingest path's reader has to walk past.

        Bounded by construction: at most one observation row, and at most `MAX_CANDIDATES` (100)
        pair rows, per activation. That is the ceiling the ingest path already lives under — this
        adds no new unbounded work, and Phase 6 states the measured cost per trap rather than
        asserting it is small.
        """
        if not self.enabled or self.run_id is None:
            return
        try:
            obs_b = await self._observation_for(
                store,
                entry,
                event,
                ne_id=ne_id,
                severity=severity,
                severity_rank=severity_rank,
                instance=instance,
                alarm_count=count,
            )
            kept = {link.other.alarm_id for link in outcome.links}
            rows: list[tuple[Any, ...]] = []
            for pair in outcome.evaluated:
                other = pair.other
                rows.append(
                    (
                        self.run_id,
                        other.alarm_id,  # KEY: the window alarm
                        entry.alarm_id,  # KEY: the newly activated alarm
                        self._observations.get(other.alarm_id),
                        obs_b,
                        situation_id,
                        # Exactly `LinkFeatures.delta_t_s` — both timestamps are immutable, so this
                        # is the value the scorer saw, recomputed rather than stored twice.
                        abs(entry.ts - other.ts),
                        # The VALUES, read back from the terms the scorer emitted. See `_value_of`
                        # for why these are not re-derived from the learner.
                        _value_of(pair.result, "class_affinity"),
                        _value_of(pair.result, "entity_affinity"),
                        learner.A.epoch,
                        learner.E.epoch,
                        pair.result.score,
                        1 if pair.result.linked else 0,
                        1 if outcome.storm else 0,
                        # An ACCEPTED link the cap dropped — not a rejection. Phase 0 measured this
                        # as 94% of what the engine discards, so the distinction is load-bearing.
                        1 if (pair.result.linked and other.alarm_id not in kept) else 0,
                        entry.ts,
                    )
                )
            await store.add_pairs(rows)
            self._forget_outside(set(store_window_ids(outcome)) | {entry.alarm_id})
        except Exception as exc:
            self._degrade(exc)

    async def _observation_for(
        self, store: Store, entry: WindowAlarm, event: TrapEvent, **facts: Any
    ) -> int:
        """The immutable observation row for this activation, written once per activation."""
        obs_id = await store.add_observation(
            capture_run_id=self.run_id,
            alarm_id=entry.alarm_id,
            ne_id=facts["ne_id"],
            device_id=entry.device_id,
            entity_id=entry.entity_id,
            class_id=entry.class_id,
            observed_at=entry.ts,
            alarm_count=facts["alarm_count"],
            severity=facts["severity"],
            severity_rank=facts["severity_rank"],
            instance=facts["instance"],
            trap_oid=event.trap_oid,
            source_address=event.device,
            # The raw material, stored once per observation rather than up to MAX_CANDIDATES times
            # per pair. Not parsed into columns: "same OID root?" and "same vendor?" are modelling
            # decisions and freezing one now is the mistake §4a names.
            varbinds=json.dumps(
                [[vb.oid, vb.value] for vb in event.varbinds], separators=(",", ":")
            ),
        )
        self._observations[entry.alarm_id] = obs_id
        return obs_id

    async def prune(self, store: Store, now: float, retention: RetentionPolicy) -> None:
        """The maintenance-time dataset pass: the policy's two **background** bounds, then one
        **verification**.

        The bounds are the sink's dual bound (age, then a row cap — unchanged from v0.8.0) and the
        **audit bound**, the outer edge of the data's life and the only background path that may
        delete a human label.

        **The training tier is deliberately absent**: it *selects* rather than deletes
        (DECISIONS #110). v0.8.0's directive 9 — this loop must never *silently* destroy labels —
        is satisfied rather than repealed, because the audit sweep destroys nothing the operator
        did not configure a bound for, and every deletion is counted here and reported.

        Degrades exactly as capture does. A sweep that failed is a disk-space problem; a
        maintenance pass that raised would also skip the learned-state flush behind it.

        **Why the verification's call site is here** (v0.9.2): it belongs to the maintenance
        cadence, it must run inside the lock the pass already holds, and `engine.py` is
        `COHESION_EXEMPT` at a ceiling equal to its exact size, so this release may not add a call
        site to it. This is the one method the maintenance pass already calls on the dataset, on the
        `PRUNE_EVERY_TICKS` schedule the verification wants. Named in the docstring rather than
        left to be discovered.
        """
        if not self.enabled:
            return
        try:
            await store.prune_sink(now - retention.sink_days * 86400.0, retention.sink_rows)
            swept = await store.prune_dataset_audit(now - retention.audit_days * 86400.0)
        except Exception as exc:
            self._degrade(exc)
        else:
            for key, count in swept.items():
                self.audit_swept[key] = self.audit_swept.get(key, 0) + count
        await self.verify_evidence(store)

    async def verify_evidence(self, store: Store) -> None:
        """Recompute the reconciled exclusion count from the child tables and **report** drift.

        The denormalized `feedback.excluded_reconciled` is a **rebuildable copy**;
        `feedback_exclusion` and `feedback_member(source='server')` remain the source of truth. So
        the system carries a reconciliation query and drift monitoring rather than trusting the
        copy — which is the ordinary discipline for a denormalized aggregate, applied literally.

        **It does not correct, and that is the decision rather than an omission** (DECISIONS #134).
        A disagreement means a **write path is broken**. Repairing the row silently would destroy
        the evidence of that, which is the entire reason this release exists: had this check shipped
        in v0.9.1 as a corrector, F46 would have been invisible — every hostile row quietly repaired
        on the next pass, the reports looking right, and the write path staying broken indefinitely.

        Surfaced through :meth:`warnings`, and counted durably by `dataset bias`, which recomputes
        it from the database on every run. **No audit row**: the audit catalog is frozen and this
        release adds no action to it, and a detection that changes no behaviour is not an event in
        the sense the catalog records. The report is the durable record; the warning is the alert.

        Degrades like everything else here. A verification that raised would take the maintenance
        pass with it, which would be a worse outcome than an unverified sweep.
        """
        if not self.enabled:
            return
        try:
            self.drift_rows = len(await store.reconciliation_drift())
        except Exception as exc:
            self._degrade(exc)

    def _forget_outside(self, live: set[int]) -> None:
        """Keep the observation index bounded by the correlator's own window.

        Without this the map would grow for the life of the process. It mirrors a structure that is
        already bounded, so bounding it the same way costs nothing and needs no new number.
        """
        if len(self._observations) > MAX_CANDIDATES * 4:
            self._observations = {k: v for k, v in self._observations.items() if k in live}


def store_window_ids(outcome: CorrelationResult) -> list[int]:
    """The alarm ids this decision still considers live — the bound for the observation index."""
    return [c.alarm_id for c in outcome.considered]


def _run_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row["scorer_config_id"],
        row["scorer_params_hash"],
        float(row["retention_sink_days"]),
        int(row["retention_sink_rows"]),
        float(row["retention_training_days"]),
        float(row["retention_audit_days"]),
    )
