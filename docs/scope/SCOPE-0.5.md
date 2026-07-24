# SCOPE — NetCoreNOC v0.5.0

**Theme: structure, organization, and growth readiness — no engine change.** v0.5.0 is a
deliberately small, gradual release. It makes the project **legible** (a newcomer can clone it
and understand it from a documentation index), **installable** (one command, no external
services), and **contributable** (a stranger can use it, contribute to it, and report a
vulnerability responsibly), and it **prepares the ground for v0.6.0 without building any of
it**. Nothing in the running correlator's behaviour changes — not ingestion, not learning, not
the API contract, not the schema, not the UI behaviour, not the eval outputs.

The zero-config runtime identity is unchanged: one Python 3.12 asyncio process, one SQLite
(WAL) file, one static UI, environment variables only, no UI build step, **zero new runtime
dependencies**. All four prior scope documents (`SCOPE.md`, `SCOPE-0.2.md`, `SCOPE-0.3.md`,
`SCOPE-0.4.md`) and their invariants still hold; the `docs/threat-model.md` keeps the authority
it has held since v0.2.0. This document states only what v0.5.0 adds or changes.

**Delivery model.** The repository is read-only to automation: the maintainer takes the
resulting archive and pushes it by hand. Therefore no step depends on pushing, on CI running,
or on any external account, registry, or dashboard action. Every gate is local and reproducible
on the maintainer's machine (`make qa`, `make eval`, `docker compose config`, a locally built
wheel). Any CI or release workflow committed here is a ready-to-use artifact that activates only
if and when the maintainer chooses — never a prerequisite for this release to be complete.

## In scope — exactly five workstreams, and nothing else

### 1. Open-source community scaffolding
The standard files that let a stranger understand, use, contribute to, and responsibly report
problems with the project — real and specific, never generic boilerplate. `CONTRIBUTING.md`
(dev setup, the `make qa` quality bar, Conventional Commits, branch/PR flow, the "a scored-path
change ships its `make eval` delta" rule); `CODE_OF_CONDUCT.md` (Contributor Covenant v2.1);
a restructured `SECURITY.md` where a genuine **coordinated vulnerability disclosure policy** is
what a reporter finds first (private channel, response-time commitment, embargo expectations,
in/out-of-scope statement, safe-harbour clause, no bug-bounty claim), with the operator
hardening guide moved under `docs/security/`; a `.well-known/security.txt` valid per RFC 9116
(future-dated `Expires`), committed and **served by the app** from a fixed static route under
the existing CSP/security-headers middleware; GitHub issue/PR templates that route security
reports to the disclosure policy, not to public issues; third-party licence compliance
(`ui/vendor/d3.LICENSE` beside the vendored asset, a top-level `NOTICE`); `.editorconfig` and
README badges.

### 2. Self-contained deployment (one command, no external services)
"Start using it" becomes `docker compose up`, wrapping the existing single process without
changing it. A hardened `docker-compose.yml` (read-only rootfs, `cap_drop: [ALL]`,
`no-new-privileges`, `tmpfs /tmp`, a named DB volume, a `/healthz` healthcheck,
`restart: unless-stopped`) with a committed `.env.example` (never a real `.env`, which is
git-ignored) and a commented TLS/allowlist profile. A hardened example systemd unit
`deploy/netcorenoc.service`. `.dockerignore`/`MANIFEST.in` so build artifacts, the DB, and
secrets never enter the image or sdist while everything that must ship does. `make dist`
(local wheel + sdist + optional image) and `make release-check` (version agreement across
`pyproject.toml`, `netcorenoc/__init__.py`, and the CHANGELOG heading). Optional, dormant,
opt-in committed workflows: a SHA-pinned least-privilege `release.yml` (built-in token only;
any publish/sign step commented and opt-in) and `dependabot.yml`.

### 3. Repository and documentation restructuring for a growing project
Adopt the PyPA-recommended `src/` layout (`git mv netcorenoc → src/netcorenoc`, history
preserved; every path reference updated; import path unchanged), which also guarantees tests
run against the installed package — the standing guard against the F12 class of bug. Turn the
flat `docs/` into a navigable tree with an index (`architecture/`, `adr/`, `security/`,
`scope/`, `releases/`, `gates/`) and a newcomer `repo-map.md`. Keep the package flat inside
`src/netcorenoc/` unless a shallow one-level grouping demonstrably lowers cognitive load
(optional, P2). Structure-guard and link-check tests prove the reorg moved locations, never
behaviour.

### 4. Security review and critical analysis of (1)–(3)
A dedicated review in the project's established style (`docs/security/SECURITY-REVIEW-0.5.md`),
continuing the finding series from **F15**, each with a fix, a regression/assertion test, and a
mapping row: container/compose hardening reproduced, no secret in image or compose, `.env`
git-ignored; systemd hardening coherent; `security.txt`/disclosure policy RFC-valid and routing
to a private channel; packaging integrity (the `src/` move ships no tests/DB/secrets and still
ships the UI/migrations/d3-checksum; licence/`NOTICE` obligations met); committed workflows
SHA-pinned, least-privilege, and unable to publish by accident; no new runtime attack surface
beyond the static `security.txt`; and an honest critical-analysis prose section on residual
risk. The threat model gains an entry for the static `security.txt` if warranted.

### 5. Terrain-preparation for v0.6.0 — specification only, no implementation
`docs/architecture/EXTENSIBILITY-0.6-DRAFT.md`: a specification (following the proven
"spec-now-implement-later" pattern of `CASE-SCHEMA-DRAFT.md`) that names the three already-clean
surfaces — the RBAC map (`rbac.py`), the visibility serializer (`shaping.py`), and the scoring
parameters (`correlate.py`) — and specifies, with a hard security framing and every element
marked `v0.6.0: planned`: admin-configurable RBAC (within the fixed role ceiling, sessions
re-evaluate, every change audited); per-role/per-principal visibility scoping (fail-closed,
audited, 404-not-403 past authorization); and a configurable/pluggable match formula (built-in
default always the safe fallback; bounded/validated parameters off the hot path; an external
criterion API as the strictest-controlled, opt-in, allowlisted, hard-timeout, fail-safe
surface). **v0.5.0 implements none of it.**

## Explicitly out of scope (deferred, in this order)

No engine or feature work ships here. Each item is a `docs/ROADMAP.md` line:

1. **The v0.6.0 configurability features themselves** — admin-defined per-role capabilities,
   per-role/per-principal visibility scoping, and a configurable/pluggable match formula
   (possibly with an external criterion API). v0.5.0 writes their specification (§8) and
   confirms the surfaces are clean; it implements none.
2. **SNMPv3** (opt-in, minimal-config).
3. **Self-observability** — a Prometheus `/metrics` endpoint.
4. **pcap replay** in the trap tooling; **outbound webhook / `Case` JSON emission**.
5. **Typed relations, device-archetype clustering, situation subsumption, impact scope,
   pattern recurrence**; the `device_id → entity_id/ne_id` cutover and removal of
   `learn.device_affinity` (DECISIONS #35/#36).
6. **Removing the legacy `OPTICORR_*` env aliases** — the deprecation window is extended by one
   version to **v0.6.0** (DECISIONS #39). This is the only behaviour-adjacent decision in the
   release, and it is a *non-removal*: the aliases and their once-per-variable warning stay.
7. **Publishing to PyPI / a container registry / artifact signing.** Committed workflows may
   contain these as opt-in, commented, ready-to-enable steps, but they are deferred as an active
   concern to a later version and require no setup now.

## Hard constraints (unchanged; violating any is a build failure)

No engine, schema, API, or UI-behaviour change — packaging, docs, process, and a specification
only. Zero new runtime dependencies (dev/CI tooling only, justified in `docs/DECISIONS.md`). Do
not over-split the package: prefer the flat module set inside `src/netcorenoc/`; a shallow
grouping is optional and must earn itself; no frameworks, plugin systems, or dynamic loading
introduced by the reorg. One process, one SQLite file, one static UI, env vars only, no UI build
step. No external-account or push dependency for the release to be complete. No feature outside
the five workstreams — everything else is a ROADMAP line, and v0.6.0 is specified, never built.
Preserve git history on every move (`git mv`); never renumber existing ADR/decision entries.
