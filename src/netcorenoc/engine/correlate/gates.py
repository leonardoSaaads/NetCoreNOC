"""When a learned term stops being evidence about the pair in front of you.

Two gates, and they are the same argument one term apart. The scorer's two learned quantities
each answer a question narrower than the one the score needs:

* `E` answers *"do these two network elements go together?"*
* `A` answers *"do these two alarm types go together?"*

Neither answers *"are these two alarms part of one incident?"*, and in two specific pair shapes
the gap between those questions is wide enough to merge unrelated incidents. Each gate names its
shape, and each carries the measurement that found it rather than an argument that it should
exist. They live together because they are one idea and because a reader who has understood one
has most of the other; they live outside `scoring.py` because that module had reached its
400-line budget and because the gates are a *policy about the features*, while the scorer is the
arithmetic over them.

Both are pure functions of one `LinkFeatures`. Neither reads a clock, a store or any state, so a
grouping stays reproducible from the row that recorded it.
"""

from __future__ import annotations

from netcorenoc.engine.correlate.scorer_contract import LinkFeatures

__all__ = ["cross_subtree_elements", "unrelated_elements"]


def unrelated_elements(features: LinkFeatures) -> bool:
    """Two alarms on **different network elements the appliance has learned nothing about**.

    ## The defect, found by running an estate rather than the corpus (v0.19.0)

    Seventy devices, each with one independent card failure, two alarms three seconds apart, four
    vendors. Every incident is separate by construction. The appliance put **all 140 alarms into
    one situation**: 650 links, 580 of them between different devices, and **492 of the 650 were
    carried by class affinity**.

    The arithmetic is not subtle once it is in front of you. Every device raised the same two trap
    classes, so `A[2.1.3, 2.1.4]` climbed with each one, and after a few dozen it sat near 0.63.
    Two alarms of those classes, on **any** two elements, arriving within a few seconds, then
    score `0.294` (temporal) `+ 0.220` (class) `= 0.514` against a `0.50` threshold. A situation
    is a connected component, so one chain of those swallowed the estate.

    **This is F76 one term over.** `E` meant *"these two elements go together"* and was cheap to
    establish; the v0.18.0 gate stopped it carrying cross-element links. `A` means *"these two
    alarm types go together"* and says **nothing whatever about which device** — so on two
    unrelated elements it is co-occurrence without relatedness, which is the same false positive
    wearing the other term's colours. The corpus could not show it: v0.18.0 measured suppressing
    the class term as well, found it changed nothing on all ten scenarios, and shipped the narrow
    gate on that evidence. The ten scenarios have two to four elements each. Seventy have a
    failure mode four cannot.

    ## The rule, and why it is the documented one

    `docs/correlation.md` already claims this behaviour: *"two alarms group only when they are on
    the same network element and within about 21 seconds"*. That is true at cold start and stops
    being true the moment `A` learns anything — the doc described the first hour and the code did
    something else for every hour after. This makes the claim hold at all times.

    So: **different elements, and `E` is exactly zero** — no learned relationship at all, the pair
    never having cleared `MIN_EDGE_N` — means the only evidence is *"similar alarms, close
    together"*, and that is not enough to merge two incidents. Temporal alone caps at `w_t` (0.30
    by default), safely under any sane threshold, so such pairs do not link.

    **It is deliberately not a threshold on `E`.** `E > 0` means the two elements have co-occurred
    enough times to clear the trust gate, and from that point class affinity applies in full and
    genuine cross-element correlation works exactly as designed. The gate closes only where the
    appliance has learned *nothing at all* connecting the two elements — where it would otherwise
    be inferring a relationship between devices purely from the shape of the alarms.
    """
    return features.ne_i != features.ne_j and features.entity_affinity == 0.0


def cross_subtree_elements(features: LinkFeatures) -> bool:
    """Are these two alarms on **different network elements** and from **different enterprise
    subtrees**? If so the learned cross-element affinity is not evidence about this pair.

    ## The defect (F76, open since v0.15.0)

    `eval/corpus/dual_incident.json` says of itself *"Two unrelated incidents overlap in time on
    disjoint NEs; **must stay separate**."* They did not: all sixteen alarms landed in one
    situation, the scenario scored `ari 0.000` and `over_merge_rate 1.000`, and a test pinned the
    wrong answer on purpose because fixing it needed the correlator.

    **The obvious fix does not work, and measuring is how that was found.** The seven
    cross-incident links scored 0.5857 to 0.7243; the twenty-five within-incident links scored
    0.6161 to 0.7684. They *overlap*: the strongest bridge beat most legitimate links, and one
    cross-incident pair scored 0.7134 against a within-incident pair at 0.7131. No threshold, and
    no "a merge needs a stronger link than a join" rule, separates those two numbers. Nor is it
    one weak bridge that a connected-component rule could refuse: there were **seven**.

    ## Why the subtree is the signal

    What does separate them is already in the trap: incident A is `1.3.6.1.4.1.1271.*` (Ciena) and
    incident B is `1.3.6.1.4.1.2636.*` (Juniper), disjoint at the enterprise arc.
    `PREREGISTRATION-0.9.0.md` §2.3 registered exactly this as the fourth feature and v0.9.0 could
    not serve it, for one recorded reason — it needed an edit to `correlate.py`, whose bytes were
    pinned. **No MIB is consulted**: this is arithmetic on the identifier, which is the same
    opaque token the appliance already keys on, minus the habit of discarding its structure.

    It gates **only the entity term, and only across elements**. `E` means *"these two network
    elements go together"*, and F58/F61 measured that claim to be cheap: `MIN_EDGE_N` is cleared
    by **six** ordinary alarms, after which `E` is 0.833. Two vendors' unrelated alarms inside one
    window is co-occurrence without relatedness, and that is precisely the false positive. Same-
    element pairs are untouched — there `E` is structural, not learned. Class affinity is
    untouched too, which is what leaves a genuinely recurring cross-vendor pair a way to link.

    ## Measured, on the whole corpus, after the change

    `dual_incident` goes `pairwise_f1` 0.6364 → **1.0000**, `ari` 0.0000 → **1.0000**,
    `over_merge_rate` 1.0000 → **0.0000**. The other nine scenarios do not move by any metric, and
    `under_merge_rate` stays 0.0000 on all ten. Suppressing the class term as well was measured
    too and changes nothing further, so the narrower gate is the one that ships.

    ## The limitation, stated

    **No scenario in the corpus contains a ground-truth incident that spans two enterprise
    subtrees on different elements**, so the corpus cannot show this gate's cost. The nearest
    measurement: thirty recurrences of the same genuine cross-vendor pair produce
    `entity_affinity = 0.0000` and a total of 0.4652 against a 0.5 threshold — it does **not**
    link today either, because affinity mass is driven by burst density rather than by
    recurrence. So the gate removes no capability that currently works. That, and not an
    argument, is why it ships; the missing scenario is recorded as a finding.
    """
    if features.same_oid_root is not False:
        return False  # True (same subtree) or None (unknown) — the gate stays out of the way
    return features.ne_i != features.ne_j
