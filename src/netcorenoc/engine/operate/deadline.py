"""Each trap's own clock: classified within the deadline of **its own** arrival, or counted late.

ADR #447. The classification KPI is per trap. A trap that arrives 20 s after another has its own
full deadline, measured from its own arrival (`TrapEvent.ts`, stamped when the datagram was
received) to the commit of the batch that placed it — the instant every reader can see where it
went. Nothing here shortens one trap's budget because an earlier one is still waiting; what a
storm costs a trap is the time it spends **behind** the traps that arrived before it, and that is
exactly what this measures.

What it keeps, all in memory and bounded:

* the last :data:`SAMPLES` latencies, for percentiles;
* how many traps were classified, how many of them **late**, the worst latency since start;
* how many were **lost** to a rolled-back batch (F11) — never classified, so a miss.

The queue-full drops the receiver counts are misses too; the caller adds them (they are the
receiver's counter, not this one's). No lock, no I/O, no await: the engine calls
:meth:`DeadlineTracker.committed` once per committed transaction.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from netcorenoc.ingest.events import TrapEvent

__all__ = ["DEADLINE_S", "SAMPLES", "DeadlineTracker"]

#: The default per-trap deadline, in seconds (``NETCORENOC_CLASSIFY_DEADLINE_S``).
DEADLINE_S = 60.0
#: Latencies kept for the percentiles: the newest ones, a bounded window.
SAMPLES = 4096


@dataclass
class DeadlineTracker:
    deadline_s: float = DEADLINE_S
    latencies: deque[float] = field(default_factory=lambda: deque(maxlen=SAMPLES))
    classified: int = 0
    late: int = 0
    lost: int = 0
    worst_s: float = 0.0

    def committed(self, items: Sequence[object], now: float) -> None:
        """The traps among ``items`` were classified at ``now`` (their batch committed)."""
        for item in items:
            if not isinstance(item, TrapEvent):
                continue  # a quarantined packet is stored, not classified
            waited = max(0.0, now - item.ts)
            self.latencies.append(waited)
            self.classified += 1
            if waited > self.deadline_s:
                self.late += 1
            if waited > self.worst_s:
                self.worst_s = waited

    def rolled_back(self, items: Sequence[object]) -> None:
        """The traps among ``items`` were dropped with a rolled-back batch: never classified."""
        self.lost += sum(1 for item in items if isinstance(item, TrapEvent))

    def percentile(self, q: float) -> float:
        """The ``q`` quantile (0..1) of the kept latencies; 0.0 before any trap."""
        if not self.latencies:
            return 0.0
        ordered = sorted(self.latencies)
        return ordered[min(len(ordered) - 1, int(q * len(ordered)))]

    @staticmethod
    def head_age(queue: asyncio.Queue[Any], now: float) -> float:
        """How long the oldest trap still queued has been waiting; 0.0 for an empty queue.

        Reads the queue's own deque (`asyncio.Queue._queue`, a CPython attribute stable since 3.4)
        rather than keeping a second record of what is queued: a copy could disagree with it.
        """
        pending = getattr(queue, "_queue", None)
        if not pending:
            return 0.0
        return max(0.0, now - float(getattr(pending[0], "ts", now)))

    def report(self, queue: asyncio.Queue[Any], now: float, dropped: int) -> dict[str, Any]:
        """The numbers `GET /api/stats` publishes as ``classification``."""
        ordered = sorted(self.latencies)

        def at(q: float) -> float:
            return (
                round(ordered[min(len(ordered) - 1, int(q * len(ordered)))], 4) if ordered else 0.0
            )

        return {
            "deadline_s": self.deadline_s,
            "classified": self.classified,
            "late": self.late,
            "lost": self.lost,
            "dropped": dropped,
            "p50_s": at(0.50),
            "p95_s": at(0.95),
            "p99_s": at(0.99),
            "worst_s": round(self.worst_s, 4),
            "head_age_s": round(self.head_age(queue, now), 4),
        }

    def warnings(self, dropped: int) -> list[str]:
        """One sentence when any trap missed its deadline; silent otherwise."""
        missed = self.late + self.lost + dropped
        if not missed:
            return []
        parts = []
        if self.late:
            parts.append(f"{self.late} classified late (worst {self.worst_s:.1f} s)")
        if self.lost:
            parts.append(f"{self.lost} lost to a storage error")
        if dropped:
            parts.append(f"{dropped} dropped at a full queue")
        return [
            f"{missed} trap(s) missed the {self.deadline_s:g} s classification deadline: "
            + "; ".join(parts)
            + ". Each trap's deadline runs from its own arrival; a miss means the appliance fell "
            "behind the trap rate. Check CPU and the queue depth on the Overview."
        ]
