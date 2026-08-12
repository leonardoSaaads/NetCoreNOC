# SCOPE — v0.10.0

**The honest judge: an evaluation whose verdict cannot be produced by the thing being evaluated, and
a holdout that is built and deliberately not spent.**

This release does **not** produce a better model. It produces the machinery that could one day tell
whether a model is better, plus a verdict — and the verdict this corpus returns is
`INSUFFICIENT_EVIDENCE`. **That is the pre-registered expected outcome and it is a successful
release.** [`../analysis/PREREGISTRATION-0.10.0.md`](../analysis/PREREGISTRATION-0.10.0.md) §7.1 says
so in advance, before any result existed, which is the entire point of having written it first.

Binding authorities, in the order they win: **the ratified pre-registration** on every analytical
question; this document on **scope**;
[`../architecture/MODULE-ARCHITECTURE.md`](../architecture/MODULE-ARCHITECTURE.md) on **where code
goes**; [`../architecture/ROADMAP-0.8-TO-0.13.md`](../architecture/ROADMAP-0.8-TO-0.13.md) on
**sequence**; [`../security/threat-model.md`](../security/threat-model.md) on **security posture**;
the build prompt on process and quality.

> **Where the build prompt and the plan appear to disagree, the plan wins**, and the apparent
> disagreement is a defect reported in [`../gates/v0.10.0-phase-1.md`](../gates/v0.10.0-phase-1.md)
> §6 rather than resolved silently. A paraphrase that drifts becomes a second source of truth, and
> this repository has been bitten by that twice.

---

## 0. Why this release exists, in three measured facts

All three reproduced by execution in [`../gates/v0.10.0-phase-1.md`](../gates/v0.10.0-phase-1.md),
each with a control that had to behave the other way.

**Fact 1 — the sample size cannot decide.** A cluster bootstrap over the corpus's 37 merge-aware
incidents gives a 95 % interval **0.297 wide** (plan: 0.289). The minimum detectable difference at
80 % power is **29.8 p.p. by a documented closed form and 33 p.p. by direct Monte-Carlo** — the plan
registers 25, and Gate 1 §2 reports that disagreement rather than resolving it. No plausible pair of
scorers over the same three features differs by a quarter, still less by a third.

**Fact 2 — looking repeatedly is worth more than learning.** Adaptive selection over 12 queries on
37 incidents inflates a reported rate by a **median +11.1 p.p. when every candidate is equally
good** — reproduced to three decimal places on all six figures of the plan's table. Four releases
(v0.10.0 → v0.13.0) tuning against one holdout is exactly that scenario. **This is why the seal is
built and not spent.**

**Fact 3 — the corpus supplies zero asserted negative pairs**, and ten of its forty-one labelled
bags are excluded before anything else begins (`coverage` in `none`/`empty`). The judge therefore
**cannot be exercised on the corpus** and is demonstrated on purpose-built fixtures — exactly as
v0.9.1's exclusion set had to be.

**A fourth fact this phase added, which the brief did not anticipate:** every merge chain in the
corpus is **one hop**, so `COALESCE` and a transitive resolution both return 37 incidents. The
incident-identity guard cannot be demonstrated on the corpus either.

---

## 1. In scope — seven workstreams

### W1 — incident identity, and the population census

Transitive resolution of `situation.merged_into` to a fixed point, with a **cycle guard** and a
**non-termination guard**, both *reported* rather than silently collapsed. **One function, one
implementation**: two call sites computing incident identity separately is how the estimator and the
seal come to disagree about which incidents exist, without anything going red.

The census — merge-aware incidents **and the reduction from the one-hop count**, per-label coverage
four ways with the §2.6(c) exclusion counted, the three scope populations never averaged,
`asserting_bags` / `asserting_incidents` / `asserted_negative_pairs` with concentration, and
pre-v0.8.0 merges **counted, never assumed absent**.

### W2 — the sealed holdout

Migration **`0012`**, additive, forward-only, carrying the seal and its access log. Construction
exactly as plan §3.3 fixes it. Four properties, each structural rather than conventional:

1. constructed **once**; a second construction is **refused, not silently re-derived**;
2. **structurally unreadable from the estimator** — designed before the estimator, because
   retrofitting is how it becomes a convention;
3. every read appends **exactly one** access-log row;
4. a read **requires a ratified plan hash already on record** — a release cannot look first and
   register after.

**v0.10.0's query count is 0**, asserted by test. The release's headline discipline, not a footnote.

### W3 — the estimator

Grouped repeated cross-validation over merge-aware incidents; an incident is **wholly within one
fold**. Folds, repetitions and seed fixed by plan §3.2 and not varied afterwards. Every rate carries
a cluster bootstrap over incidents.

The **power condition** — the minimum detectable difference at the observed `n` — computed by a
documented closed form, validated against the plan's table, and **emitted together with every floor
evaluation**, asserted by a test that fails if a report prints one without the other.

### W4 — the metrics

`shadow_eval.py` is **split**, not exempted, and the split is recorded in an ADR. The metric set is
plan §5 and is not extended: four named quantities, **never composed**, plus calibration at bag
level. The fourth — **`asserted_negative_respected_rate`** — is per bag, aggregated as the **mean
over bags** with a cluster bootstrap over **incidents**, **never pooled over pairs**, excluding
`coverage IN ('none','empty')` bags (counted and reported), and computed for the **champion by the
same code path**.

### W5 — the verdict

A three-valued type — `BETTER`, `NOT_BETTER`, `INSUFFICIENT_EVIDENCE` — where **the type makes the
third value unavoidable**. Every trigger of plan §6.2 implemented and **each individually tested** by
a corpus that fires it and no other.

### W6 — the demonstrated-guard discipline

Inherited exactly from
[`../gates/v0.9.2-guard-demonstrations.md`](../gates/v0.9.2-guard-demonstrations.md). Every guard:
the exact defect injected as a diff, the command, the verbatim RED, the verbatim GREEN, and the
**control that had to pass in both runs**. A guard with no recorded red blocks the gate; a guard
whose control was absent blocks the gate. Plus a mutation ledger reporting **the survivor list, not
the ratio**.

### W7 — security review and the v0.11.0 specification

`SECURITY-REVIEW-0.10.0.md` continuing from **F49**, and
`CHAMPION-CHALLENGER-0.11-DRAFT.md` with every element tagged `v0.11.0: planned`, implementing
nothing.

---

## 2. The intentional behaviour changes, enumerated

**Any change not on this list is a defect in this build's work.**

1. **A new store table `holdout_seal`** and **a new store table `holdout_access`** exist after
   migration `0012`. Both empty on a fresh install until the seal is constructed.
2. **`dataset shadow` gains sections**: the merge-aware census with the one-hop reduction, the CV
   estimate with its cluster-bootstrap interval, the detection threshold beside every floor
   evaluation, the fourth metric for challenger **and champion**, and the three-valued verdict.
3. **A new CLI subcommand `dataset judge`** renders the verdict, the census and the seal's query
   count. Read-only, admin-only, aggregates only.
4. **The maintenance pass constructs the seal once**, off the batch lock, degrading to a warning on
   failure exactly as every other slow-loop step does.
5. **`shadow_eval.py` is split**, so two module names exist where one did. No metric changes value.

Nothing else. In particular: **no promotion mechanism, no pointer move, no new route, no new
capability, no UI change, no scorer change, no correlation change, no ingest change.**

---

## 3. Explicitly out of scope — deferred, with the reasoning

* **Promotion.** v0.11.0's, and this release must not make it easier by leaving a half-built one
  behind. A release that could promote would be judged by the only metric it had.
* **Spending the seal.** Plan §3.2 and §8. Reserving later is impossible; spending later is always
  possible.
* **Relaxing `incumbent_linked`.** Plan §1 and §8, unchanged, including as a feature.
* **A composite quality score**, and the entity-resolution family (B-Cubed, MUC, CEAF, pairwise F,
  ARI, NMI, VI). Named in the report so no later release re-derives them: they summarise clustering
  divergence in one scalar and cannot say whether a difference came from merges, splits or
  reorganisations, which is exactly what this project separates over-merge from under-merge to
  expose.
* **The Ladder / reusable holdout.** Plan §4.2 rejected it *with its arithmetic*, measured at this
  `n`: to control the inflation, η must approach the detection threshold, and at η = 0.30 a model
  genuinely improving 2 p.p. per query fires 1.02 times per run. Adopting it would be decoration.
* **Active learning**, per-archetype models (v0.12.0), the external cartridge (v0.13.0).
* **Correcting the plan's MDD table.** Gate 1 §2 reports the disagreement; plan §9 sends it to the
  security review as an opinion for v0.11.0. Editing a ratified plan is the one thing this
  discipline exists to prevent.
* **Guarding the migration files' SQL text (F49).** Revealed in Gate 0 §2.5, real, and a ROADMAP
  line rather than a fix inside a fenced gate.

---

## 4. What Gate 0 and Gate 1 measured that bounds this release's claims

* **The corpus meets four fewer floors than it needs**, so no quality claim is available and none is
  made. `asserting_bags` 0 of 50; `asserting_incidents` 0 of 30; `split` bags 13 of 50; mixed 5 of 20.
* **The judge cannot be exercised on the corpus.** Zero asserted negative pairs, and only **two**
  bags would assert anything even if the labelling rule marked everything it could.
* **The incident-identity guard cannot be exercised on the corpus.** Every merge chain is one hop.
* **The blind-fraction rule cannot be exercised on the corpus.** The `checked` population is empty:
  eight labels were written under a restricted scope and every one hid nothing.
* **The projection will be `undefined`**, and must print as `undefined`. The corpus applies 41
  verdicts over a 40-second span; printing a rate from that would be manufacturing.
* **The plan's detection threshold at `n = 37` does not reproduce.** Two independent methods say
  ~30 p.p., not 25. The direction strengthens the plan's conclusion.

**Everything in the four bullets above is demonstrated on a purpose-built fixture instead**, and the
build report says so in those terms rather than implying corpus evidence.

---

## 5. Hard constraints

1. **No new runtime dependency**, not even optional. Bootstrap resampling, closed-form power and
   cross-validation are arithmetic.
2. **Exactly one migration**, `0012`, additive, forward-only. No existing migration is touched.
3. **`make eval` byte-identical**; correlation, capture and ingest paths untouched; `engine.py`
   unchanged.
4. **No promotion mechanism, no new route, no new capability, no UI change.**
5. **No new abstractions**: one seal module, one estimator module, one verdict type, the existing
   report surfaces extended.
6. **No module over 400 lines**; `DEBT_ALLOWLIST` empty; `COHESION_EXEMPT` unchanged at one entry
   and 580. `shadow_eval.py` is **split**, not exempted.
7. **The evaluator is read-only over the corpus** — it writes the seal, the access log and its own
   report rows, and nothing else.
8. **The evaluator runs off the batch lock.** An evaluator failure degrades evaluation and surfaces
   as an operator warning; it never fails ingestion.
9. **Everything deterministic**: fixed fold assignment, fixed resample seeds, fixed row ordering,
   byte-identical across two runs and two processes.
10. **Neither pre-registration is edited after Phase 0**, and the guard covers both.
