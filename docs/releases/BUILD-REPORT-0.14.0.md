# Build report — v0.14.0, "the model family"

## The thing this release set out to do, and did not

**Drive the whole evidence chain end to end until a champion changes.** The chain ran, end to end,
over real UDP and the real routes. The champion did not change.

| | after ten increments | floor | short by |
|---|---:|---:|---:|
| `asserting_bags` | **10** | 50 | **40** |
| `asserting_incidents` | **10** | 30 | **20** |

`PREREGISTRATION-0.14.0.md` §5.3 registered that branch **before any corpus existed** as one of two
successful stopping conditions, and §8.3 named it: *"the demonstration is incomplete, the shortfall is
reported per floor, and the release ships the three kinds without the end-to-end proof. **This is a
gate outcome, not a failure**, and the report leads with it rather than burying it."*

So it leads with it. It is also worth being precise about what *did* run, because "the champion did
not change" is not the same as "the machinery did not work":

* A real appliance booted on an empty database, migrations applied at boot.
* **855 trap PDUs over a real UDP socket. 0 dropped, 0 quarantined, 0 denied.**
* Situations formed. Three principals labelled them through
  `POST /api/situations/{sid}/feedback`, with RBAC, scope resolution, `Exclusion` reconciliation and
  an audit row on every call.
* Three artefacts — `tree`, `forest`, `gradient_boosting` — fitted on the labelled corpus and
  registered through the CLI.
* `POST /api/promotion` derived every input server-side and **refused all three**, naming four
  triggers each, with the seal unspent.

**The refusal is the machinery working.** What is missing is the other half: nobody has ever observed
a champion change propagate to a subsequent situation's provenance. §8.7 registers the failure mode
and this release has no observation either way.

## What an operator can now do that they could not before

| | |
|---|---|
| Run a **decision tree**, a **random forest** or a **gradient-boosted model** as the correlator | in this process, in pure Python, with the same five dependencies |
| Read **why** any of them grouped two alarms | three exact Shapley contributions, summing to the score minus the base value |
| See **what is actually deciding** on the Link scorer screen | including the artefact, its fingerprint and its hyperparameters |
| Read a promotion decision in full | all four named quantities, **both arms**, with intervals — or "not computable", never a zero |
| **Choose** which artefact to propose | v0.13.0 proposed whichever sorted first, with no selector |
| Register an artefact without being able to promote it | `netcorenoc promotion register`, which prints that it is an artefact every time |

## The numbers

```
scorer kinds            2 -> 5      additive, logistic, tree, forest, gradient_boosting
runtime dependencies    5           unchanged since v0.2.0. Three model kinds added zero
migrations              0001-0013   none added; a kind is a model_version row and always was
tests                   1428 -> 1542
coverage                95.92 % / 95.94 %  two runs, both inside BOTH registered bands
make qa                 green       lint typecheck deadcode scan test eval
mypy (strict)           clean, 202 source files: src, tests, eval, tools
ruff / ruff format      clean, 462 files
python eval/harness.py | sha256sum
                        c2e8a0ced29d9edf986279d41089ddb68e18da65a46bdc7e9f04811e8b9b6f26
                        byte-identical, unchanged since v0.7.0
trap path               byte-identical to v0.13.0, and now pinned by a test
/api                    44 routes, byte-identical in order. Not one added, removed or renamed
served paths            88 -> 90    both new ones static UI modules
guard demonstrations    15 injections, 15 reds, 15 controls
mutation ledger         8 survivors, named
live browser pass       39 checks, after 5 fixes
seal query count        0           on the production tree and on both demonstration databases
```

## What was hard, and what it cost

### The kinds were the easy half, and saying so is the point

A CART over three continuous features is a first-year exercise. `boosting.py` is under 300 lines.
What was hard is that a model cannot enter *this* project the way a model enters most projects: it
has to pass degeneracy rules registered before it existed, decompose its own decision **exactly**,
load through one dispatch, fit byte-identically across two processes, and add zero dependencies.

The release's first real work was proving that `docs/architecture/ROADMAP-0.8-TO-0.13.md` was wrong.
It said tree ensembles *"can only enter through the ONNX door"*, and had said so for two releases.
**Principle 5 forbids dependencies, not implementations** — v0.9.0 had already written logistic
regression in pure Python on exactly that argument. ADR #183 supersedes the bullet without rewriting
it, and the amendment beneath it says what was confused and why.

### Making exact Shapley affordable on the ingest path

Eight coalitions per pair per tree, against a 256-row background set, is not something to compute at
score time. Two exact identities make it a table lookup: **attribution is linear in the model**, so an
ensemble's values are the weighted sum of its members'; and **a tree is constant on the cells its own
thresholds cut**, so there are at most a few hundred distinct answers.

Verified against a brute-force implementation sharing no arithmetic: maximum difference `5.5e-17`,
and the sum identity exact on 5000 of 5000 points. `MAX_CELLS_PER_TREE` **refuses** a model too large
to tabulate; it never approximates, because a sampled Shapley value wearing the exact one's name
would be worse than a refusal.

### The attribution double-counted the base value, and the test that caught it was the honest one

A model whose true score was `0.1` reported `0.996875`. The stored per-cell output was being added to
the base value, which was already inside it. The repair is not a subtraction: **the score is now
*defined* as `base_value + Σφ`**, so `==` in the explainability check is an identity rather than a
coincidence, and the value that decides `linked` is the same one the explanation decomposes.

### The merge chain was depth 1 for two structural reasons, neither guessable

`engine._assign_situation` keeps `min(sids)`, so bridging the lowest pair first gives two edges both
pointing at the survivor — two merges, chain length one. And `learn.entity_affinity` returns zero for
a cross-NE pair until mass reaches `MIN_EDGE_N`, so giving each merge-chain incident *fresh* NEs made
the chain unbuildable: every ring pair was seen once and `E` stayed at zero forever.

`eval/simulation/measure.py` is the only reason anybody knows. It exits non-zero when the longest
chain is below 2, which is what turned a page of plausible design into a measurement.

### Three defects were found by driving the system, and none by a guard

* **F59** — the promotion gate measured `engine.shadow.scorer` and activated the `model_version_id`
  the request named. Nothing bound them. **Three releases** of tests missed it because no test had
  ever proposed a candidate that *differed* from the shadow scorer.
* **F60** — the console reported the coded additive defaults as *"Active configuration"* whenever a
  model version was running. **Two releases**, one of them a console rewrite.
* **F58** — a mass storm defeats `MIN_EDGE_N` for every NE in the window.

Each had been readable for at least two releases. `SECURITY-REVIEW-0.14.0.md` §7.1 says this is an
uncomfortable result for a project heavy on static guards, and it is.

### F58, measured to the last digit and left unfixed

`Learner.observe_pairs` deposits `STORM_DAMPING` (0.1) of mass between the new alarm's NE and **every
distinct other NE in the window**. Fifty-six dying-gasp alarms on one OLT therefore deposit
`56 × 0.1 = 5.6` against the `5.0` the gate requires. Past that gate the normalised PMI saturates at
1.0 — during a storm everything co-occurs with everything — so the only limit left is the evidence
discount `m/(m+1)`.

The measurement is what makes it a finding rather than a story: the observed entity terms are exactly
`5/6, 6/7, 7/8, 8/9`, and the largest is `34/35` beside a recorded pair mass of `34.000`. Two numbers
agreeing to the last digit is where a hypothesis stops being one.

An entire increment of twenty concurrent incidents collapsed into **one 230-member situation holding
24 unrelated faults** — which is why exactly one asserting bag forms per increment, and therefore why
the floors are arithmetically unreachable.

**Not fixed.** The trap path is byte-identical for the whole of v0.14.0, and changing the correlator
after seeing the verdict it produced is adaptive selection one layer below where §5.4 forbids it.

### The mutation ledger earned its keep, again

Ten mutations written expecting some to survive. **Eight survived all 1542 tests**, and six of the
eight are properties a docstring states emphatically and no test asserts — including *"a rate that is
not computable is not a rate of zero"*, which `docs/ROADMAP.md` records as having cost this project a
release once.

The most dangerous is S-E: restoring `contextlib.suppress(OSError)` around the harness's UDP bind —
the exact construct that module's docstring forbids — is caught by nothing, and would have made every
number in Gate 7 about a different corpus.

### One "control" was not a control, and the exercise caught it pointing at itself

Injection 10's control asserted a shortfall of 40 and 20 — **numbers derived from the floors the
injection changed.** It went red alongside the guard. A control that shares a constant with the
injection is not a control, and the only way to find one is to run it.

### Then the finished screens were opened in a browser, and five more defects were on screen

Every structural assertion was green. Four of the five are one defect wearing four hats: `htm`
collapses the newline between a text node and the element after it, so `word\n<code>` rendered as
`wordVALUE` — *"Attribution base value0.477278"*, *"byadmin"*, *"Triggers that fired:FLOOR_UNMET"*.
The fifth was `widgets.TimeCell` rendering a `<td>` inside a `<p>`, which is invalid HTML and lays out
as a block, dropping a full stop alone onto its own line.

**The DOM harness has no layout and no content-model validation.** It cannot see any of this, and it
never could. ADR #182's rule — *a UI release gets one pass with eyes on it* — turns out to generalise:
**a release that adds a screen inherits the rule, whatever else it is about.**

## Honest notes

### 1. The demonstration did not reach its stated goal, and that is the release's headline

Not a footnote. The plan registered the branch, the branch is a success by the plan's own definition,
and the release still did not do the thing it set out to do. Both sentences are true and the second
one is the one a reader would want said out loud.

### 2. The corpus shape may be wrong, and this release could not say so

Driving twenty incidents concurrently is what lets one storm's co-occurrence reach every other
incident in the window. Separating them in time would very probably have cleared both floors — and
changing it after seeing the shortfall is exactly what §5.4 exists to forbid.

**The two designs measure different things**, which is the release's finding about its own plan:
concurrency measures the appliance under NOC-at-3-a.m. load (and found F58); spacing measures the
promotion machinery (which is what the floors count). v0.14.0's plan registered one shape and got
both questions half-answered. `SECURITY-REVIEW-0.14.0.md` §7.4.

### 3. A correction that corrected nothing

The simulated operator's truth lookup was re-keyed from the generator's `entity_key` to
`(device, trap OID)` on sound reasoning. **The re-run was byte-identical.** The shortfall it was meant
to explain was never caused by it. Recorded in `labelling.truth_of`'s docstring and in Gate 7 §3.6,
because a fix that fixes nothing is worth exactly one sentence saying so — and the alternative is a
reader finding the reasoning later and believing the problem was solved.

### 4. Coverage is 95.92 % and every surviving mutation lives in a covered line

Eight behaviours, all unasserted, all in files the coverage report counts as exercised. Coverage and
assertion are different measurements and only one of them is reported as a percentage.

### 5. The guard I am least confident in

`test_the_increment_ceiling_is_the_registered_ten` asserts that the string `--increments` does not
appear in `drive.py`. That is a text search standing in for a property, and it would pass a loop that
read the ceiling from an environment variable. The constant itself is asserted beside it, so the
guard is not *only* the string — but the string half is the half that would quietly stop meaning
anything.

### 6. One test flapped once and I do not know why

`test_the_report_is_deterministic_across_two_runs` failed in a full run that shared the machine with
a headless browser drive. It passes in isolation, and a clean full run of all 1542 tests immediately
afterwards was green. **I did not identify the mechanism.**

There is a plausible one — `_stable` blanks the report's two measured durations with a regex that
requires whitespace between two `{:>12.3f}` fields, and a duration wide enough to fill its own
padding would stop matching — but a plausible mechanism from one observation is a story, not a
diagnosis. It is a ROADMAP line rather than a fix, because the tempting fix is to widen the blanking
rule, and widening an assertion to stop a flake you have not explained is how a gate quietly stops
gating.

### 7. What I would undo if I could

Editing `eval/simulation/appliance.py` while a mutation runner was mutating it. The runner restored
its own snapshot and silently reverted the fix, which then had to be found and re-applied. Nothing
shipped wrong; twenty minutes went missing and the lesson is worth more than the time: **a tool that
writes to the tree owns the tree while it runs.**

## Where the next release should start

`docs/architecture/CARTRIDGE-0.15-DRAFT.md` carries the argument; the short form:

1. **Fix F58, or decide in writing that it is correct behaviour.** It is the one defect this release
   found and did not repair.
2. **Change a champion.** §5.3 steps 7 and 8 have never run. Whether that needs a different corpus, a
   different driving mechanism or a lower floor is a question for a **new** pre-registration.
3. **Write the behavioural-floor design.** T5's reachability, T6's saturation, F4's identical members
   and G4's base score are all parameter inspections, and an opaque artefact defeats every one. That
   is the real question behind the cartridge and it is unanswered.
4. **Then** decide whether the cartridge is worth building, with three fewer unknowns.

**Do not build the cartridge in v0.15.0.** Three of the four things genuinely out of reach in this
process are not plumbing problems — they are inspectability, the feature contract, and trust — and
the release that first changes a champion should change it to something this process can build.
