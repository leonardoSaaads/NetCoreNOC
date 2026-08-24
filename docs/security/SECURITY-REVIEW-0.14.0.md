# Security review — v0.14.0

**Theme: the model family, and the first end-to-end walk of the evidence chain.**

This release adds three scorer kinds that run **inside** the appliance's process, and drives the
whole promotion chain into a real appliance for the first time. It issues **F56**, **F57** and
**F58** unfixed, and closes **F59** and **F60** — both found by walking the chain, neither visible
from reading.

Findings continue from **F55**. Nothing before it is renumbered and nothing before it is edited.

---

## 0. The shape of this release's risk, stated before the findings

Three new kinds of code can now decide which alarms group. That is the largest change to what runs on
the correlation path since v0.6.0's scoring seam, and the honest way to describe the exposure is by
what an attacker or a mistake would have to reach:

| Surface | Reachable from the network? | What stops it |
|---|---|---|
| Registering a model version | **No.** CLI only | There is no HTTP route that creates one. This is a design decision, stated on the console |
| Proposing a promotion | Yes, admin-only | The request names a candidate and nothing else; the server re-derives every input |
| The parameter document | Only through registration | `validate_document` — the same validator the load path runs, so a payload refused at load is refused at registration |
| A malformed artefact at load | n/a | `_load_model_version`'s wide `try`: **never raises**, falls back to the coded defaults, raises an operator warning |
| The attribution table | Built at registration | `MAX_CELLS_PER_TREE` **refuses**; it never approximates and never allocates unboundedly |

**No new dependency, no new route, no new capability, no new audit action, no migration.** The
served surface moved by two static UI modules and by nothing else.

---

## 1. F56 — a malformed corpus file hangs `eval/harness.py` instead of failing it

**Issued unfixed.** Found by accident in Phase 0 and reproduced deliberately with a control.

A scenario JSON missing its `truth` key raises `KeyError` at `harness.py:233`, prints a traceback,
**and then never exits**: `run_scenario` leaves the `Engine.run()` task alive when it raises, and
`asyncio.run`'s shutdown waits on it. The process sits at 0 % CPU until it is killed.

```
CONTROL   well-formed extra file  : exit 0     (terminated)
TREATMENT 'truth' key removed     : exit 124   (KILLED AT DEADLINE)
```

**Severity: low, and the reason is the blast radius.** `eval/harness.py` is offline tooling. It runs
on a maintainer's machine and in CI; it is not in the appliance, not on the trap path, and not
reachable from any network surface. Nothing an operator or an attacker controls reaches it.

**Why it is worth issuing anyway.** In CI the failure mode is *an indefinite hang rather than a red
build*, which is the worse of the two: a red build stops a release and a hang stops a person. The
repair is one `finally` that cancels the engine task, and it is a ROADMAP line rather than a fix here
because Part VII.8 forbids a fix inside a move.

## 2. F57 — a guard's premise was superseded and the guard said nothing

**Issued and closed in the same release**, which is unusual enough to be worth stating plainly.

`tests/test_challenger.py` carried a guard from v0.9.0 asserting that no runtime module reaches
`challenger.py` — written when *"the challenger never becomes the active scorer"* was simply true,
because promotion did not exist. v0.11.0 made a promoted logistic model **the champion**, so
`model_version.scorer_for` constructs a `LogisticScorer` and hands it to the engine, and the guard's
premise was gone.

It kept passing for three releases because it looked for one import form and `model_version.py`
writes another (`from netcorenoc import challenger`). **The guard was green because it was
incomplete, not because the property held.**

Closed by fixing the check to parse rather than match, and by adding `model_version.py` to the
allowlist **with the v0.11.0 reason written next to it**. That second half is the point: the name is
on the list as an admission, not as an exemption.

**The general lesson, recorded because this release found two more instances of it (§4, §5): a guard
whose premise changes does not go red. It goes quiet.**

## 3. F58 — a mass storm defeats the only guard on NE affinity

**Issued unfixed, and this is the release's most substantial finding.**

### 3.1 What happens

`learn.entity_affinity` returns a learned NPMI for a cross-NE pair **only** once that pair's
co-occurrence mass reaches `MIN_EDGE_N` (5.0); below it the pair contributes exactly zero. That gate
is the only thing standing between an unrelated pair of network elements and a link.

A storm walks straight past it:

1. `correlate.Window.process` sets `storm = len(self.index) >= STORM_ALARMS` — **window occupancy**,
   not a situation's size. Many concurrent incidents keep the flag on.
2. `Learner.observe_pairs` deposits `STORM_DAMPING` (0.1) of mass on the pair between the new alarm's
   NE and **every distinct other NE currently in the window**.
3. Fifty-six dying-gasp alarms on one OLT inside three seconds therefore deposit
   **56 × 0.1 = 5.6** on `(OLT, X)` for every `X` in the window — above the 5.0 the gate requires.
4. Past the gate, `Matrix.npmi`'s normalised PMI **saturates at 1.0**, because during a storm every
   alarm co-occurs with every other. The only thing left limiting the value is the evidence discount
   `m/(m+1)`.

The measurement is exact, which is what makes this a finding rather than a hypothesis. The observed
entity terms are `0.8333, 0.8571, 0.8750, 0.8889` — that is `5/6, 6/7, 7/8, 8/9` — and the largest is
`0.9714 = 34/35`, beside a largest recorded pair mass of `34.000`.

The score follows: `s = 0.30·decay + 0.35·A + 0.35·E` reaches **0.85 rising past 0.93** for a pair of
network elements that share nothing, where the design intends at most `0.30`.

### 3.2 `STORM_DAMPING` does not prevent it, and the docstring implies it does

`learn.py` describes `STORM_DAMPING` as *"10x smaller updates during mass storms"*. Two reasons it
does not cover this case:

* **It delays the crossing; it does not prevent it.** A storm large enough to matter is large enough
  to clear a threshold of 5.0 at one-tenth weight. `STORM_ALARMS` is 50, so 56 alarms is *barely* a
  storm by the module's own definition.
* **It damps the numerator and not the denominator.** `observe_pairs` takes the weight;
  `observe_activation` calls `observe_occurrence(item)` with the default 1.0, so the marginals and
  the activation total are undamped. NPMI is a ratio and damping one side of it does not scale the
  result the way *"10x smaller updates"* implies.

**The guard that is working is `MIN_EDGE_N`.** 1 276 of 1 357 NE pairs never reach it — the median
pair mass is 0.300, two orders of magnitude below the threshold. It holds for every ordinary pair and
is defeated only by a storm, which is precisely the traffic it most needs to hold for.

### 3.3 Severity, and why it is not fixed here

**Severity: moderate, availability-of-judgement rather than confidentiality or integrity.** Nothing
leaks and nothing is corrupted. What degrades is the correlator's grouping under storm load: unrelated
incidents merge, and an operator sees one enormous situation instead of twenty. On the generated
network an entire increment collapsed into **one 230-member situation spanning 24 unrelated faults**.

It is a *denial of clarity* at exactly the moment clarity matters most, and a NOC's 3 a.m. is the
condition that produces it.

**Three reasons it is not fixed in v0.14.0, and the third decides it:**

1. **Scope.** The release's first non-negotiable is that `correlate.py`, `engine.py`, `receiver.py`,
   `capture.py` and `learn.py` end byte-identical, verified by hash. A fix lives in `learn.py`.
2. **It is not obviously a defect.** An appliance that has watched two NEs alarm together 34 times
   *should* raise its opinion of that pair. Whether a storm's co-occurrence deserves the same weight
   as an ordinary one is a modelling question with a real answer on either side.
3. **Fixing it here would be adaptive selection with the correlator as the knob.**
   `PREREGISTRATION-0.14.0.md` §5.4 forbids changing the data-generating process after seeing a
   verdict, for a reason that applies with full force one layer down: *"it is worse than tuning the
   model, because the model's tuning is recorded in `params_document` and this would be recorded
   nowhere."* Changing the correlator after seeing the verdict it produced is the same move.

**Recorded as a hypothesis about real networks, not a claim about one.** It is measured on a corpus
this release generated, and `PREREGISTRATION-0.14.0.md` §6 says three ways that such a corpus is a
demonstration of machinery and never evidence about a network.

## 4. F59 — the promotion gate measured a different model from the one it would activate

**Closed in this release. ADR #195.**

`routes_promotion._derived_inputs` computed the four named quantities against
`engine.shadow.scorer` — the model the engine happens to hold in shadow — while `propose_promotion`
activates the `model_version_id` **the request named**. Nothing bound the two: no check that the
candidate's parameters were the shadow scorer's, and `params_hash` reaches `promotion.evaluate` only
as a fold-materialisation key.

### 4.1 Why this is a security finding and not merely a bug

`routes_promotion.py`'s own docstring states the property the module exists to enforce:

> The server re-derives the verdict. The request body may name a `model_version_id`; **it may not
> assert a verdict.**

`PromotionIn` has no `verdict`, `metrics` or `floors_met` field, and *"the enforcement is that the
fields do not exist"*. This defect re-opened that door from the other side, and in the worse
direction: **a verdict about a model nobody measured looks derived.** An admin who registered an
arbitrary artefact would get it evaluated on the strength of whatever was in shadow.

The `missing_run` guard bounds the damage — an artefact with no `challenger_run_id` cannot be applied
— but it checks that *a* run exists, not that the run fitted *these* parameters. It is a bound, not a
binding.

**Requires admin**, which is the mitigating half. The aggravating half is that this system's entire
architecture exists to constrain what an admin may *assert* about a model, and this was a way to
assert one by arranging what got measured.

### 4.2 Why it survived three releases

**No test had ever proposed a candidate that differed from the shadow scorer.** Every fixture
registered the logistic model that was already in shadow, so the two arms coincided and the
substitution was invisible. It is F57's lesson again: the guard was not wrong, it was never given the
input that would have made it disagree.

It also could not have been repaired before this release. `scorer_for` covered two kinds until
v0.14.0, so the gate could not have *loaded* a `tree` candidate at all.

### 4.3 The repair

The arm measured is the scorer the candidate row describes, built by `model_version.scorer_for` —
**the** dispatch, the same function `scorer_lifecycle` uses to activate a row. One function, one
document, so the model scored at the gate and the model that would run after the pointer moves cannot
differ. A candidate whose document will not load is refused with a 400 naming the reason and writes
no `promotion` row; scoring *something else* and returning a verdict would be the same defect wearing
an error-handling hat.

**It changes no verdict this release reports** — the floors are unmet, so the verdict is
`INSUFFICIENT_EVIDENCE` whatever the metrics say — which is what removes the adaptive-selection
objection to fixing a measurement after seeing a result.

## 5. F60 — the console reported the coded defaults as the active configuration

**Closed in this release. ADR #196.**

`scorer_config` and `model_version` are mutually exclusive by a database CHECK, so with a model
version active there is no configuration row. `_active_scorer()` fell back to
`scoring.default_scorer()`, and `GET /api/scorer` published that fallback as `scorer_id`, `params`
and `params_hash`. The console rendered it under the heading **"Active configuration"**, captioned
*"What the running engine is grouping with right now."*

**An operator whose champion was a promoted model read five weights that decided nothing, under a
heading asserting that they did.**

**Severity: low as a control, higher as a control-surface.** Nothing was mis-scored — the engine ran
the right model throughout. What was wrong was the operator's picture of it, and this is the screen
an admin uses to decide whether to retune or roll back. A wrong picture of what is running is how a
correct system gets operated incorrectly.

It predates the tree kinds: a promoted `logistic` — reachable since v0.11.0 — produced the same
misreport, through v0.12.0 and a whole console rewrite in v0.13.0. It survived because nothing had
ever been promoted, so the branch was never taken outside a unit test.

Repaired by splitting the one function that had two meanings: `_tunable_scorer()` is what the form
edits, `_running_scorer()` is what is deciding. The five weights are still shown — an admin may
retune them and roll a model version back — but labelled, and marked inactive when they are.

---

## 6. What was assessed and found sound

### 6.1 The three kinds add no dependency and no dynamic loading

Five runtime dependencies, unchanged since v0.2.0. `model_version.scorer_for` is a chain of `if`s
over a frozen `SUPPORTED_KINDS`, and
`test_the_tree_family_cannot_reach_the_store_the_clock_or_the_network` parses `tree.py`,
`forest.py`, `boosting.py`, `cart.py` and `attribution.py` and fails on any import of the store, the
clock or a socket. **A model kind is arithmetic and nothing else.**

### 6.2 A malformed artefact is a fallback, never an exception

`scorer_lifecycle._load_model_version` wraps the load in a wide `try` and **never raises**: a payload
this build cannot construct leaves the coded defaults in place and raises an operator warning. That
is v0.6.0's fail-safe discipline and the tree kinds inherit it unchanged. `AttributionError` becomes
`ModelPayloadError` before it reaches that door, so `MAX_CELLS_PER_TREE`'s refusal takes the same
path every other rejection takes.

### 6.3 The attribution cannot allocate unboundedly

`MAX_CELLS_PER_TREE = 4096` is checked **before** the table is built, from the cut counts alone. A
registration that would need more refuses; nothing large is allocated first and discarded. The bound
is generous against the shape it protects: a complete depth-4 tree cuts at most 15 thresholds across
three features, and the worst product under that constraint is 6·6·6 = 216.

### 6.4 The simulator cannot reach the promotion path

`PREREGISTRATION-0.14.0.md` §1 extends the `incumbent_linked` prohibition to the generator: *"a label
the machine produced does not judge the machine, whether the machine is the champion or the
generator."* Enforced by **parsing every module** under `src/netcorenoc/` for an import of
`simulation` or `scenario_dsl`, and separately by asserting that `promotion.py`, `judge.py`,
`shadow_cv.py` and `evaluation_folds.py` do not so much as *name* `situation_key`, `entity_key` or
`is_root`. Parsed rather than read, because reading is what let F57 hide.

### 6.5 The seal was never spent

`seal_query_count` is **0** on the demonstration databases and **0** on the production tree. The
floors were never met, so `promotion.evaluate` never reached the branch that reads it. §9's *"the
seal is spent at most once, on the demonstration database, and never on the production tree"* is
satisfied in the safe direction and by construction rather than by care.

### 6.6 The demonstration harness carries each principal's real headers

`eval/simulation/appliance.py` drives the real appliance over real UDP and real HTTP. Two properties
were checked because a harness is where a control gets quietly disabled:

* **The CSRF check fired and was satisfied honestly.** `POST /api/tokens` returned 403 until the
  cookie client sent the `Origin` and `X-NetCoreNOC-Client` headers the shipped console sends. The
  bearer clients do **not** send them, because a token is not sent by a browser — the harness carries
  each principal's real headers rather than the union of them.
* **The rate limiter fired and was waited out, not disabled.** Every principal comes from
  `127.0.0.1` and shares one bucket; the client backs off and retries, which is what a console does,
  and a 429 that will not clear still raises. A harness that turned the limiter off would be
  demonstrating a different appliance.

### 6.7 Prime directive 1 is now a test

`correlate.py`, `engine.py`, `receiver.py`, `capture.py` and `learn.py` are byte-identical to
v0.13.0, and `TRAP_PATH_HASHES` pins all five by content. **That mattered in this release
specifically**: F58 is a finding about `learn.py`, measured with the evidence on screen, and left
unfixed. A hash a human runs at the end of a phase catches a change only if the human remembers, and
the phase where they would most want to forget is the one that found the defect.

---

## 7. Honest notes

Four things this review would rather not say, said because a review that only reports what it found
is a review that stopped looking.

### 7.1 The three findings this release fixed were all found by *driving*, not by reviewing

F58, F59 and F60 were found by booting an appliance, sending traps at it and clicking through the
console — not by reading code, and not by any guard. The guards for them exist now **because the
defects did first.** Every one of them had been readable for at least two releases.

That is an uncomfortable result for a project whose method is heavy on static guards, and it is the
strongest argument in this release for the method it added: an end-to-end walk.

### 7.2 The mutation ledger has eight named survivors

`docs/gates/v0.14.0-phase-8.md` §3 lists them by name rather than as a ratio. Two are worth
repeating here because they are security-adjacent:

* **The harness's `bind` failing open.** Reverting `appliance.Sender.socket_for` to
  `contextlib.suppress(OSError)` — the exact construct the module's docstring says is wrong here — is
  caught by nothing. The whole simulated network would silently arrive from one address and every
  number in Gate 7 would be about a different corpus.
* **A non-computable rate contributing zero instead of contributing nothing.** The distinction that
  cost this project a release once is asserted nowhere that a mutation reaches.

### 7.3 The simulated corpus is not evidence about a network, and the release says so four times

`PREREGISTRATION-0.14.0.md` §6, `docs/gates/v0.14.0-phase-6.md` §8, `docs/gates/v0.14.0-phase-7.md`
§7 and the build report each state it independently. The repetition is deliberate: a synthetic corpus
that produced `BETTER` would be the most quotable number this project has ever generated and the
least meaningful, and the guard against quoting it is that every document it appears in refuses the
reading in advance.

**It did not produce `BETTER`.** It produced `INSUFFICIENT_EVIDENCE`, which is a weaker claim and
therefore a safer one — and that is luck, not discipline. The discipline is what was written before
the number was seen.

### 7.4 The registered corpus shape may simply be wrong, and this release could not say so

`PREREGISTRATION-0.14.0.md` §5.4 forbids changing the generator's shape after a verdict is observed
and sends the conclusion here instead:

> If the build concludes that the registered shape is wrong, that conclusion goes into the security
> review as an opinion **for v0.15.0**, and the demonstration is reported against the registered
> shape whatever it produced.

**The opinion, offered as one.** Driving twenty incidents concurrently — chosen in Phase 6, before
any verdict, with its reasons recorded — is what lets one storm's co-occurrence reach every other
incident in the window. Separating them in time instead would very probably have cleared both floors.
It was **not** changed, because changing it after seeing the shortfall is exactly what §5.4 exists to
forbid, whether or not the word "shape" strictly covers the driving mechanism.

A v0.15.0 pre-registration should decide, in advance, whether incidents are concurrent or spaced —
and should notice that the two answers measure different things. Concurrency measures the appliance
under NOC-at-3-a.m. load, which is the interesting case and the one that found F58. Spacing measures
the promotion machinery, which is what the floors are counting. **This release conflated them, and
that is the review's own finding about the review's own plan.**

---

## 8. Residual risks carried forward

| Risk | Status |
|---|---|
| **F58** — a storm defeats `MIN_EDGE_N` for every NE in the window | **open, unfixed**, §3 |
| **F56** — a malformed corpus file hangs the harness | **open, unfixed**, §1. Offline tooling |
| The graph screen's d3 render paths | unchanged from v0.13.0 — a recording double, so a defect there is caught by nothing |
| The champion has never changed on any corpus | §8.7 of the plan is untested in either direction |
| The four named quantities at scale | computed for both arms from one code path, on **ten** asserting bags |
| The visual layer | outside the instrument since v0.12.0; Phase 9 is the pass with eyes on it |

## 9. Regression posture

| | |
|---|---|
| F1–F55 | unedited and green |
| **F56** | **issued** — §1, unfixed, ROADMAP line |
| **F57** | **issued and closed** — §2 |
| **F58** | **issued** — §3, unfixed, with the arithmetic |
| **F59** | **fixed in this release** — §4, ADR #195 |
| **F60** | **fixed in this release** — §5, ADR #196 |
