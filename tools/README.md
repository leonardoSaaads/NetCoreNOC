# `tools/` — command-line tools for developers and maintainers

Development-only: not shipped in the wheel or the image, never imported by `src/netcorenoc/`.
Each tool runs with the project's virtual environment and needs no extra dependency.

| Tool | What it does | Typical use |
|---|---|---|
| `trap_replay.py` | Sends real SNMPv2c traps over UDP: replays a scenario file, or generates synthetic load. | Feed a running appliance without network equipment; load tests. |
| `trap_sim.py` | Runs a declarative scenario (`eval/simulation/scenario_dsl.py`) against a running appliance, or writes it as a scenario file. | Reproduce a specific alarm pattern. |
| `trappack_build.py` | Builds the built-in trap pack (`src/netcorenoc/ingest/trappack.tsv.gz`, `trapobjects.tsv.gz`) from vendors' MIB files. | Refresh trap names and default severities. |
| `corpus_census.py` | Replays the whole corpus through one engine and reports what the promotion gate would decide. | `make census`. |
| `release_check.py` | Checks that the version agrees in `pyproject.toml`, the package, `CHANGELOG.md` and `flake.nix`. | `make release-check`; the release workflow. |

## Sending traps without equipment

The two traffic tools are the supported way to exercise the appliance with no network devices. The
step-by-step guide for Linux and Windows is [`docs/simulate.md`](../docs/simulate.md). Short form,
with the appliance listening on UDP 1162:

```sh
python tools/trap_replay.py eval/corpus/fiber_cut.json --port 1162   # a labelled scenario
python tools/trap_sim.py --list                                      # scenario DSL catalogue
python tools/trap_sim.py login_burst --send --port 1162              # one DSL scenario
python tools/trap_replay.py --synthetic 20 --classes 10 --rate 200 --duration 10 --port 1162
```

Every tool prints its options with `--help`.

## Adding a tool

A tool belongs here when a developer or maintainer runs it by hand or from the `Makefile`, and the
product does not need it at runtime. Give it a module docstring that says what it does and how to
run it, a `--help`, a row in the table above, and a test under `tests/` (most live in
`tests/evaluation/` or `tests/ingest/`).
