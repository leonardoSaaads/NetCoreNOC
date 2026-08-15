# NetCoreNOC documentation

The map of everything under `docs/`. NetCoreNOC is a zero-configuration SNMP trap correlator —
one Python 3.12 asyncio process, one SQLite (WAL) file, one static web UI. Start with the
project [`README.md`](../README.md) for what it is and how to run it; come here for design
rationale, decisions, security, scope, and release history.

New to the codebase? Read [`architecture/repo-map.md`](architecture/repo-map.md) first — a
one-screen tour of the tree.

## Areas

| Area | What it is | Who it's for |
|---|---|---|
| [`architecture/`](architecture/) | How the system is built and where it's going | contributors, reviewers |
| [`adr/`](adr/) | The append-only decision log (every ambiguity call, numbered) | contributors, maintainers |
| [`security/`](security/) | Threat model, security reviews, operator hardening guide | operators, security reviewers |
| [`scope/`](scope/) | Per-version product scope (what each release does and does not do) | maintainers, reviewers |
| [`releases/`](releases/) | Per-version build reports (what changed, quality numbers, caveats) | anyone auditing a release |
| [`gates/`](gates/) | Phase-gate evidence for each build (proof each phase's bar was met) | maintainers |
| [`analysis/`](analysis/) | **Pre-registered analysis plans** — what a release will measure and what it will conclude under each outcome, written *before* the results and hash-guarded afterwards | maintainers, reviewers |
| [`ROADMAP.md`](ROADMAP.md) | Post-MVP, ordered — everything out of the current scope lands here as one line | everyone |

## Architecture

- [`architecture/DESIGN.md`](architecture/DESIGN.md) — the design rationale, per version:
  the queue→engine→store flow, learning, auth/audit, the learned entity model, and the v0.4.0
  hardening.
- [`architecture/repo-map.md`](architecture/repo-map.md) — a newcomer's one-screen tour of the
  repository tree.
- [`architecture/CASE-SCHEMA-DRAFT.md`](architecture/CASE-SCHEMA-DRAFT.md) — the versioned
  `Case` JSON contract, specified ahead of implementation (spec only).
- [`architecture/EXTENSIBILITY-0.6-DRAFT.md`](architecture/EXTENSIBILITY-0.6-DRAFT.md) — the
  v0.5.0-era configurability specification, **superseded in place by v0.6.0**: the scoring surface
  was built, the other two were resequenced, and the external-criterion API was rejected. Read the
  box at the top for the disposition table.
- [`architecture/GOVERNANCE-0.7-DRAFT.md`](architecture/GOVERNANCE-0.7-DRAFT.md) — admin-configurable
  RBAC and per-role/per-principal visibility scoping, the specification **v0.7.0 implements**. States
  the mandatory limit: **visibility scoping is a presentation control, not tenant isolation.** Where
  v0.7.0 departed from it — the stored policy resolves as an *intersection* with the compiled
  ceiling rather than as a grant table validated at write time — the reason is DECISIONS #53.
- [`architecture/ROADMAP-0.8-TO-0.13.md`](architecture/ROADMAP-0.8-TO-0.13.md) — **the single
  source of truth for what each release from v0.8.0 to v0.13.0 is**, and why the order cannot be
  permuted. Written in v0.7.4 from DECISIONS #93, which recorded a resequencing the project had been
  acting on without ever writing down. Enforced by `tests/test_documentation.py`.
- [`architecture/FEEDBACK-PATH-0.7.5-DRAFT.md`](architecture/FEEDBACK-PATH-0.7.5-DRAFT.md) — the
  operator-feedback **acquisition** path: why the expanded situation card collapses every two
  seconds and how a click can be recorded against a membership the operator never evaluated. Spec
  only, `v0.7.5: planned`.
- [`architecture/FEEDBACK-DATASET-0.8-DRAFT.md`](architecture/FEEDBACK-DATASET-0.8-DRAFT.md) — the
  operator-feedback **dataset**: capture-don't-reject, the four constraints the schema must answer,
  and the bias report. Spec only, `v0.8.0: planned`.
- [`architecture/SHADOW-MODE-0.9-DRAFT.md`](architecture/SHADOW-MODE-0.9-DRAFT.md) — the v0.9.0
  specification: what a challenger may and may not do, the label-derivation policies, the leakage
  vectors, and why effective sample size is bags rather than pairs.
- [`analysis/PREREGISTRATION-0.10.0.md`](analysis/PREREGISTRATION-0.10.0.md) — **what v0.10.0 would
  conclude under every outcome, written before any result existed.** Ratified in a commit that
  changed nothing else, hash-guarded by `tests/test_preregistration.py`, and tagged `v0.10.0-gate0`.
  Read §7.1 first: it names `INSUFFICIENT_EVIDENCE` as the expected branch, in advance.
- [`architecture/CHAMPION-CHALLENGER-0.11-DRAFT.md`](architecture/CHAMPION-CHALLENGER-0.11-DRAFT.md)
  — promotion as a pointer move, the human approval that may never be skipped, and the two refusals
  that must differ. **Specification only; v0.10.0 implements none of it.**
- [`architecture/EVIDENCE-BOUNDARY-0.9.2.md`](architecture/EVIDENCE-BOUNDARY-0.9.2.md) — **which
  side of the trust boundary a stored quantity comes from**: the three tiers (reported, reconciled,
  interpretable), every client-controlled write-path input classified against them, what each
  consumer is entitled to read, and the two findings that came of getting it wrong. Written in
  v0.9.2 and **live**: it is the document a release consults before adding a column that measures
  anything.
- [`architecture/HONEST-JUDGE-0.10-DRAFT.md`](architecture/HONEST-JUDGE-0.10-DRAFT.md) — the
  v0.10.0 specification, **written from what v0.9.0 measured rather than from what it hoped**: the
  split by time or by incident and never at random, why the merge chain makes incident identity
  non-obvious, the iterative-overfitting hazard no column measures, and the restated
  `incumbent_linked` invariant.
- [`architecture/UI-0.13-DRAFT.md`](architecture/UI-0.13-DRAFT.md) — the v0.13.0 UI, specified by
  v0.12.0 **against a UI that could by then be executed**: sidebar navigation with room for Phase 2
  and Phase 3 (and **no empty placeholders** for them), per-role dashboards, the enumeration of
  every capability with no UI surface today, the **three parameter classes** that keep a settings
  screen from lowering a sufficiency floor, the env-then-database precedent `runtime.py` already
  proves, the framework recommendation, and the twelve things v0.13.0 must not do. Spec only,
  `v0.13.0: planned`.
- [`architecture/ARCHETYPES-0.12-DRAFT.md`](architecture/ARCHETYPES-0.12-DRAFT.md) — per-archetype
  weights, spec only, **`v0.15.0: planned`**. Written during v0.11.0 as the v0.12.0 specification
  and **resequenced by DECISIONS #170** on the measurement in its own §0: a corpus that cannot
  decide one comparison cannot decide `k` of them. The filename records when it was written, not
  what it governs; read the box at the top.
- [`architecture/SCORER-PLUGINS-0.13-DRAFT.md`](architecture/SCORER-PLUGINS-0.13-DRAFT.md) —
  customer-supplied models (blessed ONNX adapter) under the v0.6.0 `LinkScorer` contract, spec only,
  `v0.14.0: planned`. Refined during v0.7.0 (§R1–R5): the worker-process preemption harness is a
  **blocking prerequisite**. Written as the v0.8.0 spec and **resequenced in place** by v0.7.4
  (DECISIONS #93); the Python entry-point escape hatch it also specified is **rejected, not
  deferred**. Read the box at the top for the disposition.

## Decisions

- [`adr/DECISIONS.md`](adr/DECISIONS.md) — one append-only, numbered log of every
  scope-ambiguity resolution and notable engineering choice (context → options → choice →
  reason). See [`adr/README.md`](adr/README.md) for the format and rules.

## Security

- [`security/threat-model.md`](security/threat-model.md) — lightweight STRIDE over the attack
  surface, extended per version. Holds the same authority as the scope docs: on any
  security-relevant ambiguity, the stricter option wins.
- [`security/SECURITY-REVIEW-0.2.md`](security/SECURITY-REVIEW-0.2.md),
  [`security/SECURITY-REVIEW-0.4.md`](security/SECURITY-REVIEW-0.4.md),
  [`security/SECURITY-REVIEW-0.5.md`](security/SECURITY-REVIEW-0.5.md),
  [`security/SECURITY-REVIEW-0.6.md`](security/SECURITY-REVIEW-0.6.md),
  [`security/SECURITY-REVIEW-0.7.md`](security/SECURITY-REVIEW-0.7.md) — the numbered
  finding → fix → test reviews and standards-compliance mappings. The finding series is continuous
  and never renumbered: F1–F14 (v0.1–v0.4), F15–F19 (v0.5.0), F20–F26 (v0.6.0), F27–F33 (v0.7.0),
  F34–F39 (v0.7.1), F40–F41 (v0.7.4 — the declaration gate, in
  [`security/SECURITY-REVIEW-0.7.4.md`](security/SECURITY-REVIEW-0.7.4.md)), F42 (v0.7.5 — the same
  gate failing open on route *shapes*, in
  [`security/SECURITY-REVIEW-0.7.5.md`](security/SECURITY-REVIEW-0.7.5.md), which also **corrects a
  completeness claim v0.7.4 made** about that gate).
- [`security/operations.md`](security/operations.md) — the **operator** security & operations
  guide (deployment, TLS, roles, audit-log operations, container hardening). The root
  [`../SECURITY.md`](../SECURITY.md) is the coordinated **vulnerability disclosure policy** (how
  to report a vulnerability privately, response times, embargo, scope, safe harbour).

## Scope

- [`scope/SCOPE.md`](scope/SCOPE.md), [`scope/SCOPE-0.2.md`](scope/SCOPE-0.2.md),
  [`scope/SCOPE-0.3.md`](scope/SCOPE-0.3.md), [`scope/SCOPE-0.4.md`](scope/SCOPE-0.4.md),
  [`scope/SCOPE-0.5.md`](scope/SCOPE-0.5.md), [`scope/SCOPE-0.6.md`](scope/SCOPE-0.6.md),
  [`scope/SCOPE-0.7.md`](scope/SCOPE-0.7.md), [`scope/SCOPE-0.7.1.md`](scope/SCOPE-0.7.1.md),
  [`scope/SCOPE-0.7.2.md`](scope/SCOPE-0.7.2.md), [`scope/SCOPE-0.7.3.md`](scope/SCOPE-0.7.3.md),
  [`scope/SCOPE-0.7.4.md`](scope/SCOPE-0.7.4.md) — per-version product scope. Later documents state
  only what changed; earlier invariants still hold. The current one is
  [`scope/SCOPE-0.9.0.md`](scope/SCOPE-0.9.0.md).

## Analysis plans

- [`analysis/PREREGISTRATION-0.9.0.md`](analysis/PREREGISTRATION-0.9.0.md) — v0.9.0's plan,
  written **before any model existed**: the hypotheses, both label-derivation policies, the
  features, the training population, the metrics, the held-out choice, the sufficiency floors, and
  **what will be concluded under every outcome** — including the outcome where nothing beats the
  champion and the outcome where the data is insufficient.

  A plan in this directory is **immutable once written**. Its SHA-256 is recorded in the phase gate
  and asserted by a test, so editing it after seeing a result turns the suite red. The guard makes
  the plan immutable, not honest; that limit is stated in the test's own docstring. A disagreement
  with a registered plan goes into the release's security review as an **opinion for the next
  release**, never into the plan.

## Releases

- [`releases/BUILD-REPORT.md`](releases/BUILD-REPORT.md) and the per-version
  `BUILD-REPORT-0.x.md` — the build narrative, quality/perf numbers, decisions, and honest
  caveats for each release.

## Conventions

- **Authority order.** On scope, the scope document for the version wins; on process and
  quality, the build document wins; on security posture, the threat model wins.
- **History is append-only.** The decision log is never renumbered; build reports and gate
  evidence are point-in-time records and are not rewritten.
- **Moves preserve history** (`git mv`) and every internal cross-link is checked
  (`tests/test_structure.py::test_no_broken_relative_markdown_links`).

### Saying what a release is — the claim form (v0.7.4)

Until v0.7.4 the repository stated **two different answers** to "what is v0.8.0", four lines apart
in `ROADMAP.md`, and nothing noticed for a whole release. `tests/test_documentation.py` now enforces
that there is exactly one answer. It can only do that if a claim is detectable, so there are two
marked forms and they do different jobs. Use them.

**1. A release claim — what a release *is*.** One HTML comment, on its own line, directly above the
prose it formalises:

```
<!-- release-claim: v0.8.0 = operator-feedback-dataset -->
```

The key on the right must match the `claim` column of the release table in
[`architecture/ROADMAP-0.8-TO-0.13.md`](architecture/ROADMAP-0.8-TO-0.13.md), **which is the single
source of truth**. If you believe a release is something else, change the table — with an ADR — and
let the documents follow. Never the other way round. The comment is invisible in rendered Markdown,
so the prose beneath it stays the thing a human reads.

**2. An element tag — what a spec *element* is planned for.** The pre-existing convention, kept:

```
## 1. The blessed ONNX path (`v0.14.0: planned`)
```

A document that carries a release claim may only tag elements for **that** release; that is what
catches a half-finished supersession, where a draft is retagged but one heading is missed.

**Backticks around the tag are the convention, and the guard reads them.** `` `v0.8.0: planned` ``
and a bare `v0.8.0: planned` are both found — the tag is what counts, not its rendering. Between
v0.7.4 and v0.7.5 that was **not** true: `test_documentation.py::source_of` stripped inline code
spans before looking, so it saw only the unbackticked form that nobody writes, and 31% of the
repository's element tags were visible to it. The strip is gone (DECISIONS #100), and
`test_the_element_tag_check_goes_red_on_a_backticked_stray_tag` now demonstrates the failure rather
than assuming it. **Only a fenced block is exempt** — like the two examples above, which show the
forms without asserting them.

**Live documents and records.** The guard reads forward-looking documents (`architecture/`,
`ROADMAP.md`, `security/threat-model.md`, this file) and deliberately does **not** read
`scope/`, `adr/`, `gates/`, `releases/` or the per-release security reviews. Those are records of
what was believed or done at a point in time — `SCOPE-0.6.md` says v0.8.0 is customer models because
that is what v0.6.0 believed — and rewriting a record to agree with a later decision is falsifying
it. Supersede in place with a dated note; never rewrite.
