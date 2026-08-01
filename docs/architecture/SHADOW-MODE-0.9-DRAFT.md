# Shadow mode — v0.9.0 draft (specification only, not implemented in v0.8.0)

<!-- release-claim: v0.9.0 = shadow-mode -->

**Implement none of this in v0.8.0.** Every element below is tagged **`v0.9.0: planned`**. v0.8.0
**trains nothing**, and a specification is not a licence to start early: the release that captures
the dataset must not also be the release that consumes it, or the schema gets bent toward whatever
the first model happened to want.

This document is written **from the schema v0.8.0 actually built**, not from the schema v0.8.0
intended to build. Where the two differ, the built one wins and the difference is recorded.

Its parent is [`FEEDBACK-DATASET-0.8-DRAFT.md`](FEEDBACK-DATASET-0.8-DRAFT.md), as corrected on
2026-08-01; the binding sequence is [`ROADMAP-0.8-TO-0.13.md`](ROADMAP-0.8-TO-0.13.md).

---

## 0. The invariant this release must not break (`v0.9.0: planned`)

Restated first, in full, because it is the one thing a shadow-mode release is structurally tempted
to violate — and it is addressed to **v0.10.0 and v0.11.0** as much as to v0.9.0:

> **No metric that decides promotion may be computed against `incumbent_linked`.**

`incumbent_linked` is the champion's decision at the instant of evaluation. It is a **legitimate
column** and v0.9.0 may use it freely as provenance, as context, as an input **feature**, and as the
basis of champion/challenger *comparison*. What it may never be is the **target a promotion
decision is scored against**, because a challenger judged by the champion can only converge on the
champion: the champion's performance becomes the challenger's ceiling, and the number looks
excellent all the way up.

The schema expresses this structurally — **`dataset_pair` has no target column**, the only label
lives in `feedback`, and reaching it requires the join. That friction is deliberate. A v0.9.0 author
writing a training loop must go and *get* the human label and cannot reach for the machine's by
autocomplete.

**Two measured facts that make this non-negotiable rather than merely prudent:**

1. The evaluated-and-rejected pairs are **0.17 %** of the eval corpus (194 341 evaluated, 194 002
   accepted, 339 rejected). The 17.7× amplification is `MAX_LINKS_PER_ALARM` truncating *accepted*
   links, not the scorer rejecting pairs.
2. The accept rate is **a property of the traffic**: 0 % on `background_noise`, 100 % on every storm
   scenario. A quantity that swings that far with the weather cannot be an evaluation basis.

> **The roadmap's v0.9.0 blurb asks whether a model "can reproduce the built-in scorer's decisions
> at all". Read that as a *feasibility* question — can anything be learned from this volume of data?
> — and never as a success criterion.** A challenger that reproduces the champion perfectly has
> demonstrated that the pipeline works and has demonstrated nothing whatever about whether it should
> be promoted.

---

## 1. What shadow mode consumes from this schema (`v0.9.0: planned`)

Everything below exists **today**, in a database an operator is already filling.

| Source | What it provides |
|---|---|
| `dataset_pair` (`lifecycle='dataset'`) | one row per **evaluated** pair belonging to a labelled situation — linked *and* rejected, before `MAX_LINKS_PER_ALARM` truncation |
| `delta_t_s`, `class_affinity`, `entity_affinity` | the feature **values** the scorer actually saw — not `weight × value`, which cannot be divided back out when a weight is zero |
| `a_epoch`, `e_epoch` | the decay clock, without which two `A` readings are not comparable |
| `incumbent_linked`, `storm`, `truncated` | the champion's decision, the window state, and whether the cap discarded an accepted link |
| `dataset_observation` | the immutable raw material — severity, entity, `alarm_count` at decision time, varbinds — that `alarm` overwrites on re-fire |
| `feedback` + `feedback_member` (`source='server'`) | **the label, and the bag it was about**, recoverable even after a merge |
| `feedback.coverage*` | whether the bag's pairs were fully, partly, or not at all captured |
| `feedback.scope_*` | whether the operator was looking at a partial view, and by how many members |
| `feedback.capture_provenance` | `legacy_capture` versus `current` |
| `capture_run` | the configuration, window bounds and retention policy in effect |
| `situation.merged_into` | the merge chain, so two labels on one incident are identifiable as one |

**The join that produces training rows is deliberately not one line**, and v0.9.0 should write it
once, in one place, with the coverage and provenance filters visible:

```sql
-- Sketch. The `capture_provenance` filter is the DEFAULT, not an option: legacy rows are of
-- unknown quality and are opt-IN (§5c of the parent document).
SELECT p.*, f.verdict, f.member_count, f.coverage, f.scope_restricted
FROM dataset_pair p
JOIN feedback_member m ON m.alarm_id IN (p.alarm_a, p.alarm_b) AND m.source = 'server'
JOIN feedback f        ON f.id = m.feedback_id
WHERE p.lifecycle = 'dataset'
  AND f.capture_provenance = 'current'
```

---

## 2. The label-derivation policy — v0.9.0's to **evaluate**, not to assume (`v0.9.0: planned`)

v0.8.0 left this open on purpose, and capturing the **bag** rather than derived pairs is what keeps
it open. This is the release that closes it, and the requirement is that it closes it **with a
comparison, not a preference**.

The problem class, named (parent §3.3a): this is **multiple-instance learning**.

| Verdict | Formally | What it licenses about the pairs |
|---|---|---|
| `confirm` | an **all-positive bag** | every pair in the bag is a positive |
| `split` | a bag with **at least one negative**, unspecified which | **nothing about any individual pair** |

### The candidate policies, and what each costs

| # | Policy | Cost |
|---|---|---|
| **A** | `confirm` → all pairs positive; `split` → all pairs negative | Maximum data, and it **fabricates negatives**: an operator splitting nine members usually means "these three do not belong with those six", not "all thirty-six pairs are wrong". The fabrication is invisible — every row looks like an observation. |
| **B** | `confirm` bags only; discard `split` entirely | Honest and throws away the minority class, which is the one any model will get wrong. |
| **C** | `confirm` → positives; `split` → a **cannot-link constraint** on the bag rather than pairwise labels | Correct formalism; needs a learner that accepts constraints, which logistic regression does not without modification. |
| **D** | Weight `confirm` by bag size (confirmation strength decays with size) and treat `split` as **C** | The most defensible, and the most machinery. |

**v0.9.0 must implement at least two and report both.** A release that picks one and reports its
number has assumed exactly what v0.8.0 refused to assume, one release later.

**The measurement that decides it** cannot be agreement with `incumbent_linked` (§0). It has to be
held-out **human** verdicts, at bag granularity — *"does the derived-label model predict the
operator's verdict on a bag it has not seen?"* — which is a small-*n* question and §4's problem.

---

## 3. Calibration — a metric v0.9.0 **owes** (`v0.9.0: planned`)

A challenger that emits a probability rather than a bare verdict can route what the operator is
asked. That is valuable, and it comes with an obligation:

> **Report reliability curves and Brier score or ECE.** A threshold on an uncalibrated score is
> meaningless.

A model that says "0.9" on cases it gets right 60 % of the time is not 90 % confident — it is wrong
about its own reliability, and every downstream decision that reads the number inherits the error.
**Calibration is not implied by accuracy**: a model can rank perfectly and be badly calibrated, which
is precisely the case where a threshold does the most damage.

### And the half that is a trap

> **Confidence may route what the operator is asked. It may never authorise autonomy.**

The long-term roadmap's autonomy trigger is **measured agreement with humans** — never the model's
self-reported certainty. The two are different quantities and the failure mode is silent: a
confidently wrong model is *more* dangerous than an uncertainly wrong one, because confidence is
exactly what a self-authorising system would gate on. Self-reported certainty is evidence about the
model's internal state, not about correctness, and the two diverge where it matters most.

---

## 4. Effective sample size, and which model families survive it (`v0.9.0: planned`)

**The constraint that should shape this release more than any other.**

> ***n* is the number of independent labelled bags. It is not the number of pairs.**

Pairs from one alarm share a side. Pairs from one situation are strongly correlated. Situations from
one operator share a criterion — and `situation.merged_into` means two situations may be one
incident. A dataset of "5 000 labelled pairs" may carry an effective *n* of **300**, and the bias
report states bags, operators and merge-aware incidents precisely so v0.9.0 cannot quote the wrong
one by accident.

### What that implies

* **Viable**: logistic regression on the three existing features, plus a small number of derived
  ones. It is the v0.6.0 scorer with learned weights, it inherits the per-term explainability
  contract for free, and it needs no new runtime dependency.
* **Marginal**: anything with more parameters than there are independent bags. At *n* in the
  hundreds, a gradient-boosted tree ensemble will fit the operators, not the network.
* **Out of the question at this volume**: anything with an embedding layer, and anything consuming
  an identifier as a feature — see "keys are not features" (parent §4a rule 2). `ne_id = 47` means
  "the forty-seventh NE this appliance ever saw"; a model that learns it has learned one customer's
  topology and **generalises to nobody**, while scoring extremely well on that customer's held-out
  data. That is what makes the mistake survive review.
* **Confidence intervals must be computed over bags**, with clustering by operator and by incident.
  A naive per-pair interval will be wrong by a factor nobody checks.

**A likely and legitimate v0.9.0 outcome is "there is not enough data yet".** The release should be
built so that reporting that honestly is a *success*, not a failure — because the alternative is a
model promoted on a number that was never sound.

---

## 5. The leakage vectors v0.9.0 inherits (`v0.9.0: planned`)

All four are from parent §3.5. Each has a column; none is fixed by having one.

| Vector | Column | What v0.9.0 must do |
|---|---|---|
| **Matrix epoch** — a label at *T* is inside the features at *T+1* | `a_epoch`, `e_epoch` vs `feedback.created_at` | Report performance **split by** whether the pair was evaluated before or after the first label. Note the mechanism: a label does **not** advance the epoch (F36) — the contamination travels through the **mass values**. |
| **Re-fire near-duplicates** | `dataset_observation.alarm_id` | Deduplicate, or weight, observations sharing an alarm id. They are the same deduplicated alarm at different counts, not independent samples. |
| **Situation lineage** | `situation.merged_into` | Never let two labels on one merge chain land on opposite sides of a split. |
| **Four releases tuning against one dataset** | *none* | v0.10.0's problem, and the honest record is that **no column measures it**. It is a process hazard: if v0.9.0–v0.12.0 each tune against the same held-out set, the fourth is reporting a number optimised against four times, by slow leakage through the researchers. No code change causes it and no test catches it. |

---

## 6. What shadow mode may and may not do (`v0.9.0: planned`)

**May.** Train offline in the slow loop; write its opinion to its own table; be compared against the
champion; report calibration, effective sample size and per-policy results; be switched off.

**May not.**

1. **Reach a situation, the UI, an operator, or `learn.penalize()`.** The built-in scorer decides
   everything. A challenger that can influence grouping is not in shadow mode.
2. **Train on the ingest path.** Prime directive 1 is unchanged and this release is the one that
   makes it tempting. Training runs off the batch lock or it does not run.
3. **Be promoted.** v0.9.0 has no promotion mechanism, deliberately — that is v0.11.0's, after
   v0.10.0 builds an evaluator worth trusting. A release that could promote would be judged by the
   only metric it had, which would be agreement with the champion (§0).
4. **Consume `legacy_capture` rows by default.** They are of *unknown quality*; including them is an
   explicit, conscious choice and must be reported separately when made.
5. **Introduce a dependency without arguing it.** Five runtime dependencies has been the standing
   constraint for eight releases; logistic regression over three features does not need `numpy`.

---

## 7. What v0.9.0 should expect to want changed in this schema (`v0.9.0: planned`)

Recorded so it reads as a deferral rather than a defect (`SECURITY-REVIEW-0.8.0.md` §9.3):

* **`dataset_observation.varbinds` is opaque JSON.** Querying it — "both sides carry an interface
  index", "same enterprise OID" — needs a full scan. The likely fix is an additive
  `observation_varbind(observation_id, oid, value)` child table or a generated column. **Additive,
  so nothing captured is lost**, which is the test the v0.8.0 decision had to pass.
* **`incumbent_linked` sits on the pair row**, which makes the "no target column" claim depend on a
  comment rather than on structure. A release that wants it structural would move it to a
  `champion_decision` side table.
* **The sink's row cap, not its 21-day window, is what governs** (`…phase-6.md` §4), so the corpus
  is biased toward **quickly-labelled** situations. v0.9.0 has the latency data to retune it and
  should say what the right number is even if it does not change it.

---

## 8. Explicitly not in v0.9.0

1. **Promotion, or any path by which a model's opinion reaches an operator.** v0.11.0.
2. **The train/test split.** v0.10.0's; v0.9.0 may hold out data to report a number, and must not
   claim that is the split.
3. **Active learning.** The `acquisition_channel` column exists for it and v0.9.0 still writes only
   `organic`. Soliciting labels changes the distribution, and doing it before the bias of the
   organic population is understood destroys the baseline it would be measured against.
4. **The partial-split affordance.** Still the single highest-leverage UI change for the whole ML
   roadmap (parent §3.3a), and still a UI release's to make.
5. **Per-archetype models.** v0.12.0.
