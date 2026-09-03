# Pre-registered analysis plan — v0.16.0 (the situation lifecycle)

<!-- release-claim: v0.16.0 = situation-lifecycle -->

**STATUS: RATIFIED 2026-09-03.** This document becomes the pre-registration the moment it is
ratified. From that instant it is immutable: its SHA-256 is pinned by
`tests/test_preregistration.py`, so an edit made after seeing a result turns the suite red.

**Ratify before any v0.16.0 code is written.**

Its parents are [`PREREGISTRATION-0.9.0.md`](PREREGISTRATION-0.9.0.md),
[`PREREGISTRATION-0.10.0.md`](PREREGISTRATION-0.10.0.md) and
[`PREREGISTRATION-0.11.0.md`](PREREGISTRATION-0.11.0.md), whose floors, metric set, verdict states
and contamination protections this plan inherits **without amendment**.

## 0. What was known when this was written

**Known — the corpus.** `asserting_bags = 0`, `asserting_incidents = 0`,
`asserted_negative_pairs = 0`. Every release since v0.9.1 has returned `INSUFFICIENT_EVIDENCE`
against a registered floor of 50 asserting bags and 30 asserting incidents.

**Known — the champion accepts 99.83 %** of the pairs it evaluates, and rejections live in quiet
traffic that attracts no labels.

**Known — the statistics of this `n`.** A cluster bootstrap over 37 incidents gives a 95 % interval
0.289 wide; the minimum difference detectable at 80 % power is 25 percentage points.

**Known — the design effect.** One 501-member bag with 250 marks contributes 62 750 pairs from a
single human decision.

**Known — the merge mechanism.** With `W_A + W_E = 0.70` against a threshold of 0.50 and a temporal
term that reaches 0.0055 at the window's edge, two alarms link on affinity alone; and situations are
connected components while scoring is pairwise, so one weak bridge merges two incidents. F76 is the
measured instance.

**Known — `TrainingRow.weight` already carries two meanings**: the per-bag design-effect correction
`1/len(bucket)`, and the class balance applied after it.

**NOT known.** No operator gesture of the kinds this release adds has ever been recorded. No
confidence has ever been reported by an operator of this system. **Every threshold below is chosen
from the corpus census and from the arithmetic of the feature space, never from an outcome, because
no outcome exists.**

## 1. The invariants this plan inherits without amendment

> **No metric that decides promotion may be computed against `incumbent_linked`**, including as a
> feature, including as a fallback where a human verdict is missing.

> **The same prohibition extends, for the same reason, to any signal that is not an assertion about
> a grouping.** A **manual clear of a zombie alarm** and a **self-clear** are facts about an alarm's
> lifecycle. They are recorded as events and they produce **no link-training row**. A fact about a
> different question may not do the work of a measurement about this one.

> **Pairwise accuracy is not reported as a quality metric, in any section, under any name.**

> **`resolved = the more demanding of (project floor, deployment policy)`.** Monotone toward
> evidence.

> **A quantity that describes the evidence is derived by the server.**

## 2. DECISION 1 — what each gesture asserts, and at what granularity

> **Registered, and not extended by any other document:**
>
> | Gesture | Assertion | Unit |
> |---|---|---|
> | `confirm` | every pair in the bag is positive | bag |
> | `split` (verdict only) | marked members negative against the remainder | pair subset |
> | **`move`** — alarm A from S1 to S2 | A × members(S1) **negative**; A × members(S2) **positive** | pair, both signs |
> | **`merge`** — S1 with S2 | every cross pair positive | bag × bag |
> | **`operator_split`** — S into S1, S2 | every cross pair negative | bag × bag |
> | `manual_clear` | **nothing about the grouping** | — |
> | self-clear | **nothing about the grouping** | — |
>
> Each is recorded with its own `acquisition_channel`, **reported separately and never averaged**
> (DECISIONS #126).

**The design effect applies to the new gestures exactly as §2.1(c) of the v0.10.0 plan applied it to
the old.** A merge of two 200-member situations yields 40 000 cross pairs from **one** human
decision.

> **Registered:** every rate computed over the new channels is aggregated **per gesture**, as the
> mean over gestures, with a cluster bootstrap over **incidents** — never pooled over pairs.
> `asserting_bags` counts a gesture, not a pair. A gesture that produces 40 000 pairs increments it
> by **one**.

## 3. DECISION 2 — the floors are unchanged

> **Every floor registered in v0.9.0 and v0.10.0 is retained unchanged**: `asserting_bags ≥ 50`,
> `asserting_incidents ≥ 30`, `split` bags ≥ 50, mixed ≥ 20, operators ≥ 3, top-operator share
> ≤ 60 %, incidents ≥ 30, two derivation policies.
>
> **No floor is lowered because a new channel exists.** A new source of assertions changes how the
> floor is reached, not what it requires. Lowering a floor because it became easier to clear is the
> softening §1 forbids.

## 4. DECISION 3 — how operator confidence enters

Operator-reported confidence is **not calibrated**. This system has never collected any, no operator
of it has ever been scored, and the general finding that humans are systematically overconfident is
not a number this project has measured on its own users.

> **Registered:**
>
> * confidence is recorded on every gesture, **in its own column**, per actor, on a 0–1 scale;
> * a gesture with **confidence < 0.50 produces no training row.** The action still happens — the
>   operator is running the network, not labelling it — and the event is recorded in full;
> * for confidence `c ≥ 0.50`, the training row's weight is multiplied by
>
>   ```
>   m(c) = 0.6 + 0.4 · c          m(0.5) = 0.80,  m(0.8) = 0.92,  m(1.0) = 1.00
>   ```
>
> * this multiplier is applied **at derivation**, composed with the existing design-effect and
>   class-balance factors, and the composition is recorded in the run's diagnostics. It is **never**
>   folded into a stored `weight`.

**Why shrunk rather than direct.** A direct weight lets a systematically overconfident operator
dominate; a pure filter throws information away. Shrinking toward 1.0 bounds the damage: a
miscalibrated operator degrades a row's contribution by **at most 20 %**, which is a known bound
rather than an unknown one.

**Why 0.6, 0.4 and a floor of 0.50.** They are **conventions**, chosen for a bounded multiplier over
the half-open range a self-report can meaningfully occupy, and they are **not derived from any
measurement of operator calibration, because none exists.** They are registered so that they cannot
be chosen later to suit a result.

> **What is registered about calibration, so it is not lost:** confidence is stored per actor
> precisely so a later release can measure whether a given operator's stated 0.8 corresponds to an
> 0.8 rate of being right, once the corpus contains enough corrected gestures to check. **Until that
> measurement exists, `m(c)` is not revised.**

## 5. DECISION 4 — bag provenance is recorded and not consumed

Two quantities are recorded beside every training row:

* the **weakest link's margin** over the threshold within the bag;
* whether the bag's link graph has a **bridge** whose removal splits it into two parts each above a
  registered minimum size.

> **Registered: neither enters any model, any metric that decides, or any floor, in v0.16.0.** They
> are recorded because they cannot be recomputed later — the scores decay, membership mutates, and
> `0008`'s first rule applies. They are **reported** in the census, stratified.
>
> A build that supplies either to a scorer, a promotion input, or a verdict trigger has violated this
> plan.

**Why deferred rather than used.** A bag held by one frail edge is plausibly worth less than a dense
one, and that plausibility is exactly the kind of thing that looks right and has never been measured.
Using it now would encode an unmeasured belief as a weight, which is the error §2.1(c) of the v0.10.0
plan rejects under a different name.

## 6. DECISION 5 — the verdict is unchanged

> The judge returns exactly one of `BETTER`, `NOT_BETTER`, `INSUFFICIENT_EVIDENCE`, with every
> trigger of `PREREGISTRATION-0.10.0.md` §6.2 implemented unchanged. **`INSUFFICIENT_EVIDENCE`
> remains terminal within its release.**
>
> **The sealed holdout is not spent by this release.** Query count 0, asserted, unless a
> promotion is exercised, in which case it follows §4.3 of the v0.10.0 plan without amendment.

## 7. What will be concluded under each outcome

**7.1 — The census moves and floors are still unmet.** The expected branch. Verdict
`INSUFFICIENT_EVIDENCE`. The release reports the census before and after, the number of gestures of
each kind, and **what rate of operator gestures would be needed to clear the floors**. This is a
successful release: the mechanism exists and the number moved for the first time since v0.9.1.

**7.2 — The floors are met and the judge returns a verdict.** The first real verdict in the project's
history. It is reported with its interval, its query count, and the channel breakdown, and **no
claim is made that generalises beyond the corpus that produced it.**

**7.3 — The census moves a great deal, quickly.** Treat with suspicion before celebration. Check
first that `asserting_bags` counts gestures and not pairs (§2), that a single merge has not
incremented it by its cross-pair count, and that the channels are reported separately. **A number
that moved because the unit changed is not a number that moved.**

**7.4 — A channel dominates.** If one `acquisition_channel` supplies most assertions, that is
reported and the metrics are shown per channel. No channel is dropped and none is blended.

**7.5 — The census does not move.** The mechanism exists and operators did not use it, or the
demonstration did not exercise it. Reported plainly, with which of the two it was, and what would
have to change.

**7.6 — A zombie clear or a self-clear is found to have produced a training row.** A defect, not a
result. The row is removed, the guard is added, and the finding is issued.

## 8. Stopping rules

* **`m(c)`, its floor, and the channel-to-assertion mapping of §2 are not changed after any census
  or verdict is seen.**
* **No floor is lowered.**
* **Bag provenance is not consumed** in this release.
* **`incumbent_linked` is not relaxed**, and neither is the prohibition of §1 on alarm-lifecycle
  signals.
* **No metric is added after a result is seen.** A quantity not named in `PREREGISTRATION-0.10.0.md`
  §5 may be reported under *"additional observations"* and may never support a conclusion in §7.
* **This file is not edited** once ratified.

## 9. Where a disagreement with this plan goes

Into the release notes as an opinion **for v0.16.1**, with the data that produced it — never as a
change here. The reviewer of a pre-registered plan is entitled to say *"the floor of 0.50 was
wrong"* after seeing the data. They are not entitled to say it by editing the plan.
