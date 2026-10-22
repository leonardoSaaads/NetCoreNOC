# Installing NetCoreNOC

Four routes, and a fifth way to try it with no equipment at all. All of them give you the same
thing: **one appliance, one PostgreSQL schema, one static web console**, with five Python
dependencies and no build step. Pick by what you already run.

Python **3.12 or newer** is required everywhere, and **PostgreSQL 15 or newer**. Docker Compose
brings its own PostgreSQL; every other route points `NETCORENOC_DATABASE_URL` at one — on the same
host, on your own cluster, or a managed one such as Supabase
([`configure.md`](configure.md#database)).

| Route | Use it when | Hardened by default |
|---|---|---|
| [Docker Compose](#docker-compose) | you want the shortest path to a running appliance | yes |
| [Docker](#docker) | you already have an orchestrator | yes, with the flags below |
| [pip](#pip) | you are developing, or packaging it yourself | no — see [`security.md`](security.md) |
| [Nix](#nix) | you run NixOS or use flakes | no |

Then read [`operate.md`](operate.md) — the first-boot password is printed once and there is one
detail about signing in that will otherwise look like a bug.

## Docker Compose

```sh
docker compose up --build
```

That is the whole setup. Three services start, in order:

| Service | What it is |
|---|---|
| `db-secret` | Runs once and exits. On the first start it writes a random password into the `netcorenoc-secrets` volume, which only the two other services mount, read-only. PostgreSQL sets it for its role and the appliance reads it from the same file: it is never typed, never in `.env`, and in no container's environment |
| `postgres` | PostgreSQL 17 (`postgres:17.5-alpine`) on an internal network with **no published port** and no route out; only the appliance joins it. Read-only root filesystem, every capability dropped except the five its entrypoint needs to drop to its own user, SCRAM-SHA-256 for every connection, data checksums, a health check, and settings tuned for many small transactions (in `command:`) within 2.0 CPUs and 1 GiB |
| `netcorenoc` | The appliance, started once PostgreSQL is healthy: read-only root filesystem, every Linux capability dropped except `CAP_NET_BIND_SERVICE` (needed to bind privileged UDP 162), no privilege escalation |

Three named volumes survive `docker compose down`: `netcorenoc-pgdata` (the database),
`netcorenoc-data` (the trap spool) and `netcorenoc-secrets` (the generated password).
`docker compose down --volumes` deletes all three, data included. Keep `netcorenoc-pgdata` and
`netcorenoc-secrets` together: a new password is not applied to an existing database.

Optional configuration lives in `.env`:

```sh
cp .env.example .env    # then edit; .env is git-ignored and is the only place secrets belong
```

To run the appliance on a managed PostgreSQL (Supabase, RDS, Cloud SQL, Azure, Neon) instead of
the bundled one, use the override in
[`configure.md`](configure.md#a-managed-postgresql-supabase-and-others).

Point your equipment's SNMP trap destination (v2c or v1; v3 once its user is added under Settings →
SNMP) at the host, open `http://<host>:8080/`, and go to [`operate.md`](operate.md).

### Resource limits, and what happens when one is hit

`docker-compose.yml` sets `deploy.resources`: **2.0 CPUs** and **512 MiB** for the appliance, with
0.25 CPU and 128 MiB reserved. The appliance is three processes — the receiver (reads the trap
socket and appends each datagram to the spool), the decoder, and the main one (classification and
the console) — with the spool on its own volume. PostgreSQL is a separate container with limits of
its own (2.0 CPUs, 1 GiB). The two limits fail in **different ways**:

| limit | what happens when it is reached | what it costs you |
|---|---|---|
| **memory** | the kernel OOM-kills a process; `restart: unless-stopped` brings it back, and PostgreSQL keeps every committed batch | almost nothing since ADR #450: every accepted trap is in the spool on disk before it is classified, and the engine resumes after the last trap it classified. At most the few milliseconds the receiver held before its next write. |
| **cpus** | the processes are throttled, not killed | classification falls behind the wire and the backlog grows **in the spool**. The Overview's trap pipeline shows the backlog, its oldest wait and every trap past the KPI. Nothing is dropped while the spool has room (`NETCORENOC_SPOOL_MAX_MB`). |

So if a storm puts classification further behind than the KPI allows, **raise `cpus`**; the spool,
not memory, absorbs the backlog. Size the `netcorenoc-data` volume for it: about 200 bytes per
waiting trap.

`make storm` sends a storm of distinct alarms through the real path — receive, spool, decode,
classify — and reports every trap's decision latency against the KPI. Since ADR #450 a storm waits
in the **spool on disk** (`NETCORENOC_SPOOL_DIR`; by default `spool` in `NETCORENOC_DATA_DIR`, the
`netcorenoc-data` volume), not in memory, so memory no longer grows with a backlog; give the
container two CPUs (receiving and decoding run in processes of their own) and room on that volume
for the spool (`NETCORENOC_SPOOL_MAX_MB`).

To change them, edit `deploy.resources` in `docker-compose.yml`. With plain `docker run`, the
equivalents are `--cpus 1.0 --memory 512m`.

## Docker

The image holds the appliance only. Give it a PostgreSQL and the password in a file:

```sh
docker build -t netcorenoc .
docker run -d --name netcorenoc \
  --read-only --cap-drop ALL --cap-add CAP_NET_BIND_SERVICE \
  --security-opt no-new-privileges --tmpfs /tmp \
  --cpus 1.0 --memory 512m \
  -p 162:162/udp -p 8080:8080 \
  -v netcorenoc-data:/home/netcorenoc \
  -e NETCORENOC_DATABASE_URL=postgresql://netcorenoc@db.example:5432/netcorenoc \
  -e NETCORENOC_DATABASE_PASSWORD_FILE=/run/secrets/db_password \
  -v /etc/netcorenoc/db_password:/run/secrets/db_password:ro \
  netcorenoc
docker logs netcorenoc     # the one-time bootstrap admin password is here
```

The password file is read inside the container by its user, uid 10001: make it readable by that
uid and nobody else (`sudo chown 10001 /etc/netcorenoc/db_password && sudo chmod 0400 …`). Never
pass the password with `-e`: `docker inspect` shows the environment to anyone who can run it.

**Do not drop the data volume.** It holds the trap spool: traps accepted and not yet classified
live there, and are lost with the container's writable layer.

If you would rather not grant `CAP_NET_BIND_SERVICE`, map a high port instead and tell the appliance
about it (with Compose, `NETCORENOC_TRAP_PORT=1162` in `.env` does both):

```sh
docker run -d --cap-drop ALL -e NETCORENOC_TRAP_PORT=1162 -p 1162:1162/udp ... netcorenoc
```

Your equipment then has to be configured to send to 1162, which most vendors support.

## pip

```sh
python3.12 -m venv .venv
.venv/bin/pip install .     # SNMPv3 privacy (DES/AES) included: `cryptography` is a core dependency
.venv/bin/python -m netcorenoc.main
```

Binding UDP 162 needs privileges. Unprivileged, run on a high port:

```sh
NETCORENOC_TRAP_PORT=1162 .venv/bin/python -m netcorenoc.main
```

The defaults expect a role `netcorenoc` owning a database `netcorenoc` on `localhost:5432`
(`NETCORENOC_DATABASE_URL`); the appliance creates its schema, `netcorenoc`, and applies the
migrations on the first start. The spool is written under `NETCORENOC_DATA_DIR` (default: the
working directory).

### The database, on the same host

On Debian or Ubuntu (any PostgreSQL 15 or newer will do):

```sh
sudo apt install postgresql
sudo -u postgres createuser --pwprompt netcorenoc         # choose the role's password
sudo -u postgres createdb --owner netcorenoc netcorenoc
sudo install -d -m 0750 /etc/netcorenoc
sudo sh -c 'umask 077; cat > /etc/netcorenoc/db_password'  # the same password, Enter, Ctrl-D
```

The role needs no superuser: it owns its database, so it may create the schema. Run the appliance
with `NETCORENOC_DATABASE_PASSWORD_FILE` naming a file readable by the user it runs as — the unit
below hands this one over by itself. [`configure.md`](configure.md#database) has every database
variable; [`security.md`](security.md#the-database) what the role should and should not hold.

**Better: a second role for the appliance, which owns nothing** (ADR #453), so neither an injection
nor a compromised process can alter a table or rewrite the audit log. Create `netcorenoc_app` beside
the owner, keep the owner's password for `db migrate`, and give the appliance the runtime role:
[`configure.md`](configure.md#two-roles-one-owns-the-schema-the-appliances-owns-nothing). The unit
file carries the lines to uncomment.

**The secret key** is created on the first start in the data directory (`secret.key`,
`/var/lib/netcorenoc` with the unit below). Back it up once, apart from the database: it seals
personal data and secrets and keys the password hashes ([`operate.md`](operate.md#8-backups-upgrades-and-checks)).

### As a systemd service

[`deploy/netcorenoc.service`](../deploy/netcorenoc.service) is a hardened unit file — a dedicated
user, `ProtectSystem=strict`, `NoNewPrivileges`, and `AmbientCapabilities=CAP_NET_BIND_SERVICE` so
the process can bind 162 without running as root. It expects the database above (or set
`NETCORENOC_DATABASE_URL` in a drop-in for another server), and passes the password as a
**systemd credential**: `LoadCredential=` copies `/etc/netcorenoc/db_password` into a directory only
the service can read, and `NETCORENOC_DATABASE_PASSWORD_FILE` names it there (`%d`). The password
is in neither the unit nor the environment. The spool is `/var/lib/netcorenoc` (`StateDirectory=`).

**The unit's `ExecStart` is `/opt/netcorenoc/.venv/bin/python`**, so install there rather than in
the working directory the [pip](#pip) section uses — a unit pointing at a venv that does not exist
fails to start, and `systemd-analyze verify` is what says so:

```sh
sudo python3.12 -m venv /opt/netcorenoc/.venv
sudo /opt/netcorenoc/.venv/bin/pip install .
sudo cp deploy/netcorenoc.service /etc/systemd/system/
systemd-analyze verify /etc/systemd/system/netcorenoc.service   # silence means it will start
sudo systemctl daemon-reload && sudo systemctl enable --now netcorenoc
journalctl -u netcorenoc | grep -i bootstrap     # the one-time admin password
```

Environment variables go in a drop-in (`systemctl edit netcorenoc`), not in the unit file, so an
upgrade does not overwrite them.

## Nix

```sh
nix run github:leonardoSaaads/NetCoreNOC
# or, in a clone:
nix run .
```

The flake pins nixpkgs 24.11 and Python 3.12. It packages the appliance, not a database: set
`NETCORENOC_DATABASE_URL` as for [pip](#pip). `nix develop` gives you the development shell with
the test tooling.

## Trying it without any hardware

See [`simulate.md`](simulate.md) (Linux and Windows): the bundled scenarios are sent as **real SNMP
PDUs over UDP**, not loaded into the database, so the correlation you see afterwards is the
appliance's own work.

## Upgrading

Take a backup, then install the new version and start it again. Schema migrations are
**forward-only, idempotent, and applied at startup** — `python -m netcorenoc db migrate`
(`make migrate`) applies them beforehand if you prefer, and is a no-op otherwise. There is no
downgrade path, so the backup is the rollback. With Compose
([`operate.md`](operate.md#8-backups-upgrades-and-checks) explains the command):

```sh
docker compose exec -T postgres sh -c 'PGPASSWORD="$(cat /run/netcorenoc-secrets/db_password)" \
  exec pg_dump --format=custom --schema=netcorenoc -U netcorenoc netcorenoc' > before-upgrade.dump
git pull && docker compose up -d --build
```

Elsewhere, `python -m netcorenoc backup <file>` does the same with the PostgreSQL client tools
(`pg_dump`, `pg_restore`) on the host. `pg_dump` reads one consistent snapshot while the appliance
runs; a copy of PostgreSQL's data directory taken while the server runs is not a backup.

**Upgrading from v0.30.0 or earlier**, which kept everything in a SQLite file: the file is imported
into PostgreSQL once, before the new release's first start. [`MIGRATION.md`](../MIGRATION.md) has
the steps for Compose and for systemd.

[`MIGRATION.md`](../MIGRATION.md) records what each version changed and the two or three upgrades
that need you to know something.

## Verifying what you installed

```sh
python -m netcorenoc db status        # the server, the schema's version and its size
python -m netcorenoc audit verify     # walks the audit chain, reports the first broken link
make checksums                        # the vendored asset bytes against their pinned SHA-256
```
