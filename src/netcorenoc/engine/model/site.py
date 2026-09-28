"""Adapting the shipped model to one site, and judging whether the adaptation is better.

v0.26.0 (ADRs #411, #413). The question the promotion gate used to ask was *"trust this model or
nothing?"*, and it needed fifty split bags from three operators to answer it — a team size, not an
evidence standard, which a two-person NOC can never meet. With a validated model already running,
the question changes shape:

> **Does the site-adapted model beat the shipped one, on this site's own labelled traffic?**

That is a **paired** comparison — both models score the same pairs from the same incidents — and a
paired comparison needs far less data than an absolute one, because each incident is its own
control and the between-incident variance cancels.

## The adaptation

A site model is the shipped model **plus** boosting rounds fitted on the site's labelled pairs,
starting from the shipped model's logit and using its bins (`gam_fit.fit(base=...)`). It cannot
forget what it was shipped with faster than the site's evidence pushes it, and a site with little
evidence gets a model that is nearly the shipped one — which is the right prior.

## The judgement (`judge`), three-valued as the project has always had it

The newest 30 % of labelled bags (by label time) are held out — time-ordered, because the model
predicts the future from the past. On them, per incident, the mean log loss of each model; the
per-incident difference ``site − shipped`` is bootstrapped over incidents.

* ``BETTER`` — the upper end of the 95 % interval is below zero, **and** the site model does no
  harm on the shipped benchmark (below);
* ``NOT_BETTER`` — the interval lies at or above zero, or the site model harms the benchmark;
* ``INSUFFICIENT_EVIDENCE`` — a floor is unmet or the interval straddles zero. Not terminal (ADR
  #411): the next search on more labels asks again.

## The floors, and what replaced `operators >= 3`

`operators >= 3` protected against one person's idiosyncratic labels teaching the appliance their
habits. Two checks protect against that and a two-person team can pass both:

* **temporal spread** — labels from at least :data:`MIN_LABEL_DAYS` distinct days, so one bad
  shift, one mis-configured session or one afternoon's mood cannot be the whole evidence;
* **do no harm** — the site model's log loss on the **shipped benchmark** (held-out generated pairs
  packaged with the model) may not exceed the shipped model's by more than :data:`HARM_MARGIN`. One
  person's labels can make the model better *here*; they cannot make it forget what a fibre cut
  looks like, because the benchmark was written before anyone labelled anything.

The numeric floors and their power argument are in ADR #411 and :data:`FLOORS`.
"""

from __future__ import annotations

import json
import math
import random
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any

from netcorenoc.engine.correlate.features import FEATURE_NAMES
from netcorenoc.engine.model import confidence as confidence_rules
from netcorenoc.engine.model import gam, gam_fit

__all__ = [
    "FLOORS",
    "HARM_MARGIN",
    "MIN_LABEL_DAYS",
    "Judgement",
    "SiteRow",
    "adapt",
    "judge",
    "rows_from",
    "split_by_time",
]

#: Minimum labelled incidents overall, in the held-out newer part, and bags asserting a negative.
FLOORS = {"incidents": 20, "test_incidents": 6, "negative_bags": 4}
MIN_LABEL_DAYS = 3
HARM_MARGIN = 0.02  # nats of benchmark log loss
TEST_FRACTION = 0.3
REPLICATES = 400


@dataclass(frozen=True)
class SiteRow:
    x: tuple[float, ...]
    y: int
    w: float
    bag: tuple[str, int]
    incident: int
    label_at: float


def rows_from(pairs: list[dict[str, Any]], features: dict[int, str]) -> list[SiteRow]:
    """Labelled pairs that carry a captured v2 vector, as training rows (policy A labels).

    A pair captured before v0.26.0 has no vector and is skipped — its features were never recorded,
    and re-deriving them now would be training on numbers the scorer never saw. One unit of weight
    per bag, times the operator's confidence multiplier; no class balancing, which would bend the
    probabilities this model is judged on.
    """
    by_bag: dict[tuple[str, int], list[tuple[dict[str, Any], tuple[float, ...]]]] = defaultdict(list)
    for pair in pairs:
        raw = features.get(int(pair["pair_id"]))
        if raw is None or not confidence_rules.admits(pair.get("confidence")):
            continue
        vec = tuple(float(v) for v in json.loads(raw))
        if len(vec) != len(FEATURE_NAMES):
            continue  # a vector from a different feature set: never served to this model
        bag = (str(pair.get("source", "feedback")), int(pair["feedback_id"]))
        by_bag[bag].append((pair, vec))
    out: list[SiteRow] = []
    for bag in sorted(by_bag):
        members = by_bag[bag]
        w = 1.0 / len(members)
        for pair, vec in members:
            factor = confidence_rules.multiplier(pair.get("confidence"))
            out.append(
                SiteRow(
                    vec,
                    1 if pair["verdict"] == "confirm" else 0,
                    w * factor,
                    bag,
                    int(pair.get("incident", pair["feedback_id"])),
                    float(pair["label_at"]),
                )
            )
    return out


def split_by_time(rows: list[SiteRow]) -> tuple[list[SiteRow], list[SiteRow]]:
    """Older bags train, the newest :data:`TEST_FRACTION` of bags test. Whole bags, never pairs."""
    first_label = {}
    for r in rows:
        first_label[r.bag] = min(first_label.get(r.bag, math.inf), r.label_at)
    ordered = sorted(first_label, key=lambda b: (first_label[b], b))
    cut = len(ordered) - max(1, int(round(len(ordered) * TEST_FRACTION))) if ordered else 0
    test_bags = set(ordered[cut:])
    return [r for r in rows if r.bag not in test_bags], [r for r in rows if r.bag in test_bags]


def dataset_of(rows: list[SiteRow], features: tuple[str, ...]) -> gam_fit.Dataset:
    picks = [FEATURE_NAMES.index(f) for f in features]
    return gam_fit.Dataset.from_rows(
        features, [r.x for r in rows], [r.y for r in rows], [r.w for r in rows], picks
    )


def adapt(
    shipped: gam.GamScorer, train: list[SiteRow], valid: list[SiteRow], params: gam_fit.FitParams
) -> gam_fit.FitResult:
    """Boost from the shipped model's logit, on its bins. Grouping parameters are inherited."""
    model = shipped.model
    features = model.features
    edges = {s.feature: s.edges for s in model.shapes}
    return gam_fit.fit(
        dataset_of(train, features),
        dataset_of(valid, features) if valid else None,
        params,
        edges=edges,
        base=model,
        threshold=model.threshold,
        grouping=dict(model.grouping),
    )


@dataclass
class Judgement:
    verdict: str  # BETTER | NOT_BETTER | INSUFFICIENT_EVIDENCE
    unmet: list[str] = field(default_factory=list)
    stats: dict[str, float] = field(default_factory=dict)
    difference: dict[str, float] = field(default_factory=dict)  # site - shipped, per incident
    benchmark: dict[str, float] = field(default_factory=dict)
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ll(scorer: gam.GamScorer, rows: list[SiteRow]) -> float:
    total = mass = 0.0
    for r in rows:
        z = max(-700.0, min(700.0, scorer.logit(r.x)))
        p = min(max(1.0 / (1.0 + math.exp(-z)), 1e-12), 1.0 - 1e-12)
        total -= r.w * (math.log(p) if r.y else math.log(1.0 - p))
        mass += r.w
    return total / mass if mass else 0.0


def floors(rows: list[SiteRow], test: list[SiteRow]) -> tuple[list[str], dict[str, float]]:
    incidents = {r.incident for r in rows}
    test_incidents = {r.incident for r in test}
    negative_bags = {r.bag for r in rows if r.y == 0}
    days = {int(r.label_at // 86400) for r in rows}
    stats = {
        "incidents": float(len(incidents)),
        "test_incidents": float(len(test_incidents)),
        "negative_bags": float(len(negative_bags)),
        "label_days": float(len(days)),
        "rows": float(len(rows)),
    }
    unmet = [f"{k}: {int(stats[k])} < {v}" for k, v in FLOORS.items() if stats[k] < v]
    if stats["label_days"] < MIN_LABEL_DAYS:
        unmet.append(f"label_days: {int(stats['label_days'])} < {MIN_LABEL_DAYS}")
    return unmet, stats


def judge(
    shipped: gam.GamScorer,
    site: gam.GamScorer,
    rows: list[SiteRow],
    test: list[SiteRow],
    benchmark: list[SiteRow],
    seed: int = 0,
) -> Judgement:
    """The paired comparison. See the module docstring for each of the three outcomes."""
    unmet, stats = floors(rows, test)
    by_incident: dict[int, list[SiteRow]] = defaultdict(list)
    for r in test:
        by_incident[r.incident].append(r)
    diffs = [_ll(site, group) - _ll(shipped, group) for _, group in sorted(by_incident.items())]
    bench = {}
    harm = False
    if benchmark:
        b_ship, b_site = _ll(shipped, benchmark), _ll(site, benchmark)
        harm = b_site > b_ship + HARM_MARGIN
        bench = {"shipped": b_ship, "site": b_site, "margin": HARM_MARGIN, "rows": float(len(benchmark))}
    difference: dict[str, float] = {"incidents": float(len(diffs))}
    if diffs:
        rng = random.Random(seed)
        means = sorted(
            sum(diffs[rng.randrange(len(diffs))] for _ in diffs) / len(diffs) for _ in range(REPLICATES)
        )
        difference.update(
            mean=sum(diffs) / len(diffs),
            low=means[int(0.025 * (REPLICATES - 1))],
            high=means[int(0.975 * (REPLICATES - 1))],
            site_better_share=sum(1 for d in diffs if d < 0) / len(diffs),
        )
    if unmet:
        return Judgement("INSUFFICIENT_EVIDENCE", unmet, stats, difference, bench,
                         "a floor is unmet: " + "; ".join(unmet))
    if harm:
        return Judgement("NOT_BETTER", [], stats, difference, bench,
                         "the site model is worse on the shipped benchmark than the margin allows")
    if difference["high"] < 0.0:
        return Judgement("BETTER", [], stats, difference, bench,
                         "the site model's log loss is lower on this site's newest incidents, "
                         "and the whole interval says so")
    if difference["low"] >= 0.0:
        return Judgement("NOT_BETTER", [], stats, difference, bench,
                         "the site model is not better on this site's newest incidents")
    return Judgement("INSUFFICIENT_EVIDENCE", [], stats, difference, bench,
                     "the interval of the difference includes zero: more labels would tell")


def benchmark_rows(raw: list[list[float]]) -> list[SiteRow]:
    """The shipped benchmark's rows: ``[y, w, *vector]`` each, generated and labelled."""
    return [SiteRow(tuple(r[2:]), int(r[0]), float(r[1]), ("bench", i), i, 0.0)
            for i, r in enumerate(raw)]
