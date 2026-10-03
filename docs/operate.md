# Operating NetCoreNOC

From the first sign-in to closing a situation. Commands assume Docker Compose. Elsewhere, replace the
`docker compose exec netcorenoc` prefix and point the command at the database — with the bundled
systemd unit: `sudo NETCORENOC_DB=/var/lib/netcorenoc/netcorenoc.db /opt/netcorenoc/.venv/bin/python -m netcorenoc …`.

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
rule. Then create one account per person under **People & access**:

| Role | Can |
|---|---|
| `viewer` | read everything they are allowed to see |
| `editor` | also work situations: promote, close, move, merge, split, clear, label |
| `admin` | also manage people, tokens, settings, models and the audit log |

Create a **second admin** straight away. Programs use **service tokens**, not passwords.

**Lost a password?** Another admin can reset it on **People & access**. If you are the only admin:

```sh
docker compose exec netcorenoc python -m netcorenoc admin reset-password admin
```

It prints a one-time password, signs that account out everywhere, and is recorded in the audit
log. If no enabled admin exists at all, restarting the appliance prints a new bootstrap password.

**Signing in through the API.** A first sign-in must send the new password in the same request;
without it the answer is `200 {"must_change_password": true}` and no session cookie:

```sh
curl -i -X POST http://localhost:8080/api/login -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<one-time>","new_password":"<your new password>"}'
```

## 2. Sending traps

Point each device's **SNMPv2c or v1 trap destination** at the appliance (UDP 162, or the port in
`NETCORENOC_TRAP_PORT`). Nothing is configured on this side: devices and trap types appear as their
traps arrive. Set `NETCORENOC_ALLOWLIST` to your equipment's networks so nothing else is accepted.

**Check it is receiving** — the health control in the top bar, or:

```sh
curl -b cookies http://localhost:8080/api/stats
```

| Field | Means |
|---|---|
| `devices`, `classes` rising | traps are arriving and being understood |
| `receiver.denied` > 0 | traps arrived from addresses outside the allowlist |
| `quarantined` > 0 | packets that were not valid traps; see **Quarantine** |
| `queue_depth` growing | the appliance is not keeping up; see [`troubleshoot.md`](troubleshoot.md) |

Times shown are when the **appliance received** each trap, in your browser's time zone.

## 3. Sending traps without equipment

The repository's tools send real SNMP traps to the appliance, so it can be tried with no network
devices. [`simulate.md`](simulate.md) has the steps for Linux and Windows. In short, from a clone:

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

Five trained models compete; a judge re-ranks them every few minutes and the best one decides.
**Settings → Correlation** shows which one is running. An admin can **pin** a model (a reason is
required and audited) or fall back to the built-in formula. The startup log names the deciding model:

```
grouping decided by random_forest:d2cdd3cd10c7; a repeat silent over 3600s is a new occurrence
```

**Judge** shows how each model measures. **Settings → Autonomy** lets a model act without a person
(start with *naming* or *severity*); it switches itself off when it disagrees with operators too
often, and **Stop autonomy** in the top bar stops it at once.

## 8. Backups, upgrades and checks

```sh
# a consistent copy while it runs (never overwrites an existing file)
docker compose exec netcorenoc python -m netcorenoc backup /home/netcorenoc/backup-$(date +%F).db
docker compose cp netcorenoc:/home/netcorenoc/backup-$(date +%F).db .

docker compose exec netcorenoc python -m netcorenoc audit verify    # is the audit log intact?
docker compose exec netcorenoc python -m netcorenoc --help          # every maintenance command
```

**Upgrade:** take a backup, then `git pull && docker compose up -d --build`. The database is
upgraded automatically at start and cannot be downgraded — the backup is your way back.
[`MIGRATION.md`](../MIGRATION.md) lists what each version changed.

The analysis reports for contributors (`make bias-report`, `make agreement-report`,
`make shadow-report`, `make census`) are described in [`correlation.md`](correlation.md).
