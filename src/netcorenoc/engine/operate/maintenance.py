"""The periodic work that runs **off** the ingest path.

Entity promotion, severity confirmation, and flushing the profiler's accumulators. All of it
reasons about elapsed time and accumulated evidence rather than about a batch, which is why
`MODULE-ARCHITECTURE.md` §7 lets it leave the `Engine`.

`maintenance()` itself did **not** leave (DECISIONS #90). It acquires ``self.store.lock`` — the
same `asyncio.Lock` object `_commit_batch` takes, because there is only one — and it calls
`_close_situation`, which directive 4 names as must-stay. The methods here run *inside* the lock
`maintenance` already holds and must never take it themselves.

**`maintenance_loop` DID leave, in v0.9.0 (DECISIONS #121).** It takes no lock and calls no
must-stay method, and as of this release its body is no longer one call to `maintenance()`: it
sequences **two** periodic activities with *different* lock disciplines — the maintenance pass,
which holds `store.lock` throughout, and the challenger's training, which must not. Stating that
distinction is exactly what this module's docstring is for, and `engine.py`'s cohesion exemption
covers the **ingest path's** readability, which a loop that runs between batches is not part of.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from netcorenoc import __version__
from netcorenoc.crosscutting import audit
from netcorenoc.engine.correlate import severity
from netcorenoc.engine.correlate.varbind_profile import MAX_ENTITIES_PER_NE
from netcorenoc.engine.dataset import census, seal
from netcorenoc.engine.dataset.retention_policy import RETENTION_META_KEY, RetentionPolicy
from netcorenoc.engine.operate.engine_base import EngineBase

MAINT_INTERVAL_S = 5.0
# Train once every this many maintenance ticks. The fit reads the whole labelled corpus and runs a
# fixed number of passes over it; at the 5 s maintenance cadence that would be constant work for a
# quantity that moves when an operator clicks — which is minutes or hours apart, not seconds.
TRAIN_EVERY_TICKS = 60

# The pre-registration that authorises cutting the seal, recorded ON the seal row so a later reader
# can tell WHICH ratified plan it was cut under — and required again before it may ever be read
# (`PREREGISTRATION-0.10.0.md` §4.3(3)). Pinned here rather than read from the file at runtime: the
# appliance must not depend on `docs/` being installed, and a hash read from a file the operator can
# edit would authorise whatever that file happened to say.
PLAN_SHA256 = "c03aef0181554c0c71482e57d03677f25964c3a5ac20a7bf1b1d74bff1ba1e01"


class MaintenanceMixin(EngineBase):
    async def maintenance_loop(self, retention_provider: Callable[[], float]) -> None:
        """The slow loop: the maintenance pass, then — **off the lock** — the challenger's training.

        The ordering is the whole design (DECISIONS #118). Phase 0 measured that
        `Engine.maintenance` is a single `async with self.store.lock` block with zero statements
        after it, so **there was no point in the periodic path that ran outside the lock**. This is
        that point: `maintenance()` has returned and released the lock before `shadow.train` is
        called, and `train` takes the lock only to read its rows and, separately, to write its
        result. The fit itself holds nothing and yields to the event loop between iterations.

        Training failure degrades training. `Shadow.train` catches everything and records it as an
        operator warning, exactly as a capture failure does, so a bad fit can never stop the
        maintenance pass behind it or the ingestion behind that.
        """
        tick = 0
        while True:
            await asyncio.sleep(MAINT_INTERVAL_S)
            tick += 1
            await self.maintenance(time.time(), retention_provider(), tick)
            # v0.16.2: **after** the sweep, so this counts what the sweep could not resolve rather
            # than what it was about to. See `_observe_idle_active`.
            await self._observe_idle_active(time.time())
            if self.shadow.enabled and tick % TRAIN_EVERY_TICKS == 0:
                await self.shadow.train(self.store, time.time(), self.store.lock)
                await self._seal_once(time.time())

    async def _observe_idle_active(self, now: float) -> None:
        """Count the situations the sweep just refused to resolve (v0.16.2, DECISIONS #275).

        A situation that is live, that nobody has touched for `IDLE_CLOSE_S`, and that still holds
        an **active** alarm. Until this release the sweep resolved exactly this population, which
        removed a burning incident from every live view and — because a repeating trap increments
        an existing alarm rather than raising a new one — from every view it could ever return to.
        The sweep now leaves them alone, and leaving them alone silently would be the same defect
        with a smaller radius: an operator would still not be told.

        **Here rather than in `maintenance()`** for prime directive 4's reason: `engine.py` carries
        *"ingestion is sacred"* and its bytes are pinned by `TRAP_PATH_HASHES`. `maintenance_loop`
        left that file in v0.9.0 (DECISIONS #121) and already runs at the one point in the periodic
        path that is outside the lock, which is where a second lock acquisition belongs.

        Recorded as a **count**, not a list: the warning names how many and the console names which,
        and both read `store.idle_active_situations` — one expression, so the two cannot disagree.

        **The import is function-local and that is not a style choice.** `IDLE_CLOSE_S` is defined
        at `engine.py:67`, *after* the line that imports this module, so a module-level import here
        raises `ImportError` on a cold start rather than at review time. Moving the constant would
        edit the pinned file. The threshold is read from the one place that defines it, at the one
        moment it can be.
        """
        from netcorenoc.engine.operate.engine import IDLE_CLOSE_S

        async with self.store.lock:
            self._idle_active_count = len(
                await self.store.idle_active_situations(now - IDLE_CLOSE_S)
            )

    def stale_situation_warnings(self) -> list[str]:
        """The operator warning, through the channel that already carries seven others.

        *"Nobody has touched this in an hour and an alarm is still on"* is the most actionable
        sentence this appliance can produce, and `runner.py` composes it into the same list that
        carries *"the trap allowlist is empty"* to `/api/stats`. Building a second mechanism for
        the most important message would be an argument against the first one.

        Silent at zero, which is the ordinary state: a warning list that always holds an entry is a
        warning list nobody reads.
        """
        if not self._idle_active_count:
            return []
        n = self._idle_active_count
        return [
            f"{n} situation{'s' if n != 1 else ''} nobody has touched for over an hour still "
            f"hold{'' if n != 1 else 's'} an active alarm. They are still open and are marked "
            "stale in the console; the idle sweep will not resolve them while an alarm is on."
        ]

    async def _seal_once(self, now: float) -> None:
        """Cut the sealed holdout, once, ever. **Off the batch lock, and it cannot fail upward.**

        v0.10.0, Workstream 2. The seal is a set of incidents reserved for a decision no release in
        this version is permitted to make, and it is constructed here rather than by a command
        because it must exist from the moment there is a corpus to cut — `reserving later is
        impossible; spending later is always possible`.

        **A second call is not a special case that needs handling; it is the normal steady state.**
        This runs on every training tick for the life of the appliance, and every call after the
        first is refused by `holdout_seal.singleton`'s UNIQUE constraint. That is not an error and
        is not logged as one.

        Any other failure degrades **sealing** and nothing else: it is counted as a shadow-mode
        error, surfaced as an operator warning, and the maintenance pass and the ingestion behind it
        are untouched. A holdout is evidence discipline, not a correlator, and it may never be the
        reason a trap is dropped.
        """
        try:
            async with self.store.lock:
                if await self.store.seal_row() is not None:
                    return
                first_label_at = await census.first_label_per_incident(self.store)
                if not first_label_at:
                    return  # nothing labelled yet; there is no corpus to cut
                await seal.construct(
                    self.store,
                    release=__version__,
                    plan_sha256=PLAN_SHA256,
                    now=now,
                    first_label_at=first_label_at,
                )
                await self.store.commit()
        except Exception as exc:
            self.shadow._degrade(exc)

    async def _capture_run(self, now: float) -> None:
        """Open or refresh the feedback-dataset capture run (v0.8.0).

        Here rather than in `engine.py` for the same reason everything else in this file is: it
        reasons about *elapsed configuration*, not about a batch. It marshals the engine's current
        scorer and retention state into `netcorenoc.capture`, which owns every decision — this is a
        call site, and `engine.py`'s COHESION_EXEMPT entry covers the ingest reasoning, not code
        that merely lands nearby.

        Called from `Engine.start` and from the top of each `maintenance` pass, alongside
        `load_scorer_config`: a capture run records the constants a period of capture shares, so a
        retune that changed the scorer must not have its pair rows attributed to the configuration
        it replaced.
        """
        await self._load_retention()
        await self.capture.open_run(
            self.store,
            now=now,
            scorer_config_id=self.scorer_config_id,
            scorer_params_hash=self._loaded_key[1] if self._loaded_key else None,
            learner=self.learner,
            retention=self.retention,
        )

    async def _load_retention(self) -> None:
        """Adopt the persisted retention policy, or fall back to the shipped default and warn.

        **Why here.** `_capture_run` is the documented configuration reload point — it already runs
        at `Engine.start` and at the top of every maintenance pass, and a capture run is re-opened
        when the retention policy moves. Reading the stored policy at exactly that point means a
        restart adopts it (which is the defect: v0.8.0 answered `"saved"` and silently reverted to
        the shipped defaults on the next boot) *and* that a change written by one process is picked
        up by another, with the resulting pair rows correctly attributed to the new policy.

        **The fail-safe.** Absent is the ordinary zero-config case and is silent. Unusable —
        malformed JSON, a missing or wrong-typed field, or a stored ordering violation — keeps the
        **shipped default** and raises a persistent operator warning. Never a partial
        reconstruction: a policy that cannot be parsed must not become a policy that deletes more
        than the default would (DECISIONS #111).

        The warning goes to `store.integrity_warnings` — the channel for *durable state that is
        damaged*, which an unreadable `meta` row is — so it reaches `/api/stats` through the wiring
        `runner.py` already has, and the engine needs no new field. Added once and removed again
        when a valid policy is stored, so a maintenance pass every five seconds cannot accumulate
        copies and a fixed policy does not leave a stale complaint behind.
        """
        raw = await self.store.get_meta(RETENTION_META_KEY)
        if raw is None:
            return
        warning = (
            f"The stored feedback-dataset retention policy ({RETENTION_META_KEY}) could not be "
            "read and was ignored; the shipped defaults are in effect. Re-apply it through "
            "POST /api/dataset/retention."
        )
        stored = RetentionPolicy.from_json(raw)
        if stored is None:
            if warning not in self.store.integrity_warnings:
                self.store.integrity_warnings.append(warning)
            return
        if warning in self.store.integrity_warnings:
            self.store.integrity_warnings.remove(warning)
        self.retention = stored

    async def _promotion_sweep(self, now: float) -> None:
        """Once per maintenance pass, try to subdivide NEs that saw traffic and have no
        discriminator yet, confirm a severity field for those without one, and audit any that
        just breached the entity cap."""
        active = sorted(self._active_nes)
        self._active_nes.clear()
        for ne_id in active:
            if ne_id not in self.ne_discriminator:
                await self._maybe_promote(ne_id, now)
            if ne_id not in self.ne_severity:
                await self._maybe_confirm_severity(ne_id, now)
        for ne_id in sorted(self._entity_cap_hit - self._cap_audited):
            self._cap_audited.add(ne_id)
            await audit.write_event(
                self.store,
                ts=now,
                actor="system",
                role=None,
                source_ip=None,
                action="entity.promote",
                outcome="denied",
                object_type="ne",
                object_id=str(ne_id),
                details={"reason": "max_entities_per_ne", "cap": MAX_ENTITIES_PER_NE},
            )

    async def _maybe_promote(self, ne_id: int, now: float) -> None:
        # promotion_chain returns the coarsest->finest FD chain to promote, deferring while a
        # finer child is still emerging or an unrelated candidate is within the margin (S6).
        chain = self.profiler.promotion_chain(ne_id)
        if not chain:
            return
        self.ne_discriminator[ne_id] = [(c.varbind_oid, c.score) for c in chain]
        for candidate in chain:
            self.profiler.set_role(ne_id, candidate.varbind_oid, "entity")
        self._ne_entity_keys[ne_id] = set(await self.store.entity_keys_for_ne(ne_id))
        await self.store.del_meta(f"entity_reset:{ne_id}")  # a fresh decision clears the reset
        await audit.write_event(
            self.store,
            ts=now,
            actor="system",
            role=None,
            source_ip=None,
            action="entity.promote",
            outcome="ok",
            object_type="ne",
            object_id=str(ne_id),
            details={
                "chain": [c.varbind_oid for c in chain],
                "score": round(chain[-1].score, 4),
                "n_obs": chain[-1].n_obs,
                "n_distinct": chain[-1].n_distinct,
                "levels": len(chain),
            },
        )

    async def _maybe_confirm_severity(self, ne_id: int, now: float) -> None:
        """Confirm a severity varbind when it is both severity-shaped (the profiler: a small
        ordinal cross-class field, not the identifier) and ordinal against observed alarm
        lifetimes (the store). Forward-only: new alarms gain a severity, history is untouched;
        a field that cannot be validated stays unknown (S8, §5.3)."""
        entity_oids = {oid for oid, _ in self.ne_discriminator.get(ne_id, [])}
        cand = severity.severity_candidate(self.profiler, ne_id, entity_oids)
        if cand is None:
            return
        samples = await self.store.closed_alarm_varbind_lifetimes(ne_id, cand.varbind_oid)
        if not severity.confirm_ordinality(cand, samples):
            return
        self.ne_severity[ne_id] = cand.varbind_oid
        self.profiler.set_role(ne_id, cand.varbind_oid, "severity")
        await audit.write_event(
            self.store,
            ts=now,
            actor="system",
            role=None,
            source_ip=None,
            action="severity.confirm",
            outcome="ok",
            object_type="ne",
            object_id=str(ne_id),
            details={
                "varbind_oid": cand.varbind_oid,
                "kind": cand.kind,
                "values": sorted(cand.ranks),
                "n_obs": cand.n_obs,
                "n_classes": cand.n_classes,
            },
        )

    async def _flush_profiler(self, now: float) -> None:
        rows = self.profiler.flush_rows(now)
        if rows:
            await self.store.upsert_varbind_profiles(rows)
