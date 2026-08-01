# Build report — NetCoreNOC v0.8.0

**"The scoreboard": capture the operator feedback as a durable dataset, and measure its bias.
It trains nothing.**

Seven workstreams, eight gates, one migration, zero new dependencies, `make eval` byte-identical.
837 tests (was 777), coverage 96.02 % (was 95.89 %).

This release exists because **capture is irreversible**. `A` and `E` decay continuously, `alarm` is
deduplicated and mutated on re-fire, and situations are merged and lose their membership. A field not
captured at the moment of decision is not captured late — it is captured never. That inverts the
usual decision rule, and the inversion is written into the schema: *an unneeded column costs bytes; a
missing one costs the field forever.*

---

## 1. What Phase 0 found, and why it changed the release

Three reproductions changed a later phase. All were executed, not read.

### The merge destroys the label's referent — and the chain too

`engine._assign_situation` merges with `sid = min(sids)`; `store.merge_situations` moves
`situation_alarm`, re-points `link`, marks the source `merged` — **and does not touch `feedback`**.
Afterwards:

| Query | Result |
|---|---|
| the `feedback` row | still points at the **absorbed** id |
| `situation_alarm` for that id | **empty** |
| `feedback JOIN situation_alarm` | **empty** |
| `situation_alarm` for the **survivor** | the **union** of both bags plus the bridge |

**No query recovers which alarms the verdict was about.** The union is not a recovery: nothing in it
distinguishes the two members the operator judged from the three they never saw.

**A second loss, not in the brief and found here:** `merge_situations` recorded `status='merged'` but
**not the destination**, so the merge chain was unrecoverable as well. That promoted "situation
lineage" from a nicety to a **schema prerequisite** — without a recorded merge edge, v0.10.0's
split-by-incident cannot be built at all.

### Feedback posted to a merged situation: 200, a row, and nothing

| Question | Answer |
|---|---|
| Status | **200** — `merged` is a status, not a deletion |
| Row written? | **yes**, indistinguishable from a real label |
| `learn.penalize()` receives | **an empty list** |
| Effect on learned state | **none** — `A.pairs` byte-identical |

A silent no-op that looked exactly like a good label. v0.8.0 records the empty bag **as empty**, and
that population is countable for the first time.

### The amplification is truncation, not rejection — and the draft's §3.1 was wrong twice

The reviewer's figure reproduced exactly: **194 341 pairs against 10 973 links, 17.71×, median 100
candidates, 86.0 % storm.** Counting at `score_link` rather than at `process()` separated three
populations the single number conflates:

| Population | Count | Share |
|---|---|---|
| evaluated | 194 341 | 100 % |
| **accepted** | 194 002 | 99.83 % |
| persisted after the cap | 10 973 | 5.6 % |
| **lost to truncation** | 183 029 | 94.2 % |
| **evaluated and rejected** | **339** | **0.17 %** |

The draft called the rejected pairs *"the majority class, without which supervised training is
impossible"*. They are **0.17 %** of this corpus. And per scenario the accept rate runs **0 %**
(`background_noise`) to **100 %** (every storm) — so the class balance of `incumbent_linked` is a
property of **the traffic**, not of the scorer.

---

## 2. The correction that mattered most

`FEEDBACK-DATASET-0.8-DRAFT.md` §3.1's sentence was wrong **conceptually** as well as
arithmetically: the rejected pairs are the **machine's decision**, not the human label's majority
class, and the human majority class does not exist in quantity and never will. Read in good faith by
whoever writes v0.9.0 — and it *would* be read in good faith, because it is a specification — that
sentence authorises training a challenger against `incumbent_linked`.

Struck through in place, with the correction beneath it and the invariant stated:

> **No metric that decides promotion may be computed against `incumbent_linked`.**

And made **structural**, because prose in a specification is advisory to whoever reads it in two
years:

> **`dataset_pair` has no target column.** The only label lives in `feedback`, and reaching it
> requires the join.

That friction is the mechanism (DECISIONS #103). The same sentence had already propagated to
`ROADMAP-0.8-TO-0.13.md`; corrected there too, by reference.

**And a correction back to the build prompt.** Its §5.2 builds the epoch column's case on the epoch
advancing when a label arrives. Executed:

```
epoch before penalize : (0, 0)
epoch after  penalize : (0, 0)     -> advanced: False
A/E pair mass before  : (1.0, 1.0)
A/E pair mass after   : (0.5, 0.5)   -> mutated: True
```

Since v0.7.1's F36 fix **neither feedback path ticks the epoch** — only a closed situation does. The
contamination is real but travels through the **mass values**. The column survives with the correct
reason; had this been taken on trust, v0.9.0 would have been briefed to hunt contamination in a
counter that never moves.

---

## 3. The schema, and why each column exists

Two rules, and **every one of the 66 columns satisfies one or the other**:

> **Store what cannot be recomputed. Derive what can.** And: **keys are not features.**

| Table | Columns | cannot-recompute | key | other |
|---|---|---|---|---|
| `capture_run` | 13 | 13 | 0 | **0** |
| `dataset_observation` | 15 | 9 | 6 | **0** |
| `dataset_pair` | 18 | 13 | 5 | **0** |
| `feedback_member` | 4 | 1 | 3 | **0** |
| `feedback` (added) | 15 | 15 | 0 | **0** |
| `situation` (added) | 1 | 1 | 0 | **0** |

Four decisions where the rule did real work:

1. **`class_affinity`/`entity_affinity` store the *value*, never the contribution.** `term_a` is
   `w_a · A`, and dividing the weight back out **fails when the weight is zero** — a legal,
   supported, and especially interesting configuration.
2. **`truncated` exists because Phase 0 measured that it matters.** 94 % of what the engine discards
   is accepted links the cap dropped, not rejections. Without the column v0.9.0 would read a cap
   artefact as a scoring decision.
3. **Severity, entity and `alarm_count` are captured although `AdditiveScorer` ignores them.**
   `LinkFeatures`' reserved slots are `None` because the *incumbent* does not use them.
4. **"Same /24?", "same vendor?" are NOT columns.** Modelling decisions; freezing one now is the
   same mistake as freezing the label-derivation policy.

### The physical layout: chosen **against** its own measurement

Both candidates built at Phase 0's 194 341-row volume:

| Operation | one table + `lifecycle` | two tables |
|---|---|---|
| file size | 23.92 MB | **21.52 MB** |
| promotion | 18.42 ms | **12.57 ms** |
| dataset query | 0.44 ms | **0.06 ms** |
| sink deletion by age | 326.03 ms | **297.04 ms** |

**Two tables won every measurement. One table was chosen anyway.** A pair row references two
*observation* rows, so under two tables a promoted pair would live in one table while its
observations lived in another — promotion would have to preserve ids across four independently
autoincrementing tables, or **rewrite every reference as it moved**. The failure mode is a dataset
row silently pointing at the wrong raw material, on the path that produces the release's entire
output. Here nothing moves: promotion is one `UPDATE`, ids are stable (DECISIONS #107).

**The cost is recorded, not argued away:** the sink/dataset separation is a `WHERE` clause rather
than a table boundary — a *checked* guarantee where the other would have been *structural*, against
this project's usual preference. Mitigated by a dedicated test, not by the layout.

---

## 4. F43, and the fourth narrowing of one claim

The declaration gate refused unknown route **shapes** (F42) but iterated `route.methods` within a
known one — and an empty set produces zero iterations, so the route was **neither checked nor
refused** while Starlette served all seven verbs on it. Reproduced with the doc routes disabled so
F41 could not mask it.

Refused now, by the same logic that refuses an unknown shape. **The option not taken** — treating an
empty set as the full verb set — is superficially more precise and worse: it invents a declaration
requirement for seven verbs nobody wrote, so the natural fix turns a mistake into seven
authorizations (DECISIONS #106).

Five `test_f43_*`; the two asserting the refusal **proven red** on the unmodified tree, the other
three passing on both because they are controls.

**The honest note** (`SECURITY-REVIEW-0.8.0.md` §9.1): this is the **fourth consecutive release** to
narrow a completeness claim about this one guard — F40, F41, F42, F43. Each fix was right; each claim
was broader than what had been checked. The guard is now complete along three axes and **nobody has
enumerated the axes.**

---

## 5. Measurements

### Ingest cost

| | capture OFF | capture ON |
|---|---|---|
| `dataset_pair` | 0 | **194 341** |
| `dataset_observation` | 0 | **2 256** |
| `alarm` / `link` | 2 252 / 10 976 | **unchanged** |
| database | 2.47 MB | 24.35 MB |

```
added rows per trap    : 62.24
added bytes per trap   : 6929
database growth        : 9.88x
replay wall time       : +31.5%
```

**The worst case, not the typical one** — the corpus is 86 % storm with a median of 100 candidates;
`background_noise` runs at 12.

### The finding that is least comfortable

**The sink's row cap binds long before its 21-day age limit at every realistic rate.**

| traps/s | cap binds after |
|---|---|
| 0.1 | **3.7 days** |
| 1 | **8.9 hours** |
| 10 | **0.9 hours** |

One 200 000-trap storm produces 12.4 M rows — **6.2× the 2 M cap**. Raising the cap is not the fix:
21 days at 10 traps/s needs ~1.1 billion rows, ~124 GB.

**The cap is a disk budget; the 21-day figure is a ceiling most deployments never reach.** The
response was not to change the number but to stop implying otherwise — `dataset stats` reports the
**observed** window, and the consequence (a late label records `coverage: none`, biasing the corpus
toward quickly-labelled situations) is measured and stated rather than fixed.

---

## 6. What the guards caught that review did not

Three defects were found by tooling, not by reading:

1. **`vulture` found an unwired destructive path.** `store.prune_dataset` had **no caller**:
   applying a reduced retention stored the policy, audited the change, and **deleted nothing**. An
   operator would have set three months, seen the count, confirmed, and kept twelve. The dead-code
   guard caught a security-relevant bug no security test was looking for.
2. **The bias-report gate found a misleading coverage state.** A verdict on a memberless situation
   with one stray pair was recorded as `full` — vacuously true, and it would have told a reader a
   complete situation was captured. A fourth state, `empty`, was added; the specification's three
   all presuppose a bag with members.
3. **`test_upgrade.py` caught a compatibility break.** `merge_situations` writing `merged_into`
   failed against a frozen pre-`0008` schema. That test enforces a real convention —
   `create_situation` already tolerates a schema predating `scorer_config_id` — because it is what
   proves the *migration* changes behaviour and the code does not.

A fourth was caught during implementation: the first draft of capture **re-derived `A` and `E` from
the learner**, after `observe_activation` and `observe_pairs` had already moved the masses. Those
rows would have been features the scorer never saw, indistinguishable from real ones — the exact
trap the specification names for re-scoring, one level down. The values are now read back from
`result.terms`, which carried them all along.

---

## 7. Decisions

| # | Decision |
|---|---|
| **#102** | The draft is corrected **in place and dated**, never rewritten — the withdrawn sentence stays visible |
| **#103** | The imitation-trap invariant is **structural** (no target column), not only prose |
| **#104** | The membership record is server-side, ordered ids, a **child table**, digest alongside |
| **#105** | The observation row is **per activation**, not per trap (0.7142 measured) |
| **#106** | An empty method set is **refused**, not defaulted to all verbs and not skipped |
| **#107** | One pair table with a lifecycle column — **chosen against the measurement**, for a correctness property |
| **#108** | `engine.py`'s ceiling 542 → 580, **paid for** by a new guard forbidding any SQL in it |

**#108 deserves a note.** The ceiling is a shrink-only ratchet, and raising one for convenience is
how a ratchet becomes a comment. The 38 lines are call sites and two attribute assignments; every
capture decision lives in `capture.py`. The raise is paid for by
`test_the_engine_holds_no_capture_logic`, verified non-vacuous by injecting `INSERT INTO
dataset_pair` and observing red. The release therefore ends with a **stronger** guarantee than it
started with: the old rule bounded size alone and was satisfiable by a file full of SQL.

---

## 8. Honest caveats

* **The corpus is storm-heavy and every per-trap number inherits that.** 62 rows/trap is the worst
  case. It is stated everywhere it appears, and nobody should quote it as *the* figure.
* **The sink's effective window is the row cap, not 21 days**, so the dataset is biased toward
  quickly-labelled situations. Measured, surfaced, **not fixed** — a later release with real latency
  data should retune it.
* **The merge chain is recorded from v0.8.0 forward only.** Pre-upgrade merges are gone; no
  migration can reconstruct a destination that was never written.
* **`dataset_observation.varbinds` is opaque JSON**, so v0.9.0 cannot query inside it without a full
  scan. A deliberate deferral of a *modelling* decision, and the fix is additive — nothing captured
  is lost — which is the test the decision had to pass.
* **The sink/dataset separation is a `WHERE` clause**, not a table boundary. A checked guarantee
  where a structural one was available, accepted with its reason and its test.
* **An admin can still destroy the corpus deliberately.** The control is preview plus audit, not
  prevention — the same posture the product takes on scorer parameters.
* **Nobody has enumerated the declaration gate's axes.** Four releases, four narrowed claims.
* **The Docker daemon was unavailable in the build environment**, so the image was not built. The
  prescribed substitute was performed: the wheel installed into a clean Python 3.12 virtualenv,
  applying migrations to `user_version=8`, shipping the UI and d3 checksums, and serving the new CLI.
  `docker compose config` validates.

---

## 9. Nothing trains

No fit. No weights learned from labels. No train/test split. No model file. No `numpy`, no
`scikit-learn`. The bias report's distribution helper is four indices into a sorted list.

**Runtime dependencies: five, unchanged** — `pysnmp`, `aiosqlite`, `fastapi`, `uvicorn`, `pydantic`.

The release measures, and writes down what it cannot know.
