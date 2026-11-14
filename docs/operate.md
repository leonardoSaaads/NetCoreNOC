# Operating NetCoreNOC

From the first sign-in to closing a situation, and the NOC's cases and agents. Commands assume Docker Compose. Elsewhere, replace the
`docker compose exec netcorenoc` prefix and point the command at the database — with the bundled
systemd unit, whose database URL is the default:
`sudo NETCORENOC_DATABASE_PASSWORD_FILE=/etc/netcorenoc/db_password /opt/netcorenoc/.venv/bin/python -m netcorenoc …`.

## 1. First sign-in

On its first start the appliance creates the user `admin` and prints its password **once**:

```
======================================================================
  NetCoreNOC bootstrap admin created (first run)
      username: admin
      password: DpZj2epK1JRLivrFcS2b
  Sign in and change this password immediately. It is shown ONCE.
======================================================================
```

```sh
docker compose logs netcorenoc | grep -A4 "bootstrap admin"    # Compose
journalctl -u netcorenoc | grep -A4 "bootstrap admin"          # systemd
```

Open `http://<host>:8080/`, sign in, and choose a new password: **12 to 128 characters**, no other
rule. Then set up **two-factor sign-in** — required for every account (ADR #482):

1. The password card moves aside and a panel opens beside it with a **QR code**. Scan it with an
   authenticator app (Google Authenticator, Authy, 1Password, Microsoft Authenticator…). No camera?
   "Can't scan? Type a key instead" shows the key as text.
2. **Keep the ten recovery codes** (Copy or Save .txt), then tick "I saved my recovery codes". Each
   works once, instead of a code, when the phone is lost. They are **hidden by default** — someone
   behind you could read them: Copy and Save work without showing them; "Show codes" first draws
   dots and a warning, and only "I'm ready — show them" puts them on screen. The typed key works
   the same way.
3. Type the **6-digit code** the app shows. Only then is the session opened.

From then on every sign-in is the password, then the code. Then create one account per person
under **People & access**:

| Role | Can |
|---|---|
| `viewer` | read everything they are allowed to see |
| `editor` | also work situations: promote, close, move, merge, split, clear, label |
| `admin` | also manage people, tokens, settings, models and the audit log |

Create a **second admin** straight away. Programs use **service tokens**, not passwords.

**Lost the phone?** Sign in with a recovery code ("Lost your phone? Use a recovery code"), then
**Your account → Security → Move to a new phone**. Lost the recovery codes too: another admin
resets your two-factor on **People & access** (you set it up again at the next sign-in). The only
admin uses the CLI: `docker compose exec netcorenoc python -m netcorenoc admin reset-two-factor admin`.

**Lost a password?** Another admin can reset it on **People & access**. If you are the only admin:

```sh
docker compose exec netcorenoc python -m netcorenoc admin reset-password admin
```

It prints a one-time password, signs that account out everywhere, and is recorded in the audit
log. If no enabled admin exists at all, restarting the appliance prints a new bootstrap password.

**Signing in through the API** takes two requests (ADR #482). The password step answers with a
challenge and sets no cookie; the code step opens the session:

```sh
curl -s -X POST http://localhost:8080/api/login -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<your password>"}'
# {"must_change_password": false, "second_factor": "verify", "challenge": "<challenge>", ...}
curl -c cookies -X POST http://localhost:8080/api/login/code -H 'Content-Type: application/json' \
  -d '{"challenge":"<challenge>","code":"<6 digits from your app>"}'
```

A first sign-in sends `new_password` with the first request; its challenge is `"enroll"` and
carries the QR code, the key and the recovery codes. Programs should use **service tokens**, which
have no password and no second factor.

## 2. Sending traps

Point each device's **SNMPv2c or v1 trap destination** at the appliance (UDP 162, or the port in
`NETCORENOC_TRAP_PORT`). Nothing is configured on this side: devices and trap types appear as their
traps arrive. Set `NETCORENOC_ALLOWLIST` to your equipment's networks so nothing else is accepted.

**SNMPv3** needs one thing on this side: the user, under **Settings → SNMP** (name, security level,
protocols, passphrases). The form prints the Cisco IOS lines and a Net-SNMP test command for it.
One user serves every device configured with it; the appliance localizes the keys to each device's
engine ID itself.

**Check it is receiving** — the health control in the top bar, or:

```sh
curl -b cookies http://localhost:8080/api/stats
```

| Field | Means |
|---|---|
| `devices`, `classes` rising | traps are arriving and being understood |
| `receiver.denied` > 0 | traps arrived from addresses outside the allowlist |
| `quarantined` > 0 | packets that were not valid traps; see **Quarantine** |
| `queue_depth` growing | the backlog — traps spooled and not yet classified — is growing: classification is behind the trap rate. Nothing is lost while the spool has room; the Overview's **Trap pipeline** shows how far behind, against the KPI. See [`troubleshoot.md`](troubleshoot.md) |
| `classification.over_limit` > 0 | traps were classified later than the KPI's limit (still classified, never discarded) |

Times shown are when the **appliance received** each trap, in your browser's time zone.

## 3. Sending traps without equipment

The repository's tools send real SNMP traps to the appliance, so it can be tried with no network
devices. [`simulate.md`](simulate.md) has the steps for Linux and Windows, and for the database the
appliance needs. In short, from a clone, with `NETCORENOC_DATABASE_URL` set:

```sh
NETCORENOC_TRAP_PORT=1162 .venv/bin/python -m netcorenoc.main &
make replay-list                     # every scenario
make replay SCENARIO=olt_storm       # send one
```

Run the appliance **without Docker** for this: Docker's port proxy rewrites the source address of
local traffic, so every simulated device would look like one.

## 4. How alarms repeat, clear and come back

An **alarm** is one device + trap type + instance (usually the port or object the trap names).
These rules decide what a new trap does to it:

| What arrives | What happens |
|---|---|
| The same trap again, while the alarm is active | **A repeat.** The count goes up; the situation stays as it is. A device re-sending every few minutes is one alarm in one situation |
| A clear: `linkUp` for a `linkDown`, a trap whose severity says `cleared`, or a clear the appliance learned from your traffic | The alarm **clears**. Its situation is kept 5 minutes in case it bounces, then resolves as *every alarm cleared* |
| The trap again after its clear | The alarm is active again. Within those 5 minutes it rejoins the same situation |
| The trap again after **more than an hour of silence**, with no clear in between, when it is the only thing still active in its situation | **A new occurrence**: the clear was probably lost. The old situation resolves (*it went quiet*) and the trap opens a **new situation**. The window is `NETCORENOC_REARM_S` (3600 s; `0` turns this off) |
| The trap again after an operator **closed** its situation, without a clear | **A new occurrence** too: the fault is still being reported, so it opens a new situation |
| A port that goes down and up repeatedly | After its second bounce in an hour, its situation is kept while it keeps bouncing, so the bounces stay together. After about six regular bounces it is marked **flapping** and its further bounces stop creating work |
| A reboot (`coldStart`, `warmStart`), an authentication failure, a configuration change, a UPS on battery | These report an **event** and never send a clear. The alarm ends after 5 minutes without a repeat, and its situation resolves |

A fault whose device never sends a clear (a dying gasp, for example) stays active, and its
situation stays live, until an operator **clears the alarm by hand** or closes the situation.
**Close** says "we are done with this"; **clear** says "this alarm is over" — a later trap after a
close opens a new situation, a later trap after a clear is a new activation.

## 5. Working a situation

| State | Means | Moves on when |
|---|---|---|
| **New** | grouped by the appliance; nobody has looked at it | an editor works it (promote, label, move, merge, split, clear) → **Open** |
| **Pending** | the model would add these alarms to an **Open** situation, and waits for a person | **Accept** merges them; **Reject** makes it a New situation of its own |
| **Open** | the team is handling it | its alarms clear, it goes quiet, or someone closes it → **Resolved** |
| **Resolved** | over; the card says why | — |

Expand a card for the probable root cause, the member alarms and **Why these were grouped**: one row
per link, with its score and the contribution of each reason. A model never grows an **Open**
situation by itself.

**Teach it when it is wrong.** **Move** an alarm to where it belongs, **Split** out the ones that do
not belong, **Merge** two situations that are one event, or **Confirm** a grouping. Each records how
sure you said you were; below 50 % the action still happens but teaches nothing. Clearing an alarm
by hand and renaming a situation teach nothing about grouping.

## 6. Planned work

Before taking equipment down, declare a **maintenance window** (**Operations → Maintenance**): the
elements, the interval and the site's time zone. Traps from those elements are not collected while
it runs, except what its rules let through. Windows over six hours wait for an editor or admin to
confirm them. A window can report, when it closes, the faults that started inside it and never
cleared — check for that situation before calling the job done. Details:
[`console.md`](console.md#maintenance-declaring-planned-work-v0210).

## 7. Which model decides

Seven trained models compete; a judge re-ranks them every few minutes and the best one decides.
**Settings → Correlation** shows which one is running. An admin can **pin** a model (a reason is
required and audited) or fall back to the built-in formula. The startup log names the deciding model:

```
grouping decided by random_forest:d2cdd3cd10c7; a repeat silent over 3600s is a new occurrence
```

**Judge** shows how each model measures. **Settings → Autonomy** lets a model act without a person
(start with *naming* or *severity*); it switches itself off when it disagrees with operators too
often, and **Stop autonomy** in the top bar stops it at once.

## 8. Backups, upgrades and checks

The PostgreSQL schema is the whole state. Back it up with `pg_dump`, which reads one consistent
snapshot while the appliance runs. With Compose it runs in the database's container — the
appliance's image carries no PostgreSQL client — with the generated password read from its file:

```sh
docker compose exec -T postgres sh -c 'PGPASSWORD="$(cat /run/netcorenoc-secrets/db_password)" \
  exec pg_dump --format=custom --schema=netcorenoc -U netcorenoc netcorenoc' > nc-$(date +%F).dump
```

Elsewhere, with the PostgreSQL client tools (`pg_dump`, `pg_restore`) beside the CLI:

```sh
python -m netcorenoc backup nc-$(date +%F).dump    # never overwrites an existing file
```

It dumps the schema in PostgreSQL's compressed custom format, hands `pg_dump` the password in its
environment rather than on a command line `ps` can read, and reads the file back with
`pg_restore --list` before it says `ok`. A managed provider's own backups (Supabase, RDS and the
rest) are theirs to restore; a dump is the copy you hold.

**Back up the secret key too — once, and apart from the dumps** (ADR #453). It seals personal data
and secrets in the database and keys every password hash: a dump restored without it has unreadable
values and no password that signs in. It never changes, so one copy, kept where the dumps are not,
is enough:

```sh
docker compose cp netcorenoc:/home/netcorenoc/secret.key ./netcorenoc-secret.key
chmod 600 ./netcorenoc-secret.key     # then move it to your secrets vault, not beside the dumps
```

Elsewhere it is `secret.key` in `NETCORENOC_DATA_DIR`, or `NETCORENOC_SECRET_KEY_FILE`. Restoring
onto a new host means restoring this file before the first start: the appliance refuses a key that
is not the one that sealed its database.

**And the secrets key, apart from both** (ADR #455). The NOC's device credentials are sealed under
it as well as under `secret.key`; without it they cannot be read, and have to be stored again. Keep
it somewhere neither the dumps nor the `secret.key` copy are:

```sh
docker compose cp netcorenoc:/var/lib/netcorenoc-vault/secrets.key ./netcorenoc-secrets.key
chmod 600 ./netcorenoc-secrets.key    # a third place: not beside the dumps, not beside secret.key
```

Elsewhere it is `NETCORENOC_SECRETS_KEY_FILE` (default `secrets.key` in the data directory; the
systemd unit's `/var/lib/netcorenoc-vault/`).

**Restore** replaces the schema: stop the appliance, drop the schema, restore the dump, start.

```sh
docker compose stop netcorenoc
docker compose exec -T postgres sh -c 'PGPASSWORD="$(cat /run/netcorenoc-secrets/db_password)" \
  exec psql -U netcorenoc -d netcorenoc -c "DROP SCHEMA netcorenoc CASCADE"'
docker compose exec -T postgres sh -c 'PGPASSWORD="$(cat /run/netcorenoc-secrets/db_password)" \
  exec pg_restore -U netcorenoc -d netcorenoc' < nc-2026-10-18.dump
docker compose start netcorenoc
```

Elsewhere, `pg_restore --dbname=<database URL> <file>` into a database where the schema is absent.

**Checks:**

```sh
docker compose exec netcorenoc python -m netcorenoc db status       # server, schema version, size
docker compose exec netcorenoc python -m netcorenoc audit verify    # intact, and sealed by our key?
docker compose exec netcorenoc python -m netcorenoc --help          # every maintenance command
```

`db status` names the server, the database, the role and the schema it reached, then the schema's
version beside this release's — `version 45 (this release: 45)` — and the schema's size on disk. A
lower first number is migrations not yet applied (start the appliance, or run `db migrate`); a
higher one is a database a newer release migrated, which the console also warns about.

**Upgrade:** take a backup, then `git pull && docker compose up -d --build`. The database is
upgraded automatically at start and cannot be downgraded — the backup is your way back. From
v0.30.0 or earlier, the SQLite file is imported once, before the first start: see
[`MIGRATION.md`](../MIGRATION.md), which also lists what each version changed.

The analysis reports for contributors (`make bias-report`, `make agreement-report`,
`make shadow-report`, `make census`) are described in [`correlation.md`](correlation.md).

## 9. The NOC: customers, cases and AI agents

Switch the top bar to **NOC**. **The case comes from you** (ADR #480): in the Broker, confirm or
correct a situation's grouping and it is **Open**; when it is the one the NOC should work — of ten
situations a storm opens, the root cause — press **Start working this** on it. Its case enters the
NOC queue with its priority (from the Broker's severity) and its clocks — the take, the customer
update and the restore ([`console.md`](console.md#noc--work)). People and agents work it the same
way: take it, post to its feed (*Internal*, *Specified* or *All with access*), fill and send the
**customer update**, set it **pending** while someone outside the NOC must act (the restore clock
pauses), and resolve it.

**Each customer first** (ADR #481). Under **Customers**, add the customer (an admin) and put its
elements in it from each element's page; then fill its book: **Instructions**, **Workbook**,
**Additional docs**, **Circuits**, and

* **Access** — a credential for each of its devices: SSH, SNMP, **Telnet over TLS** (or plain
  Telnet for equipment with nothing else) or an API, then **Let agents use it** (a custodian confirms
  with their password); and its mail server — an SMTP and an IMAP credential;
* **Email** — the recipients of the customer update, the sender, and which of its credentials
  sends (or the appliance's server, Settings → Email) and which inbox is read for replies.

A sent customer update is then emailed to the customer, its subject tagged with the case; the
customer's reply lands on the case's feed, and a case waiting on the customer goes back to the
queue for whichever agent at its tier has the fewest cases.

AI agents are optional. The NOC's **Overview** lists what is missing, each item a link:

1. **Agents → AI providers**: connect a model — any OpenAI-compatible API, a local Ollama (no key)
   or Claude. From the Compose container a local Ollama is `host.docker.internal`, port 11434;
   start Ollama with `OLLAMA_HOST=0.0.0.0` so the container can reach it.
2. **Agents → Hire an agent**: a name and a model are enough. Its job comes prefilled; the first
   agent hired takes cases from the queue, and **Organization** places agents in tiers to escalate
   through.
3. **Customers → a customer → Access**: its devices' credentials, switched on for agents. Until
   then an agent says it has no access and sets the case *Pending connectivity*; it is woken the
   moment access is granted.

An agent never receives a password: it leases a short-lived token for one device and the appliance
runs the read-only command for it. Who stored, read or changed a credential, and which agent leased
and used it, is in the audit log ([`security.md`](security.md#the-credential-vault-noc--each-customers-access)).
Credentials stored before they belonged to a customer are under **Vault access → Not assigned**:
open each and choose its customer with **Change…**.

## 10. Before a team relies on it

| Step | How |
|---|---|
| Only accept traps from your equipment | `NETCORENOC_ALLOWLIST=10.0.0.0/8,192.0.2.10` in `.env` |
| Serve HTTPS | `NETCORENOC_TLS_CERT` / `NETCORENOC_TLS_KEY`, or a TLS reverse proxy — [`security.md`](security.md) |
| One account per person | **People & access**: `viewer` (read), `editor` (work situations), `admin` (everything). A visibility scope narrows what someone sees, but it is **not tenant isolation** — [`security.md`](security.md) |
| A second admin | so a forgotten password never locks the team out |
| Password recovery by email | **Settings → Email** and a recovery address per person |
| Accept only what you expect | **Settings → SNMP**: switch off unused versions, list communities, or require SNMPv3 users |
| Programs use service tokens | **People & access → Service tokens**; the value is shown once |
| Back up the database and both key files | section 8 above |
| Cannot use port 162 | `NETCORENOC_TRAP_PORT=1162` in `.env`, then `docker compose up -d` — [`install.md`](install.md) |

