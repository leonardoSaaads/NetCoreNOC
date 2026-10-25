# Troubleshooting

Symptom first, then what to check. Commands assume Docker Compose. Elsewhere, replace the
`docker compose exec netcorenoc` prefix and point the command at the database — with the bundled
systemd unit, whose database URL is the default:
`sudo NETCORENOC_DATABASE_PASSWORD_FILE=/etc/netcorenoc/db_password /opt/netcorenoc/.venv/bin/python -m netcorenoc …`.

## Signing in

### I missed the first-run password

```sh
docker compose logs netcorenoc | grep -A4 "bootstrap admin"
```

It is printed as a block; the line you need reads `password: …`. If the logs were rotated away, use
the reset below.

### I forgot my password

If an admin has set up email (**Settings → Email**) and your account has a recovery address, use
**Forgot your password?** on the sign-in screen: the link works once, for 30 minutes, and signs the
account out everywhere when used. Otherwise another admin resets it on **People & access**. If you
are the only admin:

```sh
docker compose exec netcorenoc python -m netcorenoc admin reset-password <username>
```

It prints a one-time password, signs that account out everywhere, and writes an audit row.

### The recovery email does not arrive

The sign-in screen answers the same sentence whether or not an account matched, so check here
instead: the account needs a recovery address (People & access, or Your account), the request is
an audit row (`password.reset.request`, `denied` when nothing matched), and one link per account is
sent every two minutes. **Settings → Email → Send test** shows exactly what the mail server said;
Gmail, Microsoft 365, Yahoo and iCloud refuse the account password and need an app password. A
failed send is a warning line in the log naming the account, never the link.

### No admin account is left

Restart the appliance and read the log: whenever no **enabled** admin exists, the start prints a new
bootstrap password, as on first run. It uses the name `admin`, or `recovery-admin` if `admin`
exists but is not an admin. A disabled admin does not count — check `role` and `disabled`. The
appliance refuses to demote, disable or delete the last enabled admin, so this is rare.

### `POST /api/login` returns 200 and sets no cookie

The account must change its password, and the new one has to be in the **same** request
(`new_password`). [`operate.md`](operate.md#1-first-sign-in) shows the request.

### Sign-in works with `curl` but not in the browser, behind a proxy

The console checks `Origin` against `Host`. A reverse proxy that rewrites `Host` or drops `Origin`
makes a correct password look rejected. Make the proxy preserve both.

### A password is refused

The only rule is length: 12 to 128 characters.

## Traps

### `devices` and `classes` stay at zero

```sh
curl -b cookies http://localhost:8080/api/stats | python3 -m json.tool
```

* **`receiver.received` is 0** — nothing reaches the appliance. Check the device's trap destination
  and port, and that firewalls pass **UDP**. The start-up log line `listening for traps on …`
  shows the port in use. In Docker the port must be published as `/udp`; with Compose, set
  `NETCORENOC_TRAP_PORT` in `.env` and both the mapping and the process follow it.
* **`received` climbs but `devices` does not** — the traps are refused: check `denied` (allowlist)
  and `quarantined` (not a valid trap).

### Every device shows the same address

Something between the devices and the appliance rewrites the source address:

* **Docker on the same machine.** Traps sent from the host itself (for example `make replay`) go
  through Docker's port proxy and arrive from the bridge gateway. Replay without Docker, or from
  another machine.
* **Docker Desktop (macOS, Windows)** rewrites every source. Run the appliance on a Linux host.
* **A NAT or trap relay** in the path. Send traps directly, or relay without rewriting the source.

### `denied` keeps climbing

The sender is outside `NETCORENOC_ALLOWLIST` (comma-separated CIDRs). The source is the datagram's
real source address, which behind NAT is not the device's.

### `quarantined` keeps climbing

Open **Quarantine**: each entry says why it was refused, and **Settings → SNMP** counts the
refusals by reason since the start. Common causes:

| Reason | What to check |
|---|---|
| `v3-unknown-user` | the SNMPv3 user is not configured on Settings → SNMP, or its name differs |
| `v3-authentication-failed` | the authentication passphrase or protocol differs from the device's |
| `v3-decryption-failed` | the privacy passphrase or protocol differs (AES-256 vs Cisco's AES-256-C) |
| `v3-not-in-time-window` | a replay, or a device whose engine boots counter resets on reboot — fix it, or switch replay protection off |
| `v3-privacy-unavailable` | an encrypted trap on an install without `netcorenoc[snmpv3]` (the Docker image has it) |
| `community-not-accepted` | Settings → SNMP accepts only listed communities, and this is not one |
| `snmp-v1-not-accepted` / `v2c` / `v3` | that version is switched off on Settings → SNMP |

Truncated packets and oversized values are the other usual causes. Reading this list is audited.

### The process will not start and names a variable

Every unreadable setting stops the start with one sentence naming it, for example:

```
NETCORENOC_TRAP_PORT='' is not a whole number of a UDP port. Unset it to use the default (162), …
NETCORENOC_TLS_CERT is set and NETCORENOC_TLS_KEY is not. Built-in TLS needs both; …
```

A blank line in `.env` is the usual cause. `NETCORENOC_API_TOKEN`, any `OPTICORR_*` variable and
`NETCORENOC_DB` are refused on purpose: they were removed, and the message names the replacement.
The database's own refusals are under [The database](#the-database).

### Permission denied binding UDP 162

Port 162 needs `CAP_NET_BIND_SERVICE` (the Compose file and the systemd unit grant it). Or set
`NETCORENOC_TRAP_PORT=1162` and point the equipment at 1162.

## Situations

### The same alarm opened a new situation

Expected when the alarm comes back after **more than an hour of silence** with no clear, or after
an operator **closed** its situation. The old situation's history says *"its alarm went quiet
without a clear and was raised again"*. To keep repeats in one situation regardless of silence,
set `NETCORENOC_REARM_S=0`. The rules: [`operate.md`](operate.md#4-how-alarms-repeat-clear-and-come-back).

### A situation never resolves

One of its alarms is still active: its device never sent a clear (common for dying-gasp and some
vendor alarms). Clear that alarm by hand on the card, or close the situation. Reboot and
authentication-failure traps end by themselves after 5 minutes.

### A cleared port's situation stays on the board

The port bounced at least twice within the hour, so its situation is kept until the port has been
quiet for an hour — the next bounce would join it instead of opening another situation.

### Alarms that belong together are not grouped

Check which model decides (**Settings → Correlation**, or the start-up log line `grouping decided
by …`) and read *Why these were grouped* on the card. If grouping is consistently wrong for your
network, **Move**, **Merge** and **Split** teach it, and an admin can pin another model. Without a
model (the built-in formula), two alarms on different devices group only once the appliance has
learned they co-occur: [`correlation.md`](correlation.md#cold-start-honestly).

### Everything joined one huge situation

Usually a real storm — during a mass outage everything does co-occur. **Split** it; that is the
signal that teaches the appliance.

## Load and storage

### `queue_depth` grows and does not come back down

Classification is behind the trap rate; the backlog waits **in the spool on disk** and nothing is
lost while it has room (`NETCORENOC_SPOOL_MAX_MB`). The Overview's **Trap pipeline** shows the
oldest wait and how many traps passed the KPI. Give the container more CPU; `make storm` measures
what a host sustains. If the spool fills or the disk refuses writes, new traps are counted as
dropped, a warning names the cause and `/readyz` turns not-ready.

### "trap record(s) could not be classified and were set aside"

A trap crashed the engine, or a spool record was not a valid event: it is kept in
`<spool>/dead-letter.jsonl` (exact bytes, base64) with the reason, and every other trap was
classified. The log names each one. Once the cause is fixed, the record can be appended to a spool
again.

### `ingest_gaps` is not empty

The appliance knows it missed traffic, and when. Investigate the network path and the load.

### "batch(es) failed to persist due to a storage error"

The database could not be written: the server is down, restarting or failing over, the connection
dropped, or the server's disk or connection slots are full. The appliance keeps running,
reconnects, retries the same batch with backoff, and resumes by itself. No trap is dropped: they
wait in the spool (ADR #450). Check the server — `db status`, and its own log
(`docker compose logs postgres`).

## The database

### The appliance will not start: "NETCORENOC_DATABASE_URL=… could not be opened"

The message carries the driver's reason after the URL, with the password shown as `***`:

| The reason says | What to check |
|---|---|
| `Connect call failed`, `Connection refused`, a name that does not resolve | The server is not running, or the host or port is wrong. With Compose the appliance waits for PostgreSQL's health check and reaches it as `postgres` on the internal network; from the host the database is deliberately unreachable |
| `password authentication failed` | The password file and the role disagree. With Compose: the `netcorenoc-secrets` volume was recreated while `netcorenoc-pgdata` kept the old password — PostgreSQL applies its password file only when it creates the database. Restore that volume; without a copy, the role's password has to be reset inside the database container to the new file's content |
| `database "…" does not exist`, `role "…" does not exist` | Create them ([`install.md`](install.md#the-database-on-the-same-host)), or fix the URL |
| `permission denied for database` | The schema is missing and the role may not create one. Make the role the database's owner, grant it `CREATE` on the database, or create the schema for it (`CREATE SCHEMA netcorenoc AUTHORIZATION <role>`) |
| `SSL`, `certificate` | `sslmode` in the URL: `require` to encrypt, `verify-full` with `sslrootcert` to verify the server ([`configure.md`](configure.md#database)) |

`PostgreSQL 14 is too old: NetCoreNOC needs 15 or newer` means what it says: upgrade the server.

### "another NetCoreNOC is already using schema … of this database"

**One appliance per schema**: the first holds an instance lock (a session advisory lock) on it for
as long as it runs, and a second one stops at start rather than write the same history twice. To
run two appliances on one database, give each its own `NETCORENOC_DB_SCHEMA`.

If no other appliance is running, the server is still holding the session of one that vanished
without closing it — a host that lost power, a cut network — and lets the lock go when it notices.
To end that session at once, as a role allowed to:

```sql
SELECT pid, client_addr, backend_start FROM pg_stat_activity WHERE application_name = 'netcorenoc';
SELECT pg_terminate_backend(<pid>);
```

The maintenance CLI never takes the lock, so it runs beside the appliance.

### "pending migrations … run python -m netcorenoc db migrate"

The appliance's role does not own its schema (ADR #453) and so may not migrate it. With Compose,
`db-migrate` runs first and does it — check its log (`docker compose logs db-migrate`). Elsewhere,
run `db migrate` with the owner's URL and `NETCORENOC_DB_RUNTIME_ROLE` set to the appliance's role,
or give the appliance `NETCORENOC_DATABASE_OWNER_URL`
([`configure.md`](configure.md#two-roles-one-owns-the-schema-the-appliances-owns-nothing)).

### The secrets key (NOC → Secrets)

**"a credential sealed with another secrets key"** on a reveal or a change — the appliance was given
another `secrets.key` than the one the vault was written with (a new `netcorenoc-vault` volume, a
restore onto a new host, a changed `NETCORENOC_SECRETS_KEY_FILE`). Restore the original file (your
backup, `operate.md` §8) and restart. Nothing else is affected: the broker never reads the vault.

**"a sealed credential failed authentication"** — the row no longer matches what it was sealed for:
its host, port, kind, protocol version or access level was changed in the database rather than
through the console. That is the seal doing its job; restore the row from a backup, or store the
credential again.

**The secrets key is lost for good.** The stored credentials cannot be recovered. As the schema owner,
`DELETE FROM netcorenoc.noc_secret` (their checks and leases go with them), start with a new key, and
store the credentials again; the audit history of the old ones stays.

### The secret key

**"this database was sealed with secret key … and the key file holds key …"** — the appliance was
given another key file than the one that sealed the database: a new data volume, a restore onto a
new host, a changed `NETCORENOC_SECRET_KEY_FILE`. Restore the original `secret.key` (your backup,
`operate.md` §8) and start again. Nothing was changed by the refused start.

**"… may be read by other users; chmod 600"** — the key file's mode lets anyone on the host read
it. `chmod 600` it (or `440` for a secret mounted with a group).

**The key is lost for good.** Personal data and secrets in the database cannot be recovered, and no
password signs in. Recover the appliance, not the values: stop it; move `meta` rows `vault.kid` and
`audit.sealed_from` aside as the schema owner (`DELETE FROM netcorenoc.meta WHERE key IN
('vault.kid', 'audit.sealed_from', 'config.email', 'config.snmp', 'community_hmac_key')`); clear the
sealed columns (`UPDATE netcorenoc.app_user SET email=NULL, email_index=NULL, display_name=NULL`;
`DELETE FROM netcorenoc.user_avatar`); start it with a new key; then set each password with
`python -m netcorenoc admin reset-password <user>`, and re-enter Settings → Email and SNMP. The
seals the lost key wrote cannot be checked any more: `audit verify` stops at the first of them —
keep that report with the incident's record; the hash chain itself is unchanged.

### The CLI says "no NetCoreNOC database at … has no NetCoreNOC tables"

The CLI **refuses a schema that is not there** instead of creating it, so a command pointed at the
wrong database fails instead of answering about an empty one. Usually the variables are not the
appliance's: run it where they are set (`docker compose exec netcorenoc python -m netcorenoc …`),
or set `NETCORENOC_DATABASE_URL`, `NETCORENOC_DATABASE_PASSWORD_FILE` and `NETCORENOC_DB_SCHEMA` as
the appliance has them. On a new installation, start the appliance once or run
`python -m netcorenoc db migrate`.

### "NETCORENOC_DB=… names a SQLite database"

The variable of v0.30.0 and earlier is still set. If the file has not been imported yet, follow
[`MIGRATION.md`](../MIGRATION.md); then unset it — with systemd it may sit in a drop-in
(`systemctl cat netcorenoc` shows them). Only `db import-sqlite` runs while it is set.

### A warning: "Migration … changed after it was applied to this database"

Each applied migration is recorded with its SHA-256 in `schema_migrations`. This warning — in the
console, not a stop, so traps keep flowing — means the file installed now differs from the one
applied: a modified installation or a pre-release build. Reinstall the release. Its sibling, *"The
database is at schema N, newer than this release's M"*, means a newer release migrated this
database: run that release, or restore the backup taken before it.

### PostgreSQL logs "page verification failed, calculated checksum … but expected …"

The bundled server keeps **data checksums**, so a page the disk corrupted is refused when it is read
(`invalid page in block …`) rather than returned as data, and the statement that read it fails.
Stop both containers, check the disk under the `netcorenoc-pgdata` volume, and restore the last
dump ([`operate.md`](operate.md#8-backups-upgrades-and-checks)) onto healthy storage.

## The console

### `make dom` says "skipped"

Node 22 or newer is not on `PATH`. The console tests skip rather than fail; install Node.

## When you are stuck

```sh
docker compose exec netcorenoc python -m netcorenoc db status      # which database, which schema
docker compose exec netcorenoc python -m netcorenoc audit verify   # is the audit log intact?
docker compose logs --tail 200 netcorenoc                          # what the appliance said
docker compose logs --tail 200 postgres                            # what the database said
make checksums                                                     # are the console files intact?
```

Then open an issue with the output and the steps that led there.
