# NetCoreNOC v0.28.1 — handoff

**The repository, organised** for the next phase (AI agents for problem-solving) and for new
contributors. No product behaviour changed: no migration, `make eval` unchanged (`43328080…`), and
the HTTP surface differs only in the version string and in comments and docstrings that cite tests
by path. The v0.28.0 handoff (the field review) is at `ebd9954`: `git show ebd9954:HANDOFF.md`.

## What moved

| Before | Now | Why |
|---|---|---|
| 116 test modules flat in `tests/` | 13 area folders that follow the package; `tests/README.md` maps them | a test's purpose was not visible from its location |
| helpers mixed with tests | `tests/support/` (incl. the DOM harness), one `paths.py` for repository paths | tests imported other tests; paths were counted with `parents[n]` |
| `testbed/` | `tests/lab/`; `NETCORENOC_LAB_STATE`, `netcorenoc-lab-*`, `lab.db` | it is test tooling; one name everywhere |
| `eval/` flat | `generators/`, `simulation/` (with the DSL), `synth/`, the gate at the root | five roles in one folder |
| `tools/evidence/`, `r2/after/` | removed | run by nothing; one-off v0.13–v0.16 measurements and an old screenshot |

`tests/repo/test_structure.py` now refuses a test at the root of `tests/` and an undocumented
folder, so the layout cannot drift back. `docs/record.md` maps every old path to its new one.

## Documentation

- **`docs/simulate.md`**: using the appliance with no network equipment, on Linux and on Windows
  (WSL 2). Bundled and DSL scenarios, a hand-written scenario (`linkDown`/`linkUp`), synthetic load,
  `snmptrap` (v1, v2c, chosen source address), the lab. Every command was run on this release; the
  hand-written scenario's alarm was confirmed `cleared` in the database.
- `tests/README.md`, `tools/README.md`, `eval/README.md` (rewritten), `tests/lab/README.md`
  (rewritten without the v0.17.0 measurement tables), a repository map in `CONTRIBUTING.md`.
- Corrected: a dead anchor in `install.md`; `tests/test_perf.py::burst`, a test that never existed;
  "the eval hash has held at `c2e8a0ce…` since v0.7.0" in three places (it has moved four times);
  a `HANDOFF.md §1` pointer that a later handoff replaced.

## Known limits, recorded rather than fixed

- **The appliance does not start on native Windows**: `main.py` uses `loop.add_signal_handler` and
  the resource sampler `os.statvfs`. The documentation gives WSL 2; `docs/ROADMAP.md` says what
  native support needs (both replaced, and a Windows CI job so the claim is tested).
- The Docker image was not built here: this environment's network policy blocks
  `registry-1.docker.io`. CI builds it; the lab was run end to end without Docker (two sources,
  14 clears, the unplaced rectifier alarm active, as CI asserts).

## For the next phase

- Start at `CONTRIBUTING.md` (setup, repository map, hard constraints) and
  `docs/architecture.md` (layers). An agent integration is an API client: the routes are in
  `src/netcorenoc/api/routes/`, the role model in `src/netcorenoc/crosscutting/rbac/`, and the
  maintenance-window resource was designed for an agent caller (its module docstring says how).
- New tests go in the area of the code they protect; a new area is a folder plus a row in
  `tests/README.md` and in `TEST_AREAS` (`tests/repo/test_structure.py`).
- Simulated traffic is never a label (`tests/dataset/test_evidence_boundary*.py`); an agent's
  proposals must reach the dataset only through an operator's gesture.

## Verification

| gate | result |
|---|---|
| `ruff check`, `ruff format --check`, `mypy --strict` (381 files) | clean |
| `vulture`, `bandit` | clean |
| full suite | see the PR description |
| `make eval` | no gated regressions; hash `43328080…` unchanged |
| `src/` pin | 357 files, no file added, removed or moved; digest re-recorded for comment and docstring path updates |
| behaviour-identity record | 85 lines, all attributed: the version in `/healthz` and `/openapi.json` (whose route descriptions also cite tests by new path), 19 console modules whose comments cite tests (4 roles each), and the script's own path in the header |
| lab, end to end | `run_local.py --demo`: CI's database assertions pass on `tests/lab/state/lab.db` |
