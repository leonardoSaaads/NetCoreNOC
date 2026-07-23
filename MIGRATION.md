# Upgrading OptiCorr

## v0.2.0 → v0.3.0

v0.3.0 adds the **learned entity model**: OptiCorr now learns *what* is alarmed (the ONU, the
port, the camera) from the trap varbinds, not just *who* reported it. The upgrade is **in
place and forward-only**: migrations `0003_entity.sql` and `0004_state_clear.sql` add tables
and columns without touching your data, and it is still one Python process over one SQLite file.

### Before you upgrade

- Back up `opticorr.db` (copy the file; WAL is checkpointed on clean shutdown).
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

Restore the pre-upgrade `opticorr.db` backup. The v0.3.0 tables and columns are additive and
harmless to a v0.2.0 binary, but the clean path is the backup.

## v0.1.0 → v0.2.0

v0.2.0 adds identity, role-based authorization, and a tamper-evident audit log. The
upgrade is **in place and forward-only**: the schema migration adds tables without
touching v0.1.0 data, and the process is still one Python process over one SQLite file.

### Before you upgrade

- Stop the v0.1.0 process (or upgrade during a maintenance window; the trap listener is
  briefly down while the process restarts).
- Back up `opticorr.db` (copy the file; WAL is checkpointed on clean shutdown).

### The upgrade

1. Install v0.2.0 into the same environment (`pip install .`), pointing `OPTICORR_DB` at
   the existing database file.
2. Start the process. On first start the schema migrates automatically
   (`PRAGMA user_version` 1 → 2; migration `0002_auth_audit.sql`), all v0.1.0 rows intact.
3. **Bootstrap admin.** Because the `user` table is empty, OptiCorr creates an `admin`
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
synthetic admin identity `legacy-token`. On startup OptiCorr logs a deprecation warning
naming the variable (never its value) and writes one `legacy_token.used` audit event the
first time it is used. **`OPTICORR_API_TOKEN` is removed in v0.3.0** — migrate every
client to a named service token before upgrading past v0.2.x.

### TLS

v0.2.0 can terminate TLS itself: set `OPTICORR_TLS_CERT` and `OPTICORR_TLS_KEY` (the
session cookie then gains `Secure` automatically). A reverse proxy terminating TLS is the
documented alternative — see `SECURITY.md`.

### Rollback

If you must roll back to v0.1.0, restore the pre-upgrade `opticorr.db` backup. The v0.2.0
tables are additive and harmless to a v0.1.0 binary (which ignores them), but v0.1.0
cannot read audit history, so restoring the backup is the clean path.

### Audit log operations

- Verify integrity: `python -m opticorr audit verify` (walks the hash chain, reports the
  first broken link).
- Export: `python -m opticorr audit export > audit-YYYYMMDD.ndjson` (NDJSON + final chain
  hash on stderr).
- Retention: audit rows are kept for `OPTICORR_AUDIT_RETENTION_DAYS` (default 365) and are
  never touched by the ordinary prune; only an explicit, audited admin action removes
  them.
