# NetCoreNOC

**Zero-configuration alarm correlation for telecom networks.** Point your equipment at NetCoreNOC as
an SNMP trap destination — that is the whole setup. From the raw trap stream alone, with no MIBs, no
inventory and no topology files, it discovers devices, learns a topology graph from alarm
co-occurrence, groups related alarms into *situations*, and ranks the probable root cause. Every
grouping decomposes into three named numbers you can read on screen, so you can always answer *why
did it group these?*

One Python process. One SQLite file. One web console, no build step.

[![CI](https://github.com/leonardoSaaads/NetCoreNOC/actions/workflows/ci.yml/badge.svg)](https://github.com/leonardoSaaads/NetCoreNOC/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Latest release](https://img.shields.io/github/v/release/leonardoSaaads/NetCoreNOC?sort=semver)](https://github.com/leonardoSaaads/NetCoreNOC/releases)

> **Pre-alpha.** Zero users, nothing in production. It is honest engineering and it has not met your
> network yet.

## Quickstart

```sh
docker compose up --build
docker compose logs netcorenoc | grep -A4 bootstrap    # the one-time admin password
```

Point your equipment's SNMPv2c trap destination at the host and open `http://<host>:8080/`.

**One detail that will otherwise look like a bug:** the bootstrap admin must supply a *new* password
in the same request that signs it in. In the browser the login form does this for you. Against the
API, a password-only `POST /api/login` returns `200 {"must_change_password": true}` and **no session
cookie** — that is correct, and [`docs/operate.md`](docs/operate.md) shows both requests side by
side.

No hardware handy? Send real SNMP PDUs over UDP from a bundled scenario:

```sh
python3.12 -m venv .venv && .venv/bin/pip install .
NETCORENOC_TRAP_PORT=1162 .venv/bin/python -m netcorenoc.main &
make replay
```

Other install routes — plain Docker, pip, Nix, systemd — are in [`docs/install.md`](docs/install.md).

## What you see

Seventeen views in three groups: **Operations** (situations, network graph, timeline, entities,
alarm classes), **Evidence** (labelling, corpus, judge & promotion) and **Administer** (users,
tokens, settings, link scorer, governance, quarantine, audit). A view you cannot use is not
rendered — a viewer sees no `Administer` group at all.

The screen the product exists for is **Situations**: dense cards that expand in place to show the
probable root cause, the member alarms, and then *Why these were grouped* — one row per link with
the score and **the three named terms, each with its number beside its bar**.

## How it works

Every trap is reduced to a **device** (source IP), a **class** (the trap OID as an opaque token — no
MIB is ever consulted) and an **instance**. Alarms deduplicate on that fingerprint. Two alarms
inside a 120-second window are linked when

```
s = 0.3·e^(−Δt/30s) + 0.35·A[class_i, class_j] + 0.35·E[ne_i, ne_j] > 0.5
```

`A` and `E` are learned incrementally from co-occurrence (normalised PMI, exponential forgetting,
damped 10× during storms, and an entity pair needs five observations before its edge is trusted). A
**situation** is a connected component of the resulting link graph; learned temporal precedence
flags the probable root. Raise/clear pairs are learned from strict alternation. `Confirm` reinforces
a grouping, `Split` penalises it.

**Cold start is honest.** With nothing learned, two alarms group only when they are on the same
network element and within about 21 seconds. Everything beyond that — cross-device correlation,
raise/clear pairs, which varbind names the alarmed entity — is learned from *your* stream. Run it
alongside your existing NMS from day one; it only needs a copy of the traps.

The formula is a seam, not a constant: five scorer kinds exist (`additive`, `logistic`, `tree`,
`forest`, `gradient_boosting`), all running in this process in pure Python with **no new
dependency**. Each decomposes its own decision into the same three contributions, **exactly** — a
model too large to explain exactly is refused rather than approximated. Nothing is promoted without
evidence measured against floors registered before the data existed, and there is no HTTP route that
creates a model version.

## Documentation

Start at [`docs/README.md`](docs/README.md).

| | |
|---|---|
| [`docs/install.md`](docs/install.md) | Docker, Compose, pip, Nix, systemd |
| [`docs/operate.md`](docs/operate.md) | First boot, signing in, sending traps, reading a situation |
| [`docs/configure.md`](docs/configure.md) | Every environment variable and what it costs |
| [`docs/correlation.md`](docs/correlation.md) | How two alarms come to be linked |
| [`docs/console.md`](docs/console.md) | The views, and four things the console does not do |
| [`docs/security.md`](docs/security.md) | The posture, the perimeter, RBAC, the audit chain |
| [`docs/troubleshoot.md`](docs/troubleshoot.md) | What breaks and what the symptom looks like |
| [`docs/architecture.md`](docs/architecture.md) | The layers, the rules, and the three-phase design |
| [`docs/findings.md`](docs/findings.md) | Every open finding, with a reproduction command |

## Development

```sh
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
make qa        # ruff + mypy --strict + 1500-odd tests with coverage + the eval gate
make dom       # the DOM tests, reporting how many actually EXECUTED
make security  # bandit + pip-audit
```

The console is tested by **executing** it: `tests/domharness/` links and evaluates the whole ES
module graph in a DOM under `node:vm` and drives it against responses captured from the real server.
It needs Node ≥ 22 on `PATH` and nothing else — no npm, no `package.json`, no lockfile. Without
Node the DOM tests **skip loudly**, and `27 skipped` must never be read as `27 passed`.

[`CONTRIBUTING.md`](CONTRIBUTING.md) has the quality bar and the hard constraints.

## Philosophy

- **Zero configuration.** You provide a trap destination and nothing else.
- **Structure emerges from the stream.** Devices, classes, raise/clear pairs, topology edges and
  precedence are learned, never declared.
- **Explainability over sophistication.** Three numbers explain every link, and that survives the
  arrival of tree ensembles.
- **Simplicity is a feature.** No brokers, no ORMs, no plugins, no frontend toolchain — and that
  last one is a test, not an intention.
- **Mechanism is configurable; the standard of evidence is not.**

## Licence

Apache-2.0 — see [LICENSE](LICENSE). Report vulnerabilities privately per
[`SECURITY.md`](SECURITY.md).
