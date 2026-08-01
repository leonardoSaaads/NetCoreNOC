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

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from netcorenoc.correlate import (
    MAX_CANDIDATES,
    MAX_LINKS_PER_ALARM,
    WINDOW_S,
    CorrelationResult,
    WindowAlarm,
)
from netcorenoc.scoring import LINK_THRESHOLD, LinkScore

if TYPE_CHECKING:  # pragma: no cover - type-only, no runtime edge (tests/test_layers.py)
    from netcorenoc.events import TrapEvent
    from netcorenoc.learn import Learner
    from netcorenoc.store import Store

log = logging.getLogger("netcorenoc")

__all__ = ["Capture", "RetentionPolicy", "member_digest"]


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


def member_digest(alarm_ids: list[int]) -> str:
    """A stable digest over an **ordered** bag of member ids.

    Order is part of the record, so the digest is over the ordered list rather than a set: two bags
    with the same members in a different order are different observations of what the operator saw,
    and collapsing them would discard that. Comma-joined decimal rather than JSON so the digest
    never moves if a serializer's spacing changes.
    """
    return hashlib.sha256(",".join(str(a) for a in alarm_ids).encode()).hexdigest()


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

    def warnings(self) -> list[str]:
        """Persistent operator warning after capture degraded, in `db_error_warnings`' shape."""
        if not self.errors:
            return []
        return [
            f"{self.errors} feedback-dataset capture write(s) failed (last: {self.last_error}); "
            "those pairs are not in the dataset. Ingestion was unaffected. Check disk space."
        ]

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

    async def prune_sink(self, store: Store, now: float, retention: RetentionPolicy) -> None:
        """Apply the sink's **dual bound** — age, then a row cap — on the maintenance tick.

        **Only the sink.** The training tier is never pruned from a background loop: reducing it
        destroys human labels irreversibly, and directive 9 requires the operator to have seen a
        preview count first. That is the whole difference between this and every other prune in the
        product, and it is why `store.prune_dataset` has no caller here.

        Degrades exactly as capture does. A sink that failed to prune is a disk-space problem;
        a maintenance sweep that raised would also skip the learned-state flush behind it.
        """
        if not self.enabled:
            return
        try:
            await store.prune_sink(now - retention.sink_days * 86400.0, retention.sink_rows)
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


@dataclass(frozen=True)
class RetentionPolicy:
    """The three tiers, ordered. **The defaults are defaults, not policy.**

    The right value depends on the customer's network in ways no specification can anticipate: a
    stable regional ISP and a large operator with rolling equipment refreshes want different
    numbers. The product's posture has always been *"you can do this, but understand the risk"*
    rather than a number nobody may touch.

    The **21-day sink default is a conservative guess and this docstring says so.** v0.8.0 measures
    real label latency (`feedback.situation_opened_at`), so a later release can replace it with the
    observed distribution instead of defending a number nobody derived.
    """

    sink_days: float = 21.0
    sink_rows: int = 2_000_000
    training_days: float = 365.0
    audit_days: float = 730.0

    def as_key(self) -> tuple[Any, ...]:
        return (self.sink_days, self.sink_rows, self.training_days, self.audit_days)

    def validate(self) -> str | None:
        """The ordering invariant, **fail-closed, with a precise reason**.

        Returns an error string, or ``None`` when valid. A reason rather than a bool because the
        admin has to be able to fix it: "invalid" on a form with four numbers is not actionable.

        `sink < training <= audit` is not arbitrary. A sink outliving the training tier would keep
        unlabelled rows longer than labelled ones — the opposite of the point. A training tier
        outliving the audit archive would mean the corpus survived its own provenance.
        """
        if self.sink_days <= 0 or self.training_days <= 0 or self.audit_days <= 0:
            return "every retention tier must be greater than zero days"
        if self.sink_rows <= 0:
            return "the sink row cap must be greater than zero"
        if self.sink_days >= self.training_days:
            return (
                f"sink retention ({self.sink_days} d) must be strictly less than training "
                f"retention ({self.training_days} d): the sink holds rows whose destiny is "
                "unknown, so it cannot outlive the corpus they are promoted into"
            )
        if self.training_days > self.audit_days:
            return (
                f"training retention ({self.training_days} d) must not exceed audit retention "
                f"({self.audit_days} d): the corpus cannot outlive its own provenance"
            )
        return None
