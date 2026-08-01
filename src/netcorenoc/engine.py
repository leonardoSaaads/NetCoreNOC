"""The correlation engine: the queue, the batch, and the one lock the whole thing turns on.

**This file is deliberately large, and it may not be split.** `MODULE-ARCHITECTURE.md` §1's
invariant *"ingestion is sacred"* is only auditable if the whole ingest path can be read in one
place: a reviewer must be able to confirm, **without following imports**, that nothing on that path
takes a lock, does I/O, or awaits where it must not. Fragmenting it would make the project's oldest
invariant unauditable, which is the opposite of what a structural release is for. That is why
`engine.py` is on `COHESION_EXEMPT` rather than the debt allowlist — there is no release in which
someone "fixes" this, so filing it as debt would be a promise nobody intends to keep
(DECISIONS #91).

What stayed, and why:

* `run`, `_commit_batch`, `_process`, `drain` — the batch loop and its one transaction.
* `_assign_situation`, `_handle_clear`, `_handle_state_clear`, `_close_situation` — grouping and
  closing, all under the batch lock.
* `_resolve_entity`, `_resolve_severity`, `_seed_clear_pair`, `_is_flapping`, `FlapDetector` — the
  per-trap decisions the batch makes.
* `apply_feedback` — a write path into learned state, on the same lock discipline.
* `maintenance` and `maintenance_loop` — against both module tables, because `maintenance`
  acquires ``self.store.lock`` (the *same* `asyncio.Lock` `_commit_batch` takes; there is only one)
  and calls `_close_situation`. A reviewer asking "what closes a situation, and under which lock?"
  must not have to follow an import (DECISIONS #90).

What left: the maintenance *helpers* (`maintenance.py`), the gap tracker (`gaps.py`), the scorer
lifecycle (`scorer_lifecycle.py`), configuration (`settings.py`) and the process runner
(`runner.py`).

**`engine.py` must never import `netcorenoc.api`.** That was v0.7.2's one recorded layer violation
and v0.7.3 resolved it; `tests/test_layers.py` holds the line.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import statistics
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from netcorenoc import capture as capture_mod
from netcorenoc import known_oids, severity
from netcorenoc.capture import (
    Capture,
    ClientFingerprint,
    LabelScope,
    RetentionPolicy,
)
from netcorenoc.correlate import Correlator, ScoredLink, WindowAlarm
from netcorenoc.engine_base import EngineBase
from netcorenoc.events import Fingerprint, QuarantinedPacket, TrapEvent
from netcorenoc.gaps import GapMixin, GapTracker
from netcorenoc.learn import STORM_ALARMS, STORM_DAMPING, Learner
from netcorenoc.maintenance import MaintenanceMixin
from netcorenoc.receiver import MAX_INSTANCE_CHARS, QueueItem
from netcorenoc.rootcause import Member, Precedence
from netcorenoc.scorer_lifecycle import ScorerLifecycleMixin
from netcorenoc.store import FeedbackResult, Store
from netcorenoc.varbind_profile import MAX_ENTITIES_PER_NE, VarbindProfiler

log = logging.getLogger("netcorenoc")

BATCH_SIZE = 500
LATENCY_SAMPLES = 4096
LEARN_CAP = 20  # window members observed per activation (bounded work per event)
IDLE_CLOSE_S = 3600.0  # open situations idle this long are closed by maintenance
MAINT_INTERVAL_S = 5.0
PRUNE_EVERY_TICKS = 12  # prune once a minute
PROFILE_STALE_S = 7 * 86400.0  # profiler accumulators untouched this long are pruned (§6)


@dataclass
class FlapDetector:
    """Demote fingerprints that re-activate with short, regular periods (noise)."""

    min_raises: int = 6
    max_mean_interval: float = 900.0
    max_cv: float = 0.5
    reset_gap: float = 3600.0
    history: dict[Fingerprint, deque[float]] = field(default_factory=dict)

    def observe(self, fingerprint: Fingerprint, ts: float) -> bool:
        raises = self.history.setdefault(fingerprint, deque(maxlen=8))
        if raises and ts - raises[-1] > self.reset_gap:
            raises.clear()
        raises.append(ts)
        if len(raises) < self.min_raises:
            return False
        intervals = [b - a for a, b in zip(raises, list(raises)[1:], strict=False)]
        mean = statistics.fmean(intervals)
        if mean <= 0.01:
            return False  # simultaneous repeats are a storm, not flapping
        cv = statistics.pstdev(intervals) / mean
        return mean <= self.max_mean_interval and cv <= self.max_cv


class Engine(MaintenanceMixin, GapMixin, ScorerLifecycleMixin, EngineBase):
    """Consumes the queue in batches; one SQLite transaction per batch."""

    def __init__(self, store: Store, queue: asyncio.Queue[QueueItem]) -> None:
        self.store = store
        self.queue = queue
        self.flap = FlapDetector()
        self.flapping: set[Fingerprint] = set()
        self.learner = Learner()
        self.precedence = Precedence()
        self.correlator = Correlator()
        self.profiler = VarbindProfiler()
        self.sit_of: dict[int, int] = {}  # alarm id -> open situation id
        self.members: dict[int, list[Member]] = {}  # open situation id -> members
        self.latencies: deque[float] = deque(maxlen=LATENCY_SAMPLES)
        self.processed = 0
        self.db_errors = 0  # batches lost to a sqlite operational error (F11)
        self.last_db_error = ""
        self._seeded_oids: set[str] = set()
        self.audit_retention_days = 365.0  # dedicated audit retention (F6/§6.5)
        self.gap = GapTracker()
        self._dropped_baseline = 0
        # Set by main() to the receiver's cumulative queue-full drop count (§5.6). Default
        # 0 keeps the engine self-contained in tests; the trap path stays untouched.
        self.dropped_provider: Callable[[], int] = lambda: 0
        # Entity promotion state (S5): the learned discriminator per NE, the keys seen under
        # each NE (for the MAX_ENTITIES_PER_NE cap), NEs breaching the cap, and the NEs that
        # received traffic since the last promotion sweep.
        self.ne_discriminator: dict[int, list[tuple[str, float]]] = {}  # coarsest -> finest chain
        self._ne_entity_keys: dict[int, set[str]] = {}
        self._entity_cap_hit: set[int] = set()
        self._cap_audited: set[int] = set()
        self._active_nes: set[int] = set()
        # Learned severity (S8): the confirmed severity varbind OID per NE. A trap's value on
        # that OID is normalised to (severity, rank); an absent NE keeps severity unknown.
        self.ne_severity: dict[int, str] = {}
        # Scoring configuration (v0.6.0). `scorer_config_id` is the provenance recorded on every
        # situation this engine opens; None until the store has been read (fresh in-memory DBs in
        # tests may have no pointer). Loaded at the documented reload point only — never per
        # packet, never in receiver.datagram_received.
        self.scorer_config_id: int | None = None
        self.scorer_warnings: list[str] = []
        # (config id, params hash) of the configuration currently instantiated. A reload that
        # finds the same key is a no-op, which is what keeps a fail-safe degradation sticky.
        self._loaded_key: tuple[int, str] | None = None
        # Feedback-dataset capture (v0.8.0). Call sites only; the logic is `netcorenoc.capture`,
        # because this file's COHESION_EXEMPT entry covers the ingest reasoning, not code nearby.
        self.capture = Capture()
        self.retention = RetentionPolicy()

    def forget_situation(self, sid: int) -> None:
        """Drop in-memory membership after an operator manually closes a situation."""
        for member in self.members.pop(sid, []):
            self.sit_of.pop(member.alarm_id, None)

    async def start(self) -> None:
        """Reload learned state and open-situation membership after a restart."""
        await self.load_scorer_config()
        await self._capture_run(time.time())
        await self.learner.load(self.store)
        await self.precedence.load(self.store)
        for row in await self.store.load_varbind_profiles():
            self.profiler.load_row(
                int(row["ne_id"]),
                int(row["class_id"]),
                str(row["varbind_oid"]),
                int(row["n_obs"]),
                int(row["n_repeat"]),
                int(row["n_monotonic"]),
                int(row["n_numeric"]),
                float(row["updated_at"]),
            )
        reset_nes = await self.store.reset_ne_ids()  # discriminators forgotten by an admin (S11)
        for row in await self.store.promoted_discriminators():
            ne_id, oid = int(row["ne_id"]), str(row["varbind_oid"])
            if ne_id in reset_nes:
                continue  # re-learn from fresh evidence; do not resurrect the reset discriminator
            self.ne_discriminator.setdefault(ne_id, []).append((oid, float(row["score"])))
            self.profiler.set_role(ne_id, oid, "entity")
        for row in await self.store.promoted_severities():
            ne_id, oid = int(row["ne_id"]), str(row["varbind_oid"])
            self.ne_severity[ne_id] = oid
            self.profiler.set_role(ne_id, oid, "severity")
        for row in await self.store.open_situation_members():
            sid = int(row["situation_id"])
            member = Member(
                int(row["alarm_id"]),
                int(row["class_id"]),
                int(row["device_id"]),
                float(row["first_seen"]),
            )
            self.members.setdefault(sid, []).append(member)
            self.sit_of[member.alarm_id] = sid

    async def run(self) -> None:
        while True:
            first = await self.queue.get()
            batch = [first]
            while len(batch) < BATCH_SIZE and not self.queue.empty():
                batch.append(self.queue.get_nowait())
            if await self._commit_batch(batch):
                now = time.time()
                for item in batch:
                    if isinstance(item, TrapEvent):
                        self.latencies.append(now - item.ts)

    async def _commit_batch(self, batch: list[QueueItem]) -> bool:
        """Process one batch in a single transaction. On a ``sqlite3`` operational error
        (locked/busy/disk-full) the batch is rolled back — the audit chain only advances on
        commit, so it never breaks — and the loss is counted and surfaced through operator
        warnings while the process keeps running (F11). Returns True on a committed batch."""
        async with self.store.lock:
            try:
                for item in batch:
                    await self._process(item)
                await self.store.commit()
                return True
            except sqlite3.OperationalError as exc:
                await self.store.rollback()
                self.db_errors += 1
                self.last_db_error = type(exc).__name__
                # The message is safe (no secrets); the redaction filter is belt-and-braces.
                log.error(
                    "store operational error draining %d event(s); rolled back and continuing",
                    len(batch),
                )
                return False

    async def drain(self, deadline_s: float = 5.0) -> int:
        """Process traps still queued after the engine task stops (graceful shutdown, §A.5),
        within a bounded deadline. The audit chain stays consistent. Returns items drained."""
        drained = 0
        deadline = time.monotonic() + deadline_s
        while not self.queue.empty() and time.monotonic() < deadline:
            batch: list[QueueItem] = []
            while len(batch) < BATCH_SIZE and not self.queue.empty():
                batch.append(self.queue.get_nowait())
            if not await self._commit_batch(batch):
                break
            drained += len(batch)
        return drained

    def db_error_warnings(self) -> list[str]:
        """Persistent operator warning after a storage error dropped a batch (F11)."""
        if not self.db_errors:
            return []
        return [
            f"{self.db_errors} batch(es) failed to persist due to a storage error "
            f"(last: {self.last_db_error}); those traps were dropped. Check disk space and logs."
        ]

    async def _process(self, item: QueueItem) -> None:
        if isinstance(item, QuarantinedPacket):
            await self.store.quarantine_packet(item)
            return
        class_id = await self.store.class_id(item.trap_oid, item.ts)
        device_id = await self.store.device_id(item.device, item.ts)
        ne_id = await self.store.ne_id(item.device, item.ts)
        varbinds = [(vb.oid, vb.value) for vb in item.varbinds]
        # Varbind profiling: in-memory, under the batch lock, on every trap (repeats are the
        # signal). No lock, no I/O, no effect on grouping — observe-only until S5 (invariant 2).
        self.profiler.observe(ne_id, class_id, varbinds, item.ts)
        self._active_nes.add(ne_id)
        # Resolve the alarmed entity and the dedup instance. At level 0 (no promotion) this is
        # the level-0 entity and the heuristic instance — exact parity with v0.2.0.
        entity_id, instance = await self._resolve_entity(ne_id, item)
        sev, sev_rank = self._resolve_severity(ne_id, item)
        await self._seed_clear_pair(item.trap_oid, class_id, item.ts)
        if len(self.correlator.index) < STORM_ALARMS:
            # Storms teach confounders: random class interleavings would falsely
            # register raise/clear pairs, so alternation learning pauses too.
            self.learner.clears.observe(device_id, item.instance, class_id)
            self.learner.states.observe(device_id, instance, class_id, varbinds)
        self.processed += 1
        raise_class = self.learner.clears.clear_to_raise.get(class_id)
        if raise_class is not None:
            await self._handle_clear(device_id, raise_class, class_id, item, instance)
            return
        # A learned state field (S9): a single-OID trap at its clear value closes the alarm of
        # the same (device, class, instance) that its raise value opened.
        if self.learner.states.is_clear(class_id, varbinds):
            await self._handle_state_clear(device_id, class_id, item, instance)
            return
        result = await self.store.ingest(
            item, entity_id=entity_id, instance=instance, severity=sev, severity_rank=sev_rank
        )
        if not result.activated:
            return
        if await self._is_flapping(item, instance, result.alarm_id):
            return
        entry = WindowAlarm(result.alarm_id, class_id, device_id, item.ts, result.entity_id)
        outcome = self.correlator.process(entry, self.learner)
        recent = outcome.considered[-LEARN_CAP:]
        item_pair = (class_id, device_id)
        self.learner.observe_activation(item_pair)
        self.learner.observe_pairs(
            item_pair, [(c.class_id, c.device_id) for c in recent], outcome.storm
        )
        lead_weight = STORM_DAMPING if outcome.storm else 1.0
        for candidate in recent:
            self.precedence.observe(
                (candidate.class_id, candidate.device_id), item_pair, lead_weight
            )
        await self._assign_situation(entry, outcome.links)
        # After `_assign_situation`, so the situation id is where the alarm landed, and last on
        # this path so a capture failure precedes no decision. `capture.record` never raises.
        await self.capture.record(
            self.store,
            entry,
            item,
            outcome,
            self.learner,
            ne_id=ne_id,
            count=result.count,
            severity=sev,
            severity_rank=sev_rank,
            instance=instance,
            situation_id=self.sit_of.get(entry.alarm_id),
        )

    async def _seed_clear_pair(self, oid: str, class_id: int, ts: float) -> None:
        """Register the universal raise/clear pairs the first time either side shows up."""
        if oid in self._seeded_oids:
            return
        self._seeded_oids.add(oid)
        clear_oid = known_oids.CLEAR_PAIR_SEEDS.get(oid)
        if clear_oid is not None:
            self.learner.clears.register(class_id, await self.store.class_id(clear_oid, ts))
        for raise_oid, seed_clear in known_oids.CLEAR_PAIR_SEEDS.items():
            if seed_clear == oid:
                raise_id = await self.store.class_id(raise_oid, ts)
                self.learner.clears.register(raise_id, class_id)

    async def _resolve_entity(self, ne_id: int, event: TrapEvent) -> tuple[int | None, str]:
        """(entity_id, dedup instance) for a trap, walking the NE's discriminator chain from
        the NE down to the finest entity present in the trap (S6 containment). None entity_id
        means 'use the level-0 entity' (the store default). Forward-only: a promoted NE
        attributes a *new* alarm to the finest entity named by its chain; history is untouched.
        A missing level stops the walk at the current parent; MAX_ENTITIES_PER_NE keeps the
        alarm on the parent and ingestion never fails (§6)."""
        chain = self.ne_discriminator.get(ne_id)
        if not chain:
            return None, event.instance
        keys = self._ne_entity_keys.get(ne_id)
        if keys is None:
            keys = self._ne_entity_keys[ne_id] = set(await self.store.entity_keys_for_ne(ne_id))
        parent = await self.store.entity_level0(ne_id, event.device, event.ts)
        entity_id: int | None = None
        instance = event.instance
        for level, (oid, confidence) in enumerate(chain, start=1):
            value = next((vb.value for vb in event.varbinds if vb.oid == oid), None)
            if value is None:
                break  # this level's identifier is absent -> attribute to the current parent
            value = value[:MAX_INSTANCE_CHARS]
            if value not in keys and len(keys) >= MAX_ENTITIES_PER_NE:
                self._entity_cap_hit.add(ne_id)  # warned + audited in maintenance; never fail
                break
            parent = await self.store.get_or_create_entity(
                ne_id, parent, level, value, oid, confidence, event.ts
            )
            keys.add(value)
            entity_id = parent
            instance = value  # the finest value becomes the dedup instance
        return entity_id, instance

    def _resolve_severity(self, ne_id: int, event: TrapEvent) -> tuple[str | None, int | None]:
        """(severity, rank) for a trap on an NE with a confirmed severity field, else
        (None, None) — an honest unknown, never a default (S8)."""
        oid = self.ne_severity.get(ne_id)
        if oid is None:
            return None, None
        value = next((vb.value for vb in event.varbinds if vb.oid == oid), None)
        if value is None:
            return None, None
        return severity.normalize(value)

    async def reset_entity(self, ne_id: int, now: float) -> None:
        """Forget an NE's learned entity discriminator and severity field (admin recourse for a
        poisoned decision, S11). Future alarms attribute to level 0 with unknown severity; the
        next sweep re-decides from current evidence. History is untouched (forward-only), and a
        durable marker keeps the discriminator from being resurrected on restart until it is
        legitimately re-learned."""
        self.ne_discriminator.pop(ne_id, None)
        self._ne_entity_keys.pop(ne_id, None)
        self._entity_cap_hit.discard(ne_id)
        self._cap_audited.discard(ne_id)
        self.ne_severity.pop(ne_id, None)
        self.profiler.clear_roles(ne_id)
        await self.store.clear_varbind_roles(ne_id)
        await self.store.set_meta(f"entity_reset:{ne_id}", str(now))

    async def reset_profile(self, ne_id: int, now: float) -> None:
        """Everything reset_entity does, plus dropping the NE's profiler evidence (accumulators
        and persisted rows) so identity and severity re-measure from scratch (S11)."""
        await self.reset_entity(ne_id, now)
        self.profiler.drop_ne(ne_id)
        await self.store.delete_varbind_profiles_for_ne(ne_id)

    def entity_cap_warnings(self) -> list[str]:
        """Persistent operator warnings for NEs that hit MAX_ENTITIES_PER_NE (§6)."""
        return [
            f"NE {ne_id} reached the {MAX_ENTITIES_PER_NE}-entity cap; further entities are "
            "attributed to the NE. A forged per-trap identifier can cause this."
            for ne_id in sorted(self._entity_cap_hit)
        ]

    async def _is_flapping(self, event: TrapEvent, instance: str, alarm_id: int) -> bool:
        fingerprint = (event.device, event.trap_oid, instance)
        is_flapping = self.flap.observe(fingerprint, event.ts)
        if is_flapping != (fingerprint in self.flapping):
            (self.flapping.add if is_flapping else self.flapping.discard)(fingerprint)
            await self.store.set_flapping(alarm_id, is_flapping)
        return is_flapping

    async def _handle_clear(
        self, device_id: int, raise_class: int, clear_class: int, event: TrapEvent, instance: str
    ) -> None:
        cleared = await self.store.clear_alarm(device_id, raise_class, instance, event.ts)
        # Clear-class events seen before the pair was learned left stale alarms; retire them.
        stale = await self.store.clear_alarm(device_id, clear_class, instance, event.ts)
        for alarm_id in (cleared, stale):
            if alarm_id is None:
                continue  # a clear with no matching raise is only alternation evidence
            self.correlator.remove(alarm_id)
            sid = self.sit_of.get(alarm_id)
            if sid is not None and await self.store.all_cleared(sid):
                await self._close_situation(sid, event.ts)

    async def _handle_state_clear(
        self, device_id: int, class_id: int, event: TrapEvent, instance: str
    ) -> None:
        """Close the active alarm whose raise value opened it — raise and clear share the class
        and instance, differing only in the learned state varbind's value (S9)."""
        cleared = await self.store.clear_alarm(device_id, class_id, instance, event.ts)
        if cleared is None:
            return  # a clear-state trap with no open alarm is only alternation evidence
        self.correlator.remove(cleared)
        sid = self.sit_of.get(cleared)
        if sid is not None and await self.store.all_cleared(sid):
            await self._close_situation(sid, event.ts)

    async def _close_situation(self, sid: int, ts: float) -> None:
        """Close and run a learning epoch — every closed situation updates A and E."""
        members = self.members.pop(sid, [])
        for member in members:
            self.sit_of.pop(member.alarm_id, None)
        await self.store.close_situation(sid, ts)
        if len(members) > 1:
            self.learner.learn_epoch([(m.class_id, m.device_id) for m in members])

    async def _assign_situation(self, entry: WindowAlarm, links: list[ScoredLink]) -> None:
        """Connected components: join the linked situations, merging when links bridge."""
        sids = {
            self.sit_of[link.other.alarm_id] for link in links if link.other.alarm_id in self.sit_of
        }
        own = self.sit_of.get(entry.alarm_id)
        if own is not None:
            sids.add(own)
        if not sids:
            # Provenance (v0.6.0): the situation records the scoring configuration that formed
            # it. Written here — engine side, under the batch lock — never on the datagram path.
            sid = await self.store.create_situation(entry.ts, self.scorer_config_id)
            self.members[sid] = []
        else:
            sid = min(sids)
            for other_sid in sorted(sids - {sid}):
                await self.store.merge_situations(sid, other_sid, entry.ts)
                for member in self.members.pop(other_sid, []):
                    self.sit_of[member.alarm_id] = sid
                    self.members[sid].append(member)
        if self.sit_of.get(entry.alarm_id) != sid:
            await self.store.add_alarm_to_situation(sid, entry.alarm_id)
            self.sit_of[entry.alarm_id] = sid
            self.members[sid].append(
                Member(entry.alarm_id, entry.class_id, entry.device_id, entry.ts)
            )
        for link in links:
            await self.store.add_link(
                sid,
                link.other.alarm_id,
                entry.alarm_id,
                link.score,
                link.term_t,
                link.term_a,
                link.term_e,
                entry.ts,
            )
        await self.store.touch_situation(sid, entry.ts)
        root = self.precedence.pick_root(self.members[sid])
        if root is not None:
            await self.store.set_root(sid, root)

    async def apply_feedback(
        self,
        sid: int,
        verdict: str,
        ts: float,
        *,
        principal_ref: str | None = None,
        role: str | None = None,
        scope: LabelScope | None = None,
        client: ClientFingerprint | None = None,
    ) -> FeedbackResult:
        """Operator feedback: ``confirm`` reinforces the grouping, ``split`` penalizes.

        **v0.7.1 (F36): the learning effect applies only on a genuine insert.** v0.7.0 applied it on
        every post with no idempotence and no bound, so 80 posts drove 80 effects — and, through
        `learn_epoch`, 80 advances of the *global* forgetting epoch, taking one pair's mass from
        1.000000 to 1.824e-05. A situation has two possible verdicts, so its total influence on the
        learned state is now bounded at two applications however many times anyone posts. A
        *changed* verdict is a legitimate correction and still applies once (DECISIONS #68).

        **v0.7.1 (F39): this no longer commits.** The API owns the transaction boundary, so a
        feedback write is one transaction in the order mutate → audit → commit, like every other
        write path — rather than the one route where the mutation was durable before it was
        attributable (DECISIONS #73).
        """
        recorded = await self.store.add_feedback(
            sid, verdict, ts, principal_ref=principal_ref, role=role
        )
        if not recorded.exists or not recorded.inserted:
            return recorded
        members = self.members.get(sid)
        if members is not None:
            items = [(m.class_id, m.device_id) for m in members]
            bag = [m.alarm_id for m in members]
        else:
            rows = await self.store.situation_members(sid)
            items = [(int(r["class_id"]), int(r["device_id"])) for r in rows]
            bag = [int(r["id"]) for r in rows]
        # v0.8.0 (S4/S6). The bag the server holds at this instant, the label's provenance, and
        # promotion — all of it in `netcorenoc.capture`, all of it degrading rather than raising.
        await capture_mod.record_label(
            self.capture, self.store, recorded, sid, ts, bag, scope=scope, client=client
        )
        if verdict == "confirm":
            # advance_epoch=False: an epoch is a *closed situation*, which is what learn.py has
            # said since v0.1.0. Operator feedback is an opinion about one grouping and must not
            # age the whole appliance's learned state (DECISIONS #69).
            self.learner.learn_epoch(items, advance_epoch=False)
        else:
            self.learner.penalize(items)
        return recorded

    async def maintenance(self, now: float, retention_days: float, tick: int = 0) -> None:
        """Close idle situations, persist learned state, record ingest gaps, and prune."""
        async with self.store.lock:
            # Record a degradation *before* reloading, so a fallback that happened under the
            # current configuration is never lost to the swap that an admin's fix would cause.
            await self._audit_scorer_fallback(now)
            # The documented scoring-configuration reload point: an admin's apply or rollback
            # takes effect here, between batches, never inside one.
            await self.load_scorer_config()
            await self._capture_run(now)  # same reload point, same reason
            for sid in await self.store.idle_open_situations(now - IDLE_CLOSE_S):
                await self._close_situation(sid, now)
            await self._promotion_sweep(now)
            await self.learner.save(self.store, now)
            await self.precedence.save(self.store, now)
            await self._flush_profiler(now)
            await self._record_ingest_gaps(now)
            await self.store.purge_expired_sessions(now)  # expired auth sessions
            if tick % PRUNE_EVERY_TICKS == 0:
                await self.store.prune(now, retention_days * 86400.0)
                self.profiler.prune_stale(now - PROFILE_STALE_S)
                await self.store.delete_stale_varbind_profiles(now - PROFILE_STALE_S)
                await self.capture.prune_sink(self.store, now, self.retention)
            await self.store.commit()

    async def maintenance_loop(self, retention_provider: Callable[[], float]) -> None:
        tick = 0
        while True:
            await asyncio.sleep(MAINT_INTERVAL_S)
            tick += 1
            await self.maintenance(time.time(), retention_provider(), tick)

    def latency_p95(self) -> float:
        if not self.latencies:
            return 0.0
        ordered = sorted(self.latencies)
        return ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]
