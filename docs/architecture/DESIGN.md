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

Those exist so §3.5's X.733 / 3GPP TS 32.111 features and v0.13.0's richer scorers are **additive**.
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
tests are byte-identical. Generalising persisted attribution is a named v0.13.0 task.

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

## v0.7.0 — governance: a stored capability policy and visibility scoping

v0.6.0 made the *link formula* configurable. v0.7.0 makes the *perimeter* configurable — which
capabilities a role or principal holds, and which network elements a viewer or editor is shown —
without adding an authorization mechanism, a second decision site, or a runtime dependency. Both
are stored policy read **through** the existing single decision points.

The governing property, and the release gate:

> With **no** stored governance policy, v0.7.0 is byte-identical to v0.6.0. The compiled
> `PERMISSIONS` map and full visibility are simultaneously the **default** and the **ceiling**.

### The capability resolver — `ceiling ∩ policy`, one decision site

`rbac.ceiling(role)` expresses today's behaviour as a set:

```
ceiling(role) = { c ∈ PERMISSIONS : ROLE_RANK[role] >= ROLE_RANK[PERMISSIONS[c]] }
```

`rbac.resolve_capabilities(role, principal_ref, policy)` is the **only** function in the tree that
computes a capability set:

```
caps = ceiling(role)                       # the compiled map is the FIRST operand
if policy is None or policy.malformed:     # unset, unreadable, or unparseable
    caps = caps                            #   -> the shipped safe baseline (DECISIONS #55)
else:
    if policy.roles.has(role):      caps &= policy.roles[role]
    if policy.principals.has(ref):  caps &= policy.principals[ref]
if role == "admin":
    caps |= RECOVERY_CAPABILITIES          # ⊆ ceiling("admin"), so the bound is preserved
```

**Why this is the escalation-impossibility proof, and not a check** (DECISIONS #53). An
intersection cannot exceed its first operand. A policy row naming a capability above a role's
ceiling is therefore **inert** — not "rejected", *inert* — regardless of how it entered the table:
through the API, through a future second write path, through a bad migration, or through
`sqlite3` on a stolen or restored database file. The API does return `400` for such a write, but
that is a usability affordance so an admin learns immediately; it is not the control. The property
`resolve_capabilities(...) ⊆ ceiling(role)` is asserted **property-based** over generated and
adversarial policies, which is only possible because it is a property rather than a code path.

**Unset vs. set-but-empty** (DECISIONS #54). An absent key means "this layer expresses no
opinion" ⇒ no intersection ⇒ the ceiling ⇒ parity. A key present with an empty list means "allow
nothing" ⇒ intersect with ∅ ⇒ nothing. The two are different statements, stored differently, and
the distinction is what lets an upgrade be invisible while still letting an admin deliberately
grant nothing.

**The admin can never be bricked** (DECISIONS #64). A *well-formed* policy could otherwise remove
`rbac.write` from `admin`, leaving no authenticated path to repair the perimeter — a lockout the
malformed-policy fallback would never catch. `RECOVERY_CAPABILITIES = {self.read, rbac.read,
rbac.write, scope.read, scope.write}` is unioned back for the admin role inside the resolver. Since
that set is a subset of `ceiling("admin")`, the union stays inside the ceiling and the invariant
above is untouched. Governance may still restrict an admin's `users.manage`, `audit.read`,
`config.write` or `scorer.write` — it simply cannot make the appliance unrepairable.

**Where it is called.** Exactly three places, all reading the one answer: the `api.py` `security`
dependency (replacing `rbac.role_allows(...)` **at that same line** — no new site), `GET /api/me`
(so the UI gates affordances on resolved capabilities rather than on role rank), and the generated
authorization-matrix test. A source-level assertion forbids a role comparison anywhere else in
`api.py` (F28).

**Per-request evaluation.** Nothing is cached on the session or the `Principal`. The resolved set
is computed on every request from the live policy, so a change takes effect on the next request
with no restart — a property the code already had, because authorization was never cached.
`ROUTE_PERMISSIONS` and the 401/403/404 semantics are unchanged; only *which capabilities a
principal holds* becomes policy-driven, so a route the resolved set does not cover is denied and
audited exactly as before.

### The scope resolver and filter

**The model.** A scope is a set of **selectors**, resolved to a set of NE ids on each request
(DECISIONS #57 — NetCoreNOC discovers NEs continuously, so a write-time snapshot would silently
hide an NE whose address a CIDR plainly covers):

| Selector | Form | Matches |
|---|---|---|
| NE id | `ne:7` | the NE with that id |
| Exact IP | `10.0.0.5` | the NE with that address |
| CIDR | `10.0.0.0/24`, `2001:db8::/48` | every NE whose address is in the network |
| Host glob | `core-*`, `*-lab` | the NE's operator label if it has one, else its address |

**Layer composition** (DECISIONS #63, generalising #54): an **unset** layer expresses no opinion; a
**set** layer — even set to the empty list — says "exactly these". The visible set is the **union
of the layers that express an opinion**; if neither does, it is **all NEs**. So: both unset ⇒ all
(parity); role set only ⇒ the role's set; principal set only ⇒ the principal's set (a per-principal
restriction genuinely restricts); both set ⇒ the union (the "this contractor *additionally* sees
the lab range" case).

**Admin is never scoped** (DECISIONS #58). The exemption is the first line of the resolver, before
any policy is read. This single rule is what makes every fail-closed branch in the release
recoverable rather than terminal.

**Fail-closed, and never fail-open.** A malformed or unreadable scope policy resolves to the
**empty** scope for viewer and editor — they see nothing new — with an `operator_warnings()` entry
and an audit row. It is safe precisely because the admin who must repair it is exempt.

**Enforcement — one filter, applied at every NE-bearing read.** `shaping.visible_nes(...)` returns
a `Scope` carrying the in-scope NE id set *and* the corresponding IP set (the graph is a `device`
projection joined to `ne` by address, not by id — id equality is a migration artefact, not a
declared invariant). Composition at each read path is: **authorize → read → scope-project → field
shape**. Concretely:

- **Lists** (`/api/situations`, `/api/graph`, `/api/entities`, `/api/timeline`, `/api/classes`
  where it enumerates NEs) return only in-scope NEs, entities, alarms, nodes, and edges. An edge is
  returned only when **both** endpoints are in scope, so the graph never implies a neighbour the
  caller cannot see.
- **A situation is listed iff at least one member is in scope.** Its out-of-scope members are
  **redacted to a coarse count and type** — no NE id, no IP, no entity key, no varbind (DECISIONS
  #59). Links referencing a redacted member are withheld; the root-cause hint is suppressed when
  the root is out of scope. Silent omission is rejected: an operator shown "3 alarms" for a
  40-alarm cross-boundary fibre cut would be *confidently wrong*, which is the failure mode
  v0.6.0's preview refuses to ship. The redaction count is the honest signal that the operator is
  looking at the edge of their own picture.
- **A directly-requested resource entirely out of scope returns 404, not 403** (DECISIONS #60),
  past authorization — and the 404 is produced by *the projection returning nothing*, so the
  handler's existing `if detail is None: raise 404` branch fires unchanged. "Out of scope" and
  "does not exist" are therefore indistinguishable **by construction** — same branch, same body,
  same headers, same timing — rather than by two code paths that happen to agree today.
- **Aggregates** that would let a scoped principal infer out-of-scope volume (`/api/stats`
  `devices`, `active_alarms`, `open_situations`) are computed over the in-scope set only, so
  out-of-scope activity cannot move a scoped viewer's counters.
- **SSE** (`/api/events`) re-resolves capability **and** scope on **every event**, not at
  connection time — a stream opened before a policy was written must not keep streaming
  unfiltered snapshots.

**Cost, and why parity is free.** When no scope policy is active — the default, and the state of
every upgraded appliance — the resolver short-circuits before touching the database and the read
paths run the unmodified v0.6.0 queries. The extra NE listing needed to resolve selectors happens
only when a policy exists and only for viewer/editor.

### Where policy is read, and how a change invalidates

Both policies live in `governance_policy` (append-only history) with a `governance_active` pointer
per kind. On each request the API reads the two-row pointer table and re-parses a document **only
when its id differs from the parsed one it is holding**. So: correctness is per-request (a change
is visible on the very next request, with no restart and no pointer to invalidate by hand), while
the parse cost is paid once per policy version. Clearing a policy deletes its pointer row; the
history rows survive, and the appliance returns to the shipped baseline.

**Nothing here is on the trap path.** `receiver.datagram_received` is unchanged and imports neither
`rbac` nor `shaping`; the engine, `learn.py`, `rootcause.py`, `severity.py` and `scoring.py` are
untouched. The v0.6.0 F24 source-level assertions remain in force, extended to name the governance
identifiers (F33).

### Schema (migration `0006_governance.sql`, `user_version` 5 → 6)

Additive, forward-only, and it **seeds nothing** — the one structural difference from `0005`, which
had to seed a parameter row because the engine needs parameters. Governance has a compiled default,
so seeding would be the only way to make an upgrade change behaviour, and not seeding is what makes
the upgrade invisible.

- `governance_policy` — `(id, kind ∈ {rbac, scope}, document, doc_hash, created_by, created_at,
  note)`, append-only via `BEFORE UPDATE`/`BEFORE DELETE` → `RAISE(ABORT)`, exactly like
  `audit_log` and `scorer_config`, and with no sanctioned deleter (the retention prune does not
  touch it). `document` is the **whole** policy for its kind as canonical JSON, not one row per
  grant: a policy is read and applied as a unit on every request, so storing it as a unit makes
  "which policy was active" a single id and makes rollback a pointer move rather than a replay.
- `governance_active` — at most one row per kind, an UPSERT to apply or roll back, a DELETE to
  clear. A pointer, not history, so deliberately not append-only (as `scorer_active`).

Validation deliberately lives in code, not in a `CHECK` constraint: a corrupt document must remain
*readable* so the resolver can recognise it as malformed and degrade safely. A constraint that made
the row unreadable would make it unrepairable.

### The unified candidate selection (the v0.6.0 close-out)

v0.6.0 shipped `preview.partition()` as a second implementation of the engine's windowing and
candidate selection, with its own copies of the window length and the candidate cap. Nothing tied
them together, so changing `correlate.WINDOW_S` alone would have made the what-if quietly lie.

`correlate.select_candidates()` is now the single implementation, used by both
`Correlator._recent_live()` and `preview.partition()`, and `preview`'s bounds are **aliases** of
the engine's constants rather than independent literals (DECISIONS #61). The helper takes the
window, the cut-off time, the cap, and an **optional liveness set** — which is the one genuine
difference between the callers: the engine's deque carries tombstones (cleared or re-activated
alarms removed from `index` but still in the deque), and preview's snapshot cannot. Each caller
keeps its own bookkeeping; only the *selection rule* is shared.

`tests/test_correlate.py::test_preview_reproduces_the_engine_partition` asserts that
`preview.partition()` reproduces the engine's **actual situation partition** over the same alarms —
so the what-if is pinned to the engine by a test, not by a coincidence. This lands **before** the
scoping read-filter, because layering a disclosure control on two implementations that may disagree
is how an existence oracle gets built by accident.

### RBAC and audit additions

| Capability | viewer | editor | admin | Audited when denied |
|---|:--:|:--:|:--:|:--:|
| `rbac.read` — the capability policy and each role's/principal's resolved set | — | — | ✔ | ✔ |
| `rbac.write` — set, roll back, or clear the capability policy | — | — | ✔ | ✔ |
| `scope.read` — the scoping policy and a principal's resolved NE set | — | — | ✔ | ✔ |
| `scope.write` — set, roll back, or clear the scoping policy | — | — | ✔ | ✔ |

All four are admin-only, `config`-class, with no delegation — and under `ceiling ∩ policy` that is
structural: no policy can move an admin-ceiling capability down to editor or viewer.

New audit actions, in the frozen catalog and the completeness test: **`rbac.policy.update`** and
**`scope.policy.update`** (admin actor, before/after summary in `details`, never a raw document
larger than its hash and shape).

### ⚠ Visibility scoping is a presentation control and is **NOT** tenant isolation

Scoping decides **what a principal is shown**. It does **not** partition what NetCoreNOC learns,
correlates, or groups:

- **Correlation still learns across all NEs.** The class×class `A` and NE×NE `E` matrices are
  global. A storm on NEs one operator cannot see still shapes the matrices that decide how the
  alarms they *can* see group, and a `confirm`/`split` from any operator still moves them.
- **Situations may span scope boundaries.** A situation is a connected component of a link graph
  computed *before* any scoping is applied. Scoping hides members after the fact; it does not
  prevent the situation from forming.
- **Side channels remain by construction.** Situation ids are global and monotonic, timing is
  shared, learned edge weights are global. Scoping is not designed to defeat inference from them.

**True multi-tenant isolation** — per-tenant learning, per-tenant situation boundaries, per-tenant
retention and audit segmentation — is a separate, larger, later feature that would change the
engine, the schema, and the eval methodology. It is explicitly **not** what v0.7.0 delivers, it is
a `docs/ROADMAP.md` line, and a documentation test asserts this statement is present in the shipped
docs and the shipped UI so it cannot be quietly dropped.

### A clarification carried forward from v0.6.0

`situation.scorer_config_id` records the scorer configuration that **created** the situation — the
one in effect when it was opened — and not necessarily the configuration under which every later
link was scored. A long-lived situation spanning a parameter change keeps its original id. Per-link
provenance would answer the finer question and is a ROADMAP line.

## Known limits (v0.7.0, by design)

- **Scoping is presentation, not isolation** (above). The global learned state, global situation
  ids and shared timing mean a determined observer can still infer *that* activity exists beyond
  their boundary from aggregate correlated behaviour.
- **A scoped operator sees a partial picture** and can mis-size an incident that spans the
  boundary. The redacted member count and type are the mitigation, and they are honest rather than
  complete.
- **Cardinality is information.** The redaction discloses *how many* members are out of scope —
  less than the situation id and `updated_at` a viewer already sees, but not zero.
- **The three roles stay compiled in** (DECISIONS #56). Only their restriction is data-driven;
  custom roles would put the first operand of the escalation-proof intersection into stored data.
- **Field shaping is not configurable.** `shaping.py`'s `FIELD_RULES` remain compiled policy;
  scoping restricts *which resources* are visible, not *which fields*. ROADMAP.
- **Policy history cannot be pruned.** Immutability is the tamper-evidence argument; growth is one
  row per change.
- **A compromised admin can rewrite the perimeter.** Bounded by the compiled ceiling, append-only,
  attributable, reversible by pointer, and audited — but not prevented, because an admin governs by
  definition.

## v0.7.1 — the write perimeter (a security patch: no new capability, no new surface)

v0.7.0 built the visibility scope as a **read projection** and described it as a perimeter. Its
review said so in as many words — scoping is enforced by "one filter applied to every NE-bearing
read". That sentence is true, and it is the defect: a perimeter enforced on one side is not a
perimeter. Six findings (F34–F39) follow from it, and this release closes the class rather than
the six instances.

Two sentences govern the design, and everything below is an application of one of them:

> **Authorization never reads data the constrained party can write.**
>
> **A write is inside the perimeter or it is a defect.**

### The write perimeter: which routes resolve scope, and how the 404 is produced

Of the 19 non-`GET` routes in `ROUTE_PERMISSIONS`, five require a capability below `admin`. Two of
them (`POST /api/logout`, `POST /api/password`, both `self.read`) act on the caller's own session
and account and reference no network element. The other three are the perimeter:

| Route | "In scope" means | Denies with |
|---|---|---|
| `POST /api/situations/{sid}/feedback` | at least one member alarm's NE is in scope | the handler's existing `404 no such situation` |
| `POST /api/situations/{sid}/close` | at least one member alarm's NE is in scope | the handler's existing `404 no such open situation` |
| `POST /api/labels` (`kind="device"`) | `scope.allows_ne(target)` | the F37 existence branch's `404 no such device` |
| `POST /api/labels` (`kind="class"`) | *not scoped* — an alarm class is a kind of trap, not a network element, and the table carries no NE reference. The same reasoning as `GET /api/classes`. | — |

Every other mutating route is admin-only, and **admin is never scoped** (DECISIONS #58), so the
perimeter is complete at three routes.

Three properties make this a perimeter rather than three checks:

1. **One decision site.** All three call the same `scope_for()` the read paths call. There is no
   second resolver and no `if ne in …` anywhere in a handler (DECISIONS #65).
2. **One membership predicate.** "At least one member in scope" is the predicate
   `project_situation_detail` already uses to decide whether a situation is listed at all. The write
   reuses it rather than restating it, so the read and the write cannot disagree about what "yours"
   means.
3. **One not-found branch.** A scope denial does not add a branch; it routes into the branch the
   handler already has for a nonexistent target. Out-of-scope and absent are therefore the same
   status, the same body and the same timing **by construction** — DECISIONS #60, applied to writes.

The denial is audited under the action the caller attempted, so a scoped principal probing the
write surface leaves a trail.

### The resolver-input invariant

`shaping.visible_nes()` resolves selectors against **NE identity and NE address only**. It reads
four inputs and every one of them is admin-written or engine-written:

| Input | Lowest role that can write it |
|---|---|
| `role`, `principal_ref` | admin (`users.manage` / `tokens.manage`) |
| the scope policy | admin (`scope.write`, no delegation) |
| `ne.id`, `ne.ip` | not writable through any API route |

The operator **label** used to be a fifth, and it is written by `POST /api/labels` — an `editor`
route. That made the scoped role an author of its own scope. The fix is structural rather than
guarded: the label leaves `_matches()` entirely, so a glob matches the address, and `Scope.labels`
plus the label column of `list_ne_for_scope()` are deleted (DECISIONS #66). Scope-checking the label
write would have closed today's path and left the next one open; removing the input closes every
path that will ever exist, which is the same argument that made `ceiling ∩ policy` the control in
v0.7.0 rather than a write-time validator.

The second path to the same escalation was the timeline. `store.timeline_marks()` renders a device
as `COALESCE(label, ip)`, and `_mark_visible()` compared that **display string** to the scope. Labels
are not unique, so copying an in-scope NE's label onto an out-of-scope one leaked its alarm timing
and classes. The projection now carries `ne_id` and the filter uses it; the rendered `device` field
is unchanged, so the UI is untouched (DECISIONS #67). **A display string is never an authorization
key.**

A test asserts the invariant over the resolver's inputs, and a comment at the resolver says why —
the comment alone would not fail CI when someone adds a fifth input.

### Transaction discipline: one mutation, one transaction, one audit row

`Store` holds **one** `aiosqlite` connection shared by the engine and the API, serialised by
`store.lock`. `commit()` on it commits everything pending, whoever issued it. v0.7.0's `main.py`
called `rollback()`; `api.py` called it nowhere, so a handler that mutated and then raised left the
statement pending and the **next commit from any other caller adopted it** — the mutation landing
with no audit row, which contradicts F31's "every change is attributable".

The fix is one async context manager, `write_txn()`, living beside `audit_row` inside `create_app`:

```
async with write_txn():          # acquires store.lock
    ... mutate ...
    await audit_row(...)         # same transaction
# commit on success; rollback() on any exception, then re-raise
```

Every mutating handler is converted mechanically. `Engine.apply_feedback`'s internal `commit()` is
removed so the API owns the boundary, which makes `POST /feedback` a single transaction in the order
**mutate → audit → commit**, matching every other write path instead of being the one route where
the mutation was durable before it was attributable (DECISIONS #73).

An `HTTPException` raised *before* any mutation (a 404 for an out-of-scope or absent target) still
unwinds through the rollback path. That is harmless — there is nothing to roll back — and it is
deliberately not special-cased: a discipline with an exemption is a discipline someone will get
wrong.

### Feedback: idempotence, and what an epoch is

Two independent defects met on this path. `store.add_feedback` had no uniqueness, no dedupe and no
bound; and `learn_epoch` advanced the **global** forgetting epoch, against which every stored mass
decays lazily by `(1-λ)^Δepoch`. Measured on the unmodified tree: 60 confirms then 20 splits moved
one pair's mass from 1.000000 to 1.824e-05 and wrote 80 rows.

- **Idempotence is per `(situation, verdict)`.** A `UNIQUE` index (migration `0007`) plus
  `INSERT … ON CONFLICT DO NOTHING`; the learning effect applies **only** on a genuine insert. A
  situation has two possible verdicts, so its total influence on learned state is bounded at two
  applications whatever anyone posts — bounded by the shape of the data rather than by a limiter
  someone can tune wrong (DECISIONS #68). A *changed* verdict is a legitimate correction and applies
  once.
- **An epoch is a closed situation** — which is what `learn.py`'s module docstring has said since
  v0.1.0. The tick is separated from the reinforcement: `_close_situation` ticks, the confirm path
  reinforces without ticking (DECISIONS #69). Global forgetting is a property of the correlation
  lifecycle, not of an operator's opinion about one grouping.
- **Attribution.** `feedback.principal_ref` and `feedback.role`, written from the calling principal,
  nullable and NULL for pre-existing rows. The audit chain recorded the API *call*; it did not
  record who caused the *effect*, and `Engine.apply_feedback` sits between the two.

### Filter before truncate

`list_situations` and `timeline_marks` applied `LIMIT` over the **global** ordering with the scope
filter running in Python afterwards, so a scoped principal's result set was a function of traffic
they could not see. Operationally that is worse than the disclosure — a scoped viewer's own open
incidents vanished from their list while a noisy neighbour was busy.

The scope predicate moves into the query, binding `ne_ids` as `IN (…)` placeholders exactly as
`store.scoped_stats` already did in v0.7.0. Two properties are deliberate:

- **The unrestricted path runs the unmodified v0.7.0 SQL.** Parity is by construction, not by
  inspection, and a test asserts the unrestricted result set is unchanged.
- **The parameter count is bounded.** SQLite's default `SQLITE_MAX_VARIABLE_NUMBER` is 32 766 on
  modern builds (999 on very old ones); the scoped branch caps the bound id list and falls back to
  post-filtering above the cap, so a very large in-scope estate degrades to v0.7.0 behaviour rather
  than erroring. The cap is a named constant next to the query.

### What did not change

The receiver, the queue, `datagram_received`, the engine loop, the window, candidate selection, the
scorer, `LinkFeatures`, and `LinkScore` gained nothing — `make eval` is byte-identical. The only
engine-side change is `Engine.apply_feedback` and its transaction boundary, a **feedback** path and
not a **correlation** path. `PERMISSIONS`, `ROUTE_PERMISSIONS`, `PUBLIC_ROUTES`,
`AUDITED_DENIED_PERMISSIONS` and `audit.ACTIONS` are frozen at 28 / 39 / 1 / 14 / 30. Runtime
dependencies stay at five.

## Known limits (v0.7.1, by design)

- **Scoping is still presentation, not isolation.** Every limit recorded under v0.7.0 remains true.
  This release makes the perimeter symmetric; it does not make it a boundary.
- **Label globs no longer work in scope selectors.** A labelled estate is scoped by address,
  `ne:<id>`, or CIDR. `scope_policy_errors()` warns at write time on a selector that currently
  matches zero NEs, so an admin learns immediately rather than from behaviour.
- **Idempotence per `(situation, verdict)` costs a real affordance**: an operator who wants to
  reinforce the same verdict twice cannot.
- **`label` still has no foreign key.** The existence check and the `0007` cleanup close the write
  primitive; the structural fix needs a table rebuild and is a ROADMAP line (DECISIONS #71).
- **`api.py` is still 1 600+ lines**, and four of six findings lived there because of it. The
  `perimeter.py` extraction is v0.7.2's theme, with its shape already agreed (DECISIONS #74) — a
  security patch is not the place to move the files its own fixes touch.

## v0.7.2 — the perimeter as a named component (internal structure only, no behaviour change)

v0.7.2 changed no behaviour. Not one route path, method, status code, response field or capability
moved; every handler body is byte-identical to v0.7.1 and the route table is identical **in order**.
What changed is that the HTTP security boundary stopped being a property of a 1 752-line file and
became a component with a name, a file, and a stated contract.

`src/netcorenoc/api.py` is now the package `src/netcorenoc/api/` — sixteen modules, largest 361
lines, one level deep. The module map and the rules that bind future releases are in
[`MODULE-ARCHITECTURE.md`](MODULE-ARCHITECTURE.md).

### The perimeter

**`src/netcorenoc/api/perimeter.py`. Read this file first if you are reviewing security.**

Everything in it decides *whether a request may proceed*. Nothing in it decides *what a request
returns*. That is the whole criterion, and it is why the write-side scope check sits beside the
read-side one even though the two lived four hundred lines apart in v0.7.1.

**What it owns**

| Concern | Member |
|---|---|
| Security headers on every response | `security_headers` (middleware body; `create_app` registers it) |
| Origin/CSRF for cookie-authenticated mutations | `csrf_ok`, `MUTATING` |
| Identity resolution (session cookie or bearer token) | `resolve_identity` |
| The forced-password-change gate | `BOOTSTRAP_ALLOWED` |
| Capability resolution | `security` → `rbac.resolve_capabilities` (called, never reimplemented) |
| Which denials are audited | `DENIED_ACTION` + its import-time assert against `rbac.AUDITED_DENIED_PERMISSIONS` |
| Per-client rate limit | `RateLimiter`, `RATE_CAPACITY`, `RATE_REFILL` |
| Visibility scope resolution | `scope_for` → `shaping.visible_nes` (called, never reimplemented) |
| The write-side scope check and its denial audit | `situation_in_scope`, `audit_scope_denial` |
| The write transaction boundary | `write_txn` |
| The audit-row helper | `audit_row` |
| Operator warnings, including an unreadable policy | `all_warnings` |

**What it does not own:** any handler logic, any response shaping, any SQL, any domain rule. The
policy *cache* it reads is a separate module (`api/governance_cache.py`) because a cache is not a
decision; the *decisions* are `rbac.resolve_capabilities` and `shaping.visible_nes`, both of which
live where they always have.

### The order the steps run

This paragraph used to sit at the top of `api.py`. It lives here now, and `api/app.py`'s docstring
points at it, so there is one description to keep true rather than two.

```
request
  │
  ├─ security_headers middleware  ─────────────── (a real ASGI middleware, runs on every response:
  │                                                 CSP, nosniff, DENY, no-referrer; no-store on /api)
  │
  └─ the `security` dependency, on every protected /api route:
       (1) CSRF        cookie-authenticated mutations only — origin/referer host must match the
                       Host header AND X-NetCoreNOC-Client: ui   → 403 otherwise
       (2) identity    bearer token, else session cookie        → 401 if neither resolves
       (3) bootstrap   an account owing a forced password change reaches only
                       POST /api/logout, GET /api/me, POST /api/password → 403 otherwise
       (4) RBAC        resolved per request as ceiling(role) ∩ granted(role) ∩ granted(principal);
                       the compiled map is the FIRST operand, so no stored policy can grant what
                       PERMISSIONS does not. THE authorization decision. A denial of a capability in
                       AUDITED_DENIED_PERMISSIONS is itself audited → 403
       (5) rate limit  token bucket per client address           → 429
       │
       └─ handler      audits every mutating action and every sensitive read, inside write_txn()
```

Steps (1)–(5) are unchanged from v0.2.0 through v0.7.1; only their location changed.

### The registration discipline

Before v0.7.2 a route was registered with `@app.get("/api/x")` and its capability lived in a dict in
`rbac.py`, joined by a path string. The join was invisible at the point of registration — which is
why the project needed a runtime fail-closed **and** a CI completeness test to catch what the code
could not express. F34 was the same shape one level down: a route's *scope posture* was expressed
nowhere at all, so three write routes simply did not have one.

`rbac.py` remains the single source of authority and now carries **two** declarations per route:

* `ROUTE_PERMISSIONS[(method, path)]` — the capability required. Unchanged.
* `ROUTE_SCOPE[(method, path)]` — `"scoped"`, `"unscoped"` or `"admin_only"`. New. Every
  `"unscoped"` carries a written justification (required by test), and `"admin_only"` is *derived*
  from `PERMISSIONS` at import in both directions, so the two tables cannot disagree.

Registration goes through `api/declare.py::DeclaredRoutes`, which refuses a route absent from either
table **while the application is being built** — so an appliance carrying an undeclared route does
not start. `PUBLIC_ROUTES` and non-`/api` paths are exempt by explicit consultation, never by
omission. A test asserts no raw `@app.<verb>` decorator survives anywhere in the package.

`ROUTE_SCOPE` is **descriptive in v0.7.2**: nothing reads it at request time, and a test checks
every declaration against the route's observed behaviour. Having the perimeter *inject* the scope
check from the table would change control flow, and control flow is behaviour — so that is a
ROADMAP line, not this release (DECISIONS #80).

### How forty handlers moved without changing

`api/context.py::AppContext` is a frozen dataclass carrying what the route modules need. Each route
module is one `register(app, ctx)` function whose **first statement** rebinds the fields it uses to
the local names the handlers already call them by:

```python
def register(app: FastAPI, ctx: AppContext) -> None:
    store, engine, security, guarded = ctx.store, ctx.engine, ctx.security, ctx.guarded
    scope_for, audit_row = ctx.scope_for, ctx.perimeter.audit_row
    route = DeclaredRoutes(app)

    @route.get("/api/stats")
    async def stats(principal: auth.Principal = Depends(security)) -> dict[str, Any]:
        # ... body identical to v0.7.1, character for character
        scope = await scope_for(principal)
        async with store.lock:
            out: dict[str, Any] = dict(await store.stats())
        return out
```

That block is mandatory, not stylistic. Rewriting the call sites to `ctx.audit_row(...)` would touch
every handler and forfeit the hash-by-hash proof that nothing moved — which is the most valuable
thing this release leaves behind (DECISIONS #78).

## Known limits (v0.7.2, by design)

- **This release does not make the perimeter more correct.** The same code in different files has
  the same behaviour. Every caveat in `SECURITY-REVIEW-0.7.1.md` §4 stands unchanged. What v0.7.2
  buys is a boundary a reviewer can read in one sitting and a discipline under which F34's class
  cannot recur silently — not a fix.
- **`ROUTE_SCOPE` is declared and tested, not enforced.** A contributor could declare `"scoped"` and
  write a handler that never resolves scope; the posture test catches that, but a test is what F34
  already had. The step from "tested" to "structural" is a later release's.
- **`Perimeter` is constructible outside `create_app`.** Convenient for tests, and a second way to
  instantiate the authorization machinery. Harmless — it holds no state a caller could not reach
  through `store` — and named in `SECURITY-REVIEW-0.7.2.md` §5.3 rather than left to be discovered.
- **`rbac.py` crossed the module-size guard** (348 → 436 lines) because `ROUTE_SCOPE` belongs in the
  single source of authority. It is on the debt allowlist with v0.7.4 as its owner and a named split
  seam: the declaration tables on one side, the capability-policy parser and resolver on the other.

---

## v0.7.3 — the data and engine layers become legible (internal structure only, no behaviour change)

v0.7.2 rebuilt the HTTP layer as a package and proved, hash by hash, that behaviour did not move.
It deliberately left the two largest files alone. This release does the same for them, under the
same rules and with a stronger proof, and it is the **last structural release**.

Nothing here changes what the appliance does. Not one status code, not one path, not one field, not
one row, not one number. `make eval` is byte-identical; all 109 `Store` method bodies and every
moved `Engine` method body are unchanged **text**, proved by a hash table taken before the move and
recomputed after it.

### The `store/` package, and the invariant it is built around

`store.py` (1 512 lines, 109 methods on one class) becomes `store/` — sixteen modules split along
`store.py`'s own section comments, which already marked the seams. One level deep, no module over
400 lines.

The mechanism is **mixins over a thin annotated base** (DECISIONS #88). `store/base.py` holds
`StoreBase`: the ten attribute annotations and the `conn` accessor, and nothing else — no queries,
no state, no behaviour. Every domain mixin inherits it; `Store` inherits every mixin;
`Store.__init__` stays in `store/__init__.py` and remains the **only** place those ten attributes
are assigned. The base *declares*, `__init__` *initialises*, and nobody duplicates.

Two mixins additionally inherit a sibling, because they call one: `AlarmMixin(DeviceMixin)` and
`ReadModelsMixin(GovernanceMixin)`. That is the whole of it — measured, not guessed. Exactly six
methods are called across a mixin boundary in the entire class, and five of them are those two
edges (the sixth is `conn`, already on the base). The alternative, restating those signatures on
`StoreBase`, needs stub bodies, which would put behaviour on the base and create a defect whose
failure mode is a silent no-op write.

**What this design is protecting:**

> **One `Store` class, one `aiosqlite` connection, one `store.lock`.**

This is not a style preference, and it is the reason the package is one object rather than sixteen.
The measurement that makes it concrete: **103 of the 109 methods, across 15 of the 16 modules, read
`self.conn`.** The connection *is* the cross-domain coupling. And `store.lock` is stranger still —
**no `Store` method acquires it**. It is taken entirely by callers: `Engine._commit_batch`,
`Engine.maintenance`, and `Perimeter.write_txn`. The lock is therefore a **public contract of the
`Store` object**, which is exactly why splitting `Store` into several objects would be invisible to
the data layer's own tests and catastrophic in production: every write path would silently stop
being mutually exclusive, and F39's failure mode — a mutation committed by an unrelated caller —
would come back with no test anywhere to notice.

`tests/test_store_concurrency.py` is the control. It counts the distinct connection and lock
identities reachable from the store, drives concurrent writes from three different domain modules
through `asyncio.gather`, asserts the audit chain survives 24 concurrent appends (the sharpest probe
available for a fragmented lock: two appenders that are not mutually excluded read the same
predecessor and fork the chain), and asserts `write_txn` still rolls back into nothing. It was
written and mutation-tested **against the pre-split tree**, so it measures the split by a rule that
predates it.

### The `Engine` and the runner, separated

`main.py` (1 079 lines) sheds four things and **stays a module** (DECISIONS #89), because
`python -m netcorenoc.main` is the documented way to run the correlator and `main.py` carries the
`if __name__ == "__main__"` guard. A package would need `main/__main__.py` and would change the
semantics of the one command every operator types.

| Module | Owns |
|---|---|
| `engine.py` | `Engine` — the batch lock and everything that reasons about it — plus `FlapDetector` and `EngineBase` |
| `maintenance.py` | the promotion sweep, severity confirmation, the profiler flush |
| `gaps.py` | `GapTracker`, `_OpenGap`, `_record_ingest_gaps`, `GAP_CLOSE_S` |
| `scorer_lifecycle.py` | the v0.6.0 seam's *lifecycle*: load, fall back, warn, audit |
| `settings.py` | `Settings`, `read_env`, the legacy-env errors |
| `runner.py` | `run()`, `Supervisor`, `operator_warnings`, the bootstrap banner |
| `main.py` | `main()`, the `if __name__` guard, and the re-exports |

**The ingest path does not fragment.** `run`, `_commit_batch`, `_process`, `drain`,
`_assign_situation`, `_handle_clear`, `_handle_state_clear`, `_close_situation`, `_resolve_entity`,
`_resolve_severity`, `_seed_clear_pair`, `_is_flapping`, `apply_feedback` and `FlapDetector` stay
together in one file. "Ingestion is sacred" (invariant 2, since v0.1.0) is only auditable if a
reviewer can confirm — **without following imports** — that nothing on that path takes a lock, does
I/O, or awaits where it must not. Fragmenting it would make the project's oldest invariant
unauditable, which is the opposite of what a structural release is for.

`maintenance()` and `maintenance_loop()` stayed too, against both the architecture document's and
the build plan's module tables (DECISIONS #90). `maintenance` is the only extraction candidate that
does `async with self.store.lock:` — the same `asyncio.Lock` object `_commit_batch` takes, because
there is only one — and the only one that calls a must-stay method (`_close_situation`). A reviewer
asking "what closes a situation, and under which lock?" must not have to follow an import to answer.

### The layer violation, resolved — and a rule that finally has a test

`MODULE-ARCHITECTURE.md` §1 has stated the dependency rule since v0.7.2 — *a layer may import
downward and may import cross-cutting, never upward* — and recorded one genuine violation:
`main.py` → `netcorenoc.api`, because `main.py` was the `Engine` (domain) **and** the process entry
point that builds the HTTP server, in one module.

Separating them resolves it structurally rather than by exemption: `runner.py` and `main.py` are the
entry point and may reach up into `http`; **`engine.py` may not**, and
`tests/test_layers.py::test_the_engine_does_not_import_the_http_layer` says so on its own.

Until this release **no test enforced the rule at all** — the existing guards asserted module size,
nesting depth, route order and import *resolution*, never import *direction*. That gap is why the
violation sat recorded-but-unfixed for a release. `tests/test_layers.py` (DECISIONS #92) parses
every module's imports, mirrors §1's layer table, and fails on any upward edge. Its exemption list
is **empty**.

### `COHESION_EXEMPT` — "large by design" is not "unfinished"

`engine.py`'s must-stay content measures 425 method lines before any scaffolding, so it cannot come
in under the 400-line guard — and directive 4 forbids splitting it *ever*. Filing it as debt would
be dishonest: `DEBT_ALLOWLIST` means "too big, will be fixed by release N", and there is no release
N here.

So the guard grows a second, narrower mechanism (DECISIONS #91): `COHESION_EXEMPT`, mapping a module
to **the invariant that forbids splitting it**, with five constraints each enforced by its own test —
the reason must cite an invariant by name from §1; a module may be in one list or the other, never
both; entries carry **no owner and no fix date** (that absence is the semantic difference, and it is
asserted); the exempt module may not grow past its recorded count; and at most **two** entries may
exist, so the escape hatch cannot become the default.

The same release also closes a hole in the older guard: "the allowlist may only shrink" was asserted
in one direction only — a *stale* entry failed, but a **newly added** module would have passed green.
`test_no_module_may_join_the_allowlist` fixes that.

## Known limits (v0.7.3, by design)

- **This release does not make the data layer more correct.** The same 109 methods in sixteen files
  have the same behaviour, and every residual risk in `SECURITY-REVIEW-0.7.1.md` §4 stands
  unchanged. What it buys is that `store.lock`'s single ownership and the ingest path's cohesion are
  visible in the file layout instead of being facts a reviewer reconstructs from 1 512 lines.
- **A mixin split makes it *easier* to forget the lock.** The neighbouring methods that would have
  shown a contributor the pattern now live in another file. The controls are
  `tests/test_store_concurrency.py` and v0.7.1's `write_txn` discipline — **not** the layout. This
  is a real cost of the release and it is stated rather than argued away.
- **`engine.py` is over the size guard and always will be.** That is the point of
  `COHESION_EXEMPT`, and the ceiling on it is real: it may not grow.
- **The declaration gate still covers three verbs and only the decorator form.** Two latent gaps
  found while reviewing v0.7.2 are specified in `MODULE-ARCHITECTURE.md` §10 and deferred to v0.7.4,
  because fixing a security-adjacent guard inside a move release forfeits the parity story for a
  latent, unexploited gap.
