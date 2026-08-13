# Data lineage

**Status: `v0.11.0+: planned`. This document specifies and implements nothing.** No code, no schema,
no dependency, no route. It exists so that the next three efforts can be *scoped* rather than
*discovered*.

---

## 0. The question

> **To reproduce evaluation X from six months ago, what must have been stored?**

Not *"what is stored"* — the schema answers that. The question is what a reader six months from now
needs in order to **re-run an evaluation and get the same number**, and which of the inputs will
still exist when they try.

This matters now rather than eventually because **v0.11.0 turns a verdict into a promotion**. An
admin approving a scorer swap will cite an evaluation. A citation to a number nobody can reproduce is
a citation to a memory.

---

## 1. Every data surface, by trust tier

The tiers are `EVIDENCE-BOUNDARY-0.9.2.md` §1's, unchanged and not re-derived here:

| tier | what it is | who may derive it | what it is entitled to decide |
|---|---|---|---|
| **1 — Reported** | what the client said | the client | facts **about the client** |
| **2 — Reconciled** | tier 1 ∩ the server's own bag, at the instant of the verdict | the server | every quantity about the **evidence** |
| **3 — Interpretable** | whether the reconciled marks were about members the labeller could observe | the server, **and its input expires** | every quantity that will **decide** anything |

| surface | table(s) | tier | written by | it is the input to |
|---|---|---|---|---|
| **the sink** | `dataset_observation`, `dataset_pair` (`lifecycle='sink'`) | 2 | `capture.py`, on every correlation | promotion; nothing else reads it |
| **the dataset** | the same tables (`lifecycle='dataset'`) | 2 | promotion, on label | the estimator, the challenger, every report |
| **labels** | `feedback` | 1 and 2 in the same row | the label write path | every floor, every metric, the seal's ordering |
| **the client's report** | `feedback_exclusion`, `feedback_member(source='client')`, `excluded_count`, `excluded_truncated` | **1** | the client, verbatim, never rejected | facts about the client |
| **the server's bag** | `feedback_member(source='server')` | 2 | the server | reconciliation |
| **the reconciliation** | `excluded_reconciled`, `excluded_reconciled_source` | 2 | write path (`live`) or `0011` (`backfill`) | `learn.penalize`, every asserted-negative count |
| **scope at label time** | `excluded_reconciled_out_of_scope`, `scope_redacted_members` | **3** | write path only; `NULL` forever on pre-`0011` rows | whether an assertion could have been made |
| **exclusions of the corpus** | `capture_provenance`, `acquisition_channel` | 2 | capture | which population a row belongs to; **never averaged across** |
| **shadow opinions** | `challenger_run`, the shadow tables | 2 | the slow loop | the shadow report, the estimator |
| **the seal** | `holdout_seal`, `holdout_seal_member` | 2 | `seal.construct`, **once** | nothing yet — v0.10.0 did not spend it |
| **the access log** | `holdout_access` | 2 | every read and every refusal | the query count, which is published beside any holdout number |
| **the audit chain** | `audit_event` | 2 | every governed act | who did what |
| **scorer configuration** | `scorer_config` | 2 | `admin`, governed | which champion produced a given grouping |

**The merge graph is not on this list, and that is §4.**

---

## 2. Lifecycle per surface — what removes what

Three mechanisms, and they are **not** the same mechanism at three sizes:

| mechanism | governed by | what it may remove | what it may **never** remove |
|---|---|---|---|
| **operational prune** | `NETCORENOC_RETENTION_DAYS` (7.0) | cleared alarms, closed/merged situations, `situation_alarm`, `link`, quarantine | **`feedback`** — that is F44 |
| **dataset tiers** | `RetentionPolicy` — `sink_days` 21, `sink_rows`, `training_days` 365, `audit_days` 730 | sink rows (dual bound); labels past the audit tier | promoted rows inside the training tier |
| **nothing** | — | — | the seal, the access log, the audit chain, `feedback_exclusion` |

**F44 and its v0.9.2 successor are the worked examples, and they are the same lesson twice.**

*F44*: until v0.8.1 the operational prune deleted `feedback` with its situation. When v0.8.0 made
that row **the dataset's label**, the line that deleted it was not revisited — so in a default
deployment every human verdict died 7 days after its situation closed while the `dataset_pair`
features it justified survived, because `dataset_pair` deliberately carries no foreign key to
`alarm`. Silent, asymmetric, and it emptied the one asset that cannot be recomputed.

*Its v0.9.2 successor*: **the label survives; the label's *interpretability* does not.** `prune()`
collects the alarms of a closed situation, and with them `alarm.ne_id`. Measured on one clock
(`v0.9.2-phase-0.md` §4): `feedback` 1 → 1, `feedback_exclusion` 2 → 2, the server bag 4 → 4, and
*marked ids still resolvable to an NE* **2 → 0** in the same pass. The verdict is intact and the
question *"could this operator have seen what they marked?"* has become permanently unanswerable.

That asymmetry — **the fact survives, the ability to interpret it does not** — is the shape of every
lineage problem in this system, and §4 is the third instance of it.

---

## 3. Recomputable versus expiring

The rule the project already follows, stated so it can be applied rather than re-derived:

> **Store what cannot be recalculated; derive what can — *unless an input to the derivation is
> subject to retention*.**

The exception is the whole of the rule. It is DECISIONS #133's line seen from the other side:
*recomputing a quantity from evidence already stored is derivation and may be backfilled; inventing a
quantity whose input is gone is fabrication and must be `NULL`.*

| quantity | derivable? | stored? | why |
|---|---|---|---|
| `excluded_reconciled` | **yes**, from `feedback_exclusion ∩ feedback_member('server')` | **yes, materialised** | both inputs survive every retention path; stored so the *act* is attributable (`live` vs `backfill`) |
| `excluded_reconciled_out_of_scope` | **no** — needs `alarm.ne_id`, which `prune()` collects, and the scope policy *as experienced* | yes, or `NULL` forever | the input expires; a reconstruction would be of what the policy *said*, not what the operator *saw* |
| the CV fold assignment | **yes** — a rotation over sorted incident ids, no RNG | **no** | pure function of the incident ids… **which is §4** |
| the cluster bootstrap interval | yes — fixed seed, own LCG | no | pure function of the per-cluster values |
| the seal membership | yes, *at the moment it was cut* | **yes, materialised** | its inputs are the incident set and the first-label ordering, and **both move** |
| the verdict | yes | no | pure function of its arguments |

**The seal is materialised and the folds are not, and the difference between those two decisions is
the entire subject of the next section.** Both are deterministic functions of the incident set. One
was written down. The other is recomputed on demand.

---

## 4. The open item: incident identity is not stable in time

**Stated plainly, and not solved here.**

Fold assignment is deterministic in the incident ids — `assign_folds` is a rotation over the sorted
ids, with no RNG and no clock. Incident identity is `netcorenoc.incidents.resolve_all` over the
merge edges. **And the merge edges change when situations merge.**

So:

1. a bag labelled today belongs to incident *I*;
2. tomorrow its situation is merged into another, and it belongs to incident *J*;
3. `assign_folds` over a different id set produces a different rotation;
4. **the bag moves to a different fold**, and every fold-level number moves with it.

**No snapshot of the merge graph is retained anywhere.** `situation.merged_into` holds the *current*
state and nothing holds a prior one — the column is forward-only, and there is no history table, no
`merged_at`, and no event in the audit chain that records the edge. An evaluation is therefore
reproducible **today** and not necessarily **later**, and nothing in the system says so.

**Why v0.11.0 is where this stops being a footnote.** v0.10.0
produced a verdict and spent nothing. v0.11.0 produces a **promotion**, and a promotion cites an
evaluation. If the evaluation cannot be re-derived, the citation degrades from evidence to assertion
between the moment it is made and the moment anyone checks it — and the check is exactly what an
approval workflow exists to make possible.

Note the second-order version, which is worse: **the seal is materialised over incident ids.**
`holdout_seal_member.incident_id` was written once, from the incident map as it stood at
construction. If identity drifts, the sealed set and the estimator's exclusions are computed from
**two different maps taken at two different times** — the disagreement
`PREREGISTRATION-0.10.0.md` §3.3 exists to prevent, arriving through *time* rather than through a
second implementation. v0.10.1's B1 and B2 close the *spatial* version of this hazard. The *temporal*
version is open.

### The candidate answers, named and **none chosen**

| candidate | what it stores | the obvious cost |
|---|---|---|
| **materialise the fold assignment per evaluation run** | `(run_id, incident_id, fold, repeat)` | reproduces the evaluation, explains nothing about *why* an incident moved; grows with runs × incidents |
| **snapshot the merge edges** | the edge set, per run or per change | reproduces *any* incident-derived quantity, including the seal's; a second copy of a live table, with the drift risk that implies |
| **version the incident map** | an incident-id generation counter, advanced on merge | cheapest to store and the most invasive to reason about; every consumer must then say *which* generation it meant |

Each has a different answer to *"and what happens when a promotion cites a run whose evidence has
since been pruned?"*, and that question is the one that should decide it.

**This document chooses none of them.** Ambiguity about whether to fix or to document resolves to
document, and the release that acts on an evaluation is the release that should pay for making it
reproducible.

---

## 5. What this document deliberately does not answer

* **Which candidate in §4.** Named, costed at one line each, chosen by whoever schedules v0.11.0.
* **Whether an evaluation should be immutable once cited.** A promotion citing a run implies the run
  is now evidence; nothing currently prevents the run's inputs from being pruned underneath it, and
  no retention tier knows what a citation is.
* **Whether the merge graph deserves an audit event.** It is an operator-visible act with a
  before-and-after, which is the shape of everything else in `audit_event` — but merges are also
  produced by the *engine*, in bulk, on the ingest path, and principle 4 forbids adding work there.
  The asymmetry is real and is not resolved here.
* **How far back reproducibility should be promised at all.** `audit_days` is 730. Nothing says an
  evaluation must be reproducible for that long, or for any length of time, and until something does,
  "reproducible" has no deadline to be measured against.
* **What a lineage *API* would look like.** No route is proposed. `EVIDENCE-BOUNDARY-0.9.2.md` §5's
  reasoning applies unchanged: a surface that answers *"where did this number come from"* is a scope
  bypass by construction, and it needs a capability and a posture before it needs an endpoint.
