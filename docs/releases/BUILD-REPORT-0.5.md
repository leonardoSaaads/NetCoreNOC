# Build Report — NetCoreNOC v0.5.0

**"Legible, installable, contributable."** An organization/structure release built as a strict
six-phase waterfall against `docs/scope/SCOPE-0.5.md` (product scope) and this repository's process
and quality bar, with `docs/security/threat-model.md` retaining its authority. Date: 2026-07-24.

The prime directive held: **nothing in the running correlator changed.** `make eval` is
byte-identical to v0.4.0 after every phase; the engine, schema, API, UI behaviour, ingestion, and
learning are untouched. This release makes the project legible (a documentation index and a
newcomer map), installable (`docker compose up`), and contributable (scaffolding + a disclosure
policy), and it specifies v0.6.0 without building any of it.

## What changed (and what did not)

**Changed — packaging, docs, process, and a specification only:**

- **`src/` layout** (`netcorenoc/` → `src/netcorenoc/`, via `git mv`). Import path unchanged;
  tests now run against the installed package (the standing guard against the F12 class of bug).
- **Documentation taxonomy** with an index, plus a newcomer `repo-map.md`.
- **Community scaffolding**: `CONTRIBUTING`, `CODE_OF_CONDUCT`, a disclosure-first `SECURITY.md`,
  issue/PR templates, `NOTICE` + `d3.LICENSE`, `.editorconfig`, badges, and an app-served RFC 9116
  `security.txt`.
- **Self-contained deployment**: `docker-compose.yml` + `.env.example`, a hardened systemd unit,
  `.dockerignore`/`MANIFEST.in`, `make dist`/`release-check`, and dormant SHA-pinned CI.
- **The v0.6.0 extensibility specification** (spec only).
- The legacy `OPTICORR_*` alias window extended to v0.6.0 (the only behaviour-adjacent change).

**Not changed:** the receiver, engine, correlator, learner, store, API, auth, sessions, audit,
RBAC, shaping, and UI code are byte-for-byte v0.4.0. Zero new **runtime** dependencies
(`pysnmp`, `aiosqlite`, `fastapi`, `uvicorn`, `pydantic`). One new **dev** dependency (`build`,
for `make dist`).

## Layout diff (v0.4.0 → v0.5.0)

```
netcorenoc/**                 ->  src/netcorenoc/**            (git mv; import path unchanged)
docs/DESIGN.md                ->  docs/architecture/DESIGN.md
docs/CASE-SCHEMA-DRAFT.md     ->  docs/architecture/CASE-SCHEMA-DRAFT.md
docs/DECISIONS.md             ->  docs/adr/DECISIONS.md
docs/threat-model.md          ->  docs/security/threat-model.md
docs/SECURITY-REVIEW-*.md     ->  docs/security/SECURITY-REVIEW-*.md
docs/SCOPE*.md                ->  docs/scope/SCOPE*.md
docs/BUILD-REPORT*.md         ->  docs/releases/BUILD-REPORT*.md
SECURITY.md (operator guide)  ->  SECURITY.md (disclosure policy) + docs/security/operations.md

new: docs/README.md · docs/architecture/repo-map.md · docs/adr/README.md
     docs/architecture/EXTENSIBILITY-0.6-DRAFT.md · docs/security/SECURITY-REVIEW-0.5.md
     CONTRIBUTING.md · CODE_OF_CONDUCT.md · NOTICE · .editorconfig
     docker-compose.yml · .env.example · deploy/netcorenoc.service · .dockerignore · MANIFEST.in
     .github/ISSUE_TEMPLATE/* · .github/PULL_REQUEST_TEMPLATE.md · .github/workflows/release.yml
     .github/dependabot.yml · tools/release_check.py
     src/netcorenoc/ui/.well-known/security.txt · src/netcorenoc/ui/vendor/d3.LICENSE
     tests/test_structure.py · test_security_txt.py · test_workflows.py · test_deploy.py

removed: opticorr/ (stale v0.3.0 duplicate not in the authoritative v0.4.0 archive; Phase 0)
```

## Baseline reconciliation (Phase 0)

The GitHub checkout had drifted from the authoritative `NetCoreNOC0.4.0` archive: it was missing
`.github/workflows/ci.yml` and `.gitignore` and carried a stale `opticorr/` v0.3.0 duplicate
(old name, no `shaping.py`/`CHECKSUMS.txt`, referenced nowhere). Both missing files were restored
byte-identical and `opticorr/` was removed, bringing the tree to exact parity with the archive
before any v0.5.0 work. See `docs/gates/v0.5-phase-0.md`.

## Security review

`docs/security/SECURITY-REVIEW-0.5.md` continues the finding series with **F15–F19** — all
hardening/assurance findings (this release adds no runtime code path), each closed with a fix and
a passing assertion test: compose hardening (F15), systemd hardening (F16), RFC 9116
`security.txt` + disclosure policy (F17), packaging integrity (F18), and SHA-pinned least-privilege
workflows that cannot publish by accident (F19). The one new served path
(`/.well-known/security.txt`) is static, public, and under the existing CSP/security-headers
middleware; the threat model gained a v0.5.0 note for it. No exploitable weakness was found.

## v0.6.0 groundwork

`docs/architecture/EXTENSIBILITY-0.6-DRAFT.md` names the three already-clean surfaces — the RBAC
map (`rbac.py`), the visibility serializer (`shaping.py`), and the scoring parameters
(`correlate.py`) — and specifies how each becomes configurable with its invariants preserved and
its new threat-model entries: admin RBAC within a fixed role ceiling; per-role/per-principal
visibility scoping that fails closed and returns 404 (not 403) for out-of-scope resources; and a
configurable/pluggable match formula whose built-in default is the always-available safe fallback,
whose parameters are validated and read off the ingestion hot path, and whose optional external
criterion API is opt-in, allowlisted, hard-timeout, bounded, fail-safe, off the datagram path, and
fully audited (flagged as the riskiest roadmap idea). **Implemented: none.**

## Decisions (#39–#42)

- **#39** — extend the legacy `OPTICORR_*` env-alias deprecation window to v0.6.0 (a non-removal;
  the only behaviour-adjacent decision).
- **#40** — reorg mechanics: `src/` layout, docs taxonomy, and the isort classification that kept
  churn to import grouping while keeping shipped package files pristine; stdlib-only guard tests.
- **#41** — community scaffolding: one served copy of `security.txt`, private-reporting contacts,
  and the `.well-known/` exemption to the four-files UI rule (a stronger assertion).
- **#42** — deployment: the compose privileged-port trade, sdist pruning (`MANIFEST.in`),
  dormant SHA-pinned CI, and the `build` dev-dependency.

## Deferred (ROADMAP lines, not built)

The v0.6.0 configurability features themselves; SNMPv3; a Prometheus `/metrics` endpoint; pcap
replay and outbound webhook / `Case` JSON emission; typed relations, device-archetype clustering,
situation subsumption, impact scope, pattern recurrence, and the `device_id → entity_id/ne_id`
cutover; removal of the `OPTICORR_*` aliases (v0.6.0); and publishing to PyPI / a registry /
artifact signing (dormant, opt-in workflow steps only).

## Honest caveats

- **Single-node compose is not HA.** `docker compose up` is one process over one SQLite file, by
  design; scale-out (PostgreSQL/NATS) is a roadmap line.
- **`CAP_NET_BIND_SERVICE` for UDP 162 is a deliberate trade** (drop-all-then-add-one); the
  high-port alternative is documented.
- **Deployment hardening is asserted, not kernel-enforced here.** The Docker daemon was
  unavailable in the build sandbox, so the image build and `docker compose up` are the
  maintainer's reproducible steps; `docker compose config` validates and the wheel that the image
  `pip install`s was verified to ship the full UI. This matches the delivery model (the maintainer
  ships the archive by hand; no gate depends on a pipeline or account).
- **Extending the `OPTICORR_*` window prolongs a legacy code path** by one version — compatibility,
  not a vulnerability.

## Gates

| Phase | Gate | Result |
|---|---|---|
| 0 | comprehension + baseline | reconciled to the archive; baseline green |
| 1 | scope, ADR #39, v0.6.0 draft | complete + consistent |
| 2 | src/ layout + docs taxonomy | 307 tests, eval byte-identical, wheel ships UI (F12/F13) |
| 3 | community scaffolding | 313 tests, `security.txt` valid + served with headers |
| 4 | self-contained deployment | `compose config` valid, `make dist`/`release-check`, 320 tests |
| 5 | security review + release | F15–F19 tests green, SHA-pin lint green, upgrade verified |

Final: **`make qa` green** (ruff, `mypy --strict`, `pytest` + coverage, `make eval`
non-regression, dead-code, d3 checksum, link check, SHA-pin lint), **`make security` clean**
(bandit, pip-audit), coverage ≥ 85 %.
