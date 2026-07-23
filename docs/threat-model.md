# OptiCorr v0.2.0 — Threat Model

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
- **A5 — stolen DB file.** Has a copy of `opticorr.db`. Goal: recover live sessions,
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
  requests, `Origin`/`Referer` host must match `Host` and header `X-OptiCorr-Client: ui`
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
  from the general prune; a dedicated `OPTICORR_AUDIT_RETENTION_DAYS` (default 365)
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
(`entity.reset` / `profile.reset`) recovers. *Residual risk (accepted, documented):* OptiCorr
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
