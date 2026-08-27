# Contributing to NetCoreNOC

NetCoreNOC is a **zero-configuration SNMP trap correlator** with a deliberately small surface: one
Python 3.12 asyncio process, one SQLite (WAL) file, one static web console, **no build step and no
runtime dependencies beyond five libraries**. Contributions that keep it that small and legible are
the easiest to accept.

New here? Read [`docs/architecture.md`](docs/architecture.md) and
[`docs/README.md`](docs/README.md).

## Development setup

Python **3.12+** is required.

```sh
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
make qa        # lint + typecheck + dead code + bandit + tests(+coverage) + eval non-regression
make security  # adds pip-audit (queries PyPI, so it is not in `qa`)
```

Run it locally on an unprivileged trap port:

```sh
NETCORENOC_TRAP_PORT=1162 .venv/bin/python -m netcorenoc.main
# the one-time bootstrap admin password prints to the console on first start
```

## The quality bar

`make qa` is `lint typecheck deadcode scan test eval`. Concretely, a change must keep all of these
green:

- **`ruff check` + `ruff format --check`** — line length 100. **`ruff format` also formats Python
  inside fenced `python` blocks in Markdown**, so a code example in the documentation is subject to
  it. This has failed CI before.
- **`mypy --strict`** — no type errors, no untyped defs.
- **`pytest` with coverage ≥ 85 %** — and it must not fall below the current figure minus 3 points.
- **`make eval`** — the offline harness replays the labelled corpus and fails on any regression in
  `pairwise_f1`, `ari` or `entity_accuracy` against the frozen baseline.
- **`bandit`**, and **`pip-audit`** under `make security`.
- **`vulture`** over the package with a committed allowlist.
- **`make checksums`** — the vendored console asset against its pinned SHA-256.
- **`make linkcheck`** — the `src/` layout guard and the documentation link checker.

CI runs the same targets. Everything is reproducible locally; you never need CI to know whether your
change passes. **Run the target, not the commands inside it** — a release once ran four of the five
by hand and CI caught the fifth.

### If you touch a scored path, attach the `make eval` delta

Any change to `receiver.py`, `correlate.py`, `learn.py`, `rootcause.py`, `severity.py`,
`varbind_profile.py`, `store/alarms.py`, `store/situations.py` or `engine.py` can move the metrics.
**Run `make eval` and paste the delta in your PR.** A change intending no behavioural change must
show a byte-identical delta; a change intending to improve a metric must show it and explain why it
is correct rather than metric-gaming.

## Hard constraints (a PR that violates one will not merge)

- **Zero new runtime dependencies.** The shipped app imports `pysnmp`, `aiosqlite`, `fastapi`,
  `uvicorn`, `pydantic`, and nothing else. New dev tooling goes in the `dev` extra with a decision
  entry justifying it. This covers documentation tooling too: no `mkdocs`, no `sphinx`.
- **Same runtime identity.** One process, one SQLite file, one static console, environment variables
  only, no UI build step, no npm.
- **Ingestion is sacred.** `receiver.datagram_received` gains no lock, no I/O, no `await`.
  `engine.py` holds the batch lock and every decision that reasons about it, deliberately and
  permanently — do not "tidy" it into modules. The invariant is only auditable if that path reads
  without following imports.
- **Imports go downward or sideways, never up.** `http` → `engine` → `data` → `ingest`, plus
  cross-cutting from anywhere. `tests/test_layers.py` enforces it and its exemption list is
  **empty**.
- **One `Store`, one connection, one `store.lock`**, taken by *callers* and never inside a `Store`
  method. `tests/test_store_concurrency.py` is the control.
- **Bounded memory everywhere.** Every accumulator keeps its cap and eviction, with a test.
- **UI security discipline.** Strict CSP; new DOM values go through `textContent`/`esc()`, never
  `innerHTML`; no inline script or style, no CDN.
- **Modules stay small and shallow.** One noun or one decision; over ~250 lines is a smell and over
  **400 fails CI**, with a shrink-only `DEBT_ALLOWLIST` (currently empty) and a separate
  `COHESION_EXEMPT` whose only entry is `engine.py`. One level of package nesting where earned,
  never two.
- **A new route declares itself, or the process does not start.** Add its capability to
  `rbac.ROUTE_PERMISSIONS` **and** its visibility posture to `rbac.ROUTE_SCOPE`, then register it
  through `DeclaredRoutes`. If you are changing the HTTP security boundary, read
  `src/netcorenoc/api/perimeter.py` — all of it, and nothing else.
- **If you change the console, open a browser.** The DOM harness executes the real module graph, and
  it still cannot see whitespace and cannot see emptiness: v0.13.0 shipped six visual defects with
  1428 tests green. A green suite is not evidence that the browser does what you intended.

## What a release writes, and what it does not

**A release writes no gate document, no scope document, no build report and no security review.**
That is decision #197, and it is the convention that replaced 62 000 lines of documentation with
under 5 000.

| It goes here | Not here |
|---|---|
| A finding → [`docs/findings.md`](docs/findings.md), **five lines**: what, reproduction command, measured output, disposition | a per-release security review |
| A decision → [`docs/adr/DECISIONS.md`](docs/adr/DECISIONS.md), **six lines**: decision, reason, release | a phase-gate document |
| A specification for an unbuilt release → [`docs/plans/`](docs/plans/), as **measurements and open questions** | a scope document |
| Anything else → a commit message and a `CHANGELOG` line | a build report |

**Working notes during a build are scratch files outside the repository.**

The decision log is **append-only in the sense that matters**: an entry is never renumbered, and a
superseded decision is superseded by a new entry rather than rewritten. An entry that no code cites
may be removed (#201); one that is cited may be condensed but keeps its number, because 129
docstrings in `src/` and `tests/` name these numbers.

## The one irreversible act

> **The only irreversible act in this repository is a force-push or a history rewrite of `main`.**

Everything else is recoverable. A deleted file is at the commit that held it; a bad decision is
superseded by the next one; a wrong tag is moved. That is why deleting documentation is cheap, and
why this project stopped preserving what git already preserves — see
[`docs/record.md`](docs/record.md).

**Never** `git push --force` to `main`, rebase it, amend a published commit, or retag an existing
tag onto a different commit.

### Tags

Tags are a convenience for finding a release. Nothing gates on one. If a tag is missing from the
remote:

```sh
git tag -a v0.14.0 <sha> -m "v0.14.0 — the model family"    # only if it does not exist
git push origin v0.14.0
git ls-remote --tags origin                                  # verify
```

## Commit and PR conventions

- **Conventional Commits**, one logical change per commit: `fix(receiver): drop oversized varbind
  before quarantine`, `docs(adr): record decision #207`. Types in use: `feat`, `fix`, `docs`,
  `refactor`, `test`, `chore`, `build`, `ci`.
- **Branch off the default branch**, keep the branch focused, open a pull request, and fill in
  `.github/PULL_REQUEST_TEMPLATE.md`.
- **Record ambiguity calls.** If your change resolves a genuine design ambiguity, add a numbered
  entry to [`docs/adr/DECISIONS.md`](docs/adr/DECISIONS.md) — context, choice, reason, in about six
  lines. Never renumber an existing entry.

## Trap simulator and replay

With the app running on port 1162:

```sh
make replay                      # the bundled fibre-cut scenario as real SNMP PDUs over UDP
make sim SCENARIO=login_burst    # a declarative scenario; python tools/trap_sim.py --list
make loadtest                    # 1000 traps/s for 60 s
make burst                       # 100 000 traps in one second
```

The simulator and corpus tooling live under `eval/` and `tools/` and are **never imported by the
runtime package**.

## Reporting a security vulnerability

Do **not** open a public issue. Follow the coordinated disclosure policy in
[`SECURITY.md`](SECURITY.md).
