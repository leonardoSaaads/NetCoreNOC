"""The league's judge: which member decides, and why — from day 0, with no labelling floors.

v0.27.0 (ADRs #423, #425). The judge is the slow loop's decision. It is **registered here before any
member was trained for this release**, so the rule cannot have been chosen to suit a result.

## Day 0: the offline order

Every member's manifest carries its measurements on data it never trained or tuned on. The judge
reads five **suites** from it — the four generated test splits (i.i.d., concurrency, optical and
protocol families held out) and the hand-labelled `eval/corpus`, which a different program
generated — and ranks by the **mean pairwise F1 over the five**, each suite weighing the same.
Ties within :data:`TIE` are broken by fewer operator repair gestures per incident on the generated
splits. A member missing a suite ranks after every member that has all five.

Pairwise F1 because it is the one grouping measure every suite reports on the same scale; the mean
because no suite is the network an appliance will meet, and a member that is excellent on four and
poor on the fifth is the member the fifth is there to catch.

**The corpus suite is the mean over its scenarios** (:func:`corpus_score`), each scenario weighing
the same — not the corpus's pooled pairwise F1. The pooled number counts pairs, and three storms of
a thousand alarms or more hold almost all of them: a member that split a ten-alarm fibre cut and
merged every background-noise alarm still pooled to 1.000. That is the measurement `make eval`'s
aggregate gate has always taken, and it was blind to exactly the scenarios the corpus exists for.
*This definition was corrected after the first member's corpus numbers were read and before any
other member's were* (ADR #429 records it).

## The fast loop's budget

A member is **eligible** only if its median scoring time per pair, measured on this appliance at
load over the packaged benchmark pairs, is within :data:`LATENCY_BUDGET_US`. A storm puts a hundred
candidates or more in front of the scorer for every alarm; a model too slow for that is not a
champion however well it ranks, and the table says so rather than hiding it.

## With site labels: a paired comparison, and the interval is the only floor

Every operator gesture that asserts a grouping is a label, and the captured pairs carry the vector
each model was served. Pre-trained members never saw them, so **every** labelled incident is
out-of-sample for them. For each challenger, per incident, the difference of mean log loss
``challenger - champion`` is computed, and a **95 % t-interval** is put on its mean over incidents.

* the whole interval below zero -> the challenger is better on this site: it becomes champion
  (the best such challenger, by mean difference);
* otherwise the champion stays.

**There is no count floor** (the v0.14.0 floors of 50 / 20 / 30 / 3 and v0.26.0's 20 / 6 / 4 /
3 days are retired). The t-interval widens as the sample shrinks — at two incidents its
half-width is 12.7 standard errors — so a handful of labels cannot move the champion unless they
are unanimous and large, and the operator never waits for a threshold before the models decide:
they decide from the first trap, and the labels only ever *re-order* them.

## An admin can pin

A pinned member decides regardless of the ranking, and the table still shows where it stands.
"""

from __future__ import annotations

import math
import statistics
import time
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from netcorenoc.engine.model.league import League, Member

__all__ = [
    "LATENCY_BUDGET_US",
    "SUITES",
    "TIE",
    "Choice",
    "Comparison",
    "choose",
    "compare",
    "corpus_score",
    "measure_latency",
    "offline_score",
    "offline_table",
    "t_quantile",
]

SUITES: tuple[str, ...] = (
    "test_iid",
    "test_concurrency",
    "test_optical",
    "test_protocol",
    "corpus",
)
GENERATED = SUITES[:4]
TIE = 0.005
LATENCY_BUDGET_US = 150.0


def suites(member: Member) -> dict[str, float]:
    """Pairwise F1 per suite, from the member's manifest. Absent suites are absent, not zero."""
    m = member.manifest
    out: dict[str, float] = {}
    splits = (m.get("evaluation") or {}).get("splits") or {}
    for name in GENERATED:
        try:
            out[name] = float(splits[name]["model"]["pairwise_f1"]["point"])
        except (KeyError, TypeError, ValueError):
            continue
    corpus = corpus_score(m)
    if corpus is not None:
        out["corpus"] = corpus
    return {k: v for k, v in out.items() if math.isfinite(v)}


def corpus_score(manifest: dict[str, Any]) -> float | None:
    """The hand-labelled corpus as one number: pairwise F1 averaged over its scenarios, each
    weighing the same (see the module docstring for why not the pooled F1)."""
    try:
        scenarios = manifest["corpus"]["scenarios"]
        values = [float(s["pairwise_f1"]) for s in scenarios.values()]
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    values = [v for v in values if math.isfinite(v)]
    return statistics.fmean(values) if values else None


def repair(member: Member) -> float:
    """Repair gestures per incident, pooled as a mean over the generated splits."""
    splits = (member.manifest.get("evaluation") or {}).get("splits") or {}
    values = []
    for name in GENERATED:
        try:
            values.append(float(splits[name]["model"]["repair_gestures"]["point"]))
        except (KeyError, TypeError, ValueError):
            continue
    return statistics.fmean(values) if values else math.inf


def offline_score(member: Member) -> float:
    got = suites(member)
    return statistics.fmean(got.values()) if got else math.nan


def _order_key(member: Member) -> tuple[int, float, float, str]:
    got = suites(member)
    score = offline_score(member)
    complete = 0 if len(got) == len(SUITES) else 1
    # Scores are compared at the tie resolution: two members within TIE are equal on this key.
    bucket = -math.floor(score / TIE) if math.isfinite(score) else math.inf
    return (complete, bucket, repair(member), member.kind)


def offline_table(league: League) -> list[dict[str, Any]]:
    """Every member in the offline order, with the numbers that placed it there."""
    ordered = sorted(league.members, key=_order_key)
    return [
        {
            "rank": i + 1,
            "kind": m.kind,
            "name": m.name,
            "ref": m.ref,
            "score": round(offline_score(m), 4),
            "suites": {k: round(v, 4) for k, v in suites(m).items()},
            "repair_gestures": round(repair(m), 4),
        }
        for i, m in enumerate(ordered)
    ]


def measure_latency(member: Member, vectors: Sequence[tuple[float, ...]]) -> float:
    """Median microseconds per pair on the fast path, over three passes. Off the ingest path."""
    if not vectors:
        return 0.0
    passes = []
    for _ in range(3):
        started = time.perf_counter()
        for v in vectors:
            member.scorer.logit(v)
        passes.append((time.perf_counter() - started) / len(vectors) * 1e6)
    return statistics.median(passes)


# -- the paired site comparison -----------------------------------------------------------------

#: Two-sided 97.5 % quantiles of Student's t for 1-30 degrees of freedom.
_T = (
    12.706,
    4.303,
    3.182,
    2.776,
    2.571,
    2.447,
    2.365,
    2.306,
    2.262,
    2.228,
    2.201,
    2.179,
    2.160,
    2.145,
    2.131,
    2.120,
    2.110,
    2.101,
    2.093,
    2.086,
    2.080,
    2.074,
    2.069,
    2.064,
    2.060,
    2.056,
    2.052,
    2.048,
    2.045,
    2.042,
)


def t_quantile(df: int) -> float:
    """The 97.5 % quantile of t with ``df`` degrees of freedom (a table, then Cornish-Fisher)."""
    if df < 1:
        return math.inf
    if df <= len(_T):
        return _T[df - 1]
    z = 1.959964
    return z + (z**3 + z) / (4 * df) + (5 * z**5 + 16 * z**3 + 3 * z) / (96 * df * df)


@dataclass
class Comparison:
    challenger: str
    champion: str
    incidents: int
    mean: float = math.nan  # challenger - champion, mean per-incident log loss
    low: float = math.nan
    high: float = math.nan
    verdict: str = "no_evidence"  # better | worse | undecided | no_evidence

    def as_dict(self) -> dict[str, Any]:
        return {
            k: (None if isinstance(v, float) and not math.isfinite(v) else v)
            for k, v in asdict(self).items()
        }


def _ll(member: Member, rows: Sequence[Any]) -> float:
    total = mass = 0.0
    for r in rows:
        z = max(-700.0, min(700.0, member.scorer.logit(r.x)))
        p = min(max(1.0 / (1.0 + math.exp(-z)), 1e-12), 1.0 - 1e-12)
        total -= r.w * (math.log(p) if r.y else math.log(1.0 - p))
        mass += r.w
    return total / mass if mass else 0.0


def compare(champion: Member, challenger: Member, rows: Sequence[Any]) -> Comparison:
    """Per-incident paired log-loss difference with a 95 % t-interval (see the module docstring).
    ``rows`` are `site.SiteRow`s: a vector, a label, a weight and the incident they belong to."""
    by_incident: dict[int, list[Any]] = defaultdict(list)
    for r in rows:
        by_incident[r.incident].append(r)
    diffs = [
        _ll(challenger, group) - _ll(champion, group) for _, group in sorted(by_incident.items())
    ]
    out = Comparison(challenger.ref, champion.ref, len(diffs))
    if len(diffs) < 2:
        return out
    mean = statistics.fmean(diffs)
    half = t_quantile(len(diffs) - 1) * statistics.stdev(diffs) / math.sqrt(len(diffs))
    out.mean, out.low, out.high = mean, mean - half, mean + half
    out.verdict = "better" if out.high < 0.0 else "worse" if out.low > 0.0 else "undecided"
    return out


def _fitted_at(member: Member) -> float:
    try:
        return float(member.manifest["provenance"]["fitted_at"])
    except (KeyError, TypeError, ValueError):
        return 0.0


def _out_of_sample(rows: Sequence[Any], *members: Member) -> list[Any]:
    """The labels no member of the pair was fitted on. Pre-trained members never saw a site label,
    so for them that is every row; a **site** member was fitted on the labels before its fit, so
    a comparison involving one reads only the labels that arrived after it (ADR #425)."""
    since = max((_fitted_at(m) for m in members if m.origin == "site"), default=None)
    if since is None:
        return list(rows)
    return [r for r in rows if float(r.label_at) > since]


@dataclass
class Choice:
    champion: str
    reason: str
    table: list[dict[str, Any]] = field(default_factory=list)
    comparisons: list[dict[str, Any]] = field(default_factory=list)
    ineligible: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def choose(
    league: League,
    *,
    rows: Sequence[Any] = (),
    current: str | None = None,
    pinned: str | None = None,
    latency_us: dict[str, float] | None = None,
) -> Choice | None:
    """The champion, and every reason. ``None`` only for an empty league (a build fault)."""
    if not league.members:
        return None
    latency_us = latency_us or {}
    table = offline_table(league)
    ineligible = {
        ref: f"{us:.0f} µs per pair, over the fast loop's {LATENCY_BUDGET_US:.0f} µs budget"
        for ref, us in latency_us.items()
        if us > LATENCY_BUDGET_US
    }
    for row in table:
        row["latency_us"] = round(latency_us[row["ref"]], 1) if row["ref"] in latency_us else None
        row["eligible"] = row["ref"] not in ineligible
    eligible = [league.by_ref(r["ref"]) for r in table if r["eligible"]]
    candidates = [m for m in eligible if m is not None]
    if pinned is not None and league.by_ref(pinned) is not None:
        return Choice(pinned, "pinned by an admin", table, [], ineligible)
    if not candidates:  # every member over budget: the fastest decides, and the table says why
        fastest = min(league.members, key=lambda m: latency_us.get(m.ref, math.inf))
        return Choice(
            fastest.ref,
            "every member is over the latency budget; the fastest decides",
            table,
            [],
            ineligible,
        )
    incumbent = next((m for m in candidates if m.ref == current), None)
    reason = "kept: no challenger is better on this site's labels"
    if incumbent is None:
        incumbent = candidates[0]
        reason = "first in the offline order (no site evidence decides otherwise)"
    comparisons = [
        compare(incumbent, m, _out_of_sample(rows, incumbent, m))
        for m in candidates
        if m is not incumbent
    ]
    better = [c for c in comparisons if c.verdict == "better"]
    if better:
        best = min(better, key=lambda c: (c.mean, c.challenger))
        return Choice(
            best.challenger,
            f"better than {incumbent.name} on this site's labels "
            f"({best.incidents} incidents, 95 % interval {best.low:+.3f} to {best.high:+.3f} nats)",
            table,
            [c.as_dict() for c in comparisons],
            ineligible,
        )
    if not rows:
        reason = f"{_why_first(incumbent, candidates)}; no site labels yet"
    return Choice(incumbent.ref, reason, table, [c.as_dict() for c in comparisons], ineligible)


def _why_first(first: Member, candidates: Sequence[Member]) -> str:
    """Why the offline order put ``first`` first, in the words an operator reads: the best score,
    or — when others are within :data:`TIE` of it — the fewest repair gestures among them."""
    key = _order_key(first)
    tied = [m for m in candidates if m is not first and _order_key(m)[:2] == key[:2]]
    score = offline_score(first)
    if not tied:
        return f"the best offline score ({score:.3f}, mean pairwise F1 over five suites)"
    names = ", ".join(f"{m.name} {offline_score(m):.3f}" for m in tied)
    return (
        f"offline score {score:.3f}, tied within {TIE} with {names}; first on the tie-break, "
        f"the fewest repair gestures per incident ({repair(first):.3f})"
    )
