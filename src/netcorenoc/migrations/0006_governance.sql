-- 0006: governance (v0.7.0). Makes the HTTP perimeter's two decisions — which capabilities a
-- role/principal holds, and which network elements a viewer/editor is shown — into stored,
-- audited, versioned policy.
--
-- Forward-only and additive, applying cleanly onto a populated v0.6.0 database (schema v5).
-- Adds one append-only history table, one active-pointer table, and the append-only triggers.
--
-- IT SEEDS NOTHING. That is the whole point and it is a release gate: with no active policy the
-- resolver falls back to the compiled `PERMISSIONS` ceiling and to full visibility, which is
-- byte-identically v0.6.0 (DECISIONS #54). A migrated database therefore behaves exactly as it did
-- before the upgrade, and keeps doing so until an admin writes a policy. Compare 0005, which
-- *had* to seed a row because the engine needs parameters; governance has a compiled default and
-- so must not.
--
-- The engine's trap path never reads these tables: both policies are read HTTP-side, per request,
-- in the API's security dependency and its read handlers — never per packet, never in
-- receiver.datagram_received, never under the engine batch lock (prime directive 3).

-- Immutable, append-only history of governance policies. One row per policy version ever stored,
-- for either kind. Rollback re-points `governance_active`; it never edits or deletes a row, so the
-- perimeter's history stays answerable months later ("who could see and do what, on the day?").
-- Unlike `audit_log` there is NO sanctioned deleter: the retention prune does not touch this
-- table, because an authorization decision's provenance must outlive the sessions it governed.
--
-- `document` is the whole policy for its kind as canonical JSON (sorted keys, no whitespace),
-- not one row per grant. A policy is read and applied as a unit on every request, so storing it
-- as a unit makes "which policy was active" a single id rather than a reconstructed set, and makes
-- rollback a pointer move rather than a replay. `doc_hash` is the sha256 of exactly those bytes.
--
-- Shape of `document` (both kinds share it; absent key = unset = "expresses no opinion"):
--   rbac : {"version":1,
--           "roles":{"viewer":["stats.read", ...]},
--           "principals":{"user:3":[...], "token:2":[...]}}
--   scope: {"version":1,
--           "roles":{"viewer":["10.0.0.0/24","ne:7","core-*"]},
--           "principals":{"user:3":["10.1.0.0/16"]}}
-- An unparseable document is NOT a schema error: the resolver treats it as malformed and applies
-- the fail-safe for its kind (capabilities -> the compiled ceiling; scope -> deny for
-- viewer/editor), with an operator warning and an audit row (DECISIONS #55). Validation lives in
-- code, where it can degrade safely, not in a CHECK constraint that would make a corrupt row
-- unreadable and therefore unrepairable.
CREATE TABLE governance_policy (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL CHECK (kind IN ('rbac', 'scope')),
    document   TEXT NOT NULL,              -- canonical JSON, the whole policy for this kind
    doc_hash   TEXT NOT NULL,              -- sha256 of `document`
    created_by TEXT,                       -- admin actor; NULL is reserved for a system write
    created_at REAL NOT NULL,
    note       TEXT NOT NULL DEFAULT ''    -- free-form operator note ("why did we change?")
);
CREATE INDEX idx_governance_policy_kind ON governance_policy (kind, id);

-- Append-only at the storage layer, exactly like `audit_log` and `scorer_config`: editing the
-- policy history behind a historical authorization decision must be impossible through any normal
-- path, so the perimeter's provenance is tamper-evident alongside the hash-chained audit log.
-- Tampering must therefore defeat both.
CREATE TRIGGER governance_policy_no_update BEFORE UPDATE ON governance_policy
BEGIN
    SELECT RAISE(ABORT, 'governance_policy is append-only');
END;

CREATE TRIGGER governance_policy_no_delete BEFORE DELETE ON governance_policy
BEGIN
    SELECT RAISE(ABORT, 'governance_policy is append-only');
END;

-- The active pointer: at most one row per kind. Activating a policy and rolling back to an earlier
-- one are the same operation — an UPSERT of this row — so rollback is one action and history stays
-- immutable.
--
-- DELETING the row for a kind is the supported "clear the policy" operation, and it is what makes
-- the shipped baseline always reachable: no row => no policy => the compiled ceiling and full
-- visibility => exactly v0.6.0. The history rows survive the clear, so the record of what was once
-- active is not lost. This table is a pointer, not history, so it is deliberately NOT append-only
-- (same as `scorer_active`).
CREATE TABLE governance_active (
    kind         TEXT PRIMARY KEY CHECK (kind IN ('rbac', 'scope')),
    policy_id    INTEGER NOT NULL REFERENCES governance_policy (id),
    activated_by TEXT,
    activated_at REAL NOT NULL
);

-- No INSERT. No seed. A fresh database and a migrated v0.6.0 database are indistinguishable here,
-- and both behave exactly as v0.6.0 did.
