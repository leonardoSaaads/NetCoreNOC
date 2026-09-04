# Pre-registered analysis plan — v0.16.1 (amendment: what a bag is once its membership moves)

<!-- release-claim: v0.16.1 = visualisation-search -->

**STATUS: RATIFIED 2026-09-04.** From this instant it is immutable: its SHA-256 is pinned by
`tests/test_preregistration.py` beside the five already there, and its second home is
`docs/record.md`. An edit made after seeing a result turns the suite red.

**Ratify before any v0.16.1 repair is written.**

**This is an amendment, not a new plan.** [`PREREGISTRATION-0.16.0.md`](PREREGISTRATION-0.16.0.md)
is immutable and stays in force in full: `m(c) = 0.6 + 0.4·c`, the floor of 0.50, the gesture-to-
assertion map of its §2, the unchanged floors of its §3, and **bag provenance recorded and not
consumed** are all still registered and still binding. Its parents —
[`0.9.0`](PREREGISTRATION-0.9.0.md), [`0.10.0`](PREREGISTRATION-0.10.0.md),
[`0.11.0`](PREREGISTRATION-0.11.0.md) — bind unchanged.

It answers **four questions and nothing else**. Every one of them is the same question from a
different side: *what is a bag's identity once its membership changes underneath it?*

## 0. What is known when this is written

**Known — the corpus.** `python tools/corpus_census.py --gestures`, at `f33e565`:
`asserting_bags = 10`, `asserting_incidents = 10`, `asserted_negative_pairs = 2 222`, of which
**1 050 came from one bag**.

**Known — F90.** `promotion_metrics._asserting_bags` rebuilds an operator's marked set as
`members[:excluded_reconciled]` of the situation's **live** membership. Reproduced with a control:
a bag of eight marked `[7, 8]` reconstructs as `[1, 2]`; the same bag marked `[1, 2]` also
reconstructs as `[1, 2]` and agrees. Four of twelve asserted pairs overlap what is measured.

**Known — F89.** `feedback` is `UNIQUE (situation_id, verdict)`, so the second `move` out of one
situation records its event and no second label.

**Known — the evidence boundary already decided the analogous question once.**
`0011_evidence_boundary.sql` and F46/F48: a quantity about the **evidence** is derived by the
server, from what the server held at the instant of the act, and a stale client view may not
validate itself.

**NOT known.** No operator has ever corrected the same situation twice on this appliance. The
count of second gestures in any corpus is zero, so every quantity below is registered from the
arithmetic of the record and never from an outcome, because no outcome exists.

## 1. DECISION 1 — a label is judged against the membership captured at the gesture

> **Registered: `AssertingBag.members` is `feedback_member(source='server')`, ordered by
> `position` — the snapshot the label was captured against — and never `situation_alarm`, which is
> live. `AssertingBag.marked` is `feedback_exclusion ∩ feedback_member(source='server')`, distinct
> by alarm id, and never a positional prefix of anything.**

**The same reasoning as the evidence boundary's, and it is the same reasoning.** `0011` refused to
let the client's report validate itself because the reporter and the referent had drifted apart;
the reconciled count exists precisely so the quantity about the evidence is derived from what the
**server** held at the instant of the verdict. A judge that reads live membership commits the
mirror image of that error: it lets a bag that has since changed validate a label made about the
bag that existed. The direction of the drift differs; the defect is identical, and F46's sentence
governs both — *the report and the learner read one quantity, so they agree by construction.*

**Both halves are stored evidence that no later mutation touches.** `feedback_exclusion` holds the
ids verbatim and `feedback_member(source='server')` holds the ordered snapshot; `0008`'s first rule
is why they were recorded rather than recomputed. Nothing about a past assertion needs to be
inferred from present state, so nothing may be.

> **Registered, and it is a limitation rather than a decision:** *which* members a scoped labeller
> could not observe is **not** recorded — `LabelScope.hidden_members` is transient and only the
> counts `scope_redacted_members` and `excluded_reconciled_out_of_scope` survive. The reconstructed
> hidden set is therefore **arbitrary in identity and exact in arithmetic**: `b` of the marked
> members and `h - b` of the remainder, so `observable_pairs()` returns exactly the
> `(m - b) · ((n - m) - (h - b))` that `PREREGISTRATION-0.10.0.md` §2.4 registers. The residual —
> that the *selection* is arbitrary even where the *count* is right — is issued as a finding rather
> than repaired by inventing a column, and it is not repaired in this release.

## 2. DECISION 2 — two gestures on one situation are two labels when the bag differs

> **Registered: a bag's identity is `(situation_id, verdict, bag_key)`, where `bag_key` is a digest
> over the situation's member ids as a SET — sorted, deduplicated — at the instant of the label.**

Order is part of the **record** (it is what the operator saw, and `member_digest` keeps it). Order
is not part of the **identity**: the same alarms in a different order are the same grouping and an
operator who asserts about them twice has asserted once. The set is the identity; the order is the
observation.

**F36's measured bound is preserved exactly where F36 measured it.** v0.7.0's defect was that *N
identical posts* drove N learning effects. N identical posts have one `bag_key` and still insert
once. What inserts a second row is a post about a bag that has **changed** — which is a different
assertion, by the plan's own §2, and the release exists because it was being dropped.

> **The bound this trades away, stated rather than discovered:** the cap on one situation's
> influence on learned state moves from **two applications** (one per verdict) to **one per verdict
> per distinct membership**. It is still bounded and it is still monotone in operator acts — each
> increment requires a membership change *and* a further POST — but it is no longer a constant.
> That is the cost, it is accepted here rather than in a patch, and a release that finds it too
> loose repairs it by bounding memberships, never by dropping the identity.

> **What `asserting_bags` then counts.** `PREREGISTRATION-0.16.0.md` §2 registers that it counts a
> **gesture**, not a pair, and that registration is unchanged. Under this key it counts **one
> asserting bag per (situation, verdict, distinct membership)** — the closest quantity the record
> can support to *"a gesture that asserted something new"*. A gesture that asserts again about an
> unchanged bag increments it by **zero**, and that is not a loss: it asserted nothing new.
> **A merge still writes no label**, so it still increments it by zero, exactly as §2 registered.

> **Registered: migration `0015` is permitted, and only for this.** It adds `bag_key` — a column,
> written at insert, backfilled for existing rows from `feedback_member(source='server')` — and
> replaces the two-column unique index with the three-column one. Forward-only. **No other schema
> change is registered by this amendment**, and a build that uses `0015` to carry anything else has
> violated it.

## 3. DECISION 3 — the ten bags already acquired are recomputed, not excluded

> **Registered: every bag already in the corpus is re-read through §1 and none is excluded or
> re-labelled.**

Nothing about them was lost. The ids the operators marked are in `feedback_exclusion` and the bags
they marked them against are in `feedback_member(source='server')`; **only the reader was wrong.**
Excluding a population to hide a reader's error would destroy evidence, and it is the exact
inversion of `reconciliation_drift`'s rule that a disagreement is **reported and never corrected**
(DECISIONS #134) — had that check been a corrector, F46 would have been invisible.

Their `bag_key` is backfilled from the same stored snapshot. A row whose snapshot is empty — a
verdict posted to an already-merged situation — backfills to the empty-set digest, so the old bound
still holds for it, which is the correct answer for a bag with no members.

## 4. DECISION 4 — what moves, in which direction, predicted before it is run

Stated **before** the repair runs. A predicted direction written down afterwards is a prediction
that cannot fail.

> **4.1 — No figure `tools/corpus_census.py` prints moves.** `asserting_bags`,
> `asserting_incidents`, `asserted_negative_pairs` and `max_from_one_bag` are computed from
> `Store.asserting_bag_rows` and from `excluded_reconciled × (member_count − excluded_reconciled)`,
> and **neither expression is touched by §1**. The census's gesture arm gestures each situation at
> most once, so §2 has nothing to insert either. **Predicted: 10, 10, 2 222, 1 050 — unchanged.**
> A census that moved would mean the repair reached something it was not supposed to reach, and
> that is the finding rather than the result.

> **4.2 — The quantity that moves is not in the census.**
> `asserted_negative_respected_rate` is computed by `promotion_metrics.measure`, which the census
> does not print. **Predicted: the pairs it measures change identity for every gesture-acquired
> bag, and its observable-pair count per such bag rises by exactly one** — a `move` labels the
> source situation *before* removing the alarm, so the snapshot holds `k` members and the live bag
> holds `k − 1`. The rate itself has **no predicted direction**: it is a ratio over a different set
> of pairs, and predicting its sign would be predicting the challenger's partition.

> **4.3 — `asserting_bags_eligible` does not move.** Eligibility is `coverage ∉ {none, empty}` and
> `bool(marked)`. `excluded_reconciled ≥ 1` is `asserting_bag_rows`'s own predicate and the
> reconciled set §1 rebuilds has that same cardinality by `reconciliation_drift`'s definition, so
> a bag that was eligible stays eligible and a bag that was not stays not.

> **4.4 — The verdict does not move.** The floors are 50 and 30 against 10 and 10.
> `Trigger.FLOOR_UNMET` fires, the verdict is `INSUFFICIENT_EVIDENCE`, the seal is not read and the
> query count is 0 — `PREREGISTRATION-0.11.0.md` §3's evaluation order, unamended. **Nothing in
> this amendment can produce a promotion**, and a build that reports one has violated it.

## 5. Stopping rules

* **Nothing in `PREREGISTRATION-0.16.0.md` is amended.** `m(c)`, the floor of 0.50, the
  gesture-to-assertion map, the floors of its §3 and the bag-provenance deferral of its §5 stand
  exactly as ratified. A model input added here is a violation of that plan, not of this one.
* **`incumbent_linked` is not relaxed**, and neither is the alarm-lifecycle prohibition.
* **No floor is lowered**, and no floor is read differently because the reader was repaired.
* **The predictions of §4 are not revised after the census is run.** A number that moved in a
  direction §4 did not predict is reported as the release's leading finding.
* **`0015` carries `bag_key` and nothing else.**
* **This file is not edited** once ratified. A disagreement with it goes into the release notes as
  an opinion for v0.17.0, with the data that produced it — §9 of the v0.16.0 plan, applied to this
  one.
