# Security Review — NetCoreNOC v0.4.0

An independent adversarial re-review of the whole surface (receiver, engine, store, API, auth,
sessions, audit, RBAC, UI, CLI, container, CI), plus a standards-compliance mapping. This is the
"adequação a regras internacionais" release artifact. It is kept **honest**: an unmet control is
listed unmet with a `docs/ROADMAP.md` line, never hand-waved.

Status legend: **met** (file + test prove it) · **planned** (in-flight this release) · **N/A**
(one-line reason) · **partial** (met with a documented gap → ROADMAP).

## 1. Standards anchor

- **Application**: OWASP ASVS 4.0.3 **Level 2**.
- **Authentication**: NIST SP 800-63B (already met in v0.2.0; re-verified).
- **Container / runtime**: CIS-benchmark-style hardening.
- **SNMP wire paths**: RFC 1157 (SNMPv1), 3416 (SNMPv2 PDUs), 3418 (SNMPv2 MIB / `authenticationFailure`),
  3584 (v1↔v2 coexistence / trap mapping).

## 2. Findings — F7…Fn (continuing the v0.1.0 F1–F6 series)

Authoritative list = this review (tools `bandit`, `pip-audit`, expanded `ruff`, and a `vulture`
dead-code scan are inputs, not the list). Each finding → fix commit → regression test.

| # | Sev | Location | Finding | Fix | Test | Status |
|---|-----|----------|---------|-----|------|--------|
| F7  | Medium | `api.py` read endpoints (`/api/graph`, `/api/situations/{sid}`, `/api/timeline`, `/api/entities`, `/api/entities/{ne_id}`, SSE) | Response over-disclosure: viewers received raw device/NE source IPs (and would receive `source_ip`/`community_tag` if present) | single role-keyed serializer `netcorenoc/shaping.py`: IPs coarsened to /24 (v4) / /48 (v6) below editor, `source_ip` dropped below admin, `community_tag` dropped below editor; applied on every IP-bearing read incl. the SSE live path | `tests/test_shaping.py` (unit + role×endpoint + SSE) | **met** (S2) |
| F8  | Medium | `rbac.py` `AUDITED_DENIED_PERMISSIONS` vs `api.py` `DENIED_ACTION` | Two tables for one fact; silent drift stops denied-read auditing | `rbac.AUDITED_DENIED_PERMISSIONS` is the single source; `api.security` derives the audit decision from it; `DENIED_ACTION` is presentation-only, guarded by an import-time assert | `test_f8_audited_denied_single_source` | **met** (`cb…`→S1) |
| F9  | Low | `rbac.py` `ROUTE_PERMISSIONS`, `GET /api/config` | Read authorised via a write capability (`config.write`) | added least-privilege `config.read` (admin); `GET /api/config` now requires it; still admin-only and audited-on-deny | `test_f9_config_read_is_its_own_least_privilege_capability` + matrix | **met** (S1) |
| F10 | High | `main.py` `asyncio.gather(*tasks)` | Unsupervised task death: a crashed engine/maintenance loop stops silently | `main.Supervisor` wraps the engine + maintenance tasks: a crash is logged (redacted), counted, restarted with capped exponential backoff, and surfaced via `operator_warnings()`; a cancel (shutdown) is never restarted | `tests/test_reliability.py::test_supervisor_restarts_crashed_task_and_warns` (+ `…cancellation_…`) | **met** (S3) |
| F11 | Medium | `store.py` open/commit; `main.Engine.run` | Unhandled `sqlite3` operational error (locked/busy/disk-full) crashes the process; a damaged DB is loaded silently | `Engine._commit_batch` catches `OperationalError`, rolls back (chain only advances on commit → never breaks), counts + warns; `Store._check_integrity` runs `integrity_check`/`foreign_key_check` at startup and warns, never crashes | `tests/test_reliability.py::test_engine_survives_store_operational_error_…`, `…integrity_check_flags_foreign_key_orphans` | **met** (S3) |
| — | — | `main.run` shutdown; new `/readyz` | Reliability hardening (not a discrete vuln): SIGTERM drains the queue within a bounded deadline leaving the chain consistent; `/readyz` reports orchestrator readiness (DB reachable + migrations applied + queue headroom) as ok/not-ok only, leaking no detail | graceful `Engine.drain`; unauthenticated `/readyz` (503 when not ready) | `test_graceful_drain_…chain_verifies`, `test_readyz_…leaks_no_detail`, `…not_ready_when_queue_saturated` | **met** (S3) |
| F12 | Medium | `pyproject.toml` `[tool.setuptools.package-data]` | A built wheel shipped only `index.html`; the container UI (`pip install .`) served a missing `app.js` / `style.css` / vendored `d3` — a broken, non-functional UI in the shipped artifact | package-data now globs the whole static UI (`ui/*.html`, `ui/*.js`, `ui/*.css`, `ui/vendor/*`); a test asserts every UI file is covered by a glob so it can't silently drop again | `tests/test_supply_chain.py::test_all_ui_assets_are_covered_by_package_data_globs` | **met** (S5) |
| F13 | Low | `netcorenoc/ui/vendor/d3.v7.min.js` | Vendored third-party asset had no integrity pin; a tampered swap would ship unnoticed | SHA-256 pinned in `ui/vendor/CHECKSUMS.txt`, asserted by a test and a CI job (`make checksums`) | `tests/test_supply_chain.py::test_vendored_assets_match_pinned_checksums` | **met** (S5) |
| F14 | Info | CSRF enforcement had **no regression test** (the rebrand changed the `X-NetCoreNOC-Client` header) | A rename could have silently broken CSRF with nothing to catch it — a coverage gap, not a runtime weakness (enforcement was and is correct) | added CSRF regression tests (missing/renamed header → 403, origin/host mismatch → 403, valid → 200, Bearer exempt) | `tests/test_abuse.py` CSRF cases | **met** (S7) |
| — | — | C.4 abuse suite (auth/authz/injection/CSRF/DoS/audit-tamper) | The suite drove the real HTTP/UDP paths against v0.4.0's surface; every asserted property (CSP + headers on new routes, shaped-viewer injection inert, entity-key-forgery bound, append-only audit) **held** — no new runtime weakness surfaced | consolidated + extended coverage | `test_abuse.py`, `test_reliability.py`, `test_shaping.py`, existing `test_rbac`/`test_security_ui`/`test_audit`/`test_promotion` | **confirmed** (S7) |

Dead code removed alongside F8 (not a finding, a surface reduction): `auth.ROLES`, `auth.now_s`.

## 3. OWASP ASVS 4.0.3 Level 2 — applicable controls

Only controls that apply to this app (no browser storage of credentials, no file uploads, no
payment, no SSO — those are N/A). Proof cites the enforcing file and the test.

| ASVS | Control (abridged) | Status | Proof |
|------|--------------------|--------|-------|
| V1.2.x | Authenticated, least-privilege components; single authorization source | met | `rbac.py` single map; `test_rbac.py` matrix + fail-closed |
| V1.4.x | Trusted enforcement point; deny-by-default access control | met | `api.security` dependency; unmapped route → 403 (`test_rbac` fail-closed) |
| V2.1.x | Password policy: length-based (≥12), no composition/expiry (SP 800-63B) | met | `auth.validate_password`; `test_auth.py` |
| V2.2.x | Anti-automation on auth (throttle/lockout) | met | `auth.LoginThrottle`; `test_auth` lockout |
| V2.4.x | scrypt with strong parameters; per-hash parameters recorded | met | `auth.hash_password` (`n=2**17`); `test_auth` policy test |
| V2.5.x | Credential recovery does not reveal existence; timing-equal failure | met | `dummy_verify`, identical failure message; `test_auth` no-enumeration |
| V3.2.x | Session tokens are high-entropy, server-side; new id per login (fixation) | met | `auth.open_session` (`token_urlsafe(32)`, hashed at rest); `test_auth` fixation |
| V3.3.x | Idle + absolute session timeout; logout invalidates | met | `IDLE_TIMEOUT_S`/`ABSOLUTE_TIMEOUT_S`; `resolve_session`; `test_auth` |
| V3.4.x | Cookie flags: `HttpOnly`, `SameSite=Strict`, `Secure` under TLS | met | `_cookie_kwargs`; `test_security_ui`/`test_api` |
| V3.5.x | Session revocation on password/role change | met | `revoke_user_sessions` on both; `test_auth`/`test_api` |
| V4.1.x | Deny-by-default; enforced server-side; RBAC | met | `rbac.role_allows`; `test_rbac` |
| V4.2.x | Field-level authorization / no over-disclosure to lower roles | planned | **F7** shaping serializer + `test_f7_*` |
| V4.3.x | Admin interfaces separated; no forced-browsing bypass | met (UI) / planned (surface) | role-gated UI; **F9** config.read |
| V5.1.x | Input validation (pydantic models, bounded fields) | met | `api.py` `*In` models; `test_api` |
| V5.3.x | Output encoding / injection defence (contextual, XSS) | met | UI F1 discipline (`textContent`/`esc`); JSON responses; `test_security_ui`, `test_findings` XSS |
| V5.5.x | Deserialization safety (no pickle/eval; JSON only) | met | `audit.canonical`/store use `json`; no `eval` (bandit) |
| V7.1.x | No secrets in logs; redaction | met | `logsetup.RedactionFilter`; `test_findings` secret-leak scan |
| V7.4.x | Errors do not leak sensitive detail | met | `HTTPException` details generic; readiness ok/not-ok (**readiness test**) |
| V8.2.x | Sensitive data minimised in responses / caching disabled | partial→met | `Cache-Control: no-store` on `/api`; **F7** completes minimisation |
| V8.3.x | Sensitive data (community string) never stored (F4) | met | `receiver.community_tag` (HMAC, plaintext dropped); `test_receiver` |
| V9.1.x | TLS available; secure cookie under TLS | met | `NETCORENOC_TLS_CERT/KEY`; operator-warning without TLS |
| V10.3.x | Supply-chain integrity of shipped third-party code | met | d3 SHA-256 pinned (`ui/vendor/CHECKSUMS.txt`) + `make checksums` CI job; whole UI shipped (F12) |
| V11.1.x | Business-logic limits / anti-automation (rate limit, bounds) | met | `RateLimiter`; `MAX_*` bounds; `test_perf`/abuse suite |
| V12.x | File upload / path traversal | N/A | no user file upload; static assets from a fixed allowlist (`STATIC_ASSETS`) |
| V13.1.x | API uses least-privilege, deny-by-default, security headers | met | `SECURITY_HEADERS` + CSP on every response; `test_security_ui` |
| V13.2.x | CSRF defence for cookie-auth mutations | met | origin/host + `X-NetCoreNOC-Client`; `SameSite=Strict`; `test_api` CSRF |
| V14.1.x | Build/deploy hardening; no debug endpoints (`docs_url=None`) | met | `create_app(docs_url=None, redoc_url=None)` |
| V14.2.x | Dependencies current; vulnerability scanning in CI | met | `pip-audit` in CI; `pyproject` pins |
| V14.4.x | Security headers (CSP, X-CTO, X-Frame, Referrer-Policy) | met | `SECURITY_HEADERS`; `test_security_ui` asserts all |

## 4. NIST SP 800-63B (authenticator assurance) — re-verified

| Requirement | Status | Proof |
|-------------|--------|-------|
| Memorised-secret min length ≥ 8 (we use ≥ 12) | met | `MIN_PASSWORD=12` |
| No composition rules, no periodic rotation | met | `validate_password` length-only |
| Rate-limit / throttle failed attempts | met | `LoginThrottle` exponential lockout |
| Store with an approved memory-hard function + salt | met | scrypt `n=2**17`, 16-byte salt, per-hash params |
| Compare in constant time | met | `hmac.compare_digest` |

## 5. RFC conformance (SNMP paths)

| RFC | Where | Status |
|-----|-------|--------|
| 1157 / 3416 | v2c trap decode (`receiver._parse_v2c`) | met — trap PDU only, varbinds pretty-printed verbatim |
| 3418 | `authenticationFailure` (`1.3.6.1.6.3.1.1.5.5`) treated as an ordinary alarm | corpus C.3 |
| 3584 §3.1 | v1→v2c trap mapping (`receiver._parse_v1`, generic→`snmpTraps.(n+1)`, enterpriseSpecific→`<ent>.0.<spec>`) | met; `test_receiver` v1 cases |

## 6. CIS-style container / runtime hardening

| Control | Status | Proof |
|---------|--------|-------|
| Run as non-root | met | `Dockerfile` `USER netcorenoc` (uid 10001) |
| Base image pinned (patch tag; digest documented) | partial | `Dockerfile` pins `python:3.12.8-slim` (not floating) with the exact digest-pin command documented inline; full digest pin is a deploy-time step (ROADMAP) |
| Read-only root filesystem where feasible | met (documented) | `Dockerfile` + SECURITY.md hardened run recipe (`--read-only`, DB on a writable volume) |
| Drop Linux capabilities / no-new-privileges | met (documented) | `Dockerfile` + SECURITY.md run recipe (`--cap-drop ALL --security-opt no-new-privileges`) |
| No build tools / package managers in final image | met | multi-stage build; final stage copies `/install` only |
| No secrets in image layers or logs | met | env-var config; redaction filter |

## 7. Tool baselines (inputs, re-run each gate)

`bandit -r netcorenoc tools` clean (B101 acknowledged); `pip-audit` clean; `ruff` (expanded rule
set) clean; `vulture` clean against a committed allowlist. Recorded per gate in
`docs/gates/v0.4-phase-*.md`.
