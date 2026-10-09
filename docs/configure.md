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

| Variable | Default | What it does, and what it costs |
|---|---|---|
| `NETCORENOC_DB` | `netcorenoc.db` | Path to the SQLite file (WAL mode). Put it on a real volume: it is the entire state |
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

**Every one of them is validated at startup and refuses by name.** A port outside 1–65535, a
retention that is not a number, an allowlist entry that is not a CIDR, a TLS path that cannot be
read, or exactly one of the two TLS variables set: the process prints one sentence naming the
variable and exits 2. An empty value is not the same as an unset one — `NETCORENOC_RETENTION_DAYS=`
is a value, and it is refused.

### Two variables that are hard startup errors

Setting either of these does not warn — the process **refuses to start** and names the replacement:

* **`NETCORENOC_API_TOKEN`** — the shared API token was removed in v0.3.0. Use service tokens
  (per-identity, revocable, shown once) from the **Service tokens** screen.
* **Any `OPTICORR_*` variable** — the legacy prefix from before the rename was removed in v0.6.0.

Both refuse rather than ignore, and the reason is worth stating: an ignored `OPTICORR_ALLOWLIST`
would mean every trap source is accepted while the operator believed otherwise. A silently ignored
security setting is a regression, not an inconvenience. [`MIGRATION.md`](../MIGRATION.md) has the
rename table.

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
