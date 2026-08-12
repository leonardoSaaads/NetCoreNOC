# Build report — v0.10.0, "the honest judge"

## The verdict, first

```
verdict                                  INSUFFICIENT_EVIDENCE
holdout queries                                              0
seal                                     12 incidents of a corpus of 37, INTACT
asserting bags                                     0   (floor 50)
asserting incidents                                0   (floor 30)
split bags                                        13   (floor 50)
mixed bags                                         5   (floor 20)
minimum detectable difference at n = 37        0.298
```

**The corpus cannot support an evaluation. The judge exists and is demonstrated on purpose-built
fixtures. The seal is intact at query count 0.** §7.1 of the ratified pre-registration named this
branch as the expected one, in advance, before any result existed — which is the entire point of
having written it first.

**This is a successful release**, and the reason is worth stating rather than assuming: the release
was never going to produce a better model. It was going to produce the machinery that could one day
tell whether a model is better, plus an honest answer about whether that machinery can be pointed at
this corpus yet. It cannot, and now that is a measurement instead of an intuition.

### The census that produced it

Every figure below reproduces the plan's own §0 census exactly, and each was re-measured by this
build rather than carried forward.

| | |
|---|---:|
| labelled bags | 41 |
| `confirm` / `split` | 28 / 13 |
| **merge-aware incidents** | **37** |
| one-hop incidents (the answer this release replaces) | 37 |
| **reduction from one hop** | **0** |
| unsound merge chains (cycles, non-terminating) | 0 |
| pre-v0.8.0 merges (unrecoverable, **counted not assumed**) | 0 |
| mixed bags / both `split` and mixed | 5 / 1 |
| operators, top share | 3, 34.1 % |
| coverage: full / partial / none / empty | 25 / 6 / **6** / **4** |
| **bags excluded before anything begins** (`none` ∪ `empty`) | **10 of 41** |
| **asserted negative pairs** | **0** |
| `acquisition_channel = 'close'` rows | 0 |
| dataset pairs | 194 341 |
| labelling rate | **not measurable** (41 verdicts over a 40-second span) |

### What would have to change

1. **Asserting bags: 0 → 50.** Not *more labels* — **more partial splits**. A `split` on a
   one-member bag asserts nothing, and eleven of the thirteen `split` bags in this corpus have fewer
   than two members. The measurement that makes this concrete: a control corpus built by the same
   rule, marking two members on **every** eligible split, yields **two** asserting bags, because only
   two of the thirteen have four or more members.
2. **Asserting incidents: 0 → 30.** Follows from (1), and cannot be reached by re-labelling the same
   incidents.
3. **The holdout spent.** v0.10.0 does not spend it, deliberately. Whether v0.11.0 should is a
   decision that release must register **in advance**.
4. **A detection threshold below the observed difference.** At 37 incidents the threshold is 0.298.
   Reaching 0.16 needs roughly **120** incidents; 0.10 needs roughly **300**.

**The projection is `undefined`, and prints as `undefined`.** This repository has no labelling-rate
data at all — the harness applies 41 verdicts one second apart and every closed situation was closed
by the idle sweep. Printing the harness's rate would be manufacturing a number, so the release
prints the word and says it is one only a deployment can produce.

---

## 1. The strongest result in the chain, stated in the plan's own terms

`PREREGISTRATION-0.10.0.md` §2.6(b) asks that this be said in these terms, and it is the sentence
that justifies three releases of work:

> A bag **is** a situation; every member of a situation is in one component under the incumbent; so
> a `marked × rest` pair is **necessarily a pair the incumbent joined and an operator says it should
> not have.**

v0.9.0 measured that the discriminating population was missing: the champion accepts **99.83 %** of
what it evaluates, and its rejections live in quiet traffic that attracts no labels. **The exclusion
set manufactures that population directly**, out of the one place it can be found — the operator's
disagreement. That is what v0.9.1 built, v0.9.2 made trustworthy, and v0.10.0 learned to judge.

**And the corpus contains none of it.** The mechanism is sound and the volume is zero.

---

## 2. What was built

| module | what it owns |
|---|---|
| `incidents.py` | incident identity: `merged_into` followed to a fixed point, cycle guard, depth guard |
| `census.py` | what the labelled corpus contains, including the one-hop count it replaces |
| `seal.py` / `store/seal.py` | the holdout: construct once, ratify, **spend**, summarise |
| `shadow_cv.py` | grouped repeated CV, cluster bootstrap, the power condition |
| `shadow_assertions.py` | `asserted_negative_respected_rate` — the fourth named quantity |
| `shadow_admission.py` | the admission filter, split out of `shadow_eval.py` |
| `judge.py` | the three-valued verdict and every §6.2 trigger |
| migration `0012` | `holdout_seal`, `holdout_seal_member`, `holdout_access` — **seeding nothing** |

**Five modules were split rather than exempted.** `shadow.py` (417), `training.py` (408) and
`shadow_eval.py` (394, then 410) each crossed the 400-line guard during the build, and each was
split on a seam that already existed. `DEBT_ALLOWLIST` is still **empty**; `COHESION_EXEMPT` is
still `engine.py` alone at 580; `engine.py` is unchanged at 569.

---

## 3. The three facts, reproduced — and the one that did not

**Fact 1a — the cluster bootstrap.** Reproduced: 12 → 0.500, 37 → 0.297, 50 → 0.260, 100 → 0.180,
500 → 0.080 against a registered 0.500 / 0.289 / 0.246 / 0.180 / 0.079. **Reproduced by the shipped
bootstrap**, not by a throwaway script, which is worth more.

**Fact 2 — adaptive selection.** Reproduced **exactly, on all six figures to three decimals**:
12/4 → +0.133 / +0.217; 37/12 → **+0.111 / +0.165**; 120/12 → +0.067 / +0.100. The mechanism is not
merely plausible-looking; it is the plan's mechanism. Control: one query gives a median inflation of
**+0.0027**.

**Fact 3 — the corpus.** Every census figure reproduced. The control that matters: the same census
code over a corpus that *does* assert returns **1 474 pairs and 2 asserting bags**, so the zero is a
property of the corpus and not of the query.

### Fact 1b did not reproduce, and that is reported rather than resolved

| incidents | plan | closed form | independent Monte-Carlo |
|---:|---:|---:|---:|
| 12 | 0.42 | **0.524** | **0.52** |
| **37** | **0.25** | **0.298** | **0.33** |
| 120 | 0.16 | 0.166 | 0.18 |
| 300 | 0.10 | 0.105 | 0.11 |

**Two independent methods agree with each other and disagree with the plan below `n = 120`.**
Neither side was adjusted: tuning the closed form until it matched would be fitting a formula to a
table, and the plan is ratified and hash-guarded. Carried to the security review as an opinion for
v0.11.0, which is where §9 of the plan directs a disagreement.

**The direction strengthens the plan.** If the true threshold at 37 incidents is 30 p.p. rather than
25, then *"no plausible pair of scorers differs by that"* is more true. Nothing registered depends
on 25 being the right number.

---

## 4. The four defences of the holdout, and the honest limit on the fourth

1. **Constructed once** — `holdout_seal.singleton` is `UNIQUE` and pinned to 1, so the refusal is
   SQLite's rather than a caller's. The code inserts and lets the database say no.
2. **Structurally unreadable from the estimator** — the estimator does not import the module, and
   **exactly one function may call the one expression that returns the membership**, asserted over
   every `.py` by AST.
3. **Every access is a row**, refusals included.
4. **A read requires a ratified plan already on record** — and this one has a limit, stated in the
   module rather than implied: *it cannot stop a determined author registering and reading in the
   same minute.* What it does is make that sequence permanently visible in an append-only log, which
   is the whole of what a **process** hazard can be defended against by mechanism.

**`HONEST-JUDGE-0.10-DRAFT.md` §4 asked that this release say plainly which of its three procedural
defences were actually done.** All three: a pre-registered plan per release, hash-guarded, ratified
in a commit that changed nothing else and tagged; a durable query count rather than a hand-kept
prose counter; and a fresh held-out set reserved from this release and **not spent**.

---

## 5. What was found, and by what

**Nothing in this section was found by reading the code.**

| finding | found by |
|---|---|
| a bag with **no partition** scored 1.0 on the new metric — every assertion "respected" | the mutation ledger (M3) |
| the bootstrap's LCG used an LCG's **low bits**, giving a **zero-width interval** | a test (A11) |
| `judge()` had **inlined** the power condition — a second implementation of a quantity that is both a printed number and a verdict trigger | the dead-code gate |
| the report never said `INSUFFICIENT_EVIDENCE` is a measurement rather than a finding | the dead-code gate |
| the pairing guard could be satisfied by **prose about** the threshold rather than by printing it | the mutation ledger (M6) |
| `test_upgrade.py` does **not** catch a migration's SQL being edited — 1093 passed with the predicate deleted | a probe with a control (F49) |
| §10's observable-pair interval was **wrong twice over**, and had reached **six** places, not the two the brief believed | exhaustive enumeration |

**Three of these came from the dead-code gate and two from the mutation ledger.** Both instruments
were treated as sources of evidence rather than as chores, and **neither was ever answered with an
allowlist entry**: `DEBT_ALLOWLIST` is empty and `vulture_allowlist.py` did not grow.

---

## 6. What this release does not claim

1. **No claim about model quality, in either direction.** `INSUFFICIENT_EVIDENCE` is a measurement
   of the corpus. It is **not** a finding that the challenger is no better; those are opposite
   claims and the three-valued type exists so they cannot be conflated.
2. **No claim that the judge works on real data.** Four of its mechanisms — the judge, the fourth
   metric, the blind-fraction rule and the transitive incident resolution — are demonstrated **only**
   on purpose-built fixtures, because the corpus supplies zero asserted negatives, has every merge
   chain one hop deep, and has an empty `checked` scope population. **Fixtures prove semantics; they
   do not prove the code meets real data, and no test in this repository does.**
3. **No claim that the fold-integrity guard has been watched to fail.** Mutant M7 survived; the
   property is guaranteed by a signature rather than a body, and the guard documents the design
   rather than detecting a regression. Named in the guard demonstrations and in the security review.
4. **No claim that the seal is a usable decider.** Twelve incidents give a detection threshold near
   52 p.p. It protects those incidents from four releases of tuning, which is its whole value, and
   `SECURITY-REVIEW-0.10.0.md` §3.3 argues it should have been cut by a **timestamp** rather than a
   **count** — offered as an opinion for v0.11.0 to register in advance.
5. **No claim that the query count is tamper-evident.** `holdout_access` is append-only by trigger,
   not hash-chained. It stops the application, not somebody with the file.

---

## 7. Verification

```
ruff / ruff format          clean
mypy --strict               clean, 159 source files
vulture                     clean, allowlist unchanged
pytest                      1174 passed
make eval                   c2e8a0ced29d9edf986279d41089ddb68e18da65a46bdc7e9f04811e8b9b6f26
                            byte-identical, unchanged since v0.7.0
migrations                  0001-0012, exactly one added
runtime dependencies        5, unchanged
routes / capabilities / audit actions / served paths   unchanged
engine.py                   569 lines, unchanged
DEBT_ALLOWLIST              empty
COHESION_EXEMPT             engine.py alone, at 580
both pre-registrations      unmodified since Phase 0, proven by hash
upgrade from a live v0.9.2 database   0012 applies, every row intact,
                            audit chain final hash 3eac4858…f02076f before and after
```

**Guards: 28 injections, 3 survivors, all three named and dispositioned**
([`../gates/v0.10.0-guard-demonstrations.md`](../gates/v0.10.0-guard-demonstrations.md)).

---

## 8. The tags

Neither can be pushed from the build environment.
[`TAG-RECOVERY.md`](TAG-RECOVERY.md) carries both, alongside the **six** historical releases that
have no immutable reference on the remote — `v0.7.4`, `v0.7.5`, `v0.8.0`, `v0.8.1`, `v0.9.0` and
`v0.9.1`. The brief listed five and did not include the first two.

| tag | points at |
|---|---|
| `v0.10.0-gate0` | `6b1c73a` — the commit adding the ratified plan **and nothing else** |
| `v0.10.0` | the merge commit on `main` |
