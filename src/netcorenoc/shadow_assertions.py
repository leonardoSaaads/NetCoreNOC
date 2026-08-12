"""The fourth named quantity: **did the challenger respect what an operator asserted?**

v0.10.0, Workstream 4. `PREREGISTRATION-0.10.0.md` §2.6(d) registers it.

Split from `shadow_eval.py`, which reached 410 of its 400-line budget when the metric's third
correction landed — **split, not exempted** (Part VII rule 6), and on a seam that is real rather
than a size accident: `shadow_eval.py` scores a **partition** against a human verdict, and this
scores a **human assertion** against a partition. The subject of the sentence is different, and so
is the population it is aggregated over.

## Why every asserted negative pair matters more than its count suggests

A bag *is* a situation; every member of a situation is in one component under the incumbent; so a
`marked x rest` pair is necessarily a pair **the incumbent joined and an operator says it should not
have**. v0.9.0 measured that the discriminating population was missing — the champion accepts
99.83 % of what it evaluates and its rejections live in quiet traffic that attracts no labels. **The
exclusion set manufactures that population directly**, out of the one place it can be found: the
operator's disagreement.

## The three ways this metric is easy to get wrong

All three are §2.6's, and all three are enforced here rather than left to a caller:

1. **Per bag, never pooled over pairs.** One 501-member storm with 250 marks contributes 62 750
   pairs, and a pooled rate would be that storm's rate wearing the corpus's name.
2. **`coverage IN ('none','empty')` excluded, counted and reported** — such a bag yields an
   all-singleton partition in which every assertion is satisfied for free.
3. **The champion is scored by this same function**, because a challenger number with no champion
   number beside it is not a comparison.

**Never composed** with `over_merge_rate`, `under_merge_rate` or `split_bag_intact_rate`.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["AssertingBag", "asserted_negative_respected_rate"]


@dataclass(frozen=True)
class AssertingBag:
    """One bag that asserts something negative, with everything needed to score it.

    `marked` is the **reconciled** set — the client's report intersected with the server's own bag,
    which is what v0.9.2 built and the only quantity a metric about the evidence may read (F46).
    `hidden` is the set of members the labeller could not observe, so the observable pairs can be
    counted rather than assumed (§2.4).
    """

    feedback_id: int
    incident: int
    members: tuple[int, ...]
    marked: frozenset[int]
    hidden: frozenset[int]
    coverage: str

    @property
    def eligible(self) -> bool:
        """§2.6(c): a bag with `coverage IN ('none','empty')` is EXCLUDED, counted, and reported.

        Such a bag yields an all-singleton partition, in which **every asserted negative pair is
        satisfied for free** — v0.9.0's measured policy-B failure (a perfect number produced by
        nothing happening) appearing in a new place.
        """
        return self.coverage not in ("none", "empty") and bool(self.marked)

    def observable_pairs(self) -> list[tuple[int, int]]:
        """The `marked x rest` pairs **both of whose ends the labeller could see** (§2.4).

        `EVIDENCE-BOUNDARY-0.9.2.md` §10, corrected in v0.10.0 Gate 0 and machine-checked by
        `tests/test_evidence_boundary_observable.py`: the count is exactly
        `(m - b) * ((n - m) - (h - b))`, and this enumerates precisely those pairs. A pair is
        unobservable if **either** end is hidden.
        """
        marked_visible = [a for a in self.members if a in self.marked and a not in self.hidden]
        rest_visible = [a for a in self.members if a not in self.marked and a not in self.hidden]
        return [(a, b) for a in marked_visible for b in rest_visible]


def asserted_negative_respected_rate(
    bags: list[AssertingBag], components: dict[int, dict[int, int]]
) -> tuple[dict[int, list[float]], dict[str, int]]:
    """**The fourth named quantity.** Per bag, aggregated by the caller as the mean over bags.

    Returns `(incident -> [per-bag rates], diagnostics)`. The caller hands the first to
    `shadow_cv.cluster_bootstrap`, which resamples **incidents**.

    Three properties a build gets wrong if it is not careful, all three registered in §2.6(d):

    * **Per bag, never pooled over pairs.** One 501-member storm with 250 marks contributes 62 750
      pairs; a pooled rate would be *that storm's rate wearing the corpus's name*. This function
      returns one number per bag and has no expression that could pool.
    * **`coverage IN ('none','empty')` excluded, counted and reported.** The diagnostics carry the
      exclusion count so the report can print it rather than the reader inferring it from a gap.
    * **The champion is scored by this same function.** ``components`` is whatever partition a
      caller produced, so the challenger's and the champion's numbers come from one code path —
      a challenger number with no champion number beside it is not a comparison.

    **Never composed** with `over_merge_rate`, `under_merge_rate` or `split_bag_intact_rate`. It is
    a fourth quantity for the same reason the third exists.
    """
    per_incident: dict[int, list[float]] = {}
    excluded = observable = respected = 0
    scored = 0
    for bag in bags:
        if not bag.eligible:
            excluded += 1
            continue
        pairs = bag.observable_pairs()
        if not pairs:
            # A bag whose every asserted pair was blind asserts nothing this metric can read. Not
            # an exclusion under §2.6(c) and not a 1.0 either: counted separately, because scoring
            # it as "respected" would be the free-perfect-number failure one level down.
            excluded += 1
            continue
        component = components.get(bag.feedback_id)
        if not component:
            # **A bag the model produced no partition for scores NOTHING, not 1.0.**
            # With an empty mapping every `component.get(a, a) != component.get(b, b)` is True, so
            # an absent partition would read as "every asserted pair respected" — a perfect number
            # produced by nothing happening, which is v0.9.0's measured policy-B failure and
            # §2.6(c)'s trivially-satisfied case arriving through a different door. Found by mutant
            # M3 in `docs/gates/v0.10.0-guard-demonstrations.md`, which the first version of the
            # champion-parity guard failed to kill.
            excluded += 1
            continue
        kept = sum(1 for a, b in pairs if component.get(a, a) != component.get(b, b))
        observable += len(pairs)
        respected += kept
        scored += 1
        per_incident.setdefault(bag.incident, []).append(kept / len(pairs))
    return per_incident, {
        "bags_scored": scored,
        "bags_excluded": excluded,
        "observable_pairs": observable,
        "pairs_respected": respected,
        "incidents": len(per_incident),
    }
