# NetCoreNOC v0.30.0 — handoff

**SNMPv3, recovery by email, and the console reviewed.** Six items from an operator's review and
two capabilities the appliance lacked. Migration `0029` (schema 29) is additive. The v0.29.0
handoff (the field review and two more models) is at `f7cf702`: `git show f7cf702:HANDOFF.md`.

## What changed, by the review's items

| # | Report | Now | ADR |
|---|---|---|---|
| 1 | Sign-in has no background; the first-run card is too tall | two columns over `login-bg.svg` (drawn by `tools/login_art.py`); two-factor moved to Your account; one line or a recovery link | #446 |
| 2 | No email recovery | Settings → Email (provider presets, custom SMTP, a test) and "Forgot your password?" with single-use links | #445 |
| 3 | Sidebar and top bar misaligned | one height, `--bar-h`; measured 56/56 px at 1440 | #446 |
| 4 | Service tokens: which calls exist? Visibility: what for? | an API reference from the OpenAPI schema, narrowed by role or token, with `curl`; policy examples | #446 |
| 5 | Removing the admin role's capabilities breaks the project | the admin role and admin people are never narrowed; admin tokens still are | #443 |
| 6 | SNMP settings; SNMPv3 traps | Settings → SNMP; USM with every common auth and privacy protocol | #444 |

## Where the new code lives

| Concern | Module |
|---|---|
| What the receiver accepts | `ingest/snmpconf.py` (policy), `ingest/usm.py` (SNMPv3), `ingest/ber.py` (offsets) |
| SNMP settings | `api/routes/snmp.py`, `ui/app/views/parts/snmp.js`, `snmpuser.js` |
| Outgoing email | `crosscutting/mail.py`, `api/routes/email.py`, `ui/app/views/parts/email.js` |
| Recovery | `api/routes/recovery.py` (public), `store/recovery.py`, `ui/app/recover.js` |
| API reference | `api/reference.py`, `api/declare.py::documented`, `api/routes/apidoc.py`, `parts/apiref.js` |
| Fixed roles | `rbac/tables.py::FIXED_ROLES`, `rbac/policy.py::resolve_capabilities` |

## Decisions a reviewer should check

- **One optional dependency** (#444): SNMPv3 *privacy* needs `cryptography`, as the `snmpv3`
  extra. The core is still five; authNoPriv works without it; the Docker image includes it.
- **The SMTP password is stored in the database** (#445), write-only through the API, never
  logged or audited; `NETCORENOC_SMTP_PASSWORD` keeps it out of the file and its backups.
- **The admin role is fixed** (#443): narrowing an admin is refused, a stored entry is inert. #64's
  recovery set is gone — it was what an admin token kept after narrowing.

## Known limits, recorded rather than fixed

- **INFORMs are not answered** — v2c or v3; an inform is quarantined as `not-a-trap-pdu`, as before.
- **No SNMPv3 for a poller**: the appliance still polls nothing (`docs/ROADMAP.md`).
- **Two-factor authentication** is still on the roadmap; recovery by email is one more way in, so
  it is off until an admin configures email, and a reset ends every session of the account.
- The appliance does not start on native Windows (WSL 2 works; `docs/ROADMAP.md`).

## Verification

| gate | result |
|---|---|
| `ruff check`, `ruff format --check`, `mypy --strict` | clean |
| `vulture`, `bandit` | clean |
| full suite and DOM tests | see the PR description |
| `make eval` | byte-identical: nothing on the correlation path changed |
| live console | screenshots at 1440 and 390 px of sign-in, Settings → SNMP and Email, tokens, roles |
