"""Each trap's own clock, against the classification KPI (ADRs #447, #451).

A trap's **decision latency** runs from its own arrival (`TrapEvent.ts`, stamped when the kernel
received the datagram) to the commit of the batch that placed it in a situation — the instant every
reader can see where it went. A trap that arrives 20 s after another has its own full budget; what
a storm costs a trap is the time it spends **behind** the traps that arrived before it, and that is
what this measures.

## The KPI is two numbers an administrator sets, and neither discards anything

* the **target** (default 60 s): where a trap should be classified by;
* the **limit** (default 120 s): beyond it the appliance has failed the trap's KPI.

A trap past either is still classified — nothing on this path expires or drops a trap — and it is
**counted**: classified, over the target, over the limit. That is the shape of an SLO (an objective
and a breach) and of a Zabbix trigger pair (a warning and a high threshold).

## Zabbix's delay buckets

Zabbix's *Queue* screen sorts what is waiting into 5 s, 10 s, 30 s, 1 min, 5 min and 10 min+. The
same edges, plus 2 min, sort every classified trap by how long it took (:data:`DELAY_EDGES`), so
"how late" has a distribution and not only a percentile.

## What it keeps, all in memory and bounded

* the last :data:`SAMPLES` latencies, for the live percentiles;
* cumulative counts since start, the worst latency, the delay buckets;
* the current **interval**'s latencies (at most :data:`INTERVAL_SAMPLES`) and counts, which the
  sampler takes every 30 s and persists (`host_sample`, migration 0030) so the Overview can draw
  any range the operator picks.

No lock, no I/O, no await: the engine calls :meth:`DeadlineTracker.committed` once per committed
transaction.
"""

from __future__ import annotations

import json
import math
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from netcorenoc.ingest.events import TrapEvent

__all__ = [
    "DELAY_EDGES",
    "INTERVAL_SAMPLES",
    "KPI_KEY",
    "LIMIT_S",
    "MAX_KPI_S",
    "SAMPLES",
    "TARGET_S",
    "DeadlineTracker",
    "Kpi",
]

#: The ``meta`` row an administrator's KPI is stored in; it overrides the environment's defaults.
KPI_KEY = "config.kpi"
#: The defaults (``NETCORENOC_KPI_TARGET_S``, ``NETCORENOC_KPI_LIMIT_S``).
TARGET_S = 60.0
LIMIT_S = 120.0
#: The longest KPI an administrator may set: a day. A larger one would measure nothing.
MAX_KPI_S = 86_400.0
#: Latencies kept for the live percentiles: the newest ones, a bounded window.
SAMPLES = 4096
#: Latencies kept for one sampler interval's percentiles (about 30 s of a 1 600 traps/s storm).
INTERVAL_SAMPLES = 50_000
#: The upper edge of each delay bucket, in seconds; one more bucket holds everything beyond.
DELAY_EDGES = (5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0)


@dataclass(frozen=True)
class Kpi:
    """The classification KPI: a target and a limit, in seconds, ``0 < target <= limit``."""

    target_s: float = TARGET_S
    limit_s: float = LIMIT_S

    @staticmethod
    def problems(target_s: float, limit_s: float) -> list[str]:
        out = []
        for name, value in (("target", target_s), ("limit", limit_s)):
            if not (math.isfinite(value) and 0 < value <= MAX_KPI_S):
                out.append(f"the {name} must be above 0 and at most {MAX_KPI_S:g} seconds")
        if not out and target_s > limit_s:
            out.append("the target cannot be later than the limit")
        return out

    def to_document(self) -> str:
        return json.dumps({"target_s": self.target_s, "limit_s": self.limit_s})

    @classmethod
    def from_document(cls, document: str | None, default: Kpi) -> Kpi:
        """The stored KPI, or ``default`` when none is stored or the row is not a valid one."""
        if not document:
            return default
        try:
            data = json.loads(document)
            target, limit = float(data["target_s"]), float(data["limit_s"])
        except (ValueError, TypeError, KeyError):
            return default
        return default if cls.problems(target, limit) else cls(target, limit)


def _quantile(ordered: Sequence[float], q: float) -> float:
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))] if ordered else 0.0


@dataclass
class DeadlineTracker:
    kpi: Kpi = field(default_factory=Kpi)
    latencies: deque[float] = field(default_factory=lambda: deque(maxlen=SAMPLES))
    classified: int = 0
    over_target: int = 0
    over_limit: int = 0
    worst_s: float = 0.0
    delays: list[int] = field(default_factory=lambda: [0] * (len(DELAY_EDGES) + 1))
    # The current interval, taken by the sampler.
    _window: list[float] = field(default_factory=list)
    _window_classified: int = 0
    _window_over_target: int = 0
    _window_over_limit: int = 0
    _window_worst: float = 0.0

    def committed(self, items: Sequence[object], now: float) -> None:
        """The traps among ``items`` were classified at ``now`` (their batch committed)."""
        target, limit = self.kpi.target_s, self.kpi.limit_s
        for item in items:
            if not isinstance(item, TrapEvent):
                continue  # a quarantined packet is stored, not classified
            waited = max(0.0, now - item.ts)
            self.latencies.append(waited)
            self.classified += 1
            self._window_classified += 1
            if len(self._window) < INTERVAL_SAMPLES:
                self._window.append(waited)
            if waited > target:
                self.over_target += 1
                self._window_over_target += 1
            if waited > limit:
                self.over_limit += 1
                self._window_over_limit += 1
            self.worst_s = max(self.worst_s, waited)
            self._window_worst = max(self._window_worst, waited)
            bucket = 0
            while bucket < len(DELAY_EDGES) and waited > DELAY_EDGES[bucket]:
                bucket += 1
            self.delays[bucket] += 1

    def percentile(self, q: float) -> float:
        """The ``q`` quantile (0..1) of the kept latencies; 0.0 before any trap."""
        return _quantile(sorted(self.latencies), q)

    def take_interval(self) -> dict[str, Any]:
        """What the sampler persists for the interval since its last call, then a new interval.

        The percentiles are ``None`` for an interval in which nothing was classified: an idle
        appliance has no latency, and a zero would be a measurement nobody made.
        """
        ordered = sorted(self._window)
        out = {
            "classified": self._window_classified,
            "over_target": self._window_over_target,
            "over_limit": self._window_over_limit,
            "p95_s": _quantile(ordered, 0.95) if ordered else None,
            "max_s": self._window_worst if ordered else None,
        }
        self._window = []
        self._window_classified = self._window_over_target = self._window_over_limit = 0
        self._window_worst = 0.0
        return out

    def report(self, oldest_wait_s: float, dropped: int) -> dict[str, Any]:
        """The numbers `GET /api/stats` publishes as ``classification``."""
        ordered = sorted(self.latencies)

        def share(over: int) -> float | None:
            if not self.classified:
                return None
            return round(100.0 * (self.classified - over) / self.classified, 3)

        return {
            "target_s": self.kpi.target_s,
            "limit_s": self.kpi.limit_s,
            "classified": self.classified,
            "over_target": self.over_target,
            "over_limit": self.over_limit,
            "within_target_pct": share(self.over_target),
            "within_limit_pct": share(self.over_limit),
            "dropped": dropped,
            "p50_s": round(_quantile(ordered, 0.50), 4),
            "p95_s": round(_quantile(ordered, 0.95), 4),
            "p99_s": round(_quantile(ordered, 0.99), 4),
            "worst_s": round(self.worst_s, 4),
            "oldest_wait_s": round(oldest_wait_s, 4),
            "delays": [
                {"upto_s": edge, "count": count}
                for edge, count in zip((*DELAY_EDGES, None), self.delays, strict=True)
            ],
        }

    def warnings(self, dropped: int) -> list[str]:
        """One sentence when a trap breached the KPI limit or was dropped; silent otherwise.

        Over the **target** is a number on the Overview, not a banner: the target is where the
        appliance should be, and an operator is interrupted for the limit.
        """
        if not (self.over_limit or dropped):
            return []
        parts = []
        if self.over_limit:
            parts.append(
                f"{self.over_limit} took longer than the {self.kpi.limit_s:g} s KPI limit to "
                f"classify (worst {self.worst_s:.1f} s) and were classified late, not discarded"
            )
        if dropped:
            parts.append(f"{dropped} were dropped because the trap spool could not take them")
        return [
            "Since start, " + "; ".join(parts) + ". Each trap's clock runs from its own arrival; "
            "check CPU and the trap pipeline on the Overview."
        ]
