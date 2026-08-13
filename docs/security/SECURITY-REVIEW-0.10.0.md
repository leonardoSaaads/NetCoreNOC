# Security review — v0.10.0

Continues from **F48**. This release issues **F49**. The next finding this project issues, in any
release, is **F50**.

v0.10.0 adds an **evaluator**. It adds **no route, no capability, no audit action, no dependency and
no served path**, `make eval` is byte-identical, and the correlation, capture and ingest paths are
untouched. The security surface it does add is of one kind: **a store that now holds a decision
about which evidence will be allowed to decide something later**, and an appliance that must not be
able to quietly change or spend it.

---

## 1. F49 — a migration's pinned SQL is not tied to the migration

**Severity: low (evidence integrity). Confidentiality: nil. Availability: nil. Privilege escalation:
no. Production change required: none.**

### 1.1 The defect

`tests/test_evidence_boundary_f48.py` pins migration `0011`'s backfill expression as a **constant**,
because a migration cannot be re-run against an already-migrated database. **Nothing ties that
constant to `src/netcorenoc/migrations/0011_evidence_boundary.sql`.** The test file's own docstring
asserts that `tests/test_upgrade.py` catches the drift.

### 1.2 The reproduction — measured, not reasoned about

`AND m.source = 'server'` deleted from `0011_evidence_boundary.sql` **itself**:

```
$ python -m pytest tests/test_upgrade.py -q
7 passed in 2.15s
$ python -m pytest -q
1093 passed in 270.29s (0:04:30)
```

**The whole tree passes.** The cause is specific: `test_upgrade.py`'s v0.9.1 fixture writes its
exclusion through `Store.add_feedback_exclusion` with **no client fingerprint**, so the label carries
no `feedback_member(source='client')` rows. With nothing on the untrusted side of the join, the
predicate has nothing to exclude and deleting it changes no value. **The fixture is a correct upgrade
fixture and an inert probe for this predicate.**

### 1.3 Why it is rated low, and what it would take to matter

An applied migration is never re-run, so the shipped text of `0011` cannot affect any database that
has already passed schema 11. The exposure is to a **future** installation upgrading from v0.9.1 or
earlier against a tree whose migration had been altered — which requires an attacker who can already
modify the package, at which point this is not the interesting problem.

**It is nonetheless a real hole in an evidence-integrity guard**, and it is exactly the class F48
exists to close: *the predicate is demonstrated in two of the three places it appears, and the third
is proved open.*

### 1.4 The repair, and why it is not in this release

The right repair is a guard for the **class** — every migration whose SQL a test pins by copy should
have that copy checked against the file — and it needs a fixture carrying **both** member sources,
which `test_upgrade.py` does not have. Gate 0 was fenced to four items and Part VII rule 7 sends a
newly revealed defect to a ROADMAP line and a finding number rather than into an unrelated commit.
Carried as a [`../ROADMAP.md`](../ROADMAP.md) line.

---

## 2. What was assessed, and how

### 2.1 The evaluator cannot reach the active scoring path

**By construction**: nothing in `shadow_cv.py`, `judge.py`, `seal.py`, `census.py`,
`shadow_assertions.py` or `incidents.py` imports `scoring`, touches `Engine.scorer`, or writes a
`scorer_config` row. **By test**: `tests/test_challenger.py`'s two enumerations — which parse the
tree rather than reading it — cover the new modules, and the allowlist they carry grew only by a
**split of an already-allowed module**, which *widens* what the guard covers.

**And the strongest form of it: there is no route.** v0.10.0 adds no HTTP surface at all, so no
principal at any tier can reach any of this over the network.

### 2.2 The seal cannot be read, rebuilt, or read without a recorded plan hash

| property | mechanism | demonstrated |
|---|---|---|
| cannot be rebuilt | `holdout_seal.singleton` `UNIQUE CHECK (singleton = 1)` — the refusal is **SQLite's** | P4-B |
| cannot be edited or deleted | append-only triggers on all three tables | Gate 3 §4 |
| unreadable from the estimator | the estimator does not import the module; **exactly one function may call the one expression that returns the membership**, asserted over every `.py` by AST | P4-A, P4-A2 |
| unreadable without a plan hash | `seal.spend` refuses and **logs the refusal** | P4-D |
| every access is a row | including refusals, with `granted` distinguishing them | P4-C |

**The honest limit, stated in the module rather than implied**: none of this stops a determined
author registering a plan and reading in the same minute. What it does is make that sequence
**permanently visible** in an append-only log — which is the whole of what a *process* hazard can be
defended against by mechanism.

### 2.3 Every evaluator read is admin-only, like every other dataset read

There is no route, so the only reach is the CLI (`python -m netcorenoc dataset shadow`), which
requires filesystem access to the database — the same posture `dataset bias` and `dataset agreement`
have had since v0.8.0. `store/seal.py`'s docstring records that **every method in it is a scope
bypass by construction**, exactly as `store/shadow.py`'s and `store/dataset.py`'s are.

### 2.4 An evaluator failure cannot stall or fail ingestion

`maintenance._seal_once` runs in `maintenance_loop`, **after `maintenance()` has returned and
released `store.lock`**, on the point v0.9.0 had to create. It takes the lock itself for the
duration of one read and one write, and wraps everything in `except Exception → shadow._degrade`,
which counts the error, surfaces an operator warning, and **never re-raises** — `Capture._degrade`'s
contract, unchanged.

**Proven by injection**: a seal construction that raises leaves the maintenance pass and the
ingestion behind it untouched, and the appliance keeps correlating with the built-in scorer. The
steady state exercises the same path: **every** training tick after the first attempts a
construction and is refused by the `UNIQUE` constraint, so the failure path runs continuously in
normal operation rather than only in a test.

### 2.5 No route, capability, audit action or dependency was added

| | before | after |
|---|---|---|
| routes | unchanged | **unchanged** (`tests/test_declaration.py`) |
| capabilities | unchanged | **unchanged** (`tests/test_rbac.py`) |
| audit actions | frozen catalog | **unchanged** (`tests/test_audit.py`) — see §3.4 |
| runtime dependencies | 5 | **5** |
| served paths | unchanged | **unchanged** |
| migrations | `0001`–`0011` | `0001`–**`0012`**, additive, forward-only, seeding nothing |

---

## 3. Critical analysis

### 3.1 What the verdict does and does not license

**A reader who sees only the headline must not be able to misread it, so it is stated here in the
form that cannot be:**

> **`INSUFFICIENT_EVIDENCE` is not a finding that the challenger is no better.** It is a measurement
> of the **corpus**. The two are opposite claims and this release does not conflate them — which is
> why the verdict type has three values and why the report prints the distinction in words.

What the verdict licenses: **nothing about model quality, in either direction.** No promotion, no
rejection, no ranking, no statement that the built-in scorer is or is not adequate. The correct
summary of this release's analytical output is: *the corpus cannot support an evaluation.*

What it does license: a claim about the **machinery**. The judge exists, every trigger is reachable
and individually fired, the seal is intact at query count 0, and the metric set is four named
quantities that are never composed. Those are claims about code, demonstrated on fixtures, and the
build report says so in those terms.

### 3.2 Which pre-registered threshold I would argue is wrong — **an opinion for v0.11.0**

> ---
>
> **CORRECTION, 2026-08-13 (v0.10.1). This section reached the wrong conclusion, and the correction
> runs in the opposite direction to the one it argues for below.** The plan's small-`n` figures were
> not optimistic. **The closed form was pessimistic**, because it gave *both* arms the base rate's
> variance `p(1−p)` when the second arm sits at `p + delta` with a far smaller one — at `n = 37`,
> 0.210 against **0.058**. Corrected by fixed-point solve, the form returns 0.238 at `n = 37`, 0.149
> at 120 and 0.099 at 300, against a genuinely independent Monte-Carlo's 0.240, 0.150 and 0.099 and
> the plan's registered 0.25, 0.16 and 0.10. **The plan reproduces.**
>
> The Monte-Carlo quoted below at 0.33 did not corroborate the closed form independently; two
> methods that share an assumption are one method. The `p = 0.821` diagnostic was a real symptom
> read as evidence for the wrong cause.
>
> **DECISIONS #154** supersedes #142 and carries the full measurement. The plan was not edited then
> and is not edited now. Nothing below is deleted: a review that quietly changes its mind is worse
> than one that shows the change.
>
> ---

Plan §9 sends a disagreement here rather than into an edit, and this is the one.

**§3.1's minimum-detectable-difference table does not reproduce below `n = 120`.** A documented
closed form and an independent Monte-Carlo power search — sharing no arithmetic — agree with each
other and disagree with the plan: at `n = 37`, **0.298** and **0.33** against a registered **0.25**;
at `n = 12`, **0.524** and **0.52** against **0.42**. At 120 and 300 all three agree to a rounding
step.

**Neither side was adjusted** (DECISIONS #142). Tuning the closed form until it matched would be
fitting a formula to a table, and the plan is ratified and hash-guarded.

**My opinion for v0.11.0**: the plan's small-`n` figures are optimistic and should be re-derived,
with the simulation's assumptions written down this time. The diagnostic is that the `p` making the
closed form return 0.25 at `n = 37` is **0.821**, and at `n = 12` for 0.42 it is **0.832** — close
to each other and far from the ≈ 0.72 implied by the large-`n` rows. **The two halves of the table
behave as though produced under different assumptions**, and a table whose provenance is not
recorded is a table a later release will re-derive from scratch anyway.

**Why this does not weaken the release.** The disagreement runs in the direction that makes the
plan's own conclusion *stronger*: if the true threshold at 37 incidents is 30 p.p. rather than 25,
then *"no plausible pair of scorers over the same three features differs by that"* is more true, not
less. Nothing registered depends on 25 being right.

### 3.3 Is the seal's construction defensible given how few incidents it holds?

**Twelve.** A third of 37. Honestly: **as a decider, no — and the release says so rather than
implying otherwise.**

Twelve incidents give a bootstrap interval roughly **0.50 wide** and a detection threshold near
**52 p.p.** A holdout that could never resolve anything is not a decider; it is a ritual.

**So why cut it at all?** Because of the asymmetry, which is the only argument and is sufficient:
**reserving later is impossible; spending later is always possible.** A seal cut today over 12
incidents can be re-cut over 300 by a release that has 300 — by construction it cannot, since
`singleton` refuses — but *the incidents it holds are protected from four releases of tuning in the
meantime*, and that is the property adaptive selection destroys. The corpus of v0.13.0 will contain
these 12 incidents whether or not they were sealed; the question is only whether they will have been
looked at twelve times first.

**What I would do differently with the corpus I actually found.** I would seal by a **rule** rather
than by a **count**: *"every incident whose earliest label falls after timestamp T"*, with `T` fixed
now and the membership materialised — so the seal grows as the corpus grows and reaches a usable `n`
without ever being re-cut. That is a real design and it is **not** what the plan registered, and
registering it now, after seeing that 12 is too few, would be exactly the move pre-registration
exists to prevent. It is written down here so v0.11.0 can register it **in advance**.

### 3.4 The audit catalog was again not opened, and this time it is a larger gap

v0.9.2 §4.6 recorded that the drift check writes no audit row because the catalog is frozen.
**v0.10.0 makes that gap wider**: the seal's construction and every access to it are recorded in
`holdout_access`, which is append-only but is **not hash-chained** and is **not** the audit log.

An appliance audited from `audit_log` alone therefore cannot see that a holdout was constructed, or
spent, or that somebody tried. That is a real hole in the audit story, and the argument for
`holdout_access` being separate (DECISIONS #148) — no principal performs it, nothing is reachable
from a route — is sound about *attribution* and does not answer *tamper-evidence*.

`holdout_access` is protected by triggers, which stop the application; it is not protected by a hash
chain, which is what stops somebody with the file. **A release that wanted the query count to be
trustworthy against an adversary with disk access would need to chain it**, and this release does
not. Named here rather than in a footnote.

### 3.5 The guard I am least confident in

**`test_no_incident_spans_two_folds`.** Mutant M7 survived, and the reason is that the property is
guaranteed by a **signature** — `assign_folds` takes incidents, so a row-wise split is inexpressible
— rather than by a body a defect could be injected into. It therefore **cannot fail while the
signature stands**, and I have not watched it fail.

It is kept, with a control asserting the assignment actually *spreads* incidents, and it is named in
`v0.10.0-guard-demonstrations.md` Part 2 as *documentation of the design rather than a detector of a
regression*. A guard I cannot make go red is a guard I have to describe honestly rather than count.

**Second place**: `test_the_estimator_and_the_training_path_cannot_reach_the_seal` enumerates modules
by **name**. A new estimator module added in v0.11.0 would not be on the list, and the guard would
pass while the property failed. The list is not derived from anything; it is a list.

### 3.6 A fourth note: the corpus exercises almost none of this

Four of this release's mechanisms — the judge, the fourth metric, the blind-fraction rule and the
transitive incident resolution — are demonstrated **only** on purpose-built fixtures, because Gate 1
measured that the fullest corpus this repository can construct supplies **zero** asserted negative
pairs, has **every merge chain one hop deep**, and has an **empty `checked` scope population**.

Fixtures prove semantics. They do not prove the code meets real data, and **no test in this
repository does**. v0.9.1 met the same wall and said the same thing; v0.10.0 has more mechanism
resting on it.

---

## 4. What did not change

| Property | Evidence |
|---|---|
| `make eval` byte-identical | `c2e8a0ced29d9edf986279d41089ddb68e18da65a46bdc7e9f04811e8b9b6f26`, unchanged since v0.7.0 |
| correlation / capture / ingest paths | untouched; `engine.py` unchanged at 569 lines |
| no promotion mechanism | `seal.spend` has **no caller in the package**, asserted by AST |
| no new route / capability / audit action / served path | `tests/test_declaration.py`, `tests/test_rbac.py`, `tests/test_audit.py` |
| no new runtime dependency | five, unchanged |
| F34–F48 regression tests | unedited and green |
| both pre-registrations | unmodified since Phase 0, proven by hash |
