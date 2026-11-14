<p align="center">
  <img src="docs/media/logo.svg" width="72" height="72" alt="NetCoreNOC">
</p>

<h1 align="center">NetCoreNOC</h1>

<p align="center">
  <b>Traps in. Situations out. AI agents on call.</b><br>
  An open-source NOC for telecom networks: it correlates SNMP traps into situations with nothing to
  configure, and AI agents work the cases.
</p>

<p align="center">
  <a href="https://github.com/leonardoSaaads/NetCoreNOC/actions/workflows/ci.yml"><img src="https://github.com/leonardoSaaads/NetCoreNOC/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue.svg" alt="License: Apache-2.0"></a>
  <a href="https://github.com/leonardoSaaads/NetCoreNOC/releases"><img src="https://img.shields.io/github/v/release/leonardoSaaads/NetCoreNOC?sort=semver" alt="Latest release"></a>
</p>

<p align="center">
  <a href="docs/media/netcorenoc-promo.mp4"><img src="docs/media/netcorenoc-promo.gif" width="800" alt="NetCoreNOC in one minute: install, sign in, a fibre cut becomes one situation, and an AI agent works the case"></a><br>
  <sub>▶ <a href="docs/media/netcorenoc-promo.mp4">Watch the one-minute video</a>: install on Linux, sign in, a fibre cut becomes one situation, an AI agent works the case.</sub>
</p>

## What it does

* **Broker**: point your equipment's traps at it. It discovers devices and alarm types, groups related
  alarms into **situations**, names the probable root cause and shows *why* each alarm was grouped.
  No MIBs, no inventory, no topology files.
* **NOC**: confirm a situation's grouping, press *Start working this*, and it becomes a **case**,
  with a queue, clocks and a customer update. Each **customer** keeps its book — instructions,
  circuits, documents, its equipment's credentials and its email. **AI agents** (any
  OpenAI-compatible model, a local Ollama or Claude) take the case, read the customer's devices with
  read-only commands (SSH, SNMP, secure Telnet) and email the customer update; the customer's reply
  comes back to the case. Agents lease short-lived tokens and never see a password.
* **Secure by default**: two-factor sign-in (an authenticator app) for every person, credentials
  sealed twice, a hash-chained audit log you can search by period, person and section.
* One appliance: three Python processes, PostgreSQL and a web console. No accepted trap is lost to a
  slow model or a restart.

> **Early software.** It has not run in production yet. Run it beside your current NMS first: it
> only needs a copy of the traps.

## Quickstart (Linux, Docker Compose)

```sh
git clone https://github.com/leonardoSaaads/NetCoreNOC.git && cd NetCoreNOC
docker compose up -d --build
docker compose logs netcorenoc | grep -A4 "bootstrap admin"   # the one-time password for `admin`
```

1. Open `http://127.0.0.1:8080/` (or `http://<host>:8080/`), sign in as `admin`, choose a new
   password and set up two-factor sign-in: scan the QR code with an authenticator app, keep the
   recovery codes, type the code.
2. Point the devices' **SNMP trap destination** at the host, UDP **162** (v1/v2c; SNMPv3 users go in
   **Settings → SNMP**).
3. Watch **Situations**.

AI agents (optional): switch the top bar to **NOC**. Its Overview lists what is missing, each item a
link: connect a model under **Agents → AI providers** (a local Ollama is found by itself), hire an agent, and in each customer's
**Access** store its devices' credentials and switch agents on; its **Email** sends the customer
updates and reads the replies. No equipment yet? [`docs/simulate.md`](docs/simulate.md) sends real
traps from simulated devices.

Before a team relies on it: an allowlist, HTTPS, one account per person and backups of the database
and both key files ([`docs/operate.md`](docs/operate.md), section 10). A visibility scope narrows
what someone sees, but it is **not tenant isolation** ([`docs/security.md`](docs/security.md)).

## Documentation

| | |
|---|---|
| [`docs/install.md`](docs/install.md) | Every way to install it (Compose, Docker, pip, systemd, Nix) and upgrades |
| [`docs/operate.md`](docs/operate.md) | First sign-in, traps, situations, the NOC and its agents, backups, going to production |
| [`docs/console.md`](docs/console.md) | What each screen is for |
| [`docs/configure.md`](docs/configure.md) | Every setting and its default; the database |
| [`docs/security.md`](docs/security.md) | Accounts, roles, the credential vault, agents, TLS, the audit log |
| [`docs/troubleshoot.md`](docs/troubleshoot.md) | Symptoms and fixes |
| [`docs/README.md`](docs/README.md) | Everything else, including how it works inside |

## Development

```sh
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
make qa         # lint, type check, tests and the evaluation gate (needs PostgreSQL: `make pg-test-up`)
make dom        # the console's tests (Node 22+)
```

[`CONTRIBUTING.md`](CONTRIBUTING.md) has the rules a change must follow.

## Licence

Apache-2.0, see [LICENSE](LICENSE). Report vulnerabilities privately, as described in
[`SECURITY.md`](SECURITY.md).
