# Pre-registered analysis plan — v0.10.0 (the honest judge)

<!-- release-claim: v0.10.0 = honest-judge -->

**STATUS: RATIFIED — 2026-08-12.** This document becomes the pre-registration the
moment it is ratified. From that instant it is immutable: its SHA-256 is recorded in
`docs/gates/v0.10.0-phase-0.md` and asserted by `tests/test_preregistration.py`, so an edit made
after seeing a result turns the suite red.

**Ratify before any v0.10.0 code is written**, and after the three prerequisites of §10 are closed.

Its parent is [`../architecture/HONEST-JUDGE-0.10-DRAFT.md`](../architecture/HONEST-JUDGE-0.10-DRAFT.md);
its mould is [`PREREGISTRATION-0.9.0.md`](PREREGISTRATION-0.9.0.md); the evidence boundary it
consumes is [`../architecture/EVIDENCE-BOUNDARY-0.9.2.md`](../architecture/EVIDENCE-BOUNDARY-0.9.2.md).

## 0. What was known when this was written, declared so the choices can be audited

A pre-registration written after a corpus has been counted is not one unless it says what it knew.
Every quantity below was measured **before** any line of this plan was drafted.

**Known — the corpus census** (`../gates/v0.9.1-phase-0.md` §2, reproduced on the v0.9.2 tree):

| Quantity | Observed |
|---|---:|
| labelled bags | 41 |
| `confirm` / `split` | 28 / 13 |
| merge-aware distinct incidents | 37 |
| mixed bags / bags that are both `split` and mixed | 5 / 1 |
| distinct operators, top share | 3, 34.1 % |
| **per-label coverage** | **full 25, partial 6, none 6, empty 4** |
| bag sizes | min 0, median 1, p90 99, max 1 051 |
| **asserted negative pairs obtainable from this corpus** | **0** |
| `acquisition_channel = 'close'` rows | 0 |
| operator-initiated `situation.close` audit rows | 0 |
| labelling rate | **not measurable** |

**Known — eleven of the thirteen `split` bags have fewer than two members** (nine singletons, two
empty); the other two are storms of 240 and 501. Not one would yield a single asserted negative pair.

**Known — the statistics of this `n`**, simulated before this plan was drafted (§3.1 and §4.1 carry
the tables): a cluster bootstrap over 37 incidents gives a 95 % interval **0.289 wide**; the minimum
difference two models must have for that `n` to separate them at 80 % power is **25 percentage
points**; a third of it (12 incidents) needs **42**; and adaptive selection over 12 queries on 37
incidents inflates a reported rate by a **median +11.1 p.p. when every candidate is equally good**.

**NOT known.** No model has been trained against any v0.10.0 split. No judge has produced a verdict.
No over-merge, under-merge, intact rate or respected rate exists for any challenger under this plan.
**Every threshold below is chosen from the corpus census and from the statistics of the sample size,
never from an evaluation outcome**, because no evaluation outcome exists.

**The honest reading.** Knowing the census means every floor here was chosen knowing it would not be
met. That is not a defect; it is why §7's expected branch is `INSUFFICIENT_EVIDENCE` and why that
branch is written first. A plan that chose floors the corpus happens to clear would be the
suspicious one.

## 1. The invariants this plan inherits without amendment

> **No metric that decides promotion may be computed against `incumbent_linked`.**

v0.10.0 is the release that becomes tempted: an evaluator needs labelled data, `incumbent_linked`
sits on every captured pair, and *"use the champion's decision as a weak label where a human verdict
is missing"* will sound reasonable. It is the imitation trap. **v0.10.0 does not relax it, including
as a feature**, and §8 records that this was decided in advance rather than after a fit came out
thin.

> **Pairwise accuracy is not reported as a quality metric, in any section, under any name.**

The champion accepts 99.83 % of the pairs it evaluates. A number a constant function achieves cannot
distinguish learning from the base rate.

> **`resolved = the more demanding of (project floor, deployment policy)`.** Monotone toward
> evidence. A deployment may harden and may never soften, including by setting a floor to zero, to
> null, or by omitting it.

> **A quantity that describes the evidence is derived by the server.** Every threshold below counts
> `excluded_reconciled`, never `excluded_count`.

## 2. DECISION 1 — the floor unit and its population

### 2.1 The four candidate units

| | Unit | Moves when a label becomes more informative? | Independent? | Observed |
|---|---|---|---|---:|
| **(a)** | `split` bags | **no** | yes | 13 |
| **(b)** | `split ∧ mixed` bags | no | yes | 1 |
| **(c)** | asserted negative pairs | yes | **no** | 0 |
| **(d)** | **bags carrying a usable asserted negative pair** | **yes** | **yes** | **0** |

**(a) is the registered floor and v0.9.1 refuted it.** A partial split is still one bag, so a floor
counted in bags cannot respond to the improvement the project is making.

**(b) is `SECURITY-REVIEW-0.9.0.md` §5.4's argument** and is a better *population*, but a mixed bag
that gains an exclusion set is still one mixed bag. Same defect.

**(c) is the unit the paired model consumes, and it cannot be a floor.** Measured:

```
bag size n   marks m   asserted pairs
        15         7               56     <- meets a floor of 50 from ONE gesture
        60        30              900
       501       250           62,750
```

The pairs inside a bag come from **one human decision**, so the design effect reaches 62 750× and
the effective number of independent observations that bag contributes is **one**. A quantity one
gesture moves by four orders of magnitude is a lever, not a threshold.

**(d) fixes (a)'s defect without acquiring (c)'s.** A plain split contributes nothing; a partial
split contributes exactly one, whatever the bag's size.

### 2.2 What is registered

> **PRIMARY — `asserting_bags` ≥ 50.** A bag counts when **all** of:
>
> * `verdict = 'split'` and `excluded_reconciled ≥ 1`;
> * `coverage NOT IN ('none', 'empty')` — §2.6(c);
> * at least one of its asserted pairs was observable by the labeller — §2.4;
> * `capture_provenance = 'current'`; population `clean ∪ checked` — §2.3.
>
> **SECONDARY — `asserting_incidents` ≥ 30.** Merge-aware distinct incidents (§3.3's transitive
> resolution) contributing at least one `asserting_bag`.
>
> **REPORTED, NEVER FLOORED — `asserted_negative_pairs`**, with the maximum contributed by any single
> bag and the share from the largest three, so concentration is visible rather than inferred.
>
> **RETAINED UNCHANGED AS FLOORS** — every v0.9.0 floor: `split` bags ≥ 50, mixed ≥ 20, operators
> ≥ 3, top-operator share ≤ 60 %, incidents ≥ 30, two derivation policies. **None is replaced.**
> `asserting_bags` is added beside them. Replacing (a) with (d) would be softening, which §1 forbids.

Both new floors are minima; a deployment policy raises them and the larger value wins.

### 2.3 The population

> **`clean ∪ checked`. `unknown` is excluded, counted, and reported.**

A pre-`0011` row records no scope and is permanently uninterpretable. It may not be assumed clean and
may not swell a denominator. Acquisition channels are evaluated and reported **separately, never
blended**.

### 2.4 The blind-fraction rule for `checked`

> A `checked` bag counts toward `asserting_bags` only if **at least one** of its asserted pairs was
> observable by the labeller, and its contribution to `asserted_negative_pairs` is the **observable**
> count.

With `n = member_count`, `m = excluded_reconciled`, `h = scope_redacted_members`,
`b = excluded_reconciled_out_of_scope`:

```
observable asserted pairs = (m − b) · ((n − m) − (h − b))
```

**Exact, not a bound** — verified by exhaustive enumeration over 86 868 configurations for `n` from
2 to 8. `EVIDENCE-BOUNDARY-0.9.2.md` §10 presents an interval whose upper expression is spurious and
states the measured case as `[2, 2]` where it evaluates to `[2, 4]`. §10's correction is a
prerequisite of ratification (§10.2), because this rule depends on the exact expression.

A **truncated** report yields a lower bound. Such a bag may contribute to totals and **may not,
alone, carry a floor across its threshold**: if removing every truncated bag would drop
`asserting_bags` below 50, the verdict is `INSUFFICIENT_EVIDENCE` and the report names the
load-bearing bags.

### 2.5 The floors are NORMATIVE. What is statistical is a separate condition, and it is not a floor.

**This section exists because the two were about to be confused, and confusing them is how a
convention comes to be quoted as a result.**

#### What the statistics actually say

| Question the statistics answer | `n` required |
|---|---:|
| a cluster-bootstrap interval narrower than 0.20 | **≈ 100 incidents** |
| a minimum detectable difference of 16 p.p. | **≈ 120 incidents** |
| a minimum detectable difference of 10 p.p. | **≈ 300 incidents** |

Today: **37**. A power-derived floor would therefore sit between **100 and 300 asserting incidents**,
roughly three to eight times the entire current incident count.

#### What the registered floors are, and are not

| | Power-derived alternative | **Registered (normative)** |
|---|---|---|
| basis | interval width / detectable difference, simulated | events-per-variable convention, 10 per free parameter — v0.9.0's own basis |
| `asserting_incidents` | ≥ 100 … ≥ 300 | **≥ 30** |
| `asserting_bags` | *power says nothing about bags* | **≥ 50** |
| guarantees | that a difference, if real, is resolvable | that a fit is not degenerate and not one person's opinion |
| does **not** guarantee | anything about fit stability | **anything about resolvability** |

> **50 and 30 are normative choices. They are conventions, chosen for consistency with the floors
> already registered in v0.9.0, and they are not derived from, and do not imply, statistical power.**

#### Consequences of registering the normative floors

* **A corpus can clear both floors and still be unable to decide anything.** Sixty asserting bags
  over thirty-five incidents clears §2.2 and has a 25-percentage-point detection threshold. The
  floors are a **necessary condition, never a sufficient one.**
* **Therefore sufficiency of *evidence* is not the same as sufficiency of *power*, and the second
  lives in the verdict rather than in the floors** — §6.2's trigger returns `INSUFFICIENT_EVIDENCE`
  whenever the minimum detectable difference at the available `n` exceeds the observed difference.
  That trigger is what makes the normative choice safe to register.
* **Residual risk: a reader sees "floors met" and reads "the evaluation is trustworthy."** Mitigated
  structurally: **the report may never print a floor evaluation without the detection threshold for
  the same `n` beside it**, and a test asserts the two are emitted together.

#### Consequences of the power-derived alternative, and why it is not registered

* It would convert a **measurable** shortfall into an **unmeasurable** one. The projection needs a
  labelling rate and this repository has measured that it has none; a floor at 300 incidents would
  print `undefined` with no path stated.
* It would make "the floors" mean two different things in one list — every existing floor is a
  convention about fit stability, and mixing a power criterion in would silently redefine the word
  for the six thresholds that came before.
* **A power floor requires an effect size, and nobody has decided one.** Detecting 10 p.p. rather
  than 16 is a product decision about how much over-merge a NOC will tolerate, which this project has
  explicitly not measured (`HONEST-JUDGE-0.10-DRAFT.md` §3). Registering a power floor would encode
  an unmade product decision as a threshold — the same error §2.1(c) rejects.

#### What is registered about power, so that it is not lost

> The minimum detectable difference at the available `n` is **computed and printed with every
> evaluation**, using the simulation of §3.1 re-run at the observed `n`. It is a **reported quantity
> and a verdict trigger. It is not a floor**, it may not be hardened by a deployment policy, and no
> deployment may disable it.

### 2.6 Asserted negative pairs — four properties that govern how they may be used

**(a) They are TRUTH, not features.** An asserted negative pair enters the judge as ground truth for
a partition comparison. Features come from `dataset_pair`. Feeding an assertion in as a feature would
be F46 in a different register — a human statement doing the work of a measurement.

**(b) Every one is, by construction, a disagreement with the incumbent — and this is the strongest
result in the chain.** A bag *is* a situation; every member of a situation is in one component under
the incumbent; so a `marked × rest` pair is necessarily a pair **the incumbent joined and an operator
says it should not have**. v0.9.0 measured that the discriminating population was missing, because
the champion accepts 99.83 % of what it evaluates and rejections live in quiet traffic that attracts
no labels. **The exclusion set manufactures the discriminating population directly**, out of the one
place it can be found: the operator's disagreement. That is the justification for the whole v0.9.1 →
v0.9.2 → v0.10.0 chain, and it should be stated in the build report in these terms.

**(c) They can be satisfied TRIVIALLY where coverage is thin, and this is measured.**
`preview.partition` selects candidates through `correlate.select_candidates` — a pair never selected
gets no link and separates unless connected transitively. Per-label coverage on the fullest corpus is
**full 25, partial 6, none 6, empty 4** of 41. A bag with `coverage = 'none'` yields an all-singleton
partition, in which **every asserted negative pair is satisfied for free**. That is v0.9.0's measured
policy-B failure — a perfect number produced by nothing happening — appearing in a new place.

> **Registered:** bags with `coverage IN ('none', 'empty')` are **excluded** from `asserting_bags` and
> from every asserted-negative metric, **counted**, and reported. `coverage = 'partial'` bags
> contribute, with their partial share reported beside the metric.

**(d) Pooling them across bags puts the design effect back into the METRIC.** One 501-member storm
with 250 marks contributes 62 750 pairs; a pooled rate would be that storm's rate wearing the
corpus's name.

> **Registered — `asserted_negative_respected_rate`:** computed **per bag** as the fraction of that
> bag's observable asserted negative pairs the challenger's partition places in **different**
> components; aggregated as the **mean over bags**, with a cluster bootstrap over **incidents**.
> Never pooled over pairs. Reported conditioned on coverage and on the three scope populations, and
> computed for the **champion** the same way, because a challenger number with no champion number
> beside it is not a comparison.
>
> **Never composed** with `over_merge_rate`, `under_merge_rate` or `split_bag_intact_rate`. It is a
> fourth named quantity for the same reason the third exists.

## 3. DECISION 2 — grouped CV to estimate, a sealed holdout to decide

### 3.1 What this sample size can and cannot do

Cluster bootstrap over incidents, 2 000 resamples, rate 0.70:

| incidents | 12 | **37** | 50 | 100 | 500 |
|---|---:|---:|---:|---:|---:|
| median 95 % interval width | 0.500 | **0.289** | 0.246 | 0.180 | 0.079 |

Minimum true difference detectable at 80 % power:

| incidents | 12 | **37** | 120 | 300 |
|---|---:|---:|---:|---:|
| minimum detectable difference | 42 p.p. | **25 p.p.** | 16 p.p. | 10 p.p. |

> **At 37 incidents the judge cannot decide anything about model quality.** No plausible pair of
> scorers over the same three features differs by a quarter.

### 3.2 What is registered

> **Grouped repeated CV over merge-aware incidents is the ESTIMATOR.** An incident is wholly within
> one fold. Folds, repetitions and seed fixed here and not varied afterwards. Every rate carries a
> cluster bootstrap over incidents. **This is the only number any tuning loop, any release, or any
> human may look at while making a modelling choice.**
>
> **The sealed holdout is the DECIDER, and v0.10.0 CONSTRUCTS IT AND DOES NOT SPEND IT.**

**Why not spend it.** Reserving later is impossible; spending later is always possible. At 37
incidents, opening the envelope buys a number with a ±14.5 p.p. interval and a 25 p.p. detection
threshold, and costs the one-shot property permanently. The asymmetry is the argument, and it is the
same one §1 uses for floors.

**Both admissible splits are still implemented and both reported** — by time and by incident. The
sealed set is the combination; the by-incident split is exercised through CV.

### 3.3 Construction of the seal, fixed here so it cannot be chosen later to suit a result

1. every labelled bag maps to its merge-aware incident, resolved by **following `merged_into`
   transitively to a fixed point, with a cycle guard** — a single `COALESCE` is one hop and is not
   enough (`HONEST-JUDGE-0.10-DRAFT.md` §2);
2. incidents ordered by the timestamp of their **earliest** label, ties broken by incident id;
3. the **last third** by that order is sealed;
4. the seal is an explicit immutable list of incident ids with a SHA-256, written once.

**Pre-v0.8.0 merges are counted, never assumed absent.**

## 4. DECISION 3 — protecting the holdout from contamination

### 4.1 The hazard, quantified

Every candidate equally good; the entire reported-minus-true gap is selection noise:

| incidents | queries | median inflation | p90 |
|---:|---:|---:|---:|
| 12 | 4 | **+13.3 p.p.** | +21.7 |
| **37** | **12** | **+11.1 p.p.** | **+16.5** |
| 120 | 12 | +6.7 p.p. | +10.0 |

Four releases tuning against one set is the middle row. **An eleven-point improvement produced
entirely by looking is larger than any improvement this project is likely to produce by learning.**

### 4.2 The mechanism considered and rejected, with its arithmetic

The reusable-holdout line (Dwork et al. 2015) and the Ladder (Blum & Hardt 2015) exist for exactly
this. **Measured at this `n`, the Ladder does not work.** With 37 incidents and 12 queries:

| η | releases per run, no real gain | median inflation |
|---:|---:|---:|
| 0.10 | 1.60 | +8.4 p.p. |
| 0.20 | 1.15 | +3.0 p.p. |
| 0.30 | 1.02 | +0.3 p.p. |

To control the inflation, η must approach the 25 p.p. detection threshold — and at η = 0.30 a model
genuinely improving 2 p.p. per query fires **1.22 times per run**. The mechanism cannot respond to a
real improvement at this sample size. **Adopting it would be decoration.** The rejection is recorded
with its arithmetic so a later release with a larger corpus can revisit rather than re-derive.

### 4.3 What is registered

> 1. **The seal is structural.** The sealed incident ids live in their own store table; the CV
>    estimator, the training path and every report cannot read them — asserted by a test that injects
>    a read and observes it refused, not by convention.
> 2. **Every access is a row, not prose.** Append-only: which release, which pre-registration hash,
>    what was asked, what was returned.
> 3. **Reading the seal requires a ratified pre-registration.** The access path refuses unless the
>    requesting release's plan hash is already recorded. A release cannot look first and register
>    after.
> 4. **Every holdout number ever printed carries its query count**, so a reader can apply §4.1
>    without being told to.
> 5. **v0.10.0's query count is 0**, asserted by test, and stated as the release's headline discipline
>    rather than as a footnote.

## 5. The metrics

Inherited unchanged from `HONEST-JUDGE-0.10-DRAFT.md` §3 and `PREREGISTRATION-0.9.0.md` §4:
`over_merge_rate`, `under_merge_rate`, `split_bag_intact_rate` — **three numbers, never composed** —
plus calibration at bag level, plus the champion measured the same way.

Added by this plan: **`asserted_negative_respected_rate`** (§2.6(d)), the **fourth** named quantity,
also never composed.

**Not adopted, and named so it is not re-derived:** the entity-resolution family (B-Cubed, MUC, CEAF,
pairwise F, ARI, NMI, VI). These produce a single scalar summary of divergence between clusterings
and do not reveal whether a difference came from merges, splits or reorganisations — which is
precisely what over-merge and under-merge are separated to expose. The refusal to compose is a
decision, and adopting a composite index would reverse it under a different name.

## 6. DECISION 4 — `INSUFFICIENT_EVIDENCE` as a first-class terminal state

### 6.1 Why

`training.Sufficiency` is a two-valued verdict about the corpus. The **judge's** verdict is a
different object and must not be two-valued: *"the challenger is not better"* and *"this corpus
cannot tell"* are opposite claims a binary type collapses into one. That collapse is how a v0.11.0
promotion gate would come to treat *no evidence* as *evidence of no difference*.

### 6.2 What is registered

> The judge returns exactly one of three values, and the type makes the third unavoidable:
>
> * **`BETTER`** — every §2 floor met; the holdout spent under §4; the interval on the difference
>   excludes zero on **both** `over_merge_rate` and `under_merge_rate`; `split_bag_intact_rate` and
>   `asserted_negative_respected_rate` reported and not composed.
> * **`NOT_BETTER`** — every floor met, the holdout spent, and the interval contains zero or favours
>   the champion.
> * **`INSUFFICIENT_EVIDENCE`** — **any** of:
>   * a §2 floor unmet;
>   * the sealed holdout not spent;
>   * fewer than 10 incidents on either side of any reported split;
>   * **the minimum detectable difference at the available `n` exceeds the observed difference**
>     (§2.5 — the power condition, which is a trigger and not a floor);
>   * bags excluded for `coverage IN ('none','empty')` (§2.6(c)) numerous enough that including them
>     would change any floor evaluation;
>   * `unknown`-population rows numerous enough that their exclusion would change the verdict;
>   * truncated bags load-bearing for a floor (§2.4).
>
> **`INSUFFICIENT_EVIDENCE` is terminal within its release.** No later analysis in the same release
> may convert it into either other value. It is not an error, not a failure and not an absence — it is
> a measurement of the corpus, reported with §6.3's projection.
>
> **v0.11.0 must refuse to promote on `INSUFFICIENT_EVIDENCE` and on `NOT_BETTER` by different code
> paths with different messages**, so no future reader can mistake one for the other. Specified here;
> implemented there.

### 6.3 The projection, and its honest limit

v0.9.0 §5.6's arithmetic, in months, `undefined` where the span is zero or fewer than two labels
exist. **And it will be `undefined`**: this repository has no data on the labelling rate at all — the
harness applies 41 verdicts one second apart, and every closed situation was closed by the idle
sweep. v0.10.0 prints `undefined` rather than the harness's rate, and says the number is one only a
deployment can produce.

## 7. What will be concluded under each outcome

**7.1 — Any §2 floor unmet.** *The expected branch, and §0 declares it was expected.* Verdict
`INSUFFICIENT_EVIDENCE`. The judge is built, demonstrated against a purpose-built fixture, and **not
run against the corpus for a quality claim**. The seal is constructed and not spent. The build report
opens with: *the corpus cannot support an evaluation, the judge exists and is demonstrated, the seal
is intact at query count 0, and here is what would have to change.* **This is a successful release.**

**7.2 — Floors met but `asserting_incidents` < 30** while `asserting_bags` ≥ 50. Verdict
`INSUFFICIENT_EVIDENCE`; the concentration is named. The conjunction of §2.2 exists for this branch.

**7.3 — Floors met; power condition fails** (§2.5). Verdict `INSUFFICIENT_EVIDENCE`, and the report
states the detection threshold beside the floor evaluation. **This is the branch §2.5 was written
for**, and reaching it is the normative floors working as designed rather than failing.

**7.4 — Floors and power met; the holdout spent; interval excludes zero on both rates.** Verdict
`BETTER`. **No promotion** — that is v0.11.0's. Interval and query count quoted with every statement.

**7.5 — Better on one rate, worse on the other.** Verdict `NOT_BETTER`. Over-merge and under-merge
are different failures with different costs, this project has not measured those costs, and no
composite is computed. Both numbers reported.

**7.6 — `split_bag_intact_rate` high while both headline rates look good.** The model buries splits:
v0.9.0's measured policy-B failure repeating. Verdict `NOT_BETTER` regardless of the other numbers,
and the report says which number produced the verdict.

**7.7 — `asserted_negative_respected_rate` near 1.0 while `under_merge_rate` is also high.** The
challenger is separating everything, so it respects every assertion for free. Verdict `NOT_BETTER`,
and the report states that the two numbers must be read together.

**7.8 — CV and the sealed holdout are ever compared and disagree by more than the CV interval.**
Verdict `INSUFFICIENT_EVIDENCE`; the disagreement is raised as a finding, never averaged.

**7.9 — The merge chain contains a cycle** or fails to reach a fixed point. Affected incidents
excluded, counted, reported as `unknown`; verdict `INSUFFICIENT_EVIDENCE` if their exclusion would
change any floor evaluation.

**7.10 — Pre-v0.8.0 merges exceed 10 % of `asserting_incidents`.** Verdict `INSUFFICIENT_EVIDENCE`.

## 8. Stopping rules

* **The sealed holdout is not read in v0.10.0.** Query count 0, asserted by test.
* **One CV run at the configuration fixed in §3.2.** No re-running with different folds, seeds,
  feature sets or thresholds after seeing a metric.
* **No metric is added after a result is seen.** A quantity not named in §5 may be measured and
  reported under *"additional observations"* and may never support a conclusion in §7.
* **No floor is changed**, and **the power condition is not converted into a floor**.
* **`incumbent_linked` is not relaxed**, including as a feature.
* **This file is not edited** once ratified.

## 9. Where a disagreement with this plan goes

Into `../security/SECURITY-REVIEW-0.10.0.md` §critical-analysis, as an opinion **for v0.11.0**, with
the data that produced it — never as a change here. The reviewer of a pre-registered plan is entitled
to say *"floor X was wrong"* after seeing the data. They are not entitled to say it by editing the
plan.

## 10. Prerequisites of ratification — all three blocking, none large

### 10.1 F48 — the `source = 'server'` predicate, demonstrated

The predicate appears in three places: the live path (via `Exclusion.marked_positions`), migration
`0011`'s backfill, and `Store.reconciliation_drift`. **Only the live path is adversarially tested.**
Removing it from either of the other two leaves **1086 tests green**, verified. The case is a stale
client view — which the v0.7.5 SSE teardown produced routinely — and it is **verified non-equivalent**:
the live path records `excluded_reconciled = 0` while an unfiltered join records 1.

Ship `tests/test_evidence_boundary_f48.py`, whose three tests each carry a control and each go **red**
under the corresponding injection. Issue as **F48** in `SECURITY-REVIEW-0.9.2.md`, whose §3 already
reserves the number.

### 10.2 `EVIDENCE-BOUNDARY-0.9.2.md` §10 corrected

The observable-pair interval is spurious: `h − b` is exactly the hidden count in the remainder, so
the lower expression is **exact**. The cited case evaluates to `[2, 4]`, not `[2, 2]`. Replace the
interval with the exact expression, note that it is exact rather than a bound, and mirror the
correction in `docs/ROADMAP.md`, which repeats it. §2.4 of this plan depends on it.

### 10.3 The remote tags recreated

`git ls-remote --tags origin` returns only `v0.7.3`. `v0.8.0`, `v0.8.1`, `v0.9.0`, `v0.9.1` and
`v0.9.2` have no immutable reference on the remote. A plan whose guard is a content hash is weakened
by a repository whose releases are not addressable.

## 11. Versioning and immutability of Gate 0

1. This file lands at `docs/analysis/PREREGISTRATION-0.10.0.md` in a commit that changes **nothing
   else**, so the ratified content is a single addressable object.
2. Its SHA-256 is recorded in `docs/gates/v0.10.0-phase-0.md` and pinned by
   `tests/test_preregistration.py` beside v0.9.0's — **both**, because v0.9.0's plan governs a corpus
   this one still reads.
3. An annotated tag `v0.10.0-gate0` is created on that commit **and pushed**, so the ratified plan is
   addressable independently of any later branch or rebase.
4. **No v0.10.0 code is written before steps 1–3 complete.** The first implementation commit's
   message cites the plan's hash.
