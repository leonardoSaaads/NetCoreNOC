# Pre-registered analysis plan — v0.9.0

<!-- release-claim: v0.9.0 = shadow-mode -->

**Written in Phase 1, before any model existed, and not edited afterwards.** Its SHA-256 is recorded
in [`../gates/v0.9.0-phase-1.md`](../gates/v0.9.0-phase-1.md) and asserted by
`tests/test_preregistration.py`, so an edit made after seeing a result turns the suite red.

**What that guard does and does not do.** It makes this document *immutable*. It does not make it
*honest*. Nothing here stops a future release writing a new plan, and nothing detects a plan written
loosely enough to accommodate whichever result arrives. Honesty is in §7, where every outcome is
given its conclusion in advance, and in the fact that §7's most likely branch — *insufficient data* —
was written by an author who had already measured (`../gates/v0.9.0-phase-0.md` §2) that it is the
branch this release would land on.

Authority: `../scope/SCOPE-0.9.0.md` wins on scope, `../architecture/MODULE-ARCHITECTURE.md` on
placement, `../security/threat-model.md` on security posture. This document governs **what will be
measured, how, and what each result will be taken to mean.**

---

## 0. The invariant everything below inherits

> **No metric that decides promotion may be computed against `incumbent_linked`.**

Restated from `../architecture/SHADOW-MODE-0.9-DRAFT.md` §0 and unchanged. `incumbent_linked` is a
legitimate column — provenance, context, comparison basis, and in principle an input feature. It is
never the target. **There is no promotion mechanism in this release at all**, which is what makes the
invariant cheap to hold here and expensive to lose later, so it is restated in
`../architecture/HONEST-JUDGE-0.10-DRAFT.md` for the release that will be tempted.

A second commitment of the same kind, specific to this release:

> **Pairwise accuracy is not reported as a quality metric, in any section, under any name.**

Phase 0 §1 measured why: the champion accepts 99.83 % of the pairs it evaluates, so a classifier that
always answers "link" scores 99.83 %. A number that a constant function achieves cannot distinguish
a model that learned something from one that learned the base rate.

---

## 1. Hypotheses

Stated as questions with a decision attached, not as things hoped for.

**H1 — the champion's agreement with the operators (no model required).**
*What fraction of labelled bags did the operator `confirm`, and how does that fraction decompose by
bag size, storm, mixed-versus-uniform, scope restriction, operator and capture provenance?*
This is the release's primary deliverable and it is descriptive: there is no null hypothesis, no
test, and no threshold. It is reported with a clustered interval over **bags** and it bounds the
value of v0.10.0 through v0.13.0.

**H2 — feasibility.**
*Given a corpus meeting §5's floors, does a logistic challenger trained on it produce a partition
whose `over_merge_rate` and `under_merge_rate` against the human verdicts are no worse than the
champion's?*
H2 is **conditional on §5**. If the floors are unmet, H2 is not evaluated, no model is fitted, and
§7.1 applies. H2 is a feasibility question in the sense the shadow-mode draft insists on: an
affirmative answer means *something can be learned from this data*, and means nothing whatever about
whether anything should be promoted.

**H3 — does the label-derivation policy matter?**
*Do policies A and B (§3) produce different coefficients, different partitions, and different
partition metrics — and if so, by how much?*
H3 is evaluated whenever H2 is, and it has no preferred answer: agreement and divergence are both
informative results and §7.5–§7.6 say what each will be taken to mean.

---

## 2. The training population — the release's most consequential modelling choice

Pre-registered here because §11 of the build prompt rules that a modelling ambiguity resolves to
*"pre-register it and report the alternative"*, and because Phase 0 measured that the naive choice
is fatal.

### 2.1 What is chosen

Training rows are the promoted `dataset_pair` rows of labelled bags, with `capture_provenance =
'current'`, weighted as:

```
w(row) = w_bag(row) × w_class(row)

w_bag   = 1 / (number of promoted pairs in that row's bag)
w_class = W / (2 × W_c)   where W_c is the total w_bag mass of the row's derived class c,
                          and W is the total w_bag mass over both classes
```

Two effects, each with a reason the bias report already states in prose:

* **`w_bag` makes every bag contribute equally.** *Confirmation strength decays with bag size*: an
  operator confirming a 1 051-member storm did not verify 551 275 pairs, and one confirming two
  members verified the only pair there was. Phase 0 §2 measured bag sizes from 0 to 1 051 in one
  corpus, so without this the fit is a description of the largest storm present.
* **`w_class` balances the derived classes.** Under policy A the positives outnumber the negatives
  by roughly the accept rate, and an unweighted fit reaches its optimum by predicting the majority.

### 2.2 What is not chosen, and will be reported alongside

**(a) Restrict to mixed bags only** — the population where the champion made a decision it could
have got wrong. Rejected as the *training* population because Phase 0 §2 measured **5** mixed bags
in the richest corpus this repository can construct, of which **1** was a `split`. A fit restricted
to that is a description of five situations. **It is retained as a diagnostic population**: every
metric in §4 is reported over all bags and again over mixed bags alone, and where the mixed-bag
count is below the §5 floor the mixed-bag figures are printed with an explicit
*"below the pre-registered floor; not interpretable"* marker rather than omitted.

**(b) An unweighted fit over all pairs** — the default, and the one that produces the triumphant
number §14 of the build prompt warns about. Reported as a **contrast run**: the same code, the same
data, weights all 1.0, so the difference between it and the chosen weighting is visible as a number
rather than asserted.

### 2.3 The features, fixed now

Four features and an intercept. **Five free parameters**, which is what §5's events-per-variable
floor is computed against.

| # | Feature | Source | Why it is not an identifier |
|---|---|---|---|
| 1 | `decay = exp(−|Δt| / τ₀)`, τ₀ = 30.0 s fixed | `dataset_pair.delta_t_s` | a duration |
| 2 | `class_affinity` | `dataset_pair.class_affinity` | a learned scalar in [0, 1] |
| 3 | `entity_affinity` | `dataset_pair.entity_affinity` | a learned scalar in [0, 1] |
| 4 | `same_oid_root` ∈ {0, 1} | both observations' `trap_oid` share their first seven dot-components (`1.3.6.1.4.1.<enterprise>`) | a **relation between** two OIDs, not an OID |

**τ₀ is held at the champion's 30.0 s and is not learned.** Learning it makes the objective
non-convex and the fit non-deterministic in the way directive 5 forbids. Recorded as a limitation,
not hidden as an implementation detail.

**Excluded, deliberately, each with its reason:**

* `ne_id`, `class_id`, `alarm_a`, `alarm_b`, `situation_id`, `observation_a`, `observation_b` —
  **keys are not features** (migration 0008's rule 2). `ne_id = 47` means "the forty-seventh NE this
  appliance ever saw"; a model reading it learns one customer's estate and generalises to nobody
  while scoring extremely well on that customer's held-out data.
* `incumbent_linked` — permitted as an input by the shadow-mode draft §0, and **excluded here
  anyway**. A challenger that reads the champion's answer as an input can reach any accuracy against
  a target derived from the champion's behaviour without having learned anything about alarms, and
  this release has no evaluator strong enough to tell the two apart. Available to v0.10.0 once there
  is one.
* `storm` — a property of the traffic, not of the pair. Phase 0 §1 measured the accept rate swinging
  from 0 % to 100 % with it; a model given it learns "in a storm, link everything", which is the
  champion's rule, arrived at by imitation.
* `a_epoch`, `e_epoch` — the decay clock. Needed to *interpret* the affinities, monotone in time,
  and therefore a proxy for "when", which is a leakage vector rather than a feature.

### 2.4 Reproducibility, fixed now

* Training rows are ordered by `(feedback.id, dataset_pair.id)` — both stable, both unique.
* The optimiser is **batch gradient descent with a fixed iteration count**, no RNG, no shuffling,
  no early stopping on a validated quantity.
* Coefficients are compared **byte for byte** across two runs and two processes.
* Features are computed by **one function**, used by training, by offline reconstruction and by the
  online shadow path alike. §6's skew test is what proves that claim rather than repeating it.

---

## 3. The label-derivation policies

Both are implemented and both are reported. This is not negotiable by a deployment (§5.3).

| | Policy | Derivation |
|---|---|---|
| **A** | fabricate the negatives | `confirm` bag → every pair positive; `split` bag → every pair **negative** |
| **B** | discard the minority | `confirm` bags only; `split` bags discarded entirely |

**A's cost, named in advance:** an operator splitting a nine-member situation usually means *"these
three do not belong with those six"*, not *"all thirty-six pairs are wrong"*. A manufactures up to
thirty-five false negatives from one true statement, and every fabricated row is indistinguishable
from an observed one once written.

**B's cost, named in advance:** it throws away the entire minority class, which is the class any
model will get wrong, and leaves a training set on which "always positive" is optimal.

C (cannot-link constraints) and D (size-weighted confirms plus C) from the draft are **not
implemented in v0.9.0**. C needs a learner that accepts constraints, which logistic regression does
not without modification, and D needs C. The size-weighting half of D *is* implemented — it is
§2.1's `w_bag` — and is applied to both A and B, so the two policies differ in exactly one thing:
what they do with `split`.

---

## 4. The metrics

### 4.1 Primary: partition-level over-merge and under-merge

`eval/metrics.py`'s `over_merge_rate` and `under_merge_rate`, unmodified, applied to a partition the
challenger produced.

**How the partition is produced.** The challenger's link decisions are run through
`netcorenoc.preview.partition` — the same candidate-selection rule (`correlate.select_candidates`)
and the same union-find the engine's own what-if uses. Pairwise decisions are not scored; the
partition is.

**What the truth is.** The **human verdict at bag granularity**, never `incumbent_linked`:

* a **`confirm`** bag asserts *these members are one situation*. It is a truth label: every member
  carries the bag's id as its truth situation. A prediction that splits it is an **under-merge**; a
  prediction that unites two different confirm bags is an **over-merge**.
* a **`split`** bag asserts *these members are at least two situations, and does not say which*. It
  supports **no truth partition**, so it is excluded from the two rates above and scored by a third,
  separately named quantity:

  > **`split_bag_intact_rate`** — the fraction of `split` bags the challenger left wholly inside one
  > predicted component. Lower is better. This is the only quantity in this release that measures
  > the failure a NOC actually pays for: two incidents buried in one situation.

Reporting these as three numbers rather than folding the split bags into an over-merge rate is a
deliberate refusal to fabricate a denominator, and it is the partition-level analogue of not
choosing policy A silently.

**Coverage is reported, never dropped.** Bags whose `coverage` is `partial`, `none` or `empty` are
counted per class and reported beside every metric. A bag with `coverage = 'none'` contributes no
pairs and is excluded from the partition with that exclusion stated.

### 4.2 Secondary: calibration, at bag level

Bag level, because that is the granularity of the label. For each bag, the challenger's predicted
probability that the bag is a single situation is the **minimum** over the bag's pairs of the
challenger's link probability — the weakest link, which is what governs whether a connected
component holds together. Against the binary outcome `verdict == 'confirm'`:

* a **reliability curve** over five equal-width probability bins: bin, n, mean predicted, observed
  confirm rate;
* the **Brier score**, `mean((p − y)²)`.

**Stated in the report, not implied:** calibration is not implied by accuracy — a model can rank
perfectly and be badly calibrated, which is precisely the case where a threshold does the most
damage. And the half that is a trap, addressed to v0.11.0 and later:

> **Confidence may route what the operator is asked. It may never authorise autonomy.** The autonomy
> trigger is measured agreement with humans, never the model's self-reported certainty.

### 4.3 Reported, and explicitly not a quality metric

Agreement between the challenger and `incumbent_linked`, and the challenger's pairwise accuracy
against derived labels. Both are printed under a heading that says they measure the pipeline and not
the model, because a reader will compute them anyway and it is better that the report states their
meaning than that a spreadsheet invents one.

### 4.4 The champion, measured the same way

Every metric in §4.1 and §4.2 is computed for the **champion** over the same bags, from the captured
`score` and `incumbent_linked` columns. A challenger number with no champion number beside it is not
a comparison.

---

## 5. Sufficiency: the floors, and how a deployment may raise them

### 5.1 The governance model

> **`resolved = the more demanding of (project floor, deployment policy)`.**

The same shape as `ceiling(role) ∩ granted`, which made privilege escalation structurally impossible
in v0.7.0. The asymmetry is the argument, and it is stated rather than assumed: **softening admits a
bad model; hardening rejects a good one.** Those costs are not symmetric. A rejected good model costs
a release. An admitted bad one costs the operator's trust in every grouping the product makes
afterwards, and that is not recoverable by a later fix. So the monotone direction is toward evidence,
always, and "more demanding" is resolved per threshold with its direction declared (§5.2).

A deployment expresses its policy in the `meta` key **`config.evidence_floors`**, absent by default —
the same mechanism `config.dataset_retention` uses (DECISIONS #111). **No route, no capability, no
new HTTP surface.** The resolved thresholds are recorded in the challenger run's provenance and
printed at the top of the report, so two deployments reporting "passed" cannot mean different things
without saying so. An unreadable value falls back to the **project floors** as a whole and raises an
operator warning — never a partial reconstruction, and never softer than shipped.

### 5.2 The project floors

| Requirement | Floor | Direction of hardening | Why this quantity |
|---|---|---|---|
| `split` bags | **≥ 10 per free parameter = ≥ 50** (five free parameters, §2.3) | raise | events-per-variable convention for a stable logistic fit; scales itself if features are added |
| **mixed** bags | **≥ 20** | raise | the only population where there was a decision to get wrong |
| distinct operators | **≥ 3** | raise | one or two operators means modelling a person |
| single-operator share | **≤ 60 %** | lower | concentration makes a clustered interval meaningless |
| merge-aware distinct incidents | **≥ 30** | raise | the real unit of independence |
| derivation policies implemented and reported | **≥ 2 — A and B are mandatory** | — | not deployment-configurable (§5.3) |

Every floor is counted over bags with `capture_provenance = 'current'`. `legacy_capture` rows are
excluded by default; including them is an explicit choice, and when made the floors are evaluated
and reported **twice**, separately, never blended.

### 5.3 What a deployment may never touch

The requirement to implement and report at least two derivation policies; the prohibition on
evaluating against `incumbent_linked`; the pre-registration itself; and the floors **as floors** —
`config.evidence_floors` can make a requirement stricter and can never make one looser, including by
setting it to zero, to null, or by omitting it. The product makes the cost of an *operational* choice
visible; it does not make the *evidentiary standard* negotiable.

### 5.4 Held-out data, for reporting only

Chosen **by incident and by time**, never at random:

1. every labelled bag is mapped to its merge-aware incident, `COALESCE(situation.merged_into,
   feedback.situation_id)`;
2. incidents are ordered by the timestamp of their **earliest** label;
3. the **last third by that order** is held out; the first two thirds train.

An incident is wholly on one side, so the merge chain cannot leak across the boundary. Ties break by
incident id, so the split is deterministic.

> **This is not v0.10.0's split, and this release does not claim it is.** It is a reporting device
> over an *n* in the tens. It has no confidence attached beyond the clustered interval printed with
> it, it was chosen before any result was seen, and it will be superseded by
> `HONEST-JUDGE-0.10-DRAFT.md`'s design. Anyone quoting a held-out number from this release as
> evidence of generalisation is quoting it against this paragraph.

### 5.5 Intervals

Every rate is reported with a **cluster bootstrap over bags**, 1 000 resamples, seeded with a fixed
constant, resampling **incidents** (not bags, and never pairs) so the merge chain and the
within-incident correlation are respected. Percentile interval at 2.5 % / 97.5 %. Where the number of
incidents is below 10 the interval is printed as `n/a (too few incidents)` rather than as a number
nobody should read.

### 5.6 The projection, when the floors are unmet

*"Not enough yet"* is not actionable; *"about seven months at the current rate"* is. For each unmet
floor:

```
span_days = (newest label created_at − oldest label created_at) / 86400
rate      = observed count of that quantity / span_days
shortfall = floor − observed
projection_days = shortfall / rate
```

Reported in months (30.44 days). **Where `span_days` is zero, or fewer than two labels exist, or the
rate is zero, the projection is `undefined` and is printed as `undefined`** — an extrapolation from a
single instant is a fabricated number, and this release does not print one.

---

## 6. Shadow: the two mechanisms and the skew test

Both ship. They answer different questions and their **disagreement is the measurement neither
produces alone**.

| | measures | cannot measure |
|---|---|---|
| **offline reconstruction** — recompute the challenger's opinion from stored features | model quality, at no ingest cost | training/serving skew, **by construction**: recomputing from the stored features is tautologically consistent with them |
| **online shadow** — score live in the engine, write a **sample** of opinions | real per-call latency, real behaviour under real traffic | nothing further about quality |

**The pre-registered skew test.** For every sampled pair, the online opinion and the offline
reconstruction must be equal **bit for bit** — the score compared by `==` on the float, not by a
tolerance. The report states the observed divergence rate over real data. Registered in advance:

* **expected divergence: 0.0000 %.** Any non-zero rate is a defect, not a tolerance to be widened.
* a divergence rate above zero means **model quality figures in the same report are figures about
  features that were never served**, and §7.9 says what will be concluded.

**Sampling.** The deployment chooses the rate and the duration; it does not choose whether online
ever runs. Default low enough that the ingest cost is a rounding error against the 62 pair rows per
trap capture already writes, and that is **proved by a measurement in rows, bytes and microseconds**,
not claimed.

---

## 7. What will be concluded under each outcome

Written before any of them was observable. Each branch names what the release will *say*, so that no
result can be received as a surprise that needs interpreting.

**7.1 — Any §5 floor is unmet.** *The most likely branch, and Phase 0 §2 already indicates it.*
The release reports **insufficiency**, fits nothing, records no coefficients as a challenger, and
publishes §5.6's projection per unmet floor. Conclusion, stated in the build report's opening:
*the corpus cannot support a challenger, this is a successful outcome of the release, and the case
for shrinking v0.10.0–v0.13.0 rests on the champion-agreement number of H1 rather than on any model.*
The shadow mechanisms of §6 still ship and are still measured, because they are infrastructure whose
correctness is testable without a model.

**7.2 — Floors met; the challenger is no better than the champion on both partition metrics.**
Conclusion: *the champion is at or near the ceiling this feature set supports on this corpus.*
Recommendation to v0.10.0: build the evaluator, not a bigger model. **No promotion, and no claim that
the challenger is worse** — an *n* in the tens cannot establish that either.

**7.3 — Floors met; the challenger is better on both `over_merge_rate` and `under_merge_rate`, and
its interval excludes the champion's point estimate.**
Conclusion: *feasibility is established — something is learnable from this corpus.* Nothing about
promotion, which is v0.11.0's and requires v0.10.0's evaluator first. The interval is quoted with
every statement of the result.

**7.4 — Floors met; the challenger is better on one metric and worse on the other.**
Conclusion: *no superiority claim.* Over-merge and under-merge are different failures with different
costs to an operator, this release has not measured those costs, and trading one for the other is a
product decision that no metric in it can make. Both numbers reported, no composite score computed.

**7.5 — A and B agree** (coefficients within the reported tolerance, and partition metrics equal to
the printed precision).
Conclusion: *the `split` bags contributed nothing to the fit.* That is a **diagnostic result, not a
tie**: it means the minority class is too small or too uniform to move the optimiser, and it directly
supports the v0.10.0 recommendation to acquire `split` labels deliberately — which is active learning,
which is out of scope here and specified as v0.10.0's question.

**7.6 — A and B diverge.**
Conclusion: *the fabricated negatives are driving the model, and the size of the divergence is the
cost of policy A.* The divergence is reported as the difference in each partition metric and in each
coefficient. Neither policy is declared correct; the release reports the cost of the choice, which is
what the shadow-mode draft asked v0.9.0 to do instead of assuming.

**7.7 — H1 decomposes: high agreement on uniform bags, materially lower on mixed bags.**
Conclusion: *the ML programme has located headroom.* The mixed-bag agreement rate is the target
v0.10.0 through v0.13.0 are aiming at, and the aggregate is not.

**7.8 — H1 is uniformly high across every conditioning in §1.**
Conclusion: *the headroom is small, and the case for four more model releases is weak.* The release
says so plainly in its build report, in the first paragraph, whatever else it found.

**7.9 — Online and offline shadow disagree at any non-zero rate.**
Conclusion: *a training/serving skew defect exists.* The rate is reported, the release does **not**
present model-quality figures as if the served features were the trained ones, and the defect is
raised as a finding in `SECURITY-REVIEW-0.9.0.md` rather than as a footnote. Fixing it is in scope
for this release; reporting quality numbers over known-skewed features is not.

**7.10 — The champion's agreement cannot be computed** because no labelled bag survives with usable
coverage. Conclusion: *the corpus is not merely small, it is unusable*, and that is reported as the
headline rather than as an absence.

---

## 8. Stopping rules, and what is forbidden after the first result

* **One fit per policy**, at the configuration in §2. No re-running with different weights, feature
  sets, iteration counts or thresholds after seeing a metric. The contrast run of §2.2(b) is
  pre-registered and is therefore not a re-run.
* **No metric is added after a result is seen.** A quantity not named in §4 may be *measured* and
  reported under "additional observations", and may never be used to support a conclusion in §7.
* **No floor is changed.** §9 is where an opinion about a floor goes, and it is an opinion addressed
  to v0.10.0, not an amendment to this document.
* **This file is not edited.** `tests/test_preregistration.py` enforces it.

---

## 9. Where a disagreement with this plan goes

Into `../security/SECURITY-REVIEW-0.9.0.md` §critical-analysis, as an **opinion for v0.10.0**, with
the data that produced it — never as a change here. The reviewer of a pre-registered plan is
entitled to say *"floor X was wrong"* after seeing the data; they are not entitled to say it by
editing the plan, and this repository makes the difference structural rather than cultural.
