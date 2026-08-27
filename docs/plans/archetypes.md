# Archetypes — v0.17.0 draft (not implemented)

<!-- release-claim: v0.17.0 = archetypes -->

**Implement none of this.** Every element below is tagged **`v0.17.0: planned`**.

Written during v0.11.0 **from what that release measured**, not from what it hoped, and resequenced
twice since (#170, #184) on the measurement in §0 — which is this document's own opening argument
rather than a later objection to it. Its evidentiary discipline is
[`../analysis/PREREGISTRATION-0.11.0.md`](../analysis/PREREGISTRATION-0.11.0.md).

## 0. The uncomfortable fact this draft starts from (`v0.17.0: planned`)

**v0.11.0 built a promotion gate and it refused, on a corpus with `asserting_bags = 0` against a
floor of 50.** Per-archetype weights mean **one model per archetype**, which means **splitting an
already-insufficient corpus `k` ways**.

> **If a corpus cannot decide one comparison, it cannot decide `k` of them, and dividing it makes
> every arm worse.**

So the first thing the release's pre-registration must register is **not** how archetypes are
defined. It is **what evidence a per-archetype model needs before it may be evaluated at all**, and
the honest default is `asserting_bags ≥ 50` **per archetype**, not in total — a total floor would
let one well-labelled archetype carry `k − 1` that nobody had labelled.

## 1. What an archetype is: three candidates, none chosen here (`v0.17.0: planned`)

| candidate | the partition | the obvious cost |
|---|---|---|
| **by alarm class** | the class OID prefix | the class is already a scoring *feature*; partitioning by it puts the same information in two places |
| **by element kind** | learned from the varbind profile | the profiler's output is itself learned and drifts, so the partition drifts with it |
| **by situation shape** | storm / quiet / mixed, as the agreement report already cuts | the cut exists and is reported, but it is a property of the **outcome** |

**The third is the tempting one and the most dangerous**, named here so the release does not discover
it: choosing an archetype from a property of the grouping the scorer produced is the imitation trap
wearing a different hat.

## 2. What is already in place, unchanged (`v0.17.0: planned`)

* **`model_version`** carries a **canonical JSON document**, so a per-archetype model is a document
  with more in it — **not a new column and not a migration** (#161).
* **The per-kind validator** already dispatches; an archetype kind is a branch and a rule set.
* **The active pointer's `CHECK`** admits exactly one artefact, so a per-archetype model is **one**
  artefact holding `k` parameter sets, **not `k` pointers** — which would reintroduce the ambiguity
  the schema removed.
* **Promotions already record refusals, triggers and the plan hash**, so per-archetype evidence is
  more rows, not a new table.
* **The fold assignment is already materialised**; per-archetype folds are the same rotation, grouped
  by archetype, and an incident must stay wholly within one fold *and* one archetype.

## 3. What the release must decide in advance (`v0.17.0: planned`)

* **The floor, per archetype** (§0) — and whether an archetype below it falls back to the global model
  or is refused outright.
* **What happens to an alarm whose archetype is unknown**, which is the zero-config case and therefore
  the *normal* case on day one.
* **Whether a promotion is per-archetype or whole-artefact.** A partial promotion — three archetypes
  swapped, two not — is a state this project has no vocabulary for.
* **Whether the four named quantities are reported per archetype, in total, or both** — and **never
  composed across archetypes**.
* **The detection threshold at `n/k`**, printed beside every per-archetype floor evaluation, because
  it bites much harder after a `k`-way split.

## 4. What v0.11.0 recommends against, from what it measured (`v0.17.0: planned`)

1. **Do not ship archetypes before the corpus can decide one comparison.** The refusal is not a defect
   to route around; it is the measurement saying a `k`-way split has nothing to spend.
2. **Do not use the seal to choose `k`.** Choosing a partition against the holdout is adaptive
   selection, measured at a **median +11.1 p.p.** inflation at twelve queries on 37 incidents *when
   every candidate is equally good*.
3. **Do not let an archetype be defined by the grouping outcome** (§1).
4. **Do not add an adapter column.** That is the cartridge's, and a per-archetype document needs
   none.
