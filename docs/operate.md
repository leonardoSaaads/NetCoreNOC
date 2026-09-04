# Operating NetCoreNOC

From a fresh install to reading why two alarms were grouped. Everything below was run against a real
appliance; the outputs are what it actually printed.

## 1. First boot

On its first start NetCoreNOC creates one account and prints its password **once**, in a banner:

```
======================================================================
  NetCoreNOC bootstrap admin created (first run)
      username: admin
      password: DpZj2epK1JRLivrFcS2b
  Sign in and change this password immediately. It is shown ONCE.
======================================================================
```

Where to find it depends on how you started it:

```sh
docker compose logs netcorenoc | grep -A4 bootstrap
docker logs netcorenoc | grep -A4 bootstrap
journalctl -u netcorenoc | grep -A4 bootstrap
```

If you missed it and no other admin exists, the recovery is to stop the process, move the database
file aside, and start again — a new bootstrap admin is created for a new database. Everything
learned is in that file, so this is a real cost, not a reset button.

## 2. Signing in — the one detail that looks like a bug

**The bootstrap admin must supply a new password in the same request that signs it in.** Not
afterwards, not on a second screen: in the same `POST /api/login`.

In the browser this is invisible — the login form asks for a new password on first sign-in and does
the right thing. Against the API it is not invisible at all, and it has been reported as a defect
more than once. Here is exactly what happens:

```sh
# The way everybody tries first — password only:
curl -i -X POST http://localhost:8080/api/login -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<the bootstrap password>"}'

HTTP/1.1 200 OK
{"must_change_password":true}
```

**200, no error, and no `Set-Cookie`.** That is correct, and it is the whole trap: a success status
with no session. The server is telling you the credential is right and the account is not usable
until the password changes.

```sh
# With new_password in the SAME post:
curl -i -X POST http://localhost:8080/api/login -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<the bootstrap password>","new_password":"a-long-enough-password-1"}'

HTTP/1.1 200 OK
set-cookie: netcorenoc_session=…; HttpOnly; Path=/; SameSite=strict
{"user":"admin","role":"admin","must_change_password":false}
```

The password policy is **length only** — 12 to 128 characters, no composition rules, no forced
expiry — following NIST SP 800-63B. A rejected password comes back as a 400 naming the rule.

After that, create per-operator accounts under **Users** with the least role each needs, and issue
**service tokens** for anything non-interactive. A token's value is shown once.

## 3. Sending traps

Point your equipment's SNMPv2c (or SNMPv1) trap destination at the appliance. There is nothing to
configure on this side: no MIBs to load, no inventory to import, no topology to declare. A trap
whose OID the appliance has never seen becomes an alarm class the first time it arrives.

No equipment handy? Send real SNMP PDUs over UDP from the bundled scenarios:

```sh
make replay                       # a two-NE fibre cut
make sim SCENARIO=login_burst     # python tools/trap_sim.py --list for the rest
```

```
sent 8 traps in 7.50s (1/s)
```

## 4. Checking it is receiving

```sh
curl -b cookies http://localhost:8080/api/stats
```

```json
{"devices":2,"classes":4,"active_alarms":8,"open_situations":1,"quarantined":0,
 "ingest_gaps":[],"latency_p95_s":0.0053,"queue_depth":0,
 "warnings":["Trap allowlist is empty: all sources are accepted. Set an allowlist to enforce.", …]}
```

Read it in this order:

* **`devices` and `classes` climbing from zero** is the appliance discovering your network. Neither
  was configured.
* **`quarantined` above zero** means datagrams the parser refused. Look at them — the **Quarantine**
  screen shows why, and reading that list is audited.
* **`warnings`** are the things an admin should fix. The two above are the zero-config defaults
  telling you honestly what they cost.
* **`queue_depth` growing and not falling back** is the one number that means the appliance is not
  keeping up. See [`troubleshoot.md`](troubleshoot.md).
* **`receiver.denied` above zero** means datagrams arrived and the allowlist refused them. It is
  also a banner on every screen, naming the count and the allowlist that refused them.

All of the above are on the **Overview**'s *System health* section since v0.15.2, with a trap rate
derived between two polls and labelled with the window it covers. There is no CPU, memory or uptime
figure, because the appliance does not measure one.

## 5. Reading a situation

A **situation** is a connected component of the link graph — a group of alarms the appliance
believes are one event.

```sh
curl -b cookies http://localhost:8080/api/situations
```

```json
[{"id":1,"status":"open","root_alarm_id":1,"alarm_count":8},
 {"id":2,"status":"merged","root_alarm_id":2,"alarm_count":0}]
```

`merged` is not an error: two situations that turn out to be one event are merged, and the absorbed
one keeps its id so that anything referring to it still resolves.

Open one and the answer to *"why were these grouped?"* is in the payload, not in a separate
explanation endpoint:

```json
"links": [
  {"alarm_a": 1, "alarm_b": 3, "score": 0.636,
   "terms": [{"name": "temporal",        "contribution": 0.286},
             {"name": "class_affinity",  "contribution": 0.0},
             {"name": "entity_affinity", "contribution": 0.35}]}
]
```

One row per link, carrying the score, the pair, and **the three named terms with each term's
number**. The contributions sum to the score exactly. In the console the same thing is a bar per
term on the expanded situation card, and you can answer the question without leaving the screen.

That example is worth reading closely, because it is what a cold start looks like: `class_affinity`
is **0.0** — the appliance has not yet seen these two trap types together often enough to have an
opinion — and the link is carried by time proximity plus the two alarms being on the same network
element. Nothing was configured; nothing was assumed.

See [`correlation.md`](correlation.md) for what each term means and how it is learned.

## 6. Telling it when it is wrong

Every **Confirm** and **Split** on a situation is the only human judgement the system ever receives.
Confirm reinforces a grouping; Split penalises it. Both are recorded with the evidence the verdict
was about, which is what every machine-learning release after v0.8.0 is built on.

Use them. A confirm on a grouping where every pair fell on the same side of the threshold contains
no decision and teaches nothing; a confirm on a *mixed* bag does. Roughly an eighth of labelled bags
are mixed, and they are where the value is.

### 6.1 Correcting it is stronger than judging it (v0.16.0)

**Confirm** and **Split** judge a grouping the appliance produced. **Moving an alarm corrects one**,
and it says two things at once: this alarm does not belong here, and it belongs *there*. That is the
strongest evidence this appliance can be given, and until v0.16.0 there was no way to give it.

Five gestures, and what each one asserts:

* **move** — a negative pair against every member it left, and a positive against every member it
  joined;
* **merge** — the cross pairs between the two memberships as they stood when you merged them;
* **split marked members out** — a negative for each marked member against each unmarked one;
* **rename** — nothing about the grouping. It is a label on an id that does not change;
* **clear an alarm by hand** — that the ALARM is stale, and nothing whatever about the grouping.

The last one matters more than it looks. A zombie alarm that never cleared is an alarm-lifecycle
fact; letting it reach the link scorer would be a signal about one question doing the work of a
measurement about another, which is the mistake this project has spent six releases not making.
So a hand-clear carries no confidence, produces no training row, and is recorded in full.

**Say how sure you are.** Every restructuring gesture carries a confidence you set on the card. It
is stored per gesture and per operator exactly as you gave it, and it shrinks that gesture's weight
by at most 20 % (`0.6 + 0.4 x c`). **Below 50 % the action still happens and teaches nothing** —
you are running the network, not labelling it, and the card says so before you commit.

There is one bound worth knowing: a situation contributes **one** labelled bag per verdict, so a
second move out of the same situation is recorded in full and adds no second label
(`docs/findings.md` F89). Restructuring five situations once teaches more than restructuring one
situation five times.

## 7. Running it alongside your existing NMS

It only needs a **copy** of the traps, so you can run it in parallel with whatever you have today
from day one, with nothing at risk. Cold start is honest and documented in
[`correlation.md`](correlation.md#cold-start).

## 8. Operational commands

```sh
python -m netcorenoc audit verify        # walk the audit hash chain, report the first broken link
python -m netcorenoc dataset stats       # what capture is storing, and the window you really have
make bias-report                         # what the labels look like, and what they cannot tell you
make agreement-report                    # how well the built-in scorer already agrees with you
make shadow-report                       # the sufficiency verdict, and the seal's query count
make census                              # what the promotion gate would decide, on real data
```

Deterministic offline reports over frozen inputs. They emit aggregates only, and each closes by
saying what it *cannot* tell you.
