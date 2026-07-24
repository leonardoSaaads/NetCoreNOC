# Changelog

All notable changes to this project are documented in this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - 2026-07-23 — "trustworthy by construction"

Security- and reliability-hardening release under a new identity. **No new inference features.**
One process, one SQLite file, zero new runtime dependencies. 283 tests, 95 % coverage, eval delta
byte-identical against the frozen baseline.

### Changed

- **Project renamed** from *OptiCorr* / *NewProjectNetworj* to **NetCoreNOC**
  (`github.com/leonardoSaaads/NetCoreNOC`). Import package `netcorenoc`, env prefix `NETCORENOC_*`,
  session cookie `netcorenoc_session`, CSRF header `X-NetCoreNOC-Client`. Legacy `OPTICORR_*`
  environment names are honoured for this one version with a deprecation warning (removed in
  v0.5.0); the cookie rename forces a one-time re-login. (DECISIONS #34)
- `GET /api/config` now requires a dedicated least-privilege `config.read` capability instead of
  the write capability. (F9)
- Response bodies are shaped by role: viewers receive coarsened device IPs (/24, /48) and never
  `source_ip` or `community_tag` — deny-by-default extended from routes to fields. (F7)
- Admin screens are pruned from a non-admin DOM (absent, not merely hidden); the UI gained a
  design-token refresh (light variant, focus states, AA contrast, responsive) — still four files.

### Added

- **Reliability**: supervised background tasks with backoff-restart and operator warnings (F10);
  startup `PRAGMA integrity_check`/`foreign_key_check` (F11); `/readyz` readiness endpoint (DB
  reachable + migrations applied + queue headroom, ok/not-ok only); graceful queue drain on SIGTERM.
- **Supply chain**: the vendored d3 is SHA-256-pinned (`ui/vendor/CHECKSUMS.txt`) with a CI job;
  the container is documented with a hardened run recipe and base-image digest pinning.
- **Standards**: `docs/SECURITY-REVIEW-0.4.md` — OWASP ASVS 4.0.3 L2 / NIST SP 800-63B / RFC /
  CIS compliance mapping.
- **Corpus/tooling**: a declarative scenario DSL (`eval/scenario_dsl.py`) + trap simulator
  (`tools/trap_sim.py`, `make sim`); security-event correlation and network-fault-breadth
  scenarios as engine-driven tests; a consolidated abuse suite.
- CI gains a dead-code gate (`vulture` + committed allowlist) and a d3-checksum job.

### Fixed

- **A built wheel shipped only `index.html`**, so `pip install .` (the Dockerfile path) served a
  UI whose `app.js`, `style.css`, and vendored d3 all 404'd. The whole UI now ships. (F12)
- The orphaned second audited-denied table (`rbac.AUDITED_DENIED_PERMISSIONS` vs
  `api.DENIED_ACTION`) is collapsed to one source with a divergence test. (F8)
- Removed confirmed dead code (`auth.ROLES`, `auth.now_s`, `store.set_user_disabled`,
  `VarbindProfiler.role_of`).

### Security

- CSRF enforcement now has regression tests (missing/renamed `X-NetCoreNOC-Client`, origin/host
  mismatch → 403); the rename could otherwise have silently broken it. (F14)
- All abuse-suite properties (CSP + headers on new routes, shaped-viewer injection inert,
  entity-key-forgery bound, append-only audit) confirmed to hold.

## [0.3.0] - 2026-07-23

Entity identity — learning *what* is alarmed, not merely *who* reported it. A network element
starts as a single entity and is subdivided only when the trap stream proves, statistically,
which varbind names the alarmed sub-object. Nothing about the ingestion path or the v0.2.0
grouping changes until something is learned: cold start is byte-identical to v0.2.0 on every
fixture, and all 171 v0.2.0 tests still pass. One process, one SQLite file, zero new runtime
dependencies.

### Added

- **Learned entity model**: each device becomes an `ne` plus a level-0 `entity`; a bounded,
  in-engine **varbind profiler** scores every varbind by three explainable terms
  (`S = 0.35·R + 0.45·X + 0.20·D`) and promotes the discriminator only when the evidence clears
  conservative floors (score ≥ 0.60, ≥ 200 obs, ≥ 2 distinct, cardinality ratio ≤ 0.50, and a
  1.25× margin over the runner-up). Promotion is **forward-only** — history is never
  reinterpreted.
- **Containment hierarchy**: a classical functional-dependency test recovers PON port → ONU,
  chassis → card → port, and NVR → camera without a MIB or inventory. Depth is capped.
- **Learned severity, honest fallback**: a small-ordinal cross-class varbind (bundled severity
  tokens or integers) becomes the severity field only when its ordering is *validated* against
  observed alarm lifetimes; otherwise severity stays **unknown** and is rendered as unknown.
- **State-based clear**: a varbind that strictly alternates between exactly two values on a
  `(device, instance, class)` is learned as that class's state field, its terminating value the
  clear — for platforms that carry raise and clear in one trap OID.
- **Entity affinity**: `device_affinity` becomes `entity_affinity`, kept at NE level (same
  entity → 1.0, same NE → 0.8, else the learned NE×NE affinity); reduces to v0.2.0 exactly
  before any promotion.
- **SNMPv1 (RFC 3584)**: v1 traps are mapped into the pipeline (NE = the UDP source, not the
  spoofable agent address, which is exposed as a varbind); no configuration.
- **Durable ingest gaps** (§5.6): queue-full and window-overflow drops are recorded as
  `ingest_gap` rows and surfaced in `/api/stats` and a UI banner — "events lost between t1 and
  t2" as first-class NOC information.
- **Inspectable UI + admin recourse**: a viewer **Entities** tab shows the entity tree
  (`key_source`, `confidence`) and the profiler evidence behind every decision, plus learned
  state fields; situation detail gains a **severity** column; admins can reset a poisoned
  identity (`entity.reset`) or wipe the evidence (`profile.reset`) — both audited and
  forward-only-safe.

### Changed

- **Performance**: the 120 s sliding-window scan is made non-quadratic — an O(1) removal index,
  bounded candidate iteration, and an absolute `MAX_WINDOW_ALARMS` cap with oldest-first
  eviction.
- The alarm uniqueness constraint becomes `UNIQUE (entity_id, class_id, instance)`, equivalent
  at level 0 to the v0.2.0 constraint (the mechanical basis of the parity gate). `device_id` is
  retained and kept in sync for one more version.

### Removed

- **`OPTICORR_API_TOKEN`** (deprecated in v0.2.0): setting it is now a hard startup error that
  names the service-token migration path; the `legacy_token.used` audit action is retired from
  the catalog (historical rows still verify).

### Migration

- Additive migrations `0003_entity.sql` (ne/entity model, profiler, ingest-gap tables; alarm
  gains `ne_id`/`entity_id`/`severity`) and `0004_state_clear.sql`, applied automatically at
  startup (`PRAGMA user_version` → 4). Populated v0.1.0/v0.2.0 databases upgrade in place with
  all data, the append-only audit triggers, and the hash chain intact. See `MIGRATION.md`.

## [0.2.0] - 2026-07-20

Identity, role-based authorization, and a tamper-evident audit log — plus remediation of
six findings from the independent v0.1.0 security review. The ingestion path is unchanged
and still lossless; all v0.2.0 security controls live on the HTTP side.

### Added

- **Accounts and roles**: viewer / editor / admin with a single deny-by-default permission
  map (`opticorr/rbac.py`); `401` unauthenticated, `403` insufficient, `404` only after
  authorization.
- **Authentication**: `scrypt` password hashing (n=2¹⁷, upgradeable) with NIST SP 800-63B
  length policy; server-side sessions with SHA-256-stored ids, 30-minute sliding idle and
  12-hour absolute timeouts; per-username/per-IP exponential login lockout with no user
  enumeration; a bootstrap admin printed once at first start; forced password change.
- **Service tokens**: admin-created, per-identity, per-role, revocable bearer tokens
  (stored as SHA-256), shown once at creation; replace the shared static token.
- **Tamper-evident audit log**: append-only (SQLite triggers) and hash-chained; covers
  authentication, management, operator actions, and sensitive reads, including denied
  attempts; `python -m opticorr audit verify|export` tooling; dedicated 365-day retention.
- **UI v0.2.0**: login page; role-aware navigation (viewers see no mutating controls);
  situation timeline; root-cause confidence; filters/search; Server-Sent Events at
  `/api/events` (heartbeat every 15 s) as the primary live-update path with polling
  fallback; admin screens for users, tokens, config, quarantine, and the audit log. Still
  four static files, no build step.
- **Optional built-in TLS** (`OPTICORR_TLS_CERT`/`OPTICORR_TLS_KEY`) with an auto-`Secure`
  cookie; reverse-proxy TLS documented in `SECURITY.md`.
- **CSRF protection** for cookie-authenticated mutations (`Origin`/`Host` match plus an
  `X-OptiCorr-Client` header; `SameSite=Strict` cookie).
- **Operator warning banners** for an empty allowlist or a non-TLS non-loopback bind.
- **Config via the UI** (allowlist, retention) with audited changes; env defaults are
  overridden by admin-saved values.
- `docs/threat-model.md`, `docs/SCOPE-0.2.md`, `docs/SECURITY-REVIEW-0.2.md`, `SECURITY.md`,
  `MIGRATION.md`; `make migrate` and `make audit-verify` targets; a CI secret-leak scan.

### Fixed (v0.1.0 security review)

- **F1 (High)** Stored XSS: all externally sourced strings now reach the DOM via
  `textContent`/`createElement`; strict CSP with locally vendored d3; security headers.
- **F2 (High)** Shared static token and `localStorage` storage removed in favour of
  sessions and per-identity service tokens.
- **F3 (Med)** No secret is written to logs; a root-logger redaction filter and a
  secret-leak test enforce it; the bootstrap banner is the one sanctioned exception.
- **F4 (Med)** The SNMPv2c community string is never persisted or logged; an HMAC
  `community_tag` is kept for grouping and quarantine blanks or omits the community.
- **F5 (Med)** Optional TLS with an automatic `Secure` cookie.
- **F6 (Low)** Insecure-default deployment now surfaces a persistent admin banner.

### Changed

- `create_app` takes a `legacy_token` (the deprecated `OPTICORR_API_TOKEN`) mapped to a
  synthetic admin identity `legacy-token`, with a startup deprecation warning and a
  one-time audit event. **Removal is scheduled for v0.3.0.**
- Schema migrated to v2 (`0002_auth_audit.sql`): `user`, `session`, `api_token`,
  `audit_log`, plus F4 quarantine columns and an alarm `community_tag`. Forward-only;
  applies to a populated v0.1.0 database.

## [0.1.0] - 2026-07-19

First release: a zero-configuration SNMP trap correlator in one Python process, one
SQLite file, and one web UI.

### Added

- SNMPv2c trap receiver (UDP 162) with source-IP allowlist, defensive parsing, and raw
  quarantine for malformed packets — nothing can crash or block ingestion.
- Zero-config discovery: devices from source IPs, alarm classes from trap OIDs, vendor
  names from a bundled IANA enterprise-number table, standard SNMPv2/MIB-II trap
  semantics built in.
- Deduplication by (device, class, instance) fingerprint with a periodic-flapping
  detector that demotes noisy fingerprints.
- Incremental learning: class-affinity matrix A and device-affinity matrix E via
  evidence-discounted normalized PMI with exponential forgetting (λ = 0.05 per closed
  situation), an n ≥ 5 trust threshold on device edges, and 10× damped updates during
  mass storms. The learned device graph is the living topology.
- Correlation: three-term link score (temporal decay + class affinity + device
  affinity) over a 120 s sliding window; situations as connected components; the three
  terms stored on every link for auditability.
- Probable-root hints from learned temporal precedence (class- and device-level
  lead/lag statistics).
- Raise/clear pairs learned from strict alternation per (device, instance), seeded with
  linkDown/linkUp; fully cleared situations auto-close and reinforce the matrices.
- Operator feedback: confirm reinforces a grouping, split penalizes it; cosmetic,
  persisted renames for devices and classes.
- FastAPI HTTP API with static bearer-token auth (autogenerated when unset) and
  per-client rate limiting; single-file d3-force web UI with the living graph,
  situation explanations, renames, and feedback buttons.
- SQLite (WAL) storage with plain-SQL migrations, versioned learned-edge persistence,
  and retention pruning; state survives restarts.
- Tooling: real-PDU trap replay (fixtures and synthetic load), Makefile
  (qa/security/run/replay/loadtest), CI with ruff, mypy --strict, pytest + coverage,
  bandit, and pip-audit; Dockerfile (non-root) and flake.nix.
