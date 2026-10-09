# NetCoreNOC

**SNMP trap correlation for telecom networks, with nothing to configure.** Point your equipment's
trap destination at NetCoreNOC. It discovers the devices, groups related alarms into
**situations**, names the probable root cause, and shows *why* each alarm was grouped. No MIBs, no
inventory and no topology files are needed.

One Python process, one SQLite file, one web console.

[![CI](https://github.com/leonardoSaaads/NetCoreNOC/actions/workflows/ci.yml/badge.svg)](https://github.com/leonardoSaaads/NetCoreNOC/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Latest release](https://img.shields.io/github/v/release/leonardoSaaads/NetCoreNOC?sort=semver)](https://github.com/leonardoSaaads/NetCoreNOC/releases)

> **Early software.** It has not run in production yet. Run it beside your current NMS first — it
> only needs a copy of the traps.

## Quickstart (10 minutes, Docker)

You need Docker with Compose, and a host your equipment can reach on UDP 162.

```sh
git clone https://github.com/leonardoSaaads/NetCoreNOC.git && cd NetCoreNOC
cp .env.example .env                       # optional settings; safe to leave as is
docker compose up -d --build
docker compose logs netcorenoc | grep -A4 "bootstrap admin"
```

The last command prints a one-time password for the user `admin`. Then:

1. Open `http://<host>:8080/`, sign in as `admin`, and choose a new password (12+ characters).
2. Point your equipment's **SNMPv2c or v1 trap destination** at `<host>`, UDP port **162**. For
   **SNMPv3**, add the user under **Settings → SNMP** first; the form prints the device commands.
3. Watch **Situations**. Devices and alarm types appear as traps arrive.

Cannot use port 162? Set `NETCORENOC_TRAP_PORT=1162` in `.env`, run `docker compose up -d` again,
and send traps to port 1162. Other ways to install (plain Docker, pip, systemd, Nix):
[`docs/install.md`](docs/install.md).

### Try it with test traffic, without equipment or Docker

```sh
python3.12 -m venv .venv && .venv/bin/pip install .
NETCORENOC_TRAP_PORT=1162 NETCORENOC_HTTP_PORT=8081 .venv/bin/python -m netcorenoc.main
# in a second terminal: a fibre cut on two devices, sent as real SNMP traps
make replay                    # `make replay-list` shows the other scenarios
```

Open `http://localhost:8081/`. Run this without Docker: Docker's port proxy rewrites the source
address of traps sent from the same machine, so every simulated device would appear as one. More
scenarios, your own traps, `snmptrap`, and the steps for Windows (WSL 2):
[`docs/simulate.md`](docs/simulate.md).

## Before a team relies on it

| Step | How |
|---|---|
| Only accept traps from your equipment | `NETCORENOC_ALLOWLIST=10.0.0.0/8,192.0.2.10` in `.env` |
| Serve HTTPS | `NETCORENOC_TLS_CERT` / `NETCORENOC_TLS_KEY`, or a TLS reverse proxy — [`docs/security.md`](docs/security.md) |
| One account per person | **People & access**: `viewer` (read), `editor` (work situations), `admin` (everything). A visibility scope narrows what someone sees, but it is **not tenant isolation**: correlation still learns across the whole estate — [`docs/security.md`](docs/security.md) |
| A second admin | so a forgotten password never locks the team out |
| Password recovery by email | **Settings → Email** (Gmail, Microsoft 365 and others preset, or any SMTP server), and a recovery address per person |
| Accept only what you expect | **Settings → SNMP**: switch off unused versions, list communities, or require SNMPv3 users |
| Programs use service tokens | **People & access → Service tokens**; the value is shown once, and the API reference below the list shows every call a token can make |
| Back up the database | `docker compose exec netcorenoc python -m netcorenoc backup /home/netcorenoc/backup.db` |
| Upgrade | back up, `git pull`, `docker compose up -d --build`; read [`MIGRATION.md`](MIGRATION.md) |

Forgot a password? **Forgot your password?** on the sign-in screen once email is set up; otherwise
`docker compose exec netcorenoc python -m netcorenoc admin reset-password <user>`. More in
[`docs/troubleshoot.md`](docs/troubleshoot.md).

## How it decides

* Every trap becomes an **alarm**: device + trap type + instance (for example the port). A repeat
  of the same alarm increments its count instead of creating a new one.
* A trained model compares each new alarm with recent ones and decides which belong together. Five
  models compete and a judge picks the best; an admin can pin one. If no model can be loaded, a
  simple built-in formula takes over. Every grouping shows the reasons, with numbers.
* A situation is **New** until someone works on it, **Open** while the team handles it, and
  **Resolved** when its alarms clear, it goes quiet, or an operator closes it. A model never adds
  alarms to an Open situation on its own — it proposes them as **Pending** for an operator to
  accept or reject.
* A clear (for example `linkUp`, or a trap whose severity reads `cleared`) ends its alarm. An alarm
  that repeats after a long silence, or after its situation was closed, opens a **new** situation.
  [`docs/operate.md`](docs/operate.md#4-how-alarms-repeat-clear-and-come-back) has the exact rules.

## Documentation

| | |
|---|---|
| [`docs/install.md`](docs/install.md) | Every way to install it |
| [`docs/operate.md`](docs/operate.md) | Signing in, sending traps, working situations, alarm rules |
| [`docs/configure.md`](docs/configure.md) | Every setting and its default |
| [`docs/console.md`](docs/console.md) | What each screen is for |
| [`docs/troubleshoot.md`](docs/troubleshoot.md) | Symptoms and fixes |
| [`docs/security.md`](docs/security.md) | Accounts, roles, TLS, the audit log |
| [`docs/README.md`](docs/README.md) | Everything else, including how it works inside |

## Development

```sh
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
make qa         # lint, type check, the test suite and the evaluation gate
make dom        # the console's tests; needs Node 22+ on PATH
make security   # bandit and pip-audit
```

[`CONTRIBUTING.md`](CONTRIBUTING.md) has the rules a change must follow.

## Licence

Apache-2.0 — see [LICENSE](LICENSE). Report vulnerabilities privately, as described in
[`SECURITY.md`](SECURITY.md).
