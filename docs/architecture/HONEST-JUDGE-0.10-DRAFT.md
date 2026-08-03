# The honest judge — v0.10.0 draft (specification only, not implemented in v0.9.0)

<!-- release-claim: v0.10.0 = honest-judge -->

**Implement none of this in v0.9.0.** Every element below is tagged **`v0.10.0: planned`**. v0.9.0
holds out data to *report* a number and says in its own pre-registration that this is **not** the
split; a specification is not a licence to start early, and the release that builds the evaluator
must not also be the release that was measured by it.

This document is written **from what v0.9.0 measured**, not from what v0.9.0 hoped. Where the two
differ, the measurement wins and the difference is recorded.

Its parent is [`SHADOW-MODE-0.9-DRAFT.md`](SHADOW-MODE-0.9-DRAFT.md); the binding sequence is
[`ROADMAP-0.8-TO-0.13.md`](ROADMAP-0.8-TO-0.13.md); the evidentiary discipline it inherits is
[`../analysis/PREREGISTRATION-0.9.0.md`](../analysis/PREREGISTRATION-0.9.0.md).

---

## 0. The invariant, restated because this is the release that will be tempted (`v0.10.0: planned`)

> **No metric that decides promotion may be computed against `incumbent_linked`.**

v0.9.0 could hold this cheaply: it has no promotion mechanism, so it had no promotion decision to
corrupt. **v0.10.0 is the release that builds the thing a promotion will be decided by**, and it
therefore inherits the invariant at the exact moment it becomes expensive.

The temptation has a specific shape and it is worth naming so it can be recognised: an evaluator
needs a lot of labelled data, `incumbent_linked` is available on every captured pair, and *"use the
champion's decision as a weak label where a human verdict is missing"* is a sentence that will sound
reasonable in a design review. **It is the imitation trap.** A challenger judged by the champion can
only converge on the champion; the champion's performance becomes the challenger's ceiling, and the
number looks excellent all the way up.

`incumbent_linked` remains a legitimate column: provenance, context, comparison basis, and in
principle an input feature. v0.9.0 excluded it even as a *feature*, which is stricter than the
parent draft requires, and v0.10.0 may reasonably relax that — **once it has an evaluator strong
enough to tell a model that learned from one that copied.** Not before.

---

## 1. The split: by time or by incident, never at random (`v0.10.0: planned`)

A random 80/20 leaks. Alarms from one incident are correlated with each other **by construction**, so
a random split puts near-duplicates of the test set into the training set and reports a number that
cannot be reproduced on a network.

Two admissible splits, and the release should implement **both** and report both, for the reason
v0.9.0 implemented two derivation policies: a release that picks one and reports its number has
assumed what the previous release refused to assume.

| Split | Definition | What it measures | What it cannot |
|---|---|---|---|
| **by time** | train on labels before *T*, test on labels after | *"will this model work next month?"* — the question a deployment actually has | it confounds model quality with drift: the network changed too |
| **by incident** | an incident is wholly on one side | *"does this generalise across incidents?"* — the cleaner causal question | it says nothing about time, and a merge chain makes incident identity non-obvious (§2) |

**Neither is a superset of the other**, and reporting only one is choosing a question without saying
so. v0.9.0's reporting split — merge-aware incident, ordered by earliest label, last third held out —
is a *combination* of the two and is explicitly labelled as a reporting device rather than a design.

## 2. Why the merge chain makes incident identity non-obvious (`v0.10.0: planned`)

The single hardest part of §1, and the reason a split-by-incident is not a `GROUP BY`.

`situation.merged_into` (added by 0008, **forward-only**) records the destination of a merge. So the
merge-aware incident of a labelled bag is `COALESCE(situation.merged_into, feedback.situation_id)` —
which v0.9.0 uses throughout, and which is **one hop, not a chain**. Three facts a v0.10.0 author
must have in hand before writing the split:

1. **A merge chain can be longer than one hop.** `merge_situations(sid, other_sid)` marks the source
   merged into `sid`, and `sid` can itself later be merged. Resolving identity requires **following
   `merged_into` transitively to a fixed point**, with a cycle guard — the schema does not forbid a
   cycle and no code prevents one today.
2. **Situation identity is not stable under `sid = min(sids)`.** The id a label was written against
   is not necessarily the id anything holds afterwards, which is why `feedback.situation_id_at_label`
   exists. A split that groups on the *current* id and a split that groups on the *label-time* id
   are different splits.
3. **Merges before v0.8.0 are unrecoverable.** The destination was never written and no migration can
   reconstruct one. A pre-v0.8.0 database therefore has incidents that *look* independent and are
   not, and **no column distinguishes them from genuinely independent ones.** v0.10.0 must report how
   many of its incidents come from that era rather than assume none do.

**The consequence for effective sample size**, measured in v0.9.0: 41 labelled bags resolved to **37**
merge-aware incidents on the fullest corpus available. A ~10 % reduction, on a *n* already in the
tens.

## 3. The metric: over-merge and under-merge, and the third one (`v0.10.0: planned`)

Not accuracy — v0.9.0 measured why: the champion accepts **99.83 %** of evaluated pairs, so a
constant "link" scores 99.83 % pairwise.

v0.10.0 inherits `over_merge_rate` and `under_merge_rate`, which now live in
`netcorenoc.shadow_eval` and are re-exported by `eval/metrics.py` (DECISIONS #122), and it inherits
the third quantity v0.9.0 had to invent:

> **`split_bag_intact_rate`** — the fraction of `split` bags a model leaves wholly inside one
> predicted component. A `split` bag asserts *"these members are at least two situations"* without
> saying **which**, so it supports no truth partition; folding it into an over-merge rate fabricates
> a denominator.

**The measurement that proves this matters**, from v0.9.0's Gate 4:

| | over_merge | under_merge | **split_bag_intact** |
|---|---:|---:|---:|
| policy A | 0.0000 | 0.0000 | 0.0000 |
| policy B | 0.0000 | 0.0000 | **1.0000** |

Policy B scores perfectly on both headline rates **and buries every single split bag**. A release
reporting only over-merge and under-merge would have called it the better model. **v0.10.0 must not
drop the third number**, and it should consider whether a composite is ever appropriate — v0.9.0's
position, carried forward as a recommendation, is that over-merge and under-merge have different
costs to an operator, this project has not measured those costs, and a weighted sum would encode a
product decision as a metric.

## 4. The iterative-overfitting hazard, which no column measures (`v0.10.0: planned`)

Carried from the parent's §5, and it is the item most likely to be dropped because nothing fails when
it is:

> If v0.9.0 through v0.12.0 each tune against the same held-out set, **the fourth is reporting a
> number optimised against four times, by slow leakage through the researchers.** No code change
> causes it, no column records it, and no test catches it.

It is a **process hazard**, and the only defences are procedural:

* **A pre-registered plan per release**, hash-guarded, stating what will be concluded under every
  outcome *before* any result exists. v0.9.0 built that discipline
  (`tests/test_preregistration.py`); v0.10.0 inherits it and should not treat it as v0.9.0's
  peculiarity.
* **Recording, in each release's plan, how many times the held-out set has been looked at.** A
  counter maintained by hand, in prose, is worth more than nothing and is all that is available.
* **A "fresh" held-out set at least once**, if the corpus ever grows enough to afford one — data
  reserved from the first release that never informed a decision until it is spent, once.

**Say plainly in v0.10.0's build report which of these were actually done.** The honest failure mode
here is a release that lists the hazard and then does none of them.

## 5. What v0.10.0 inherits from v0.9.0 (`v0.10.0: planned`)

**Mechanisms**, all of which exist and none of which need building again:

* the **pre-registration discipline** and its hash guard;
* the **sufficiency floors** with `resolved = the more demanding of (project floor, deployment
  policy)`, monotone toward evidence (DECISIONS #114) — and the fact that a floor is **kept** when a
  model turns out to have fewer free parameters than registered;
* the **challenger as a structural `LinkScorer`**, so per-term explainability and `SafeScorer`'s
  fail-safe are inherited rather than rebuilt;
* the **slow loop off `store.lock`** in `maintenance_loop` (DECISIONS #118/#121) — the point v0.9.0
  had to create, since none existed;
* **both shadow mechanisms**, and the skew test as the difference between them, at a pre-registered
  0.0000 % and measured at 0.0000 % over 2 000 real opinions;
* the **admission filter**, defined before the first model and run against the **champion** too.

**Facts**, which are the harder inheritance:

* **The corpus does not meet v0.9.0's floors** — 13 `split` bags against 50, **5** mixed bags against
  20, and **exactly one bag that is both `split` and mixed**. v0.10.0's first act should be to check
  whether that has changed, and its plan should state what it will do if it has not.
* **Only ~12 % of labelled bags are mixed**, structurally. Whatever an operator's real confirm rate
  is, roughly seven eighths of it is agreement about arithmetic that could not have gone otherwise.
* **Policy B is degenerate** on bag-level labels: it derives only one class, so the target is
  constant. v0.10.0 should not spend a section re-discovering that.
* **`SECURITY-REVIEW-0.9.0.md` §5.4 argues the wrong floor was registered** — that
  `split ∧ mixed` bags, not `split` bags, is the population that carries information. That is an
  opinion offered for this release's plan to accept or reject **in advance**, which is the only way
  a pre-registration can receive one.

## 6. Explicitly not in v0.10.0

1. **Promotion.** v0.11.0's, after this release builds an evaluator worth trusting. A release that
   could promote would be judged by the only metric it had.
2. **Active learning.** Still the thing the corpus most needs and still out of scope, for the reason
   v0.9.0 gave: soliciting labels changes the distribution, and doing it before the organic
   population's bias is characterised destroys the baseline it would be measured against. If
   v0.10.0 wants to change that, it must argue it as its own decision and pre-register the
   consequence.
3. **Per-archetype models** (v0.12.0) and **the external cartridge** (v0.13.0).
4. **A composite quality score.** See §3.
5. **Relaxing the `incumbent_linked` invariant.** See §0 — and if v0.10.0 believes it has earned the
   relaxation, that belief belongs in its pre-registration, before the numbers.
