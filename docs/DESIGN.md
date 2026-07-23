# Design

OptiCorr v0.1.0 is the explainable baseline of current alarm-correlation practice: one
Python process, one SQLite file, one web UI. This document records what was built, the
state of the art it builds on, and the explicit non-goals.

## Position in the state of the art

- **Pairwise relatedness.** Modern correlators (e.g. OpenNMS ALEC) reduce grouping to the
  question "are these two alarms related?". OptiCorr answers it with a three-term
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
2. origin / CSRF         cookie-auth mutations: Origin host == Host, X-OptiCorr-Client: ui
3. identity resolution   Bearer token -> api_token/legacy;  else opticorr_session cookie
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
  `secrets.token_urlsafe(32)`, store only its SHA-256, set the `opticorr_session` cookie
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
(DECISIONS v0.2 #3). Tooling: `python -m opticorr audit verify|export`.

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
