# Audit: trap → situation classification against the 3 × 20 s KPIs

Technical audit of the correlation path (trap → alarm → situation) at commit `46e3039`, from five
perspectives: security, software and database architecture, ML architecture, power user, and
reliability. It answers three questions:

1. Is the per-pair latency gate the constraint that limits classification quality?
2. Can the appliance meet **20 s ingest + 20 s analysis + 20 s situation selection (60 s total)**
   under a trap storm?
3. Why do situations that are 4 to 7 days old not keep receiving the traps that belong to them?

It also sets the prerequisites for the planned second stage (automated test suites with
severity-based SLAs). Nothing in this document is built. It is a specification and a remediation
plan, written to the conventions in [`../architecture.md`](../architecture.md).

**Classification of every finding.** Each finding is one of three kinds:

- **Confirmed:** reproduced on this tree, with the measurement in §2.
- **Substantiated:** follows directly from the code cited, but was not reproduced end to end.
- **Information gap:** the audit cannot decide it without data that is not in the repository.

Severity is **Critical**, **High**, **Medium** or **Improvement**.

---

## 1. Executive summary

| # | Conclusion | Kind |
|---|---|---|
| 1 | The latency gate is not 175 µs. It is `LATENCY_BUDGET_US = 150.0` **per scored pair** (`engine/model/league_judge.py:95`). It is a **throughput** eligibility gate, not a decision deadline. It excludes one member: `boosted_trees`, at 166.55 µs on this host, ranked 6th of 7. The champion, `logistic_regression`, costs **4.71 µs per pair**. Raising the gate to 20 s would not change the champion and would not improve accuracy. It would remove the only guard against a slow champion collapsing storm throughput. | Confirmed |
| 2 | **KPI 1 fails on an alarm storm.** With many distinct fingerprints, the engine drains **81–105 traps/s**. A burst of 3 000 new alarms gives an enqueue → commit latency of **p50 17.3 s and max 28.3 s**. The model is a minor cost. Most of the time goes to Python feature construction, about 25 SQLite round trips per trap, and observe-only consumers, all serialised behind one lock. | Confirmed |
| 3 | **KPI 3 fails structurally for long-lived situations.** An alarm can only be compared with alarms *activated* in the last 120 s (window) or the last hour (recall rings). Repeats never refresh either. The `dt` feature is capped at 1 h, and training data spans about an hour at most. Measured: a related trap on the same element and port as a still-repeating fault opens a **new situation after 5 min**. After 1 h the old alarm is **not even a candidate**, so a 4-day-old situation is unreachable. | Confirmed |
| 4 | **A restart makes every open situation unreachable.** The correlator's window and rings are rebuilt only from new traffic. Measured: a related trap 30 s after a restart opens a new situation. Without the restart, it joins. | Confirmed |
| 5 | Model capacity is not the bottleneck. The seven league members are within 0.07 pairwise F1 of each other (0.834–0.900). The measured failures come from **candidate generation, time horizon, and pair-only decisions**. A larger model cannot fix them. A **situation-level retrieval and scoring stage** can. | Substantiated |
| 6 | The 20 s budgets for stages 2 and 3 are not independent in this design. Analysis and selection are one synchronous step. Queue wait (stage 1) equals the backlog multiplied by that step's service time. The extra time is best spent on a **settle pass** (§4.3), not on a slower per-pair model. | Substantiated |
| 7 | Stage 2 has a naming collision before it starts. `pending` already means "a model proposal awaiting an operator" (migration `0027`). The planned *pending customer / carrier / MW* states must not reuse it. | Confirmed |

---

## 2. Evidence base

**Environment.** 4 vCPU cloud sandbox, Python 3.12.3. In process, with the engine fed directly; no
UDP. Temp-file SQLite. Packaged league, champion `logistic_regression:94cf73566cc2`. Absolute
numbers are indicative and must be reproduced on reference hardware (gap G2). Ratios and
mechanisms are robust.

### M1: per-pair model cost (`league_judge.measure_latency`, 1 000 benchmark pairs, median of 3)

| Member | µs / pair | Gate (150 µs) | Offline rank (score) |
|---|---:|---|---|
| decision_tree | 0.53 | eligible | 7 (0.8337) |
| gam | 1.09 | eligible | 5 (0.8849) |
| logistic_regression | 4.71 | eligible | **1 (0.9001), champion** |
| random_forest | 30.14 | eligible | 4 (0.8844) |
| knn | 50.68 | eligible | 3 (0.8912) |
| xgboost | 59.00 | eligible | 2 (0.8900) |
| boosted_trees | **166.55** | **excluded** | 6 (0.8754) |

### M2: activation storm, burst-enqueued, champion `logistic_regression`

| Load | Traps | Activations | Throughput | Pairs / activation | Enqueue → commit | Lock held per 500-trap batch |
|---|---:|---:|---:|---:|---|---:|
| `make loadtest` shape (20 devices × 10 classes) | 6 000 | 200 | **1 736 traps/s** | n/a | n/a | max 1.28 s |
| storm (400 devices × 60 classes × 48 ports) | 3 000 | 2 995 | **105 traps/s** | 120 (max 144) | **p50 17.3 s, max 28.3 s** | n/a |
| storm, same shape | 6 000 | 5 984 | **81 traps/s** | n/a | 74 s to drain | **max 8.63 s** |

Throughput falls as the burst grows (105 → 81 traps/s). Per-activation cost increases with
situation size (finding AR-5).

### M3: profile of 1 500 activations (cProfile, 27.3 s total)

| Component | Cumulative |
|---|---:|
| `Correlator.process` (whole stage 2 + 3) | 12.3 s (45 %) |
| ↳ `features.vector` (165 438 pairs) | 4.9 s |
| ↳ model fast path (`SafeScorer.fast` → `linear.logit`) | 3.8 s |
| ↳ `EpisodeMemory.observe` | 1.6 s |
| `Precedence.pick_root` (per activation) | 2.9 s |
| `LeagueShadow.observe` | 1.8 s |
| `CorrelationMonitor.observe` | 1.2 s |
| aiosqlite thread hand-offs | ~37 000 (≈ 25 per trap) |

### M4: long-lived situation probe

Alarm A (element `10.9.9.1`, port 1) is active and repeats every 10 min (every 30 min in the last
row). Alarm B is a sibling trap type in the same vendor subtree, on the same element and port,
arriving *gap* after A's first activation. Champion `logistic_regression`, `join_bias = 1.5`.

| Gap | A scored against B? | Evidence (log-odds) | Result |
|---|---|---:|---|
| 5 s | yes | +4.18 | joined |
| 60 s | yes | +2.14 | joined |
| 5 min | yes | +0.72 | **new situation** |
| 15 min | yes | −0.24 | **new situation** |
| 58 min | yes | −1.44 | **new situation** |
| 62 min | **no, not a candidate** | n/a | **new situation** |
| 96 h | **no, not a candidate** | n/a | **new situation** |

### M5: restart probe

A, then B 30 s later (same pair as M4). Without a restart, B **joins** A's situation. With an
engine restart between them, B **opens a new situation**.

### M6: league order arithmetic

`_order_key` buckets scores with `floor(score / TIE)`, where `TIE = 0.005`. This gives 0.8949 →
bucket 178 and 0.8951 → bucket 179: different buckets 0.0002 apart. It also gives 0.8900 and
0.8949 → the same bucket, 0.0049 apart.

Reproduction scripts are in Appendix A.

---

## 3. Findings by agent

### Agent 3: ML and deep learning architecture (the question asked first)

**ML-1 [Critical, Confirmed]: long-lived situations are unreachable by candidate generation.**

- **Evidence.**
  - `engine/correlate/pairs.py:33` sets `WINDOW_S = 120`.
  - `engine/correlate/retrieval.py:68` sets `RING_S = 3600`, and `:152` cuts recall on the
    *activation* time (`new.ts - alarm.ts > RING_S`).
  - `engine/operate/engine.py:324-325`: a repeat returns before `Correlator.process`, so it never
    re-enters the window or the rings.
  - M4: no candidate after 62 min.
- **Mechanism.** Candidates are found by alarm activation time. A situation that has been burning
  for 4 days on alarms that only repeat has no member that can be recalled.
- **Impact.** KPI 3 fails for every long-lived case. Duplicate situations follow. In stage 2, each
  duplicate starts its own SLA clock and its own test runs.
- **Fix.** See §4.2:
  - a **situation index** of open situations, keyed on structure (elements, entities, OID parents,
    referenced addresses) and on **last activity**, with no age cut;
  - an in-memory `last_seen` that repeats refresh at O(1), with no correlation.
- **Test.** M4 and M5 as regression tests in `tests/lifecycle/`.
- **Acceptance.**
  - For any open situation that shares an element, entity or referenced address with a new
    activation, the situation is among that activation's candidates in 100 % of cases, whatever
    its age.
  - The long-horizon suite (ML-3) meets its pre-registered F1.

**ML-2 [High, Confirmed]: pair recency is measured from first activation, so repeats carry no
signal.**

- **Evidence.**
  - `features.py:142-148`: `dt` is computed from `WindowAlarm.ts`, the activation time, and capped
    at `MAX_DT_S = 3600`.
  - `chatter` counts activations, not repeats.
  - M4: evidence decays from +4.18 to −1.44 while A was repeating every 10 min.
- **Fix.**
  - Add `dt_last_seen`, the seconds since the other alarm last reported, and `other_active`, a
    flag that the other alarm has not cleared.
  - Compute both in the one `features.vector` so that training and serving stay identical.
  - The offline replay must emit repeats.
- **Acceptance.** On the long-horizon suite, the pairs of a still-repeating fault and its new
  siblings score above `join_bias` at the pre-registered rate.

**ML-3 [High, Confirmed]: the training distribution has no multi-hour or multi-day incidents.**

- **Evidence.**
  - `eval/synth/spread.py:1`: incidents span *"minutes to an hour"*.
  - `league_judge.SUITES` (`league_judge.py:84`) has no long-horizon suite.
  - `MAX_DT_S = 3600`.
- **Impact.** The models have never seen the situations this audit is about. Moving the time cut
  alone would make them extrapolate.
- **Fix.**
  - Add synthetic families for chronic faults, periodic degradation over days, and recurrence after
    a lost clear.
  - Add a `test_longlived` suite, pre-registered before any model is trained on it (the project's
    principle 8, `docs/analysis/`).

**ML-4 [Medium, Confirmed]: the latency gate depends on hardware and is measured once per
process.**

- **Evidence.**
  - `scorer_lifecycle.py:172-179` measures once, on the event loop, at the first reload.
  - `league_judge.py:317-321` excludes on an absolute µs value.
  - `boosted_trees` measured 166.55 µs here. The 175 µs figure in the brief is consistent with
    another host's measurement.
- **Impact.** Champion eligibility can differ between hosts and between boots. This conflicts with
  principle 3 (determinism).
- **Fix.**
  - Replace the absolute µs gate with a gate **derived from the design storm** (§4.1). Measure it
    as all-in cost per activation, features included, because features cost 5× the champion
    model (M3).
  - Take the median over several reloads, with hysteresis.

**ML-5 [Medium, Confirmed]: per-pair cost is dominated by Python feature construction, not by the
model.**

- **Evidence.** M3: `features.vector` takes 4.9 s and the model 3.8 s, for 165 438 pairs.
- **Fix.**
  - Hoist the per-activation invariants out of the per-pair loop: `new`'s episode-table rows,
    class row and entity row.
  - Score candidates in one pass per activation.
- **Acceptance.** All-in cost per pair is at most half of today's 24.4 µs on the same host.

**ML-6 [Medium, Confirmed]: the offline tie rule does not do what its comment says.**

- **Evidence.**
  - `league_judge.py:147-148`: the comment says *"two members within TIE are equal"*, but the code
    buckets by `floor(score / TIE)` (M6).
  - Today `xgboost` (0.8900) is ordered before `knn` (0.8912) on the tie-break.
- **Fix.** Compare `|score − leader| ≤ TIE` against the best-scoring member.
- **Governance.** This changes day-0 champion selection, so it needs an ADR and a pre-registration
  entry.

**ML-7 [Medium, Confirmed]: root cause considers only the 25 earliest members.**

- **Evidence.**
  - `rootcause.py:15` sets `ROOT_SAMPLE = 25`.
  - `rootcause.py:65` sorts *all* members on every activation.
- **Impact.** In a 7-day situation, a later true root cannot be named root, for example a fibre cut
  that joins an ongoing degradation. The cost of the sort grows with the situation (AR-5).
- **Fix.**
  - Keep the earliest K in an incrementally maintained structure.
  - Sample the earliest K, the most recent K and the most severe K.
  - Optionally name a root per episode.

**ML-8 [Improvement, Substantiated]: duplicate situations are not measured.**

- **Evidence.** `CorrelationMonitor` keeps aggregate counters in memory only. No metric counts
  *"a new situation was opened while an open one shares its element or entity"*.
- **Fix.**
  - Add that counter, and a re-home rate for the settle pass (§4.3).
  - Both are KPI 3's online proxy.

**ML stack maturity.** It is high on governance:

- a judge with paired t-intervals;
- shadow challengers;
- sealed hold-outs;
- explainability by contract;
- a fail-safe scorer.

It is low on **time horizon** and **situation-level modelling**.

**Recommended model architecture.** Keep a pairwise model for tier 1. It is explainable, costs
microseconds, and is already measured. Add a **situation-level scorer** for tier 2 (§4.2):

- Gradient-boosted trees or a GAM over about 20 alarm-to-situation features.
- Each alarm has at most about 16 candidate situations, against about 120 candidate pairs.
- So even a 200 µs model costs about 3 ms per activation, inside budget.

Sequence models, transformers or graph networks are **not justified** by this evidence. They also
collide with the zero-runtime-dependency rule; resolving that collision is §6, C2.

### Agent 2: software and database architecture

**AR-1 [Critical, Confirmed]: storm throughput is below KPI 1.**

- **Evidence.** M2: 81–105 traps/s; a 3 000-activation burst reaches p50 17.3 s, max 28.3 s.
- **Mechanism.**
  - One asyncio process, one aiosqlite connection, one `store.lock`.
  - About 25 awaited SQL round trips per trap, each a thread hand-off (M3).
  - About 120 pairs × about 24 µs of Python per activation.
  - `pick_root`, `LeagueShadow`, `CorrelationMonitor` and `capture` all on the critical path.
- **Fix, in order of cost to benefit.**
  1. Write once per batch with `executemany`: `add_link`, `touch_situation` and `set_root` once
     per *situation* per batch, not per activation.
  2. Move the observe-only consumers to a bounded in-memory side queue drained by `maintenance`:
     capture, shadow, league shadow, monitor. They decide nothing (`engine.py:366-392`).
  3. ML-5.
  4. If the design storm still is not met, run correlation in a worker process separate from HTTP
     (standard library `multiprocessing`; no new dependency).
- **Governance.** `engine.py` is pinned (`TRAP_PATH_HASHES`, `COHESION_EXEMPT`). Each step needs an
  ADR and a re-pin.
- **Acceptance.** For the design storm (gap G1), p99 enqueue → commit ≤ 20 s on reference hardware.

**AR-2 [High, Confirmed]: the store lock is held for seconds during a storm.**

- **Evidence.** M2: up to 8.63 s per 500-trap batch (`BATCH_SIZE = 500`, `engine.py:77`). API
  handlers take the same lock.
- **Impact.** The console and the API freeze in exactly the moment the NOC needs them. API writes
  also delay the engine.
- **Fix.**
  - Bound each batch by **time**, for example 250 ms of work or 500 items, whichever comes first.
  - Serve API reads from a second, read-only WAL connection.
- **Acceptance.** Under the design storm, the lock is held at most 250 ms and the `/api/stats` p95
  is under 500 ms.

**AR-3 [High, Confirmed]: the queue bound is not tied to the SLA.**

- **Evidence.**
  - `runner.py:56`: `QUEUE_SIZE = 100_000`.
  - `/readyz` flips at 90 % (`api/routes/static.py:36,260`).
  - At about 100 activations/s, that is about 15 min of backlog before readiness fails, and about
    1 000 s before the first drop.
- **Impact.** A KPI 1 breach is invisible for 45× the KPI.
- **Fix.**
  - Track the **head-of-line age**, the age of the oldest queued item.
  - Report not-ready and raise a warning when it exceeds 20 s.
  - Count SLA breaches explicitly.

**AR-5 [Medium, Confirmed]: per-activation cost grows with situation size.**

- **Evidence.**
  - M2: throughput falls from 105 to 81 traps/s as the burst doubles.
  - `rootcause.py:65` sorts every member per activation.
  - `engine.py:563-565` writes `set_root` per activation.
- **Impact.** Long-lived situations, the subject of this audit, are where this is worst.
- **Fix.** ML-7, plus writes once per batch (AR-1).

**AR-6 [Medium, Substantiated]: one slow call permanently demotes the champion.**

- **Evidence.**
  - `scoring.py:238,340-343`: one call over 0.5 s degrades to the formula "for the rest of the
    process".
  - The degradation is sticky across reloads (`scorer_lifecycle` load key).
  - Candidate triggers: a GC pause, CPU steal, or GIL contention with the league judge's worker
    thread (`league_loop.py:58`).
- **Fix.**
  - Degrade on *k* consecutive violations or on a rolling p99.
  - Re-admit automatically after a probation period.
  - Keep the audit row.

### Agent 1: security and data protection

**SEC-1 [High, Substantiated]: trap-stream poisoning of learned state and grouping.**

- **Evidence.**
  - The allowlist is empty by default (`docs/security.md:41-44`), and v1/v2c is unauthenticated.
  - UDP sources can be spoofed.
  - Every activation updates the NPMI affinities, episodes and precedence (`engine.py:301-305`,
    `:355-364`; `correlate.py:290-294`).
  - `cross_ref` and named-neighbour recall read IPv4 addresses from **sender-controlled varbinds**
    (`features.py:101-117,163`; `retrieval.py:136-144`).
  - The archived threat model (`git show 3ecf237:docs/security/threat-model.md`) predates
    `cross_ref`.
- **Impact.** A forged trap that names a victim's management address pulls the victim's alarms into
  recall and sets `cross_ref = 1`. That can drive wrong merges, or proposals into `open`
  situations. In stage 2, it could trigger automated scripts against the wrong devices.
- **Fix.**
  - A "production" profile that refuses to start without an allowlist or SNMPv3-only.
  - Per-source rate limits on learning updates.
  - Ignore `cross_ref` and named recall for unauthenticated sources unless they are allowlisted.
  - A threat-model entry.
- **Test.** Inject forged traps that name a victim's address. Assert no merge into an `open`
  situation, and a bounded change to learned state.

**SEC-2 [High, Information gap]: stage-2 automation is a code-execution surface.**

- **Evidence.** Scripts written by NOC agents and run against network devices are not designed yet
  (`docs/architecture.md`, Phase 2).
- **Required before build.**
  - A credential vault; no device secrets in the DB or in scripts.
  - Per-script least privilege, with read-only command allowlists by default.
  - Review and signature of scripts.
  - Execution in a separate sandboxed process, never on the ingest path or under `store.lock`.
  - Per-device rate limits and a global kill switch.
  - Full audit of inputs and outputs.

**SEC-3 [Medium, Information gap]: personal data in varbinds and customer updates (LGPD/GDPR).**

- **Evidence.** Varbinds often carry `ifAlias` and circuit descriptions that name customers. They
  are stored verbatim and retained. The planned customer updates would carry them outward.
- **Required.** Classify varbind fields, define redaction rules for exports and customer updates,
  and confirm the retention periods with the data-protection owner.

### Agent 4: production power user

**OPS-1 [High, Confirmed]: the KPIs cannot be observed per stage.**

- **Evidence.**
  - There is one combined latency, `now - item.ts`, on committed batches only (`engine.py:208-212`),
    over 4 096 samples.
  - Rolled-back batches and queue-full drops are excluded, a survivorship bias.
  - `item.ts` is wall-clock `time.time()` (`ingest/receiver.py:387`), so NTP steps distort it.
- **Fix.**
  - Stamp monotonic times `t_recv`, `t_dequeue`, `t_scored`, `t_placed`, `t_commit` and
    `t_settled`.
  - Export a histogram per stage and an SLA-breach counter that includes drops.
- **Acceptance.** The console shows p95 and p99 per KPI stage, and breaches by cause.

**OPS-2 [High, Confirmed]: the repository's load test does not exercise the KPI scenario.**

- **Evidence.** `Makefile:111-113` (`make loadtest`) uses 20 devices × 10 classes, which is mostly
  repeats. M2: 1 736 traps/s for that shape, against 81 traps/s for an activation storm.
- **Impact.** False confidence: the gate passes the scenario the KPI does not describe.
- **Fix.**
  - Add `make storm`: N distinct fingerprints, with SLA assertions on head-of-line age and p99.
  - Run it nightly in CI on fixed hardware.

**OPS-3 [Medium, Confirmed]: duplicate situations move work onto operators.**

- **Evidence.** This is the consequence of ML-1 and REL-2.
- **Fix.** Until §4.2 ships, show "related open situations" for each new situation, from the same
  structural index, so that a merge is one action.

### Agent 5: reliability and edge cases

**REL-1 [High, Confirmed by code]: chronic faults spawn a new situation on each report after 1 h of
silence.**

- **Evidence.**
  - `engine/operate/occurrence.py:42`: `REARM_S = 3600`.
  - `occurrence.py:75-86`: a repeat after more than an hour of silence, from the situation's only
    active member, resolves the situation as `idle` and opens a new one.
- **Impact.** A fault that reports every 2 h for a week produces about 84 situations. In stage 2,
  that is 84 SLA clocks.
- **Fix.**
  - Learn the re-arm interval per class from inter-arrival times.
  - Link a new occurrence to its predecessor as a recurrence: a reopen, or a relation the situation
    index scores, rather than an unrelated situation.

**REL-2 [High, Confirmed]: a restart splits every ongoing incident.**

- **Evidence.**
  - M5.
  - `Engine.start` (`engine.py:161-200`) reloads membership (`sit_of`, `members`) but not the
    correlator's `window`, `index`, `recall` or `live`.
- **Impact.** Every upgrade or crash breaks KPI 3 for every situation that is open at the time.
- **Fix.** Rebuild the situation index (§4.2) and the recall rings from the store at start: open
  situations' active members, with `last_seen`.
- **Test.** M5 as a regression test.

**REL-3 [Medium, Confirmed by code]: storage errors and shutdown lose classified work.**

- **Evidence.**
  - A sqlite `OperationalError` rolls back and **drops** up to 500 traps (`engine.py:225-234`; F11,
    documented).
  - The shutdown drain is bounded at 5 s (`runner.py:57`), which is about 500 traps at the measured
    rate.
- **Fix.**
  - Retry the batch with backoff before dropping.
  - Count drops as SLA breaches (OPS-1).
  - Document the expected loss per restart at the design storm.

**REL-4 [Improvement, Confirmed]: status vocabulary collision for stage 2.**

- **Evidence.** `situation.status` has no CHECK constraint (`migrations/0014…sql:35-37`), and
  `pending` already means a model proposal (`migrations/0027…sql:9-14`).
- **Fix.**
  - Keep `status` as the lifecycle (`new | open | resolved`, plus the proposal state).
  - Put the planned work states in a separate `work_state` column with a CHECK constraint.
  - Rename the proposal state in the console to *proposed*.

---

## 4. Target architecture

### 4.1 What the 3 × 20 s budget actually constrains

Correlation (stage 2) and selection (stage 3) run in one synchronous call. So the time a trap waits
in the queue (stage 1) is set by the backlog ahead of it and the service time per activation:

```
queue wait of the last alarm in a burst of B activations ≈ B × s
KPI 1 (≤ 20 s)  ⇒  s ≤ 20 s / B_design
```

| Design burst B (activations) | Service time per activation s | Per-pair budget, features + model (≈ 50 % of s, 120 pairs) |
|---:|---:|---:|
| 2 000 | ≤ 10 ms | ≤ 42 µs |
| 5 000 | ≤ 4 ms | ≤ 17 µs |
| 10 000 | ≤ 2 ms | ≤ 8 µs |
| 20 000 | ≤ 1 ms | ≤ 4 µs |

Measured today: s is about 9.5–12.3 ms (M2), all-in.

**Conclusion.** For any storm larger than about 2 000 new alarms, 20 s makes the per-pair budget
*tighter* than today's 150 µs model-only gate, not looser. The extra time must therefore go where
it adds information (§4.3), not into a slower per-pair model.

### 4.2 Two-stage classification: alarm → pairs → situation

```
trap ─▶ ingest ─▶ activation ─┬─▶ tier 1: pairwise champion over window + rings (rings keyed on last_seen)
                              │        └─▶ pair evidence per candidate alarm
                              └─▶ situation index: open situations retrieved by structure + last activity,
                                       no age cut (elements, entities, OID parents, referenced addresses)
                                              │
                                              ▼
                              tier 2: situation scorer, one decision per candidate situation
                                      features: max/mean pair evidence · overlap counts · time since the
                                      situation's last activity · its age · size · active members ·
                                      recurrence count · severity delta
                                              │
                                              ▼
                              placement: join | new | propose (an `open` target is never grown; ADR #428)
```

- **Situation index.** In memory, bounded by the open situations. It is updated at placement,
  merge and close, and **rebuilt from the store at start** (fixes REL-2). It is read-only to the
  pair stage, so recall stays independent of grouping for the offline replay.
- **Repeats.** A repeat updates `last_seen` in the rings and in the index at O(1). It does not
  re-correlate (fixes ML-1 and ML-2 without per-trap cost).
- **Explainability.** The tier-2 scorer emits per-feature contributions, as the contract requires.
- **Data.** ML-3's long-horizon families are the training and evaluation set. The pair stage keeps
  its current suites.

### 4.3 Settle pass: what the 60 s budget buys

- **Placement.** The alarm is placed provisionally at once, within seconds.
- **Re-scoring.** At `t_recv + 45 s`, a settle pass re-scores it against what arrived after it:
  late siblings, and traps the management plane delivered out of order.
- **Re-homing.** It may move the alarm's membership in a `new` situation. For an `open` one it
  proposes, as `proposals.route` already does.
- **SLA start.** The stage-2 SLA clock starts at the situation's creation, which is the
  conservative choice. The settle pass only refines membership.
- **Metrics.** The re-home rate is reported (ML-8).

### 4.4 Stage-2 prerequisites (future; the decisions are the product owner's)

| Topic | Requirement |
|---|---|
| SLA policy | A versioned table, `severity → minutes`: Critical 25, Major 120, Minor 240, Warning 600. A durable `sla_deadline_at` per situation, which survives restart. |
| Unknown severity | Many vendors place no X.733 severity (`severity_rank = -1`). **Decision needed:** default to the strictest SLA or to a triage queue. |
| Escalation and merge | **Decision needed.** Recommended: on escalation, `deadline = min(current, now + new budget)`. On merge, keep the earliest start and the strictest severity. |
| Status model | `work_state` with a CHECK constraint (`awaiting_tests`, `tests_running`, `awaiting_noc_review`, `pending_customer`, `pending_carrier`, `pending_maintenance`, `monitoring`), separate from `status` (REL-4). A durable `next_review_at` for periodic review. |
| Execution | An outbox table written in the batch transaction, read by a separate worker process. Never on the ingest path or under `store.lock`. An idempotency key per (situation, script, attempt). A timeout per script. Results stored as evidence. Automatic resolution only above an evidence threshold the NOC defines. |
| Security | SEC-2. |

---

## 5. Consolidated risk matrix

Priority: **P0** blocks the KPI or creates silent wrong decisions; **P1** next; **P2** planned.

| ID | Finding | Severity | Kind | Agents | Priority | Reproduction | Acceptance |
|---|---|---|---|---|---|---|---|
| ML-1 | Long-lived situations unreachable | Critical | Confirmed | 3, 5 | P0 | M4 | 100 % candidate presence; long-horizon F1 per pre-registration |
| AR-1 | Storm throughput below KPI 1 | Critical | Confirmed | 2, 4 | P0 | M2 | p99 enqueue → commit ≤ 20 s at the design storm |
| REL-2 | Restart splits ongoing incidents | High | Confirmed | 5, 3 | P0 | M5 | B joins after a restart |
| OPS-1 | KPIs not observable per stage | High | Confirmed | 4 | P0 | code | per-stage p95/p99 and breach counter in console |
| OPS-2 | Load test misses the KPI scenario | High | Confirmed | 4, 2 | P0 | M2 | `make storm` gates CI |
| ML-2 | Recency from activation, not last seen | High | Confirmed | 3 | P1 | M4 | see ML-2 |
| ML-3 | No long-horizon training or evaluation data | High | Confirmed | 3 | P1 | code | `test_longlived` suite pre-registered |
| REL-1 | Chronic faults re-open hourly | High | Confirmed | 5 | P1 | code | one situation, recurrences linked |
| AR-2 | Lock held for seconds | High | Confirmed | 2, 4 | P1 | M2 | lock ≤ 250 ms; API p95 < 500 ms |
| AR-3 | Queue bound not tied to the SLA | High | Confirmed | 2, 4 | P1 | code | readiness fails on head-of-line age > 20 s |
| SEC-1 | Trap-stream poisoning, `cross_ref` | High | Substantiated | 1 | P1 | forged-trap test | no merge into `open`; bounded state change |
| SEC-2 | Stage-2 execution surface | High | Gap | 1 | before stage 2 | n/a | requirements in §3 met |
| ML-4 | Hardware-dependent latency gate | Medium | Confirmed | 3, 2 | P1 | M1 | gate derived from design storm, stable across boots |
| ML-5 | Feature construction dominates cost | Medium | Confirmed | 3, 2 | P1 | M3 | ≤ 50 % of today's µs/pair |
| AR-5 | Cost grows with situation size | Medium | Confirmed | 2 | P1 | M2 | flat throughput vs burst size |
| ML-6 | Tie rule differs from its comment | Medium | Confirmed | 3 | P2 | M6 | leader-relative tolerance; ADR |
| ML-7 | Root among 25 earliest only | Medium | Confirmed | 3, 2 | P2 | code | later root nameable; O(1) per activation |
| AR-6 | Sticky degradation on one slow call | Medium | Substantiated | 2, 5 | P2 | injected delay | k-of-n plus auto-recovery |
| REL-3 | Drops on storage error and shutdown | Medium | Confirmed | 5 | P2 | code | retry, then counted breach |
| SEC-3 | Personal data in varbinds and updates | Medium | Gap | 1 | before stage 3 | n/a | classification and redaction rules |
| OPS-3 | Duplicate situations cost operators | Medium | Confirmed | 4 | P2 | M4 | related-situations panel |
| ML-8 | Duplicate rate not measured | Improvement | Substantiated | 3 | P1 | code | metric live |
| REL-4 | `pending` vocabulary collision | Improvement | Confirmed | 5 | before stage 2 | code | `work_state` column |

---

## 6. Conflicts between recommendations

| # | Conflict | Resolution |
|---|---|---|
| C1 | "Give the models 20 s" (brief) against storm throughput (AR-1) | Per-pair cost stays bounded (§4.1). The time budget goes to tier 2 and the settle pass. |
| C2 | Heavier models against *zero new runtime dependencies* | Tier 1 and tier 2 stay pure Python (GBDT and GAM already are). Anything heavier needs evidence first, then an optional extra (#444's precedent) or a separate process. |
| C3 | Throughput refactor against *"ingestion is sacred"* and the pinned, unsplittable `engine.py` | The datagram path is unchanged. Engine changes go through an ADR and a re-pin of `TRAP_PATH_HASHES`. |
| C4 | A longer join horizon against over-merge (a wrong join into a 7-day situation is costly) | A situation-level scorer trained on bystander negatives (`spread.py`). `open` targets are proposed, never grown. |
| C5 | A per-class learned re-arm (REL-1) against predictable behaviour for operators | Show the learned interval per class in the catalogue, with an admin override. |
| C6 | A host-measured gate (ML-4) against determinism (principle 3) | Derive the gate from the design storm. Record the measurement as evidence, not as a decision input. |

---

## 7. Execution plan

| Phase | Scope | Exit criteria |
|---|---|---|
| **0: Instrument** | OPS-1 timestamps and histograms; head-of-line age (AR-3); `make storm` (OPS-2); M4 and M5 as `xfail(strict=True)` regression tests; define the design storm (G1) | Metrics visible; storm test reproduces M2 |
| **1: Throughput (KPI 1–2)** | AR-1 steps 1–3; AR-2; ML-5; AR-5 | `make storm` p99 ≤ 20 s at the design storm on reference hardware |
| **2: Classification (KPI 3)** | Long-horizon data and suite (ML-3, pre-registered); `last_seen` (ML-2); situation index and rebuild at start (ML-1, REL-2); tier-2 scorer; settle pass; REL-1 | Phase-0 tests turn green; long-horizon F1 meets pre-registration; duplicate rate (ML-8) below the target |
| **3: Governance and security** | ML-4, ML-6, AR-6; SEC-1 production profile and gating | ADRs merged; forged-trap test green |
| **4: Stage-2 prerequisites** | REL-4, §4.4, SEC-2, SEC-3 | Design reviewed before any runner code |

**Re-audit.** After each phase, re-run Appendix A, the full suite, `make eval` and `make security`.
For phase 1, also run `make storm` and a restart-recovery test. A single clean run is not
acceptance.

---

## 8. Information gaps (needed to close the plan)

| # | Gap | Why it matters |
|---|---|---|
| G1 | **Design storm:** peak traps/s, burst size in distinct alarms, sustained duration | Sets s and the per-pair budget (§4.1) |
| G2 | Reference production hardware (vCPU, disk class) | The M1–M3 numbers come from a 4 vCPU sandbox |
| G3 | Is there a relay or queue upstream of UDP 162? | KPI 1 includes it, and the appliance cannot see it (it stamps receipt only) |
| G4 | Statistic for each 20 s budget: p95, p99 or max, and at which load | Defines pass and fail |
| G5 | Labelled examples of real 4–7-day situations | Ground truth for ML-3; synthetic data alone is not enough |
| G6 | Source of the "175 µs" figure | The code says 150 µs; this host measures `boosted_trees` at 166.55 µs |

---

## Appendix A: reproduction

From the repository root, with the dev environment (`pip install -e ".[dev]"`).

**Per-pair cost (M1):**

```sh
.venv/bin/python -I -c "
from netcorenoc.engine.model import league, league_judge
lg = league.load(); v = league.benchmark_vectors()[:1000]
for m in lg.members: print(m.kind, round(league_judge.measure_latency(m, v), 2), 'us/pair')
for r in league_judge.offline_table(lg): print(r['rank'], r['kind'], r['score'])"
```

**Long-lived probe (M4).** A shortened version. The M5 restart probe is the same with
`Store.close()` and a new `Engine` between A and B.

```python
import asyncio, tempfile
from pathlib import Path
from netcorenoc.engine.operate.engine import Engine
from netcorenoc.ingest.events import TrapEvent, Varbind
from netcorenoc.store import Store


def ev(oid: str, ts: float) -> TrapEvent:
    return TrapEvent(
        device="10.9.9.1",
        trap_oid=oid,
        instance="1",
        ts=ts,
        varbinds=[
            Varbind(oid="1.3.6.1.6.3.1.1.4.1.0", kind="ObjectIdentifier", value=oid),
            Varbind(oid="1.3.6.1.2.1.2.2.1.1.1", kind="Integer", value="1"),
        ],
    )


async def probe(gap: float) -> list[int]:
    with tempfile.TemporaryDirectory() as tmp:
        s = Store(str(Path(tmp) / "p.db"))
        await s.open()
        e = Engine(s, asyncio.Queue())
        await e.start()
        t0 = 1_800_000_000.0
        await e._commit_batch([ev("1.3.6.1.4.1.2011.5.25.1", t0)])
        t = t0 + 600
        while t < t0 + gap:  # A keeps repeating every 10 min
            await e._commit_batch([ev("1.3.6.1.4.1.2011.5.25.1", t)])
            t += 600
        await e._commit_batch([ev("1.3.6.1.4.1.2011.5.25.2", t0 + gap)])
        cur = await s.conn.execute("SELECT situation_id FROM situation_alarm ORDER BY alarm_id")
        rows = [r[0] for r in await cur.fetchall()]
        await s.close()
        return rows


for gap in (5, 60, 300, 3700, 4 * 86400):
    print(gap, asyncio.run(probe(gap)))  # one id = joined, two ids = new situation
```

**Storm (M2).** Build N `TrapEvent`s with distinct `(device, trap OID, ifIndex)` fingerprints:
400 devices, 60 classes, 48 ports, and a varbind naming a peer address. Enqueue them all, run
`Engine.run()`, and read `Engine.latencies` (enqueue → commit) and `Engine.processed` over time.
For the lock-hold figure, time each `Engine._commit_batch` call on slices of 500.
