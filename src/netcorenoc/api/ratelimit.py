"""The perimeter's rate limits: a token bucket per client address (moved out of `perimeter.py`
when ADR #508 took it past the size guard; behaviour unchanged).
"""

from __future__ import annotations

RATE_CAPACITY = 30.0
RATE_REFILL = 10.0
# Preview re-partitions up to MAX_PREVIEW_ALARMS alarms, so it is the one endpoint whose cost is
# worth more than a share of the general bucket: its own, much tighter bucket (3 burst, then one
# roughly every 10 s) bounds it independently of the per-client limiter (F22).
PREVIEW_RATE_CAPACITY = 3.0
PREVIEW_RATE_REFILL = 0.1


class RateLimiter:
    """Token bucket per client address; deliberately small and in-memory."""

    def __init__(self, capacity: float, refill: float) -> None:
        self.capacity = capacity
        self.refill = refill
        self.buckets: dict[str, tuple[float, float]] = {}

    def allow(self, key: str, now: float) -> bool:
        tokens, last = self.buckets.get(key, (self.capacity, now))
        tokens = min(self.capacity, tokens + (now - last) * self.refill)
        if len(self.buckets) > 4096:
            self._bound(now)
        if tokens < 1.0:
            self.buckets[key] = (tokens, now)
            return False
        self.buckets[key] = (tokens - 1.0, now)
        return True

    def _bound(self, now: float) -> None:
        """Forget the fullest buckets first: a full bucket is indistinguishable from no bucket,
        and the emptiest belong to the clients being limited. Until v0.28.0 this cleared every
        bucket, so 4 097 addresses touching the API reset the limit for the one client actually
        hammering it."""

        def level(item: tuple[str, tuple[float, float]]) -> float:
            tokens, last = item[1]
            return min(self.capacity, tokens + (now - last) * self.refill)

        self.buckets = dict(sorted(self.buckets.items(), key=level)[:2048])
