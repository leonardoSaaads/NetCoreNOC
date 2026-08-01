# v0.8.0 → v0.13.0 — the plan the project has been working from

**Status: this document is the single source of truth for what each release from v0.8.0 to v0.13.0
is.** Every other document in `docs/` that asserts what one of these releases *is* carries a
machine-readable claim that is checked against the table below by
`tests/test_documentation.py`. Where prose and this table disagree, the table wins and the test
fails.

Written in v0.7.4, from **DECISIONS #93**, which recorded a resequencing the project had been acting
on for two releases without ever writing down. Before that entry the repository said both that
v0.8.0 was customer-supplied models and that v0.8.0 was the operator-feedback dataset — in
`ROADMAP.md`, four lines apart in the same file. The full enumeration is
[`../gates/v0.7.4-phase-0.md`](../gates/v0.7.4-phase-0.md) §6.

This document plans; it implements nothing and it schedules nothing. A release listed here is a
theme with a reason for its position in the chain, not a commitment to a date.

---

## The chain

<!-- The `claim` column is the machine-readable key. `tests/test_documentation.py` parses this table
     and every `<!-- release-claim: vX.Y.Z = key -->` marker across the live documents, and fails if
     any two disagree. Do not reformat this table without reading that test. -->

| Release | Theme | claim |
|---|---|---|
| **v0.8.0** | **The scoreboard** — capture the operator feedback as a durable dataset and measure its bias. Trains nothing. | `operator-feedback-dataset` |
| **v0.9.0** | **Shadow mode** — simple models train in the slow loop and record how they *would* have grouped. The built-in scorer decides everything. | `shadow-mode` |
| **v0.10.0** | **The honest judge** — held-out evaluation split **by time or by incident, never at random**, scored on over-merge and under-merge. | `honest-judge` |
| **v0.11.0** | **Champion/challenger** — the slow loop proposes a promotion with the evidence; an admin approves; the swap is one more immutable `scorer_config` row. | `champion-challenger` |
| **v0.12.0** | **Archetypes** — per-archetype weights (PON/access, transport/DWDM, IP core). Marked *likely, review before committing*. | `archetypes` |
| **v0.13.0** | **The external cartridge** — ONNX under the proven framework, behind the worker-process harness. | `external-cartridge` |

---

## Why the order cannot be permuted

The chain is not a preference. Each link consumes something the previous one produces, and the
dependency is on *evidence*, not on code:

> **You cannot train a challenger without a label. You cannot trust the label without knowing it is
> scarce and biased. You cannot declare a winner without an evaluator that never saw the training
> data. And you cannot automate the promotion without proof of real agreement with humans.**

Read backwards, that is why each release exists. Read forwards, it is why none of them can be
brought earlier:

* **v0.9.0 before v0.8.0** would train on labels that do not exist. There is no other source of
  human judgement in this system — no ticket import, no external ground truth, no annotator pool.
* **v0.10.0 before v0.9.0** would build an evaluator with nothing to evaluate, and the temptation
  would be to evaluate the built-in scorer against the labels it already influences, which measures
  agreement with itself.
* **v0.11.0 before v0.10.0** would promote on the training metric. That is the classic failure, and
  in this system it is worse than usual: the operator sees the *result* of correlation and labels
  what they see, so a model that over-merges produces fewer, larger situations to label, and the
  label stream itself moves under the model.
* **v0.12.0 before v0.11.0** would ship per-archetype weights with no mechanism to prove one
  archetype's weights beat the general ones. It would be six sets of numbers nobody can defend.
* **v0.13.0 before v0.11.0** would put the riskiest element — a new runtime dependency and a new
  trust surface — in front of the framework that receives it and the evaluator that judges it. A
  customer model that cannot be evaluated cannot be promoted, so it would arrive with nowhere to go.

---

## v0.8.0 — the scoreboard

<!-- release-claim: v0.8.0 = operator-feedback-dataset -->

**Capture the operator feedback as a durable dataset, and measure its bias. Trains nothing.**

The feedback click is the only human label in the system. Today it adjusts learned weights through
`learn.penalize()` and is then gone: there is no row that records *what the operator was looking at*
when they judged it. v0.8.0 adds that row.

Three things make this a release rather than a migration:

1. **The evaluation is discarded.** `correlate.process()` evaluates candidate pairs and returns
   `links` *and* `considered`; only `links` is persisted, and `MAX_LINKS_PER_ALARM` truncates even
   that. The dataset is censored on both ends and the capture has to fix it at the moment of
   decision, not afterwards.

   > **Corrected 2026-08-01 (v0.8.0).** This bullet previously called the evaluated-and-rejected
   > pairs *"the majority class, without which supervised training is impossible"*. That is wrong
   > twice and the sentence is withdrawn — see
   > [`FEEDBACK-DATASET-0.8-DRAFT.md`](FEEDBACK-DATASET-0.8-DRAFT.md) §3.1a. Briefly: they are the
   > **machine's decision**, not the human label's majority class, and reading them as a training
   > target is the imitation trap — *no metric that decides promotion may be computed against
   > `incumbent_linked`*. They are also only **0.17 %** of the eval corpus (194 341 pairs evaluated,
   > 194 002 accepted, 339 rejected): the 17.7× amplification is `MAX_LINKS_PER_ALARM` truncating
   > *accepted* links, and the accept rate swings from 0 % on quiet traffic to 100 % in a storm.
   > The operational consequence — capture every evaluated pair, before either censoring — is
   > unchanged.
2. **Features must be captured, not reconstructed.** `A` and `E` decay continuously and `alarm` is
   deduplicated and mutated on re-fire, so the state at decision time is not recoverable later. This
   is why v0.8.0 is a capture workstream and not an offline job over history.
3. **The bias report is the deliverable, not a by-product.** Confirms versus splits, the size
   distribution of labelled situations, how many labels come from how few operators, and what
   fraction were made under a visibility scope that hid part of the situation. A dataset whose bias
   nobody has measured is not an asset; it is a liability with a schema.

Specified in [`FEEDBACK-DATASET-0.8-DRAFT.md`](FEEDBACK-DATASET-0.8-DRAFT.md).
Its acquisition path is fixed first, in v0.7.5 —
[`FEEDBACK-PATH-0.7.5-DRAFT.md`](FEEDBACK-PATH-0.7.5-DRAFT.md) — because an unreliable click
produces an unreliable dataset, and a silently wrong label is worse than a missing one.

---

## v0.9.0 — shadow mode

<!-- release-claim: v0.9.0 = shadow-mode -->

**Simple models train in the slow loop and record how they *would* have grouped. The built-in
scorer decides everything.**

A challenger runs beside the champion and writes its opinion to a table. Nothing it says reaches a
situation, the UI, or an operator. The release exists to answer one question — *can a model trained
on this dataset reproduce the built-in scorer's decisions at all?* — before anything is staked on
the answer.

**Logistic regression is the natural front-door candidate**, and the reason is structural rather
than a judgement about model families: the v0.6.0 scorer *is* a three-term weighted sum, so logistic
regression is that same formula with learned weights. It inherits the per-term explainability
contract for free — a `TermContribution` per feature is what the model already computes — and it
needs no new runtime dependency. This is also where v0.9.0 decides the **label-derivation policy**
that v0.8.0 deliberately left open: how a per-situation verdict becomes per-pair training signal is
a modelling choice, and v0.9.0 is the first release that can *evaluate* it rather than assume it.

---

## v0.10.0 — the honest judge

<!-- release-claim: v0.10.0 = honest-judge -->

**Held-out evaluation, split by time or by incident — never at random — scored on over-merge and
under-merge.**

A random split leaks. Alarms from one incident are correlated with each other by construction, so a
random 80/20 puts near-duplicates of the test set into the training set and reports a number that
cannot be reproduced on a network. Splitting by **time** (train on the past, test on the future) or
by **incident** (an incident is wholly in one side) is the only split that measures what the
appliance will actually do.

The metric is not accuracy. Over-merge and under-merge are different failures with different costs
to an operator: an over-merged situation buries a second fault inside a first, and an under-merged
one gives the NOC two tickets for one cable. The existing `eval/harness.py` already reports both,
which is why this release extends a harness rather than inventing one.

---

## v0.11.0 — champion/challenger

<!-- release-claim: v0.11.0 = champion-challenger -->

**The slow loop proposes a promotion with the evidence; an admin approves; the swap is one more
immutable `scorer_config` row.**

The mechanism already exists. v0.6.0 made the scorer a versioned, swappable, explainable seam with
an append-only configuration history and one-click rollback; a promotion is a new row and a moved
pointer, which is exactly what `POST /api/scorer` already does. What v0.11.0 adds is the *proposal*
— the challenger's held-out numbers, the sample of decisions that changed, and the admin's approval
as an audited action.

**A human approves.** Not because the numbers cannot be trusted, but because a correlation change is
a system-wide logic change, and the project has held since v0.6.0 that those are admin-only with no
delegation. Automating the approval is a v0.12.0-or-later question and needs proof of sustained
agreement between the proposal and what admins actually approve.

**Two admission criteria are decided here, and they are easy to lose:**

* **A model competes on quality only after passing a speed-and-explainability admission filter.** A
  marginally more accurate model that threatens ingestion **loses on purpose**. "Ingestion is
  sacred" is the project's oldest invariant, and a scorer sits on the correlation path inside the
  batch lock. A model that cannot explain a grouping per-term also loses, because the operator-facing
  EXPLAIN is a contract, not a feature.
* **Tree ensembles cannot be champion before v0.13.0.** Not on merit — on plumbing. There is no
  in-process implementation of one among the five runtime dependencies, so a gradient-boosted model
  can only enter through the ONNX door, and that door opens in v0.13.0. Saying so here prevents a
  future reader mistaking a packaging constraint for a finding about model families.

---

## v0.12.0 — archetypes

<!-- release-claim: v0.12.0 = archetypes -->

**Per-archetype weights — PON/access, transport/DWDM, IP core.** Marked ***likely, review before
committing***.

A dying-gasp storm on a PON tree and a fibre cut on a DWDM span have different time constants, and
one set of weights is a compromise between them. The `LinkScorer` seam accommodates per-archetype
parameter sets without a contract change, and by v0.12.0 the champion/challenger framework can prove
that a specialised set beats the general one *per archetype* rather than on average.

The hedge is deliberate and is carried from `ROADMAP.md`: this depends on device-archetype
clustering (DECISIONS #36), which is itself unbuilt, and it multiplies the number of models to
evaluate and roll back by the number of archetypes. Review the evidence from v0.11.0 before
committing to it. It is the one release in this chain that may reasonably be dropped.

---

## v0.13.0 — the external cartridge

<!-- release-claim: v0.13.0 = external-cartridge -->

**ONNX under the proven framework, behind the worker-process harness.**

Customer-supplied models, resequenced here from v0.8.0 by **DECISIONS #93**. Specified in
[`SCORER-PLUGINS-0.13-DRAFT.md`](SCORER-PLUGINS-0.13-DRAFT.md), which was written as the v0.8.0
specification during v0.6.0 and is retagged rather than rewritten — its technical analysis stands;
only the release changed.

Two constraints travel with the resequencing and are not negotiable at build time:

* **ONNX only. The Python entry-point escape hatch is rejected, not deferred** (DECISIONS #93).
  ONNX is *data executed by a pinned runtime*; an entry-point scorer is *arbitrary code running as
  the process*, holding the process's database handle and its network. Modern frameworks all export
  to ONNX, so the entry point buys reach the project does not need at a trust cost it should not
  pay. `onnxruntime` remains an optional extra and never a base dependency: the five runtime
  dependencies are a shipped promise.
* **The worker-process preemption harness is a blocking prerequisite.** v0.6.0's `SafeScorer` is
  post-hoc — it measures a call after it returns and degrades the *next* one — which is right for
  five floating-point operations and useless against a C extension that never returns.
  `SCORER-PLUGINS-0.13-DRAFT.md` §R2 specifies the harness, including that the worker→parent channel
  **must not use `pickle`**: a compromised worker returning a malicious pickle is remote code
  execution in the parent by the back door, which would turn the sandbox into a delivery mechanism.

---

## What this document does not decide

* **Dates.** None of these releases has one.
* **v0.7.5.** It is not in this chain: it is a runtime-behaviour fix to the feedback *acquisition*
  path, specified in [`FEEDBACK-PATH-0.7.5-DRAFT.md`](FEEDBACK-PATH-0.7.5-DRAFT.md), and it is a
  prerequisite for v0.8.0 rather than a member of the sequence.
* **Anything after v0.13.0.** `ROADMAP.md` keeps the unsequenced ideas, one line each, as it always
  has.
* **Whether v0.12.0 happens at all.** Recorded above as *likely, review before committing*, which is
  the honest state.
