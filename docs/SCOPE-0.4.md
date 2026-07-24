# SCOPE — NetCoreNOC v0.4.0

**Theme: trustworthy by construction.** v0.4.0 is a security- and reliability-hardening release
under the new identity (§0 rebrand, DECISIONS #34). It ships **no new inference capability** —
everything here makes what already exists safer, clearer, and provably correct against realistic
traffic. The zero-config identity is unchanged: one Python 3.12 asyncio process, one SQLite (WAL)
file, environment variables only, one static UI, no build step, **zero new runtime dependencies**.

The three prior scope documents (`SCOPE.md`, `SCOPE-0.2.md`, `SCOPE-0.3.md`) and their invariants
still hold. This document only states what v0.4.0 adds or changes.

## In scope

### 0. Rebrand (done first, gated) — DECISIONS #34
Rename the project from *OptiCorr* / *NewProjectNetworj* to **NetCoreNOC**: import package,
metadata, CI, container, env prefix (`NETCORENOC_*`, legacy `OPTICORR_*` honoured one version with
a deprecation warning), session cookie, CSRF header, UI wordmark, logger name. The rename changes
names, never behaviour — proven by a byte-identical eval delta table.

### A. Security hardening and high reliability (P0 — the priority)
1. **Standards anchor** (`docs/SECURITY-REVIEW-0.4.md`): a compliance-mapping table targeting
   **OWASP ASVS 4.0.3 Level 2** for the app, continuing **NIST SP 800-63B** for auth, and
   **CIS-benchmark-style** hardening for the container/runtime. Every applicable ASVS L2 control is
   marked *met* (with the file + test that prove it) or *not applicable* (one-line reason). SNMP
   paths reference RFC 1157/3416/3418/3584. Honest: an unmet control is listed unmet with a
   ROADMAP line.
2. **Independent re-review → findings F7…Fn.** A fresh adversarial pass over the whole surface
   produces a numbered findings table continuing the v0.1.0 series. Each finding gets a severity,
   a precise location, a fix, a regression test named `test_f<N>_*`, and a review-table row mapping
   finding → fix → test. Confirmed seeds: the orphaned `AUDITED_DENIED_PERMISSIONS` vs
   `DENIED_ACTION` duplication (collapse to one table + divergence test); dead code
   (`auth.ROLES`, `auth.now_s`); `GET /api/config` gated by a write capability (introduce a
   least-privilege `config.read` or document the choice, and make the matrix test reflect it).
3. **Least-privilege response shaping**: a single role-keyed serializer redacts/coarsens
   over-disclosed fields (raw source IPs, `community_tag`, quarantine metadata surfaced via stats,
   session `source_ip`, internal ids) by role. Deny-by-default extends to fields, not only routes.
   Covered by a test parametrized over role × endpoint.
4. **UI affordances match access**: viewers see no mutating controls (hidden, not disabled);
   admin-only surfaces are absent from a non-admin DOM. Asserted by a UI test.
5. **Reliability & durability**: supervised background tasks (crash → logged, backed-off restart
   where safe, surfaced via `operator_warnings()`); store-integrity handling (WAL checkpoint
   cadence, graceful handling of `sqlite3` locked/busy/disk-full, a startup
   `PRAGMA integrity_check` / `foreign_key_check` that warns not crashes); a **readiness** signal
   (DB reachable, migrations applied, queue not saturated) that leaks no detail to the
   unauthenticated caller; graceful shutdown that drains the queue and flushes the profiler within
   a bounded deadline without breaking the audit chain; fault-injection tests (DB locked mid-write,
   queue saturation, malformed-packet flood, clock skew) that assert no crash, no loss beyond a
   recorded `ingest_gap`, chain still verifies.
6. **Supply chain & container**: pin and assert the vendored `d3.v7.min.js` SHA-256
   (`netcorenoc/ui/vendor/CHECKSUMS.txt` + test + CI job); Docker base image by digest; non-root,
   read-only root filesystem where feasible, dropped capabilities, no build tools in the final
   image; `bandit`/`pip-audit` stay green; the v0.2.0 redaction filter stays.

### B. Operator-grade, role-aware UI (P1)
A coherent design-token layer in `style.css` (colour/spacing/type/elevation/radii), a legible
type scale, an accessible dark palette (and a `prefers-color-scheme` light variant if cheap),
WCAG-AA contrast, visible focus, responsive to a narrow viewport — hand-written CSS only.
Role-tailored experiences (viewer read-only ops view; editor + feedback/rename/close; admin +
management surfaces visually separated). Real functionality: server-side filters/search where data
is large, the ingest-gap and operator-warning banners, a "why did it decide that?" drill-down
(R/X/D + evidence), an honest audit view with chain-verify status. SSE primary + polling fallback.
Accessibility and robustness (keyboard, ARIA, empty/error states, no console errors).
**The UI's security properties (strict CSP unchanged, no inline script/style, F1 escaping via
`textContent`/`createElement`/`esc()`, role-gated DOM) are P0 and may not be simplified.**

### C. Standards-based fault-and-abuse corpus (P0 for security scenarios, P1 for breadth)
- **C.1 Declarative, deterministic trap simulator** (test/tooling only, under `eval/`/`tools/`,
  never imported by `netcorenoc/`, no runtime dependency): a small scenario format describing
  devices, emitted trap classes (real-shaped OIDs), varbind templates (entity discriminator,
  severity, state, decoys), timing, and ground-truth labels. Determinism mandatory (fixed seeds,
  injected clock).
- **C.2 Realistic network-fault scenarios** (breadth, P1): access/PON, transport/DWDM & SAOS10,
  L2/L3 protocol faults, VPN/tunnel, aggregation/BNG/router/switch, camera/NVR — each with ground
  truth and, where relevant, expected containment depth / situation count / learned
  discriminator/severity/state. Coverage of each *phenomenon class* over sheer count.
- **C.3 Security-event traps flow through correlation** (P0): `authenticationFailure`, vendor
  login-failure/SSH-brute-force, config-change traps and correlated bursts — ordinary alarms that
  the engine groups; a coordinated-login burst becomes one situation. Correlation coverage, not an
  app-security test.
- **C.4 Abuse tests against NetCoreNOC's own attack surface** (P0), through the real HTTP/UDP
  paths: auth abuse (brute-force throttle, timing-equal responses, no enumeration, session
  fixation, expiry, revocation, cookie flags); authorization (route × role × method matrix,
  fail-closed on unmapped routes, 401/403/404 semantics, denied-sensitive-read auditing);
  injection/rendering (hostile payloads persisted through real ingest render inert; CSP + all
  security headers on every route class); CSRF (origin/host mismatch and missing header rejected,
  `SameSite=Strict`); resource abuse/DoS resilience (malformed-packet flood, oversized varbinds,
  unique-per-trap entity-key forgery, queue saturation → `ingest_gap`, rate limits) reusing the
  existing bounds; audit tamper-evidence (raw-SQLite edit → exact break, append-only triggers,
  secret-leak scan).

### `Case` draft
`docs/CASE-SCHEMA-DRAFT.md` stays a **draft**; it may be kept aligned to what v0.4.0 guarantees,
but nothing is implemented.

## Explicitly out of scope (deferred, in this order)

This release adds **no new inference capability**. Deferred (DECISIONS #35/#36; ROADMAP lines):
1. **Typed relations** — physical adjacency / containment / common-cause-of-site (v0.5.0).
2. **Device archetype clustering** by emitted-class vector (v0.5.0). *(1 and 2 were tentatively
   tagged v0.4.0 in SCOPE-0.3; re-deferred here so the hardening release stays focused.)*
3. Situation subsumption, impact scope, situation fingerprint / recurrence (v0.5.0).
4. **`Case` JSON contract implementation** stays a draft; downstream **automated per-equipment
   testing** (OTDR/SSH/Ansible-style checks, the operator's "phase 2") and **automatic NOC ticket
   JSON emission** (the operator's "phase 3") remain roadmap. No test-execution engine, no
   ticketing integration.
5. SNMPv3, automatic MIB enrichment, PostgreSQL/NATS, external IdP/SSO, MFA — still out.
6. **Remove the legacy `OPTICORR_*` env aliases** — v0.5.0 (accepted for one version here).
7. **Complete the `device_id` → `entity_id`/`ne_id` cutover** — v0.5.0 (re-deferred, DECISIONS #35).

## Hard constraints (unchanged; violating any is a build failure)
Zero new runtime dependencies. No vendor MIB semantics in the runtime (`known_oids.py` stays tiny
and public-standard-only; realistic vendor OIDs live only in the corpus/simulator). One process,
one SQLite file, env vars only, one static UI, no build step, no npm. Bounded memory everywhere
(every accumulator keeps its cap + eviction, tested). No feature outside this document. Modules
stay under ~300 lines; the UI stays four files; simulator/corpus tooling stays under `eval/`/`tools/`.
