# OptiCorr — v0.2.0 Scope

Authoritative product scope for the v0.2.0 release. Where this document and
`docs/threat-model.md` disagree with an implementation choice, they win; where scope and
the threat model disagree with each other, scope wins on *what* ships and the threat
model wins on *security posture*. v0.1.0 behaviour (`docs/SCOPE.md`) remains intact
except where a finding remediation or the authorization model deliberately changes it.

## Theme

**Identity, authorization, and tamper-evident audit.** v0.1.0 proved the correlator; it
had no notion of *who* was operating it and stored a network password in the clear.
v0.2.0 adds real accounts and roles, fixes six findings from the independent v0.1.0
security review, and evolves the UI — while keeping the zero-configuration spirit: the
only new mandatory interaction is the first admin login.

## What ships

### Accounts and roles

Three built-in roles with a single source of truth (`opticorr/rbac.py`):

- **viewer** — read everything (situations, graph, timeline, stats, classes) and receive
  the live event stream. No mutating controls at all.
- **editor** — everything a viewer can do, plus operational actions: confirm/split
  feedback, rename devices and classes, and manually close/acknowledge a situation.
- **admin** — everything an editor can do, plus administration: manage users, roles, and
  service tokens; change runtime config (allowlist, retention) from the UI; read the
  quarantine viewer (each read audited); and read, export, or prune the audit log.

HTTP contract: `401` when unauthenticated, `403` when authenticated but under-privileged,
`404` only after authorization has passed (no resource-existence oracle). A route with no
permission entry fails closed and fails CI.

### Authentication

- **Password login** with `hashlib.scrypt` (stdlib), NIST SP 800-63B policy (12–128
  characters, no composition rules, no forced expiry), constant-time verification.
- **Server-side sessions** in SQLite, cookie-based (`HttpOnly; SameSite=Strict`, `Secure`
  when TLS is on), 30-minute sliding idle timeout and 12-hour absolute cap; session ids
  stored only as SHA-256. Logout and any password/role change revoke sessions.
- **Login throttling** with per-username and per-IP exponential backoff, identical timing
  and message for unknown-user and wrong-password (no enumeration).
- **Bootstrap admin**: on an empty user table, one admin account is created with a random
  password printed once to the console. Until that password is changed, the app is locked
  to the login and password-change endpoints.
- **Service tokens** (bearer, admin-created, per-identity, per-role, individually
  revocable, shown once) replace the single shared token. The legacy
  `OPTICORR_API_TOKEN` still works as a deprecated admin identity until v0.3.0.
- **Optional built-in TLS** (`OPTICORR_TLS_CERT`/`OPTICORR_TLS_KEY`), with a documented
  reverse-proxy alternative.

### Tamper-evident audit

An append-only `audit_log` (enforced by SQLite triggers even against the application),
hash-chained (`entry_hash = sha256(prev_hash + canonical)`), covering authentication,
management actions, operator actions, and sensitive reads — including denied attempts.
`python -m opticorr audit verify` walks the chain and reports the first broken link;
`python -m opticorr audit export` emits NDJSON plus the final chain hash. Audit rows are
excluded from ordinary pruning and kept for a dedicated retention window (default 365
days), pruned only by an explicit, audited admin action.

### UI evolution

A login page; role-aware navigation (viewers never see mutating controls); a situation
**timeline** (alarms over time with raise/clear marks); root-cause confidence; filters
and search (device, class, status, time range); **Server-Sent Events** at `/api/events`
as the primary live-update path with automatic fallback to polling; and admin screens for
users, service tokens, config, the quarantine viewer, and the audit log. Still exactly
four static files (`index.html`, `app.js`, `style.css`, `vendor/d3.v7.min.js`), no build
step, no npm; d3 is vendored locally so the UI can run under a strict CSP.

### Security remediations (findings F1–F6)

| # | Finding | Remediation |
|---|---|---|
| F1 | Stored XSS via `innerHTML` | `esc()` + `textContent`/`createElement` everywhere; strict CSP; split JS/CSS; vendored d3; security headers |
| F2 | Shared static token, stored in `localStorage` | Sessions + per-identity service tokens; `localStorage` token input removed |
| F3 | Secret written to startup log | No secret in any log; bootstrap banner is the single once-only exception; root-logger redaction filter |
| F4 | Community string persisted in cleartext | Community never persisted; HMAC `community_tag` for grouping; quarantine blanks the community octets or stores metadata only |
| F5 | No TLS | Optional built-in TLS; auto-`Secure` cookie; reverse-proxy guidance |
| F6 | `0.0.0.0` bind with no warning | Persistent admin-visible banner when the allowlist is empty or the listener is non-TLS on a non-loopback bind |

## What does not change

The ingestion path (receiver → queue → engine → store), the learning and correlation
mathematics, the single-process / single-SQLite / no-build-step identity, and the
zero-config defaults for trap handling. Auth and audit are HTTP-side only and add no lock,
I/O, or latency to the trap path.

## Explicitly out of scope (deferred to ROADMAP / v0.3.0)

Removal of the legacy `OPTICORR_API_TOKEN` (v0.3.0); SNMPv3; external identity providers
(OIDC/LDAP/SAML); multi-tenant isolation; password reset by email; hardware-token / TOTP
MFA; per-object ACLs finer than the three roles; and a second read-only DB connection for
the API. These are named here so they are decisions, not omissions.
