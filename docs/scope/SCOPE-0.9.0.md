# SCOPE — NetCoreNOC v0.9.0

**Theme: shadow mode — a challenger runs beside the champion and writes its opinion where nobody
acts on it. The built-in scorer decides everything.**

This is the release where the project stops being a correlator with a dataset and starts being a
correlator that can be *measured*. Its most valuable output is not a model. It is two numbers:

> **how well the champion already agrees with the operators**, and **whether there is enough signal
> in the data to learn anything at all.**

Either number may make the case for shrinking v0.10.0 through v0.13.0, and **reporting them honestly
is the release succeeding, not failing.**

Phase 0 measured both halves before anything was designed
([`../gates/v0.9.0-phase-0.md`](../gates/v0.9.0-phase-0.md)), and two of its findings shape
everything below:

1. **The champion accepts 99.83 % of the pairs it evaluates** (194 341 evaluated, 339 rejected), with
   the accepted population's first percentile at 0.5551 — above the 0.50 threshold. A pairwise
   classifier trained on the joined data scores 99.83 % by always answering "link".
2. **There is no point in `maintenance()` that runs outside `store.lock`.** The slow loop this
   release was told to train in does not exist yet; the release has to create it.

The runtime identity is unchanged: one Python 3.12 asyncio process, one SQLite (WAL) file, one
static UI of four files, environment variables only, no build step, **five runtime dependencies**
(unchanged), and **nine migrations** (`0009`, exactly one, additive).

All prior scope documents and their invariants still hold. On a conflict, this document wins on
*scope*, the build prompt wins on *process and quality*,
[`../security/threat-model.md`](../security/threat-model.md) wins on *security posture*, and
[`../architecture/MODULE-ARCHITECTURE.md`](../architecture/MODULE-ARCHITECTURE.md) wins on
*placement*. The specification being implemented is
[`../architecture/SHADOW-MODE-0.9-DRAFT.md`](../architecture/SHADOW-MODE-0.9-DRAFT.md); the
evidentiary standard is [`../analysis/PREREGISTRATION-0.9.0.md`](../analysis/PREREGISTRATION-0.9.0.md),
which was written before any result existed and is hash-guarded.

**Delivery model (unchanged).** The repository is read-only to automation; the maintainer takes the
resulting archive and pushes it by hand. No step depends on pushing, on CI running, or on any
external account. Every gate is local and reproducible (`make qa`, `make eval`, `make bias-report`,
`make agreement-report`, `make shadow-report`, a locally built wheel).

---

## 1. In scope — exactly six workstreams, and nothing else

### W0 — the pre-registered analysis plan

`docs/analysis/PREREGISTRATION-0.9.0.md`, written in Phase 1 **before a single number from a model
existed**, and not edited afterwards. It states the hypotheses, the derivation policies, the metrics,
the held-out choice, the floors — and, the part usually omitted, **what will be concluded under every
outcome, including the one where nothing beats the champion and the one where the data is
insufficient.**

Its SHA-256 is recorded in the Phase 1 gate and asserted by `tests/test_preregistration.py`. Editing
the plan after seeing results fails the suite.

### W1 — the champion's own agreement with the operators

**Delivered first and independently of everything else.** No model, no training, no new theory. The
headline is cheap — at bag level the champion's agreement *is* the confirm rate. The deliverable is
the **conditioning**: by bag size, by `storm`, by **mixed versus uniform bag**, by `scope_restricted`,
by operator, and by `capture_provenance` (`current` and `legacy_capture` reported separately and
never averaged), with a clustered interval computed over **bags**.

A sibling CLI subcommand, `python -m netcorenoc dataset agreement` (`make agreement-report`),
deterministic and gated on a fixture exactly as the bias report is. The reasoning for a subcommand
rather than a section of the bias report is DECISIONS #115.

### W2 — the challenger

A deterministic, dependency-free logistic `LinkScorer`: four features and an intercept, fitted by
batch gradient descent with a fixed iteration count and no RNG. It satisfies the v0.6.0 Protocol
**structurally** — no base class, no registry — so per-term explainability is inherited by contract
and `SafeScorer` already wraps it.

Trained in a slow loop **this release creates**, in `maintenance_loop` after `maintenance()` has
returned and released the lock. The fit holds no lock. A training failure degrades training and is
surfaced as an operator warning, exactly as capture failure is.

### W3 — two label-derivation policies, evaluated at partition level

Policies **A** (`confirm` → all pairs positive, `split` → all pairs negative) and **B** (`confirm`
only, `split` discarded), both fitted and both reported. Evaluation is at **partition** level through
`eval/metrics.py`'s `over_merge_rate` and `under_merge_rate`, plus a third, separately named
`split_bag_intact_rate` — because a `split` bag supports no truth partition and folding it into an
over-merge rate would fabricate a denominator.

### W4 — sampled online shadow and offline reconstruction

Both ship, and they are not alternatives. Offline reconstruction measures model quality at no ingest
cost and **cannot** measure training/serving skew, by construction. Online shadow measures real
per-call latency and behaviour under real traffic and says nothing further about quality. **Their
divergence is the skew test**, asserted bit-for-bit on a fixture and reported as a rate on real data.

The deployment chooses the **sampling rate and the duration**, not whether online ever runs.

### W5 — the admission filter, calibration, the security review, and the v0.10.0 specification

The admission filter is defined **before the first model** so it cannot be written around whatever
wins: speed relative to the champion, explainability asserted, determinism across runs and processes,
bounded memory, contract version, no new dependencies — and the **champion is measured against the
same filter** and its numbers published as the reference.

Calibration at bag level: reliability curve and Brier score. Then
`docs/security/SECURITY-REVIEW-0.9.0.md` continuing from **F45**, and
`docs/architecture/HONEST-JUDGE-0.10-DRAFT.md` implementing nothing.

---

## 2. Out of scope — and these are refusals, not omissions

1. **Any promotion mechanism.** Deliberately absent: a release that could promote would be judged by
   the only metric it had, which would be agreement with the champion. v0.11.0's, after v0.10.0
   builds an evaluator worth trusting.
2. **Any path by which a model's opinion reaches an operator** — no route, no UI, no SSE event, no
   field on an existing response. Shadow rows are admin-only like every other dataset read.
3. **The train/test split.** v0.10.0's. This release holds out data to *report* a number and says in
   its own pre-registration that this is not the split.
4. **Active learning.** `acquisition_channel` still gets `organic` on every row. Soliciting labels
   changes the distribution, and doing it before the bias of the organic population is understood
   destroys the baseline it would be measured against.
5. **Policies C and D** from the shadow-mode draft. C needs a learner that accepts cannot-link
   constraints, which logistic regression does not without modification; D needs C. D's
   size-weighting half *is* implemented and is applied to both A and B.
6. **A new runtime dependency**, including an optional extra. Logistic regression over four features
   is arithmetic.
7. **Learning τ.** Held at the champion's 30.0 s. Learning it makes the objective non-convex and the
   fit non-deterministic in the way directive 5 forbids.
8. **Per-archetype models** (v0.12.0), **the external cartridge** (v0.13.0), **a model registry or
   plugin surface** (v0.13.0, specified, not built).
9. **The partial-split affordance.** Still the single highest-leverage UI change for the whole ML
   roadmap, and still a UI release's to make.
10. **Any change to `correlate.py`, `capture.py`'s write path, `learn.py`, `receiver.py`, `rbac/`,
    `shaping/`, the existing migrations, or `scoring.py`'s existing classes.** `scoring.py` gains
    nothing; the challenger lives in its own module.

---

## 3. The invariants this release may not break

Unchanged from v0.8.1 and re-verified at Gate 6:

* **`make eval` byte-identical** — `c2e8a0ced29d9edf986279d41089ddb68e18da65a46bdc7e9f04811e8b9b6f26`.
  The correlation path, the capture path and the ingest path do not change; this release adds a
  reader and a shadow writer.
* **The champion decides everything.** Structurally, not by convention: there is no code path by
  which the challenger becomes the active scorer, and a test asserts it.
* **`make bias-report` deterministic** and byte-stable against its fixture.
* **Zero new runtime dependencies**; **exactly one migration**, `0009`.
* **`mypy --strict`**, the layer test with an empty exemption list, the module-size guard with
  `DEBT_ALLOWLIST` empty, `COHESION_EXEMPT` unchanged at one entry and its ceiling of 580, the
  declaration gate, route order and handler hashes, `Store` method hashes, one connection and one
  lock, the documentation guard, F12/F13, the d3 checksum, and the F34–F44 regression tests unedited.
* **`store/dataset.py` is at 395 of 400.** If this release needs to grow it, it splits it properly
  and records the split; it does not raise a guard.
* **Ingestion is sacred.** Training never runs inside a batch, never holds `store.lock` while
  fitting, and never appears in `receiver.datagram_received`.

---

## 4. What "success" means for this release

Stated here so that the build report cannot redefine it afterwards:

* **The champion-agreement number and its conditioning exist, are deterministic, and are gated.**
* **The sufficiency verdict is reached against pre-registered floors**, and where the corpus is
  insufficient the release says so, trains nothing, and publishes the projection of how long until
  the floors would be met.
* **Both derivation policies are implemented and reported**, whichever way they come out.
* **The two shadow mechanisms agree bit for bit**, and the ingest cost of the online one is measured
  rather than claimed.
* **Nothing the challenger says reaches an operator.**

A release that reports *"not yet, and here is when"* has served the project better than one that
trains something on thirteen `split` bags and calls it a challenger.
