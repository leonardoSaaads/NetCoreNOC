-- 0035: the executor's host keys, and playbooks (ADRs #462, #463). Forward-only and additive.
--
-- 1. `noc_secret.host_key` — the SSH host key pinned the first time the executor connected with
--    this credential (trust on first use), in OpenSSH's one-line form. Every later connection must
--    present the same key or is refused. Cleared when the credential is rebound to another host or
--    port, and by a custodian's step-up ("the device was replaced").
--
-- 2. `noc_playbook` — a troubleshooting tree an agent wrote after resolving a case, stored as the
--    canonical JSON it was validated as (`engine/noc/playbook.py`): data, never code. `version`
--    counts its edits; `enabled` is the switch a person may turn off.
--
-- 3. `noc_playbook_run` — every time one ran on a case: which version, how it ended, how long it
--    took, and its trace (the steps taken, each with what it did and a digest of what came back —
--    the output itself goes to the case feed, bounded, where the NOC reads it).

ALTER TABLE noc_secret ADD COLUMN host_key text COLLATE "C";
ALTER TABLE noc_secret ADD COLUMN host_key_at timestamptz;

CREATE TABLE noc_playbook (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name         text COLLATE "C" NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
    document     text COLLATE "C" NOT NULL CHECK (length(document) <= 65536),
    version      integer NOT NULL DEFAULT 1,
    enabled      boolean NOT NULL DEFAULT true,
    source_case  bigint REFERENCES noc_case (situation_id),
    created_at   timestamptz NOT NULL,
    created_by   text COLLATE "C" NOT NULL,
    updated_at   timestamptz NOT NULL,
    updated_by   text COLLATE "C" NOT NULL
);
CREATE INDEX idx_noc_playbook_enabled ON noc_playbook (id) WHERE enabled;

CREATE TABLE noc_playbook_run (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    playbook_id  bigint NOT NULL REFERENCES noc_playbook (id),
    version      integer NOT NULL,
    situation_id bigint NOT NULL REFERENCES noc_case (situation_id),
    started_at   timestamptz NOT NULL,
    ended_at     timestamptz,
    outcome      text COLLATE "C" CHECK (outcome IS NULL OR outcome IN (
        'resolved', 'pending', 'queue', 'timeout', 'error', 'taken')),
    trace        text COLLATE "C" NOT NULL DEFAULT '[]'
);
CREATE INDEX idx_noc_playbook_run_playbook ON noc_playbook_run (playbook_id, id DESC);
CREATE INDEX idx_noc_playbook_run_case ON noc_playbook_run (situation_id, id DESC);
CREATE INDEX idx_noc_playbook_run_open ON noc_playbook_run (id) WHERE ended_at IS NULL;
