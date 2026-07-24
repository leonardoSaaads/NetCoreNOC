-- NetCoreNOC schema v2 — identity, sessions, service tokens, tamper-evident audit.
--
-- Forward-only. Adds tables only; applies cleanly on top of a populated v0.1.0 database
-- (schema v1). The engine's trap path never touches these tables — auth and audit are
-- HTTP-side only (prime directive 2).

-- Operator accounts. Passwords are scrypt hashes in the
-- scrypt$<n>$<r>$<p>$<salt_b64>$<hash_b64> form so parameters are upgradeable. The
-- bootstrap admin is created at startup with must_change_password=1.
CREATE TABLE user (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    username             TEXT NOT NULL UNIQUE,
    password_hash        TEXT NOT NULL,
    role                 TEXT NOT NULL,               -- admin | editor | viewer
    must_change_password INTEGER NOT NULL DEFAULT 0,
    disabled             INTEGER NOT NULL DEFAULT 0,
    created_at           REAL NOT NULL,
    updated_at           REAL NOT NULL
);

-- Server-side sessions. Only the SHA-256 of the cookie value is stored, so a DB leak
-- cannot resurrect a live session. Idle (sliding) and absolute expiry are stored hints;
-- the maintenance loop purges expired rows.
CREATE TABLE session (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_hash    TEXT NOT NULL UNIQUE,             -- sha256 hex of the token
    user_id         INTEGER NOT NULL REFERENCES user (id) ON DELETE CASCADE,
    created_at      REAL NOT NULL,
    last_seen_at    REAL NOT NULL,
    idle_expiry     REAL NOT NULL,                    -- last_seen_at + idle window
    absolute_expiry REAL NOT NULL,                    -- created_at + absolute window
    source_ip       TEXT
);
CREATE INDEX idx_session_user ON session (user_id);
CREATE INDEX idx_session_absolute ON session (absolute_expiry);

-- Service tokens (bearer). Value is shown once at creation and stored only as SHA-256;
-- bound to a named identity and a role; individually revocable.
CREATE TABLE api_token (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash   TEXT NOT NULL UNIQUE,                -- sha256 hex of the token value
    name         TEXT NOT NULL UNIQUE,                -- identity the token acts as
    role         TEXT NOT NULL,                       -- admin | editor | viewer
    created_at   REAL NOT NULL,
    created_by   TEXT,                                -- admin username at creation
    last_used_at REAL,
    revoked      INTEGER NOT NULL DEFAULT 0,
    revoked_at   REAL
);

-- Tamper-evident audit log. Append-only (triggers below) and hash-chained: every row's
-- entry_hash = sha256(prev_hash + canonical_json). The genesis prev_hash is 64 zeros.
CREATE TABLE audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    actor       TEXT NOT NULL,
    role        TEXT,
    source_ip   TEXT,
    action      TEXT NOT NULL,
    object_type TEXT,
    object_id   TEXT,
    outcome     TEXT NOT NULL,                        -- ok | denied | error
    details     TEXT NOT NULL DEFAULT '{}',           -- redacted JSON object
    prev_hash   TEXT NOT NULL,
    entry_hash  TEXT NOT NULL
);
CREATE INDEX idx_audit_ts ON audit_log (ts);
CREATE INDEX idx_audit_action ON audit_log (action);

-- Immutable even to the application itself: no UPDATE, no DELETE through any normal path.
-- The one sanctioned exception — the admin-only, itself-audited audit retention prune —
-- drops and recreates these triggers inside a single locked transaction (see
-- Store.prune_audit and DECISIONS v0.2 #3); everything else aborts here.
CREATE TRIGGER audit_log_no_update BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only');
END;

CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only');
END;

-- F4: quarantine keeps only a sanitized packet (community octets blanked) or, when the
-- community cannot be located in a malformed packet, metadata only. These columns hold
-- the always-safe metadata; raw holds the sanitized bytes (or empty for metadata-only).
ALTER TABLE quarantine ADD COLUMN sha256 TEXT;
ALTER TABLE quarantine ADD COLUMN length INTEGER;
ALTER TABLE quarantine ADD COLUMN first8 TEXT;
ALTER TABLE quarantine ADD COLUMN sanitized INTEGER NOT NULL DEFAULT 0;

-- F4: opaque HMAC tag of the SNMPv2c community for grouping; the community itself is
-- never persisted. Blank for alarms ingested before v0.2.0.
ALTER TABLE alarm ADD COLUMN community_tag TEXT;

