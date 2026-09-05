# Pre-registered analysis plan — v0.16.2 (amendment: what promotion asserts)

<!-- release-claim: v0.16.2 = critical-repairs -->

**STATUS: RATIFIED 2026-09-05.** This document becomes the pre-registration the moment it is
ratified. From that instant it is immutable: its SHA-256 is pinned by
`tests/test_preregistration.py` beside the six already there.

**Ratify before any v0.16.2 code is written.**

It amends [`PREREGISTRATION-0.16.0.md`](PREREGISTRATION-0.16.0.md) §2 and nothing else. Every other
registration in that plan and in [`PREREGISTRATION-0.16.1.md`](PREREGISTRATION-0.16.1.md) is
unchanged and still binding.

## 0. What was known when this was written

**Known — the census.** `asserting_bags = 10` against a floor of 50; `asserting_incidents = 10`
against 30; `asserted_negative_pairs = 2 222`, of which **1 050 came from one bag**. The corpus's
ceiling is 41 asserting bags whatever an operator does, because 69 of its live windows offer a
single-member situation and F36 bounds a situation at one asserting label.

**Known — promotion is currently implicit.** `store.promote_situation` is called from **seven** sites:
one verdict path, four lifecycle gestures, two annotate paths. Any gesture on a `new` situation moves
it to `open`. No call site asks the operator whether the grouping is correct.

**Known — the maintainer's intent.** `new → open` should mean *"a human triaged this and the
grouping is correct"*, and corrections should still be possible in `open`, though unusual.

**NOT known.** No operator has ever been asked to affirm a grouping as a distinct action. **The
registrations below are chosen from the arithmetic of the current corpus and from the failure mode
§2 names, never from an outcome, because no outcome exists.**

## 1. The invariants this plan inherits without amendment

> **No metric that decides promotion may be computed against `incumbent_linked`**, nor against any
> signal that is not an assertion about a grouping. The manual clear of a zombie alarm and the
> self-clear remain outside the link-training path.

> **`m(c) = 0.6 + 0.4·c` for `c ≥ 0.50`; below the floor the action happens and no training row is
> written.** Unchanged.

> **`asserting_bags` counts gestures, not pairs.** Unchanged.

> **Bag provenance is recorded and not consumed.** Unchanged.

## 2. DECISION — promotion and assertion are two actions

### 2.1 The alternative, and why it is rejected

The cheap design is that promoting a situation to `open` **is** a `confirm`: the operator looked, the
operator worked it, therefore the operator agrees. One line of code, and `asserting_bags` would rise
with every triage.

**It is rejected, and the reason is the failure mode rather than a preference.** If promotion is
required to work a situation, an operator under load will promote to get on with the shift. The
appliance would then record, at scale, `confirm` assertions that mean *"I needed this out of my
way"*. That is **impatience wearing evidence's name**, and it is indistinguishable from judgement in
every column the corpus stores.

The project has measured this shape before. `PREREGISTRATION-0.9.0.md` §1 records that the champion
accepts 99.83 % of pairs and that a model trained on the joined data scores 99.8 % **by always
predicting link** — a triumphant number produced by a population that never disagreed. Promotion-as-
confirm would manufacture the same population deliberately.

### 2.2 What is registered

> **Two distinct operator actions, and the appliance must be able to tell them apart:**
>
> * **Affirm** — *"this grouping is correct."* Promotes `new → open` **and** records a `confirm`
>   assertion with the operator's confidence, under the existing `m(c)` and floor.
> * **Promote without judging** — moves `new → open` and **asserts nothing.** No training row, no
>   `acquisition_channel`, no bag.
>
> **A restructuring gesture — `move`, `merge`, `operator_split` — promotes if it must, and does not
> affirm.** Correcting a grouping says the *previous* grouping was wrong; it does not say the result
> is right. Those gestures keep the assertions §2 of the v0.16.0 plan registers for them, and gain
> none.
>
> **The event log records which action occurred.** An affirmation and a bare promotion are different
> facts about the same transition, and a reader two months later must be able to tell them apart.

### 2.3 What is not registered, and stays open

**Whether a bare promotion is itself weak evidence.** An operator who read a situation and declined
to affirm it has told the appliance something. **This plan does not decide what**, and a build that
turns it into a training row of any weight has violated this plan. It is an open question for a later
release, to be decided when there are enough bare promotions to look at.

## 3. The floors are unchanged

> Every floor registered in v0.9.0 and v0.10.0 is retained: `asserting_bags ≥ 50`,
> `asserting_incidents ≥ 30`, `split` bags ≥ 50, mixed ≥ 20, operators ≥ 3, top-operator share
> ≤ 60 %, incidents ≥ 30, two derivation policies.
>
> **No floor moves because a new action exists.**

## 4. What will be concluded under each outcome

**4.1 — The census is unchanged.** The expected branch. Separating the actions does not create
assertions; it stops manufacturing them. The report says so, and `INSUFFICIENT_EVIDENCE` remains the
verdict.

**4.2 — The census falls.** Possible if a call site that promoted was also, in effect, recording an
assertion that no longer fires. **Reported, with which call site.** A fall here is the plan working.

**4.3 — The census rises.** **Treat with suspicion before celebration.** Check that no path makes
affirmation implicit, that `asserting_bags` still counts gestures and not pairs, and that no
restructuring gesture gained an assertion §2 of the v0.16.0 plan did not give it. A number that rose
because promotion started implying agreement is the outcome §2.1 rejects, arriving anyway.

**4.4 — Operators affirm almost nothing.** Recorded as the measurement it is, not as a failure of the
design. It would say the affirmation is too expensive or too unclear, which is a UI question for
v0.16.4 and a real finding.

## 5. Stopping rules

* **§2.2's split is not collapsed** after any census is seen.
* **A bare promotion does not become a training row** in this release.
* **No floor is lowered.**
* **`m(c)`, its floor, and the gesture-to-assertion map are unchanged.**
* **This file is not edited** once ratified.

## 6. Where a disagreement with this plan goes

Into the release notes as an opinion **for v0.16.3**, with the data that produced it — never as a
change here.
