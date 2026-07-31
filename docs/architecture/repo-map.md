# Repository map — a one-screen tour

Where everything lives, for someone who just cloned the repo. NetCoreNOC is **one process, one
SQLite file, one static UI**; the tree is small on purpose.

```
NetCoreNOC/
├── src/netcorenoc/          the import package (PyPA src/ layout; import path stays `netcorenoc`)
│   ├── __init__.py          version string
│   ├── __main__.py          `python -m netcorenoc audit verify|export` (audit CLI)
│   ├── main.py              `python -m netcorenoc.main` — main(), the __main__ guard, re-exports
│   ├── runner.py            run(): open the store, start receiver + engine + HTTP; Supervisor
│   ├── engine.py            Engine: queue→batch→store under ONE lock, plus FlapDetector.
│   │                        **The ingest path, deliberately in one file** — "ingestion is sacred"
│   │                        is only auditable if it can be read without following imports.
│   ├── maintenance.py       periodic, off the ingest path: promotion sweep, severity, profiler
│   ├── gaps.py              GapTracker: drop counters → durable ingest_gap rows
│   ├── scorer_lifecycle.py  the v0.6.0 seam's lifecycle: load, fail safe, warn, audit
│   ├── settings.py          Settings from the environment; the removed-alias startup errors
│   ├── receiver.py          UDP trap listener; parse (v2c + v1/RFC 3584); allowlist; quarantine
│   ├── events.py            TrapEvent / Varbind / Fingerprint / QuarantinedPacket models
│   ├── correlate.py         the sliding window: candidate selection, verdict, bookkeeping
│   ├── scoring.py           THE scoring seam: LinkScorer contract + the default AdditiveScorer
│   ├── preview.py           read-only scorer what-if (bounded re-partition of recent alarms)
│   ├── learn.py             learned affinities A/E (npmi, forgetting, storm damping), clears
│   ├── rootcause.py         precedence learning; situation root pick
│   ├── severity.py          learned severity field (shape + ordinality), honest unknown
│   ├── varbind_profile.py   the entity/identity profiler (R/X/D score, FD containment)
│   ├── store/               the SQLite layer (v0.7.3). **One Store, ONE connection, ONE lock** —
│   │                        the whole write discipline rests on it (F39). Sixteen domain modules.
│   │   ├── base.py          StoreBase: the ten attribute annotations + the `conn` accessor
│   │   ├── types.py         IngestResult, EdgeRow, FeedbackResult, MIGRATIONS_DIR, the constants
│   │   ├── lifecycle.py     open/close/commit/rollback, migrations, integrity checks
│   │   └── *.py             one mixin per domain: devices, alarms, learned, situations,
│   │                        feedback, read_models, entities, state_clears, ingest_gaps,
│   │                        scoring_config, governance, auth, audit_log, retention
│   ├── api/                 the HTTP layer (v0.7.2). **Read api/perimeter.py first if you are
│   │                        reviewing security** — it is the whole boundary in one file.
│   │   ├── perimeter.py     CSRF → identity → bootstrap gate → RBAC → rate limit; audit row,
│   │   │                    write transaction, scope resolution, security-headers middleware
│   │   ├── declare.py       the registration gate: an undeclared route cannot be registered
│   │   ├── app.py           create_app: build the perimeter, build the context, register
│   │   ├── context.py       AppContext — what every route module receives
│   │   ├── models.py        every pydantic request model, in one file
│   │   ├── governance_cache.py  the per-request cache of the two stored policies
│   │   └── routes_*.py      nine route groups, each one register(app, ctx) function
│   ├── auth.py              scrypt passwords, sessions, service tokens, login throttle
│   ├── audit.py             append-only hash-chained audit log + verify/export
│   ├── rbac.py              THE authorization map + the ceiling∩policy resolver
│   ├── shaping.py           response serializer: role→fields, and NE scope→rows
│   ├── runtime.py           in-memory runtime config (allowlist, retention)
│   ├── logsetup.py          logging + secret-redaction filter
│   ├── known_oids.py        tiny public-standard OID table (no vendor MIB semantics)
│   ├── migrations/*.sql     forward-only schema migrations (applied at startup)
│   ├── ui/                  the static UI: index.html, app.js, style.css (no build step)
│   │   └── vendor/          locally vendored d3 + CHECKSUMS.txt (strict CSP, no CDN)
│   └── py.typed             PEP 561 marker
├── tests/                   pytest suite (unit, integration, abuse, structure/link guards)
├── eval/                    deterministic offline evaluation harness + labelled corpus + baseline
├── tools/                   trap_replay.py, trap_sim.py (operator/test tooling; never imported by the package)
├── docs/                    documentation tree — see docs/README.md for the index
├── deploy/                  deployment examples (systemd unit)   [added v0.5.0]
├── Dockerfile               hardened multi-stage image (non-root, read-only rootfs friendly)
├── docker-compose.yml       one-command self-contained run       [added v0.5.0]
├── Makefile                 qa / eval / dist / run targets
├── pyproject.toml           packaging, tool config (ruff/mypy/coverage/bandit/pytest)
├── flake.nix                Nix build + dev shell
├── README.md · SECURITY.md · CONTRIBUTING.md · CODE_OF_CONDUCT.md · CHANGELOG.md · MIGRATION.md · NOTICE · LICENSE
```

## The one flow to understand first

```
UDP 162 ─▶ receiver.datagram_received ─▶ asyncio.Queue ─▶ Engine.run (batches) ─▶ store (one txn/batch)
           (parse, allowlist, quarantine;                  per alarm: dedup → learn → correlate →
            NO lock, NO I/O, NO await — "ingestion is       assign situation → pick root)
            sacred", invariant 2)
```

The HTTP side is the security perimeter, and since v0.7.2 it is **one file you can read end to
end**: `api/perimeter.py` — security headers → CSRF → identity → bootstrap gate → RBAC
(`rbac.py`, the single source) → rate limit → handler, plus the audit-row helper, the write
transaction boundary and scope resolution. Response bodies are shaped by role (`shaping.py`) and
every mutating action / sensitive read is written to the hash-chained audit log (`audit.py`). The
route modules receive the perimeter's bound helpers; none of them implements one.

## Where the configurability surfaces live

- **Scoring — built in v0.6.0.** `scoring.py` is the `LinkScorer` contract and the default
  `AdditiveScorer` (the five parameters live there, not in `correlate.py`); `preview.py` is the
  read-only what-if; the stored configuration is `scorer_config` + the one-row `scorer_active`
  pointer (migration `0005`). See [`DESIGN.md`](DESIGN.md) § "v0.6.0 — the scoring seam".
- **The HTTP layer — restructured in v0.7.2.** `api.py` became the package `api/`: the security
  boundary is `api/perimeter.py`, the registration gate is `api/declare.py`, and nine `routes_*`
  modules hold the handlers. **No behaviour changed** — every handler body is byte-identical and
  the route table is identical in order. See [`DESIGN.md`](DESIGN.md) § "v0.7.2 — the perimeter as
  a named component" and [`MODULE-ARCHITECTURE.md`](MODULE-ARCHITECTURE.md).
- **Governance — built in v0.7.0.** `rbac.py` holds the compiled `PERMISSIONS` **ceiling** and the
  one capability resolver (`resolve_capabilities`, an intersection, so escalation is structurally
  impossible); `shaping.py` gained a second axis beside field shaping — `visible_nes` and the scope
  projections, deciding *which rows* a principal sees rather than *which fields*. The stored policy
  is `governance_policy` (append-only) + the per-kind `governance_active` pointer (migration
  `0006`). See [`DESIGN.md`](DESIGN.md) § "v0.7.0 — governance" and
  [`GOVERNANCE-0.7-DRAFT.md`](GOVERNANCE-0.7-DRAFT.md). **Visibility scoping is a presentation
  control and is not tenant isolation.**
- **Customer-supplied models — v0.13.0, specified not built.** They plug into the v0.6.0
  contract; see [`SCORER-PLUGINS-0.13-DRAFT.md`](SCORER-PLUGINS-0.13-DRAFT.md). Resequenced from
  v0.8.0 by DECISIONS #93 — the chain from **v0.8.0 (the operator-feedback dataset)** to v0.13.0 is
  [`ROADMAP-0.8-TO-0.13.md`](ROADMAP-0.8-TO-0.13.md).

The original three-surface specification, [`EXTENSIBILITY-0.6-DRAFT.md`](EXTENSIBILITY-0.6-DRAFT.md),
is superseded in place with a disposition table at the top.

## Conventions worth knowing before you edit

- **Zero new runtime dependencies.** The shipped app imports only `pysnmp`, `aiosqlite`,
  `fastapi`, `uvicorn`, `pydantic`. Dev/CI tooling lives in the `dev` extra.
- **Modules stay small and shallow.** A module owns one noun or one decision; over ~250 lines is a
  smell and over **400** fails CI, with an explicit shrink-only debt allowlist naming the release
  that will fix each current offender — and, since v0.7.3, a separate `COHESION_EXEMPT` for the one
  module that is large because an **invariant** forbids splitting it. The two are not
  interchangeable: debt carries an owner and a date, a cohesion exemption carries neither, because
  there is no fix. One level of nesting where it has been earned — today `api/` and `store/` — and
  never two. The rule and the layer map are in
  [`MODULE-ARCHITECTURE.md`](MODULE-ARCHITECTURE.md); the guards are `tests/test_architecture.py`.
- **Imports go downward or sideways, never up.** `http` → `engine` → `data` → `ingest`, plus
  cross-cutting from anywhere. Enforced since v0.7.3 by `tests/test_layers.py`, whose exemption list
  is empty. The `Engine` may not import `netcorenoc.api`; the process runner may, and that is what
  `runner.py` is for.
- **A new route declares itself or the process does not start.** `rbac.py` holds both the
  capability (`ROUTE_PERMISSIONS`) and the visibility posture (`ROUTE_SCOPE`); registration goes
  through `api/declare.py`, which refuses anything it has not been told about.
- **The UI is four files, no build step.** New DOM values go through `textContent`/`esc()` —
  never `innerHTML` — under the strict CSP.
- **A change to a scored path ships its `make eval` delta.** See `CONTRIBUTING.md` at the repo
  root (added in v0.5.0).
