# SCOPE — v0.11.0

**Champion/challenger: promotion becomes possible, auditable, and refusable — and on this corpus it
refuses.**

This release does **not** promote anything. It builds the machinery that could promote a scorer, the
two refusals that stop it, and the audit trail that survives either — and against the real corpus the
gate returns `INSUFFICIENT_EVIDENCE` with its reasons enumerated. **That is the pre-registered
expected outcome and it is a successful release.**
[`../analysis/PREREGISTRATION-0.11.0.md`](../analysis/PREREGISTRATION-0.11.0.md) §6.1 says so in
advance, before any result existed, which is the entire point of having written it first.

Binding authorities, in the order they win: **the ratified pre-registration** on every analytical
question; this document on **scope**;
[`../architecture/MODULE-ARCHITECTURE.md`](../architecture/MODULE-ARCHITECTURE.md) on **where code
goes**; [`../architecture/ROADMAP-0.8-TO-0.13.md`](../architecture/ROADMAP-0.8-TO-0.13.md) on
**sequence**; [`../security/threat-model.md`](../security/threat-model.md) on **security posture**;
the build prompt on process and quality.

> **Where the build prompt and the plan appear to disagree, the plan wins**, and the apparent
> disagreement is reported in [`../gates/v0.11.0-phase-1.md`](../gates/v0.11.0-phase-1.md) §3 rather
> than resolved silently.

> **Where `CHAMPION-CHALLENGER-0.11-DRAFT.md` and the build prompt disagree, the build prompt wins on
> the three claims its Part IV names, and only those three.** Every other apparent disagreement is
> reported in [`../gates/v0.11.0-phase-1.md`](../gates/v0.11.0-phase-1.md) §3. The draft is **not
> edited**: it is v0.10.0's record of what v0.10.0 could see, and rewriting it to agree with what
> v0.11.0 measured would destroy the only evidence of what was and was not foreseeable.

---

## 0. Why this release exists, in three measured facts

All three reproduced by execution in
[`../gates/v0.11.0-phase-0.md`](../gates/v0.11.0-phase-0.md) §2, each with a control that had to
behave the other way.

**Fact A — a fitted challenger cannot be stored in `scorer_config`.**
`validate_params(1.7, 3.1, -0.8, 30.0, 0.0)` raises on `w_t` — and independently on `w_a`, on `w_e`,
and on `threshold`. Four rejections, not one. The fourth is the one that cannot be negotiated:
`MIN_THRESHOLD` is `0.01` because *for the additive scorer* a threshold at zero links everything,
while *for the logistic scorer* `0.0` is the neutral point. The same number means opposite things to
the two kinds. **Control**: the seeded defaults through the same call are accepted.

**Fact B — `scorer_lifecycle` has no dispatch.** By `ast`: one scorer construction inside
`load_scorer_config`, **unconditional**; the `scorer_id` column reaches a keyword argument and never
a comparison. It is written *to* the scorer, never read *to choose* one. **Control**: a synthetic
dispatching module through the same probe reports `DISPATCH FOUND`.

**Fact C — `scorer_config` receives manual retunes, and a promotion column there would be `NULL` on
every one.** Measured three ways — the handler passes 11 arguments and none names a judgement, a
challenger run or an approver; the table has no such column (**control**: `holdout_seal` and
`holdout_access` do); and a retune POSTed at a live app reads back with `created_by = 'adm'`, an
actor rather than an approver of a judgement.

**And the fourth fact, which decides what the gate says**: the corpus census, re-run in
[`../gates/v0.11.0-phase-1.md`](../gates/v0.11.0-phase-1.md) §1 and reproducing every v0.10.0
figure — **41 bags, 37 merge-aware incidents, `asserting_bags` 0 against a floor of 50**. The gate
this release builds will refuse, and it will refuse `INSUFFICIENT_EVIDENCE`.

---

## 1. In scope

| # | Item | Note |
|---|---|---|
| 1 | **Migration `0013`**, additive, forward-only, **exactly one** | `model_version`, `promotion`, `scorer_active.model_version_id` + its `CHECK`, `evaluation_fold`, the `holdout_access` hash chain. **No rows seeded.** |
| 2 | **`model_version`** — the artefact | `kind`, `contract_version`, `params_document` (canonical JSON), `params_hash`, `challenger_run_id`, `created_at`. Append-only, triggers like `scorer_config`'s. |
| 3 | **`promotion`** — the event | the verdict and its triggers, the ratified plan hash, the seal query count at decision time, `approved_by`, `decided_at`, `outcome IN ('applied','refused')`, `refusal_reason`. **Refusals leave rows.** |
| 4 | **The per-kind payload validator**, with the logistic degeneracy rules of the plan's §5 | five rules, registered before any fit existed |
| 5 | **Dispatch by kind in `scorer_lifecycle`** | which it has never had (Fact B) |
| 6 | **Fold materialisation** — `(run_id, incident_id, fold, repeat)` | so a citation points at stored rows |
| 7 | **The promotion gate**: server-side verdict re-derivation, and **two refusals by different code paths** | the plan's §4 |
| 8 | **Human approval** — route plus CLI | admin only, no delegation |
| 9 | **The audit actions this release makes possible**, and no others | ADR #162 |
| 10 | **`holdout_access` becomes hash-chained** | registered in the plan's §3 |

## 2. Explicitly out of scope

| # | Item | Why, and where it goes instead |
|---|---|---|
| 1 | **Automatic promotion**, in any form | There is no `auto_promote` flag, **not even defaulted off**. A flag defaulted off is a flag, and the next release would find it and turn it on. |
| 2 | **Any UI change** — not a button, not a field, not a string | ADR #163. A ROADMAP line, gated on a test that executes `ui/app.js` in a real DOM. |
| 3 | **Per-archetype models** | v0.12.0. `ARCHETYPES-0.12-DRAFT.md` specifies it and implements nothing. |
| 4 | **The external cartridge / ONNX** | v0.13.0, and **nothing here creates a plugin surface** — no adapter column, no registry, no entry point. |
| 5 | **A composite quality score** | Four named quantities, never composed. Adopting a composite would reverse that decision under a different name. |
| 6 | **Re-cutting the seal**, or any change to its construction rule | The plan's §3 declines it *and says why it declines it*, which is the part that matters: this release changes `scorer_active` and adds dispatch, and changing the seal in the same release would remove the only stable thing to measure against. |
| 7 | **Relaxing `incumbent_linked`** | Refused in the plan's §1, in advance, rather than reconsidered after the gate came out empty. |
| 8 | **The v0.9.2 reconciliation-drift audit gap** | ADR #162. Not newly reachable in this release. Stays a ROADMAP line and a finding. |
| 9 | **Snapshotting the merge graph** | `DATA-LINEAGE.md` §5 names it; Phase 4 records the deferral and its cost. |
| 10 | **Any change to** `correlate.py`, `receiver.py`, `learn.py`, `capture.py`'s write path, `engine.py`, `labels.py`'s write path, `shaping/`, `seal.py`'s protections, or any existing migration | Prime directive 10. |

## 3. What a promotion's evidence licenses, and what it does not

Stated here rather than only in the security review, because it is a **scope** claim: it says what
the artefact this release produces is *for*.

A `promotion` row with `outcome = 'applied'` licenses exactly one inference: **an admin, at a named
time, approved a swap that a server-derived verdict said was justified, citing a stored evaluation
over a stored fold assignment.** It does **not** license *"the challenger is better"* as a fact about
networks — the verdict is over this corpus, at this `n`, under this plan.

And on **this** corpus no such row can exist, because the floors are unmet. Every promotion row this
release can actually produce carries `outcome = 'refused'`.

## 4. The one-sentence test

> **Could an operator, six months from now, ask *"what has this appliance been asked to deploy, and
> why was it refused"* — and get an answer from rows rather than from memory?**

That is the question `holdout_access` was built to answer about the seal, and it is the question
`promotion` is built to answer about the correlator. A table of successes answers *"what is
deployed"*, which is a different and smaller question.
