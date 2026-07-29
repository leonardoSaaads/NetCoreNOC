# Changelog

All notable changes to this project are documented in this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.7.1] - 2026-07-29 — "the write perimeter" (security patch)

**A security patch, not a feature release.** Six confirmed defects (F34–F39) in which a v0.7.0
guarantee was enforced on reads and not on writes. No new capability, no new route, no new
configurability, no restructuring. `PERMISSIONS`, `ROUTE_PERMISSIONS`, `PUBLIC_ROUTES`,
`AUDITED_DENIED_PERMISSIONS` and the audit action catalog are unchanged; runtime dependencies stay
at **five**; `make eval` is **byte-identical** to v0.7.0.

v0.7.0's review declared, under F32, that scoping is enforced by "one filter applied to every
NE-bearing read". That sentence is true, and it is the defect: the perimeter was designed as a
*read* projection and the three editor-level write routes were never brought inside it. Worse, one
of the resolver's own inputs — the operator label — was writable by the very role the scope
constrains. This release closes the class, not the six instances.

> **Authorization never reads data the constrained party can write, and a write is inside the
> perimeter or it is a defect.**

### Security

- **F34 (High) — scope is now enforced on the three `editor` write routes.** `POST
  /api/situations/{sid}/feedback`, `POST /api/situations/{sid}/close` and `POST /api/labels`
  resolve scope through the **same** `scope_for` the reads use, and deny through each handler's
  **existing** 404 branch, so out-of-scope and nonexistent stay indistinguishable in status, body
  and timing. Denials are audited. *Impact: a scoped editor could previously mutate global learned
  state for network elements they cannot see, and the 200-vs-404 split was an existence oracle.*
- **F35 (Critical) — an editor can no longer widen their own scope by writing a label.** Scope
  selectors resolve against **NE identity and address only**; the operator label is gone from the
  resolver, and the timeline filters on `ne_id` rather than on a rendered display string. *Impact:
  with a policy of `{"editor": ["core-*"]}`, labelling an out-of-scope device `core-pwned` used to
  add it to the editor's own visible set; a colliding label leaked an out-of-scope element's alarm
  timing and classes.*
- **F36 (High) — operator feedback is idempotent and bounded, and no longer ages global state.**
  At most one effect per `(situation, verdict)`, and the forgetting epoch advances only when a
  situation **closes**. Feedback rows now record `principal_ref` and `role`. *Impact: 60 confirms
  and 20 splits previously drove one pair's learned mass from 1.000000 to 1.824e-05, and the author
  was unrecorded.*
- **F37 (Medium) — a label write to a target that does not exist now returns 404.** Migration
  `0007` removes existing orphans. *Impact: every editor previously held an unbounded,
  never-reclaimed write primitive against the database file.*
- **F38 (Medium) — list endpoints apply their `LIMIT` after scope filtering.** *Impact: a scoped
  viewer's own open incidents used to vanish from their list when a noisy neighbour they cannot see
  was busy, and the returned count varied with out-of-scope volume.*
- **F39 (Medium) — every API write is one transaction: mutate → audit → commit, or nothing.** A
  single `write_txn()` helper rolls back on any exception. *Impact: a handler that raised after
  mutating previously left the statement pending on the shared connection, and the next commit from
  an unrelated caller adopted it — the change landing with no audit row.*

### Changed

Three deliberate behaviour changes at empty policy, and no others (`docs/scope/SCOPE-0.7.1.md` §2):

1. a label write to a target that does not exist returns **404** (was 200) — F37;
2. a repeated **identical** feedback verdict is a **no-op** (was applied and recorded each time) —
   F36;
3. list endpoints truncate after filtering — invisible at empty policy, and the unrestricted result
   set is asserted byte-identical — F38.

**Breaking for one configuration only:** a scope selector that relied on matching an operator
**label** (`core-*`) now matches by address or not at all, and is rejected at write time with a
message pointing at `MIGRATION.md`. Review any stored scope policy that uses one. Selectors by NE
id, exact address, CIDR and **address** glob (`10.0.*`) are unchanged.

### Added

- Migration `0007_write_perimeter.sql` — two nullable attribution columns on `feedback`, a
  `UNIQUE (situation_id, verdict)` index with prior de-duplication (earliest row by `created_at`
  kept), and the F37 orphan cleanup. **Seeds nothing; changes no behaviour by itself.**
- A **generated** write-perimeter test over `ROUTE_PERMISSIONS`: every mutating route below `admin`
  must resolve scope, so a route added in any future release fails CI until it is inside the
  perimeter.
- A **resolver-input invariant** test: no input to the scope decision may be writable by a scopable
  role.
- A transaction-discipline test and a feedback-boundedness test.
- `docs/security/SECURITY-REVIEW-0.7.1.md`, `docs/scope/SCOPE-0.7.1.md`, decisions **#65–#74**, and
  a `v0.7.1` threat-model extension. `SECURITY-REVIEW-0.7.md`'s F32 row is **superseded in place**
  with a dated note pointing to F34 and F38 — the published claim is left intact rather than
  rewritten.

### Fixed

- The UI now surfaces a failed feedback, close, or rename instead of silently swallowing the
  rejection.

## [0.7.0] - 2026-07-25 — "governance"

An admin can define what each role and principal may **do** and may **see**. Both are stored,
audited policy read through the **existing** single decision points — no new authorization
mechanism, no second decision site, no new runtime dependency, and nothing whatsoever on the ingest
path.

**With no stored policy, v0.7.0 is byte-identical to v0.6.0.** The compiled permission map and full
visibility are simultaneously the *default* and the *ceiling*. Migration `0006` seeds **no** rows,
so a fresh install and an upgraded one behave exactly as v0.6.0 did, and most operators never open
the Governance panel. That parity is a release gate, not a claim.

### Added

- **Admin-configurable RBAC.** A stored policy narrows what a role — or an individual principal —
  holds. The resolved set is `ceiling(role) ∩ granted(role) ∩ granted(principal)`, computed by one
  function (`rbac.resolve_capabilities`) that every caller reads. **Escalation is structurally
  impossible, not merely forbidden**: an intersection cannot exceed its first operand, so a policy
  naming a capability above a role's ceiling is *inert* — however the row arrived, including a
  direct `sqlite3` write to a stolen or restored database. Proven property-based over generated and
  adversarial policies (DECISIONS #53).
- **Per-role / per-principal visibility scoping.** Which NEs a viewer or editor may see, by NE id,
  exact address, CIDR, or name glob, resolved against the live inventory on every request — so an
  NE discovered after the policy was written is covered by a CIDR that plainly matches it
  (DECISIONS #57). One filter at every NE-bearing read; a graph edge survives only when **both**
  ends are in scope.
- **404, not 403, for an out-of-scope resource**, produced by the projection returning nothing so
  the handler's *existing* not-found branch fires. "Not yours" and "does not exist" are one code
  path — same status, same body, same timing — rather than two that happen to agree (DECISIONS #60).
- **Honest redaction.** A situation is listed if any member is in scope; out-of-scope members become
  a **count and their alarm classes** — never an id, address, entity key, or varbind — and links to
  them are withheld. Silent omission was rejected: an operator shown "3 alarms" for a 40-alarm
  cross-boundary fibre cut would be *confidently wrong* (DECISIONS #59).
- **Four capabilities** — `rbac.read`, `rbac.write`, `scope.read`, `scope.write` — admin-only,
  `config`-class, no delegation, all four audited when denied. **Two audit actions**
  (`rbac.policy.update`, `scope.policy.update`) with before/after, plus `governance.fallback` for a
  policy that will not parse.
- **Migration `0006_governance.sql`** (`user_version` 5 → 6): an append-only `governance_policy`
  history with `RAISE(ABORT)` triggers and no sanctioned deleter, plus a per-kind `governance_active`
  pointer. Apply, roll back, and **clear** are one call each; clearing removes the pointer, never
  the history.
- **A Governance panel** in the UI, and tabs/affordances now gated on the **resolved capability set**
  from `/api/me` rather than on role rank — a UI deriving permissions from rank would be a second
  decision site that silently disagrees with the server once a policy exists.

### Changed

- **One candidate-selection rule for the engine and preview** (the v0.6.0 close-out).
  `preview.partition()` was a second implementation of the engine's windowing with its own copies of
  the window length and the cap; a change to `correlate.WINDOW_S` alone would have left the what-if
  replaying a different window from the engine it claims to predict. `correlate.select_candidates()`
  is now the single implementation and preview's bounds are **aliases** of the engine's constants.
  A test asserts preview reproduces the engine's *actual situation partition*, member for member;
  another asserts the two callers cannot drift again (DECISIONS #61).
- `rbac.role_allows` is reimplemented on top of the new `rbac.ceiling` and is no longer called from
  `src/` — it answers the *ceiling* question and is kept as the independent oracle the parity gate
  compares the resolver against.
- `auth.Principal` gains `token_id` and a `ref` property (`user:<id>` / `token:<id>`), so a
  per-principal policy keys on row identity rather than on a display name that is not unique across
  users and tokens (DECISIONS #62).
- SSE re-resolves capability **and** scope on **every event**, not at connection time, and ends the
  stream if `events.stream` is revoked mid-connection.

### Security

- **F27–F33** in [`docs/security/SECURITY-REVIEW-0.7.md`](docs/security/SECURITY-REVIEW-0.7.md),
  each with a passing regression test: escalation via a stored policy, a second decision site,
  fail-safe and lockout, session staleness, provenance integrity, scope bypass and existence
  disclosure, and the hot-path surface.
- **Fail-safe in both directions, deliberately asymmetric.** A malformed *capability* policy falls
  back to the compiled ceiling (the shipped v0.6.0 baseline — nobody gains anything); a malformed
  *scope* policy denies viewer and editor (nobody sees anything new). Both raise an operator warning
  and write an audit row. The asymmetry is safe **only because admins are never scoped**
  (DECISIONS #55, #58).
- **An admin can never be locked out.** A *well-formed* policy could otherwise remove `rbac.write`
  from the admin role, leaving no authenticated path to repair the perimeter. A small compiled
  recovery set is unioned back inside the resolver; it is a subset of the admin ceiling, so the
  escalation invariant is untouched (DECISIONS #64).

### ⚠ Visibility scoping is a presentation control and is **not tenant isolation**

Correlation still learns across **all** network elements, and a situation may still *form* across a
boundary a principal cannot see — its members are hidden from them, not prevented from correlating.
A scoped operator therefore sees a partial picture, which is exactly why the redacted count is
shown rather than the members silently dropped. True multi-tenant isolation is a separate, larger
feature on [`docs/ROADMAP.md`](docs/ROADMAP.md); v0.7.0 does not provide it, and says so in the
docs, in the API responses, and in the UI, with a documentation test asserting the statement cannot
be quietly dropped.

### Quality

426 → **499 tests**; coverage **95.43 %** (up from 95.24 %); `make eval` **byte-identical**;
`ruff`, `ruff format`, `mypy --strict`, `vulture`, `bandit`, `pip-audit`, structure guard, link
check, SHA-pin lint and the d3 checksum all clean. Runtime dependencies unchanged at **five**.

## [0.6.0] - 2026-07-25 — "the scoring seam"

The correlation formula stops being a hard-coded expression and becomes the **default
implementation of a versioned, swappable, explainable interface** — plus admin-tunable
parameters with safe preview and one-click rollback. **Grouping behaviour does not change**: at
the default parameters v0.6.0 produces byte-identical output to v0.5.0 on every fixture and a
byte-identical `make eval` delta table. One process, one SQLite file, one static UI, **zero new
runtime dependencies**, and not one byte of new work on the ingest path.

### Added

- **The `LinkScorer` seam** (`src/netcorenoc/scoring.py`): a `Protocol` with `scorer_id`,
  `contract_version`, `score(LinkFeatures) -> LinkScore` (pure, deterministic, side-effect-free,
  inference-only) and `params_fingerprint()`. `LinkFeatures` carries exactly what the current
  computation uses plus **reserved optional slots** (`severity_i/j`, `topo_distance`,
  `probable_cause_i/j`, `event_type_i/j`, all `None` in 0.6) so X.733/3GPP features and future
  scorers are a *minor* contract bump, never a breaking one. `LinkScore.terms` makes a per-term
  breakdown **contractual**, so "why did it decide that?" can never regress.
- **`AdditiveScorer`** — the built-in three-term score as the default and the always-available
  safe fallback, with the five parameters as dataclass fields (completing the v0.5.0 P2 tidy).
- **Tier A: admin-configurable scoring parameters.** `GET /api/scorer` (viewer+),
  `POST /api/scorer` and `POST /api/scorer/rollback` (admin), backed by an **append-only,
  immutable** `scorer_config` table and a one-row active pointer — apply and rollback are the
  same operation, and history is never edited or deleted.
- **Read-only preview** (`POST /api/scorer/preview`, admin): re-partitions a bounded snapshot of
  recent alarms under the candidate parameters and returns the structural delta (what merges,
  what splits, links gained/lost). Deterministic, bounded by an alarm cap *and* a hard timeout,
  rate-limited by its own tight bucket, writes nothing, off the ingest path, and imports nothing
  from `eval/`. It says plainly that it is directional, not exhaustive.
- **Validation that rejects the degenerate, not merely the out-of-range** — a threshold at zero
  merges every alarm into one situation; a threshold at the weight sum links nothing, ever.
  Neither can be stored.
- **Decision provenance**: every situation records the `scorer_config_id` in effect when it was
  opened, so a historical grouping stays explainable months later.
- **Fail-safe execution**: any scorer exception, contract violation, or budget overrun degrades
  to the coded defaults, audits `scorer.fallback`, and raises an operator warning. The engine can
  never run scorer-less.
- **UI**: an admin-only **Scorer** panel (active parameters, preview with its caveat, immutable
  history with one-click rollback), pruned from a non-admin DOM entirely.
- New capabilities `scorer.read` (viewer+), `scorer.preview` / `scorer.write` (**admin only, no
  editor delegation**); new audit actions `scorer.config.update`, `scorer.preview`,
  `scorer.fallback`.
- **v0.7.0 and v0.8.0 specifications** (spec only, built later):
  `docs/architecture/GOVERNANCE-0.7-DRAFT.md` (admin RBAC + visibility scoping, stating
  explicitly that scoping is **not** tenant isolation) and
  `docs/architecture/SCORER-PLUGINS-0.8-DRAFT.md` (blessed ONNX adapter + Python entry-point
  escape hatch under this release's contract).

### Removed

- **The legacy `OPTICORR_*` environment aliases**, as promised in v0.4.0 and v0.5.0
  (DECISIONS #34, #39, #45). Setting any of them is now a **hard startup error** naming each
  variable and its `NETCORENOC_*` replacement — never a silent no-op, because an ignored
  `OPTICORR_ALLOWLIST` would mean every trap source is accepted. See `MIGRATION.md`.

### Changed

- `correlate.py` selects candidates and applies the verdict; it no longer inlines the arithmetic.
  `link` objects in `GET /api/situations/{sid}` gain an additive `terms` list alongside the
  unchanged `term_t`/`term_a`/`term_e`.
- The external-criterion API specified in `EXTENSIBILITY-0.6-DRAFT.md` was **rejected**, not
  deferred (DECISIONS #44): `score()` is typed pure and inference-only, so no outbound call can
  decide a link. That draft is superseded in place with a disposition table.

### Security

- **`docs/security/SECURITY-REVIEW-0.6.md`** — findings F20–F26 (parameter poisoning, privilege
  boundary, preview as a DoS/exfiltration surface, provenance integrity, hot-path surface,
  fail-safe execution, removed-knob misconfiguration), each with a control and a regression test,
  plus an honest critical analysis of residual risk. Threat model extended.

### Migration

- One forward-only migration, `0005_scorer_config.sql` (`user_version` 4 → 5), applying to a
  populated v0.5.0 database: additive tables, a nullable provenance column, a seed equal to the
  coded defaults, and a backfill. Grouping is unchanged, the audit chain still verifies.
  **One-time action required:** rename any `OPTICORR_*` environment variables. See `MIGRATION.md`.

## [0.5.0] - 2026-07-24 — "legible, installable, contributable"

An organization/structure release: it makes the project legible, installable, and contributable,
and prepares the ground for v0.6.0 — **without changing the running correlator at all.** No engine,
schema, API, or UI-behaviour change; the `make eval` metrics are byte-identical to v0.4.0. One
process, one SQLite file, one static UI, **zero new runtime dependencies**. 320 tests, 95 %
coverage.

### Changed

- **Repository adopts the PyPA `src/` layout** (`netcorenoc/` → `src/netcorenoc/`, history
  preserved). The import path stays `netcorenoc` — no public change. Tests now run against the
  installed package, the standing guard against the F12 class of bug. All packaging/tooling paths
  updated (`pyproject`, `Dockerfile`, `Makefile`).
- **Documentation reorganised into a navigable tree** with an index (`docs/README.md`):
  `architecture/`, `adr/`, `security/`, `scope/`, `releases/`, `gates/`, plus a newcomer
  `architecture/repo-map.md`. The decision log stays one append-only file under `adr/`.
- **`SECURITY.md` restructured** so a coordinated **vulnerability disclosure policy** is what a
  reporter finds first; the operator hardening guide moved to `docs/security/operations.md`.
- **Quickstart is now `docker compose up`.**
- The legacy `OPTICORR_*` environment-alias deprecation window was **extended one version to
  v0.6.0** (DECISIONS #39) — the only behaviour-adjacent change, and a non-removal.

### Added

- **Self-contained deployment**: a hardened `docker-compose.yml` (read-only rootfs, `cap_drop:
  [ALL]` + `CAP_NET_BIND_SERVICE`, `no-new-privileges`, `tmpfs /tmp`, named DB volume, `/healthz`
  healthcheck) with a committed `.env.example`; a hardened example `deploy/netcorenoc.service`
  systemd unit; `.dockerignore`/`MANIFEST.in`; `make dist`/`make release-check` and
  `tools/release_check.py`.
- **Open-source scaffolding**: `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md` (Contributor Covenant
  v2.1), GitHub issue/PR templates (security → private advisories), `NOTICE` and
  `ui/vendor/d3.LICENSE`, `.editorconfig`, README badges.
- **`/.well-known/security.txt`** (RFC 9116), shipped in the package and served by the app from
  the static allowlist under the existing CSP/security headers.
- **v0.6.0 specification** (spec only): `docs/architecture/EXTENSIBILITY-0.6-DRAFT.md` — admin
  RBAC, per-role/per-principal visibility scoping, and a configurable/pluggable match formula,
  each with its security framing; every element `v0.6.0: planned`, implemented none.
- **Dormant, opt-in CI**: a SHA-pinned least-privilege `release.yml` (built-in token only;
  publish/sign steps commented) and `dependabot.yml`; `ci.yml` actions SHA-pinned.
- **New guard tests**: structure + documentation link check (`test_structure.py`), GitHub-Actions
  SHA-pin lint (`test_workflows.py`), deployment-hardening assertions (`test_deploy.py`), and the
  RFC 9116 `security.txt` tests (`test_security_txt.py`).

### Security

- **`docs/security/SECURITY-REVIEW-0.5.md`** — findings F15–F19 (compose/systemd hardening,
  `security.txt`/disclosure policy, packaging integrity, SHA-pinned least-privilege workflows),
  each with an assertion test, plus an honest critical-analysis of residual risk. No exploitable
  runtime weakness was found; the runtime attack surface gains only the static `security.txt`
  path. Threat model extended with a v0.5.0 note.

### Migration

- No schema change; a live v0.4.0 database upgrades in place. The `netcorenoc` import path and all
  `NETCORENOC_*`/legacy `OPTICORR_*` env names are unchanged (the alias removal is now v0.6.0).
  See `MIGRATION.md`.

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
