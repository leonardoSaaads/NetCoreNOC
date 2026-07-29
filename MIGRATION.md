# Upgrading NetCoreNOC

## v0.7.0 → v0.7.1 (the write perimeter — a security patch)

v0.7.1 fixes six defects (F34–F39) in which a v0.7.0 guarantee was enforced on reads and not on
writes. It adds no feature, no route, and no configuration.

**Migration `0007_write_perimeter.sql` is transparent.** It adds two nullable columns, one unique
index, and two clean-ups, and it **seeds nothing**. Apply it the way you always do — it runs at
startup, or `make migrate`. Back up the database file first, as always.

### What the migration does

| | |
|---|---|
| Schema | `user_version` 6 → 7, forward-only and additive |
| Adds | `feedback.principal_ref` and `feedback.role` (both nullable, **not** backfilled); a `UNIQUE (situation_id, verdict)` index on `feedback` |
| Removes | duplicate `feedback` rows (keeping the **earliest** of each `(situation, verdict)` by `created_at`), and `label` rows naming a device or alarm class that does not exist |
| Seeds | **nothing** |
| Runtime dependencies | unchanged (still five) |

The de-duplication and the orphan cleanup only remove rows that v0.7.0's missing constraints let in.
Your alarms, situations, links, learned edges, scorer configuration, governance policy, provenance
and audit chain are untouched, and the audit chain's head hash is byte-identical across the upgrade.

### Action required: review any scope policy that uses a label glob

**This is the one breaking change, and it affects one configuration only.**

v0.7.0 resolved a glob scope selector against the **operator label** when a network element had
one, so `{"editor": ["core-*"]}` selected everything labelled `core-…`. But the operator label is
written by `POST /api/labels`, an **editor**-level route — so the role being scoped could widen its
own scope simply by relabelling a device it was not allowed to see (F35). That is a privilege
escalation, and closing it properly means the label cannot be an input to the decision at all.

**Since v0.7.1 a selector resolves against network-element identity and address only.** A selector
that can never match an address is now rejected when you write the policy, with a message saying
so.

| Selector | v0.7.0 | v0.7.1 |
|---|---|---|
| `ne:12` | NE id 12 | unchanged |
| `10.0.0.1` | that address | unchanged |
| `10.0.0.0/24` | that range | unchanged |
| `10.0.*` | address glob | unchanged |
| `core-*` | matched the **label** | **matches nothing** — rejected at write time |
| `POP-SUL` | matched that label | **matches nothing** — rejected at write time |

**What to do.** Open **Governance → Visibility scope** and look at each selector. If any is a name
rather than an address, replace it with the addresses, CIDRs, or `ne:<id>` values of the elements
you meant. Until you do, that line selects nothing — which is fail-closed: a viewer or editor sees
*less* than intended, never more. Admins are never scoped, so you can always get in to fix it.

If you have no scope policy stored — the default, and most installations — **there is nothing to
do.**

### Two other behaviour changes you may notice

Both are defect fixes, and both are deliberate:

- **A label write to a target that does not exist now returns 404** (was 200, and the row
  persisted). If you have automation that labels devices by id, it will now get an honest error for
  an id that is not there. This is the same 404 an out-of-scope target returns, deliberately, so the
  two are indistinguishable.
- **Posting the same feedback verdict on the same situation twice is now a no-op.** The second call
  still answers 200, but it records nothing and changes no learned state. Changing your mind
  (`confirm` after `split`, or the reverse) is a correction and still applies. Previously each
  repeat applied again *and* aged the whole appliance's learned state, so a loop could drive every
  learned mass to near zero.

Everything else is byte-identical to v0.7.0: every route, every status code, every shaped field,
and `make eval`.

---

## v0.6.0 → v0.7.0 (governance — one migration, and nothing changes until you ask it to)

v0.7.0 lets an admin restrict what each role and principal may **do** (capabilities) and may
**see** (network elements). Both are stored, audited policy read through the existing single
decision points.

**Nothing changes on upgrade.** Migration `0006_governance.sql` adds two tables and **seeds no
rows**. With no policy stored, the compiled role permissions and full visibility are what you get
— byte-identically v0.6.0. Every route, status code, and shaped field is unchanged, `make eval` is
unchanged, and most operators never open the Governance panel. There is **no breaking change and
no action required**.

### What the migration does

| | |
|---|---|
| Schema | `user_version` 5 → 6, forward-only and additive |
| Adds | `governance_policy` (append-only history, `RAISE(ABORT)` on UPDATE/DELETE) and `governance_active` (a per-kind pointer) |
| Seeds | **nothing** — that is what makes the upgrade invisible |
| Removes | nothing |
| Runtime dependencies | unchanged (still five) |

Apply it the way you always do — it runs at startup, or `make migrate`. Back up
`netcorenoc.db` first, as with any upgrade.

### If you do choose to write a policy

- **A capability policy can only take capabilities away.** The compiled permission map is the
  *ceiling*: the resolved set is `ceiling(role) ∩ policy`, so an entry naming a capability above a
  role's ceiling has **no effect** rather than granting it. There is no way to give a viewer an
  admin capability through configuration.
- **An admin can never be locked out.** The capabilities needed to read and repair the governance
  policy stay with the admin role no matter what a policy says, and **admins are never scoped**.
- **Clearing is one click** and returns the appliance to the shipped baseline. Policy history is
  append-only, so a clear removes the pointer, not the record.
- **If a policy becomes unreadable**, capabilities fall back to the built-in permissions (nobody
  gains anything) and scoping denies viewers and editors (nobody sees anything new). Both raise an
  operator warning and write an audit row; the admin repairs it from the Governance panel.

### ⚠ Visibility scoping is a presentation control and is **not tenant isolation**

Read this before you use scoping to separate customers or teams.

Scoping decides **what a principal is shown**. It does **not** partition what NetCoreNOC learns,
correlates, or groups:

- correlation still learns across **all** network elements — the class and NE affinity matrices are
  global, and feedback from any operator still moves them;
- a situation may still **form across** a scope boundary; its out-of-scope members are then shown
  to a scoped reader as a redacted count and their alarm classes, never as identifiers;
- situation ids, timing, and learned edge weights are global by construction.

A scoped operator therefore sees a **partial picture** and could mis-size an incident that spans
the boundary — which is exactly why the redacted count is shown rather than the members being
silently omitted. True multi-tenant isolation (per-tenant learning, per-tenant situation
boundaries, per-tenant retention and audit segmentation) is a separate, larger feature on
[`docs/ROADMAP.md`](docs/ROADMAP.md), and v0.7.0 does not provide it.

## v0.5.0 → v0.6.0 (the scoring seam — one migration, one removal)

v0.6.0 makes the correlation formula configurable, explainable, reproducible, and reversible.
**Grouping does not change**: at the default parameters v0.6.0 produces byte-identical output to
v0.5.0, and the migration seeds exactly those defaults. It is still one Python process over one
SQLite file, with zero new runtime dependencies.

### ⚠ One breaking change: the legacy `OPTICORR_*` environment aliases are removed

Deprecated since v0.4.0, warned once per variable through v0.5.0, and **removed now** as promised
(DECISIONS #34, #39, #45). Setting **any** `OPTICORR_*` variable is a **hard startup error** that
names each offending variable and its replacement.

**One-time action, before you upgrade:** rename every `OPTICORR_*` variable to `NETCORENOC_*` and
unset the old one. The mapping is mechanical — the prefix, nothing else:

| Removed | Use |
|---|---|
| `OPTICORR_DB` | `NETCORENOC_DB` |
| `OPTICORR_TRAP_HOST` / `OPTICORR_TRAP_PORT` | `NETCORENOC_TRAP_HOST` / `NETCORENOC_TRAP_PORT` |
| `OPTICORR_HTTP_HOST` / `OPTICORR_HTTP_PORT` | `NETCORENOC_HTTP_HOST` / `NETCORENOC_HTTP_PORT` |
| `OPTICORR_ALLOWLIST` | `NETCORENOC_ALLOWLIST` |
| `OPTICORR_RETENTION_DAYS` | `NETCORENOC_RETENTION_DAYS` |
| `OPTICORR_AUDIT_RETENTION_DAYS` | `NETCORENOC_AUDIT_RETENTION_DAYS` |
| `OPTICORR_TLS_CERT` / `OPTICORR_TLS_KEY` | `NETCORENOC_TLS_CERT` / `NETCORENOC_TLS_KEY` |
| `OPTICORR_LOG_JSON` | `NETCORENOC_LOG_JSON` |
| `OPTICORR_API_TOKEN` | *(nothing — the shared token was removed in v0.3.0; issue a service token)* |

Check with `env | grep OPTICORR_`. The `netcorenoc` audit CLI refuses for the same reason: reading
the wrong database would give a confidently wrong answer about the audit chain's integrity.

**Why an error rather than silently ignoring them.** A removed knob that quietly no-ops is a
*security* regression, not a nuisance: an operator still setting `OPTICORR_ALLOWLIST` would
believe trap sources were filtered while **every source was being accepted**. Failing at startup,
naming the replacement, turns that into a five-second fix.

### What else changes for you

- **One schema migration, `0005_scorer_config.sql` (`user_version` 4 → 5)**, applied
  automatically at startup, forward-only and additive. It adds the immutable `scorer_config`
  history table, a one-row `scorer_active` pointer, and a nullable `situation.scorer_config_id`
  provenance column. It **seeds the coded defaults and marks them active**, and backfills every
  existing situation to that row — a truthful record, because those situations really were formed
  by those parameters. Your data, learned state, sessions, tokens, and audit chain are untouched.
- **Nothing else changes by default.** The defaults are unchanged, the API responses are
  unchanged (the `link` objects gain an additive `terms` list alongside the existing
  `term_t`/`term_a`/`term_e`), and the UI gains one admin-only **Scorer** tab that most operators
  will never open.
- **New capabilities**: `scorer.read` (viewer+), `scorer.preview` and `scorer.write` (admin only,
  no editor delegation). New audit actions: `scorer.config.update`, `scorer.preview`,
  `scorer.fallback`.

### The upgrade

1. Back up the database (copy the file, or `sqlite3 netcorenoc.db ".backup backup.db"`).
2. **Rename any `OPTICORR_*` variables** (table above) and unset the old names.
3. Install v0.6.0 into the same environment (`pip install .`), or `docker compose up --build`,
   pointing `NETCORENOC_DB` at the existing database.
4. Start the process. Migration `0005` applies automatically. Verify with
   `python -m netcorenoc audit verify` — the chain still verifies across the upgrade.

### Rollback

Restore the pre-upgrade backup and reinstall v0.5.0. A v0.5.0 binary will not read a
`user_version=5` database's new tables, but it ignores them; the safe path is the backup.

*Rolling back a **scoring configuration** is a different, much cheaper thing: it is one click in
the Scorer tab (or `POST /api/scorer/rollback`), it moves a pointer, and it never edits history.*

## v0.4.0 → v0.5.0 (organization/structure — no behaviour change)

v0.5.0 makes the project legible, installable, and contributable. **Nothing in the running
correlator changes** — not ingestion, learning, the API contract, the schema, or the UI
behaviour. It is still one Python process over one SQLite file.

### What changes for you

- **Nothing operationally.** No schema migration, no config change, no data change. A live v0.4.0
  database is read as-is; the audit chain still verifies across the upgrade
  (`python -m netcorenoc audit verify`).
- **The import path is unchanged** (`netcorenoc`), even though the source now lives under `src/`
  (the PyPA layout). `pip install .` / `pip install netcorenoc-0.5.0-*.whl` and
  `python -m netcorenoc.main` work exactly as before.
- **Environment variables are unchanged.** All `NETCORENOC_*` names, and the legacy `OPTICORR_*`
  aliases, still work. The alias removal that v0.4.0 scheduled for v0.5.0 has been **extended one
  version to v0.6.0** (DECISIONS #39) — you have another release to rename them; they still emit a
  one-time deprecation warning each. *(That removal has since happened — if you are upgrading past
  v0.5.0, read the v0.5.0 → v0.6.0 section above first.)*
- **The easiest way to run it is now `docker compose up`.** The bundled `docker-compose.yml`
  expresses the hardened run declaratively; copy `.env.example` to `.env` for any configuration.

### The upgrade

1. Back up the database (copy the file, or `sqlite3 netcorenoc.db ".backup backup.db"`).
2. Install v0.5.0 into the same environment (`pip install .`), or `docker compose up --build`,
   pointing `NETCORENOC_DB` at the existing database.
3. Start the process. No migration runs (there is no schema change); learned state, sessions,
   tokens, and the audit chain are untouched.

### Rollback

Reinstall v0.4.0 and restore the backup if you wish — but nothing in v0.5.0 writes an
incompatible on-disk format, so a v0.4.0 binary reads a v0.5.0-touched database unchanged.

## v0.3.0 → v0.4.0 (the rebrand)

v0.4.0 renames the project from *OptiCorr* to **NetCoreNOC** and hardens security and
reliability. **No schema change is required by the rebrand itself** (the audit chain, learned
matrices, promoted entities, sessions, and tokens all survive untouched), and it is still one
Python process over one SQLite file.

### What the rename changes for you

- **Environment variables** move from `OPTICORR_*` to `NETCORENOC_*`. The legacy `OPTICORR_*`
  names are still honoured and emit a single startup deprecation warning each (naming the
  variable, never its value). The v0.4.0 removal target was **extended by one version**: they are
  now **removed in v0.6.0** (DECISIONS #39 — v0.5.0 is an organization/structure release and
  removing them here would be an unrelated breaking change) — rename them at your convenience
  before then. `NETCORENOC_*` wins if both are set.
- **The session cookie** is renamed `opticorr_session` → `netcorenoc_session`. This invalidates
  live sessions exactly once: **every operator re-logs-in after the upgrade.** No data is lost.
- **The CSRF header** (browser UI only) changed `X-OptiCorr-Client` → `X-NetCoreNOC-Client`. The
  bundled UI already sends the new header; a *custom* browser client that forged the old header
  must update it. Service-token API clients (`Authorization: Bearer …`) are unaffected.
- **The import package** is `netcorenoc` and the module entry points are `python -m netcorenoc`
  (audit CLI) and `python -m netcorenoc.main` (the server).

### The upgrade

1. Back up the database (copy the file; WAL is checkpointed on clean shutdown, or use
   `sqlite3 netcorenoc.db ".backup backup.db"`).
2. Install v0.4.0 into the same environment (`pip install .`), pointing `NETCORENOC_DB`
   (or the still-honoured `OPTICORR_DB`) at the existing database.
3. Start the process. No migration is required for the rebrand; if you also adopt any optional
   v0.4.0 schema change it applies forward-only and idempotently at startup. Learned state
   survives and `python -m netcorenoc audit verify` still reports the chain OK across the upgrade.
4. Tell operators to sign in again (the cookie rename logged them out once).

### Rollback

Restore the pre-upgrade backup and reinstall v0.3.0. Nothing in the rebrand writes an
incompatible on-disk format.

## v0.2.0 → v0.3.0

v0.3.0 adds the **learned entity model**: NetCoreNOC now learns *what* is alarmed (the ONU, the
port, the camera) from the trap varbinds, not just *who* reported it. The upgrade is **in
place and forward-only**: migrations `0003_entity.sql` and `0004_state_clear.sql` add tables
and columns without touching your data, and it is still one Python process over one SQLite file.

### Before you upgrade

- Back up `netcorenoc.db` (copy the file; WAL is checkpointed on clean shutdown).
- **Remove `OPTICORR_API_TOKEN` from the environment.** It was deprecated in v0.2.0 and is
  removed in v0.3.0: starting v0.3.0 with it still set is a **hard startup error** that names
  the migration path. Move every API client to a named **service token** (admin → Tokens in
  the UI, or the tokens API) before upgrading.

### The upgrade

1. Install v0.3.0 into the same environment (`pip install .`), pointing `OPTICORR_DB` at the
   existing database.
2. Start the process. The schema migrates automatically (`PRAGMA user_version` 2 → 4): each
   device becomes a reporting element (`ne`) plus one level-0 `entity`, every existing alarm is
   attributed to its NE's level-0 entity, and the profiler/ingest-gap/state-clear tables are
   created empty. Learned matrices, sessions, tokens, and the audit chain survive untouched —
   the audit hash chain still verifies across the upgrade.

### What an operator should expect to observe

- **Day 0 is identical to v0.2.0.** Every NE begins undivided (one entity), so grouping and
  root-cause output are byte-for-byte what v0.2.0 produced. Intelligence is additive and
  earned, never a different day-one behaviour.
- **Over the first hours to days, entities are promoted.** As a proxying NE (an OLT, a
  chassis, an NVR) reports the same identifier across different alarm classes, the profiler
  accumulates evidence and — once it crosses the promotion thresholds — subdivides that NE
  into its real entities (ONUs, ports, cameras). Promotion is forward-only: historical alarms
  are never reinterpreted.
- **You can see why.** For every promoted entity the UI and API show the varbind OID that
  identified it (`key_source`) and the score that promoted it (`confidence`). If the profiler
  ever picks the wrong discriminator, an admin can reset an NE's learned entity key (audited).
- **Dropped traps now leave a trace.** If ingestion ever sheds load (queue full or window
  overflow), an `ingest_gap` record appears in `/api/stats` and the UI: "events lost between
  t1 and t2" is now first-class information.
- **SNMPv1 devices are no longer invisible.** v1 traps (much access equipment, most cameras)
  are mapped in per RFC 3584 and correlated like any other trap — no configuration needed.

### Rollback

Restore the pre-upgrade `netcorenoc.db` backup. The v0.3.0 tables and columns are additive and
harmless to a v0.2.0 binary, but the clean path is the backup.

## v0.1.0 → v0.2.0

v0.2.0 adds identity, role-based authorization, and a tamper-evident audit log. The
upgrade is **in place and forward-only**: the schema migration adds tables without
touching v0.1.0 data, and the process is still one Python process over one SQLite file.

### Before you upgrade

- Stop the v0.1.0 process (or upgrade during a maintenance window; the trap listener is
  briefly down while the process restarts).
- Back up `netcorenoc.db` (copy the file; WAL is checkpointed on clean shutdown).

### The upgrade

1. Install v0.2.0 into the same environment (`pip install .`), pointing `OPTICORR_DB` at
   the existing database file.
2. Start the process. On first start the schema migrates automatically
   (`PRAGMA user_version` 1 → 2; migration `0002_auth_audit.sql`), all v0.1.0 rows intact.
3. **Bootstrap admin.** Because the `user` table is empty, NetCoreNOC creates an `admin`
   account and prints a random 20-character password **once** to the console inside a
   banner. Copy it now — it is never printed again.
4. Open the UI, log in as `admin`, and set a new password when prompted (the account
   starts with `must_change_password`; the app is locked to login + password change until
   you do). Create the users and roles your operators need.

### What changes for operators and clients

- **The UI now requires login.** The old `localStorage` API-token box is gone.
- **API clients** move from the single shared token to **service tokens**: an admin
  creates a named token with a role in the UI (or the tokens API), and the value is shown
  once. Send it as `Authorization: Bearer <value>`.
- **Roles**: viewer (read + live stream), editor (+ feedback, rename, close), admin
  (+ user/token/config management, quarantine, audit).

### Legacy token deprecation timeline

`OPTICORR_API_TOKEN` still works in v0.2.0 for backward compatibility: it is accepted as a
synthetic admin identity `legacy-token`. On startup NetCoreNOC logs a deprecation warning
naming the variable (never its value) and writes one `legacy_token.used` audit event the
first time it is used. **`OPTICORR_API_TOKEN` is removed in v0.3.0** — migrate every
client to a named service token before upgrading past v0.2.x.

### TLS

v0.2.0 can terminate TLS itself: set `OPTICORR_TLS_CERT` and `OPTICORR_TLS_KEY` (the
session cookie then gains `Secure` automatically). A reverse proxy terminating TLS is the
documented alternative — see `SECURITY.md`.

### Rollback

If you must roll back to v0.1.0, restore the pre-upgrade `netcorenoc.db` backup. The v0.2.0
tables are additive and harmless to a v0.1.0 binary (which ignores them), but v0.1.0
cannot read audit history, so restoring the backup is the clean path.

### Audit log operations

- Verify integrity: `python -m netcorenoc audit verify` (walks the hash chain, reports the
  first broken link).
- Export: `python -m netcorenoc audit export > audit-YYYYMMDD.ndjson` (NDJSON + final chain
  hash on stderr).
- Retention: audit rows are kept for `OPTICORR_AUDIT_RETENTION_DAYS` (default 365) and are
  never touched by the ordinary prune; only an explicit, audited admin action removes
  them.
