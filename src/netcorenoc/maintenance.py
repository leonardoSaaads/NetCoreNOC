"""The periodic work that runs **off** the ingest path.

Entity promotion, severity confirmation, and flushing the profiler's accumulators. All of it
reasons about elapsed time and accumulated evidence rather than about a batch, which is why
`MODULE-ARCHITECTURE.md` §7 lets it leave the `Engine`.

`maintenance()` itself did **not** leave (DECISIONS #90). It acquires ``self.store.lock`` — the
same `asyncio.Lock` object `_commit_batch` takes, because there is only one — and it calls
`_close_situation`, which directive 4 names as must-stay. The methods here run *inside* the lock
`maintenance` already holds and must never take it themselves.
"""

from __future__ import annotations

from netcorenoc import audit, severity
from netcorenoc.engine_base import EngineBase
from netcorenoc.varbind_profile import MAX_ENTITIES_PER_NE


class MaintenanceMixin(EngineBase):
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
