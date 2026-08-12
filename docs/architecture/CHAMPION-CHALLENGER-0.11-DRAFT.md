# Champion / challenger — v0.11.0 draft (specification only, not implemented in v0.10.0)

<!-- release-claim: v0.11.0 = champion-challenger -->

**Implement none of this in v0.10.0.** Every element below is tagged **`v0.11.0: planned`**. A
release that could promote would be judged by the only metric it had, and v0.10.0 is the release
that built that metric — so it must not also be the release that acts on it.

Written **from what v0.10.0 measured**, not from what it hoped. Where the two differ, the
measurement wins and the difference is recorded.

Its parent is [`ROADMAP-0.8-TO-0.13.md`](ROADMAP-0.8-TO-0.13.md); the evidentiary discipline it
inherits is [`../analysis/PREREGISTRATION-0.10.0.md`](../analysis/PREREGISTRATION-0.10.0.md); the
constraints it must not relax are [`HONEST-JUDGE-0.10-DRAFT.md`](HONEST-JUDGE-0.10-DRAFT.md) §0.

---

## 0. The two things that may never be merged or skipped (`v0.11.0: planned`)

`ROADMAP-0.8-TO-0.13.md` names them and this draft repeats them because v0.11.0 is where the first
one becomes expensive:

1. **Human approval.** The slow loop *proposes*; an **admin approves**; the swap is an immutable
   row. There is no configuration that makes promotion automatic, and adding one is not a v0.11.0
   decision to make quietly.
2. **ONNX last.** v0.13.0, behind a worker-process harness. Nothing here creates a plugin surface.

## 1. Promotion is a pointer move in `scorer_config` (`v0.11.0: planned`)

`scorer_config` (migration `0005`) is already a versioned, audited, rollback-able record of *which
parameters the correlator is running*. **Promotion is that table gaining a row**, not a new
mechanism:

* the challenger's coefficients are written as a new `scorer_config` row;
* the engine loads it exactly as it loads a retune today — `scorer_lifecycle` is unchanged;
* rollback is the mechanism that already exists, because a promotion is a configuration change and
  this project has had change control for one since v0.6.0.

**What must be added** is the *provenance*: which `challenger_run` produced the coefficients, which
`Judgement` authorised them, and which admin approved. A promotion whose evidence cannot be named
afterwards is a retune with a better story.

## 2. The two refusals, specified here and required to differ (`v0.11.0: planned`)

> **Promotion must refuse on `INSUFFICIENT_EVIDENCE` and on `NOT_BETTER` by different code paths
> with different messages**, so no future reader can mistake one for the other.

`PREREGISTRATION-0.10.0.md` §6.2 registers this and v0.10.0 built the type that makes it possible:
`netcorenoc.judge.Verdict` has three members and `Judgement.decisive` is `False` for exactly one of
them.

| verdict | refusal | the message must say |
|---|---|---|
| `INSUFFICIENT_EVIDENCE` | **the corpus cannot decide** | which §6.2 triggers fired, the detection threshold at the available `n`, and what would have to change |
| `NOT_BETTER` | **the corpus decided, against the challenger** | which of the four named quantities produced the verdict, with its interval and the champion's number beside it |

**They are opposite claims.** A single `if not judgement.decisive or verdict is not BETTER: refuse()`
would satisfy a naive test and destroy the distinction — which is the collapse the three-valued type
exists to prevent, one layer up. **Two code paths, and a test that each is reachable and that
neither message can be produced by the other's condition.**

**A third refusal v0.10.0 did not anticipate and v0.11.0 will need:** the holdout is **unspent**, so
`Trigger.HOLDOUT_UNSPENT` fires on every corpus. v0.11.0 must decide, *in its own pre-registration
and in advance*, whether promotion requires the holdout to have been spent — and if it does, then
v0.11.0 is the release that spends it, once, and every number it prints afterwards carries a query
count of 1.

## 3. What v0.11.0 inherits from v0.10.0's seal and access log (`v0.11.0: planned`)

**Mechanisms**, all of which exist and none of which need building again:

* the **seal**, constructed once and refused thereafter by `holdout_seal.singleton`;
* `seal.ratify` — v0.11.0 records its own ratified plan hash **before** any read, and the row is
  append-only and timestamped, so *"registered before the read"* is a durable fact;
* `seal.spend` — the one access path, which logs granted and refused alike;
* `Store.query_count` — **0** today, and §4.3(4) requires it printed beside every holdout number
  v0.11.0 ever publishes.

**Facts, which are the harder inheritance:**

* **The seal holds twelve incidents.** A third of 37. It gives a bootstrap interval near 0.50 and a
  detection threshold near 52 p.p. **As a decider it cannot resolve anything**, and
  `../security/SECURITY-REVIEW-0.10.0.md` §3.3 says so plainly. v0.11.0 must decide what to do about
  that in advance, and the option that is **not** available is re-cutting the seal.
* **The corpus supplies zero asserted negative pairs**, and only **two** bags would assert anything
  even if the labelling rule marked everything it could. Every floor of §2.2 is unmet by a wide
  margin.
* **The plan's detection-threshold table does not reproduce below `n = 120`**
  (`../gates/v0.10.0-phase-1.md` §2). v0.11.0 should re-derive it and **record the simulation's
  assumptions this time**.
* **`incumbent_linked` is still not a target**, including as a feature. v0.11.0 is the release that
  will be most tempted, because a promotion gate wants data.

## 4. `scorer_config` becomes a model inventory (`v0.11.0: planned`)

Today it holds parameter sets. After promotion exists it holds **models with provenance**, and the
questions an operator will ask of it are different:

* *what is running, and what authorised it?* — `challenger_run` id, `Judgement`, approving admin;
* *what has run before, and why did it stop?* — the row it replaced and the reason;
* *what was proposed and refused?* — **a refused promotion should leave a row too**, for the reason
  `holdout_access` records refusals: a table of successes answers *"what is deployed"* and not
  *"what has this appliance been asked to deploy"*, and the second is the audit question.

**Not a plugin registry.** v0.13.0 owns the external cartridge, and a model inventory that grew an
`adapter` column would be that release starting early.

## 5. Explicitly not in v0.11.0

1. **Automatic promotion.** §0.
2. **Per-archetype models** (v0.12.0) and **the external cartridge** (v0.13.0).
3. **A composite quality score.** Four named quantities, never composed —
   `PREREGISTRATION-0.10.0.md` §5, and adopting a composite index would reverse that decision under
   a different name.
4. **Re-cutting the seal.** Structurally impossible, and it should stay that way.
5. **Relaxing `incumbent_linked`.** If v0.11.0 believes it has earned the relaxation, that belief
   belongs in its **pre-registration**, before the numbers.

## 6. What v0.11.0's pre-registration must decide in advance

Listed here so it is not discovered mid-build, which is what v0.10.0's own §10 prerequisites were
for:

* whether promotion requires the holdout to have been spent — and if so, that v0.11.0 spends it;
* what a promotion's evidence must contain, and what a refused one records;
* whether the seal's construction rule should be re-registered as a **timestamp** rather than a
  **count** (`../security/SECURITY-REVIEW-0.10.0.md` §3.3 argues it should, **as an opinion offered
  in advance** rather than as a change to a ratified plan);
* whether `holdout_access` should be hash-chained (§3.4 of the same review);
* the corrected detection-threshold table, with its assumptions.
