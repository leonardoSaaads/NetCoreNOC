# Security review — NetCoreNOC v0.9.0

Continues from **F44** (v0.8.1). **No new finding is issued**, so the next release continues from
**F45**. Scope: the six workstreams of [`../scope/SCOPE-0.9.0.md`](../scope/SCOPE-0.9.0.md) — shadow
mode, and nothing else.

**Summary for a reader in a hurry.** This release adds **no route, no capability, no audit action,
no dependency, no served path, and no HTTP surface of any kind.** It adds one migration and two
tables that no principal can read over HTTP because there is nothing to read them with. The
security-relevant work is §1 — *the challenger cannot reach the active scoring path, by construction
and by test* — and §5, which is the honest criticism this release is obliged to make of itself.

Issuing no F-number is a claim, not an omission: §1–§4 are the checks that back it, and §5.4 says
what a reviewer would look at next.

**One defect was found and fixed inside this release**, by execution rather than by review (§3.1).
It is **not** issued an F-number, and the reason is the one v0.8.1's review gave for the opposite
call: the F-series tracks defects in **shipped** code, and numbering one that never reached a user
would devalue the numbers that did. It is recorded in full below and in
[`../gates/v0.9.0-phase-5.md`](../gates/v0.9.0-phase-5.md) §2 instead.

---

## 1. The challenger cannot reach the active scoring path

The single security-relevant property of a shadow-mode release. Asserted four ways, three of them
by **parsing the tree** rather than by reading it.

| Control | Check |
|---|---|
| No module outside shadow mode may import the challenger | `tests/test_challenger.py::test_no_code_path_makes_the_challenger_the_active_scorer` parses every `.py` under `src/netcorenoc/` and fails on an import of `netcorenoc.challenger` from anything but the five shadow modules. The permitted set is **enumerated in the test**, so widening it is a failing diff someone must argue for. |
| No shadow module may reach the swap | `test_the_shadow_modules_never_reach_the_active_scorer_or_the_learner` parses attribute and call **names** for `set_scorer`, `penalize`, and an assignment to another object's `.scorer`. Over the AST, not the text — these modules document what they may not do, on purpose, and a substring search would forbid the sentence that states the property. |
| A running engine's active scorer is the champion | `test_a_fresh_engine_runs_the_champion_and_knows_nothing_of_a_challenger`, and again after a real ingest replay in `test_the_engine_scores_a_sample_on_the_real_ingest_path`. |
| The guards are not vacuous | `test_a_challenger_and_the_champion_disagree_which_is_the_point`. If the two scorers agreed everywhere, every check above would pass on a challenger with no opinion of its own. |

**Structurally, the schema declines to help too.** Migration `0009` adds **no pointer** from
`scorer_config` to `challenger_run`. There is no promotion mechanism in this release — deliberately,
because a release that could promote would be judged by the only metric it had, which would be
agreement with the champion. Adding the edge later is a visible, arguable diff rather than an
autocomplete away.

**And `SafeScorer` still owns the fail-safe.** The challenger satisfies `LinkScorer` structurally, so
if a future release *does* activate one, it inherits degradation on an exception, on a non-finite
score, on an empty `terms` tuple, and on an over-budget call — with **no new code**. The
`_LOGIT_CLAMP` in `challenger.py` exists for that inheritance: `SafeScorer` treats a non-finite score
as a contract violation and degrades **permanently**, so an absurd coefficient must not be able to
take the wrapper out of service. `test_the_link_function_cannot_overflow_out_of_the_contract` drives
`1e300` through it.

## 2. Shadow rows are admin-only, by absence

`shadow_opinion` and `challenger_run` are written engine-side, where visibility scoping does not
exist and must not — correlation learns across the whole estate. They inherit migration 0008's
posture exactly: **no read below `admin`, on any route, in any format, ever.**

v0.9.0 satisfies that in the strongest available form: **it adds no route.** `tests/test_declaration.py`
is unedited and green (137 tests), the route table is untouched, and the reports are CLI subcommands
for the reason `bias.py` already recorded — a route would need a capability, a scope posture, a
declaration, a rate limit and a place in the perimeter to serve a report no scoped principal may read
anyway, and it could never be a byte-for-byte gate.

Two disclosure controls are worth naming because they were **decisions, not defaults**:

* **The champion-agreement report anonymises operators** (DECISIONS #120). The alias map is built in
  `agreement.collect` and the `principal_ref` **never enters the document the renderer receives**, so
  `render` cannot print one by mistake. `test_the_report_never_prints_an_operator_identity` asserts
  absence from the rendered text *and* from `repr(measurements)`. Without this the release would have
  shipped a per-employee performance report generated by a tool nobody was told would generate one.
* **Both new reports emit aggregates only.** Asserted against every NE address and OID in their
  fixtures.

## 3. The training path cannot stall or fail ingestion

Three separate mechanisms, and the third was found by measuring.

**It runs off the lock.** Phase 0 §4 proved by parsing `Engine.maintenance` that **no point inside it
runs outside `store.lock`** — one `async with` block, zero statements after it. `maintenance_loop`
therefore calls `shadow.train` *after* `maintenance()` has returned and released the lock
(DECISIONS #118/#121). `train` takes the lock only to read rows and, separately, to write the result;
**the fit holds nothing.**

**It cannot starve the event loop.** The fit is `async` for exactly one reason: `await
asyncio.sleep(0)` between iterations. Running off the batch lock but inside the same event loop, a
multi-second fit that never yielded would stall ingestion by an indirect route — prime directive 1
broken by accident rather than by design. The yields are between iterations, never inside the
arithmetic, so they change no number.

**It fails safe, proven by injection.** `test_an_injected_training_failure_degrades_training_and_not_ingestion`
makes the store raise where training reads: `Shadow.train` records the error, returns `{}`, raises an
operator warning, and ingestion is unaffected. `test_an_injected_scoring_failure_never_reaches_the_engine`
does the same on the **ingest path**, where `observe` runs.

**Bounded by construction, not by a timeout.** `MAX_PAIRS_PER_BAG = 256`, `MAX_TRAINING_ROWS = 8000`,
`ITERATIONS = 200`, `MAX_BUFFERED = 2000`, `SHADOW_MAX_ROWS = 200 000` — every one a constant, none a
deadline, because a time-based early stop would make the fit's answer a property of the machine and
determinism outranks a deadline.

**The measured cost on the ingest path**, at the shipped 1 % default over four corpus scenarios:
454 `score()` calls, **6.09 ms in total across the whole replay**, 454 rows against 45 474 captured
pair rows, and +45 KB on a 5.3 MB database.

### 3.1 The defect measuring found — a silently truncated sample

Not issued an F-number (see the summary), and worth more space than a footnote because of **how** it
was found.

At `sample_rate = 1.0` the Phase 5 replay recorded **43 474 dropped opinions of 45 474**. The
in-memory buffer (`MAX_BUFFERED = 2000`) fills long before the maintenance flush, so 95 % of the
sample is discarded. The bound itself is correct and deliberate — dropping a sample costs a data
point, growing without bound costs the process — and the drops were already counted.

**The defect was that nothing told the operator.** An operator who set the rate to 1.0 believing they
would get every pair would read a 2 000-row *truncated prefix* as a census, and every quality figure
computed from it would be a figure about the first two thousand pairs after each flush rather than
about the traffic. That is a biased sample presented as an unbiased one, which is the failure mode
this whole release is organised against, arriving through the back door of an implementation detail.

| | |
|---|---|
| **Severity** | Medium for interpretability. **Nil for security** — no disclosure, no access-control effect, no audit-chain effect, and the drops were never attacker-triggerable in a way ordinary traffic is not. |
| **Fixed** | `Shadow.warnings()` raises an operator warning naming the drop count, the buffer size and the remedy; wired into `runner.py`'s existing warning channel beside the capture and scorer warnings, so it reaches `/api/stats`. |
| **Found by** | executing the cost measurement Gate 5 required. **Reading the code would not have found it** — every individual piece was behaving as designed. |

The general lesson, recorded because it will recur: **a bound that is correct and a bound that is
visible are different properties**, and this project's gates test the first far more often than the
second.

## 4. No new surface

| | v0.8.1 | v0.9.0 |
|---|---|---|
| routes | unchanged | **unchanged** |
| capabilities | unchanged | **unchanged** |
| audit actions | unchanged | **unchanged** |
| runtime dependencies | 5 | **5** |
| migrations | 8 | 9 (`0009`, additive, **seeds no rows**) |
| served static paths | unchanged | **unchanged** |
| `meta` keys read | `config.allowlist`, `config.retention_days`, `config.dataset_retention`, `community_hmac_key` | **+ `config.evidence_floors`, `config.shadow_sample_rate`** |

The two new `meta` keys are read-only from the engine's side and have **no write path in this
release** — no route sets them, so an operator sets them out of band, exactly as an operator may
already edit `meta` directly. Both fail safe: `config.evidence_floors` falls back to the **project
floors as a whole** on anything unreadable and can only ever make a requirement *harder*
(DECISIONS #114); `config.shadow_sample_rate` clamps into `[0, 1]` and falls back to the shipped
default. Neither can widen what anyone may read.

`git diff --stat a958cfd` over `correlate.py`, `capture.py`, `receiver.py`, `learn.py`, `rbac/`,
`shaping/`, `scoring.py` and the existing migrations is **empty**.

---

## 5. Critical analysis

The part of this document that is worth reading.

### 5.1 What the champion-agreement number does and does not license

The release's primary deliverable is a *conditioned* agreement rate, and the conditioning is the
whole point. Three limits, stated plainly:

**It is agreement, not correctness.** There is no ground truth in this system — only what an operator
said about a grouping they were shown. A 94 % agreement rate and a 94 % correctness rate are
different claims and this product can only ever measure the first. The report says so in its own
second paragraph, which is where a reader will actually see it.

**On a uniform bag it licenses nothing at all.** A bag whose every pair fell on the same side of the
threshold contained **no decision the champion could have got wrong**. Confirming it is agreement
about an outcome that could not have been otherwise. Phase 0 measured seven of nine eval scenarios
as uniform and, on the fullest corpus this repository can build, **five mixed bags out of forty-one**
— so roughly an eighth of any headline rate is about a judgement, and seven eighths is about
arithmetic. **That proportion is structural and does not depend on who did the labelling**, which
makes it the most transferable thing this release measured.

**The number this release publishes for its own corpus is not a measurement of operators.** The
68.3 % in `v0.9.0-phase-2.md` §5 came from a corpus labelled by a **mechanical rule** — every third
situation `split` — declared before the report existed. A rule that splits one in three produces
66.7 % by arithmetic. It measures the instrument, and the gate says so in a boxed warning rather
than in a footnote. **A real deployment's number is the one worth quoting, and this release does not
have one.**

### 5.2 Was the discriminating population large enough for any of this to mean anything?

**No, and that is the release's finding rather than its failure.**

| Floor | Required | Best measured corpus |
|---|---:|---:|
| `split` bags | 50 | **13** |
| mixed bags | 20 | **5** |
| merge-aware incidents | 30 | 37 ✓ |
| distinct operators (top ≤ 60 %) | 3 | 3 at 34.1 % ✓ |

The line that matters is not in the table: **exactly one bag was both `split` and mixed.** The rows
where an operator contradicted the champion *about a decision the champion actually made* number one
in the richest corpus this repository can construct. Every quality figure in the shadow report comes
from a **synthetic** fixture built to exercise the machinery, and the report and the gates label them
as such.

The consequence for the roadmap, offered as an opinion: **v0.10.0's honest judge cannot be validated
on this corpus.** An evaluator whose held-out set contains a handful of discriminating bags will
report a number with an interval wider than any difference it could detect. The case for
v0.10.0–v0.13.0 rests on acquiring `split` labels deliberately — which is active learning, which
v0.9.0's scope explicitly refuses because soliciting labels before the organic population's bias is
understood destroys the baseline it would be measured against. **That tension is real and this
release does not resolve it.**

### 5.3 A deviation from the pre-registered plan, reported rather than edited

`PREREGISTRATION-0.9.0.md` §2.3 registered **four** features. `same_oid_root` was **not built**:
`LinkFeatures` carries no trap OID and neither does `correlate.WindowAlarm`, so it is computable
offline and **not online** — and a feature that cannot be served guarantees the training/serving skew
§6 of the plan exists to detect. Building it would have required editing `correlate.py`, which this
release may not touch.

The model therefore has **four free parameters, not five**, and the events-per-variable convention
would give a floor of forty. **The floor was kept at the registered fifty.** `resolved = the more
demanding of` runs monotone toward evidence (DECISIONS #114), and lowering a floor because the model
got simpler is exactly the move that rule exists to forbid. The deviation is recorded here, in
`challenger.py`'s docstring and in `v0.9.0-phase-4.md` — **never by editing the plan**, which §9 of
the plan itself directs.

**A reviewer should note what this cost.** The plan's own feasibility question would have been better
served by noticing, in Phase 1, that a feature must be serveable before it can be registered. That
check did not exist and now, in effect, does.

### 5.4 Which pre-registered threshold I would argue is wrong — an opinion for v0.10.0

**The `split`-bag floor is the wrong quantity, and `mixed_bags ≥ 20` should have been the binding
one.**

Events-per-variable is a convention about **the minority class of the target**, and under policy A
the minority class is the `split` bags — so fifty is arithmetically defensible. But Phase 0 measured
why it is the wrong *product* quantity: a `split` on a **uniform** bag tells the model that pairs it
already rejects should be rejected. It is a row, and it teaches nothing. The population that carries
information is bags that are **both `split` and mixed**, and the plan does not floor that quantity at
all. On the fullest corpus available it stands at **one**.

If I were writing v0.10.0's plan I would floor `split ∧ mixed` bags directly, at something like ten
per free parameter, and treat `split_bags` as a diagnostic. I am recording that here rather than
acting on it, because acting on it would mean editing a plan after seeing its results, which is the
one thing the guard in `tests/test_preregistration.py` exists to make visible.

**A second, smaller one.** `MIN_INCIDENTS_FOR_INTERVAL = 10` in the agreement report is too
permissive. A cluster bootstrap over ten incidents produces an interval that is technically computed
and practically meaningless; over the driven corpus it printed `[33.3, 91.7]` on twelve incidents,
which is a range wide enough to contain any conclusion. Thirty would be more honest, and it would
have printed `n/a` where this release printed a number.

### 5.5 Two things a reviewer would look at next

* **`hash()` is used to namespace partition component ids** in `shadow_eval.evaluate`. It is stable
  within a process and `PYTHONHASHSEED` does not affect integers or tuples of integers, so the
  metrics are reproducible — verified across two processes. It is nonetheless a construct whose
  stability is a CPython property rather than a documented contract, and a future release comparing
  partitions **across** processes should replace it with an explicit `(bag, component)` tuple key.
* **`shadow_opinion` has a row cap and no age bound.** That is deliberate (rows grow with traffic ×
  sample rate, not with time), and the consequence is that a deployment which lowers its traffic
  keeps old opinions indefinitely. They contain no human label and join to none, so no retention
  tier reaches them and none needs to — but a reader auditing "what deletes what" should know this
  table is bounded only by count.

---

## 6. Threat-model impact

**No trust boundary moves, no asset changes classification, and no new attacker capability appears.**
`threat-model.md` gains a v0.9.0 section recording:

* the shadow tables as **assets at the same classification as the feedback dataset** — engine-side,
  scope-bypassing by construction, admin-only, and unreachable over HTTP because no route exists;
* the challenger as a component that is **structurally unable to influence grouping**, with the four
  controls of §1 as its checks;
* the two new `meta` keys as **operator configuration with fail-safe parsing**, neither of which can
  widen what any principal may read, and one of which (`config.evidence_floors`) is monotone in the
  direction of *more* evidence by construction.

**This was not a full re-review of the attack surface.** F1–F44's controls were re-checked only where
this release touches them, which is: the write-transaction discipline (untouched), the dataset's
admin-only posture (extended to two more tables), and the operator-warning channel (extended by two
messages). Everything else was verified as *unchanged*, by diff, rather than re-reasoned.
