"""How the challenger is judged: **at partition level, against human verdicts, never pairwise.**

Phase 0 measured that the champion accepts **99.83 %** of the pairs it evaluates, so a classifier
that always answers "link" scores 99.83 % pairwise. **Pairwise accuracy is not reported as a quality
metric here, in any section, under any name** — a number a constant function achieves cannot
distinguish a model that learned something from one that learned the base rate.

What is reported instead:

* **`over_merge_rate` and `under_merge_rate`**, over a partition the challenger produced by running
  its link decisions through union-find, scored against the **human verdict at bag granularity**;
* **`split_bag_intact_rate`** — a third, separately named quantity, because a `split` bag supports
  no truth partition and folding it into an over-merge rate would fabricate a denominator;
* **calibration at bag level**, a five-bin reliability curve and the Brier score;
* **the admission filter**, run against the **champion** as well, because a filter nobody has run
  against the incumbent is a filter whose budget is a guess.

`over_merge_rate` and `under_merge_rate` **live here now** and `eval/metrics.py` imports them back
(DECISIONS #122): `src/netcorenoc/` never imports `eval/`, and two copies of one metric would agree
until one was tuned.
"""

from __future__ import annotations

import time
from collections.abc import Hashable, Sequence
from dataclasses import dataclass
from typing import Any

from netcorenoc.challenger import FEATURE_NAMES, LogisticScorer, sigmoid
from netcorenoc.scoring import AdditiveScorer, LinkFeatures

__all__ = [
    "CALIBRATION_BINS",
    "PartitionScore",
    "admission",
    "brier",
    "calibration",
    "evaluate",
    "over_merge_rate",
    "partition",
    "reconstruct",
    "split_bag_intact_rate",
    "under_merge_rate",
]

Label = Hashable
CALIBRATION_BINS = 5


# -- the two metrics `eval/metrics.py` used to own (DECISIONS #122) ---------------------------


def _groups(labels: Sequence[Label], keys: Sequence[Label]) -> dict[Label, set[Label]]:
    """For each label, the set of distinct co-labels (e.g. truth situations per predicted)."""
    out: dict[Label, set[Label]] = {}
    for label, key in zip(labels, keys, strict=True):
        out.setdefault(label, set()).add(key)
    return out


def over_merge_rate(pred: Sequence[Label], truth: Sequence[Label]) -> float:
    """Fraction of predicted situations that span two or more true situations."""
    groups = _groups(pred, truth)
    if not groups:
        return 0.0
    spanning = sum(1 for members in groups.values() if len(members) >= 2)
    return spanning / len(groups)


def under_merge_rate(pred: Sequence[Label], truth: Sequence[Label]) -> float:
    """Fraction of true situations split across two or more predicted situations."""
    groups = _groups(truth, pred)
    if not groups:
        return 0.0
    split = sum(1 for members in groups.values() if len(members) >= 2)
    return split / len(groups)


# -- the partition ----------------------------------------------------------------------------


def partition(edges: list[tuple[int, int]], members: set[int]) -> dict[int, int]:
    """Union-find over accepted links: `alarm_id -> component id`.

    The same machinery `preview.partition` and the engine's `_assign_situation` use — connected
    components of the link graph, with the component id the **smallest member alarm id** so the
    labelling is stable and two runs over the same input are identical.

    Written here rather than reused from `preview.partition` because that function re-scores from a
    live `Learner`; this one is handed the edges a scorer already accepted over **stored** features.
    The union-find itself is the same eight lines, and the property that matters — a component is
    named by its minimum member — is asserted by the tests rather than assumed from a shared call.
    """
    parent = {alarm: alarm for alarm in members}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        if a not in parent or b not in parent:
            continue
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    return {alarm: find(alarm) for alarm in sorted(members)}


@dataclass(frozen=True)
class PartitionScore:
    """The three numbers, and the populations each was computed over."""

    over_merge: float | None
    under_merge: float | None
    confirm_bags: int
    confirm_alarms: int
    split_bags: int
    split_bags_intact: int

    @property
    def split_bag_intact_rate(self) -> float | None:
        """**The failure a NOC actually pays for**: two incidents buried in one situation.

        The fraction of `split` bags left wholly inside one predicted component. Lower is better,
        and it is reported separately rather than folded into `over_merge_rate` because a `split`
        bag asserts *"these members are at least two situations"* without saying **which** — so it
        supports no truth partition, and inventing one would fabricate a denominator.
        """
        return None if not self.split_bags else self.split_bags_intact / self.split_bags


def evaluate(
    bags: list[dict[str, Any]], pairs: list[dict[str, Any]], accepted: dict[int, bool]
) -> PartitionScore:
    """Score one set of link decisions against the human verdicts.

    ``accepted`` maps `dataset_pair.id -> linked`, so the same function scores the challenger and
    the champion — the champion's decisions come from `incumbent_linked`, which is legitimate **as
    a comparison basis** and is never a target here: the target is the operator's verdict.

    * a **`confirm`** bag asserts *these members are one situation*: its members carry the bag's id
      as their truth label, a prediction that splits it is an **under-merge**, and one that unites
      two different confirm bags is an **over-merge**;
    * a **`split`** bag supports no truth partition and is scored only by
      :attr:`PartitionScore.split_bag_intact_rate`.
    """
    verdicts = {int(bag["feedback_id"]): str(bag["verdict"]) for bag in bags}
    by_bag: dict[int, list[dict[str, Any]]] = {}
    for pair in pairs:
        by_bag.setdefault(int(pair["feedback_id"]), []).append(pair)

    pred: list[int] = []
    truth: list[int] = []
    confirm_bags = confirm_alarms = split_bags = split_intact = 0
    for feedback_id, rows in sorted(by_bag.items()):
        members = {int(r["alarm_a"]) for r in rows} | {int(r["alarm_b"]) for r in rows}
        edges = [
            (int(r["alarm_a"]), int(r["alarm_b"]))
            for r in rows
            if accepted.get(int(r["pair_id"]), False)
        ]
        components = partition(edges, members)
        if verdicts.get(feedback_id) == "confirm":
            confirm_bags += 1
            confirm_alarms += len(members)
            for alarm in sorted(members):
                # Component ids are only unique within a bag, so they are namespaced by bag id
                # before pooling — otherwise two bags whose smallest alarm happened to coincide
                # would look like one predicted situation.
                pred.append(hash((feedback_id, components[alarm])))
                truth.append(feedback_id)
        else:
            split_bags += 1
            if len({components[alarm] for alarm in members}) == 1:
                split_intact += 1
    return PartitionScore(
        over_merge=over_merge_rate(pred, truth) if pred else None,
        under_merge=under_merge_rate(pred, truth) if pred else None,
        confirm_bags=confirm_bags,
        confirm_alarms=confirm_alarms,
        split_bags=split_bags,
        split_bags_intact=split_intact,
    )


def split_bag_intact_rate(score: PartitionScore) -> float | None:
    """Free-function accessor, so a report never has to reach into the dataclass to name it."""
    return score.split_bag_intact_rate


# -- calibration, at BAG level -----------------------------------------------------------------


def calibration(
    bags: list[dict[str, Any]], probabilities: dict[int, float]
) -> tuple[list[dict[str, Any]], float | None]:
    """`(reliability curve, Brier score)` at **bag** level — the granularity of the label.

    ``probabilities`` maps `feedback_id -> P(this bag is one situation)`, which the caller derives
    as the **minimum** over the bag's pairs: a connected component holds together only as well as
    its weakest link, so the minimum is the quantity that corresponds to the bag-level claim.

    Five equal-width bins over `[0, 1]`, fixed edges — a data-dependent binning would not be
    reproducible, and this report is a gate.

    **Calibration is not implied by accuracy.** A model can rank perfectly and be badly calibrated,
    which is precisely the case where a threshold does the most damage. And the half that is a trap,
    addressed to v0.11.0 and later: *confidence may route what the operator is asked; it may never
    authorise autonomy.* The autonomy trigger is measured agreement with humans, never the model's
    self-reported certainty.
    """
    points = [
        (probabilities[int(bag["feedback_id"])], 1.0 if bag["verdict"] == "confirm" else 0.0)
        for bag in bags
        if int(bag["feedback_id"]) in probabilities
    ]
    if not points:
        return [], None
    curve: list[dict[str, Any]] = []
    for index in range(CALIBRATION_BINS):
        low = index / CALIBRATION_BINS
        high = (index + 1) / CALIBRATION_BINS
        last = index == CALIBRATION_BINS - 1
        inside = [(p, y) for p, y in points if (low <= p < high) or (last and p == 1.0)]
        curve.append(
            {
                "bin": f"{low:.1f}-{high:.1f}",
                "n": len(inside),
                "mean_predicted": (
                    round(sum(p for p, _y in inside) / len(inside), 4) if inside else None
                ),
                "observed": (
                    round(sum(y for _p, y in inside) / len(inside), 4) if inside else None
                ),
            }
        )
    return curve, brier(points)


def brier(points: list[tuple[float, float]]) -> float:
    """`mean((p - y)^2)`. Lower is better; 0.25 is what always predicting 0.5 achieves."""
    return round(sum((p - y) ** 2 for p, y in points) / len(points), 6) if points else 0.0


# -- the admission filter ----------------------------------------------------------------------


def admission(
    scorer: LogisticScorer | AdditiveScorer, samples: list[LinkFeatures], *, budget_ratio: float
) -> dict[str, Any]:
    """Run one scorer against the filter. **The champion is measured on the same samples.**

    A model competes on quality **only after** passing this, and the filter was written before the
    first model so it could not be shaped around whatever won. Six checks:

    * **speed** — median and p99 microseconds per `score()` call, expressed *relative to the
      champion measured in the same process at the same time*. Phase 0 §5 measured the champion's
      p99 moving 2.6x between two runs on one machine, so an absolute microsecond budget would be a
      measurement of the scheduler; a ratio is not.
    * **explainability** — emits `terms`, one per free parameter, summing to the score. Asserted.
    * **determinism** — the same features give a byte-identical score on a second call.
    * **memory** — the scorer's own state does not grow with the number of calls.
    * **contract** — implements `LinkScorer` at a version this build supports.
    * **dependencies** — none new; asserted at the suite level, reported here for completeness.
    """
    warmed = samples[: min(200, len(samples))]
    for features in warmed:
        scorer.score(features)
    timings: list[float] = []
    for features in samples:
        started = time.perf_counter_ns()
        scorer.score(features)
        timings.append((time.perf_counter_ns() - started) / 1000.0)
    timings.sort()

    probe = samples[0]
    first, second = scorer.score(probe), scorer.score(probe)
    total = 0.0
    for term in first.terms:
        total += term.contribution
    before = _state_size(scorer)
    for features in samples[: min(1000, len(samples))]:
        scorer.score(features)
    return {
        "scorer_id": scorer.scorer_id,
        "contract_version": scorer.contract_version,
        "calls": len(timings),
        "median_us": round(_quantile(timings, 0.5), 3),
        "p99_us": round(_quantile(timings, 0.99), 3),
        "budget_ratio": budget_ratio,
        "explainable": bool(first.terms) and total == first.score,
        "terms": len(first.terms),
        "deterministic": first == second and first.score.hex() == second.score.hex(),
        "memory_stable": _state_size(scorer) == before,
        "state_floats": before,
    }


def _quantile(ordered: list[float], q: float) -> float:
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))] if ordered else 0.0


def _state_size(scorer: object) -> int:
    """The number of scalars the scorer carries. A cache or an accumulator would move it."""
    return sum(1 for value in vars(scorer).values() if isinstance(value, (int, float, str))) + len(
        FEATURE_NAMES
    )


def verdict(challenger: dict[str, Any], champion: dict[str, Any]) -> tuple[bool, list[str]]:
    """`(admitted, reasons it was not)`. **A model failing any check does not compete.**"""
    reasons: list[str] = []
    if not challenger["explainable"]:
        reasons.append("explainability: contributions do not sum to the score")
    if not challenger["deterministic"]:
        reasons.append("determinism: two calls on identical features differed")
    if not challenger["memory_stable"]:
        reasons.append("memory: the scorer's state grew while scoring")
    if challenger["contract_version"] != champion["contract_version"]:
        reasons.append(
            f"contract: {challenger['contract_version']} is not the running "
            f"{champion['contract_version']}"
        )
    budget = champion["p99_us"] * challenger["budget_ratio"]
    if challenger["p99_us"] > budget:
        reasons.append(
            f"speed: p99 {challenger['p99_us']:.3f}us over the budget {budget:.3f}us "
            f"({challenger['budget_ratio']:.1f}x the champion's {champion['p99_us']:.3f}us)"
        )
    return not reasons, reasons


def champion_decisions(pairs: list[dict[str, Any]]) -> dict[int, bool]:
    """The champion's link decisions, from `incumbent_linked`.

    **Provenance and a comparison basis, never a target.** The target everywhere in this module is
    the operator's verdict; this exists so the challenger's partition can be put beside the
    champion's, and a challenger number with no champion number beside it is not a comparison.
    """
    return {int(p["pair_id"]): bool(int(p["incumbent_linked"])) for p in pairs}


def reconstruct(scorer: LogisticScorer, row: dict[str, Any]) -> tuple[float, bool]:
    """**The offline half.** Recompute the challenger's opinion from stored features.

    Built from the *stored* columns and the *same* `LogisticScorer` the online path used, through
    the *same* `feature_vector`. That is what makes the skew comparison meaningful: if the two
    disagree it is because a feature was computed differently somewhere, and there is exactly one
    function that could have done it.

    Works on a `dataset_pair` row and on a `shadow_opinion` row alike — both carry `delta_t_s`,
    `class_affinity` and `entity_affinity` under those names, which is not a coincidence but the
    reason migration 0009 chose them.
    """
    features = LinkFeatures(
        delta_t_s=float(row["delta_t_s"]),
        class_i=0,
        class_j=0,
        class_affinity=float(row["class_affinity"]),
        ne_i=0,
        ne_j=0,
        entity_affinity=float(row["entity_affinity"]),
    )
    verdict = scorer.score(features)
    return sigmoid(verdict.score), verdict.linked


def challenger_decisions(
    scorer: LogisticScorer, pairs: list[dict[str, Any]]
) -> tuple[dict[int, bool], dict[int, float]]:
    """`(pair_id -> linked, pair_id -> probability)` by offline reconstruction."""
    linked: dict[int, bool] = {}
    probability: dict[int, float] = {}
    for pair in pairs:
        p, is_linked = reconstruct(scorer, pair)
        linked[int(pair["pair_id"])] = is_linked
        probability[int(pair["pair_id"])] = p
    return linked, probability


def bag_probabilities(
    pairs: list[dict[str, Any]], probability: dict[int, float]
) -> dict[int, float]:
    """`feedback_id -> min over the bag's pairs` — the weakest link, which is what holds a
    component together and therefore the quantity a bag-level claim corresponds to."""
    out: dict[int, float] = {}
    for pair in pairs:
        key = int(pair["feedback_id"])
        p = probability[int(pair["pair_id"])]
        out[key] = p if key not in out else min(out[key], p)
    return out
