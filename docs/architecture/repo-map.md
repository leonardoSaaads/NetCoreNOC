# Repository map — a one-screen tour

Where everything lives, for someone who just cloned the repo. NetCoreNOC is **one process, one
SQLite file, one static UI**; the tree is small on purpose.

```
NetCoreNOC/
├── src/netcorenoc/          the import package (PyPA src/ layout; import path stays `netcorenoc`)
│   ├── __init__.py          version string
│   ├── __main__.py          `python -m netcorenoc audit verify|export` (audit CLI)
│   ├── main.py              the process: Settings, Engine (queue→batch→store), Supervisor, run()
│   ├── receiver.py          UDP trap listener; parse (v2c + v1/RFC 3584); allowlist; quarantine
│   ├── events.py            TrapEvent / Varbind / Fingerprint / QuarantinedPacket models
│   ├── correlate.py         the sliding window: candidate selection, verdict, bookkeeping
│   ├── scoring.py           THE scoring seam: LinkScorer contract + the default AdditiveScorer
│   ├── preview.py           read-only scorer what-if (bounded re-partition of recent alarms)
│   ├── learn.py             learned affinities A/E (npmi, forgetting, storm damping), clears
│   ├── rootcause.py         precedence learning; situation root pick
│   ├── severity.py          learned severity field (shape + ordinality), honest unknown
│   ├── varbind_profile.py   the entity/identity profiler (R/X/D score, FD containment)
│   ├── store.py             the SQLite layer (one connection under an asyncio lock)
│   ├── api.py               FastAPI app: identity, RBAC, audit, SSE, security headers, static UI
│   ├── auth.py              scrypt passwords, sessions, service tokens, login throttle
│   ├── audit.py             append-only hash-chained audit log + verify/export
│   ├── rbac.py              THE authorization map (capability → role, route → capability)
│   ├── shaping.py           role-keyed response serializer (field coarsen/drop)
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

The HTTP side (`api.py`) is the security perimeter: security headers → CSRF → identity → RBAC
(`rbac.py`) → rate limit → handler, with response bodies shaped by role (`shaping.py`) and every
mutating action / sensitive read written to the hash-chained audit log (`audit.py`).

## Where the configurability surfaces live

- **Scoring — built in v0.6.0.** `scoring.py` is the `LinkScorer` contract and the default
  `AdditiveScorer` (the five parameters live there, not in `correlate.py`); `preview.py` is the
  read-only what-if; the stored configuration is `scorer_config` + the one-row `scorer_active`
  pointer (migration `0005`). See [`DESIGN.md`](DESIGN.md) § "v0.6.0 — the scoring seam".
- **RBAC and visibility scoping — v0.7.0, specified not built.** `rbac.py` (the permission map)
  and `shaping.py` (the visibility serializer); see
  [`GOVERNANCE-0.7-DRAFT.md`](GOVERNANCE-0.7-DRAFT.md).
- **Customer-supplied models — v0.8.0, specified not built.** They plug into the v0.6.0
  contract; see [`SCORER-PLUGINS-0.8-DRAFT.md`](SCORER-PLUGINS-0.8-DRAFT.md).

The original three-surface specification, [`EXTENSIBILITY-0.6-DRAFT.md`](EXTENSIBILITY-0.6-DRAFT.md),
is superseded in place with a disposition table at the top.

## Conventions worth knowing before you edit

- **Zero new runtime dependencies.** The shipped app imports only `pysnmp`, `aiosqlite`,
  `fastapi`, `uvicorn`, `pydantic`. Dev/CI tooling lives in the `dev` extra.
- **Modules stay small** (≈300 lines) and **flat** inside `src/netcorenoc/` — no deep
  subpackages, no plugin systems.
- **The UI is four files, no build step.** New DOM values go through `textContent`/`esc()` —
  never `innerHTML` — under the strict CSP.
- **A change to a scored path ships its `make eval` delta.** See `CONTRIBUTING.md` at the repo
  root (added in v0.5.0).
