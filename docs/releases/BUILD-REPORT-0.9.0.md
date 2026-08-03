# Build report — NetCoreNOC v0.9.0 — "shadow mode"

## The two numbers, first

This release exists to produce two numbers. Neither is a model, and one of them came back *no*.

> ### 1. How well does the champion already agree with the operators?
>
> **This project cannot answer that yet, and the honest report of why is the finding.**
>
> The instrument is built, deterministic and gated: `python -m netcorenoc dataset agreement`,
> conditioned six ways, with a cluster bootstrap over incidents that is *refused* rather than
> narrowed below ten of them. Run over the fullest corpus this repository can construct it printed
> **68.3 % [54.8, 81.1] over 41 bags and 37 incidents** — and that number **measures the driver, not
> operators**: those verdicts came from a mechanical rule (every third situation `split`) declared
> in Phase 0 before the report existed, and a rule that splits one in three produces 66.7 % by
> arithmetic.
>
> **What the corpus does establish, structurally and independently of who labelled it: only 5 of 41
> labelled bags are *mixed*** — bags whose pairs span the threshold, the only kind that contained a
> decision the champion could have got wrong. So roughly **an eighth** of any agreement rate this
> product ever prints is about a judgement, and seven eighths is about arithmetic that could not
> have gone otherwise. That proportion is the shape of the headroom, and it is the most transferable
> thing v0.9.0 measured.

> ### 2. Is there enough signal in the data to learn anything at all?
>
> **No.** Against floors pre-registered in Phase 1, before any result existed:
>
> | floor | required | measured |
> |---|---:|---:|
> | `split` bags | 50 | **13** |
> | mixed bags | 20 | **5** |
> | merge-aware incidents | 30 | 37 ✓ |
> | distinct operators (top ≤ 60 %) | 3 | 3 at 34.1 % ✓ |
>
> And the line that is not in the table: **exactly one bag was both `split` and mixed.** The rows
> where an operator contradicted the champion *about a decision the champion actually made* number
> **one**.
>
> On such a corpus the release **fits nothing**, records a run row saying so, names every unmet
> floor, and projects roughly how many months of labelling at the measured rate would close each
> gap — or prints `undefined` where no rate exists, because an extrapolation from a single instant
> is a fabricated number.

**Both of those are the release succeeding.** A release that reported *"not yet, and here is when"*
has served the project better than one that trained something on thirteen `split` bags and called it
a challenger. The case for v0.10.0 through v0.13.0 now rests on acquiring discriminating labels
deliberately, and `SECURITY-REVIEW-0.9.0.md` §5.2 states the tension that creates with this
release's own refusal of active learning, without resolving it.

---

## What shipped

Six workstreams, and nothing else.

| | |
|---|---|
| **W0** | `docs/analysis/PREREGISTRATION-0.9.0.md` — written before any model existed, stating what would be concluded under **ten** outcomes including insufficiency, and hash-guarded so editing it after a result turns the suite red |
| **W1** | the **champion-agreement report** — no model, delivered first, conditioned six ways, operators anonymised, gated byte-for-byte |
| **W2** | the **challenger** — a deterministic logistic `LinkScorer` satisfied *structurally*; trained in a slow loop this release had to build |
| **W3** | **both** label-derivation policies, evaluated at **partition** level against human verdicts |
| **W4** | **both** shadow mechanisms — sampled online and offline reconstruction — with their divergence as the skew test |
| **W5** | the admission filter (run against the **champion** too), bag-level calibration, the security review, and the v0.10.0 specification |

**Invariants held**: `make eval` byte-identical (`c2e8a0ce…9b6f26`); capture parity at **82.830601**
pair rows per trap on both trees; **five** runtime dependencies; **one** migration; **no route, no
capability, no audit action**; `correlate.py`, `capture.py`, `receiver.py`, `learn.py`, `rbac/`,
`shaping/`, `scoring.py` and every existing migration at a **zero-byte diff**; F34–F44 regression
tests unedited; `DEBT_ALLOWLIST` empty; `COHESION_EXEMPT` one entry, `engine.py` at exactly 580.

---

## Four things measurement found that reading would not have

### 1. The slow loop this release was told to use did not exist

Phase 0 parsed `Engine.maintenance` and found it is a single `async with self.store.lock` block with
**zero statements after it**. The lock is the same `asyncio.Lock` object `_commit_batch` takes. So
there was **no point in the periodic path that ran outside the lock**, and the build prompt's
instruction to train "at the point Phase 0 identified" had no referent.

The release built one: training runs in `maintenance_loop` *after* `maintenance()` returns and
releases the lock, which meant moving that loop out of `engine.py` — at exactly its 580-line ceiling
with zero headroom (DECISIONS #118, #121). Known before a line of training code was written, which
is the entire value of measuring first.

### 2. Policy B is degenerate, not merely weak

The shadow-mode draft called policy B *"throws away the minority class"*. Measured, on bag-level
labels it throws away **the only source of negatives**: a `confirm` bag asserts every pair in it is
positive, so with `split` discarded the target is constant and the best achievable model predicts
"link" unconditionally.

| | over_merge | under_merge | **split_bag_intact** | Brier |
|---|---:|---:|---:|---:|
| champion | 0.0000 | 0.5000 | 0.0000 | — |
| policy A | 0.0000 | 0.0000 | 0.0000 | 0.0052 |
| policy B | 0.0000 | 0.0000 | **1.0000** | 0.5932 |

**Policy B scores perfectly on both headline rates and buries every one of sixty split bags** — two
incidents inside one situation, every time. A release reporting only over-merge and under-merge
would have called it the better model. That is why `split_bag_intact_rate` is reported as a third,
separately named quantity, and why folding split bags into an over-merge rate would have fabricated
a denominator. *(Synthetic fixture; the numbers are about the machinery.)*

### 3. A feature can be registered and still be unbuildable

`same_oid_root` was pre-registered as feature four. `LinkFeatures` carries no trap OID and neither
does `correlate.WindowAlarm`, so it is computable **offline and not online** — and a feature that
cannot be served *guarantees* the training/serving skew the plan exists to detect.

It was not built. The model has **four free parameters, not five**, and the events-per-variable
convention would give a floor of forty. **The floor stayed at the registered fifty**: `resolved = the
more demanding of` runs monotone toward evidence (DECISIONS #114), and lowering a floor because the
model got simpler is the move that rule exists to forbid. Reported in the security review, in the
module docstring and in Gate 4 — **never by editing the plan**, which §9 of the plan directs.

### 4. A correct bound and a visible bound are different properties

At `sample_rate = 1.0` the cost measurement recorded **43 474 dropped opinions of 45 474**: the
in-memory buffer fills long before the maintenance flush. The bound is correct and the drops were
already counted — but nothing told the operator, who would read a 2 000-row truncated prefix as a
census and compute quality figures from a biased sample. It now raises an operator warning. **Found
by executing the measurement Gate 5 required; every individual piece was behaving as designed.**

No F-number: the series tracks defects in *shipped* code, and numbering one that never reached a
user would devalue the numbers that did.

---

## The numbers

| | |
|---|---|
| skew, online vs offline | **0.0000 %** over 2 000 opinions from four corpus scenarios — the pre-registered value exactly, compared with `==` on the float |
| online cost at the 1 % default | 454 opinions against 45 474 captured pair rows (**1.0 %**); **+45 KB** on 5.3 MB (**+0.85 %**); **6.09 ms** of scoring across the whole replay |
| champion vs challenger, per call | `additive` median 1.500 µs / p99 2.527 µs; `logistic-shadow` median 2.397 µs / p99 7.367 µs — **~1.6×**, inside a 10× budget expressed as a *ratio measured in the same process* |
| tests | 855 → **923** (+68) |
| coverage | **96 %**, equal to v0.8.1, after 2 836 new source lines |
| modules added | 7 (`challenger`, `training`, `shadow`, `shadow_eval`, `shadow_report`, `agreement`, `agreement_report`) + `store/shadow` |
| decisions | **#114 – #122** |
| findings | **none issued**; the next release continues from **F45** |

---

## Honest caveats

* **Every quality figure in the shadow report comes from a synthetic fixture.** The real corpus does
  not meet the floors, so there is nothing else it could come from, and the report and the gates
  label them as such. Nobody should read the A-versus-B table as evidence about networks.
* **The 68.3 % agreement figure is not a measurement of operators**, and Gate 2 says so in a boxed
  warning rather than a footnote. A real deployment's number is the one worth quoting; this release
  does not have one.
* **The held-out split is a reporting device, not v0.10.0's split**, over an *n* in the tens. The
  pre-registration says so in its own §5.4, before any number came out of it.
* **The `split`-bag floor is probably the wrong quantity.** `SECURITY-REVIEW-0.9.0.md` §5.4 argues
  the population that carries information is bags that are both `split` **and** mixed — which the
  plan does not floor at all, and which stands at **one**. Recorded as an opinion for v0.10.0's
  plan to accept or reject *in advance*, because acting on it here would mean editing a plan after
  seeing its results.
* **`MIN_INCIDENTS_FOR_INTERVAL = 10` is too permissive.** Over the driven corpus it printed
  `[33.3, 91.7]` on twelve incidents — a range wide enough to contain any conclusion. Thirty would
  have printed `n/a` where this release printed a number.
* **The pre-registration guard makes the plan immutable, not honest.** Its own test docstring says
  so, and names the three things it does not prevent.

---

## Evidence

Gates [0](../gates/v0.9.0-phase-0.md), [1](../gates/v0.9.0-phase-1.md),
[2](../gates/v0.9.0-phase-2.md), [3](../gates/v0.9.0-phase-3.md),
[4](../gates/v0.9.0-phase-4.md), [5](../gates/v0.9.0-phase-5.md),
[6](../gates/v0.9.0-phase-6.md), [7](../gates/v0.9.0-phase-7.md).
Scope: [`SCOPE-0.9.0.md`](../scope/SCOPE-0.9.0.md). Plan:
[`PREREGISTRATION-0.9.0.md`](../analysis/PREREGISTRATION-0.9.0.md). Review:
[`SECURITY-REVIEW-0.9.0.md`](../security/SECURITY-REVIEW-0.9.0.md). Next:
[`HONEST-JUDGE-0.10-DRAFT.md`](../architecture/HONEST-JUDGE-0.10-DRAFT.md).
