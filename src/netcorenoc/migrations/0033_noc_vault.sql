-- 0033: the NOC plane's credential vault (ADRs #454-#458). Forward-only and additive.
--
-- 1. `noc_secret` — one credential an agent or a person may need to reach a device. What is sealed
--    (`sealed`, `crosscutting/secretbox.py`) is every field of the credential: username, password,
--    private key, community, token. What is plain is what a list needs and what binds the seal:
--    the kind, the host and port, the protocol version and the access level. Those are the
--    associated data of both layers of the seal, so a row whose host is rewritten no longer opens —
--    a credential cannot be redirected to a machine an attacker controls by editing the table.
--    `ref` is random and never reused: it is bound into the seal, so a sealed value copied into
--    another row does not open either. `version` counts changes to the credential itself.
--
-- 2. `noc_secret_check` — the reachability checks behind the status light: when, whether the
--    endpoint answered its protocol, how fast. Bounded per credential by the store (the newest
--    rows are kept), so a year of five-minute checks is not a year of rows.
--
-- 3. `noc_grant` — who may hold the vault (`custodian`: an editor an admin named; every admin is
--    one) and which service tokens are agents (`agent`). Keyed by the subject's row identity,
--    `user:<id>` or `token:<id>`, as the capability policy is (DECISIONS #62).
--
-- 4. `noc_lease` — the leases agents present instead of a credential. Only a SHA-256 of the lease's
--    id is kept; the lease itself is signed, short-lived, bound to the agent that asked and to one
--    credential, and revocable here.
--
-- None of these is append-only: the audit log is the record of every act on them (`noc.*`).

CREATE TABLE noc_secret (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ref              text COLLATE "C" NOT NULL UNIQUE,
    name             text COLLATE "C" NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
    description      text COLLATE "C" NOT NULL DEFAULT '',
    kind             text COLLATE "C" NOT NULL,
    access_level     text COLLATE "C" NOT NULL
                     CHECK (access_level IN ('view_only', 'diagnostic', 'operator', 'root')),
    host             text COLLATE "C" NOT NULL,
    port             integer NOT NULL CHECK (port BETWEEN 1 AND 65535),
    protocol_version text COLLATE "C" NOT NULL DEFAULT '',
    sealed           text COLLATE "C" NOT NULL CHECK (sealed LIKE 'ncs1:%'),
    version          integer NOT NULL DEFAULT 1,
    agent_access     boolean NOT NULL DEFAULT false,
    monitor          boolean NOT NULL DEFAULT true,
    created_at       timestamptz NOT NULL,
    created_by       text COLLATE "C" NOT NULL,
    updated_at       timestamptz NOT NULL,
    updated_by       text COLLATE "C" NOT NULL,
    revealed_at      timestamptz,
    revealed_by      text COLLATE "C",
    checked_at       timestamptz
);
CREATE INDEX idx_noc_secret_due ON noc_secret (checked_at NULLS FIRST) WHERE monitor;

CREATE TABLE noc_secret_check (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    secret_id  bigint NOT NULL REFERENCES noc_secret (id) ON DELETE CASCADE,
    at         timestamptz NOT NULL,
    ok         boolean NOT NULL,
    latency_ms double precision,
    detail     text COLLATE "C" NOT NULL DEFAULT '',
    trigger    text COLLATE "C" NOT NULL CHECK (trigger IN ('schedule', 'manual'))
);
CREATE INDEX idx_noc_secret_check ON noc_secret_check (secret_id, at DESC, id DESC);

CREATE TABLE noc_grant (
    subject_ref text COLLATE "C" NOT NULL CHECK (subject_ref ~ '^(user|token):[0-9]+$'),
    role        text COLLATE "C" NOT NULL CHECK (role IN ('custodian', 'agent')),
    granted_at  timestamptz NOT NULL,
    granted_by  text COLLATE "C" NOT NULL,
    PRIMARY KEY (subject_ref, role),
    CHECK ((role = 'custodian') = (subject_ref LIKE 'user:%'))
);

CREATE TABLE noc_lease (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    jti_hash     text COLLATE "C" NOT NULL UNIQUE,
    secret_id    bigint NOT NULL REFERENCES noc_secret (id) ON DELETE CASCADE,
    subject_ref  text COLLATE "C" NOT NULL,
    subject_name text COLLATE "C" NOT NULL,
    issued_at    timestamptz NOT NULL,
    expires_at   timestamptz NOT NULL,
    revoked_at   timestamptz,
    revoked_by   text COLLATE "C",
    last_used_at timestamptz,
    uses         bigint NOT NULL DEFAULT 0
);
CREATE INDEX idx_noc_lease_secret ON noc_lease (secret_id, expires_at DESC);
CREATE INDEX idx_noc_lease_subject ON noc_lease (subject_ref, expires_at DESC);

-- A credential's own history — who created it, who read it, which agent leased and used it — is
-- the audit log's rows about it; this keeps reading them from scanning the whole chain.
CREATE INDEX idx_audit_log_noc_secret ON audit_log (object_id, id DESC)
    WHERE object_type = 'noc_secret';
