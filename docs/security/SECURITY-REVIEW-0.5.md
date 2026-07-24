# Security Review — NetCoreNOC v0.5.0

An adversarial review of everything v0.5.0 adds — the deployment artifacts (compose, systemd,
`.dockerignore`/`MANIFEST.in`), the community scaffolding (disclosure policy, `security.txt`,
templates, licence files), the `src/`-layout packaging change, and the committed (dormant) CI
workflows. v0.5.0 ships **no engine, schema, API, or UI-behaviour change**, so the runtime attack
surface gains exactly one new served path (`/.well-known/security.txt`, static and public). This
review's job is to prove the *organization* work introduced no weakness and that the packaging
move preserved every prior guarantee. Kept **honest**: an unmet control would be listed unmet with
a `docs/ROADMAP.md` line — none were.

Because this release adds no runtime code path, findings F15–F19 are **hardening/assurance
findings**: each is a property the new artifacts must have, closed with a fix (the artifact itself)
and a regression/assertion test that fails if the property is lost. No exploitable runtime
weakness was found.

Status legend: **met** (file + test prove it) · **planned** · **N/A** (one-line reason) ·
**partial** (met with a documented gap → ROADMAP).

## 1. Standards anchor (continued from v0.4.0)

- **Application**: OWASP ASVS 4.0.3 Level 2 (v0.4.0 mapping still holds; re-verified for the
  packaging/serving changes).
- **Vulnerability disclosure**: RFC 9116 (`security.txt`), coordinated-disclosure good practice.
- **Container / runtime**: CIS-benchmark-style hardening, now expressed declaratively in
  `docker-compose.yml` and an example systemd unit.
- **Supply chain**: pinned GitHub Actions (by commit SHA), least-privilege workflow permissions.

## 2. Findings — F15…F19 (continuing the F1–F14 series)

| # | Sev | Area | Finding / property asserted | Fix / control | Test | Status |
|---|-----|------|------------------------------|---------------|------|--------|
| F15 | Info | Container / compose | The compose file must reproduce the hardened run (read-only rootfs, `cap_drop: [ALL]`, `no-new-privileges`, `tmpfs /tmp`, named-volume DB), expose only trap + HTTP, add back exactly one capability for UDP 162, and carry no secret; `.env` git-ignored, only `.env.example` committed | declarative hardening in `docker-compose.yml` (drop-all then `cap_add: [CAP_NET_BIND_SERVICE]`); secrets only via `.env`/mounts/volumes; `.env` in `.gitignore` | `test_deploy.py::test_compose_reproduces_the_hardened_run`, `…single_added_capability`, `…no_secret_material…`, `…env_is_git_ignored…` | **met** |
| F16 | Info | systemd unit | Hardening directives present and coherent (minimal caps, `ProtectSystem=strict`, `PrivateTmp`, `SystemCallFilter=@system-service`, `RestrictAddressFamilies`, `MemoryDenyWriteExecute`, `RestrictNamespaces`, `LockPersonality`); `ReadWritePaths` the DB dir only | hardened `deploy/netcorenoc.service` | `test_deploy.py::test_systemd_unit_is_hardened` | **met** |
| F17 | Info | `security.txt` / disclosure policy | RFC 9116-valid; `Expires` future-dated; contact routes to a private channel; issue templates route security reports away from public issues; the policy makes no unfounded promises (no bounty) | `.well-known/security.txt` served under CSP/security headers; restructured `SECURITY.md` (disclosure-first); `config.yml` routes to private advisories | `test_security_txt.py` (parse, `Expires`-in-future, served-route headers, public access) | **met** |
| F18 | Info | Packaging integrity (the `src/` move) | The move must not start shipping tests/DB/secrets in the wheel/sdist, and must not stop shipping the UI/migrations/d3-checksum; licence/`NOTICE`/third-party obligations met | `src/` layout + package-data globs (`ui/.well-known/*` added) + `.dockerignore`/`MANIFEST.in`; `NOTICE` + `ui/vendor/d3.LICENSE` | `test_deploy.py` (MANIFEST/dockerignore/NOTICE), `test_supply_chain.py` (F12 globs, d3 checksum, licence-beside-asset), + built-wheel/sdist gate verification | **met** |
| F19 | Info | Committed workflows (even dormant) | Every GitHub Action pinned by commit SHA (a lint asserts no floating tag); a least-privilege `permissions:` block; no secret referenced or printed; dormant publish/sign steps cannot run by accident | SHA-pinned `ci.yml`/`release.yml`; `permissions:` blocks; publish steps commented/opt-in | `test_workflows.py` (SHA-pin, permissions, no-active-publishing) | **met** |
| — | — | New runtime attack surface | `/.well-known/security.txt` is the only new served path — static, public, under the existing CSP/security-headers middleware, adds no dynamic code | static allowlist entry (`STATIC_ASSETS`), not a dynamic route | `test_security_txt.py::test_security_txt_is_served_publicly_with_security_headers` | **met** |

Note on DB-volume ownership (F15): the image creates `/home/netcorenoc` owned by the non-root
user (uid 10001) via `useradd --create-home`; Docker initializes the fresh named volume from that
path, so the mounted DB directory is owned by the container user and is writable under the
read-only rootfs. This is a runtime property to confirm on the maintainer's `docker compose up`
(the daemon is unavailable in the build sandbox); the compose file encodes it correctly.

## 3. Critical analysis (prose) — residual risk

An honest assessment of where these choices could bite:

- **Single-node compose is not HA.** `docker compose up` brings up one process over one SQLite
  file — deliberately, matching the runtime identity. It is not a highly-available deployment and
  does not claim to be; scale-out (PostgreSQL/NATS) remains a ROADMAP line.
- **`AmbientCapabilities=CAP_NET_BIND_SERVICE` (or the compose `cap_add`) for UDP 162 is a
  deliberate trade.** Binding the privileged trap port as non-root needs exactly this one
  capability; the documented alternative is mapping a high port. The trade is stated, not hidden.
- **Extending the `OPTICORR_*` window (DECISIONS #39) prolongs a legacy code path** by one
  version. It is a compatibility path, not a vulnerability, and its removal is scheduled for
  v0.6.0.
- **Dormant workflows** ship SHA-pinned and least-privilege, but they are still committed code; the
  SHA-pin lint and the "cannot publish by accident" property are what keep a dormant artifact from
  becoming a live risk if the maintainer enables CI.

Next-version follow-ups are `docs/ROADMAP.md` lines, not scope creep.

Next-version follow-ups are `docs/ROADMAP.md` lines, not scope creep: the digest-pin of the base
image remains a deploy-time step; multi-node lockout/rate-limit state, `/metrics`, and SNMPv3 are
roadmap; the v0.6.0 configurability surfaces get their own threat-model entries when built (see
`docs/architecture/EXTENSIBILITY-0.6-DRAFT.md`).

## 4. Threat-model delta

`docs/security/threat-model.md` gains a v0.5.0 note for the one new served path: the static
`.well-known/security.txt` is mapped to a control (served under the existing CSP/security-headers
middleware; static, public, additive to `STATIC_ASSETS`; no dynamic code) and a check
(`test_security_txt.py` served-route header + public-access tests). No other threat changes — the
receiver, engine, store, API, auth, audit, and UI surfaces are byte-for-byte v0.4.0.

## 5. Tool baselines (inputs, re-run each gate)

`bandit`, `pip-audit`, `ruff`, `vulture` re-run and recorded per gate in `docs/gates/v0.5-phase-*`
(plus the new documentation link-check and the GitHub-Actions-SHA-pin lint). Green on the
reconciled v0.4.0 baseline (phase 0) and through every phase: 320+ tests, `mypy --strict` clean,
`make eval` byte-identical, no known dependency vulnerabilities.
