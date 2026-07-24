# Build Report — NetCoreNOC v0.4.0

**Theme: trustworthy by construction.** A security- and reliability-hardening release under a new
identity. **No new inference capability.** One Python 3.12 asyncio process, one SQLite (WAL) file,
environment variables only, one static UI, no build step, **zero new runtime dependencies**.

## What changed

- **Rebrand** *OptiCorr* / *NewProjectNetworj* → **NetCoreNOC** (import package, metadata, CI,
  container, env prefix, cookie, CSRF header, UI wordmark, logger). Wire identifiers get a
  one-version compatibility window (legacy `OPTICORR_*` env warns once; cookie rename forces one
  re-login; CSRF header changed with no window). Byte-identical eval delta proves it changed names,
  not behaviour.
- **Least-privilege response shaping** (`shaping.py`): one role-keyed serializer coarsens IPs and
  drops `source_ip`/`community_tag` for lower roles, including on the SSE live path.
- **Reliability**: supervised background tasks (crash → backoff-restart → operator warning); store
  integrity/FK check at startup; `sqlite` operational errors caught without breaking the audit
  chain; `/readyz` readiness; graceful queue drain on shutdown.
- **Supply chain / container**: whole UI now ships in the wheel (was broken); d3 SHA-256 pinned +
  CI job; Dockerfile pins the base patch tag, documents the digest pin and a hardened run recipe.
- **RBAC hygiene**: two duplicated audited-denied tables collapsed to one source with a divergence
  test; `config.read` split from `config.write`; dead code removed.
- **Corpus/abuse**: a declarative scenario DSL + trap simulator (test/tooling only); security-event
  correlation and network-fault breadth as engine-driven tests; a consolidated abuse suite that
  filled a real CSRF-regression gap.
- **UI**: admin screens pruned from a non-admin DOM; a design-token refresh (light variant, focus
  states, AA contrast, responsive) — still four files, CSP intact.

## Quality numbers

| Metric | Value |
|---|---|
| Tests | **283 passed** (from 224) |
| Coverage | **95.02 %** (`--cov=netcorenoc`; ≥ 85 %, ≥ v0.3.0 − 3) |
| ruff / mypy --strict / ruff format | clean |
| Dead-code gate (vulture + allowlist) | clean |
| bandit | clean |
| pip-audit | no known vulnerabilities |
| eval gated metrics vs frozen baseline | `pairwise_f1 1.0000`, `ari 0.9999`, `entity_accuracy 0.4480`, `root_top1 1.0000` — no regression |

## Security findings and remediations (F7…F14)

| # | Sev | Finding | Remediation | Test |
|---|-----|---------|-------------|------|
| F7 | Med | Response over-disclosure of source IPs to viewers | role-keyed shaping serializer | `test_shaping.py` |
| F8 | Med | Two drifting audited-denied tables | single `rbac` source + divergence test | `test_rbac::test_f8_*` |
| F9 | Low | `GET /api/config` behind a write capability | dedicated `config.read` | `test_rbac::test_f9_*` |
| F10 | High | Unsupervised background-task death | `Supervisor` (backoff-restart + warning) | `test_reliability` |
| F11 | Med | Unhandled `sqlite` error / damaged DB | catch + rollback (chain-safe); startup integrity check | `test_reliability` |
| F12 | Med | Wheel shipped only `index.html` (broken container UI) | package-data globs the whole UI + coverage test | `test_supply_chain` |
| F13 | Low | Vendored d3 unpinned | SHA-256 pin + CI job | `test_supply_chain` |
| F14 | Info | CSRF had no regression test (rename risk) | CSRF regression tests | `test_abuse` |

Standards: `docs/SECURITY-REVIEW-0.4.md` maps OWASP ASVS 4.0.3 L2 (met / partial / N-A with proof),
re-verifies NIST SP 800-63B, cites RFC 1157/3416/3418/3584, and records CIS-style container
hardening.

## Reliability results

Task-kill → recovery + warning; DB-locked mid-batch → rollback, counted, warned, chain intact;
startup FK damage → warning, still ingests; `/readyz` 503 on saturation (ok/not-ok only, no leak);
SIGTERM drains the queue within a bounded deadline leaving a consistent chain. All in
`test_reliability.py` (9 tests).

## Corpus / harness

The scored `eval/corpus/*.json` and the frozen `eval/baselines/v0.2.0.json` are **unchanged** —
the hardening release keeps the gated aggregate exactly. New scenarios are authored with the
declarative DSL (`eval/scenario_dsl.py`) and asserted as engine-driven tests (DECISIONS #37):

| Scenario | Phenomenon | Assertion |
|---|---|---|
| `login_burst` (C.3) | coordinated SNMP authenticationFailure burst | groups into one situation |
| `chassis_card` (C.2) | line-card failure + contained ports | one situation, 7 alarms |
| `bgp_flap` (C.2) | cross-device adjacency down | two situations cold (learned affinity would merge) |

`tools/trap_sim.py` (`make sim`) replays any DSL scenario over real UDP or writes it to JSON.

## Decisions (this release)

- **#34** Rebrand with a one-version wire-identifier compatibility window.
- **#35** Re-defer the `device_id` → `entity_id`/`ne_id` cutover to v0.5.0.
- **#36** Re-defer typed relations and device-archetype clustering to v0.5.0.
- **#37** New fault/abuse scenarios are engine-driven tests, not scored eval-corpus additions
  (keeps the frozen baseline intact).
- **#38** Role-aware UI: admin screens pruned from the DOM; gating verified statically (no
  headless-browser dependency).

## Deferred (ordered) → `docs/ROADMAP.md`

1. Remove legacy `OPTICORR_*` env aliases (v0.5.0).
2. Complete the `device_id` cutover (v0.5.0).
3. Typed relations; device-archetype clustering (v0.5.0).
4. Situation subsumption / impact scope / fingerprint-recurrence.
5. `Case` JSON contract implementation, downstream per-equipment testing, ticket emission — remain
   roadmap; only the draft is kept aligned.
6. SNMPv3, automatic MIB enrichment, PostgreSQL/NATS, external IdP/SSO, MFA.

## Honest caveats

- **No schema migration** ships; the `device_id` cutover (the only change that would need one) is
  deferred, so `alarm.device_id` remains redundant with `ne_id` for one more version.
- **Container base-image digest pinning** is documented but left as a deploy-time step (the
  `Dockerfile` pins a specific patch tag, not a full digest) — marked *partial* in the compliance
  table.
- **UI role-gating** is verified by static-discipline tests, not a headless-browser render (a
  deliberate P1 simplification, DECISIONS #38); the security properties (CSP, escaping, role-gated
  DOM) remain P0 and asserted.
- New network-fault scenarios prove *phenomenon-class* coverage, not exhaustive vendor breadth
  (breadth is P1).
