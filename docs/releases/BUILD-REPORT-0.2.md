# OptiCorr v0.2.0 — Build Report

Built autonomously as a brownfield evolution of the tagged v0.1.0 release, following the
five-phase waterfall in the v0.2.0 build brief with `docs/SCOPE-0.2.md` and
`docs/threat-model.md` as the joint authorities. Date: 2026-07-20.

## Theme

Identity, authorization, and tamper-evident audit — role-based access control, remediation
of six v0.1.0 security-review findings, and a UI evolution — with the correlator's
zero-config spirit and lossless ingestion intact.

## What changed

- **Authentication** (`auth.py`) — `scrypt` password hashing (n=2¹⁷, parameters recorded
  per hash for upgrade), NIST SP 800-63B length policy, constant-time verify with a
  timing-equal dummy for absent users, per-username/per-IP exponential login lockout,
  server-side sessions (SHA-256-stored ids, 30 m sliding idle / 12 h absolute), bootstrap
  admin, forced password change, fixation defence.
- **Authorization** (`rbac.py`) — one permission map (`PERMISSIONS` + `ROUTE_PERMISSIONS`)
  consumed by both the FastAPI `security` dependency and the Phase-4 matrix test; deny by
  default; `401`/`403`/`404` with no existence oracle.
- **Audit** (`audit.py`, `__main__.py`) — append-only `audit_log` (SQLite triggers),
  SHA-256 hash chain, full event catalog instrumented at the call sites, redaction at the
  call site and the writer, `python -m opticorr audit verify|export`.
- **Service tokens & legacy path** — per-identity revocable bearer tokens (SHA-256);
  `OPTICORR_API_TOKEN` accepted as synthetic admin `legacy-token` with a deprecation
  warning and a one-time audit event (removal in v0.3.0).
- **Findings F1–F6** — see `docs/SECURITY-REVIEW-0.2.md` for the finding → fix → test map.
- **UI** (`ui/index.html`, `app.js`, `style.css`, `vendor/d3.v7.min.js`) — login,
  role-aware nav, timeline, root-cause confidence, filters, SSE with polling fallback,
  admin screens; strict CSP, no inline script/style, no build step, no CDN.
- **Config & operations** — env defaults overridden by admin-saved `meta` values;
  operator warning banners; optional TLS; JSON logging + root-logger redaction filter;
  `make migrate` / `make audit-verify`; CI secret-leak step.
- **Schema** — `0002_auth_audit.sql` (forward-only): `user`, `session`, `api_token`,
  `audit_log` (+ triggers), F4 quarantine columns, alarm `community_tag`.

## Quality results

| Check | Result |
|---|---|
| Tests | **171 passed** (107 v0.1.0 unmodified + 64 new: auth, RBAC matrix, audit, findings, migration, perf, CLI, UI-source) |
| Coverage on `opticorr/` | **94.63%** (gate ≥ 85%; ≥ v0.1.0 97.22% − 3 = 94.22%) |
| ruff check + format | clean |
| mypy --strict | clean (39 source files) |
| bandit / pip-audit | 0 issues / no known vulnerabilities |
| Live UI | headless Chromium: bootstrap login, role-aware tabs, F6 banner, F1 payload inert under CSP |

## Performance

- **Ingestion with auth + audit active** (in-process, 60,000 events, API hammered
  concurrently): **0 dropped**, engine drain ≈ 2,115 traps/s, API p50 447 ms / p95 514 ms
  / max 1.63 s during the storm, audit chain verified OK afterward. The trap path gained
  no lock or I/O; the one datagram-path addition is a single in-memory HMAC (community
  tag).
- The v0.1.0 UDP soak/load envelope (1000 traps/s × 60 s, `make loadtest`) is unchanged —
  auth and audit are HTTP-side only.

## Decisions (this version)

`docs/DECISIONS.md` entries 14–17: audit-retention prune drops/recreates the append-only
triggers in one locked, audited transaction (14); login throttle/lockout is in-memory,
single process (15); the F4 community tag is computed in the receiver from an in-memory
key so the plaintext never enters the queue (16); `create_app` keeps a `legacy_token`
parameter so the v0.1.0 API tests exercise the real legacy compatibility path (17).

## Gate evidence

`docs/gates/v0.2-phase-0.md` … `v0.2-phase-5.md`, with rendered UI screenshots
(`v0.2-phase-3-login.png`, `v0.2-phase-3-ui.png`). All five gates passed; no gate was
skipped.

## Deferred (ordered)

1. Remove `OPTICORR_API_TOKEN` (v0.3.0). 2. SNMPv3. 3. External identity providers
(OIDC/LDAP/SAML). 4. MFA/TOTP. 5. Multi-tenant isolation and durable (multi-node) login
throttling. 6. Second read-only SQLite connection for the API if UI latency under storms
matters. Plus the v0.1.0 roadmap items (`docs/ROADMAP.md`).

## Honest caveats

- The Docker image and flake.nix could not be built in the sandbox (registry / nix
  unavailable) — unchanged from v0.1.0; the install path was verified in a clean Python
  3.12 venv and the app was driven end-to-end through a real browser.
- The full 1000 traps/s × 60 s UDP load test (`make loadtest`) was represented by an
  in-process equivalent here; the numbers above are from that run. The store-lock
  contention makes API p95 rise during a saturating storm (consistent with the v0.1.0
  12–786 ms burst figures) and settles once the queue drains.
- The `must_change_password` 403 gate applies to a live session whose user is flagged; the
  bootstrap admin reaches it via the login → forced-change flow (no session is issued
  until the change), which is the intended path.
