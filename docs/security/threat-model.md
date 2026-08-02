# NetCoreNOC v0.2.0 — Threat Model


> **v0.7.2 (file references only).** The HTTP layer became the package `src/netcorenoc/api/`. No
> trust boundary, asset, adversary or control in this document changed — see
> `docs/security/SECURITY-REVIEW-0.7.2.md` §6. The file to read when auditing the HTTP boundary is
> now [`api/perimeter.py`](../architecture/MODULE-ARCHITECTURE.md); the four file references below
> were updated to point at it, and nothing else in this document was touched.

Lightweight STRIDE over the v0.2.0 attack surface. This document has the same authority
as `docs/SCOPE-0.2.md` once written: on any security-relevant ambiguity, the stricter
option wins (decision protocol §9). Every threat below names a **control** and the
**Phase-4 test** that proves the control holds. Findings F1–F6 from the v0.1.0 review are
folded in and each appears explicitly.

## Assets

1. **Operator browser session** — the console that can change learned state and config.
2. **Credentials** — user passwords, session cookies, service-token values.
3. **The SNMP community string** — functionally a password on the management LAN (F4).
4. **The audit log** — the record an incident review must be able to trust.
5. **Availability of ingestion** — the trap path must stay lossless (v0.1.0 invariant).
6. **Learned state** — matrices, topology, situations (integrity of the product itself).

## Trust boundaries

```
                        │ UDP 162 (unauthenticated by protocol)
   network-adjacent ────┼─▶ receiver ─▶ queue ─▶ engine ─▶ store
                        │
   browser / API client ┼─▶ TLS? ─▶ headers ─▶ origin/CSRF ─▶ session|token ─▶ RBAC ─▶ handler
                        │                                                          │
   holder of the DB file┼──────────────────────────────────────────────────────▶ store
```

The datagram side is unauthenticated by SNMPv2c design (zero-config trap intake). The
HTTP side is the security perimeter and is where all v0.2.0 controls live.

## Attacker profiles

- **A1 — network-adjacent, unauthenticated.** Can send UDP to 162 and reach the HTTP
  port. Cannot authenticate. Goal: inject content, read data, or deny service.
- **A2 — malicious viewer.** Holds valid viewer credentials. Goal: escalate to
  editor/admin actions.
- **A3 — malicious editor.** Holds valid editor credentials. Goal: reach admin-only
  management, config, quarantine, or audit.
- **A4 — stolen cookie.** Possesses a session cookie lifted from a browser or the wire.
  Goal: ride the session, cross-site, or outlive its window.
- **A5 — stolen DB file.** Has a copy of `netcorenoc.db`. Goal: recover live sessions,
  passwords, tokens, or the community string; or forge audit history.

## STRIDE by component

### Receiver (A1)

- **Tampering / DoS — malformed or flooding traps.** *Control (v0.1.0, retained):*
  defensive parse → quarantine; bounded queue counts overflow, never awaits.
  *Test:* `test_receiver.py` fuzz + `test_perf_*` zero-loss under load.
- **Information disclosure — community string persisted (F4).** *Control:* the community
  is never persisted or logged. A per-install 32-byte HMAC key in `meta` yields
  `community_tag = HMAC-SHA256(key, community)[:12 hex]` for grouping only; quarantine
  blanks the community octets in the raw packet, or stores metadata only
  (`sha256`, length, first 8 bytes) when it cannot locate them. *Test:*
  `test_f4_community_never_persisted`, `test_f4_quarantine_sanitized`.
- **Prime-directive constraint:** the tag is computed in the receiver from an in-memory
  key; no lock, no I/O, no DB call is added to the datagram path. *Test:*
  `test_perf_ingest_1000tps_with_auth_audit`.

### Engine / store (A1, A5)

- **Availability — audit/auth work must not stall ingestion.** *Control:* auth and audit
  are HTTP-side only; audit rows are written under the store lock the API handler already
  holds. *Test:* `test_concurrent_api_reads_during_ingest` (retained),
  `test_perf_ingest_1000tps_with_auth_audit`.
- **Information disclosure — stolen DB file (A5).** *Control:* passwords are scrypt
  hashes; session ids and token values are stored only as SHA-256; the community string
  is absent by construction. A DB thief gets no live session, no plaintext secret, no
  community. *Test:* `test_session_id_stored_hashed`, `test_token_stored_hashed`,
  `test_f4_community_never_persisted`.
- **Tampering — forged audit history (A5).** *Control:* `audit_log` is append-only via
  SQLite `BEFORE UPDATE`/`BEFORE DELETE` triggers (`RAISE(ABORT)`) and hash-chained;
  editing a row breaks the chain detectably. *Test:* `test_audit_append_only_triggers`,
  `test_audit_tamper_detected`.

### API surface (A1, A2, A3)

- **Spoofing — unauthenticated access (F2).** *Control:* every `/api` route requires a
  resolved identity (session cookie or bearer token); `401` otherwise. *Test:*
  `test_authorization_matrix` (anonymous row).
- **Elevation of privilege (A2, A3).** *Control:* `rbac.py` is the single permission map,
  consumed by both the FastAPI dependencies and the matrix test; deny-by-default; `403`
  for an authenticated-but-insufficient role; `404` only after authorization. *Test:*
  `test_authorization_matrix`, `test_every_api_route_has_permission` (fail-closed).
- **Information disclosure — existence oracle.** *Control:* authorization precedes
  resource lookup, so `403` never leaks whether an object exists. *Test:*
  `test_403_before_404_no_oracle`.
- **Repudiation.** *Control:* every mutating endpoint and every sensitive read writes an
  audit row (actor, role, source_ip, action, outcome), including denied attempts. *Test:*
  `test_audit_catalog_completeness`.

### Authentication / sessions (A1, A2, A4)

- **Spoofing — credential guessing / user enumeration.** *Control:* scrypt +
  `hmac.compare_digest`; a dummy hash is computed for unknown users so timing and the
  error message are identical; per-username and per-IP exponential lockout (1 s→…→15 min,
  reset on success); lockouts audited. *Test:* `test_login_throttle_progression`,
  `test_login_no_user_enumeration`.
- **Elevation — session fixation (A4).** *Control:* a fresh session id is issued at login;
  any pre-login id is discarded. *Test:* `test_session_fixation_rejected`.
- **Elevation — stale/riding session (A4).** *Control:* 30-min sliding idle + 12-h
  absolute expiry; logout deletes server-side; password/role change revokes all of a
  user's sessions; expired rows purged by the maintenance loop. *Test:*
  `test_session_idle_expiry`, `test_session_absolute_expiry`, `test_logout_revokes`,
  `test_password_change_revokes_sessions`, `test_role_change_revokes_sessions`.
- **Tampering — CSRF on cookie auth (A4).** *Control:* for cookie-authenticated mutating
  requests, `Origin`/`Referer` host must match `Host` and header `X-NetCoreNOC-Client: ui`
  must be present; `SameSite=Strict` is the third layer. Bearer-token requests are exempt.
  *Test:* `test_csrf_origin_mismatch_rejected`, `test_csrf_header_required`.
- **Information disclosure — credentials on the wire / in storage (F5, A4, A5).**
  *Control:* optional TLS with auto-`Secure` cookie; session ids and tokens stored hashed;
  reverse-proxy TLS documented. *Test:* `test_cookie_flags`, `test_secure_flag_with_tls`.

### Audit subsystem (A3, A5)

- **Tampering / repudiation.** *Control:* append-only triggers + hash chain + genesis
  `prev_hash` of 64 zeros; `audit verify` reports the first broken link. *Test:*
  `test_audit_chain_verify_ok`, `test_audit_tamper_detected`,
  `test_audit_append_only_triggers`.
- **Information disclosure — secrets leaking into audit `details`.** *Control:* `details`
  is redacted at the call site (never passwords, session ids, token values, community
  strings); a root-logger redaction filter and a CI secret-leak scan back it up. *Test:*
  `test_audit_details_redacted`, `test_f3_secret_leak_scan`.
- **Availability — audit growth / accidental prune.** *Control:* audit rows are excluded
  from the general prune; a dedicated `NETCORENOC_AUDIT_RETENTION_DAYS` (default 365)
  archive+prune is admin-only and itself audited. *Test:* `test_audit_excluded_from_prune`,
  `test_audit_manual_prune_audited`.

### UI (A1, A4)

- **Stored XSS (F1).** *Control:* `esc()` on every interpolation and
  `textContent`/`createElement` for all externally sourced strings (labels, `instance`,
  varbinds, vendor names); strict CSP `default-src 'none'; script-src 'self'; style-src
  'self'; img-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'`,
  which forces no-inline JS/CSS and locally vendored d3. *Test:* `test_f1_xss_*` (hostile
  payloads persisted through the real ingest path, responses JSON-clean),
  `test_security_headers_present`, `test_csp_header`.
- **Secret theft via XSS / cleartext storage (F2).** *Control:* no token in
  `localStorage`; the token input is removed; the session cookie is `HttpOnly` so script
  cannot read it even if F1 regressed. *Test:* `test_f2_no_localstorage_token` (UI source
  assertion), `test_cookie_flags`.
- **Clickjacking.** *Control:* `X-Frame-Options: DENY` + `frame-ancestors 'none'`.
  *Test:* `test_security_headers_present`.

## Operator-warning threats

- **F6 — insecure default exposure.** `0.0.0.0` bind with an empty allowlist or a
  non-TLS listener on a non-loopback address is a real risk the operator should see.
  *Control:* a persistent admin-visible banner surfaced via `/api/stats` (empty allowlist
  and/or non-TLS non-loopback bind). *Test:* `test_f6_warning_banner`.

## Residual risk (accepted, documented)

- SNMPv2c intake is unauthenticated by protocol; the allowlist is the only network
  control and defaults to allow-all for zero-config (Decision 4). Mitigated for the
  browser blast radius by F1/F4; TLS and allowlist are the operator's LAN controls.
- Login throttling and rate limiting are in-memory and single-process (matches the
  one-process identity; recorded as a v0.2 DECISIONS entry). A restart resets counters —
  acceptable for a single-node NOC tool.
- No MFA and no external IdP in v0.2.0 (ROADMAP). Password policy + throttling + audit is
  the v0.2.0 assurance level.

## Findings coverage check

F1 (UI/XSS), F2 (API/UI spoofing+secret), F3 (audit/logging disclosure), F4
(receiver/store disclosure), F5 (auth/sessions disclosure), F6 (operator-warning) each
appear above with a control and a named Phase-4 test. Gate 1 requires this table to be
complete.

---

# v0.3.0 extension — the learned entity model

v0.3.0 turns **attacker-controlled strings arriving over UDP 162** (entity keys, severity
values, state values) into labels rendered in the UI, keys in a growing table, and inputs to
a learning process. Under the allow-all default these strings are unauthenticated. This
section extends the model with the new threats (build document §6); each names a control and
the Phase-4 test that proves it. Every v0.2.0 threat above still holds — the trap path gains
no lock and no I/O (invariant 2), so the availability and disclosure properties are unchanged.

## New asset

7. **Entity attribution** — the mapping from a trap to the thing it is about. Its integrity
   is the product in this version; a wrong discriminator degrades correlation silently.

## STRIDE — new surface

### UI (A1) — entity-key stored XSS

A hostile varbind value becomes an entity label, severity token, or profiler row rendered in
the UI. *Control (F1 discipline, unchanged):* `textContent` / `createElement` only, never
`innerHTML`; CSP unchanged (`default-src 'none'; script-src 'self'; …`). Entity keys,
severity, state values, and `key_source` OIDs are external strings and are treated as such
everywhere they are rendered. *Test:* `test_entity_key_xss_*` — a parametrized regression
drives hostile payloads (`<script>`, `"><img onerror>`, event handlers, unicode) through the
real ingest path into entity keys, severity values, and the profiler view, asserting
JSON-clean responses and the security headers/CSP on every new route.

### Engine / store (A1) — entity-cardinality DoS

An attacker forges a varbind with a unique value per trap to explode the `entity` table.
*Control:* `MAX_ENTITIES_PER_NE` (10 000). On breach: stop creating entities for that NE,
keep attributing to the parent (never fail ingestion), raise a persistent operator warning
through the existing `operator_warnings()` mechanism, and audit the event (`entity.promote`
outcome/warning path). *Test:* `test_max_entities_per_ne_bites_and_warns` — the cap stops
entity creation, ingestion continues, the warning surfaces in `/api/stats`, and the audit
row is written.

### Engine (A1) — profiler memory DoS

An attacker forges many distinct varbind OIDs, or many distinct values, to grow the
in-memory profiler unboundedly. *Control:* `MAX_TRACKED_VARBINDS_PER_CLASS` (32) with LRU
eviction by observation count, `MAX_TRACKED_VALUES` (2048) per accumulator (stop adding keys
when full, keep incrementing existing ones), and pruning of stale profiles in
`maintenance()`. Value keys are stored as `sha256(value)[:8]`, keeping attacker-controlled
strings out of the hot dictionary. The severity test (S8) needs the *readable* values, so an
accumulator additionally keeps them — but only while its distinct count is small
(`MAX_DISPLAY_VALUES` = 16, each truncated to `MAX_DISPLAY_CHARS` = 32); the first value past
the cap trips `display_capped`, clears the map, and disqualifies the varbind from being a
severity field (a high-cardinality varbind is not a severity anyway). The retained strings are
thus bounded to 16×32 bytes per low-cardinality accumulator, and a hostile value flood never
grows them. The state-clear learner (S9) keeps its own bounded alternation trackers —
`MAX_STATE_SLOTS` (4096) per-`(device, instance, class, oid)` slots, each holding two
`STATE_MAX_VALUE_CHARS` (32)-truncated values and poisoned (freed for reuse of the two-value
test, never grown) the moment a third distinct value appears. *Test:* `test_profiler_bounds_*`,
`test_candidate_ignores_..._high_cardinality`, and `test_three_valued_varbind_is_never_a_state_field`
— each cap is shown to bite, to bound memory, and never to break ingestion.

### Learning (A1) — poisoning a discriminator

A hostile trap stream trains a wrong entity discriminator, severity, or state field.
*Control:* the promotion thresholds (score, evidence, cardinality) and the 1.25× margin over
the runner-up make a single burst insufficient; an admin-only, audited reset
(`entity.reset` / `profile.reset`) recovers. *Residual risk (accepted, documented):* NetCoreNOC
trusts its trap sources; the source allowlist is the network control. A source already
trusted to report can bias learning within the thresholds; the reset and the inspectable
`key_source`/`confidence` are the operator's recourse. *Test:*
`test_learning_reset_is_admin_only_and_audited`, `test_promotion_requires_margin`.

### Engine / API (A1) — silent misattribution

A wrong discriminator degrades correlation invisibly. *Control:* `entity.key_source` (the
chosen varbind OID) and `entity.confidence` (the promotion score) are surfaced in the UI and
API for every promoted entity; `entity_accuracy` is a frozen-baseline harness gate, so a
regression fails CI rather than passing unseen. *Test:* `test_entity_key_source_surfaced`,
plus the harness `entity_accuracy` non-regression in `tests/test_eval.py`.

## New RBAC capability (single map in `rbac.py`)

| Capability | viewer | editor | admin |
|---|:--:|:--:|:--:|
| Read entity tree and varbind profiles | ✔ | ✔ | ✔ |
| Reset an NE's learned entity key (audited) | — | — | ✔ |

*Test:* the new entries are covered by the existing `test_authorization_matrix` and the
fail-closed `test_every_api_route_has_permission`.

## New audit actions (frozen catalog + completeness test)

`entity.promote` (system actor), `entity.reset` (admin), `profile.reset` (admin),
`ingest.gap` (system actor) are added to `audit.ACTIONS` and covered by
`test_audit_catalog_completeness` exactly as the v0.2.0 actions are.

## v0.3.0 residual risk

- Entity attribution is only as trustworthy as the trap sources (see poisoning above); the
  allowlist is the control and the reset is the recourse.
- The RFC 3584 v1 agent-address may differ from the UDP source; the UDP source is used for
  `ne.ip` (not spoofable at the application layer) and the agent-address is exposed only as a
  varbind (DECISIONS v0.3).

## v0.3.0 coverage check

Entity-key XSS, entity-cardinality DoS, profiler memory DoS, learning poisoning, and silent
misattribution each appear above with a control and a named Phase-4 test. Gate 1 requires
this table to be complete.

---

# v0.4.0 extension — hardening under the NetCoreNOC identity

Same authority as the v0.2.0/v0.3.0 model. This release adds **no inference surface**; the new
and changed surfaces are the response-shaping serializer, the role-gated UI views, the task
supervision / readiness / shutdown machinery, and the container. STRIDE below; each threat maps to
a control and a named Phase-4 test. The rebrand itself (env aliases, cookie/CSRF-header rename) is
a naming change with one behavioural consequence — a forced re-login when the cookie name changes —
and no new trust boundary.

## Changed asset — response bodies as a disclosure channel

Read endpoints are viewer-readable, but a viewer must not receive operational secrets or sensitive
network detail merely because the *route* is viewer-readable. Over-disclosed fields (raw source
IPs, `community_tag`, quarantine metadata surfaced via stats, session `source_ip`, internal ids)
are a real **Information Disclosure** surface distinct from route authorization.

## STRIDE — new/changed surface

### Response shaping (A1, A2) — over-disclosure to lower roles — **F-review seed**
- **I**: a viewer reading a viewer-gated endpoint receives fields intended for editors/admins
  (source IPs, community-grouping tags, quarantine internals).
- **Control**: a single role-keyed serializer (`netcorenoc/shaping.py`) redacts/coarsens per role;
  deny-by-default extends to fields. *Test:* role × endpoint field-visibility (`test_shaping_*`,
  and abuse-suite over-disclosure assertions). **F7.**

### RBAC single source (A2) — a second, drifting authorization table — **F-review seed**
- **T/E**: `AUDITED_DENIED_PERMISSIONS` (rbac) and `DENIED_ACTION` (api) encode the same fact
  twice; a future edit to one silently diverges, so a denied sensitive read stops being audited.
- **Control**: derive the audited-denied set from the single `rbac.py` source; a divergence test
  fails CI if they ever disagree. **F8.** Related dead code (`auth.ROLES`, `auth.now_s`) removed to
  shrink the surface.

### Config read authorization (A2) — read behind a write capability — **F-review seed**
- **E**: `GET /api/config` requires `config.write` (admin) — a read authorised through a write
  capability, an odd least-privilege posture that also blocks a future read-only operator role.
- **Control**: decide deliberately — introduce a least-privilege `config.read` at the correct role
  or document the admin-only read in DECISIONS — and make the authorization-matrix test reflect the
  choice. **F9.**

### Background tasks (A5) — silent death of the engine/receiver/maintenance/SSE
- **D**: an unhandled exception in a supervised task kills it; ingestion or correlation silently
  stops with no operator signal.
- **Control**: a supervisor logs the (redacted) crash, restarts with backoff where safe, and
  surfaces it through `operator_warnings()`. *Test:* kill a task, assert recovery + warning
  (`test_supervision_*`). **F10.**

### Store under fault (A5) — DB locked/busy/disk-full or a damaged file
- **D/T**: a `sqlite3` operational error crashes the process mid-write, or a corrupted DB is
  loaded silently, risking a broken audit chain.
- **Control**: operational errors are caught and surfaced without crashing or corrupting the chain;
  a startup `PRAGMA integrity_check` / `foreign_key_check` warns (never crashes) on a damaged DB.
  *Test:* DB-locked mid-write and integrity-warn (`test_store_fault_*`). **F11.**

### Readiness endpoint (A2, A6) — detail leak to the unauthenticated caller
- **I**: a readiness probe that returns rich internal state (queue depth, migration versions, DB
  paths) to an unauthenticated caller is a recon surface.
- **Control**: `/readyz` returns only ok/not-ok (503 when not ready) to the unauthenticated caller;
  detail stays behind authenticated `/api/stats`. *Test:* `test_readiness_*` asserts no detail leak.

### Graceful shutdown (A5) — chain break or data loss on SIGTERM
- **D/T**: an abrupt shutdown loses queued traps or writes a half-batch, breaking the audit chain.
- **Control**: drain the queue and flush the profiler within a bounded deadline; the final
  maintenance commit is atomic. *Test:* shutdown mid-stream, assert chain still verifies
  (`test_graceful_shutdown_*`).

### Container / supply chain (A6) — tampered asset or over-privileged runtime
- **T**: the vendored `d3.v7.min.js` is swapped for a hostile build; the container runs as root
  with a writable root filesystem and build tools present.
- **Control**: `d3` SHA-256 pinned in `vendor/CHECKSUMS.txt`, asserted by a test and a CI job; base
  image pinned by digest; non-root, read-only rootfs where feasible, dropped capabilities, no build
  tools in the final image. *Test:* `test_vendor_checksum_*` + CI checksum job.

### UI affordances (A2, A4) — offering actions the caller cannot perform
- **E**: a viewer's DOM contains mutating controls or admin surfaces, implying access the server
  will deny (confusing at best; a probing aid at worst).
- **Control**: role-gated rendering hides (not disables) every control the role lacks; the server
  still enforces. *Test:* viewer-session DOM contains none of the mutating control ids
  (`test_ui_role_gated_*`), and the F1 XSS discipline is re-asserted on every new view.

## New findings placeholders (F7…)

| # | Severity | Area | Status |
|---|----------|------|--------|
| F7  | (tbd) | Response over-disclosure to lower roles | planned |
| F8  | (tbd) | RBAC audited-denied duplication / drift | planned |
| F9  | (tbd) | `GET /api/config` behind a write capability | planned |
| F10 | (tbd) | Unsupervised background task death | planned |
| F11 | (tbd) | Unhandled sqlite operational error / damaged DB | planned |
| F12+ | (tbd) | Reserved for findings surfaced by the C.4 abuse suite | planned |

## v0.4.0 coverage check

Every threat above names a control and a Phase-4 test; every F7… row is tracked in
`SECURITY-REVIEW-0.4.md` (finding → fix → test). Gate 1 requires this table complete;
Gate 4 requires every planned test green and every finding closed or explicitly deferred with a
ROADMAP line.

---

# v0.5.0 extension — organization/structure release (no engine change)

Same authority as the prior model. v0.5.0 is packaging, docs, process, and a specification only:
it adds **no inference surface** and **no dynamic HTTP surface**. Every v0.2.0–v0.4.0 threat and
control above holds byte-for-byte (the receiver, engine, store, API, auth, sessions, audit, RBAC,
shaping, and UI code are unchanged). The review of the new *organization* artifacts is
`SECURITY-REVIEW-0.5.md` (findings F15–F19).

## The one new served path

### API surface (A1) — the static `.well-known/security.txt`

The RFC 9116 contact file is committed in the package and served at `/.well-known/security.txt`.
*Threat:* a new served route could be a dynamic surface (logic, DB, injection) or escape the
security-header posture. *Control:* it is **static and public** — a fixed entry in the
`STATIC_ASSETS` allowlist (`api/routes_static.py`), served by the same `FileResponse` path as `app.js`/
`style.css`, under the **same CSP and `SECURITY_HEADERS` middleware** as every route; it reads no
input, touches no DB, and runs no dynamic code, so it adds no injection, SSRF, or auth surface.
Being unauthenticated is by design (a contact file), consistent with `/`, `/healthz`, `/readyz`,
and the other static assets. *Test:* `tests/test_security_txt.py` — the served route returns 200
**unauthenticated** with `Content-Type: text/plain; charset=utf-8` and the full security-header
set, the served bytes equal the shipped file, and an `/api` route with no credential is still 401.

## v0.5.0 residual risk

- The disclosure policy (`SECURITY.md`) and `security.txt` publish contact URLs and a future
  `Expires`; the maintainer must refresh `Expires` before it lapses
  (`test_security_txt.py::test_security_txt_expires_is_in_the_future` fails once it does).
- Deployment hardening (compose/systemd) is expressed correctly but is only *enforced* on the
  operator's host; the review asserts the artifacts, not a running kernel. The container's
  read-only-rootfs + dropped-caps posture and the DB-volume ownership are confirmed by the
  operator's `docker compose up` (SECURITY-REVIEW-0.5 F15).

## v0.5.0 coverage check

The one new served path names a control and a test above; findings F15–F19 are tracked in
`SECURITY-REVIEW-0.5.md` (property → fix → test). Gate 5 requires every F15+ test green.

---

# v0.6.0 extension — the scoring seam (engine surface; HTTP perimeter unchanged in shape)

Same authority as the prior model. v0.6.0 makes the correlation formula the default
implementation of a versioned `LinkScorer` interface and lets an **admin only** retune its five
parameters, preview the effect read-only, apply with an audit trail, and roll back instantly.
Every v0.2.0–v0.5.0 threat and control above holds unchanged: the receiver, auth, sessions, the
audit chain, `shaping.py`'s field rules, and the 401/403/404 semantics are untouched. The review
of the new surfaces is `SECURITY-REVIEW-0.6.md` (findings F20–F26).

**What is genuinely new**: a stored input that changes *grouping logic* (`scorer_config`), a
compute-bearing admin endpoint (`POST /api/scorer/preview`), and one new provenance column
(`situation.scorer_config_id`). **What is deliberately absent**: any outbound call, any dynamic
code loading, any new runtime dependency, and any change to `receiver.datagram_received`.

## New asset

7. **The scoring configuration** — the five parameters that decide which alarms group. It is not
   a secret (it *explains* grouping, and `scorer.read` is viewer+), but its **integrity** is an
   asset: a bad value silently degrades the product itself.

## STRIDE — new surface

### Scoring configuration (A3 malicious editor, A2 malicious viewer, compromised admin)

- **Tampering — parameter poisoning / the footgun.** *Threat:* a parameter set that collapses
  every alarm into one situation (`threshold → 0`) or shatters every incident
  (`threshold ≥ w_t+w_a+w_e`) destroys correlation without touching a single alarm row.
  *Control (defence in depth, five layers):* (1) **bounds and degeneracy validation** in one
  place — weights and threshold in `[0,1]`, `1.0 ≤ τ ≤ 3600 s`, a minimum weight sum, and a
  reachability/headroom rule that keeps the threshold strictly inside the achievable score range
  (DECISIONS #46) — an invalid set is a 4xx and is **never stored**; (2) **preview before apply**,
  showing the structural partition delta on the operator's own recent data; (3) **audit** of every
  change with before/after; (4) **immutable, append-only history** plus a one-row active pointer,
  so **rollback is one action**; (5) the coded defaults remain the **fail-safe fallback**.
  *Tests:* `test_f20_*` (degenerate sets rejected at every boundary and unstorable),
  `test_scorer_bounds_*`, `test_scorer_rollback_restores_previous_partition`.
- **Elevation of privilege — an editor retunes the formula.** *Threat:* the loose "optionally
  editor, if the admin delegates" phrasing of `EXTENSIBILITY-0.6-DRAFT.md` would make a
  system-wide logic change reachable from an editor session. *Control:* deliberately **not**
  implemented. `scorer.preview` and `scorer.write` are **admin-only with no delegation** in the
  single `rbac.py` map; `scorer.read` is viewer+ because the parameters are an explanation, not a
  secret. Deny-by-default and the generated authorization matrix cover every new route × role ×
  method; 401/403/404 semantics preserved. *Tests:* `test_f21_*`, the extended
  `test_authorization_matrix`, `test_every_api_route_is_in_the_permission_map`.
- **Elevation of privilege — a config change escalating a live session.** *Threat:* a stored
  change taking effect mid-request or mid-batch. *Control:* parameters are read at the documented
  **engine configuration reload point**, never per packet and never mid-batch; a scoring-config
  change grants no capability to anyone (it is not an authorization input at all). *Test:*
  `test_f21_config_change_grants_no_capability`.

### Preview endpoint (A1 resource exhaustion, A2/A3 disclosure)

- **Denial of service — unbounded compute on demand.** *Threat:* an admin (or an attacker holding
  an admin credential) hammering a what-if that re-partitions the whole alarm history.
  *Control:* bounded by `MAX_PREVIEW_ALARMS = 5000` **and** a hard `PREVIEW_TIMEOUT_S` wall-clock
  budget **and** the existing per-client token-bucket rate limiter **and** admin-only
  authorization; the work is O(alarms × candidates) with both factors capped, runs on the HTTP
  side, and **never touches the ingest path**. *Tests:* `test_f22_preview_is_bounded_*`,
  `test_f24_ingest_unaffected_by_preview`.
- **Information disclosure — preview as an exfiltration channel.** *Threat:* a compute endpoint
  returning richer data than the caller could otherwise read. *Control:* preview returns only
  **aggregate structural deltas** (situation counts, merge/split groups by situation id, link
  counts gained/lost) — no alarm payloads, no varbinds, no device IPs, no field beyond what the
  caller could already read under `shaping.py`; and it is admin-only, the role that could read all
  of it anyway. *Test:* `test_f22_preview_response_discloses_no_new_fields`.
- **Tampering — preview mutating state.** *Threat:* a "read-only" analysis that writes a link, a
  situation, learned state, or a config row. *Control:* preview reads alarms and re-partitions
  **in memory only**; it constructs a throwaway `Correlator`, holds the learned matrices fixed as
  an input, and issues no write. *Test:* `test_f22_preview_mutates_nothing` (full-database
  snapshot compared before/after, including `sqlite_sequence`), plus
  `test_preview_is_deterministic_across_two_runs`.

### Provenance and reproducibility (A5 stolen/altered DB, post-incident review)

- **Tampering — rewriting the parameter history behind a past grouping.** *Threat:* editing or
  deleting a `scorer_config` row to make a historical situation look like it was scored
  differently. *Control:* `scorer_config` is **append-only at the storage layer**
  (`BEFORE UPDATE` / `BEFORE DELETE` triggers that `RAISE(ABORT)`), exactly like `audit_log`;
  rollback moves a pointer and never mutates a row; every change is also in the hash-chained audit
  log, so tampering must defeat both. Unlike `audit_log` there is **no sanctioned deleter** — the
  retention prune does not touch `scorer_config`. *Tests:* `test_f23_scorer_config_is_append_only`,
  `test_f23_prune_does_not_touch_scorer_config`.
- **Repudiation — "we cannot say how this situation was scored".** *Threat:* a regulated NOC
  post-incident review that cannot recover the decision rule. *Control:* every situation carries
  `scorer_config_id`; the referenced row is immutable and carries the five parameters, the
  `scorer_id`, the `contract_version`, and a `params_hash`. Existing situations are backfilled to
  the seed row, which *is* the coded defaults that in fact formed them. *Test:*
  `test_f23_situation_provenance_recovers_exact_parameters`.

### Engine (A5 availability) — the scorer itself as a failure mode

- **Denial of service — a scorer that raises, hangs, or returns nonsense.** *Threat:* the engine
  stalls or crashes because scoring failed. *Control:* every scoring call goes through a fail-safe
  wrapper: on any exception, timeout, or malformed `LinkScore` the engine **falls back to the
  coded-default `AdditiveScorer`** for the remainder of the process, records
  `scorer.fallback` (system actor) once, and surfaces a persistent operator warning. The engine
  may never run without a valid scorer. Fail-safe, never fail-open, never fail-stop. *Tests:*
  `test_f25_raising_scorer_falls_back_and_audits`, `test_f25_engine_never_runs_scorerless`.

### Startup configuration (A6 operator error) — the `OPTICORR_*` removal

- **Security misconfiguration — a removed knob silently ignored.** *Threat:* an operator upgrading
  with `OPTICORR_ALLOWLIST` still set would believe trap sources are filtered while **every source
  is accepted** — a security regression dressed as a compatibility one. *Control:* setting any
  `OPTICORR_*` variable is a **hard startup error** naming each variable, its `NETCORENOC_*`
  replacement, and `MIGRATION.md`; the error text names variables, never values, and prints no
  secret. *Tests:* `test_f26_legacy_env_prefix_is_a_startup_error`,
  `test_f26_legacy_env_error_names_no_value`.

## Rejected by design (stronger than a control)

`EXTENSIBILITY-0.6-DRAFT.md` §3 Tier B specified an admin-enabled **external API supplying the
linking criterion**, with SSRF, DoS-via-stall, and untrusted-response-injection threats each
carrying a control. v0.6.0 **rejects the design** (DECISIONS #44) rather than mitigating it:

- **SSRF via the criterion API** — *disposition:* no socket exists. `LinkScorer.score` is
  specified pure, deterministic, side-effect-free and inference-only; no outbound call decides a
  link, now or later. Any future external signal is advisory/offline only.
- **DoS / stall via the criterion API** — *disposition:* unreachable for the same reason. The
  scorer runs in-process, engine-side, under the existing batch lock.
- **Untrusted response injection** — *disposition:* unreachable; there is no response to parse.

Removing the hazard is not the same as controlling it, and this model records which one happened.

## v0.6.0 residual risk (accepted, documented)

- **A determined admin can still detune correlation.** The control is preview + audit + immutable
  history + one-action rollback + validated bounds — *not* prevention. An admin is trusted to
  change system logic by definition; the design makes the change visible, reversible, and
  attributable, and refuses only the shapes that cannot be correct.
- **Preview is directional, not exhaustive.** It reflects a bounded *recent* window (≤ 5000
  alarms), re-partitions with the learned matrices held fixed, and therefore predicts the
  immediate effect, not the long-run effect after the matrices adapt. The UI says so in the panel
  copy; a preview that looked authoritative would be worse than none.
- **Provenance grows one row per parameter change.** Bounded (a handful of rows per year),
  immutable, and never pruned — a deliberate trade against reproducibility being lost to
  retention.
- **`scorer_config` has no sanctioned deleter.** That is the point, and it means an operator
  cannot excise a parameter change from history; they can only append and roll back.

## v0.6.0 coverage check

Every threat above names a control and a test. Findings F20–F26 are tracked in
`SECURITY-REVIEW-0.6.md` (property → fix → test); Gate 5 requires every F-numbered test green,
`make eval` byte-identical at default parameters, and the authorization matrix clean over the
three new capabilities.

---

# v0.7.0 — governance: stored capability policy and visibility scoping

v0.7.0 makes two perimeter decisions **data-driven**: which capabilities a role or principal holds,
and which network elements a viewer or editor is shown. Every v0.2.0–v0.6.0 threat and control
above holds unchanged: the receiver, auth, sessions, the audit chain, `shaping.py`'s field rules,
and the 401/403/404 semantics are untouched. The review of the new surfaces is
`SECURITY-REVIEW-0.7.md` (findings **F27–F33**).

**What is genuinely new**: two stored policies that are read *on the authorization path*
(`rbac_policy`, `scope_policy`), and a resource-level projection applied to every NE-bearing read.
**What is deliberately absent**: any new runtime dependency, any change to `datagram_received`, the
engine, the learning, or the scoring seam, and any per-tenant partition of learned state.

The failure mode of this release is different from v0.6.0's and the model must say so plainly.
v0.6.0's worst case was *degraded grouping* — visible, previewable, reversible. v0.7.0's worst case
is **silent privilege escalation** or an **existence oracle**: an attacker gains a capability the
code reserves, or infers which network elements exist. Both are silent by nature, which is why the
controls below are structural (an intersection, a filtered query) rather than procedural (a
validation check, a guard clause).

## New assets

8. **The capability policy** — the stored subset of each role's/principal's compiled ceiling. Its
   **integrity** is the asset: a policy that could widen would be an escalation primitive.
9. **The scope policy** — which NEs a viewer/editor is shown. Its integrity protects *disclosure*;
   its availability protects the operator's ability to do their job.
10. **The existence of a network element** — v0.7.0 is the first release in which "does NE X exist?"
    is a question some authenticated principals must not be able to answer.

## STRIDE — new surface

### Capability policy (A2 malicious viewer, A3 malicious editor, A5 stolen DB, compromised admin)

- **Elevation of privilege — a stored policy grants above the ceiling.** *Threat:* a policy row,
  written through the API, through a future second write path, through a bad migration, or by
  direct `sqlite3` access to a stolen/restored DB file, naming a capability the role's compiled
  ceiling does not contain. *Control:* the resolved set is
  `ceiling(role) ∩ granted(role) ∩ granted(principal)` and the compiled `PERMISSIONS` map is the
  **first operand** (DECISIONS #53). An intersection cannot exceed its first operand, so an
  above-ceiling row is **inert**, not merely rejected — the guarantee does not depend on the write
  path having been reached. A write-time 400 exists only so an admin learns immediately; it is not
  the security control. *Tests:* `test_f27_*` — property-based over generated policies (including
  rows inserted directly into the table, bypassing the API) asserting `resolved ⊆ ceiling` for
  every role; `test_f27_policy_written_directly_to_the_db_cannot_escalate`.
- **Elevation of privilege — a second decision site.** *Threat:* a handler, the UI-affordance
  endpoint, or a test computing capabilities its own way and drifting from enforcement.
  *Control:* one resolver, `rbac.resolve_capabilities()`, called from the `api/perimeter.py` security
  dependency, from `/api/me`, and from the generated authorization matrix. A static assertion over
  the `api/` package's source forbids a role comparison outside the resolver. *Tests:*
  `test_f28_single_decision_site_no_role_comparison_outside_rbac`,
  `test_authorization_matrix` (regenerated with policies active).
- **Elevation of privilege — writing the policy is itself the escalation.** *Threat:* a
  non-admin reaching `rbac.write` and granting themselves capabilities. *Control:* `rbac.read` and
  `rbac.write` are **admin-only, `config`-class, no delegation**, in the single map and in
  `AUDITED_DENIED_PERMISSIONS`; and even a successful write cannot exceed the ceiling. *Tests:*
  `test_f27_rbac_write_is_admin_only`, `…_denied_attempts_are_audited`.
- **Denial of service — a malformed policy locks the admin out.** *Threat:* a corrupt or
  unparseable capability policy denying everything, including the `rbac.write` needed to repair it.
  *Control:* a malformed capability policy **falls back to the compiled ceiling** — the shipped
  v0.6.0 behaviour — with an `operator_warnings()` entry and an audit row (DECISIONS #55). Never a
  fallback that grants above ceiling; never a hard lock. *Tests:*
  `test_f29_malformed_capability_policy_falls_back_to_the_ceiling`,
  `…_warns_and_audits`, `…_admin_can_always_reach_rbac_write`.
- **Repudiation — an untraceable perimeter change.** *Control:* `rbac.policy.update` (admin actor,
  before/after in `details`) in the frozen catalog and the completeness test; the policy table is
  append-only at the storage layer with a one-row active pointer, so history is tamper-evident
  alongside the hash chain — tampering must defeat both. *Tests:*
  `test_f31_policy_history_is_append_only`, `test_audit_catalog_completeness`.
- **Elevation — a policy change riding an open session.** *Threat:* a principal whose capability
  was revoked continuing to use it on an already-authenticated session. *Control:* the resolved set
  is computed **per request** from live policy; nothing is cached on the session. *Tests:*
  `test_f30_revoked_capability_does_not_survive_an_open_session`.

### Scope policy and the read paths (A2, A3, A5)

- **Information disclosure — existence oracle via a scoped resource.** *Threat:* `GET
  /api/situations/{sid}` or `/api/entities/{ne_id}` distinguishing "out of your scope" from "does
  not exist", giving an authenticated viewer an enumeration primitive over the network inventory.
  *Control:* **404, not 403**, past authorization — and produced by *filtering the lookup itself*,
  so the handler's existing not-found branch fires unchanged and the two cases are
  indistinguishable by construction rather than by a matching pair of code paths (DECISIONS #60).
  *Tests:* `test_f32_out_of_scope_detail_is_indistinguishable_from_nonexistent` (status, body, and
  headers compared), across detail, graph, timeline, and SSE.
- **Information disclosure — leakage through aggregates.** *Threat:* `/api/stats` counters
  (`devices`, `active_alarms`, `open_situations`) letting a scoped viewer infer out-of-scope volume
  or the arrival of a new NE. *Control:* every enumerating counter is computed **over the in-scope
  set only**; a scoped principal's `devices` count is the size of their own scope. *Tests:*
  `test_f32_aggregates_are_computed_over_the_in_scope_set`,
  `…_out_of_scope_activity_does_not_move_a_scoped_viewers_counters`.
- **Information disclosure — leakage through a mixed-membership situation.** *Threat:* a situation
  spanning the boundary disclosing out-of-scope NE ids, IPs, entity keys, or varbinds through its
  member list, its links, or its root-cause hint. *Control:* out-of-scope members are **redacted to
  a coarse count and type** — no identifier of any kind — and links referencing a redacted member
  are withheld; the root hint is suppressed when the root is out of scope (DECISIONS #59). *Tests:*
  `test_f32_redacted_members_disclose_no_identifier` (asserts the response body contains no
  out-of-scope IP, NE id, or entity key anywhere at any depth).
- **Information disclosure — the live stream forgetting the policy.** *Threat:* an SSE connection
  opened before a scope was written continuing to stream unfiltered snapshots. *Control:* the
  scope is resolved **per event**, not at connection time; the same resolver, the same filter.
  *Tests:* `test_f30_sse_reevaluates_scope_on_every_event`.
- **Denial of service / fail-open — a malformed scope policy.** *Threat (both directions):* a
  corrupt selector silently resolving to "everything" (disclosure), or hiding everything from the
  admin who must repair it (lockout). *Control:* a malformed scope policy **denies for viewer and
  editor** — never fails open — with a warning and an audit row; and **admin is never scoped**
  (DECISIONS #58), so the repair path is structurally exempt. *Tests:*
  `test_f29_malformed_scope_policy_denies_viewer_and_editor`, `…_admin_is_never_scoped`.
- **Elevation — scope confused with authorization.** *Threat:* treating "in scope" as "authorized",
  so a scope grant becomes a capability grant. *Control:* the two resolvers are independent and
  composed in one order — authorization first (401/403), then scope (filter/404). A scope policy is
  not an input to `resolve_capabilities()` at all. *Tests:* `test_f28_scope_grants_no_capability`.

### Hot path (A1) — governance must not reach ingestion

- **Availability / prime-directive violation — a policy read on the trap path.** *Threat:* a
  capability or scope lookup creeping into `datagram_received`, the queue, or the engine batch,
  adding a lock or an I/O to the path that must stay lossless. *Control:* both policies are read
  **HTTP-side, per request**, and nowhere else; `receiver.py` imports neither `rbac` nor `shaping`.
  The v0.6.0 F24 source-level assertions over `datagram_received` remain in force and are extended
  with the governance identifiers. *Tests:* `test_f33_datagram_received_gained_nothing`,
  `…_receiver_does_not_import_the_governance_modules`,
  `…_engine_and_learning_are_untouched_by_governance`, unchanged `test_perf.py`.

### Migration (A5, A6)

- **Tampering / misconfiguration — a migration that changes behaviour.** *Threat:* `0006` seeding a
  policy row, and an upgrade therefore silently altering who may do or see what. *Control:* `0006`
  is additive and forward-only and seeds **zero** governance rows; "no rows" resolves to the
  ceiling and to full visibility, which is byte-identically v0.6.0 (DECISIONS #54). *Tests:*
  `test_migrate_populated_v060_database_seeds_no_governance_rows`,
  `test_upgrade.py::test_v070_upgrade_changes_no_behaviour`, plus the built wheel/sdist check (F12).

## Rejected by design (stronger than a control)

- **Tenant isolation via scoping** — *disposition:* **not built, and not claimed.** Visibility
  scoping is a presentation control and is **not tenant isolation**; it is a projection over reads. It does not partition the learned matrices, does not prevent
  a situation from forming across a boundary, and does not segment retention or audit. Claiming
  otherwise would be an over-claim that a customer would discover during an incident. The limit is
  stated in `SCOPE-0.7.md`, `DESIGN.md`, `README.md`, `MIGRATION.md`, and the UI, and a
  documentation test asserts the statement is present so it cannot be quietly dropped
  (`test_f32_scoping_is_not_tenant_isolation_is_documented`).
- **Dynamic roles** — *disposition:* not built (DECISIONS #56). A runtime-defined role has no
  compiled ceiling, so the first operand of the escalation-proof intersection would become stored
  data and the guarantee would collapse back into a validation check.
- **Write-time ceiling validation as *the* control** — *disposition:* demoted to a usability
  affordance. A check protects only the write paths it is on; the intersection protects every path
  that will ever exist.

## v0.7.0 residual risk (accepted, documented)

- **Scoping is presentation, not isolation.** A scoped operator shares global learned state,
  global situation ids, and global timing with everyone else. A determined observer can still infer
  *that* activity exists beyond their boundary from correlated behaviour in aggregate — the
  redaction count says so out loud rather than hiding it. Only true isolation would close this, and
  that is a later, larger feature.
- **A scoped operator sees a partial picture.** An incident spanning the boundary is, to them,
  smaller than it is. This is a real operational hazard, and the design's answer is honesty: the
  redacted member count and type are shown precisely so the operator knows the edge of their own
  picture rather than confidently mis-sizing an incident. It is a trade, not a solved problem.
- **Cardinality is information.** The redaction discloses *how many* members are out of scope. That
  is strictly less than the situation id and `updated_at` a viewer already sees, and is the minimum
  needed to keep the operator honest — but it is not zero.
- **A compromised admin can rewrite the perimeter.** As with the v0.6.0 scoring config, the control
  is not prevention — an admin is trusted to govern by definition. It is that every change is
  bounded by the compiled ceiling, append-only, attributable, reversible by pointer, and audited.
- **Policy history grows one versioned row per change.** Bounded (a handful per year), immutable,
  never pruned — the same deliberate trade as `scorer_config`.

## v0.7.0 coverage check

Every threat above names a control and a test. Findings **F27–F33** are tracked in
`SECURITY-REVIEW-0.7.md` (property → fix → test); Gate 5 requires every F-numbered test green,
`make eval` byte-identical, the governance-parity test green at empty policy, the property-based
ceiling-invariant test green over generated and adversarial policies, and the authorization matrix
clean over the four new capabilities.

---

# v0.7.1 extension — the write perimeter

**Nothing new is added to the system.** This extension records a *defect class* found in a release
whose review declared it closed, and the controls that close it. v0.7.0 built the visibility scope
as a **read projection** and described it as a perimeter. A perimeter enforced on one side is not a
perimeter, and the six findings F34–F39 are what that cost.

The one sentence this extension adds to the model:

> **Authorization never reads data the constrained party can write, and a write is inside the
> perimeter or it is a defect.**

## Changed asset — the write path as an escalation and disclosure channel

v0.4.0 recognised response bodies as a disclosure channel and v0.7.0 recognised NE visibility as
one. What neither recognised is that a **write** is both: its *status code* discloses existence,
its *effect* reaches global state, and — the case that defines this release — its *payload* can
become an input to the authorization decision that is supposed to constrain it.

## STRIDE — the surface that was already there and was not modelled

### Scope enforcement (A2) — the write routes outside the read perimeter — **F34**

- **Spoofing/Elevation**: a scoped `editor` writes feedback on, closes, and labels NEs they cannot
  see. The learned-state effect reaches NEs outside their scope, which is elevation in the only
  currency the correlator has.
- **Information disclosure**: 200-vs-404 on writes is an existence oracle over exactly the resources
  the read path is careful to make indistinguishable.
- *Control*: the three `editor` routes resolve scope through the **same** `scope_for` the reads use
  and deny through their **existing** 404 branch — same status, same body, same timing (DECISIONS
  #65). The denial is audited.
- *Check*: `test_f34_scope_is_enforced_on_the_editor_write_routes`,
  `test_f34_an_in_scope_editor_can_still_write`, and the **generated**
  `test_f34_every_mutating_route_below_admin_resolves_scope`, which walks `ROUTE_PERMISSIONS` so a
  route added in any future release fails CI until it is inside the perimeter.

### Scope resolution (A2) — an authorization input the constrained role can write — **F35**

- **Elevation of privilege**: the scope resolver read the **operator label**, and the operator label
  is written by `POST /api/labels`, an `editor` route. A scoped editor widened their own visibility
  by labelling an out-of-scope device to match an in-scope glob. A second variant needed no glob:
  the timeline filtered on the rendered `COALESCE(label, ip)` display string, and labels are not
  unique, so a colliding label leaked an out-of-scope NE's alarm timing and classes.
- *Control*: scope selectors resolve against **NE identity and NE address only** — structurally, so
  a future second write path to `label` cannot reopen it (DECISIONS #66). The timeline filters on
  `ne_id`, never on a display string (DECISIONS #67). `Scope.labels` and the label column of
  `list_ne_for_scope()` are deleted, and the dead-code gate keeps them gone.
- *Check*: `test_f35_an_editor_cannot_widen_their_own_scope_with_a_label`,
  `test_f35_a_colliding_label_does_not_leak_an_out_of_scope_timeline`,
  `test_f35_scope_selectors_never_read_operator_writable_data`, and the invariant test
  `test_f35_no_resolver_input_is_writable_by_a_scopable_role`.

### Learned state (A1, A2) — unbounded, non-idempotent operator feedback — **F36**

- **Tampering/DoS**: `learn_epoch` advanced the **global** forgetting epoch on every feedback post
  and `add_feedback` had no idempotence and no bound. 60 confirms plus 20 splits took one pair's mass
  from 1.000000 to 1.824e-05; ~600 epochs a minute drives every learned mass to ~1e-14. The role that
  can do it is `editor`, and under F34 it need not even see the situation.
- **Repudiation**: `feedback` recorded no author, so "who degraded the matrices?" was unanswerable —
  the audit chain recorded the API call but not the effect.
- *Control*: idempotence per `(situation, verdict)` bounds a situation's total influence at two
  applications whatever anyone posts (DECISIONS #68); the epoch tick belongs to a **closed
  situation** only (DECISIONS #69); `principal_ref` and `role` attribute every row.
- *Check*: `test_f36_repeated_feedback_is_idempotent_and_bounded`,
  `test_f36_a_changed_verdict_still_applies_once`,
  `test_f36_closing_a_situation_still_ticks_the_epoch`, `test_f36_feedback_records_its_author`.

### Store (A1) — an unbounded, never-reclaimed write primitive — **F37**

- **DoS/Tampering**: `set_label` was an unconditional UPSERT into a table with no foreign key, and
  `prune()` never touched it. Every `editor` held an unbounded write primitive against the database
  file, and its rows were never reclaimed.
- *Control*: the target must exist, and the failure is **the same 404 the out-of-scope case
  produces**, so the fix for F37 cannot re-introduce the oracle F34 closes (DECISIONS #70). Migration
  `0007` removes existing orphans. No foreign key in a patch release, deliberately (DECISIONS #71).
- *Check*: `test_f37_a_label_write_to_a_nonexistent_target_is_rejected`,
  `test_f37_label_writes_to_real_targets_still_work`, and the migration's orphan-cleanup gate.

### Scoped reads (A2) — truncation before filtering as a volume oracle — **F38**

- **Information disclosure**: `LIMIT` was applied over the global ordering and the scope filter ran
  afterwards, so a scoped principal's returned count varied with out-of-scope volume — the aggregate
  oracle F32 claims is closed.
- **Availability (operational)**: worse than the disclosure in a NOC — a scoped viewer's own open
  incidents vanished from their list while a noisy neighbour was busy.
- *Control*: the scope predicate is bound into the query so `LIMIT` applies to the filtered set,
  with the unrestricted path on the **unmodified v0.7.0 SQL** (DECISIONS #72).
- *Check*: `test_f38_truncation_is_applied_after_the_scope_filter`,
  `test_f38_the_unrestricted_result_set_is_unchanged`.

### Audit and transaction integrity (A3, A5) — an orphan write surviving a failed request — **F39**

- **Repudiation/Tampering**: one `aiosqlite` connection is shared by the engine and the API;
  `main.py` rolled back and the HTTP layer did not. A handler that mutated and then raised left the
  statement pending, and the next `commit()` from **any other caller** adopted it. Measured: a forced
  audit failure inside `POST /api/users/{uid}/role` returned 500 and the role change persisted **with
  no audit row**, contradicting F31's "every change is attributable".
- *Control*: one async context manager — acquire the lock, run the body, commit on success,
  `rollback()` on any exception, re-raise — used by every mutating handler, implemented once
  (DECISIONS #73). `Engine.apply_feedback`'s internal commit is removed so the API owns the boundary
  and the order is mutate → audit → commit on every write path.
- *Check*: `test_f39_a_failed_write_leaves_nothing_to_commit`,
  `test_f39_feedback_commits_exactly_once`.

## Corrections to the v0.7.0 model

- The v0.7.0 entry "**Existence oracle via a scoped resource**" was scoped to *reads*. It is
  superseded: the oracle existed on **writes** for the whole of v0.7.0. See F34.
- The v0.7.0 entry "**Leakage through aggregates**" claimed enumerating aggregates are computed over
  the in-scope set. True for `/api/stats`; **false** for the truncated list endpoints. See F38.
- "Scope is never an authorization input" (F28) was stated of *capabilities* and was correct. What
  was missed is the converse: an authorization input may not be **operator-writable data**. That
  sentence is new to the model as of this release, and it is the generalisation the review method
  now carries forward.

## v0.7.1 residual risk (accepted, documented)

- **Label globs in scope selectors are gone**, and some operators will miss them: a labelled estate
  now has to be scoped by address, `ne:<id>`, or CIDR. The trade is that authorization no longer
  reads operator-writable data, and it is not negotiable at any label-uniqueness guarantee an
  operator could offer.
- **Idempotence per `(situation, verdict)` is a real usability loss.** An operator who genuinely
  wants to reinforce the same verdict twice cannot. The second identical verdict carries no new
  information, so this is the correct trade — but it is a change an operator can notice.
- **The redaction cardinality disclosure of v0.7.0 is unchanged and still real.** Nothing here
  narrows it.
- **Scoping is still presentation, not isolation.** Every v0.7.0 residual-risk item above remains
  true and is re-stated, not re-litigated.
- **NE addresses are still created by whoever has network position to send a trap.** That is the
  pre-existing A1 attacker, unchanged by this release, but it is worth naming next to the new
  resolver-input invariant: the invariant says no *authenticated scopable role* can write a resolver
  input, and address creation sits outside that boundary by design.
- **The deepest one: a defect class was found in a release whose review declared it closed.** The
  review method has been changed rather than the claim quietly corrected — see
  `SECURITY-REVIEW-0.7.1.md` §"What changed in the review method".

## v0.7.1 coverage check

Every threat above names a control and a check. Findings **F34–F39** are tracked in
`SECURITY-REVIEW-0.7.1.md` (property → fix → test). Gate 5 requires every F-numbered test green,
`make eval` byte-identical, empty-policy parity except the three documented behaviour changes, the
generated write-perimeter test green over `ROUTE_PERMISSIONS`, and the resolver-input invariant test
green.

---

# v0.7.4 — the declaration gate, completed

Two findings, **F40** and **F41**, both in `api/declare.py`, both found by adversarial probing of
v0.7.2's registration gate and **reproduced by execution** before either was fixed. Neither was
exploited on the v0.7.3 surface. Full analysis: [`SECURITY-REVIEW-0.7.4.md`](SECURITY-REVIEW-0.7.4.md).

**No new asset, no new surface.** No route, capability, audit action, migration, runtime dependency
or served path is added. The counts of §"v0.7.0 coverage check" are unchanged: 39 routes, 1 public
route, 39 scope postures, 28 capabilities, 14 audited-denied, 30 audit actions, 7 migrations.

## Changed control — the gate stops enumerating and starts asserting

The registration gate v0.7.2 introduced was a **list of cases**: three verbs, one registration
style, one path prefix. Each entry was true of the surface in front of it and silent about the next
thing anyone would write. A guard whose whole value is *"nothing gets past this"* is worth exactly
its least-covered path, and both findings below are that observation at two levels.

### Registration paths outside the gate (A3, A6) — **F40**

**Threat.** A route reaches a running appliance without declaring the capability it requires or its
visibility-scope posture, and is therefore outside the authorization map. The runtime fail-closed
still denies it *if* it is under `/api`, but the appliance is already serving, and the route-map
completeness tests run in CI rather than at startup.

**Observed** (untouched v0.7.3 tree): a route registered directly on the FastAPI application, rather
than through `DeclaredRoutes`, raised nothing, took the route count 4 → 5, and appeared in neither
`ROUTE_PERMISSIONS` nor `ROUTE_SCOPE`. `DeclaredRoutes` also exposes no `put`/`patch`, so those verbs
had no gated path at all. Neither existing guard can see it: one greps for `@app.<verb>` decorators,
the other asserts there is exactly one direct-registration caller **today**.

**Control.** `declare.assert_every_route_is_declared(app)` puts **every route on the built
application** back through `require_declaration`, and `create_app` calls it as its last statement
before returning — so a mis-declared route **stops the process** rather than failing only under
test. It is complete *by construction*: the function names no verb and no registration mechanism, so
a mechanism nobody has written yet still produces a route and is still caught. The decorator-time
refusal is kept alongside it, because failing where the route is written gives the better error.

**Check.** `test_f40_*` (5 tests), each proven to fail on the unmodified tree.
`test_f40_the_assertion_runs_before_create_app_returns` parses `create_app`'s source, so the
assertion cannot later be demoted into a test helper.

### Exemption by path prefix rather than by absence of capability (A2, A3) — **F41**

**Threat.** An authenticated non-`/api` route is exempt from the gate **by accident** — declaring
neither a capability nor a scope posture, and invisible to the route-map completeness tests, which
enumerate `/api` only. `/metrics` is already on `docs/ROADMAP.md` and is the concrete case.

**Observed:** `require_declaration("GET", "/metrics")` and `("GET", "/admin/debug")` both returned
without raising, while `("GET", "/api/undeclared")` raised.

**Control.** `declare.UNAUTHENTICATED_PATHS`, an explicit frozenset of the paths served with no
identity. Membership is a **reviewable claim** — "no capability is required to fetch this" — rather
than a consequence of four characters. It is asserted against what is actually served from two
independent directions: against `routes_static.STATIC_ASSETS` plus the health surface, and against
the non-`/api` routes of a **built application** (which is how `/openapi.json`, registered by
FastAPI itself, is covered at all).

**Check.** `test_f41_*` (14 tests). Four refusal cases and the two allowlist-derivation tests fail on
the unmodified tree; the eight "every currently-served public path is still accepted" cases pass on
**both** trees, which is what proves the gate did not become narrower.

### Authorization authority under a package split (A3, A5)

**Threat.** `rbac.py` became `rbac/`. A re-export that copies rather than references creates a second
source of authorization truth that diverges on first mutation — and the test suite already mutates
`rbac.ROUTE_PERMISSIONS` in a fixture.

**Control.** Re-export by **identity**, with eight identity assertions and an AST check that no
module under `rbac/` except `tables.py` binds any table at module level. The three import-time
asserts moved into `tables.py` with the tables they constrain, so they still run as part of building
them.

**Check.** Both new tests were shown to fail against a deliberately-copying `__init__.py`, and the
second additionally against a `policy.py`-local fallback. Under that sabotage **218 pre-existing
tests pass green**, which is the measurement that justifies the new tests existing at all.

## Added to the model — information disclosure, not fixed

**`/openapi.json` is served without authentication.** FastAPI registers the schema route itself with
no security dependency, so the full API surface — every path, method and request model — is readable
by anyone who can reach the port. `docs_url` and `redoc_url` are disabled; `openapi_url` is not.

This is **pre-existing** (identical in v0.7.2 and v0.7.3) and deliberately **not changed** in a
release whose parity story forbids altering a served path. It is listed in `UNAUTHENTICATED_PATHS`
because that set records what *is* served, not what should be.

**Severity: low, and not nothing.** It discloses no data and no credential, and every route it names
is enforced by the authorization matrix, so it is not an access-control bypass. Its value to an
attacker is reconnaissance: the exact shape of the write surface without a single guess. On the
trusted management network the deployment guidance assumes, that is a small increment; on a more
exposed deployment it is the first thing read. Recorded on `docs/ROADMAP.md` for a release that may
change a served path.

## v0.7.4 residual risk (accepted, documented)

- **The gate is complete for *registration*, which is not correctness of what is registered.** A
  route can no longer be *undeclared*; it can still be *wrongly* declared. Every `ROUTE_SCOPE` entry
  is a human judgement, and the gate checks that one is present, not that it is true.
- **`ROUTE_SCOPE` is still descriptive, not injected.** The perimeter does not enforce the declared
  posture; each handler calls `scope_for` itself. Each declaration is checked against the route's
  *observed behaviour* by `tests/test_declaration.py`, which is much stronger than a comment and
  weaker than a structure. Unchanged since v0.7.2 (DECISIONS #80) and still a ROADMAP line, because
  injection changes control flow and control flow is behaviour.
- **An empty `DEBT_ALLOWLIST` is not a claim of good factoring.** It means no module exceeds a line
  count, which is a proxy. `SECURITY-REVIEW-0.7.4.md` §4.2 names what a reviewer would look at next
  and why, as an opinion.
- **A text-scanning guard in the suite counts mentions, not calls.**
  `test_add_api_route_is_confined_to_the_static_asset_allowlist` greps for an identifier; documenting
  the F40 fix in a docstring tripped it. Reworded rather than fixed (a defect found during a move
  release is a ROADMAP line), but a guard that cannot tell a call from a comment will eventually
  either fire wrongly or be silenced.
- **This was not a full re-review of the attack surface.** F1–F39's controls were re-checked only to
  the extent CI asserts them on every commit. The last full pass was v0.7.1's.

## v0.7.4 coverage check

Every threat above names a control and a check. Findings **F40** and **F41** are tracked in
`SECURITY-REVIEW-0.7.4.md` (property → fix → test). Gate 5 requires every F-numbered test green,
`make eval` byte-identical, the route-order and authorization-matrix tests green **and unedited**,
the F35 resolver-input invariant green and unedited, both authorization single-sourcing tests green,
and an upgrade from a real v0.7.3 database with no migration, an identical store snapshot and the
same audit final hash.

---

# v0.7.5 — the gate refuses what it cannot classify; the operator's click means what they meant

One finding, **F42**, in `api/declare.py`, found by adversarial probing of the v0.7.4 gate and
**reproduced by execution** — five shapes, each serving real traffic, with a passing control — before
it was fixed. Not exploited on the v0.7.4 surface. Plus one integrity defect on the operator's own
path, specified in `FEEDBACK-PATH-0.7.5-DRAFT.md` and repaired here. Full analysis:
[`SECURITY-REVIEW-0.7.5.md`](SECURITY-REVIEW-0.7.5.md).

**No new asset, no new surface.** No route, capability, audit action, migration, runtime **or dev**
dependency, or served path is added. The counts of §"v0.7.0 coverage check" are unchanged: 39 routes,
1 public route, 39 scope postures, 28 capabilities, 14 audited-denied, 30 audit actions,
7 migrations. `make eval` is byte-identical, so the engine, store, correlation and scoring seam are
untouched.

## Changed control — the gate stops assuming a shape

v0.7.4 replaced *"a list of registration mechanisms"* with an assertion over the built application
and called it complete by construction. It named no mechanism — true — but assumed a **shape**: a
flat object exposing `.path` and `.methods`. That is enumeration wearing construction's clothes, and
it is the same observation v0.7.4 made about its own registration gate, one level up.

### Route shapes the gate could not classify (A3, A6) — **F42**

**Threat.** A route reaches a running appliance without declaring the capability it requires or its
visibility-scope posture, and is therefore outside the authorization map — the F40 threat, through a
door F40 did not close.

**Observed** (untouched v0.7.4 tree, every probe app built `docs_url=None, redoc_url=None` to match
`create_app`, with an untouched control that correctly raised nothing): five shapes passed the gate
**and served**. `_IncludedRouter` from `include_router` (200); `Mount` with a sub-application (200);
`Mount` with `StaticFiles` (200, file contents returned); `APIWebSocketRoute` (handshake completed);
an explicitly-registered `HEAD`-only `APIRoute` (200). Two distinct fail-open branches, not one: the
`_IncludedRouter` escapes through `path is None`, the rest through an empty `methods` set.

**And the coverage was not stable.** The `include_router` shape was **refused** on
`fastapi==0.115.0` — the floor of this project's own pin — and **skipped** on `0.141.1`. With no
upper bound, no lockfile, and CI running a bare `pip install -e .[dev]`, the gate's completeness was
a property of whatever pip resolved that morning, and it regressed **with no commit and no failing
test**.

**Control.** `declare.KNOWN_ROUTE_SHAPES` — an explicit tuple of the route classes the traversal can
check — with **refusal** of anything outside it, naming the offending module and class. Every object
on `app.routes` now has an outcome: checked, or refused. None is skipped. Matched on exact type, not
`isinstance`, because `APIRoute` subclasses `Route` and `isinstance` would admit a future subclass
unexamined. `HEAD` is skipped only when `GET` is present on the same route — the only case Starlette
synthesises — and the `OPTIONS` exemption is removed, having fired on nothing.

Recursing into each container was **rejected**: `include_context`, `original_router` and
`effective_route_contexts` are undocumented FastAPI internals, so a gate walking them would again be
correct only for the dependency versions whose internals it matched (DECISIONS #98).

**Check.** `test_f42_*` (14), twelve proven red on the unmodified tree by stashing `declare.py`
alone. `test_f42_the_live_app_produces_exactly_the_known_shapes` asserts the shape set of a **real**
`create_app` equals the allowlist, so a dependency upgrade that changes the representation fails
loudly, naming the new class, on the day of the upgrade.

**Residual, recorded.** That test detects a **new shape**, not a **changed meaning**. If a future
`APIRoute` carried its verbs somewhere other than `.methods`, the shape set would be unchanged and
the gate would quietly check nothing. `docs/ROADMAP.md`.

### Operator feedback recorded against an unevaluated membership (integrity)

**Threat.** Not an attacker threat — an **integrity** threat to the learned state and to the v0.8.0
dataset. `renderSituations` destroyed and rebuilt every situation card every two seconds, so a click
could land on a card rebuilt between the operator's visual decision and their mouse-down. The verdict
is then recorded against a membership the operator never evaluated: a **silently wrong label**, which
`learn.penalize()` acts on, the dataset records, and a future model trains on — and which **nothing
in the system can detect**, because the record carries no evidence of what was on screen.

No `F` number: no privilege is crossed, no attacker-controlled input is involved, and it was already
specified and scheduled. It is recorded here because its consequence is data integrity.

**Control.** The expanded card's detail node is held across SSE updates; `renderDetail` swaps content
in atomically so the container is never displayed empty; a `held while open` marker tells the
operator the card is frozen.

**Check.** Six structural assertions (five proven red against the v0.7.4 source), the feedback and
SSE contract tests **unedited**, and — for the behaviour itself —
[`../gates/v0.7.5-manual-verification.md`](../gates/v0.7.5-manual-verification.md).

**Residual 1 — provenance is not closed.** The label is now *deliberate*; it is still not *traceable
to what was on screen*. That is the membership fingerprint and it is **v0.8.0**
(`FEEDBACK-DATASET-0.8-DRAFT.md` §2.2).

**Residual 2 — the marker informs, it does not enforce.** A held card is stale by design and the
badge is the whole of the mitigation. There is no confirmation step and nothing that fails closed if
it goes unread; an operator under incident pressure reading a card they believe is live is the
realistic failure. **Accepted human-factors residual.**

## v0.7.5 residual risk (accepted, documented)

- **The automated suite does not prove this release's behavioural claims.** Three of four intentional
  behaviour changes are browser behaviour and there is no JavaScript runtime in this repository, by
  design. The UI tests assert the shape of the source and say so in their own comments; the proof is
  a manual protocol that **this build wrote and did not execute**. DECISIONS #99.
- **The staleness marker is a human-factors control.** See Residual 2 above.
- **Label provenance is unresolved until v0.8.0.** See Residual 1 above.
- **The shape allowlist is enumeration**, labelled as such. What makes it maintained rather than
  merely written down is one test, whose own limit is recorded above.
- **The documentation guard's forbidden-phrase half remains enumeration** and remains
  spelling-sensitive (`->` versus `→`). Its element-tag half went from 31% to near-complete
  visibility; those are different halves and only one of them generalises.
- **`renderEntityDetail` still has the clear-then-fill shape** repaired in `renderDetail`. Not on the
  label path, deliberately not fixed inside a small diff. `docs/ROADMAP.md`.
- **`/openapi.json` is still served unauthenticated**, and `ROUTE_SCOPE` is still descriptive rather
  than enforcing. Both carried forward from v0.7.4 unchanged.
- **This was not a full re-review of the attack surface.** F1–F41's controls were re-checked only to
  the extent CI asserts them on every commit. The last full pass remains v0.7.1's.

## v0.7.5 coverage check

Every threat above names a control and a check. Finding **F42** is tracked in
`SECURITY-REVIEW-0.7.5.md` (property → fix → test). Gate 5 requires every F-numbered test green,
`make eval` byte-identical, the route-order and authorization-matrix tests green **and unedited**,
the F40/F41 sets green and unedited, the feedback and SSE contract tests green **and unedited**, the
UI at four files with the CSP unchanged, and an upgrade from a real v0.7.4 database with no
migration, an identical store snapshot and the same audit final hash.

---

## v0.8.1 — the dataset's lifecycle (F44)

One finding, **F44**, in `store/retention.py`. It is a **data-integrity** defect with **no
confidentiality, audit-chain or access-control consequence**, recorded here because the asset it
destroyed is the one this product cannot reacquire, not because it is a vulnerability.

### The human label outliving its operational data (T-DATA-1) — **F44**

| | |
|---|---|
| **Asset** | the operator's verdict (`feedback`) and its membership record (`feedback_member`) — the least reconstructible data in the system. It cannot be recomputed, re-derived, or asked for again. |
| **Threat** | routine background maintenance destroys it, silently, on a schedule nobody chose for it |
| **Trust boundary crossed** | **none.** No principal is involved and no data is disclosed. |
| **Vector** | `prune()` deleted `feedback` for every situation closed longer than the *operational* retention (`NETCORENOC_RETENTION_DAYS`, default **7.0 days**), with `feedback_member` following by `ON DELETE CASCADE`. Correct before v0.8.0, when feedback was a transient learning signal; wrong the moment v0.8.0 made that row the dataset's label. |
| **Impact** | in a **default** deployment, every human verdict was lost seven days after its situation closed, while the `dataset_pair` features it justified survived — so the corpus grew and its labels evaporated, invisibly |
| **Control** | a label is not operational data and is not governed by the operational retention. `feedback` left `prune()`'s deletion set; the labelled `situation` row is retained with it, because `feedback.situation_id` is a restricting FK under `foreign_keys=ON` and the sweep would otherwise raise. Only the tiers in `retention_policy.py` govern a label, and only the **audit** tier deletes one. DECISIONS #109, #110. |
| **Check** | `tests/test_dataset.py::test_f44_*` (four; two proven to fail on the unmodified tree by stashing), `test_the_whole_life_of_a_label`, and `test_the_audit_sweep_deletes_outside_its_bound_and_nothing_newer` — which asserts the one surviving background deleter cannot reach anything newer than the operator's bound |

### The deletion paths, enumerated

The security-relevant statement of this release: **exactly two paths can delete a human label**, and
both are bounded by a number the operator set.

| Path | Bound | Attribution | Record |
|---|---|---|---|
| the audit sweep, on the maintenance tick | `retention.audit_days` (default 730 d); cannot reach anything newer, by test | `system`, enforcing a configured policy | counted in `capture.audit_swept`, surfaced on `GET /api/dataset/retention` |
| an explicit admin reduction of the audit bound | the operator's own new value, after a preview count; `preview` defaults to `True` | the authenticated **admin** principal | `retention.preview` + `retention.change`, the audit row written **before** the deletion |

`store.prune()`, the sink's dual bound, and the **training** tier cannot reach a label at all — the
training tier is a `WHERE` clause and destroys nothing (DECISIONS #110).

### A near-miss worth recording

The dataset sweep was first written as `store.prune_audit` — already the name of the audit-**log**
deleter, and both are mixed into one `Store`. It would have **silently shadowed audit-log
retention**, which *is* an audit-chain control. `mypy --strict` refused the definition and it was
renamed `prune_dataset_audit`. The type checker was the control that caught a change to the audit
log's lifetime; no test would have.

### Residuals carried into v0.8.1

- **`Capture.warnings()` is never surfaced.** Built and tested, but `runner.py`'s warnings lambda
  does not call it, so a degraded capture is invisible on `/api/stats`. Not on the label path;
  `docs/ROADMAP.md`.
- **The sink's row cap, not its age limit, governs** at realistic traffic. Documented in `DESIGN.md`
  in this release and deliberately not changed — it is a design decision with data behind it, and
  the data is v0.9.0's.
- **Orphaned promoted pairs are measured, never collected.** Deleting features whose label an
  operator destroyed would be a second destruction nobody asked for. The bias report counts them.
- **All v0.7.5 residuals above are carried forward unchanged.** `/openapi.json` is still served
  unauthenticated, `ROUTE_SCOPE` is still descriptive, `renderEntityDetail` still has the
  clear-then-fill shape.
- **This was not a full re-review of the attack surface.** F1–F43's controls were re-checked only to
  the extent `make qa` asserts them on every commit. The last full pass remains v0.7.1's.

## v0.8.1 coverage check

Every threat above names a control and a check. Finding **F44** is tracked in
`SECURITY-REVIEW-0.8.1.md` (property → fix → test). Gate 5 requires every F-numbered test green,
`make eval` byte-identical, `make bias-report` deterministic, the F34–F39 and F40–F43 sets green
**and unedited**, the route tables and declaration gate unchanged, the layer and module-size guards
green with `engine.py` at its unchanged 580-line ceiling and `DEBT_ALLOWLIST` empty, and an upgrade
from a real v0.8.0 database with **no migration**, an identical schema hash, identical row counts,
the same audit final hash, and any pre-existing orphans **reported rather than silently collected**.
