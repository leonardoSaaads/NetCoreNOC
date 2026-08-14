"""The promotion gate: the server re-derives the verdict, and refuses in two distinguishable ways.

v0.11.0. `PREREGISTRATION-0.11.0.md` §2 fixes what a promotion's evidence must contain, §3 fixes
the seal policy and its **evaluation order**, and §4 fixes the two refusals. None is varied here.

## The two refusals are opposite claims

> `INSUFFICIENT_EVIDENCE` — **the corpus cannot decide.**
> `NOT_BETTER` — **the corpus decided, against the challenger.**

A single `if not decisive or verdict is not BETTER: refuse()` would satisfy a naive test and destroy
the distinction. So there are **two functions, each of which raises if handed the other's verdict**
— structural rather than conventional, and `tests/test_promotion_gate.py` proves the two cannot be
reached from one branch.

## The evaluation order is registered, and this module obeys it literally

> **floors first, power condition second, seal last.** The seal is spent when and only when the
> first two pass.

:func:`evaluate` reads the seal **after** it has both answers. On this project's corpus the floors
fail, so the seal is never read and the query count stays 0 — a prediction the plan made in advance
(`docs/gates/v0.11.0-phase-1.md` §1.2), not an outcome reported afterwards.

## The server re-derives every quantity that decides

The request body may name a `model_version_id`. **It may not assert a verdict, a metric, or a floor
evaluation** — v0.9.2's evidence boundary applied to promotion. :func:`evaluate` takes no verdict
parameter, so there is no argument a client could fill.

**Nothing here promotes automatically**, on any schedule, under any configuration. There is no
`auto_promote` flag, *not even defaulted off*: a flag defaulted off is a flag, and the release after
this one would find it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from netcorenoc import seal
from netcorenoc.evaluation_folds import materialise_folds
from netcorenoc.judge import Judgement, Verdict, judge
from netcorenoc.shadow_cv import Interval, Power

if TYPE_CHECKING:  # pragma: no cover - type-only, no runtime edge (tests/test_layers.py)
    from netcorenoc.store import Store

__all__ = [
    "HEADLINE_QUANTITIES",
    "QUANTITY_NAMES",
    "RATIFIED_PLAN_SHA256",
    "Decision",
    "Metrics",
    "PromotionPathError",
    "Quantity",
    "deciding_quantity",
    "evaluate",
    "insufficient_evidence_refusal",
    "not_better_refusal",
    "refusal_for",
]

# THE RATIFIED PLAN IN FORCE for this release (§2 item 6). Recorded on every `promotion` row, and
# presented to `seal.spend`, which refuses a read unless the same hash is already on record. Pinned
# here as a constant rather than read from the file: a hash computed at runtime from a document on
# disk would move with the document, which is exactly what a pre-registration hash exists to
# prevent. `tests/test_preregistration.py` is what keeps this value and the file in agreement.
RATIFIED_PLAN_SHA256 = "e011ee6ad2367d44f2ede14cad7b072df598298f91ecc1a405744358b589d449"

# The four named quantities, **never composed** (the plan's §1). Order is fixed so a report and a
# stored row list them the same way.
QUANTITY_NAMES = (
    "over_merge_rate",
    "under_merge_rate",
    "split_bag_intact_rate",
    "asserted_negative_respected_rate",
)

# The two on which `BETTER` requires the interval to exclude zero (§6.3). The other two are reported
# and can produce `NOT_BETTER` through §6.6 and §6.7, never `BETTER` on their own.
HEADLINE_QUANTITIES = ("over_merge_rate", "under_merge_rate")

# Rates where a LOWER number is better. Named rather than inferred: "which direction is good" is
# exactly the fact a reader guesses wrong and a build encodes twice.
LOWER_IS_BETTER = frozenset({"over_merge_rate", "under_merge_rate", "split_bag_intact_rate"})


class PromotionPathError(RuntimeError):
    """A refusal message was requested for a verdict that path does not describe.

    **Not operator-facing.** It is reachable only by a caller that has collapsed the two refusals
    into one condition — the defect this release exists to prevent — and it fails loudly rather
    than producing a message that reads as the other kind of refusal.
    """


@dataclass(frozen=True)
class Quantity:
    """One named quantity, for **both arms**, computed by the same code path (§2 item 4)."""

    name: str
    challenger: Interval
    champion: Interval

    @property
    def favours_challenger(self) -> bool:
        if self.name in LOWER_IS_BETTER:
            return self.challenger.rate < self.champion.rate
        return self.challenger.rate > self.champion.rate

    @property
    def difference(self) -> float:
        return self.challenger.rate - self.champion.rate

    @property
    def excludes_zero(self) -> bool:
        """Whether the challenger's interval excludes the champion's point estimate — which is
        exactly *an interval on the difference that excludes zero*, expressed where both arms are
        measured."""
        return not (self.challenger.low <= self.champion.rate <= self.challenger.high)

    def render(self) -> str:
        return (
            f"{self.name}: challenger {self.challenger.rate:.4f} "
            f"[{self.challenger.low:.4f}, {self.challenger.high:.4f}] "
            f"vs champion {self.champion.rate:.4f} "
            f"[{self.champion.low:.4f}, {self.champion.high:.4f}]"
        )


@dataclass(frozen=True)
class Metrics:
    """The four named quantities. **Never composed** — there is no `score` property here."""

    quantities: tuple[Quantity, ...]

    def by_name(self, name: str) -> Quantity:
        return next(q for q in self.quantities if q.name == name)

    def as_document(self) -> str:
        """Canonical JSON for the `promotion.metrics` column, both arms, in the fixed order."""
        return json.dumps(
            {
                q.name: {
                    "challenger": [q.challenger.rate, q.challenger.low, q.challenger.high],
                    "champion": [q.champion.rate, q.champion.low, q.champion.high],
                    "clusters": q.challenger.clusters,
                }
                for q in self.quantities
            },
            sort_keys=True,
            separators=(",", ":"),
        )


def deciding_quantity(metrics: Metrics) -> Quantity:
    """**Which of the four produced a `NOT_BETTER`** — §4 requires the message name it.

    The check order is the plan's own order of adverse findings: §6.6 (the model buries splits) and
    §6.7 (it separates everything) first, because those are verdicts *regardless of the other
    numbers*; then the headline rates. One quantity rather than a list — a refusal that named four
    would be naming none.
    """
    intact = metrics.by_name("split_bag_intact_rate")
    if not intact.favours_challenger:
        return intact
    respected = metrics.by_name("asserted_negative_respected_rate")
    under = metrics.by_name("under_merge_rate")
    if respected.challenger.rate > 0.95 and not under.favours_challenger:
        return respected
    for name in HEADLINE_QUANTITIES:
        quantity = metrics.by_name(name)
        if not quantity.favours_challenger or not quantity.excludes_zero:
            return quantity
    return metrics.by_name(HEADLINE_QUANTITIES[0])


# -- the gate ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Decision:
    """The server's re-derived verdict and everything §2 requires a `promotion` row to carry."""

    judgement: Judgement
    metrics: Metrics
    evaluation_run_id: str | None
    plan_sha256: str
    unavailable: tuple[str, ...] = ()

    @property
    def verdict(self) -> Verdict:
        return self.judgement.verdict

    @property
    def may_apply(self) -> bool:
        """**The only place that says a promotion is permitted.** `is Verdict.BETTER`, never
        `not ...` — the three-valued type has no member whose name reads as a negative result, so
        permission cannot be obtained by negating a refusal."""
        return self.judgement.verdict is Verdict.BETTER


async def evaluate(
    store: Store,
    *,
    floors_met: bool,
    power: Power,
    metrics: Metrics,
    incident_ids: list[int],
    params_hash: str,
    plan_sha256: str,
    release: str,
    now: float,
    smallest_side: int = 0,
    coverage_excluded: int = 0,
    unknown_rows: int = 0,
    truncated_load_bearing: bool = False,
    unsound_chains: int = 0,
    pre_v080_merges: int = 0,
    asserting_incidents: int = 0,
) -> Decision:
    """Re-derive the verdict. **Floors first, power second, seal last** (the plan's §3).

    The seal is read when and only when the first two pass — preserving the asymmetry that
    justified it (*reserving later is impossible; spending later is always possible*) while
    ensuring it is never opened on a corpus that had already failed for another reason.
    """
    run_id = await materialise_folds(
        store, incident_ids=incident_ids, params_hash=params_hash, now=now
    )

    # THE ORDER, LITERALLY. `power.sufficient` is read from the object rather than re-derived: a
    # second implementation is how the number the report prints and the number the verdict uses come
    # to disagree with nothing going red (judge.py's own note).
    holdout_spent = False
    unavailable: list[str] = []
    if floors_met and power.sufficient:
        access = await seal.spend(
            store,
            release=release,
            plan_sha256=plan_sha256,
            purpose="promotion",
            now=now,
        )
        holdout_spent = access.granted
        if not access.granted:
            unavailable.append(f"sealed holdout: {access.outcome}")
    else:
        unavailable.append(
            "sealed holdout: not read — the floors and the power condition are evaluated first, "
            "and the seal is spent when and only when both pass (plan §3)"
        )

    headline = [metrics.by_name(name) for name in HEADLINE_QUANTITIES]
    judgement = judge(
        floors_met=floors_met,
        holdout_spent=holdout_spent,
        power=power,
        smallest_side=smallest_side,
        coverage_excluded=coverage_excluded,
        unknown_rows=unknown_rows,
        truncated_load_bearing=truncated_load_bearing,
        unsound_chains=unsound_chains,
        pre_v080_merges=pre_v080_merges,
        asserting_incidents=asserting_incidents,
        difference_excludes_zero=all(q.excludes_zero for q in headline),
        favours_challenger=all(q.favours_challenger for q in headline),
        split_bags_buried=not metrics.by_name("split_bag_intact_rate").favours_challenger,
        separates_everything=(
            metrics.by_name("asserted_negative_respected_rate").challenger.rate > 0.95
            and not metrics.by_name("under_merge_rate").favours_challenger
        ),
        query_count=await store.query_count(),
    )
    return Decision(
        judgement=judgement,
        metrics=metrics,
        evaluation_run_id=run_id,
        plan_sha256=plan_sha256,
        unavailable=tuple(unavailable),
    )


# -- the two refusals, by different code paths --------------------------------------------------


def insufficient_evidence_refusal(judgement: Judgement) -> str:
    """**The corpus cannot decide.** Every trigger, the threshold at `n`, and what must change.

    Raises on any other verdict — the guard that makes *"neither message is producible by the
    other's condition"* a property of the code rather than a promise in a document.
    """
    if judgement.verdict is not Verdict.INSUFFICIENT_EVIDENCE:
        raise PromotionPathError(
            f"the INSUFFICIENT_EVIDENCE refusal was asked to describe a {judgement.verdict.name} "
            "verdict. These are opposite claims and one message may never stand for the other."
        )
    triggers = "\n".join(f"  - {t.name}: {t.value}" for t in judgement.triggers)
    return (
        "PROMOTION REFUSED — INSUFFICIENT_EVIDENCE.\n"
        "This corpus cannot decide whether the challenger is better. It is not a finding that the "
        "challenger is worse.\n\n"
        f"Triggers that fired ({len(judgement.triggers)}):\n{triggers}\n\n"
        f"Detection threshold at n = {judgement.incidents} incidents: "
        f"{judgement.detectable_difference:.4f} "
        f"({judgement.detectable_difference * 100:.1f} percentage points).\n"
        f"Observed difference: {judgement.observed_difference:.4f}.\n"
        f"Sealed-holdout query count at decision time: {judgement.query_count}.\n\n"
        "What would have to change: every trigger above must clear. The floors are counts of "
        "labelled evidence and are met by labelling, not by tuning. "
        f"Projected time to sufficiency: {judgement.projection} — this deployment has no "
        "labelling-rate data, and printing a rate derived from the test harness would be "
        "manufacturing one."
    )


def not_better_refusal(judgement: Judgement, metrics: Metrics) -> str:
    """**The corpus decided, against the challenger.** The deciding quantity, its interval, and
    the champion's number beside it. Raises on any other verdict, as its sibling does."""
    if judgement.verdict is not Verdict.NOT_BETTER:
        raise PromotionPathError(
            f"the NOT_BETTER refusal was asked to describe a {judgement.verdict.name} verdict. "
            "These are opposite claims and one message may never stand for the other."
        )
    quantity = deciding_quantity(metrics)
    notes = "".join(f"\n  {note}" for note in judgement.notes)
    return (
        "PROMOTION REFUSED — NOT_BETTER.\n"
        "This corpus decided, and it decided against the challenger. The evidence was sufficient; "
        "the challenger did not win.\n\n"
        f"The quantity that produced the verdict: {quantity.render()}.\n"
        f"Difference (challenger - champion): {quantity.difference:+.4f}; the challenger's "
        f"interval {'excludes' if quantity.excludes_zero else 'contains'} the champion's rate.\n"
        f"Detection threshold at n = {judgement.incidents}: "
        f"{judgement.detectable_difference:.4f}.\n"
        f"Sealed-holdout query count at decision time: {judgement.query_count}."
        f"{notes}\n\n"
        "No composite is computed. The four named quantities are reported separately and are "
        "never combined into a score."
    )


def refusal_for(decision: Decision) -> str:
    """Dispatch to the refusal the verdict describes. **`BETTER` is not a refusal and raises.**

    A `match` over the three-valued type: no branch here is shared by two verdicts, so a future
    fourth value falls through to the final case rather than silently taking a refusal written for
    something else.
    """
    match decision.verdict:
        case Verdict.INSUFFICIENT_EVIDENCE:
            return insufficient_evidence_refusal(decision.judgement)
        case Verdict.NOT_BETTER:
            return not_better_refusal(decision.judgement, decision.metrics)
        case _:
            raise PromotionPathError(
                f"{decision.verdict.name} is not a refusal; a promotion that may be applied has no "
                "refusal message, and asking for one means a caller has confused the two outcomes."
            )


def triggers_document(judgement: Judgement) -> str:
    """The `promotion.triggers` column: **enumerated, not summarised** (§2 item 3)."""
    return json.dumps([t.name for t in judgement.triggers], separators=(",", ":"))


def unavailable_document(decision: Decision) -> str:
    """§2's *"states which were unavailable and why"*, for a refusal missing some of the nine."""
    return json.dumps(list(decision.unavailable), separators=(",", ":"))
