"""Temporal-precedence statistics and the probable-root heuristic.

For every ordered pair of classes (and of devices) the engine accumulates "who fires
first" mass. Within a situation, the alarm whose class and device most consistently lead
the others is flagged as the probable root; with no learned precedence yet, the earliest
alarm wins. Deliberately lightweight and explainable — no Hawkes processes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from netcorenoc.store import EdgeRow, Store

ROOT_SAMPLE = 25  # earliest members scored when picking a root (bounded work)
EARLIEST_BONUS = 0.001  # tie-breaker favouring earlier alarms


@dataclass(frozen=True)
class Member:
    """One situation member as the root heuristic sees it."""

    alarm_id: int
    class_id: int
    device_id: int
    first_seen: float


@dataclass
class Precedence:
    class_wins: dict[tuple[int, int], float] = field(default_factory=dict)
    device_wins: dict[tuple[int, int], float] = field(default_factory=dict)
    dirty_class: set[tuple[int, int]] = field(default_factory=set)
    dirty_device: set[tuple[int, int]] = field(default_factory=set)

    def observe(self, lead: tuple[int, int], lag: tuple[int, int], weight: float = 1.0) -> None:
        """Record that `lead` (class_id, device_id) fired before `lag`."""
        if lead[0] != lag[0]:
            key = (lead[0], lag[0])
            self.class_wins[key] = self.class_wins.get(key, 0.0) + weight
            self.dirty_class.add(key)
        if lead[1] != lag[1]:
            key = (lead[1], lag[1])
            self.device_wins[key] = self.device_wins.get(key, 0.0) + weight
            self.dirty_device.add(key)

    @staticmethod
    def _prob(wins: dict[tuple[int, int], float], a: int, b: int) -> float:
        forward = wins.get((a, b), 0.0)
        backward = wins.get((b, a), 0.0)
        if a == b or forward + backward <= 0.0:
            return 0.5
        return forward / (forward + backward)

    def class_lead_prob(self, a: int, b: int) -> float:
        return self._prob(self.class_wins, a, b)

    def device_lead_prob(self, a: int, b: int) -> float:
        return self._prob(self.device_wins, a, b)

    def pick_root(self, members: list[Member]) -> int | None:
        """The member whose class/device most consistently lead the others."""
        if not members:
            return None
        sample = sorted(members, key=lambda m: m.first_seen)[:ROOT_SAMPLE]
        best_id, best_score = sample[0].alarm_id, float("-inf")
        for index, candidate in enumerate(sample):
            score = -EARLIEST_BONUS * index
            for other in sample:
                if other.alarm_id == candidate.alarm_id:
                    continue
                score += self.class_lead_prob(candidate.class_id, other.class_id)
                score += self.device_lead_prob(candidate.device_id, other.device_id)
            if score > best_score:
                best_id, best_score = candidate.alarm_id, score
        return best_id

    async def save(self, store: Store, ts: float) -> None:
        rows = [
            EdgeRow("lead_class", a, b, self.class_wins[(a, b)], self.class_wins[(a, b)], 0)
            for a, b in sorted(self.dirty_class)
        ] + [
            EdgeRow("lead_device", a, b, self.device_wins[(a, b)], self.device_wins[(a, b)], 0)
            for a, b in sorted(self.dirty_device)
        ]
        if rows:
            await store.upsert_edges(rows, ts)
        self.dirty_class.clear()
        self.dirty_device.clear()

    async def load(self, store: Store) -> None:
        for edge in await store.load_edges("lead_class"):
            self.class_wins[(edge.a_id, edge.b_id)] = edge.weight
        for edge in await store.load_edges("lead_device"):
            self.device_wins[(edge.a_id, edge.b_id)] = edge.weight
