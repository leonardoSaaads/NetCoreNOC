"""Flap detection: a fingerprint that re-activates on a short, regular period is noise.

Split out of `engine.py` in v0.26.0 to keep the ingest path inside its audited size (the cohesion
exemption in `tests/test_architecture.py`): this is a pure, in-memory detector with no lock, no
I/O and no await, so reading it does not require reading the batch lock and vice versa. The engine
calls :meth:`FlapDetector.observe` once per activation and :meth:`FlapDetector.recent` for the
``chatter`` pair feature (ADR #418).
"""

from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass, field

from netcorenoc.ingest.events import Fingerprint

__all__ = ["FlapDetector"]


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

    def recent(self, fingerprint: Fingerprint, ts: float) -> int:
        """Activations of ``fingerprint`` in the past hour, from the history already kept (≤ 8)."""
        raises = self.history.get(fingerprint)
        return 0 if raises is None else sum(1 for t in raises if ts - t <= 3600.0)
