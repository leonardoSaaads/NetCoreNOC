# NetCoreNOC documentation

New here? Start with the project [`README.md`](../README.md): it gets the appliance running in ten
minutes. Then pick the page for what you are doing.

## Using it

| I want to… | Read |
|---|---|
| Install it, or install it another way | [`install.md`](install.md) |
| Sign in, send traps, work situations, understand repeats and clears | [`operate.md`](operate.md) |
| Try it, or develop, with no network equipment (Linux and Windows) | [`simulate.md`](simulate.md) |
| Change a port, the allowlist, TLS, retention | [`configure.md`](configure.md) |
| Know what a screen is for | [`console.md`](console.md) |
| Fix something that is not working | [`troubleshoot.md`](troubleshoot.md) |
| Know what is protected, and how | [`security.md`](security.md) |
| Upgrade from an older version | [`MIGRATION.md`](../MIGRATION.md) |

## Understanding how it works

| | |
|---|---|
| [`correlation.md`](correlation.md) | How alarms are grouped, and how to read the breakdown |
| [`architecture.md`](architecture.md) | The layers, the modules and the rules between them |

## Contributing

Read [`CONTRIBUTING.md`](../CONTRIBUTING.md) first: setup, the repository map and the rules. Each
development folder explains itself: [`tests/`](../tests/README.md), [`eval/`](../eval/README.md),
[`tools/`](../tools/README.md), [`tests/lab/`](../tests/lab/README.md). The project's own record:

| | |
|---|---|
| [`adr/DECISIONS.md`](adr/DECISIONS.md) | Every design decision, numbered, with its reason ([format](adr/README.md)) |
| [`findings.md`](findings.md) | Defects found, each with its reproduction |
| [`ROADMAP.md`](ROADMAP.md) | Open items |
| [`plans/releases.md`](plans/releases.md) | What each release is — the source of truth for release claims |
| [`analysis/`](analysis/) | Pre-registered analysis plans: written before the data, hash-guarded, never edited |
| [`record.md`](record.md) | Where removed documents went, and how to read an old citation |

No documentation build step: plain Markdown, and every relative link is checked by
`tests/repo/test_structure.py`.
