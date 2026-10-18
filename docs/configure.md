# Configuration

**Everything is optional.** NetCoreNOC starts with nothing set and works: it needs a trap
destination and nothing else. This page is what each knob does and what turning it costs.

Two mechanisms, and the difference matters:

* **Environment variables** — read once at startup. Changing one needs a restart.
* **Stored settings** — a few values an admin changes in the console at runtime, which then take
  precedence over the environment. The **Settings** screen shows all three columns for every live
  value: environment default, database override, effective. *"Why is this value what it is?"* has an
  answer on screen.

## Environment variables

The database's own variables — where it is, its password, the schema — are under
[Database](#database).

| Variable | Default | What it does, and what it costs |
|---|---|---|
| `NETCORENOC_DATA_DIR` | `.` | The directory the appliance keeps its own files in: the trap spool, `<data dir>/spool`. Put it on a real volume (the image sets `/home/netcorenoc`, the systemd unit `/var/lib/netcorenoc`) |
| `NETCORENOC_TRAP_HOST` | `0.0.0.0` | Trap listener bind address. Bind to the management interface rather than everything |
| `NETCORENOC_TRAP_PORT` | `162` | Trap listener UDP port. 162 needs `CAP_NET_BIND_SERVICE` or root; 1162 needs neither |
| `NETCORENOC_HTTP_HOST` | `0.0.0.0` | Console and API bind address |
| `NETCORENOC_HTTP_PORT` | `8080` | Console and API port |
| `NETCORENOC_ALLOWLIST` | *(accept all)* | Comma-separated source CIDRs allowed to send traps. **Unset means every source is accepted**, and the console shows admins a persistent banner until you set it. Denied datagrams are counted, never silently dropped |
| `NETCORENOC_TLS_CERT` / `NETCORENOC_TLS_KEY` | *(unset)* | Enable built-in TLS. Both must be set. The session cookie gains `Secure` automatically |
| `NETCORENOC_RETENTION_DAYS` | `7` | Pruning horizon for cleared/closed **operational** history. It does **not** govern the feedback dataset — those have their own tiers below |
| `NETCORENOC_AUDIT_RETENTION_DAYS` | `365` | Retention for the audit log. Pruning is admin-triggered only, never automatic |
| `NETCORENOC_LOG_JSON` | *(off)* | Structured JSON logging. Anything other than empty, `0`, `false` or `False` enables it |
| `NETCORENOC_SMTP_PASSWORD` | *(unset)* | The SMTP password for recovery email (v0.30.0). When set it is used and Settings → Email's password field is ignored, so the password never enters the database or its backups. The rest of the mail server is configured in the console |
| `NETCORENOC_REARM_S` | `3600` | Seconds an active alarm may be silent before its next trap counts as a **new occurrence** (its clear was probably lost) and opens a new situation. `0` = a repeat always stays in its situation. [`operate.md`](operate.md#4-how-alarms-repeat-clear-and-come-back) |
| `NETCORENOC_KPI_TARGET_S` | `60` | The classification KPI's **target**: seconds from each trap's own arrival to the commit that places it in a situation. A trap past it is counted as late on the Overview's trap pipeline. An admin's value saved on **Settings → System → Live configuration** overrides this one, without a restart (#451) |
| `NETCORENOC_KPI_LIMIT_S` | `120` | The KPI's **limit** (at or above the target): a trap past it is counted as a breach and raises a warning. Neither number ever discards a trap — a late trap is still classified (#451) |
| `NETCORENOC_INGEST_PROCESSES` | `1` | `1` receives traps in a **process of their own** that spools each datagram raw, with decoding in another (#449, #450). `0` keeps the receiver in the main process **without the spool**: a trap that arrives while its queue is full is dropped. For development only. Give the container at least two CPUs with `1` |
| `NETCORENOC_SPOOL_DIR` | *(`<data dir>/spool`)* | Where accepted traps wait, on disk, to be classified. Mode 0700; put it on a real volume, on the appliance's own host (#450) |
| `NETCORENOC_SPOOL_MAX_MB` | `2048` | The spool's ceiling. About 200 bytes a trap, so the default holds about ten million waiting traps; past it new traps are counted as dropped and a warning says so. Classified traps are deleted from it as the database makes them durable (#450) |

**Every one of them is validated at startup and refuses by name.** A port outside 1–65535, a
retention that is not a number, an allowlist entry that is not a CIDR, a TLS path that cannot be
read, exactly one of the two TLS variables set, a database URL that is not `postgresql://`, a
password file that cannot be read, a schema name that is not one: the process prints one sentence
naming the variable and exits 2. An empty value is not the same as an unset one —
`NETCORENOC_RETENTION_DAYS=` is a value, and it is refused.

### The host's socket buffer, for storms

Since #450 the receiver only reads the socket and appends each datagram to the spool — no decoding
on that path — so it drains the socket as fast as the kernel fills it. Measured: 20 000 traps at
5 000/s into an 8 MiB buffer, none lost (decoding first, the same storm lost 4 238 in the kernel).
What remains is an **instantaneous** burst larger than the buffer itself, which no reader can
outrun: the kernel drops the excess before any process sees it, the one loss nothing in the
appliance can count. The trap socket asks for 32 MiB; Linux grants at most `net.core.rmem_max`, and
the appliance logs a warning at start-up when it got less. On the host (not in the container — the
setting is not per container):

```sh
sudo sysctl -w net.core.rmem_max=33554432
echo 'net.core.rmem_max=33554432' | sudo tee /etc/sysctl.d/90-netcorenoc.conf
```

`make storm` (`tools/storm_check.py`) measures what this host actually does with a storm, trap by
trap, and exits non-zero if any trap was not kept (#447-#451).

### Three variables that are hard startup errors

Setting any of these does not warn — the process **refuses to start** and names the replacement:

* **`NETCORENOC_API_TOKEN`** — the shared API token was removed in v0.3.0. Use service tokens
  (per-identity, revocable, shown once) from the **Service tokens** screen.
* **Any `OPTICORR_*` variable** — the legacy prefix from before the rename was removed in v0.6.0.
* **`NETCORENOC_DB`** — the SQLite file of v0.30.0 and earlier (#452). The message names the
  command that imports it into PostgreSQL, `python -m netcorenoc db import-sqlite <file>`; unset
  the variable once that is done. The maintenance CLI refuses it too, except that one command.

All three refuse rather than ignore, and the reason is worth stating: an ignored
`OPTICORR_ALLOWLIST` would mean every trap source is accepted while the operator believed otherwise,
and an ignored `NETCORENOC_DB` would start on an empty database that looks exactly like the whole
history lost. A silently ignored setting is a regression, not an inconvenience.
[`MIGRATION.md`](../MIGRATION.md) has the rename table and the import.

## Database

NetCoreNOC keeps its whole state in **PostgreSQL 15 or newer**, in one schema of one database
(#452). At every start the appliance creates the schema if it is missing — revoking its privileges
from `PUBLIC` — and applies the pending migrations, each in its own transaction and recorded with a
checksum in `schema_migrations`. Docker Compose runs a PostgreSQL for it and needs none of the
variables below; [`install.md`](install.md#the-database-on-the-same-host) creates a role and a
database on a host.

| Variable | Default | What it does, and what it costs |
|---|---|---|
| `NETCORENOC_DATABASE_URL` | `postgresql://netcorenoc@localhost:5432/netcorenoc` | The server, the role and the database. Query parameters go to the driver: `sslmode=require` encrypts, `sslmode=verify-full&sslrootcert=<file>` also verifies the server, `host=/var/run/postgresql` uses a Unix socket. Keep the password out of it — it is an environment variable — and use the file below |
| `NETCORENOC_DATABASE_PASSWORD_FILE` | *(unset)* | A file whose content is the role's password: a Docker or Kubernetes secret, a systemd credential (`LoadCredential=`). Read once at start, surrounding whitespace ignored, never copied into the environment. The log, the CLI's messages and the Settings screen show the URL with the password as `***` |
| `NETCORENOC_DB_SCHEMA` | `netcorenoc` | The schema the tables live in: lower-case letters, digits and `_`. **One appliance per schema**: the appliance holds a session advisory lock on it, and a second one started against the same schema stops at start. Two appliances may share a database in two schemas |
| `NETCORENOC_DB_READERS` | `4` | Connections, 1–64, that the console's reads run on besides the one writer. Each read is its own `REPEATABLE READ READ ONLY` snapshot and never waits for the engine's batch. The appliance holds at most 1 + this many connections |
| `NETCORENOC_DB_STATEMENT_CACHE` | `1024` | Prepared statements the driver (`asyncpg`) keeps per connection; `0` turns the cache off. Lowering it saves server memory and costs a parse per statement |

The maintenance CLI (`python -m netcorenoc …`) reads the same variables and **refuses a schema
that is not there** rather than creating it — an `audit verify` run against an empty schema would
report someone else's empty chain as intact. `db migrate` and `db import-sqlite` are the two
commands that create it.

What PostgreSQL does with the data — durability, vacuum, replication, point-in-time recovery — is
the server's configuration, not the appliance's. The bundled server's settings are in
`docker-compose.yml` (`command:`), sized for 1 GiB and commented.

### A managed PostgreSQL (Supabase and others)

`docker-compose.supabase.yml` runs the appliance alone against a database you already have: it
drops the bundled `postgres` and `db-secret` from the appliance's dependencies and gives it the
password as a Compose secret, `secrets/db_password.txt`. It needs Docker Compose 2.24 or newer.

```sh
mkdir -p secrets
sh -c 'umask 077; cat > secrets/db_password.txt'   # the database password, Enter, Ctrl-D
sudo chown 10001 secrets/db_password.txt            # the appliance's user in the container
# in .env, WITHOUT the password (Supabase: Project Settings -> Database -> Connection string):
# NETCORENOC_DATABASE_URL=postgresql://postgres.<project-ref>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require
docker compose -f docker-compose.yml -f docker-compose.supabase.yml up -d --build netcorenoc
```

Name the `netcorenoc` service as above: without it Compose also starts the bundled database, which
the appliance then does not use. `secrets/` is in `.gitignore` and `.dockerignore`, like `.env`.

**Which Supabase connection string:**

| String | Use it? |
|---|---|
| **Session pooler**, `…pooler.supabase.com:5432` | **Yes.** It works over IPv4 and keeps one server connection per client connection, which the appliance needs |
| Direct connection, `db.<project-ref>.supabase.co:5432` | Yes. IPv6 unless the project has the IPv4 add-on |
| Transaction pooler, port **6543** | **No.** It hands each transaction a different server connection; the appliance's instance lock is a session advisory lock and its statements are prepared once per connection, so both break |

**Any other managed PostgreSQL 15 or newer** — Neon, Amazon RDS, Google Cloud SQL, Azure Database
for PostgreSQL, your own cluster — works the same way: only `NETCORENOC_DATABASE_URL` changes. The
rule is the same too: a direct or session-mode endpoint, never a transaction-mode pooler (PgBouncer
with `pool_mode = transaction`). Without Docker, set `NETCORENOC_DATABASE_URL` and
`NETCORENOC_DATABASE_PASSWORD_FILE` directly.

* **TLS.** `sslmode=require` encrypts the connection. To also verify the server, mount the
  provider's CA certificate and use `sslmode=verify-full&sslrootcert=<path in the container>`.
* **Isolation.** The tables live in their own schema, which Supabase's Data API does not expose (it
  serves `public` and `graphql_public`), and the schema's privileges are revoked from `PUBLIC` when
  it is created: the project's `anon` and `authenticated` roles cannot read a row. Do not add
  `netcorenoc` to the exposed schemas.
* **Connections.** The appliance holds up to 1 + `NETCORENOC_DB_READERS` connections (5 by
  default), and a CLI command two or three more while it runs; check they fit the plan's limit.

## Stored settings — three classes, and one of them has no control

The **Settings** screen sorts every parameter into three visibly different classes, because they
answer different questions:

* **Mechanism** — yours to set, with the cost stated beside it. Retention horizons, capture sample
  rates, the allowlist.
* **Hardening-only** — you may make it **stricter and never looser**. The project floor is shown,
  and a looser value is refused with the reason: by the console before it is sent, and by the
  appliance if it is sent anyway. `resolved = max(project floor, deployment policy)`.
* **Structural** — a fact with no edit box. `seal: 0 queries` has no control because it is a
  guarantee, not a preference.

That third class is the one people ask about. A settings screen that let you edit a guarantee would
make it a preference, and the guarantee is the product.

## What the trap receiver accepts (v0.30.0)

**Settings → SNMP**, live — no restart. The default needs nothing: SNMPv1 and v2c traps are
accepted whatever their community, and SNMPv3 traps from the users configured there.

| Setting | Default | What it does, and what it costs |
|---|---|---|
| Versions | v1, v2c, v3 | A version switched off is refused into Quarantine with its reason |
| Communities | any | *Only these communities* refuses every other v1/v2c trap. A community is sent in clear text by the protocol, so this filters mistakes, not attackers; for that, use the allowlist or SNMPv3 |
| SNMPv3 users | none | Name, security level (noAuthNoPriv, authNoPriv, authPriv), authentication (MD5, SHA, SHA-224/256/384/512), privacy (DES, 3DES, AES-128/192/256 and Cisco's AES-192/256-C) and passphrases of 8+ characters. Accepted from every device configured with the user, or from one engine ID if pinned |
| Replay protection | on | Refuses a v3 trap more than 150 s behind its sender's clock. Switch off for equipment whose engine boots counter resets on reboot |

Privacy needs the optional `cryptography` package: `pip install "netcorenoc[snmpv3]"`. The Docker
image and the Nix package include it; without it an encrypted trap is quarantined as
`v3-privacy-unavailable`.

## Outgoing email (v0.30.0)

**Settings → Email** configures the SMTP server password-recovery links are sent through. Pick a
provider and its server, port and security are filled in; **Custom SMTP server** exposes host,
port, security (none, STARTTLS, TLS), sign-in, sender, certificate verification and a timeout.
**Send test** delivers one message with the saved settings and shows what the server answered.

| Provider | Server | What people get wrong |
|---|---|---|
| Gmail / Google Workspace | `smtp.gmail.com:587` STARTTLS | needs an **app password**, not the account's |
| Microsoft 365 / Outlook.com | `smtp.office365.com:587` STARTTLS | SMTP AUTH must be allowed for the mailbox |
| Yahoo / iCloud / Zoho | 465 TLS / 587 STARTTLS / 465 TLS | an app-specific password |
| SendGrid / Amazon SES / Mailgun | 587 STARTTLS | the service's SMTP credentials, and a verified sender |

Recovery is offered on the sign-in screen only when email is on, *Offer "Forgot your password?"*
is ticked and a console address for links is set — the address people use to reach the console,
which the link points at.

## Retention: three tiers, and lowering one deletes rows

The feedback dataset has its own retention, separate from `NETCORENOC_RETENTION_DAYS`:

| Tier | Default | Holds |
|---|---|---|
| `sink_days` | 21 days | Every evaluated pair, before anything is promoted or truncated |
| `sink_rows` | 2 000 000 | A row cap on the same tier |
| `training_days` | 365 days | Pairs promoted into the corpus by an operator label |
| `audit_days` | 730 days | The provenance a promoted pair's label rests on |

Three things about this table are load-bearing:

* **The ordering is enforced**, not advisory: `sink_days < training_days ≤ audit_days`. The sink
  holds rows whose destiny is undecided, and the corpus may not outlive its own provenance. A
  policy that breaks the order is refused.
* **The row cap almost certainly governs, not the 21 days.** At roughly 62 pair rows per trap, two
  million rows is about **nine hours at 1 trap/s**. Most deployments have hours of labelling
  window, not three weeks. Whoever raises the cap should also decide what `sink_days` is *for* once
  it can never bind.
* **Lowering a tier deletes rows and there is no undo.** So the endpoint previews by default, and
  both the preview and the change are audited.

```sh
python -m netcorenoc dataset stats     # rows, and the window you ACTUALLY have
python -m netcorenoc dataset retention # the tiers as they resolve right now
```

## Capture is on by default

Every evaluated pair is recorded at the moment of the decision, because none of it is recoverable
afterwards — `A` and `E` decay continuously, and an alarm row is overwritten on re-fire. Six months
not captured cannot be reconstructed, which is why the default is on.

It costs what it costs, and the appliance will tell you:

```sh
python -m netcorenoc dataset stats
make bias-report
```

**Capture is admin-only, everywhere.** It runs engine-side, where visibility scoping does not exist
and must not — correlation learns across the whole estate — so the dataset contains every network
element, entity and raw varbind, ungoverned by any scope policy. That makes it a bypass of the
visibility model by construction, and it is treated as one: no route below `admin` reads a dataset
row, on any path, in any format.

## What is deliberately not configurable

* **The evidence standard.** Sufficiency floors, the discrimination floor, the sealed holdout's
  query count. A deployment may make them stricter; nothing makes them looser. Mechanism is
  configurable; the standard of evidence is not.
* **The audit action catalog.** Frozen, with a completeness test. A new action is a decision, not a
  setting.
* **Anything that would need a build step.** No `package.json`, no bundler, no npm — and that is a
  test (`tests/ui/test_build_step.py`), not an intention.

## Retuning correlation

The three-term link score is a seam, not a constant, and an admin can retune it — see
[`correlation.md`](correlation.md#retuning-the-formula). Preview before you apply: a read-only
what-if re-partitions your own recent alarms under the candidate parameters and shows what would
merge and what would split, before anything is committed.
