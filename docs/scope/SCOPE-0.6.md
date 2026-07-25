# SCOPE — NetCoreNOC v0.6.0

**Theme: the scoring seam — a versioned, swappable, explainable `LinkScorer`, plus
admin-tunable scoring parameters with safe preview and one-click rollback.**

v0.6.0 answers the standing objection that the correlation formula is too rigid, and it answers
it *without* adding ML, external calls, plugin loading, or one byte of new work on the hot path.
The built-in three-term score

```
s = W_T·e^(−Δt/TAU_S) + W_A·A[class_i, class_j] + W_E·E[ne_i, ne_j]
link  ⟺  s > LINK_THRESHOLD      (defaults 0.30, 0.35, 0.35, 30 s, 0.50)
```

stops being a hard-coded expression and becomes **the default implementation of an interface**.
An admin — and only an admin — may retune its parameters, preview the effect on live data before
applying, and roll back instantly. **Cold-start grouping behaviour does not change**: at the
default parameters, v0.6.0 produces byte-identical output to v0.5.0 on every fixture and a
byte-identical `make eval` delta table.

The runtime identity is unchanged: one Python 3.12 asyncio process, one SQLite (WAL) file, one
static UI, environment variables only, no build step, no npm, **zero new runtime dependencies**.
All prior scope documents and their invariants still hold; `docs/security/threat-model.md` keeps
the authority it has held since v0.2.0. On a conflict, this document wins on *scope*, the build
prompt wins on *process and quality*, the threat model wins on *security posture*.

**Delivery model (unchanged).** The repository is read-only to automation: the maintainer takes
the resulting archive and pushes it by hand. No step depends on pushing, on CI running, or on any
external account, registration, or dashboard action. Every gate is local and reproducible on the
maintainer's machine (`make qa`, `make eval`, `docker compose config`, a locally built wheel).
Committed CI/release workflows are ready-to-use artifacts, never prerequisites.

## In scope — exactly five items, and nothing else

### 1. The `LinkScorer` seam (`src/netcorenoc/scoring.py`)

Extract the scoring computation from `correlate.py` behind a small, versioned interface, with the
current formula as the default, always-available implementation. **This is a refactor before it
is a feature, and its gate is exact parity.**

- `LinkScorer` — a `Protocol` with `scorer_id: str`, `contract_version: str`,
  `score(features: LinkFeatures) -> LinkScore` (pure, deterministic, side-effect-free,
  inference-only) and `params_fingerprint() -> str` (a stable hash of the active parameters, for
  provenance).
- `LinkFeatures` — an immutable dataclass the engine builds **once per candidate pair**: exactly
  what the current computation uses (`delta_t_s`, `class_i/j`, `class_affinity`, `ne_i/j`,
  `entity_affinity`) plus reserved optional slots (`severity_i/j`, `topo_distance`,
  `probable_cause_i/j`, `event_type_i/j`) that are `None` in 0.6 and ignored by the default
  scorer, so §3.5's X.733/3GPP features and §8's richer scorers are additive, non-breaking
  extensions.
- `LinkScore` — the required, explainable output: `linked`, `score`, `threshold`, and
  `terms: list[TermContribution]` where each is `{name, weight, value, contribution}` with
  `contribution = weight · value`. The default emits exactly the three terms (`temporal`,
  `class_affinity`, `entity_affinity`) with the same numbers surfaced today. **Emitting a
  per-term breakdown is contractual**, so "why did it decide that?" can never regress, for any
  future scorer.
- `AdditiveScorer` — the default: a dataclass with `w_t, w_a, w_e, tau_s, threshold` defaulting
  to the current constants, computing `s` exactly as today. `correlate.py` delegates every
  scoring call to `self.scorer.score(features)` and no longer inlines the arithmetic. The
  module-level constants become the dataclass defaults (the v0.5.0 P2 tidy, completed here).
- **Decision provenance.** Every situation records the `config_id` of the scorer configuration in
  effect when it was formed — by reference, never duplication — so any historical grouping is
  re-explainable months later. Written on the engine/store side, under the batch lock, never in
  `datagram_received`.
- **Interface versioning.** `contract_version` is persisted with the active configuration, and a
  configuration whose declared **major** version the running code does not support is refused.
  The schema grows by *adding* optional fields (minor bump), never by changing existing ones —
  which is what lets v0.8.0 plug ONNX/entry-point scorers into the same contract.
- **Plurality proof (test-only).** A second `LinkScorer` implementation lives under `tests/`
  (a trivial constant scorer and a deliberately-raising one) to prove the `Protocol` accepts a
  second implementation and that the fail-safe fallback engages. **No second scorer ships in
  `src/netcorenoc/`.**

`scoring.py` stays under ~300 lines. If it grows, the math gets simpler — a plugin framework is
v0.8.0, specified not built.

### 2. Tier A — admin-configurable scoring parameters

An admin may retune `w_t, w_a, w_e, tau_s, threshold`, see the effect on live data before
committing, apply with an audit trail, and roll back instantly — all off the hot path, all
fail-safe.

- **Persistence** — a forward-only migration `0005_scorer_config.sql` applying cleanly to a
  **populated** v0.5.0 database: an **append-only, immutable** `scorer_config` table
  (`id`, `scorer_id`, `contract_version`, the five parameters, `params_hash`, `created_by`,
  `created_at`, `note`) guarded by `BEFORE UPDATE`/`BEFORE DELETE` triggers exactly like
  `audit_log`; a one-row `scorer_active` pointer (rollback = point it at an earlier `id`); a
  nullable `situation.scorer_config_id` provenance FK backfilled to the seed; and a **seed row
  equal to the coded defaults, marked active**, which is what makes a migrated database
  byte-identical.
- **Read path** — the active parameters are loaded where `tau`/`threshold` are loaded today
  (engine configuration load), **not** per packet and **not** in `datagram_received`. A change
  takes effect at a documented reload point, never mid-batch. An unreachable store or an invalid
  row falls back to the coded defaults with an operator warning.
- **Validation and bounds (footgun guard)** — weights and `threshold` in `[0, 1]`, `tau_s` in a
  sane positive range, and rejection of degenerate combinations that would collapse or shatter
  all grouping. Bounds are named constants with a one-line rationale each; validation lives in
  one place and is unit-tested at its boundaries. An invalid set is a 4xx with a precise reason
  and **is never stored**.
- **Preview / what-if (read-only)** — before applying, an admin can run the candidate scorer over
  a **bounded, read-only snapshot of recent alarms already in the DB**, recompute links and
  connected components, and diff the resulting partition against the partition under the active
  parameters. It returns a structural delta (situations before/after, which merge, which split,
  links gained/lost) — enough to see "this widens grouping" or "this shatters incidents" before
  committing. It is deterministic (injected clock, fixed ordering), bounded (alarm cap + hard
  timeout), admin-only, rate-limited, **read-only** (it may not write a link, a situation, learned
  state, or a config row), off the ingest path, and it does **not import `eval/`** — the corpus
  harness stays the dev/CI gate.
- **Apply and rollback** — applying appends an immutable row and moves the active pointer
  (audited, before/after captured); rollback moves the pointer to a prior row. Both re-evaluate
  the engine configuration at the documented reload point.
- **RBAC** — three new capabilities in the single `rbac.py` map:

  | Capability | viewer | editor | admin |
  |---|:--:|:--:|:--:|
  | `scorer.read` — active scorer id, parameters, per-term contributions | ✔ | ✔ | ✔ |
  | `scorer.preview` — run a read-only what-if | — | — | ✔ |
  | `scorer.write` — append a config, move the active pointer, roll back | — | — | ✔ |

  Reading the active parameters is allowed for all roles: it *explains* grouping and is not a
  secret. Preview and write are **admin-only with no editor delegation** — deliberately breaking
  with the "optionally editor" phrasing of `EXTENSIBILITY-0.6-DRAFT.md`, because retuning the
  formula is a system-wide logic change, and security-relevant ambiguity resolves stricter.
- **Audit** — `scorer.config.update` (admin), `scorer.preview` (admin), and `scorer.fallback`
  (system actor, emitted when the active scorer errors and the engine falls back to the coded
  defaults), all covered by the catalog completeness test.

### 3. Mandated cleanup — remove the legacy `OPTICORR_*` environment aliases

Promised for v0.6.0 in v0.5.0 (DECISIONS #34, #39). The alias-acceptance path and its
once-per-variable deprecation warning are deleted; setting **any** `OPTICORR_*` variable is now a
**startup error** that names the `NETCORENOC_*` replacement and points to `MIGRATION.md` —
exactly as v0.3.0 removed `OPTICORR_API_TOKEN`. Fail loud on a removed knob; never silently
ignore it. This is the only behaviour-changing item in the release, and it is a removal planned
two versions ago.

### 4. Security review and critical analysis of (1)–(3)

`docs/security/SECURITY-REVIEW-0.6.md`, continuing the finding series from **F20**, each finding
with a severity, a precise location, a fix, a regression test `test_f<N>_*`, and a mapping row.
Assessed at minimum: parameter poisoning / footgun; the privilege boundary; preview as a DoS and
data-exfiltration surface; provenance integrity; reproducibility as a security property; the
`OPTICORR_*` removal; no new hot-path surface; migration integrity. Plus an honest
critical-analysis prose section on residual risk, and the matching `threat-model.md` entries.

### 5. Terrain-preparation for v0.7.0 and v0.8.0 — specification only

`EXTENSIBILITY-0.6-DRAFT.md` is annotated in place (never rewritten) to record that the scoring
seam is built here and the other two surfaces are resequenced, with pointers to:

- `docs/architecture/GOVERNANCE-0.7-DRAFT.md` — admin-configurable RBAC and per-role/per-principal
  visibility scoping, **stating explicitly that visibility scoping is a presentation control and
  NOT tenant isolation**.
- `docs/architecture/SCORER-PLUGINS-0.8-DRAFT.md` — customer-supplied models under the v0.6.0
  `LinkScorer` contract: a blessed ONNX adapter and a Python entry-point escape hatch.

**v0.6.0 implements none of it.**

## Explicitly out of scope (deferred, in this order)

No ML, no external calls, no plugin loading, no governance features ship here. Each is a
`docs/ROADMAP.md` line; the resequencing itself is recorded as DECISIONS #43.

1. **Admin-configurable RBAC and per-role/per-principal visibility scoping → v0.7.0.** Specified
   in `GOVERNANCE-0.7-DRAFT.md`, implemented then. Visibility scoping is a *presentation* control
   and is **not** tenant isolation: correlation still learns across all NEs, so a storm on one
   customer's NEs still shapes the learned matrices. Naming that limit is part of the 0.7 spec.
2. **Customer-supplied models → v0.8.0.** A blessed ONNX adapter (frozen weights, inference-only,
   FP32 / single-thread / pinned opset, hashed artifact) and a Python entry-point plugin escape
   hatch (`netcorenoc.link_scorers`, operator-trusted code, timeout + resource limits + mandatory
   fail-safe fallback). Specified in `SCORER-PLUGINS-0.8-DRAFT.md`. The v0.6.0 interface is
   designed so both drop in without a breaking contract change.
3. **External-API / sidecar scoring criterion — rejected on the correlation hot path.** If it ever
   exists it is advisory/offline only, never authoritative in `score()`. A ROADMAP line and a
   threat-model note, not a plan.
4. **Per-archetype weight profiles** (distinct parameter sets for PON/access vs. transport/DWDM
   vs. IP core), which depend on device-archetype clustering (already deferred, DECISIONS #36).
   The seam accommodates them; they are not built here.
5. **X.733 / 3GPP TS 32.111 scoring features** (`probableCause`, `eventType`,
   `perceivedSeverity`), which depend on MIB enrichment. `LinkFeatures` reserves optional room for
   them so adding them later is not a breaking change; populating them is out of scope now.
6. **SNMPv3, `/metrics`, pcap replay, outbound webhook / `Case` JSON emission** — still out.
7. **The `device_id` → `entity_id`/`ne_id` cutover** and removal of `learn.device_affinity`
   (DECISIONS #35) — still deferred; it would move numbers in the same release as a parity gate.

## Hard constraints (unchanged; violating any is a build failure)

1. **Zero new runtime dependencies.** No ML, no ONNX, no HTTP client, no plugin loader, no
   numpy/scipy/sklearn. Dev/CI tooling only, justified in `docs/adr/DECISIONS.md`.
2. **Default-parameter parity is inviolable.** The extracted `AdditiveScorer` at its defaults is
   byte-identical to v0.5.0; the parity gate may never be weakened. A metric change without a
   written `DECISIONS.md` justification is a build failure.
3. **Ingestion is sacred.** Nothing new in `datagram_received`; parameters and provenance are read
   and written where equivalent work already happens; preview is entirely off the ingest path.
4. **The default is always available and is the safe fallback.** Any error, exception, timeout, or
   invalid state falls back to the coded-default `AdditiveScorer`, audited `scorer.fallback`. The
   engine may never run without a valid scorer. Fail-safe, never fail-open, never fail-stop.
5. **Admin-only scoring configuration; no editor delegation; single RBAC source of truth.** No
   dynamic capability configuration in this release (that is v0.7.0).
6. **One process, one SQLite file, one static UI, env vars only, no build step, no npm.**
7. **`scoring.py` stays under ~300 lines**; no plugin framework, no dynamic loading, no external
   calls. The interface is a `Protocol` and one default implementation, nothing more.
8. Preserve git history on every move (`git mv`); never renumber existing ADR/finding entries.
9. **No feature outside the five items above.** Everything else is a `docs/ROADMAP.md` line;
   v0.7.0 and v0.8.0 are specified, never built.
