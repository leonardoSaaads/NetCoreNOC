# Security

Two audiences. If you are **deploying** NetCoreNOC, read this page. If you have **found a
vulnerability**, do not use this page — follow the coordinated disclosure policy in
[`SECURITY.md`](../SECURITY.md). Do not open a public issue.

## The posture, in one paragraph

Deny-by-default authorization from a single compiled map; `scrypt` passwords with per-username and
per-IP throttling and no user enumeration; server-side sessions with SHA-256-stored ids, an
`HttpOnly; SameSite=Strict` cookie, sliding idle **and** absolute timeouts, and CSRF checked on
origin as well; revocable per-identity service tokens; optional built-in TLS with an automatic
`Secure` cookie; a strict CSP over a locally vendored console with no CDN; an append-only,
hash-chained audit log over every mutating action and sensitive read. The SNMPv2c community string
is **never persisted or logged** — it is kept only as an HMAC grouping tag.

## Trust boundaries

```
                        │ UDP 162 — v1/v2c unauthenticated by protocol; v3 USM if configured
   network-adjacent ────┼─▶ receiver ─▶ queue ─▶ engine ─▶ store
                        │
   browser / API client ┼─▶ TLS? ─▶ headers ─▶ origin/CSRF ─▶ session|token ─▶ RBAC ─▶ handler
                        │                                                          │
   holder of DB password┼──────────────────────────────────────────────────────▶ store
```

**The datagram side is unauthenticated by default and that is deliberate**: SNMPv2c has no
authentication and zero-config means accepting traps from equipment that was never told about us.
The defences there are shape-based — defensive parsing into quarantine, a bounded queue that counts
overflow rather than awaiting, and a source-IP allowlist you should set. Since v0.30.0 you can also
switch v1/v2c off and accept only **SNMPv3** traps from configured users, authenticated (HMAC) and
optionally encrypted, with RFC 3414 replay protection (Settings → SNMP, decision #444). **The HTTP
side is the security perimeter** and is where the controls live.

If you are auditing that perimeter, the file to read is `src/netcorenoc/api/perimeter.py` — all of
it, and nothing else.

## Deploy it like this

1. **Bind to the management interface and set an allowlist** (`NETCORENOC_ALLOWLIST`,
   `NETCORENOC_TRAP_HOST`, `NETCORENOC_HTTP_HOST` — see [`configure.md`](configure.md)). With no
   allowlist **every source is accepted**, and admins see a persistent banner until you set one.
   Denied datagrams are counted, never silently dropped.
2. **Terminate TLS**, either built in (the cookie gains `Secure` automatically) or at a reverse
   proxy. If you proxy, **preserve the `Host` header and forward the browser `Origin`**: the cookie
   is `SameSite=Strict` and CSRF also checks origin against host, so a proxy that rewrites either
   breaks logins in a way that looks like a bug in the appliance.
3. **Complete the bootstrap** — see [`operate.md`](operate.md). You are forced to change the printed
   password.
4. **Issue service tokens, not shared secrets.** Per-identity, revocable, minimum role, shown once.
5. **Run the container hardened** — [`install.md`](install.md) has the flags; the shipped compose
   file already applies them.
6. **Keep the database private** — no published port, its password in a file, a role that holds
   only its own database. [The database](#the-database) below; the shipped compose file does all
   three.

## Roles and what narrows them

| Role | Can do |
|---|---|
| **viewer** | Read situations, graph, timeline, stats, classes; receive the live stream |
| **editor** | viewer + confirm/split feedback, rename devices and classes, close or acknowledge situations |
| **admin** | editor + manage users and tokens, change runtime config, read quarantine, read/export/prune the audit log |

The NOC's vault adds a narrower gate on top of the roles: **custodians** (every admin, and the
editors an admin names) and **agents** (service tokens an admin names). See
[the credential vault](#the-credential-vault-noc--secrets).

Governance is **optional and off by default**. With no policy stored, the built-in permissions and
full visibility are what you get.

* **Capabilities** can be taken away from a role or from one principal. The built-in map is a
  **ceiling**, not a starting point: the resolved set is `ceiling ∩ policy`, so a policy can only
  ever *narrow*. There is no configuration that gives a viewer an admin capability. **The admin role
  is never narrowed** (v0.30.0, decision #443): a person with it always holds every capability, so
  the appliance can always be administered; to give someone less, give them the editor role. A
  service token of any role — admin included — can be narrowed, which is how a program gets least
  privilege.
* **Visibility scoping** limits which network elements a viewer or editor sees, by element id,
  address, CIDR or address glob. Out-of-scope elements are absent from every list; a
  directly-requested one returns **404, not 403** — existence is not disclosed — and a *write* to
  one returns the same 404. A selector never resolves against the operator label, because that label
  is written by the very role being scoped. **Admins are never scoped.**
* Every change is audited with before and after; the policy history is immutable and append-only;
  rollback and clearing are one click.
* If a policy becomes unreadable, capabilities fall back to the built-in map (nobody gains anything)
  and scoping shows nothing to viewers and editors (nobody sees anything new) — with a warning and
  an audit row, never a silent change.

### ⚠ Visibility scoping is a presentation control and is **not** tenant isolation

Correlation still learns across every network element, and a situation may still *form* across a
boundary a principal cannot see. Its members are then shown to them as a redacted count and alarm
class, never as identifiers — deliberately, because a scoped operator seeing a partial picture
should know it is partial rather than have members silently vanish.

True multi-tenant isolation — per-tenant learning, per-tenant situation boundaries, per-tenant
retention and audit segmentation — is a larger, separate feature on the [roadmap](ROADMAP.md).
**NetCoreNOC does not claim it today.**

### ⚠ An organization is attribution, and it is **not** tenant isolation either (v0.21.0)

Every network element belongs to an organization, every maintenance window belongs to one, and
lists filter by it. The column answers one question — *"whose equipment is this?"* — and

* **no authorization decision anywhere reads it.** It is not an RBAC subject and not a scope input;
* **correlation still learns across every element**, and a situation may still form across an
  organization boundary, exactly as the scoping warning above says;
* it is **admin-written**, which is why it is deliberately *not* folded into the visibility-scope
  resolver: F35's rule is that no resolver input may be writable by a scopable role, and admitting
  a second admin-writable axis would widen that guarantee for a feature that is not a security
  control.

`GET /api/organizations` says so in its own response body (`"isolation": false`, with a sentence
naming what still crosses the boundary), because an agent reading a field called `organization_id`
is otherwise entitled to read it as a boundary.

### Maintenance windows suppress collection, and the suppression is visible (v0.21.0)

A window can stop an element's traps being recorded, which makes it a way to hide an outage. Three
things bound that:

* **Every role sees the marker.** A device or situation under planned work is badged on every
  screen, for a viewer as much as an admin, with when it ends. What the window *is* — its name, its
  owner, its rules — follows its visibility; **that it exists does not**, because a host that goes
  quiet with no marker reads as a healthy host.
* **A fault that outlives the window surfaces anyway.** The state ledger records, per fingerprint,
  a raise seen and a clear seen — six scalars, no payload — and anything raised inside a window
  that never cleared becomes an alarm when the window closes.
* **Every write is audited**, under six actions of its own rather than one carrying a verb, with
  the actor and whether they authenticated with a session or a service token. A window over six
  hours suppresses nothing until a human editor or admin confirms it, and an agent may never
  confirm one it created.

## The model file is data, and can be replaced (v0.26.0)

The shipped link model is two JSON files inside the package — `engine/model/linkmodel.json` and its
manifest — and **nothing in either is ever executed**: no `pickle`, no `eval`, no import, no field
that names code. Loading one is a validation: exact keys, finite numbers within stated bounds,
strictly increasing bin edges, only features this build computes, a size cap, and the manifest's
SHA-256 of the document (integrity, so the console can never describe one model while the engine
runs another — **not** an authenticity check). A file that fails is refused with its reason and the
appliance runs the additive formula as configured. Anyone who can write the package directory can
change what groups alarms, so treat it like the rest of the installed code (#419).

**Autonomy** acts as the model, never as a person: every act is written to the audit log under
`model:<ref>`, and the kill switch (`POST /api/autonomy/stop`) is deliberately **below** admin so
that an editor can always stop it. Turning a grade on is admin-only (#412).

## Sessions, passwords, throttling

* `scrypt` (n = 2¹⁷) over the password **keyed with the secret key** (`scrypt-p1`, ADR #453), so a
  copy of the database alone cannot test a single guess. Policy is **length only**: 12–128
  characters, no composition rules, no forced expiry — NIST SP 800-63B.
* **No one reads a password — an administrator included.** No route returns a password or its
  hash. An admin **resets**: People & access → *Reset password…* issues a random one-time password,
  shown once, which the person must replace at the next sign-in; their sessions and any reset link
  already emailed end. Not for the admin's own account, and not for an account with more access
  than the caller holds (a narrowed admin token cannot take over an admin).
* Login throttling is per-username **and** per-IP, with no user enumeration: a wrong username and a
  wrong password are the same response and the same timing. Its memory is bounded without ever
  forgetting an active lockout first, so a flood of throwaway usernames from many addresses cannot
  reset the backoff on the account under attack (v0.28.0).
* No request body over **1 MiB** is read, on any route, authenticated or not (v0.28.0) — a few
  oversized posts could otherwise exhaust the container's memory and lose traps in flight.
* A forgotten password is reset from the host (`python -m netcorenoc admin reset-password`), which
  needs the database's credentials — the same trust boundary as the first-run password. It is
  audited and ends every session of that account.
* **Recovery by email** (v0.30.0, decision #445) is off until an admin configures **Settings →
  Email** and a person has a recovery address. The request answers the same sentence whatever it
  is given and sends from a background task, so it reveals neither an account nor an address by its
  answer or its timing. The link carries 256 random bits, stored only as SHA-256; it lives 30
  minutes, works once and is superseded by the next request. Its host is the configured console
  address, **never the request's `Host` header** (reset poisoning). Requests are bounded per client
  address and per account, every one is audited, and a reset ends every session of the account.
  A mailbox is now a way into the account: protect it as you protect the account. **Changing your
  own recovery address takes your current password**, so a stolen session cannot point the next
  link at another mailbox, and a new address withdraws the links sent to the old one (ADR #453).
* Sessions are stored server-side by SHA-256 of the id, with a sliding idle timeout **and** an
  absolute one. A stolen cookie expires on the absolute clock whatever the holder does.
* Service tokens are per-identity and revocable. The value is shown once and stored hashed.

## NOC cases: who reads, who works, what a customer is shown (ADRs #459-#461)

* **Reading a case is reading its situation**: `cases.read` (viewer) and the same scope predicate,
  so a case outside the caller's scope is the same 404 as one that does not exist. Posting is
  `cases.post` (viewer); working — taking, editing, setting pending, resolving — is `cases.work`
  (editor), and every act but taking is the owner's; `cases.manage` (admin) acts on any case.
* **Each post carries its visibility**: `internal` to whoever holds `cases.work`, `specified` to its
  author, the people it names and `cases.manage`, `all` to every reader. Someone who cannot read
  `internal` cannot write it. Posts are append-only in the database.
* **Below editor**, every text of a case — its fields and its posts — shows device addresses as
  their network, by pattern rather than by word, and people as "NOC engineer", "NOC agent" or
  "Operator"; a scoped reader is not shown the situation's derived name, built from every member's
  address.
* **Resolving clears alarms only when the resolver states they were verified clear on the device**,
  as the broker's own `manual_clear` gestures, and only the members the resolver can see.
* Six audit actions (`noc.case.*`), under the actor's name, person or agent; the case loop's moves
  are events in the feed.

## Acting on a device: the executor and playbooks (ADRs #462, #463)

* **The secret never leaves the process.** An agent sends its lease and a command
  (`POST /api/noc/exec`); the appliance checks the lease (the same function introspection uses),
  opens the seal, connects, and answers with the device's text — never a field of the credential.
  A playbook uses the vault the same way, and only credentials whose agent access a custodian
  switched on.
* **Read only, enforced twice.** A command must start with a read verb for the lease's access level
  (`show`, `display`, `get`; `ping` and `traceroute` need `diagnostic`) and may contain no
  separator, redirection, substitution or control character, and no word that writes or escalates
  (`configure`, `commit`, `reload`, `clear`, `set`, `delete`, `enable`…); a playbook's SSH steps are
  checked when it is stored and again before each run. The only values put into a command, a branch
  test or a leaf's text are the element's address and the root alarm's entity, refused unless plain;
  a test that cannot be filled decides nothing, and the case goes to the NOC queue.
* **What a device says is untrusted.** Its answer is bounded (64 KiB), marked `untrusted`, and in a
  feed post introduced as the device's text, not instructions — SNMPv1/v2c data is
  attacker-controllable and must never steer an agent. A playbook branches on fixed tests of that
  text (contains, a number above or below), never on an evaluated expression.
* **SSH host keys are pinned on first use**; a different key later is refused, so a credential is
  never offered to an impostor. Moving the credential, or a custodian's *Forget…* with a step-up,
  clears the pin.
* **A person wins over automation**: a playbook applies its leaf only while the case is still
  `auto`; it resolves the case and clears alarms only when the tree verified them clear on the
  device; anything else, a timeout or a failure, goes to the NOC queue.
* **Playbooks are an admin's** (`playbooks.read`, `playbooks.write`): one acts on every matching
  case in every scope. An agent writing them uses an admin-role token narrowed to those
  capabilities.
* Audit: `noc.exec` for every act on a device (actor, credential, kind, duration, size and a digest
  of the command — never its output), `noc.playbook.create`, `.update` and `.run`.

## The credential vault (NOC → Secrets)

Phase 2 keeps the credentials people and AI agents use to reach equipment — SSH passwords and keys,
SFTP, SNMP communities and SNMPv3 users, API tokens, SMTP (ADRs #454-#458). The rules, each a test
in `tests/api/test_noc_vault.py` or `tests/security/test_secretbox.py`:

* **Sealed twice, under two keys.** A credential's fields are encrypted with a fresh data key
  (ChaCha20-Poly1305), the data key is wrapped under the **secrets key** (AES-256-GCM,
  `NETCORENOC_SECRETS_KEY_FILE`), and the whole is sealed again under the **secret key**
  (AES-256-GCM). Both layers authenticate where the credential works — kind, host, port, protocol
  version, access level — so a database writer who rewrites a host gets a credential that does not
  open, not one that is sent to their machine. The database row, `secret.key` and `secrets.key`
  are needed together; any two open nothing.
* **Custodians only.** The vault opens to a person — never a service token, whatever its role —
  who is an admin or an editor an admin named a custodian. Everyone else is told why and shown
  nothing. No list or detail carries a secret; lists never read the sealed column.
* **Step-up for everything that matters.** Reading a credential, changing or deleting one, letting
  agents use one and naming a custodian or an agent each ask again for the person's username and
  password, in the same request. A wrong answer is audited and counts against the sign-in throttle.
  **Two-factor authentication is a placeholder**: the field is there, disabled, and every audit row
  records `second_factor: not-enrolled` until TOTP enrolment ships — no code is "checked" today.
* **Agents get leases, never secrets.** Agent access is off per credential until a custodian
  switches it on. A service token an admin named an agent may then lease a credential: a signed
  (HS256), short-lived (15 min, at most 1 h) token bound to that agent and that credential, holding
  no secret. Changing the credential, switching agents off or withdrawing the agent ends its leases.
  Nothing yet executes on a device with a lease; that is the next step, and the secret will stay on
  the appliance's side when it does (`docs/plans/noc-phase2.md`).
* **Everything is in the audit log**: who stored, changed, deleted or read a credential, who switched
  agents on or off, who named whom, and which agent leased or used which credential — never a value.
* **The status light describes the endpoint.** Green means the host answered the protocol (an SSH
  greeting, an SMTP banner, an open port); it does not mean the password works, and the screen says
  so. Checks send nothing and never open the seal.

What it does **not** protect against, stated: a person with the appliance's host (its memory, both
key files) can read every credential; a custodian is trusted with the whole vault (it is not scoped
by visibility); and a revealed credential is on that person's screen and clipboard.

## The audit log

Append-only and **hash-chained**: each row commits to its predecessor, so a deletion or an edit
breaks the chain at a nameable row. **Sealed** too (ADR #453): each row carries an HMAC of its hash
under the secret key, so someone who can write the table — its owner, a superuser, an edited dump —
cannot rewrite a row and recompute the chain after it; stripping the seals is a break as well. The
appliance's own role cannot update, delete or truncate a row at all; pruning is a function that
refuses anything less than a day old. Appends from the appliance and from the CLI are serialised by
an advisory lock, so they never fork the chain. The export (`GET /api/audit/export`) is the plain
chain anyone can verify, without the seals.

```sh
python -m netcorenoc audit verify     # walks the chain, reports the FIRST broken link
```

It records every mutating action and every sensitive read — reading the quarantine list is audited,
because the quarantine holds raw refused datagrams. The action catalog is **frozen with a
completeness test**: adding an action is a deliberate decision, not a side effect of adding a route.
Pruning is admin-triggered only and is itself audited.

## The database

The PostgreSQL role the appliance connects as reads and writes the whole state, so its password is
the credential that matters most after an admin's (#452).

* **The password is in a file, never in the environment.** `NETCORENOC_DATABASE_PASSWORD_FILE`
  names it: the random password Compose's `db-secret` service generates on the first start, a
  Compose secret for a managed database, or a systemd credential (`LoadCredential=`) with the
  shipped unit. So `docker inspect`, `/proc/<pid>/environ` and `systemctl show` never show it.
  Wherever the URL is printed — the log, the CLI, the Settings screen — the password reads `***`,
  and `python -m netcorenoc backup` hands it to `pg_dump` in the child's environment, never on a
  command line `ps` shows.
* **The bundled server is off the network.** Compose puts PostgreSQL on an internal network with no
  published port and no route out; only the appliance joins it. Every connection, local ones
  included, authenticates with SCRAM-SHA-256. The container runs read-only, with every capability
  dropped but the five its entrypoint needs to switch to its own user, and no privilege escalation.
* **A server you run or rent is on the network**: require TLS (`sslmode=verify-full` with the
  provider's CA certificate, or at least `sslmode=require`) and allow connections only from the
  appliance (`pg_hba.conf`, the provider's network rules).
* **Least privilege: the appliance's role owns nothing** (ADR #453). One role owns the schema and
  runs the migrations; the appliance connects as another that may only `SELECT, INSERT, UPDATE,
  DELETE` rows — no DDL, no `TRUNCATE`, no trigger changes, no write to `schema_migrations`, and no
  update or delete on the twelve append-only tables. So an injection or a compromised process
  cannot drop the guards it would need to rewrite history. **Compose does this out of the box**:
  `db-migrate` runs as the owner `netcorenoc`, whose password only it can read, and the appliance
  as `netcorenoc_app`. Elsewhere, see [`configure.md`](configure.md#two-roles-one-owns-the-schema-the-appliances-owns-nothing);
  a single role that owns its schema still works and is logged as such. Neither role needs
  `SUPERUSER`, `CREATEDB`, `CREATEROLE`, `REPLICATION` or `BYPASSRLS`. The schema's privileges are
  revoked from `PUBLIC` when it is created, so other roles on a shared server — Supabase's `anon`
  and `authenticated` among them — cannot read it. The bundled server's superuser, `postgres`, has
  its own generated password that only the database container can read.
* **Slow statements are logged without their bound values** on the bundled server
  (`log_parameter_max_length=0`): those include password hashes and sealed values.
* **One appliance per schema**, enforced: a second appliance against the same schema stops at start
  (a session advisory lock), so a mistyped URL cannot interleave two histories.

## Data at rest

**What a copy of the database gives away** (ADR #453) — a stolen backup, a leaked managed-database
snapshot, a read-only SQL injection:

| In the database | Stored as | Without the secret key it is |
|---|---|---|
| A person's recovery email, display name, photo | AES-256-GCM, sealed to its column | unreadable; the email's lookup is a keyed digest |
| The SMTP password, SNMPv3 keys, the community HMAC key | AES-256-GCM | unreadable |
| The NOC's device credentials (SSH, SNMP, API tokens…) | ChaCha20-Poly1305 under a data key wrapped by the **secrets key**, then AES-256-GCM, both bound to host and port | unreadable — and needs `secrets.key` as well |
| Lease ids | SHA-256 | not reversible; a lease holds no secret anyway |
| Passwords | scrypt over an HMAC with the key | not testable offline, guess by guess |
| Session ids, reset links, token values | SHA-256 of 256 random bits | not reversible |
| The audit log | hash chain + HMAC seal | readable; not rewritable without detection |
| Situations, alarms, devices, the learned model | plain | readable — this is the appliance's working data |

**The secret key** is 32 random bytes in `NETCORENOC_SECRET_KEY_FILE` (default `secret.key` in the
data directory, `0600`, created on the first start), beside the spool and never in the database —
so the two are stolen separately or not at all. Back it up **apart** from the database: lose it and
every sealed value and every password is lost with it (an admin then issues new passwords). The
appliance refuses to start with a key that is not the one that sealed its database. Usernames stay
plain: they are the sign-in name and the actor of every audit row.

Your network's working data is not encrypted by the appliance: treat the database and every dump
as sensitive, and use the server's storage encryption (managed providers encrypt at rest) or volume
encryption if your threat model needs it. The SMTP password can stay out of the database altogether
(`NETCORENOC_SMTP_PASSWORD`). Accepted v1/v2c communities are kept as keyed hashes (F4), and a
quarantined packet keeps neither its community nor a hash or prefix that would test a guess.

## A new route declares itself, or the process does not start

Adding an HTTP route means adding its capability to `rbac.ROUTE_PERMISSIONS` **and** its visibility
posture to `rbac.ROUTE_SCOPE`, then registering it through `DeclaredRoutes`. `api/declare.py`
refuses anything `rbac/` has not been told about, **while the application is being built** — so an
undeclared route is a startup failure, not a runtime hole.

Since v0.7.5 it also refuses any route **shape** it cannot check: `include_router`, `app.mount()`
and `add_api_websocket_route` all fail `create_app`. A shape the gate cannot read would be skipped
rather than checked, and skipping is exactly how the gap that made this necessary happened.

## The full STRIDE analysis

This page is the operator's version. The 1 262-line per-component STRIDE analysis — every asset,
every attacker profile, every threat with its named control and the test that proves the control
holds, extended version by version from v0.2.0 to v0.14.0 — is at
`docs/security/threat-model.md` in commit `3ecf237`:

```sh
git show 3ecf237:docs/security/threat-model.md
```

It was folded into this page rather than kept beside it (decision #198). Nine tenths of it is
per-release accretion — *"new asset"*, *"STRIDE — new surface"*, *"vX coverage check"* repeated
thirteen times — and the tenth that a deploying operator needs is above. **The controls themselves
did not move**: each one is a test in `tests/`, which is where the evidence has always actually
lived.

## Open findings

Every finding this project has issued and not closed is in [`findings.md`](findings.md), with a
reproduction command and its measured output. Two of them are about correlation quality rather than
the security perimeter; all of them are stated rather than filed away.
