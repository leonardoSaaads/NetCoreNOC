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
                        │ UDP 162 — unauthenticated by protocol, by design
   network-adjacent ────┼─▶ receiver ─▶ queue ─▶ engine ─▶ store
                        │
   browser / API client ┼─▶ TLS? ─▶ headers ─▶ origin/CSRF ─▶ session|token ─▶ RBAC ─▶ handler
                        │                                                          │
   holder of the DB file┼──────────────────────────────────────────────────────▶ store
```

**The datagram side is unauthenticated and that is deliberate**: SNMPv2c has no authentication and
zero-config means accepting traps from equipment that was never told about us. The defences there
are shape-based — defensive parsing into quarantine, a bounded queue that counts overflow rather
than awaiting, and a source-IP allowlist you should set. **The HTTP side is the security perimeter**
and is where the controls live.

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

## Roles and what narrows them

| Role | Can do |
|---|---|
| **viewer** | Read situations, graph, timeline, stats, classes; receive the live stream |
| **editor** | viewer + confirm/split feedback, rename devices and classes, close or acknowledge situations |
| **admin** | editor + manage users and tokens, change runtime config, read quarantine, read/export/prune the audit log |

Governance is **optional and off by default**. With no policy stored, the built-in permissions and
full visibility are what you get.

* **Capabilities** can be taken away from a role or from one principal. The built-in map is a
  **ceiling**, not a starting point: the resolved set is `ceiling ∩ policy`, so a policy can only
  ever *narrow*. There is no configuration that gives a viewer an admin capability, and an admin can
  never be locked out of repairing the policy.
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
is otherwise entitled to read it as a boundary. Migration `0020` carries the same warning as a
banner in the schema itself.

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

## Sessions, passwords, throttling

* `scrypt` (n = 2¹⁷). Policy is **length only**: 12–128 characters, no composition rules, no forced
  expiry — NIST SP 800-63B.
* Login throttling is per-username **and** per-IP, with no user enumeration: a wrong username and a
  wrong password are the same response and the same timing.
* Sessions are stored server-side by SHA-256 of the id, with a sliding idle timeout **and** an
  absolute one. A stolen cookie expires on the absolute clock whatever the holder does.
* Service tokens are per-identity and revocable. The value is shown once and stored hashed.

## The audit log

Append-only and **hash-chained**: each row commits to its predecessor, so a deletion or an edit
breaks the chain at a nameable row.

```sh
python -m netcorenoc audit verify     # walks the chain, reports the FIRST broken link
```

It records every mutating action and every sensitive read — reading the quarantine list is audited,
because the quarantine holds raw refused datagrams. The action catalog is **frozen with a
completeness test**: adding an action is a deliberate decision, not a side effect of adding a route.
Pruning is admin-triggered only and is itself audited.

## Data at rest

The SQLite file is the whole state, and it is **not encrypted**. Anyone with the file has your
learned model, your situations and your audit history. Passwords and token values are hashed and
session ids are stored by digest, so the file does not yield live credentials — but treat it as
sensitive and use filesystem or volume encryption if your threat model needs it.

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
