# Design

NetCoreNOC v0.1.0 is the explainable baseline of current alarm-correlation practice: one
Python process, one SQLite file, one web UI. This document records what was built, the
state of the art it builds on, and the explicit non-goals.

## Position in the state of the art

- **Pairwise relatedness.** Modern correlators (e.g. OpenNMS ALEC) reduce grouping to the
  question "are these two alarms related?". NetCoreNOC answers it with a three-term
  explainable score — temporal decay + learned class affinity + learned device
  affinity — and forms situations as connected components of the link graph. ML
  classifiers over the same pairwise question are a documented future upgrade, not part
  of v0.1.0.
- **Incremental co-occurrence learning.** The class matrix `A` and device matrix `E` are
  updated by normalized PMI over co-occurrence observations with exponential forgetting
  (λ = 0.05), temporal decay, a minimum observation count (n ≥ 5) before an `E` edge is
  trusted, and 10× damped updates during mass storms so confounders (e.g. a regional
  power outage) are not learned as topology.
- **Temporal precedence for root cause.** Per class-pair lead/lag statistics; within a
  situation, the alarm whose class and device most consistently "fire first" is flagged
  as the probable root. This is a lightweight, explainable alternative to Hawkes-process
  models.
- **Raise/clear pairing.** Learned from strict alternation of two classes on the same
  (device, instance); the linkDown/linkUp standard pair is pre-seeded.

## Explicit non-goals for v0.1.0

GNNs, deep learning, causal discovery algorithms (PC and similar), Hawkes processes, and
MIB semantic translation are out of scope. They are candidate upgrades only after the
explainable baseline has earned trust in production.

## Architecture

One asyncio process, one SQLite file (WAL), one static HTML page:

```
devices ──UDP 162──▶ receiver.py ──asyncio.Queue──▶ engine (main.py) ──▶ store.py (SQLite WAL)
                     │ allowlist                    │ dedup + flapping        ▲
                     │ defensive parse              │ clear pairs             │ one shared
                     └─▶ quarantine ────────────────│ learn.py (A, E)         │ connection +
                                                    │ correlate.py (links)    │ asyncio lock
                                                    │ rootcause.py (root)     │
operator ◀──HTTP 8080── api.py + ui/index.html ◀────┴──────────────────────────┘
```

- **Receiver** (`receiver.py`): SNMPv2c only, source allowlist, and defensive parsing —
  any packet that fails decoding is truncated and stored raw in `quarantine` with a
  diagnostic reason; nothing can crash or block the datagram callback. Backpressure is a
  bounded queue whose overflow is counted, never awaited.
- **Engine** (`main.py`): drains the queue in batches, one SQLite transaction per batch.
  Per activated alarm: fingerprint dedup (repeats only bump counters), periodic-flapping
  demotion (coefficient-of-variation test), co-occurrence and precedence observation,
  window scoring, and situation assignment as connected components with merge-on-bridge.
  Clears close alarms; fully cleared or idle situations close and run a learning epoch.
- **Learning** (`learn.py`): A (class×class) and E (device×device) as decayed
  co-occurrence masses. NPMI is computed against the activation total and shrunk by an
  n/(n+1) evidence discount so a single co-occurrence can never manufacture a link; one
  observation per activation per distinct other class/device keeps mass ≤ activations.
  Forgetting is (1−λ)^Δepochs applied lazily (an epoch = a closed situation). Storm
  updates (window ≥ 50 alarms) are damped 10×, and raise/clear alternation learning
  pauses entirely during storms — random interleavings would teach false clear pairs.
- **Correlation** (`correlate.py`): the three-term score over a 120 s window; work per
  event is bounded (≤ 100 candidates scored, ≤ 20 learned from, ≤ 5 links stored, ≤ 25
  members scored for root cause), so storms stay linear.
- **Storage** (`store.py`): thin hand-written SQL behind one class; plain-SQL migrations
  via `PRAGMA user_version`; learned state persisted as versioned `edge` rows plus JSON
  marginals in `meta`; retention pruning bounds every history table. A single connection
  is shared by the engine and the API under an `asyncio.Lock` — commits must never
  interleave with another task's open cursor (found under load, regression-tested).
- **API/UI** (`api.py`, `ui/index.html`): FastAPI with static-token auth and per-client
  rate limiting; one static HTML file with d3-force polling the API — the living graph.

## Known limits (v0.1.0, by design)

- Cold start is ignorant until co-occurrence evidence accrues (n ≥ 5 for device edges);
  the first cross-device incident may briefly appear as per-device situations.
- Learned clear pairs are permanent for now (no unlearning; see ROADMAP).
- The alarm instance is a heuristic (ifIndex, else first payload varbind).
- Single writer: SQLite + one process is the scale envelope, validated at 1000 traps/s
  bursts; queue and storage sit behind small interfaces for the post-MVP swap.

## v0.2.0 — identity, authorization, tamper-evident audit

v0.2.0 adds a security perimeter to the **HTTP side only**. The trap path (receiver →
queue → engine → store) is untouched: no new lock, no new I/O, no new latency (prime
directive 2). The one carefully bounded exception on the datagram path is the F4 community
tag, a single in-memory HMAC over a short string (below).

### Schema (migration `0002_auth_audit.sql`, `user_version` 1 → 2)

Forward-only, tables-only, applies onto a populated v0.1.0 DB. Adds `user` (scrypt
password hash, role, `must_change_password`), `session` (SHA-256 of the cookie value,
idle + absolute expiry hints, source ip), `api_token` (SHA-256 of the value, named
identity, role, revocable), and `audit_log` (hash-chained, append-only via triggers). No
v0.1.0 table changes; the HMAC key and admin-saved config live as `meta` rows.

### Middleware order (outermost → innermost)

Requests are processed in a fixed order so each layer can rely on the ones before it:

```
1. security headers      always set (CSP, nosniff, frame-deny, referrer, no-store on /api)
2. origin / CSRF         cookie-auth mutations: Origin host == Host, X-NetCoreNOC-Client: ui
3. identity resolution   Bearer token -> api_token/legacy;  else netcorenoc_session cookie
4. bootstrap gate        must_change_password locks all but login/password-change/healthz
5. RBAC                  route's required permission looked up in rbac.py -> 401/403
6. rate / throttle       token-bucket per client (retained); login throttle per user+ip
7. handler               reads/writes under store.lock; audits mutating + sensitive reads
```

401 vs 403 vs 404: identity resolution failing anywhere is 401; a resolved identity
lacking the route's permission is 403; resource lookup (and its 404) happens only inside
the handler, after RBAC passed — so 403 never leaks whether an object exists.

### Auth flow

- **Login** (`POST /api/login`, unauthenticated): throttle check (per username **and**
  per source ip; 5 strikes then 1→2→4…→900 s) → look up user → scrypt verify with
  `hmac.compare_digest`; a **dummy** scrypt verify runs when the user is absent so timing
  and the error message are identical (no enumeration). On success: mint
  `secrets.token_urlsafe(32)`, store only its SHA-256, set the `netcorenoc_session` cookie
  (`HttpOnly; SameSite=Strict; Path=/`, `+Secure` when TLS on). A **new id is always
  minted** (fixation defence). `login.ok` / `login.fail` / `login.lockout` audited.
- **Session resolution**: hash the cookie, look up the row, reject if idle
  (`last_seen_at + 30 min`) or absolute (`created_at + 12 h`) expired; otherwise slide
  `last_seen_at`/`idle_expiry`. Expired rows are purged by the maintenance loop.
- **Logout** deletes the row. **Password or role change revokes all** of that user's
  sessions. `must_change_password` (bootstrap admin, or admin-forced) locks the app to
  login + password-change until cleared.
- **Service tokens**: `Authorization: Bearer <value>`; SHA-256 lookup in `api_token`,
  reject if revoked; maps to the token's named identity + role, CSRF-exempt. Legacy
  `OPTICORR_API_TOKEN` maps to synthetic identity `legacy-token` (role admin), warns at
  startup (names the variable, never the value), audits `legacy_token.used` once per
  process.

### Audit design

One `audit_log` row per mutating endpoint and per sensitive read (including denied
attempts). Rows are hash-chained: `entry_hash = sha256(prev_hash + canonical)`, canonical
= `json.dumps({id,ts,actor,role,source_ip,action,object_type,object_id,outcome,details},
sort_keys=True, separators=(",",":"))`, genesis `prev_hash` = 64 zeros. Writes happen at
the API call site under the store lock the handler already holds — no extra lock, and the
id is reserved as `MAX(id)+1` (only the oldest rows are ever pruned) so the id inside the
hash matches the stored id. `details` is redacted at the call site and again at the writer
(`audit.redact_details`). `BEFORE UPDATE`/`BEFORE DELETE` triggers make the table
append-only even to the app; the sole sanctioned deleter is the admin audit-retention
prune, which drops and recreates the triggers inside one locked, audited transaction
(DECISIONS v0.2 #3). Tooling: `python -m netcorenoc audit verify|export`.

### F4 community tagging (the one datagram-path change)

The receiver extracts the SNMPv2c community, computes
`community_tag = HMAC-SHA256(key, community)[:12 hex]` from an in-memory 32-byte key
(created once in `meta`, loaded at startup before the receiver binds), and discards the
plaintext immediately — it never reaches the queue, a table, or a log. This is CPU only
(one HMAC of a short string), no lock and no I/O, so the ingestion invariant holds.
Quarantine sanitizes the raw packet: locate and zero the community octets, or, if the BER
is too malformed to locate them, store metadata only (`sha256`, length, first 8 bytes).

### SSE design

`GET /api/events` (viewer+) returns a hand-rolled `StreamingResponse` of `text/event-stream`:
a small async generator emits a `hello` event, then `stats`/`graph`/`situations` deltas
on a short cadence, and a `: heartbeat` comment every 15 s to keep the connection alive
through proxies. No new dependency (no `sse-starlette`). The UI consumes it with
`EventSource`; if the stream drops, the client falls back to the existing polling loop, so
SSE is an optimisation of the update path, never a hard requirement.

### Config precedence

Env vars remain the defaults. An admin saving allowlist/retention from the UI writes a
`meta` row (`config.allowlist`, `config.retention_days`); once set, the `meta` value takes
precedence over the env default. Every change is audited (`config.change`) and applied to
the running receiver/maintenance loop. Resolution rule: `meta value if present else env
default`.

## Known limits (v0.2.0, by design)

- Login throttle and rate limiter are in-memory, single process (matches the one-process
  identity); a restart resets counters. Sessions/tokens/audit are durable in SQLite.
- No MFA, no external IdP, no password-reset-by-email in v0.2.0 (ROADMAP); password policy
  + throttling + audit is the assurance level.
- The audit-retention prune is the one path that can delete audit rows; it archives first
  and is itself audited, but it does drop/recreate the append-only triggers to do so.

## v0.3.0 — the learned entity model

v0.3.0 stops assuming the sender is the sufferer. The device is still the trap's source IP,
but *what* is alarmed — the ONU, the port, the camera — is learned from the varbinds and
carried as a first-class `entity`. Everything is additive, in-memory in the engine, flushed
by `maintenance()`, and reduces to v0.2.0 behaviour until evidence promotes an entity.

### Where profiling runs relative to the store lock (invariant 2)

Nothing changes on the datagram path. The receiver still parses, tags, and enqueues, and
`datagram_received` gains no lock and no I/O. The varbind profiler is updated **in the
engine**, inside `_process`, under the batch `store.lock` the engine already holds — it is
pure in-memory counter arithmetic over the varbinds already on the `TrapEvent`. The durable
`varbind_profile` rows are written only by `maintenance()`, alongside the existing
`learner.save`/`precedence.save` flush, and stale profiles are pruned there too. So the
profiler adds no lock, no I/O, and no latency to ingestion.

### The profiler's three terms, and why X is decisive

For each `(NE, class, varbind OID)` the profiler keeps bounded counters — observations, a
capped value-frequency map (keys are `sha256(value)[:8]`, so attacker strings never enter the
hot dictionary), repeat count, monotonic-run count, numeric count. A candidate entity
discriminator is scored by three explainable terms, mirroring the link score's discipline:

```
R = n_repeat / n_obs                     # values recur (an index recurs; a timestamp never does)
X = mean cross-class Jaccard of value sets  # the same token appears under different classes
D = 1 - n_monotonic / max(1, n_numeric)  # not a counter, not a sequence number
S_entity = 0.35·R + 0.45·X + 0.20·D
```

**X is decisive** and carries the largest weight because only an entity identifier makes the
*same* value appear across *semantically different* alarm classes on the same NE. ONU 42
appears in both loss-of-signal and dying-gasp; a timestamp never repeats a value at all, a
sequence number never repeats a value across classes, and a constant tag has no
discriminating power (its Jaccard is 1 but its distinct count is 1, so the cardinality and
distinctness gates reject it). `X` is computed per `(NE, varbind OID)` as the mean Jaccard of
tracked value sets over each pair of classes that both carry the OID, and is `0.0` when fewer
than two classes carry it.

### The promotion predicate (conservative by construction)

A candidate becomes an NE's entity discriminator only when **all** of: `S_entity ≥ 0.60`,
`n_obs ≥ 200`, `distinct ≥ 2`, `distinct / n_obs ≤ 0.50` (reject per-trap-unique values like
timestamps and serials), and `S_entity ≥ 1.25 × S_entity(runner-up)` (an unambiguous winner,
else wait for more evidence). Promotion is **per NE**, not per class — the discriminator is a
property of how a device reports; applying it per class would fragment the entity space. A
late promotion costs accuracy; an early wrong promotion costs trust, so every threshold
resolves toward promoting later.

### Hierarchy by functional dependency

Given two promoted candidates `a`, `b` on one NE with `distinct(a) < distinct(b)`, the
profiler computes over co-observed events the fraction of `b`-values mapping to exactly one
`a`-value; if that fraction ≥ `FD_THRESHOLD` (0.95) over ≥ `FD_MIN_PAIRS` (100)
co-observations, `a` is the parent of `b`. This classical functional-dependency test recovers
PON port → ONU, chassis → slot → port, and NVR → camera with no MIB, inventory, or vendor
assumption. Depth is capped at `MAX_ENTITY_LEVEL` (3); deeper structure is a ROADMAP line.

### Affinity at NE level, and the parity argument

`Learner.device_affinity` becomes `entity_affinity`, but the learned matrix stays at **NE
level**: same entity → 1.0; same NE, different entity → `SAME_NE_AFFINITY` (0.8); different NE
→ the learned NPMI `E[ne_i, ne_j]` (n ≥ 5), exactly as today. An entity×entity matrix on a
1 000-NE network with thousands of ONUs per OLT is O(10¹²) and impossible; the learned
topology that matters is between NEs, and intra-NE proximity is a structural fact, not a
statistic.

**This is why parity holds.** Before any promotion every NE has exactly one entity, so the
`SAME_NE_AFFINITY` branch is unreachable and `entity_affinity` is numerically identical to
v0.2.0's `device_affinity`. The parity gate proves this against the frozen baseline, and the
schema makes it mechanical: `UNIQUE (entity_id, class_id, instance)` on a database where each
NE has one level-0 entity is exactly the v0.2.0 `UNIQUE (device_id, class_id, instance)`.

### Schema (migration `0003_entity.sql`, `user_version` 2 → 3)

Forward-only and **additive** (DECISIONS v0.3 #22), applying onto a populated v0.2.0 DB. It
adds `ne` (one row per device, ids preserved), `entity` (one level-0 entity per NE at
backfill), `ingest_gap`, and `varbind_profile`; it augments `alarm` with `ne_id`, `entity_id`
(backfilled to the level-0 entity), `severity`, and `severity_rank`, and adds
`UNIQUE (entity_id, class_id, instance)` as an index alongside the retained v0.2.0 constraint.
`alarm.device_id` is kept and synced for one version to avoid a big-bang API/UI rewrite; its
removal is a v0.4.0 line. The two unique keys stay consistent because an alarm's `instance`
always carries its entity's discriminator value (the heuristic value at level 0, the promoted
discriminator value once an entity is learned), so no two distinct entities ever collide on
`(device_id, class_id, instance)`.

### Severity and state (learned, honest fallback)

Severity is a varbind with a small ordinal range appearing across classes whose ordering is
*validated* against observed alarm lifetimes (not assumed); unvalidated severity stays
`unknown` and is rendered as unknown — a fabricated severity is worse than none. State-based
clear adds a sibling `StateClearLearner` at the varbind level: a varbind that strictly
alternates between *exactly two* values on a `(device, instance, class)` for enough cycles is
a state field, its terminating value the clear value (routed by `_handle_state_clear`). The
two-value rule is self-selecting — an identifier or a multi-level severity poisons its slot and
is never learned. Both are additive; the class-level `ClearPairLearner` is unchanged, until a
field is learned nothing is routed (parity), and storm suppression applies identically.

### Performance and durability (P0, shipped first)

The 120 s window scan is made non-quadratic — an O(1) removal index, `islice` over the last
`max_candidates`, and an absolute `MAX_WINDOW_ALARMS` cap with oldest-first eviction — and
dropped traps leave a durable `ingest_gap` trace (queue-full or window-overflow) surfaced in
`/api/stats` and the UI. SNMPv1 traps are mapped into the pipeline per RFC 3584 (the UDP
source is the NE, the v1 agent-address is exposed as a varbind, DECISIONS v0.3 #21).

## Known limits (v0.3.0, by design)

- An NE begins undivided; entity structure appears only as evidence accumulates. This is
  deliberate (invariant 4) and observable via `entity.confidence` / `entity.key_source`.
- The profiler trusts its trap sources; a source already trusted to report can bias learning
  within the thresholds. The admin-only, audited entity/profile reset is the recourse.
- Entity affinity is NE-level; intra-NE structure is the fixed `SAME_NE_AFFINITY`, not a
  learned entity×entity statistic (deliberate, above).
- `alarm.device_id` is redundant with `ne_id` for one version; its removal is a v0.4.0 line.

## v0.4.0 — hardening under the NetCoreNOC identity (no new inference)

v0.4.0 adds security and reliability, not intelligence. The design additions are all on the
HTTP/maintenance side; the ingest path (`datagram_received` → queue → engine) is frozen
(Invariant 2). **No schema migration is required.** The chosen work — response shaping,
task supervision, readiness, integrity checks, graceful shutdown, container hardening, the
corpus/simulator — is presentation-, control-, or tooling-side and touches no table or column.
The `device_id` cutover, the one change that *would* need a `0005_*.sql`, is re-deferred to
v0.5.0 (DECISIONS #35), so this release ships **zero** migrations.

### Response-shaping serializer (`netcorenoc/shaping.py`) and its role model

A single, small module owns field-level authorization so it is *one* decision point, never
scattered `if role ==` checks (the same discipline `rbac.py` applies to routes). The model:

- **Roles are ordered** viewer < editor < admin (reusing `rbac.ROLE_RANK`); a field is declared
  with the *minimum role* that may see it in full. A caller below that role gets the field
  **redacted** (dropped) or **coarsened** (e.g. a source IP → its `/24` network, a `community_tag`
  → absent) — never the raw value.
- **Deny-by-default for fields**: a serializer takes the store's row dicts and a role and returns
  a role-appropriate projection. Endpoints call it in one place; adding a field without a rule
  means it is not emitted to lower roles until a rule is written (fail-closed).
- **What is shaped** (from the §A.3 audit): `quarantine.source` and quarantine internals (admin
  full, editor coarsened, viewer counts-only via stats), session `source_ip` (never to non-admin),
  `community_tag` (admin/editor only), raw device IPs in situation/graph/timeline (viewers get the
  label or a coarsened form where a label is absent). The shaping is presentation-only: the engine
  and the audit log keep full fidelity.

### Task supervision

`main.run` replaces the bare `asyncio.gather(*tasks)` with a `supervise(name, coro_factory)`
wrapper per long-lived task (receiver-drain via the engine, maintenance loop, SSE is per-request
so it is guarded differently). A supervised task that raises is: logged through the redaction
filter, counted, restarted with capped exponential backoff where restart is safe (engine,
maintenance), and surfaced through `operator_warnings()` as a persistent banner. A task that is
*cancelled* (shutdown) is not restarted. The supervisor never touches the trap datagram path.

### Readiness signal

`/healthz` stays a pure liveness probe (process up). A new **`/readyz`** is the orchestrator
readiness signal: it returns `200 {"status":"ready"}` only when the DB is reachable, migrations are
applied (`PRAGMA user_version` == latest), and the queue is below a saturation threshold; otherwise
`503 {"status":"not ready"}`. It is unauthenticated (orchestrators cannot present a session) and so
**leaks no detail** beyond ok/not-ok — the reasons live behind authenticated `/api/stats`.

### Store integrity and fault handling

`Store.open` runs `PRAGMA integrity_check` and `PRAGMA foreign_key_check` once and, on a damaged
result, records an `operator_warning` rather than crashing (a NOC trap sink must keep ingesting
even with a partly-damaged history DB). WAL checkpointing cadence is documented and left to
SQLite's automatic checkpointer plus the clean-shutdown commit. `sqlite3.OperationalError`
(locked/busy/disk-full) raised inside a batch is caught at the engine boundary, logged, folded
into an `ingest_gap`-style operator signal, and the batch is retried/abandoned without breaking the
audit chain (the chain only advances on a successful commit).

### Graceful shutdown

On SIGTERM the process stops the transport (no new datagrams), drains the queue into a final
bounded set of batches, flushes the profiler, runs a last maintenance pass, and commits — all
within a deadline. Because the audit chain only advances on commit, an interrupted drain leaves a
consistent chain (verified by `test_graceful_shutdown_*`).

### UI design-token system and role-gated rendering

`style.css` gains a `:root` design-token layer (colour, spacing, type scale, elevation, radii) with
a dark default and a `prefers-color-scheme: light` variant — **all in the stylesheet**, since the
strict CSP forbids injected/inline styles. Role gating stays where it is (the `TABS`/`canEdit`/
`isAdmin` gate in `app.js`) and is strengthened so a lower role's DOM contains *none* of the
higher-role control ids (hidden, not disabled). Every externally-sourced string still reaches the
DOM only through `text()`/`el(...,{text})`/`esc()` (F1). The UI stays exactly four files.

### Where the trap simulator lives (and why it never touches the runtime)

The declarative scenario format + simulator live under `eval/` and `tools/` only
(`eval/scenario_dsl.py`, `tools/trap_sim.py`), reusing `tools/trap_replay.py` for the real-UDP
path. They are **never imported by `netcorenoc/`** and add no runtime dependency. Realistic vendor
OIDs live exclusively in these scenarios and the corpus — the engine still learns them blind. This
is the structural guarantee behind the zero-config identity: vendor semantics are test data, not
runtime knowledge.

## v0.6.0 — the scoring seam (a versioned, swappable, explainable `LinkScorer`)

v0.6.0 stops treating the correlation formula as a fixed expression. The three-term score becomes
the **default implementation of an interface**, and an admin — only an admin — may retune its five
parameters, preview the effect on live data, apply with an audit trail, and roll back instantly.

**Nothing about cold-start behaviour changes.** At the default parameters the extracted scorer is
byte-identical to v0.5.0 on every fixture and produces a byte-identical `make eval` delta table.
The seam is a refactor before it is a feature; extracting the formula moved no number. The ingest
path is untouched: `receiver.datagram_received` gains no lock, no I/O, and no config read
(prime directive 2), and preview never touches it at all.

### The contract (`src/netcorenoc/scoring.py`)

```
LinkScorer (Protocol)
  scorer_id: str                              # "additive" is the built-in default
  contract_version: str                       # semver of the feature/output SCHEMA — "1.0" here
  score(features: LinkFeatures) -> LinkScore  # pure, deterministic, side-effect-free, inference-only
  params_fingerprint() -> str                 # stable hash of the active parameters, for provenance
```

`score()` being **pure, deterministic, side-effect-free and inference-only** is not documentation —
it is the type-level statement that no scorer reaches the network, the disk, or the clock. It is
what forecloses the rejected external-criterion design (DECISIONS #44) rather than merely
discouraging it.

**`LinkFeatures`** is an immutable dataclass the engine builds **once per candidate pair**. It
carries exactly what the current computation uses:

```
delta_t_s, class_i, class_j, class_affinity (A), ne_i, ne_j, entity_affinity (E)
```

plus reserved optional slots that are `None` throughout v0.6.0 and ignored by the default scorer:

```
severity_i/j, topo_distance, probable_cause_i/j, event_type_i/j
```

Those exist so §3.5's X.733 / 3GPP TS 32.111 features and v0.8.0's richer scorers are **additive**.
The versioning rule (DECISIONS #49) is written down and enforced: **adding an optional field is a
minor bump; changing or removing an existing field is a major bump.** A configuration whose
declared *major* contract version the running code does not support is **refused at activation** —
not coerced, not ignored.

**`LinkScore`** is the required, explainable output:

```
linked: bool, score: float, threshold: float, terms: list[TermContribution]
TermContribution = {name, weight, value, contribution}    with contribution = weight · value
```

Emitting a per-term breakdown is **contractual** (DECISIONS #50). The default emits exactly three —
`temporal`, `class_affinity`, `entity_affinity` — with the same numbers the API and UI show today.
The requirement is general so a future scorer with five terms can satisfy it honestly; the
*storage* stays specific (`link.term_t/term_a/term_e` are the default scorer's three
contributions), which is exactly why the schema, the API response, the UI, and the pre-existing
tests are byte-identical. Generalising persisted attribution is a named v0.8.0 task.

**`AdditiveScorer`** is the default: a frozen dataclass with `w_t, w_a, w_e, tau_s, threshold`
defaulting to the coded constants `(0.30, 0.35, 0.35, 30.0, 0.50)`, computing

```
s = w_t·e^(−Δt/τ) + w_a·A + w_e·E,      linked ⟺ s > threshold
```

`correlate.py` constructs one and delegates every scoring call to `self.scorer.score(features)`;
it no longer inlines the arithmetic. The module-level `W_T`/`W_A`/`W_E` become the dataclass
defaults — the v0.5.0 P2 tidy, completed here — and are re-exported from `correlate` so no caller
or test breaks.

#### The parity argument

Three properties make the extraction provably non-moving:

1. **Same operations, same order.** `AdditiveScorer.score` computes `term_t`, `term_a`, `term_e`
   and sums them in the identical order the inlined expression did. Floating-point addition is not
   associative, so *order* is the thing that had to be preserved, and it was.
2. **Same inputs.** `LinkFeatures` is populated by `Correlator.score` from the same
   `learner.class_affinity` / `learner.entity_affinity` calls with the same arguments; the reserved
   slots are `None` and are never read by the default scorer.
3. **Same comparison.** Acceptance stays the strict `score > threshold` on the same float.

The gate is mechanical, not argumentative: `make eval` in both modes must reproduce the sha256
recorded in `../gates/v0.6-phase-0.md` §2, and `tests/test_eval.py::test_cold_mode_reproduces_the_v020_baseline`
still compares per-scenario dicts with `==`.

### Where parameters are read — and the engine reload point

The active parameters are loaded **exactly where `tau` and `threshold` are loaded today**: at
`Engine` configuration load, on the engine/maintenance side. They are **not** read per packet,
**not** read per candidate pair, and **not** read in `receiver.datagram_received`.

The **reload point** is explicit: a parameter change takes effect when the engine reloads its
scorer configuration, which happens (a) at `Engine.start()`, and (b) at the start of the next
`Engine.maintenance()` pass after an apply or rollback — i.e. within one `MAINT_INTERVAL_S` (5 s).
It never takes effect mid-batch, so a batch is always scored by one configuration, and the
`config_id` recorded on a situation is always the one that actually scored it.

If the store is unreachable, the pointer dangles, or a stored row fails validation, the engine uses
the **coded defaults** and raises a persistent operator warning. The engine can never run with an
unvalidated formula, and it can never refuse to run for want of one.

### Fail-safe execution

Every scoring call goes through a wrapper. On any exception, timeout, or malformed `LinkScore`, the
engine falls back to the coded-default `AdditiveScorer` for the rest of the process, writes one
`scorer.fallback` audit row (system actor), and surfaces an operator warning. **Fail-safe, never
fail-open, never fail-stop** — a broken scorer degrades correlation to the shipped default rather
than stalling ingestion or silently linking everything. A test-only raising scorer under `tests/`
proves both the fallback and that the `Protocol` accepts a second implementation; **no second
scorer ships in `src/netcorenoc/`.**

### The `scorer_config` store (migration `0005_scorer_config.sql`, `user_version` 4 → 5)

Forward-only and additive, applying onto a populated v0.5.0 database:

- **`scorer_config`** — append-only, immutable rows (`id`, `scorer_id`, `contract_version`, the
  five parameters, `params_hash`, `created_by`, `created_at`, `note`), guarded by
  `BEFORE UPDATE`/`BEFORE DELETE` triggers that `RAISE(ABORT)`, exactly like `audit_log`. Unlike
  `audit_log` there is **no sanctioned deleter**: the retention prune does not touch it, because a
  situation's provenance must outlive the alarms that formed it.
- **`scorer_active`** — a one-row pointer (`CHECK (id = 1)`). Apply and rollback are the *same*
  operation: an UPDATE of this single row. History is never mutated, so rollback is one action and
  loses nothing.
- **`situation.scorer_config_id`** — nullable FK, backfilled to the seed. Provenance **by
  reference, not duplication** (DECISIONS #47): one row per parameter change rather than five
  floats per situation forever, and no second source of truth to disagree with the config table.
- **The seed** — one row equal to the coded defaults, marked active, with a `params_hash` a test
  asserts equals `AdditiveScorer().params_fingerprint()`. This is what makes a migrated database
  byte-identical *and* makes the backfill truthful: every pre-v0.6.0 situation genuinely was
  formed by the coded defaults.

Provenance is written where situations are already written — `Engine._assign_situation`, under the
batch lock — never in `datagram_received`.

**Honest limit**: `scorer_config_id` records the configuration a situation was *opened* under. A
long-lived situation that keeps absorbing alarms across a parameter change carries its original
id while later links were scored under the newer configuration. Per-link provenance is a ROADMAP
line, not a shipped claim.

### Validation and bounds (the footgun guard)

One function validates every parameter set, and an invalid set is a 4xx with a precise reason that
is **never stored**. Range checks alone are insufficient — a syntactically valid set can destroy
correlation — so the rules cover degeneracy too (DECISIONS #46):

| Rule | Constant | Why |
|---|---|---|
| `0 ≤ w_t, w_a, w_e ≤ 1` | — | a weight is a share of the score, not a gain |
| `0 ≤ threshold ≤ 1` | — | the score is bounded by the weight sum, itself ≤ 3 |
| `1.0 s ≤ tau_s ≤ 3600 s` | `MIN_TAU_S`, `MAX_TAU_S` | below 1 s the temporal term is a step function inside one batch; above the 120 s window it stops discriminating at all |
| `w_t + w_a + w_e ≥ 0.10` | `MIN_WEIGHT_SUM` | a vanishing weight sum makes every threshold unreachable — grouping stops silently |
| `threshold ≥ 0.01` | `MIN_THRESHOLD` | `threshold ≤ 0` links every candidate pair: one giant situation |
| `threshold ≤ (w_t+w_a+w_e) − 0.01` | `THRESHOLD_MARGIN` | `threshold ≥ max achievable score` links nothing, ever: every alarm is a singleton |

Every ambiguity resolved toward the tighter bound: a slightly-too-tight bound costs a little tuning
range, a too-loose one lets an admin shatter or collapse every incident on a production NOC.
Preview is a *warning* control, not a *prevention* control — it is directional and an admin may
skip it — so the store refuses the shapes that cannot be correct.

### Preview (read-only what-if)

`POST /api/scorer/preview` answers "what would these parameters do to *my* data?" without
committing anything (DECISIONS #48):

1. Read at most `MAX_PREVIEW_ALARMS` (5000) recent alarms from the DB, most-recent-first, then
   replay them in chronological order for a stable candidate ordering.
2. Run the **candidate** and the **active** scorer over the same candidate pairs, using the
   engine's own `Correlator` in a read-only mode, with the learned matrices held **fixed** — they
   are an input to a what-if, not an output of it.
3. Compute connected components for each and diff the two partitions.
4. Return the structural delta: situations before/after, which groups **merge**, which **split**,
   and links gained/lost.

It is **deterministic** (fixed ordering, no wall clock in the scored path), **bounded** (the alarm
cap *and* a hard `PREVIEW_TIMEOUT_S`), **admin-only**, **rate-limited** by the existing token
bucket, **read-only** (no link, situation, learned-state, or config write), and **off the ingest
path**. It imports nothing from `eval/` — the corpus harness stays the dev/CI gate and never
becomes a runtime dependency, and it would answer the wrong question anyway (corpus behaviour, not
this operator's).

**Its honest limit, stated in the UI**: preview reflects a bounded *recent* window and holds the
learned matrices fixed, so it predicts the **immediate** effect, not the long-run effect after `A`
and `E` adapt to the new grouping. It is directional, not exhaustive.

### RBAC and audit

Three capabilities in the single `rbac.py` map:

| Capability | viewer | editor | admin |
|---|:--:|:--:|:--:|
| `scorer.read` — active scorer id, parameters, per-term contributions | ✔ | ✔ | ✔ |
| `scorer.preview` — run a read-only what-if | — | — | ✔ |
| `scorer.write` — append a config, move the active pointer, roll back | — | — | ✔ |

Reading is viewer+ because the parameters *explain* grouping and are not a secret. Preview and
write are **admin-only with no editor delegation**, deliberately departing from the v0.5.0 draft's
"optionally editor": retuning the formula is a system-wide logic change, and security-relevant
ambiguity resolves toward the stricter option.

Three audit actions join the frozen catalog: `scorer.config.update` (admin; before/after in
`details`), `scorer.preview` (admin), and `scorer.fallback` (system actor). All three are covered
by the catalog-completeness test exactly as existing actions are.

### The `OPTICORR_*` removal

The alias-acceptance path and its once-per-variable deprecation warning are gone. Any `OPTICORR_*`
variable in the environment is a **hard startup error** naming each variable, its `NETCORENOC_*`
replacement, and `MIGRATION.md` — mirroring how v0.3.0 removed `OPTICORR_API_TOKEN`. The message
names variables, never values. A removed knob that silently no-ops is a *security* regression: an
operator still setting `OPTICORR_ALLOWLIST` would believe traps are filtered while every source is
accepted.

## Known limits (v0.6.0, by design)

- Provenance is per-situation, not per-link (above). ROADMAP.
- Preview is directional, based on a recent window with the matrices held fixed (above).
- The eval gate proves **parity at the defaults**, not the quality of a retuned configuration.
  Nothing here tells an operator their new weights are *better* — only what they would do to
  recent grouping. The corpus is not the operator's network.
- `scorer_config` cannot be pruned. Immutability is the tamper-evidence argument; the growth is a
  handful of rows per year.
- A determined admin can still detune correlation. The control is bounds + preview + audit +
  immutable history + one-action rollback + the coded-default fallback — visibility and
  reversibility, not prevention.
