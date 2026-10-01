"""The league's live shadow: every challenger scores a sample of the champion's own pairs.

v0.27.0 (ADR #423). The fast loop runs one member of the league, the champion. The others are
**challengers**, and this is how they run beside it on live traffic without costing it anything that
matters: for a bounded sample of each activation's candidate pairs — the vectors the champion's
scoring already built — each challenger computes its own logit, and the two link verdicts are
compared. In a storm only one activation in :data:`STORM_STRIDE` is sampled.

**Observability, not evidence.** How often a challenger agrees with the champion says nothing about
which is right; operator labels do, and the slow loop's judge reads those (`league_judge.compare`).
These counters are in memory, never persisted, and no promotion path reads them — the rule
`CorrelationMonitor` has kept since v0.18.0. They answer the operator's question *"would the
others have grouped this differently?"* while the evidence accumulates.

No I/O, no lock, no clock, bounded work; ``observe`` never raises.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from netcorenoc.engine.correlate.learn import STORM_ALARMS
from netcorenoc.engine.correlate.pairs import CorrelationResult

__all__ = ["SAMPLE_PAIRS", "STORM_STRIDE", "LeagueShadow"]

SAMPLE_PAIRS = 12
STORM_STRIDE = 8


class _Challenger(Protocol):
    @property
    def ref(self) -> str: ...

    @property
    def scorer(self) -> Any: ...


@dataclass
class _Tally:
    pairs: int = 0
    agree: int = 0
    champion_only: int = 0  # the champion linked, the challenger would not
    challenger_only: int = 0  # the challenger would have linked, the champion did not
    margin_gap: float = 0.0  # sum of |challenger margin - champion margin|, in log-odds


class LeagueShadow:
    def __init__(self) -> None:
        self.champion: str | None = None
        self.tallies: dict[str, _Tally] = {}
        self.activations = 0
        self.sampled = 0
        self.errors = 0

    def reset(self, champion: str) -> None:
        """A new champion: the tallies compared against the old one no longer mean anything."""
        self.__init__()  # type: ignore[misc]
        self.champion = champion

    def observe(
        self,
        alarm_id: int,
        outcome: CorrelationResult,
        challengers: Sequence[_Challenger],
        burst: int,
    ) -> None:
        if not challengers:
            return
        self.activations += 1
        if burst >= STORM_ALARMS and alarm_id % STORM_STRIDE:
            return
        pairs = [p for p in outcome.evaluated if p.vector is not None][:SAMPLE_PAIRS]
        if not pairs:
            return
        self.sampled += 1
        for member in challengers:
            tally = self.tallies.setdefault(member.ref, _Tally())
            try:
                threshold = float(member.scorer.threshold)
                for pair in pairs:
                    margin = float(member.scorer.logit(pair.vector)) - threshold
                    linked = margin > 0.0
                    tally.pairs += 1
                    if linked == pair.linked:
                        tally.agree += 1
                    elif pair.linked:
                        tally.champion_only += 1
                    else:
                        tally.challenger_only += 1
                    tally.margin_gap += abs(margin - pair.evidence)
            except Exception:  # a challenger must never take the engine down
                self.errors += 1

    def snapshot(self) -> dict[str, Any]:
        return {
            "dataset": "live",
            "champion": self.champion,
            "activations": self.activations,
            "sampled": self.sampled,
            "errors": self.errors,
            "sample_pairs": SAMPLE_PAIRS,
            "storm_stride": STORM_STRIDE,
            "challengers": {
                ref: {
                    "pairs": t.pairs,
                    "agreement": (t.agree / t.pairs) if t.pairs else None,
                    "champion_only": t.champion_only,
                    "challenger_only": t.challenger_only,
                    "mean_margin_gap": (t.margin_gap / t.pairs) if t.pairs else None,
                }
                for ref, t in sorted(self.tallies.items())
            },
        }
