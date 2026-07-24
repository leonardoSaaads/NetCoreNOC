# Contributing to NetCoreNOC

Thanks for your interest. NetCoreNOC is a **zero-configuration SNMP trap correlator** with a
deliberately small surface: one Python 3.12 asyncio process, one SQLite (WAL) file, one static
web UI, **no build step and no runtime dependencies beyond five libraries**. Contributions that
keep it that small and legible are the easiest to accept.

New here? Read the [repository map](docs/architecture/repo-map.md) and the
[documentation index](docs/README.md) first.

## Development setup

Python **3.12+** is required.

```sh
python3.12 -m venv .venv
.venv/bin/pip install -e .[dev]
```

Run the whole quality bar:

```sh
make qa        # lint + typecheck + dead-code + tests(+coverage) + eval non-regression
make security  # bandit + pip-audit
```

Run the app locally (bind a high trap port so no privileges are needed):

```sh
NETCORENOC_TRAP_PORT=1162 .venv/bin/python -m netcorenoc.main
# the one-time bootstrap admin password prints to the console on first start
```

## The quality bar (every change must keep `make qa` green)

`make qa` is `lint typecheck deadcode test eval`; `make security` adds `bandit` and
`pip-audit`. Concretely, a change must keep all of these green:

- **`ruff check` + `ruff format --check`** — lint and formatting (line length 100).
- **`mypy --strict`** — no type errors, no untyped defs.
- **`pytest` with coverage ≥ 85%** — coverage must not fall below the current figure minus 3
  points.
- **`make eval` — non-regression.** The offline harness replays the labelled corpus and fails on
  any regression in the gated metrics (`pairwise_f1`, `ari`, `entity_accuracy`) against the
  frozen baseline.
- **`bandit` + `pip-audit`** — no new security findings, no vulnerable dependency.
- **Dead-code gate** — `vulture` over the package with a committed allowlist
  (`vulture_allowlist.py`).
- **Supply chain** — the vendored d3 SHA-256 in `src/netcorenoc/ui/vendor/CHECKSUMS.txt` must
  match (`make checksums`).
- **Structure + links** — `make linkcheck` (the `src/` layout guard and the documentation
  link checker) stays green.

CI (`.github/workflows/ci.yml`) runs the same targets, but everything is reproducible locally —
you never need CI to know whether your change passes.

### If you touch a scored path, attach the `make eval` delta

Any change to the ingestion/learning/correlation path (`receiver.py`, `correlate.py`,
`learn.py`, `rootcause.py`, `severity.py`, `varbind_profile.py`, `store.py` ingest, `main.py`
engine) can move the evaluation metrics. **Run `make eval` and paste the delta table in your
PR.** A change that intends no behavioural change must show a byte-identical delta; a change that
intends to improve a metric must show it and explain why it is correct, not metric-gaming.

## Hard constraints (a PR that violates one will not merge)

- **Zero new runtime dependencies.** The shipped app imports only `pysnmp`, `aiosqlite`,
  `fastapi`, `uvicorn`, `pydantic`. New dev/CI tooling goes in the `dev` extra (or a workflow)
  and is justified with a `docs/adr/DECISIONS.md` entry.
- **Same runtime identity.** One process, one SQLite file, one static UI, environment variables
  only, no UI build step, no npm.
- **Ingestion is sacred.** `receiver.datagram_received` gains no lock, no I/O, no `await`.
- **Bounded memory everywhere.** Every accumulator keeps its cap and eviction, with a test.
- **UI security discipline.** The UI is four files under a strict CSP; new DOM values go through
  `textContent`/`esc()`, never `innerHTML`; no inline script/style, no CDN.
- **Modules stay small** (≈300 lines) and flat inside `src/netcorenoc/`; no frameworks, plugin
  systems, or dynamic loading.

## Trap simulator and replay (test the engine end-to-end)

With the app running on port 1162:

```sh
make replay                 # replay the bundled fiber-cut fixture as real SNMP PDUs over UDP
make sim SCENARIO=login_burst   # a declarative DSL scenario; python tools/trap_sim.py --list
make loadtest               # 1000 traps/s for 60 s
```

The simulator and corpus tooling live under `eval/` and `tools/` and are **never imported by the
runtime package**.

## Commit and PR conventions

- **Conventional Commits.** One logical change per commit, e.g.
  `fix(receiver): drop oversized varbind before quarantine`,
  `docs(adr): record decision #40`. Types in use: `feat`, `fix`, `docs`, `refactor`, `test`,
  `chore`, `build`, `ci`.
- **Branch + PR.** Branch off the default branch, keep the branch focused, and open a pull
  request. Fill in `.github/PULL_REQUEST_TEMPLATE.md` — the checklist mirrors the quality bar
  (`make qa` green, tests added, docs/CHANGELOG updated, no new runtime dependency, `make eval`
  delta attached if a scored path changed).
- **Record ambiguity calls.** If your change resolves a genuine design ambiguity, add a numbered
  entry to `docs/adr/DECISIONS.md` (context → options → choice → reason). Never renumber existing
  entries.

## Where things live

- **Architecture & rationale:** [`docs/architecture/DESIGN.md`](docs/architecture/DESIGN.md).
- **Decisions:** [`docs/adr/DECISIONS.md`](docs/adr/DECISIONS.md) (format:
  [`docs/adr/README.md`](docs/adr/README.md)).
- **Scope per version:** [`docs/scope/`](docs/scope/).
- **Security:** [`docs/security/`](docs/security/) (threat model, reviews, operator guide); the
  vulnerability disclosure policy is [`SECURITY.md`](SECURITY.md).
- **Roadmap:** [`docs/ROADMAP.md`](docs/ROADMAP.md) — everything out of the current scope is one
  line here.

## Reporting a security vulnerability

Do **not** open a public issue. Follow the coordinated disclosure policy in
[`SECURITY.md`](SECURITY.md).
