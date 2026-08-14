# Build report — v0.11.0 (champion / challenger)

## The verdict, first

**On the real corpus the promotion gate refuses, and the sealed holdout is not read.**

```
verdict          : INSUFFICIENT_EVIDENCE
triggers         : FLOOR_UNMET, HOLDOUT_UNSPENT, THIN_SPLIT, POWER
seal query count : 0
promotions applied / refused : 0 / 1
```

`asserting_bags` is **0** against a registered floor of **50**. The detection threshold at
n = 37 incidents is **0.2384**. The projection is **`undefined`**, because this repository has
measured that it has no labelling-rate data at all.

**`PREREGISTRATION-0.11.0.md` §6.1 predicted this before any of this release's code existed**, and
`v0.11.0-phase-1.md` §1.2 predicted it again from a re-run census **before the gate was built**. The
prediction and the measurement agree. That was the point of writing the plan first.

**This is a successful release**, for the same reason v0.10.0's verdict was one.

---

## What was built

| | |
|---|---|
| Migration | **`0013`** — one, additive, forward-only, **seeding no rows** |
| New tables | `model_version`, `promotion`, `evaluation_fold` |
| Changed table | `scorer_active`, rebuilt with a `CHECK` admitting **exactly one** pointer |
| New runtime modules | `model_version.py`, `promotion.py`, `evaluation_folds.py`, `store/promotion.py`, `api/routes_promotion.py` |
| New routes | 2 — `GET /api/promotion` (viewer+), `POST /api/promotion` (admin) |
| New capabilities | 2 — `promotion.read`, `promotion.write` |
| New audit actions | 4 — `promotion.applied`, `promotion.refused`, `seal.construct`, `seal.spend` |
| New CLI | `netcorenoc promotion list` / `register` |
| UI changes | **0** — not a button, not a field, not a string |
| New runtime dependencies | **0** (still five) |
| ADRs | #160–#165 |
| Findings | **F52**, issued and closed in this release |

---

## The numbers

| Item | v0.10.1 | v0.11.0 |
|---|---|---|
| tests | 1196 | **1302** |
| `mypy --strict` | clean, 161 files | **clean, 172 files** |
| `eval` output hash | `c2e8a0ce…8b9b6f26` | **`c2e8a0ce…8b9b6f26`** — byte-identical |
| migrations | `0001`–`0012` | **`0001`–`0013`** |
| schema `user_version` | 12 | **13** |
| ADR high-water mark | #159 | **#165** |
| findings | F51 | **F52** |
| `src/` modules / lines | 91 / 17 586 | **96 / 19 072** |
| `engine.py` | 569 | **569** — unchanged |
| `scoring.py` | 394 | **394** — byte-identical |
| runtime dependencies | 5 | **5** |
| coverage | 96.10 – 96.21 % (band) | **96.02 %** — 0.08 below the band, explained in Gate 7 §9.1 |
| largest new module | — | `promotion.py`, **400** |

**Determinism**, across two runs in two processes:

| artefact | sha256 |
|---|---|
| `make eval` | `c2e8a0ced29d9edf986279d41089ddb68e18da65a46bdc7e9f04811e8b9b6f26` |
| `make census` | `d4bb4fbfb911869b373c7c89d00432f99d7356521dae4e23e0f6a64449743b29` |
| the real-corpus verdict | `198978a524128cd443b20dd066e8bf47adfcf4f326c10dca49cf6768b47491c2` |

---

## The three things this release is actually for

**1. The refusal is the product.** On this corpus the gate always refuses, so the refusal paths are
the only ones an operator will meet for a long time. There are **two**, they are opposite claims, and
**each raises if handed the other's verdict** — asserted by handing each the other's, not by
comparing strings a rewording would break. A third refusal covers an artefact whose coefficients
cannot be traced to a run.

**2. A refused promotion leaves a row.** With its reason, its triggers enumerated rather than
summarised, the ratified plan hash, the seal's query count, the approving admin, and a fold-assignment
reference that resolves to 111 stored rows. That turns *"what is deployed"* into *"what has this
appliance been asked to deploy, and why was it refused"* — the audit question.

**3. The two refusals were written before the approval.** The plan was ratified in a commit that
changed nothing else, tagged `v0.11.0-gate0`, and its hash is cited by the first implementation
commit.

---

## What went wrong, and what it cost

**F52 — the primary floor's predicate was unguarded.** Widening `asserting_bag_rows`'s
`excluded_reconciled >= 1` to `>= 0` left **all 1 296 tests green** while changing what counts toward
`asserting_bags ≥ 50`. Found only because the build prompt **mandated re-running F48's injection**;
nothing this build chose to try found it. Closed in the same release, and named in the security
review as the measure of how much a self-chosen mutation ledger is worth.

**A missing derivation, found by a fixture that was too convenient.** The first version of the
applied-path test lowered the floors to zero to reach `BETTER`. It still failed — `THIN_SPLIT` fired,
because **the route never derived `smallest_side`**. Seeding sixty genuinely asserting bags instead
of removing the floor exposed it. A fixture that reaches a state by deleting the check is not a
fixture for that state.

**Two modules crossed the 400-line guard** (`promotion.py` at 415, `routes_scorer.py` at 457) and were
**split onto real seams rather than allowlisted** (ADR #165). Trimming further would have meant
deleting reasoning to satisfy a line count.

**A probe reported a wall as a constraint.** Gate 2's `CHECK` demonstration rolled back the
`model_version` row it then referenced, so the control came back *"the CHECK refuses everything"*. A
probe that only checks that bad input fails would have passed.

---

## What this release does **not** claim

* **Not that the challenger is better.** No promotion was applied and none could be.
* **Not that an evaluation is reproducible.** Fold materialisation makes it **citable**. The merge
  graph is still unsnapshotted, retention still does not know what a citation is, and the seal's own
  membership is still not reproducible. Security review §3.3.
* **Not that `incumbent_linked` is guarded by an injection.** No expression reaches it from a
  promotion decision, so the mandated injection could not be performed without writing the defect
  first. Named as a limitation, not as a passed check.
* **Not that the mutation ledger is adequate.** F52 is one measured instance of it missing something
  material; there is no reason to believe it is the only one.

---

## Gates

| Gate | State |
|---|---|
| 0 — the plan ratified, hashed, pinned, tagged; Facts A–C with controls | **closed** |
| 1 — census re-run, scope, ADRs, the Part IV check | **closed** |
| 2 — migration `0013` against a populated database | **closed** |
| 3 — the artefact, the validator, dispatch; additive parity proven twice | **closed** |
| 4 — fold materialisation, and the drift property | **closed** |
| 5 — the gate, both refusals, the approval | **closed** |
| 6 — 17 demonstrated guards, each with a control; the mutation ledger | **closed** |
| 7 — verification | **closed** |
| 8 — review, `ARCHETYPES-0.12-DRAFT.md`, release | **closed** |
