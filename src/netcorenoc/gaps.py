"""Ingest gaps: turning drop counters into durable "events lost between t1 and t2" rows (§5.6).

Purely maintenance-side. The trap datagram path never touches any of this — the receiver only
increments a counter, and the folding into `ingest_gap` rows happens on the maintenance pass,
under the batch lock the caller already holds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from netcorenoc import audit
from netcorenoc.engine_base import EngineBase
from netcorenoc.store import Store

GAP_CLOSE_S = 10.0  # an ingest gap closes after this long with no further drops (§5.6)


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


class GapMixin(EngineBase):
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
