"""Single asyncio process: receiver → in-process queue → engine → SQLite.

The engine drains the queue in batches (one SQLite transaction per batch) and, per
activated alarm: deduplicates by fingerprint, demotes periodic flappers, learns
co-occurrence and precedence, scores the alarm against the sliding window, and folds it
into a situation (connected components, merging as links appear). Clears — standard or
learned by alternation — close alarms and, when a situation fully clears, trigger a
learning epoch.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import sqlite3
import statistics
import time
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

import uvicorn

from netcorenoc import audit, auth, known_oids, severity
from netcorenoc.api import QuietServer, create_app
from netcorenoc.correlate import Correlator, ScoredLink, WindowAlarm
from netcorenoc.events import Fingerprint, QuarantinedPacket, TrapEvent
from netcorenoc.learn import STORM_ALARMS, STORM_DAMPING, Learner
from netcorenoc.logsetup import configure_logging
from netcorenoc.receiver import MAX_INSTANCE_CHARS, QueueItem, start_receiver
from netcorenoc.rootcause import Member, Precedence
from netcorenoc.runtime import RuntimeConfig
from netcorenoc.store import Store
from netcorenoc.varbind_profile import MAX_ENTITIES_PER_NE, VarbindProfiler

log = logging.getLogger("netcorenoc")

QUEUE_SIZE = 100_000
BATCH_SIZE = 500
LATENCY_SAMPLES = 4096
LEARN_CAP = 20  # window members observed per activation (bounded work per event)
IDLE_CLOSE_S = 3600.0  # open situations idle this long are closed by maintenance
MAINT_INTERVAL_S = 5.0
PRUNE_EVERY_TICKS = 12  # prune once a minute
GAP_CLOSE_S = 10.0  # an ingest gap closes after this long with no further drops (§5.6)
PROFILE_STALE_S = 7 * 86400.0  # profiler accumulators untouched this long are pruned (§6)
SHUTDOWN_DRAIN_S = 5.0  # bounded deadline to drain queued traps on graceful shutdown (§A.5)


@dataclass
class _OpenGap:
    started_at: float
    last_drop_at: float
    dropped: int


@dataclass
class GapTracker:
    """Turns drop counters into durable ``ingest_gap`` records (§5.6).

    A gap opens on the first drop for a reason and closes after ``GAP_CLOSE_S`` with no
    further drops; the closed row is written to the store and audited. In-memory open gaps
    are surfaced live in ``/api/stats``. Purely maintenance-side — the trap path is untouched.
    """

    open_gaps: dict[str, _OpenGap] = field(default_factory=dict)

    def observe(self, reason: str, count: int, now: float) -> None:
        if count <= 0:
            return
        gap = self.open_gaps.get(reason)
        if gap is None:
            self.open_gaps[reason] = _OpenGap(now, now, count)
        else:
            gap.dropped += count
            gap.last_drop_at = now

    async def flush(self, store: Store, now: float) -> list[tuple[str, _OpenGap]]:
        """Close and persist any gap idle for ``GAP_CLOSE_S``; return the closed gaps."""
        closed: list[tuple[str, _OpenGap]] = []
        for reason, gap in list(self.open_gaps.items()):
            if now - gap.last_drop_at >= GAP_CLOSE_S:
                await store.record_ingest_gap(gap.started_at, gap.last_drop_at, gap.dropped, reason)
                closed.append((reason, gap))
                del self.open_gaps[reason]
        return closed

    def snapshot(self) -> list[dict[str, Any]]:
        return [
            {
                "reason": reason,
                "started_at": gap.started_at,
                "last_drop_at": gap.last_drop_at,
                "dropped": gap.dropped,
                "open": True,
            }
            for reason, gap in self.open_gaps.items()
        ]


# Rebrand (v0.4.0): the canonical environment prefix is NETCORENOC_*. The legacy OPTICORR_*
# names are accepted for exactly one version (removal in v0.5.0 — see docs/ROADMAP.md and
# MIGRATION.md) with a single startup deprecation warning per variable, naming the variable and
# never its value. This mirrors the v0.2.0 legacy-token deprecation discipline.
ENV_PREFIX = "NETCORENOC_"
LEGACY_ENV_PREFIX = "OPTICORR_"
_warned_legacy_env: set[str] = set()


def _warn_legacy_env(legacy_name: str, new_name: str) -> None:
    if legacy_name in _warned_legacy_env:
        return
    _warned_legacy_env.add(legacy_name)
    log.warning(
        "environment variable %s is deprecated and will be removed in v0.5.0; use %s instead",
        legacy_name,
        new_name,
    )


def read_env(suffix: str, default: str | None = None) -> str | None:
    """Read NETCORENOC_<suffix>, falling back to the deprecated OPTICORR_<suffix> (warned once)."""
    value = os.environ.get(ENV_PREFIX + suffix)
    if value is not None:
        return value
    legacy = os.environ.get(LEGACY_ENV_PREFIX + suffix)
    if legacy is not None:
        _warn_legacy_env(LEGACY_ENV_PREFIX + suffix, ENV_PREFIX + suffix)
        return legacy
    return default


@dataclass(frozen=True)
class Settings:
    """All configuration is environment variables with sane defaults — nothing else."""

    db_path: str = "netcorenoc.db"
    trap_host: str = "0.0.0.0"  # nosec B104 - a trap destination must listen externally
    trap_port: int = 162
    http_host: str = "0.0.0.0"  # nosec B104 - the UI/API serve the operator LAN
    http_port: int = 8080
    allowlist: str = ""
    api_token: str = ""
    retention_days: float = 7.0
    audit_retention_days: float = 365.0
    tls_cert: str = ""
    tls_key: str = ""
    log_json: bool = False

    @classmethod
    def from_env(cls) -> Settings:
        def s(suffix: str, default: str) -> str:
            got = read_env(suffix, default)
            return got if got is not None else default

        return cls(
            db_path=s("DB", cls.db_path),
            trap_host=s("TRAP_HOST", cls.trap_host),
            trap_port=int(s("TRAP_PORT", str(cls.trap_port))),
            http_host=s("HTTP_HOST", cls.http_host),
            http_port=int(s("HTTP_PORT", str(cls.http_port))),
            allowlist=s("ALLOWLIST", cls.allowlist),
            # The shared API token was removed in v0.3.0; either prefix setting it is a hard
            # error at startup (run()). Read both names so the removal is enforced, not evaded.
            api_token=os.environ.get(
                ENV_PREFIX + "API_TOKEN",
                os.environ.get(LEGACY_ENV_PREFIX + "API_TOKEN", cls.api_token),
            ),
            retention_days=float(s("RETENTION_DAYS", str(cls.retention_days))),
            audit_retention_days=float(s("AUDIT_RETENTION_DAYS", str(cls.audit_retention_days))),
            tls_cert=s("TLS_CERT", cls.tls_cert),
            tls_key=s("TLS_KEY", cls.tls_key),
            log_json=s("LOG_JSON", "") not in ("", "0", "false", "False"),
        )

    @property
    def tls_enabled(self) -> bool:
        return bool(self.tls_cert and self.tls_key)


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


class Engine:
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

    def forget_situation(self, sid: int) -> None:
        """Drop in-memory membership after an operator manually closes a situation."""
        for member in self.members.pop(sid, []):
            self.sit_of.pop(member.alarm_id, None)

    async def start(self) -> None:
        """Reload learned state and open-situation membership after a restart."""
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
            sid = await self.store.create_situation(entry.ts)
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

    async def apply_feedback(self, sid: int, verdict: str, ts: float) -> bool:
        """Operator feedback: ``confirm`` reinforces the grouping, ``split`` penalizes."""
        if not await self.store.add_feedback(sid, verdict, ts):
            return False
        members = self.members.get(sid)
        if members is not None:
            items = [(m.class_id, m.device_id) for m in members]
        else:
            rows = await self.store.situation_members(sid)
            items = [(int(r["class_id"]), int(r["device_id"])) for r in rows]
        if verdict == "confirm":
            self.learner.learn_epoch(items)
        else:
            self.learner.penalize(items)
        await self.store.commit()
        return True

    async def maintenance(self, now: float, retention_days: float, tick: int = 0) -> None:
        """Close idle situations, persist learned state, record ingest gaps, and prune."""
        async with self.store.lock:
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
            await self.store.commit()

    async def _flush_profiler(self, now: float) -> None:
        rows = self.profiler.flush_rows(now)
        if rows:
            await self.store.upsert_varbind_profiles(rows)

    async def _record_ingest_gaps(self, now: float) -> None:
        """Fold receiver queue-full drops and window-overflow drops into durable gap rows."""
        total_dropped = self.dropped_provider()
        self.gap.observe("queue_full", total_dropped - self._dropped_baseline, now)
        self._dropped_baseline = total_dropped
        self.gap.observe("window_overflow", self.correlator.take_overflow(), now)
        for reason, closed in await self.gap.flush(self.store, now):
            await audit.write_event(
                self.store,
                ts=now,
                actor="system",
                role=None,
                source_ip=None,
                action="ingest.gap",
                outcome="ok",
                object_type="ingest_gap",
                details={
                    "reason": reason,
                    "dropped": closed.dropped,
                    "started_at": closed.started_at,
                    "ended_at": closed.last_drop_at,
                },
            )

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


SUPERVISOR_BACKOFF_BASE_S = 1.0
SUPERVISOR_BACKOFF_MAX_S = 30.0


@dataclass
class Supervisor:
    """Keeps long-lived background tasks alive (§A.5, F10).

    A supervised task that raises is logged (through the redaction filter), counted, and — where
    a restart is safe — restarted with capped exponential backoff; the crash is surfaced through
    ``operator_warnings()`` so it is never silent. A *cancelled* task (graceful shutdown) is never
    restarted. This supervises the engine and the maintenance loop only; the trap datagram path
    lives in the receiver's UDP callback, which cannot raise into the event loop, and is untouched.
    """

    crashes: dict[str, int] = field(default_factory=dict)
    last_error: dict[str, str] = field(default_factory=dict)
    backoff_base: float = SUPERVISOR_BACKOFF_BASE_S
    backoff_max: float = SUPERVISOR_BACKOFF_MAX_S

    async def run(self, name: str, factory: Callable[[], Any], *, restart: bool = True) -> None:
        delay = self.backoff_base
        while True:
            try:
                await factory()
                return  # a task that returns cleanly is done (infinite loops never do)
            except asyncio.CancelledError:
                raise  # shutdown — propagate, never restart
            except Exception as exc:  # the supervisor's whole job is to catch and recover
                self.crashes[name] = self.crashes.get(name, 0) + 1
                self.last_error[name] = type(exc).__name__
                log.exception("supervised task %r crashed (restart=%s)", name, restart)
                if not restart:
                    return
                await asyncio.sleep(delay)
                delay = min(delay * 2, self.backoff_max)

    def warnings(self) -> list[str]:
        return [
            f"Background task '{name}' crashed {n} time(s) (last: {self.last_error.get(name, '?')})"
            " and was restarted; ingestion/correlation may have paused. Check the logs."
            for name, n in sorted(self.crashes.items())
            if n > 0
        ]


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "::ffff:127.0.0.1"}


def operator_warnings(allowlist: str, tls_enabled: bool, http_host: str) -> list[str]:
    """F6: persistent, admin-visible warnings about insecure deployment defaults."""
    warns: list[str] = []
    if not allowlist.strip():
        warns.append(
            "Trap allowlist is empty: all sources are accepted. Set an allowlist to enforce."
        )
    if not tls_enabled and http_host not in LOOPBACK_HOSTS:
        warns.append(
            "HTTP is not using TLS on a non-loopback bind. Set NETCORENOC_TLS_CERT/KEY or "
            "front NetCoreNOC with a TLS reverse proxy."
        )
    return warns


async def _community_key(store: Store) -> bytes:
    """A per-install 32-byte HMAC key for community tagging, created once in `meta` (F4)."""
    key_hex = await store.get_meta("community_hmac_key")
    if key_hex is None:
        key_hex = os.urandom(32).hex()
        await store.set_meta("community_hmac_key", key_hex)
        await store.commit()
    return bytes.fromhex(key_hex)


def _print_bootstrap_banner(password: str) -> None:
    """The single sanctioned place a secret is printed — once, at first startup (F3)."""
    line = "=" * 70
    print(f"\n{line}", flush=True)  # noqa: T201
    print("  NetCoreNOC bootstrap admin created (first run)", flush=True)  # noqa: T201
    print("      username: admin", flush=True)  # noqa: T201
    print(f"      password: {password}", flush=True)  # noqa: T201
    print("  Sign in and change this password immediately. It is shown ONCE.", flush=True)  # noqa: T201
    print(f"{line}\n", flush=True)  # noqa: T201


class LegacyTokenRemovedError(RuntimeError):
    """The shared API token was removed in v0.3.0 (§5.8); setting it is now a hard error."""


async def run(settings: Settings) -> None:
    if settings.api_token:
        # §5.8: the deprecated shared token is removed in v0.3.0. Fail fast, naming the path.
        raise LegacyTokenRemovedError(
            "NETCORENOC_API_TOKEN (and the legacy OPTICORR_API_TOKEN) was removed in v0.3.0. "
            "Unset it and issue a named service token instead (admin -> Tokens in the UI, or "
            "POST /api/tokens); send it as 'Authorization: Bearer <value>'. See MIGRATION.md."
        )
    store = Store(settings.db_path)
    await store.open()
    community_key = await _community_key(store)

    # Config precedence: admin-saved meta values override env defaults (DESIGN v0.2).
    saved_allow = await store.get_meta("config.allowlist")
    saved_ret = await store.get_meta("config.retention_days")
    effective_allowlist = saved_allow if saved_allow is not None else settings.allowlist
    effective_retention = float(saved_ret) if saved_ret is not None else settings.retention_days

    queue: asyncio.Queue[QueueItem] = asyncio.Queue(maxsize=QUEUE_SIZE)
    transport, receiver = await start_receiver(
        queue, settings.trap_host, settings.trap_port, effective_allowlist, community_key
    )
    runtime = RuntimeConfig(
        allowlist=effective_allowlist,
        retention_days=effective_retention,
        on_allowlist_change=lambda nets: setattr(receiver, "networks", nets),
    )
    engine = Engine(store, queue)
    engine.audit_retention_days = settings.audit_retention_days
    engine.dropped_provider = lambda: receiver.stats.dropped  # §5.6 queue-full gap source
    await engine.start()

    bootstrap_password = await auth.bootstrap_admin(store, time.time())
    await store.commit()
    if bootstrap_password is not None:
        _print_bootstrap_banner(bootstrap_password)

    def receiver_stats() -> dict[str, Any]:
        return {"receiver": asdict(receiver.stats)}

    supervisor = Supervisor()

    app = create_app(
        engine,
        extra_stats=receiver_stats,
        tls_enabled=settings.tls_enabled,
        runtime=runtime,
        warnings=lambda: (
            operator_warnings(runtime.allowlist, settings.tls_enabled, settings.http_host)
            + engine.entity_cap_warnings()
            + engine.db_error_warnings()
            + list(store.integrity_warnings)
            + supervisor.warnings()
        ),
    )
    scheme = "https" if settings.tls_enabled else "http"
    server = QuietServer(
        uvicorn.Config(
            app,
            host=settings.http_host,
            port=settings.http_port,
            log_level="warning",
            ssl_certfile=settings.tls_cert or None,
            ssl_keyfile=settings.tls_key or None,
        )
    )
    log.info("listening for traps on %s:%d/udp", settings.trap_host, settings.trap_port)
    log.info("web UI and API on %s://%s:%d/", scheme, settings.http_host, settings.http_port)
    tasks = [
        asyncio.create_task(supervisor.run("engine", engine.run)),
        asyncio.create_task(
            supervisor.run(
                "maintenance",
                lambda: engine.maintenance_loop(lambda: runtime.retention_days),
            )
        ),
        asyncio.create_task(server.serve()),
    ]
    try:
        await asyncio.gather(*tasks)
    finally:
        transport.close()  # stop accepting new datagrams
        server.should_exit = True
        for task in tasks:
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.gather(*tasks)
        # Graceful shutdown (§A.5): drain the traps still queued, then a final maintenance pass
        # (flushes the profiler and learned state). The audit chain only advances on commit, so
        # an interrupted drain leaves it consistent.
        await engine.drain(deadline_s=SHUTDOWN_DRAIN_S)
        await engine.maintenance(time.time(), runtime.retention_days)
        await store.close()
        log.info("receiver stats: %s", receiver.stats)


def main() -> None:
    settings = Settings.from_env()
    configure_logging(settings.log_json)
    loop_main = asyncio.new_event_loop()
    task = loop_main.create_task(run(settings))
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop_main.add_signal_handler(sig, task.cancel)
    with contextlib.suppress(asyncio.CancelledError):
        loop_main.run_until_complete(task)


if __name__ == "__main__":
    main()
