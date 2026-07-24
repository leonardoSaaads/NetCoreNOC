# OptiCorr v0.2.0 — Security Review Remediation

The six findings from the independent v0.1.0 security review, each mapped to its fix
commit and the regression test that proves it. All fixes precede the features that depend
on them (F1 before any auth code, per prime directive 3).

| # | Sev | Finding | Fix commit | Fix summary | Regression test(s) |
|---|-----|---------|-----------|-------------|--------------------|
| **F1** | High | Stored XSS via `innerHTML` interpolation of externally controlled strings (labels **and** trap-supplied `instance`/varbinds); unauthenticated via UDP 162 under the allow-all default | `6475e75` | `esc()` + `textContent`/`createElement` for every externally sourced string; UI split into `index.html`/`app.js`/`style.css` (no inline script/style); d3 vendored locally; strict CSP + `X-Content-Type-Options`/`X-Frame-Options`/`Referrer-Policy`; `Cache-Control: no-store` on `/api` | `test_security_ui.py::test_f1_hostile_strings_survive_the_ingest_path_as_json`, `::test_security_headers_on_every_route_class`, `::test_ui_source_has_no_f1_antipatterns`; live browser check in `docs/gates/v0.2-phase-3.md` |
| **F2** | High | Single shared static token, no identity/attribution; UI stored it in `localStorage` (exfiltratable by F1) | `6475e75`, `719dca6` | `localStorage` token input removed entirely; per-user sessions (`HttpOnly` cookie) and per-identity service tokens replace the shared token; identity + role attributed on every request and audit row | `test_security_ui.py::test_ui_source_has_no_f1_antipatterns`, `test_rbac.py::test_authorization_matrix`, `test_findings.py::test_legacy_token_accepted_as_admin_and_audited_once` |
| **F3** | Med | Generated API token written to the startup log; secrets in operational logs | `719dca6` | Generated-token log line removed; no secret is ever logged; root-logger `RedactionFilter` masks any bearer/cookie/`key=value` secret; the bootstrap banner (stdout `print`) is the single sanctioned once-only exception | `test_findings.py::test_f3_secret_leak_scan_over_login_and_token_flows`, `::test_redaction_filter_masks_secrets`, `::test_configure_logging_installs_redaction` |
| **F4** | Med | Quarantine persisted the raw packet, exposing the SNMPv2c community string (a password) in cleartext | `719dca6` | Community never persisted or logged; per-install 32-byte HMAC key in `meta` yields `community_tag = HMAC-SHA256(key, community)[:12]` computed in the receiver and stored on the alarm; quarantine blanks the community octets or, if unlocatable, stores metadata only (`sha256`, length, first 8 bytes) | `test_findings.py::test_f4_community_never_persisted_only_tagged`, `::test_f4_quarantine_blanks_community_in_raw`, `::test_f4_quarantine_metadata_only_when_unlocatable` |
| **F5** | Med | No TLS: cookies/tokens travel cleartext on the management LAN | `719dca6` | Optional built-in TLS (`OPTICORR_TLS_CERT`/`OPTICORR_TLS_KEY`) passed to uvicorn; the session cookie gains `Secure` automatically when TLS is enabled; reverse-proxy TLS documented in `SECURITY.md` | `test_auth.py::test_secure_cookie_when_tls_enabled`, `::test_session_cookie_flags` |
| **F6** | Low | HTTP binds `0.0.0.0` by default with no operator warning | `719dca6` | Persistent admin-visible banner (surfaced via `/api/stats.warnings`) when the trap allowlist is empty or the HTTP listener is non-TLS on a non-loopback bind | `test_findings.py::test_f6_operator_warnings_conditions`, `::test_f6_warnings_surface_in_stats`; live banner in `docs/gates/v0.2-phase-3-ui.png` |

## Verification summary

Every finding has a fix, a named `test_f<N>_*` (or directly-mapped) regression test, and
appears in `docs/threat-model.md` with its planned control. The full suite (171 tests,
94.63% coverage, ruff/`mypy --strict`/bandit/pip-audit clean) is green, and the CI adds an
explicit secret-leak scanner step.
