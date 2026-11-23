-- ADR #501: a routine keeps every version it had, and why each one was written.
--
-- A routine is a troubleshooting tree the NOC — its people and its agents — improves case after
-- case: a check added because the last fault was upstream, a branch for a vendor's other wording.
-- Until now an edit overwrote the tree and only a counter said it had changed. Each version is now
-- kept whole, with who wrote it, when, and the one line saying what changed and why ("ping before
-- SSH: case 41 was a dead path, not a dead port"). Runs already record the version they ran, so
-- how each version fared is a count, not a guess.
--
-- Append-only: a version is evidence of what an agent was told to run. A routine is never deleted
-- (it is switched off), so neither are its versions.

CREATE TABLE noc_playbook_version (
    playbook_id     bigint NOT NULL REFERENCES noc_playbook (id),
    version         integer NOT NULL CHECK (version >= 1),
    name            text COLLATE "C" NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
    document        text COLLATE "C" NOT NULL CHECK (length(document) <= 65536),
    organization_id integer REFERENCES organization (id),
    tier            smallint NOT NULL CHECK (tier BETWEEN 1 AND 4),
    written_at      timestamptz NOT NULL,
    written_by      text COLLATE "C" NOT NULL,
    note            text COLLATE "C" NOT NULL DEFAULT '' CHECK (length(note) <= 300),
    PRIMARY KEY (playbook_id, version)
);

-- The version each routine is at today, so its history starts where it stands.
INSERT INTO noc_playbook_version (playbook_id, version, name, document, organization_id, tier,
                                  written_at, written_by, note)
SELECT id, version, name, document, organization_id, tier, updated_at, updated_by, ''
FROM noc_playbook;
