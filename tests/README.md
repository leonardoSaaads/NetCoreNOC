# Tests

The suite is grouped by **what it protects**, and the groups follow the package under
`src/netcorenoc/`. A test module lives in exactly one area folder; nothing sits loose at the root
of `tests/`. `tests/repo/test_structure.py` enforces that, and fails if a new folder appears here
without a line in the table below.

## Layout

| Folder | What it protects | Main code under test |
|---|---|---|
| `ingest/` | Receiving traps: UDP listener, SNMP decoding, OID names, the built-in trap pack, throughput. | `ingest/` |
| `correlation/` | Grouping alarms into situations: features, episodes, root cause, scoring, clears, scenarios. | `engine/correlate/` |
| `lifecycle/` | A situation's states (New, Pending, Open, Resolved), transitions, occurrences, maintenance. | `engine/operate/`, `engine/mw/` |
| `model/` | The learned models: league, decider, autonomy, judge, shadow, promotion gate, bias, metrics. | `engine/model/`, `engine/evaluation/` |
| `dataset/` | What operator feedback is captured, and the boundary that keeps ground truth out of it. | `engine/dataset/`, `engine/report/` |
| `store/` | SQLite: queries, concurrency, migrations, upgrades from older schemas. | `store/`, `migrations/` |
| `api/` | The HTTP API: every route, its payloads, limits and errors. | `api/` |
| `security/` | Authentication, roles, audit, rate limiting, governance, supply chain. | `crosscutting/` |
| `ui/` | The console: DOM tests in Node, the build pins, icons, stylesheet, UI invariants. | `ui/` |
| `ops/` | Running the appliance: CLI, startup, Docker/compose, CI workflows, backup, operations. | `__main__.py`, `main.py`, `runner.py`, deploy files |
| `repo/` | The repository itself: architecture, layering, tree shape, docs, links, preregistrations. | whole tree |
| `evaluation/` | The offline gate (`make eval`), simulation, synthetic data, the trap replay tool. | `eval/`, `tools/` |
| `lab/` | The two-host lab (simulated network elements) and its guards. | `tests/lab/` |

Two folders hold no tests:

| Folder | Contents |
|---|---|
| `support/` | Shared helpers, importable by name from any test (`import util`, `import paths`, `from domdriver import dom_test`). `domharness/` is the Node DOM used by the `ui/` tests. |
| `fixtures/` | Frozen expected outputs (bias, agreement and shadow reports; the behaviour-identity record). A diff here is a behaviour change and must be reviewed as one. |

`conftest.py` (at the root of `tests/`) holds the fixtures every area shares: the `store` fixture,
the Hypothesis profile, the fast scrypt factor and the empty-league default.

## Running tests

From the repository root, after the development setup in
[`CONTRIBUTING.md`](../CONTRIBUTING.md#development-setup). Linux and macOS:

```sh
make test                                   # the whole suite, with coverage
.venv/bin/pytest tests/api                  # one area
.venv/bin/pytest tests/api/test_events.py   # one module
.venv/bin/pytest -k "flapping"              # by name, across areas
make dom                                    # only the DOM tests (needs Node >= 22)
```

Windows (PowerShell):

```powershell
.venv\Scripts\python -m pytest                       # the whole suite
.venv\Scripts\python -m pytest tests\api             # one area
.venv\Scripts\python -m pytest -m dom                # only the DOM tests (needs Node >= 22)
```

DOM tests **skip** when Node 22 or newer is not on `PATH`, and say so. A skipped DOM test is not a
passing one; CI runs them with Node installed.

## Adding a test

1. Put it in the area of the code it protects. If it spans two, choose the one whose failure it
   would explain best (an API test for a lifecycle rule goes in `lifecycle/` if the rule is the
   point, in `api/` if the route is).
2. Name the module `test_<subject>.py` and the function after the behaviour, as a sentence:
   `test_a_flapping_port_opens_one_situation`. Basenames are unique across all areas.
3. Find repository paths with `paths` (`paths.REPO_ROOT`, `paths.FIXTURES`, `paths.LAB`), never by
   counting `Path(__file__).parents`.
4. A helper needed by more than one module goes in `support/`; tests never import other test
   modules.
5. Code under `src/` may cite a test as `tests/<area>/test_x.py::test_y`. The citation is checked:
   renaming or moving a cited test fails `tests/repo/test_documentation.py` until the citation is
   updated.

## Pins that change on purpose

A few tests freeze bytes so that a change is a reviewed line rather than an accident:

| Pin | Where | Update when |
|---|---|---|
| `src/` tree digest | `repo/test_architecture.py` (`SRC_TREE_DIGEST`) | any file under `src/` changes |
| Console files | `ui/test_build_step.py` (`UI_HASHES`, `UI_SIZES`) | any file under `src/netcorenoc/ui/` changes |
| HTTP surface | `fixtures/behaviour-identity.txt` | a route, header or console file changes; regenerate with `python tests/support/behaviour_identity.py --write` |
| Offline gate | `eval/baselines/` (`make eval`) | correlation behaviour changes; see `eval/README.md` |
