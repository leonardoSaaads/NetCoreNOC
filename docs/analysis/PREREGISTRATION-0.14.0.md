# Pre-registered analysis plan — v0.14.0 (the model family)

<!-- release-claim: v0.14.0 = model-family -->

**STATUS: RATIFIED — 2026-08-23.** This document becomes the pre-registration the moment it is
ratified. From that instant it is immutable: its SHA-256 is recorded in
`docs/gates/v0.14.0-phase-0.md` and asserted by `tests/test_preregistration.py`, so an edit made after
seeing a result turns the suite red.

**Ratify before any v0.14.0 code is written.**

Its parents are [`PREREGISTRATION-0.10.0.md`](PREREGISTRATION-0.10.0.md), whose floors, metric set,
verdict states and contamination protections this plan inherits **without amendment**, and
[`PREREGISTRATION-0.11.0.md`](PREREGISTRATION-0.11.0.md), whose degeneracy discipline this plan
extends to three new kinds.

## 0. What was known when this was written, declared so the choices can be audited

**Known — the corpus.** `asserting_bags = 0`, `asserting_incidents = 0`,
`asserted_negative_pairs = 0`. Every release since v0.9.1 has returned `INSUFFICIENT_EVIDENCE`. The
champion accepts 99.83 % of the pairs it evaluates; of ten eval scenarios, only `dual_incident` (120
pairs) and `fiber_cut` (28 pairs) contain both classes.

**Known — the statistics of this `n`.** A cluster bootstrap over 37 incidents gives a 95 % interval
0.289 wide; the minimum difference detectable at 80 % power is 25 percentage points; adaptive
selection over 12 queries on 37 incidents inflates a reported rate by a median +11.1 p.p. when every
candidate is equally good.

**Known — the degeneracy failure.** An all-zero logistic coefficient vector raises nothing, returns
a finite score, sums its contributions correctly, returns `linked=False` for every pair, forms no
situations, and is the **fastest** model available. This was measured before this plan was drafted.

**Known — the feature vector has three positions.** `("decay", "class_affinity",
"entity_affinity")`. Exact Shapley by enumeration is therefore `2³ = 8` evaluations.

**NOT known.** No tree, forest or boosted model has been fitted against any split. No attribution has
been computed. No simulated corpus exists. **Every threshold below is chosen from the corpus census,
from the arithmetic of the feature space, and from the statistics of the sample size — never from a
result, because no result exists.**

## 1. The invariants this plan inherits without amendment

> **No metric that decides promotion may be computed against `incumbent_linked`.**

> **The simulation's ground truth is subject to the same prohibition, for the same reason.** The
> scenario DSL knows the correct `situation_key` of every event. A label the machine produced does not
> judge the machine, whether the machine is the champion or the generator. Ground truth may measure
> the **simulator**; it may not enter any quantity the promotion gate reads.

> **Pairwise accuracy is not reported as a quality metric, in any section, under any name.**

> **`resolved = the more demanding of (project floor, deployment policy)`.** Monotone toward
> evidence.

> **The sealed holdout is the decider and the grouped cross-validation estimate is the only number
> any tuning loop, any release, or any human may look at while making a modelling choice.**

## 2. DECISION 1 — the three kinds, and their degeneracy rules

The rules below are the analogue of `MIN_WEIGHT_SUM` and are registered **before any fit exists that
they could have been chosen to suit**. Each refuses its own case and no other.

### 2.1 `tree`

> **T1 — finiteness.** Every threshold and every leaf value is finite.
> **T2 — structure.** The node list is a well-formed binary tree: exactly one root, every interior
> node has two children, every child index is in range, no cycle, every leaf reachable.
> **T3 — feature range.** Every `feature_id` is in `[0, 3)`.
> **T4 — non-degeneracy of depth.** A tree of depth 0 — a single leaf — is refused: it returns the
> same score for every pair, which is the all-zero logistic in another shape.
> **T5 — reachability.** Under the feature bounds (`decay ∈ [exp(−WINDOW_S/TAU0_S), 1]`, both
> affinities in `[0, 1]`, imported from `correlate` and `challenger` rather than restated), at least
> one leaf is above the threshold and at least one is below. A tree whose every leaf is on one side
> of the threshold cannot discriminate.
> **T6 — leaf magnitude.** No leaf value exceeds the same magnitude bound `MAX_ABS_COEFFICIENT`
> serves for logistic, for the reason DECISIONS #164 gives: a value that saturates the decision on
> its own is a hard switch whose contribution still sums correctly while no longer meaning *"this
> much evidence"*.

### 2.2 `forest`

> Every member tree satisfies T1–T3 and T6. Plus:
> **F1 — the seed is present, an integer, and inside `params_document`.**
> **F2 — `n_estimators ≥ 2`.** A forest of one is a tree with a misleading name.
> **F3 — reachability of the aggregate.** T5 applied to the **averaged** output, not to any member.
> **F4 — non-identity.** Not every member tree is byte-identical to every other; a forest whose
> bagging drew the same sample every time is a tree that costs `n` times more.

### 2.3 `gradient_boosting`

> Every member tree satisfies T1–T3 and T6. Plus:
> **G1 — `learning_rate ∈ (0, 1]`.** Zero shrinkage makes every round a no-op after the first.
> **G2 — `n_rounds ≥ 1`.**
> **G3 — reachability of the additive sum**, T5 applied to the accumulated output.
> **G4 — the base score is present and finite.**

### 2.4 What is deliberately not registered

No rule constrains **accuracy**, **fit quality**, or **agreement with the champion**. Degeneracy is
about whether a model can decide at all; whether it decides *well* is the judge's question and this
plan does not let a validator pre-empt it.

## 3. DECISION 2 — the attribution method

> **Registered: exact marginal (interventional) Shapley values over the three features, computed by
> enumeration of all `2³ = 8` coalitions, against a fixed background set.**

**The background set is registered here and is not varied afterwards:** the feature vectors of the
`MAX_CANDIDATES`-bounded evaluation set of the fixed eval corpus, deduplicated and sorted, from which
a deterministic sample of at most 256 rows is drawn by taking every `k`-th row. The base value is the
model's mean output over that background set.

**Why marginal rather than path-dependent.** Path-dependent TreeSHAP is a biased estimator of the
conditional values, and two statistically similar trees computing the *same function* can receive
different feature rankings under it, while their marginal values coincide. This product's contract is
*"why did the system group these alarms"*; an attribution that can rank features differently for two
models that decide identically would make that answer an artefact of tree structure. Marginal values
are exact here and cost eight evaluations.

**The consequences, registered so they are not discovered:**

1. Shapley values sum to `score − base_value`, not to `score`. The base value is a property of
   (model, background set), both fixed at registration, and therefore lives in `params_document` and
   enters `params_hash`.
2. The explainability check in the admission filter becomes
   `sum(contributions) + base_value == score`.
3. `TermContribution.weight` is meaningless for a Shapley attribution. The `LinkScore` gains an
   **optional** field naming the basis of its terms, which DECISIONS #49 makes a **minor** contract
   bump. A field named `weight` carrying something that is not a weight is not an option.
4. The three contributions project onto `link.term_t / term_a / term_e` unchanged, because the
   feature set is unchanged. **No migration follows from attribution.**

## 4. DECISION 3 — the lower bound is discrimination, not time

### 4.1 Why not time

`shadow_admission.py` records that the champion's own p99 moved **2.6×** between two runs on one
machine, which is why the upper bound is a ratio rather than an absolute. A **lower** bound in wall
clock would be a floor on a quantity measured to be unstable by that factor: a correct model on an
idle machine would be refused for being fast, and a broken model under load would pass.

And the failure a lower bound is meant to catch is measured to be invisible to the clock: the
all-zero model is the **fastest** model available (§0).

### 4.2 What is registered

> **The discrimination floor.** Over a fixed probe set — the same background set as §3, in the same
> order — a scorer is admitted only if **both**:
>
> * **spread**: the standard deviation of its scores over the probe set exceeds
>   `MIN_SCORE_SPREAD = 0.01`, which is `scoring.MIN_THRESHOLD`'s magnitude and is chosen for
>   consistency with it rather than from any observed distribution;
> * **decision**: it returns `linked = True` for at least one probe and `linked = False` for at least
>   one probe.
>
> A scorer failing either does not compete, whatever its speed, and the reason names which half
> failed.
>
> **The floor is hardening-only.** A deployment may raise `MIN_SCORE_SPREAD` and may never lower it,
> and may never disable the decision half.
>
> **The champion is measured against the same floor and its numbers are published as the reference.**
>
> **An optional wall-clock lower bound exists as a mechanism-class setting with a default of zero.**
> It is a proxy, the surface says so, and it may never be the only lower bound in effect.

### 4.3 What this is for, stated so v0.15.0 inherits it correctly

For an in-process kind, §2's rules inspect the parameters directly. **For a model whose parameters
cannot be inspected — v0.15.0's cartridge — a behavioural floor is the only form
threshold-reachability can take.** This floor is written to be that form, and v0.15.0 must not write a
second one.

## 5. DECISION 4 — the simulated corpus, its shape, and the stopping rule

**The whole of this section is fixed before any corpus is generated and any verdict is seen.**

### 5.1 The shape

> The generator produces, in fixed proportion:
>
> | Shape | Share of incidents |
> |---|---:|
> | independent faults overlapping within `TAU_S` on low-affinity NEs | 30 % |
> | a single fault spread beyond `TAU_S` on one NE | 20 % |
> | a mass storm concealing a simultaneous unrelated fault | 15 % |
> | a flapping port during a real incident | 15 % |
> | a situation merge chain of length ≥ 2 | 10 % |
> | quiet background noise producing no situation | 10 % |
>
> Devices, classes, entity keys, timing and decoy varbinds are drawn by the existing DSL from a
> **fixed seed recorded in the gate**. Two generations from the same seed are byte-identical.

### 5.2 The labelling

> Labels are applied through `POST /api/situations/{sid}/feedback`, the route the console calls, by
> **three distinct principals**, none contributing more than 60 % of bags — the operator-concentration
> floor of `PREREGISTRATION-0.9.0.md`, satisfied by construction rather than by luck.
>
> **A label's content is a decision function of the generator's ground truth and is recorded as
> such.** It is a *simulated operator*, and the report never calls it an operator.

### 5.3 The stopping rule

> The loop generates in fixed increments. After each increment it computes the census and reports,
> per unmet floor, the shortfall. **It stops when either:**
>
> * every floor of `PREREGISTRATION-0.10.0.md` §2.2 is met and the judge has returned a verdict; or
> * **ten increments have been generated without every floor being met**, in which case the release
>   reports the shortfall and the demonstration is recorded as incomplete.
>
> **The second branch is a successful gate outcome and the report says so.** A loop that cannot stop
> without success is a loop that will manufacture one.

### 5.4 What may and may not be changed after a verdict is seen

> **The generator's shape (§5.1), its proportions, its seed, the labelling rule (§5.2) and the
> increment size are fixed here and are not changed after any verdict is observed.** The loop may
> generate **more** data of the registered shape. It may not change the shape, the proportions, the
> seed, or the labelling rule.
>
> Changing any of them after seeing a verdict is adaptive selection with the data-generating process
> as the knob. It is worse than tuning the model, because the model's tuning is recorded in
> `params_document` and this would be recorded nowhere.
>
> If the build concludes that the registered shape is wrong, that conclusion goes into the security
> review as an opinion **for v0.15.0**, and the demonstration is reported against the registered
> shape whatever it produced.

## 6. DECISION 5 — the demonstration is not a claim

> **Every verdict, every metric and every promotion produced from the simulated corpus is a
> demonstration of the machinery and is never a claim about model quality.**
>
> Enforced three ways:
>
> 1. **A separate database.** The demonstration runs against its own SQLite file. No synthetic row
>    reaches a fixture the suite reads or a corpus the repository ships, and the production tree's
>    seal query count remains 0, asserted by test.
> 2. **The report's own words.** `BUILD-REPORT-0.14.0.md` states, in the paragraph that carries the
>    verdict, that the corpus was generated by this release. A reader who reads only the headline must
>    not come away believing a real network was measured.
> 3. **The ground-truth prohibition of §1.** The generator's `situation_key` is unreachable from every
>    quantity the promotion gate reads, asserted by a test that injects a read and observes it
>    refused.

## 7. DECISION 6 — choosing among kinds is a query

> **Each comparison between model kinds against the cross-validation estimate is one query, and the
> count is recorded and displayed beside every figure the comparison consumed.**
>
> §4.1 of `PREREGISTRATION-0.10.0.md` measured the inflation this controls. Four kinds compared once
> each is four queries; a hyperparameter sweep is one query per configuration evaluated.
>
> **The sealed holdout is not consulted for kind selection**, in this release or any other. Selection
> is a cross-validation activity by construction.

## 8. What will be concluded under each outcome

**8.1 — The simulated corpus clears every floor and the judge returns `BETTER` for at least one
kind.** The machinery is demonstrated end to end for the first time in the project's history. The
seal is spent, once, and the access row is quoted. **No claim is made about which model is better on
a real network**, because no real network was measured. This is the expected branch and it is a
successful release.

**8.2 — The corpus clears every floor and the judge returns `NOT_BETTER`.** Equally successful. The
gate was exercised, both refusal paths of v0.11.0 are reachable, and the release reports which number
produced the verdict.

**8.3 — Ten increments without every floor met (§5.3).** The demonstration is incomplete, the
shortfall is reported per floor, and the release ships the three kinds without the end-to-end proof.
**This is a gate outcome, not a failure**, and the report leads with it rather than burying it.

**8.4 — A kind fails the discrimination floor after a real fit.** Reported as a property of that
kind on that corpus, with the numbers. The floor is not lowered.

**8.5 — Two kinds produce identical verdicts and identical intervals.** The corpus is not
distinguishing them; report it, and do not present the pair as a comparison.

**8.6 — Attribution and score disagree** — the contributions plus the base value do not equal the
score for any kind on any input. **The kind does not ship.** Explainability is contractual and a
kind that cannot decompose its own decision is not a scorer this project runs.

**8.7 — The champion changes and subsequent situations do not carry the new provenance.** A defect in
the activation path, reported as a finding, and the demonstration is not claimed complete.

## 9. Stopping rules

* **The seal is spent at most once, on the demonstration database, and never on the production
  tree.**
* **One generation at the configuration fixed in §5.1.** No re-generation with a different seed,
  different proportions or a different labelling rule after seeing a metric.
* **No metric is added after a result is seen.** A quantity not in
  `PREREGISTRATION-0.10.0.md` §5 may be measured and reported under *"additional observations"* and
  may never support a conclusion in §8.
* **No floor is changed**, and the discrimination floor is not converted into a wall-clock floor.
* **`incumbent_linked` is not relaxed, and neither is the synthetic ground truth.**
* **This file is not edited** once ratified.

## 10. Where a disagreement with this plan goes

Into `../security/SECURITY-REVIEW-0.14.0.md`'s critical analysis, as an opinion **for v0.15.0**, with
the data that produced it — never as a change here. The reviewer of a pre-registered plan is entitled
to say *"rule T5 was wrong"* after seeing the data. They are not entitled to say it by editing the
plan.

## 11. Versioning and immutability of Gate 0

1. This file lands at `docs/analysis/PREREGISTRATION-0.14.0.md` in a commit that changes **nothing
   else**, so the ratified content is a single addressable object.
2. Its SHA-256 is recorded in `docs/gates/v0.14.0-phase-0.md` and pinned by
   `tests/test_preregistration.py` **beside v0.9.0's, v0.10.0's and v0.11.0's** — four, because all
   four govern corpora this release reads or produces.
3. An annotated tag `v0.14.0-gate0` is created on that commit, and recorded in `TAG-RECOVERY.md`.
4. **No v0.14.0 code is written before steps 1–3 complete.** The first implementation commit's
   message cites the plan's hash.
