# Archetypes — v0.12.0 draft (specification only, not implemented in v0.11.0)

<!-- release-claim: v0.12.0 = archetypes -->

**Implement none of this in v0.11.0.** Every element below is tagged **`v0.12.0: planned`**.

Written **from what v0.11.0 measured**, not from what it hoped. Where the two differ, the measurement
wins and the difference is recorded.

Its parent is [`ROADMAP-0.8-TO-0.13.md`](ROADMAP-0.8-TO-0.13.md); the evidentiary discipline it
inherits is [`../analysis/PREREGISTRATION-0.11.0.md`](../analysis/PREREGISTRATION-0.11.0.md); the
lineage problem it must not make worse is [`DATA-LINEAGE.md`](DATA-LINEAGE.md) §4.

---

## 0. The uncomfortable fact this draft must start from (`v0.12.0: planned`)

**v0.11.0 built a promotion gate and it refused, on a corpus with `asserting_bags = 0` against a
floor of 50.** Per-archetype weights mean **one model per archetype**, which means **splitting an
already-insufficient corpus `k` ways**.

> **If a corpus cannot decide one comparison, it cannot decide `k` of them, and dividing it makes
> every arm worse.**

So the first thing v0.12.0's pre-registration must register is **not** how archetypes are defined. It
is **what evidence a per-archetype model needs before it may be evaluated at all**, and the honest
default is: `asserting_bags ≥ 50` **per archetype**, not in total. A build that floored the total
would let one well-labelled archetype carry `k − 1` that nobody had labelled.

## 1. What an archetype is, and the three candidates (`v0.12.0: planned`)

Not chosen here. Each is costed at one line and the choice belongs to a pre-registration written
before any per-archetype number exists.

| candidate | the partition | the obvious cost |
|---|---|---|
| **by alarm class** | `alarm_class.oid` prefix | the class is already a scoring *feature*; partitioning by it puts the same information in two places |
| **by network element kind** | learned from the varbind profile | the profiler's output is itself learned and drifts, so the partition would drift with it |
| **by situation shape** | storm / quiet / mixed, as the agreement report already cuts | the cut exists and is already reported, but it is a property of the **outcome**, so a model selected by it is selected by what it is trying to predict |

**The third is the tempting one and the most dangerous**, and it is named here so v0.12.0 does not
discover it: choosing an archetype from a property of the grouping the scorer produced is
`incumbent_linked` wearing a different hat.

## 2. What v0.11.0 hands over, unchanged (`v0.12.0: planned`)

* **`model_version`** already carries `kind` + a **canonical JSON document**. A per-archetype model
  is a document with more in it, **not a new column and not a migration** — which is the whole reason
  DECISIONS #161 refused typed columns.
* **The per-kind validator** already dispatches. An `archetype` kind is a branch and a rule set.
* **`scorer_active`'s `CHECK`** already admits exactly one pointer. A per-archetype model is **one**
  artefact holding `k` parameter sets, **not `k` active pointers** — and this draft says so now,
  because `k` pointers would reintroduce the ambiguity `0013` removed.
* **`promotion`** already records refusals, triggers and the plan hash. Per-archetype evidence is
  more rows in `metrics`, not a new table.
* **`evaluation_fold`** already materialises the assignment. Per-archetype folds are the same
  rotation over the same incident ids, **grouped by archetype**, and the incident must stay wholly
  within one fold *and* one archetype.

## 3. What v0.12.0 must decide in advance (`v0.12.0: planned`)

Listed so it is not discovered mid-build:

* **the floor, per archetype** (§0) — and whether an archetype below it falls back to the global
  model or is refused outright;
* **what happens to an alarm whose archetype is unknown**, which is the zero-config case and is
  therefore the *normal* case on day one;
* **whether a promotion is per-archetype or whole-artefact.** A partial promotion — three archetypes
  swapped, two not — is a state this project has no vocabulary for, and §6.8's *"a promotion that
  half-applied would be the worst state this system could reach"* applies with more force, not less;
* **whether the four named quantities are reported per archetype, in total, or both** — and
  **never composed across archetypes**, which is the same refusal §1 of the v0.11.0 plan makes;
* **the detection threshold at `n/k`**, printed beside every per-archetype floor evaluation, because
  §2.5's structural mitigation applies unchanged and bites much harder after a `k`-way split.

## 4. What v0.11.0 recommends against, from what it measured (`v0.12.0: planned`)

1. **Do not ship archetypes before the corpus can decide one comparison.** v0.11.0's refusal is not a
   defect to route around; it is the measurement that says a `k`-way split has nothing to spend.
2. **Do not use the seal to choose `k`.** Choosing a partition against the holdout is adaptive
   selection, and `0012`'s comment measures what that costs: a **median +11.1 p.p.** inflation at
   12 queries on 37 incidents, *when every candidate is equally good*.
3. **Do not let an archetype be defined by the grouping outcome** (§1).
4. **Do not add an `adapter` column.** That is still v0.13.0, and a per-archetype document does not
   need one.

## 5. Explicitly not in v0.12.0

1. **The external cartridge / ONNX.** v0.13.0, behind a worker harness. **ONNX last** stays.
2. **Automatic promotion**, per-archetype or otherwise.
3. **A composite score across archetypes.**
4. **Re-cutting the seal**, including "per archetype".
