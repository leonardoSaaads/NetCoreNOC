-- 0005: the scoring seam (v0.6.0). Makes the link-score parameters a persisted, immutable,
-- admin-tunable configuration and records, per situation, which configuration formed it.
--
-- Forward-only and additive, applying cleanly onto a populated v0.5.0 database (schema v4).
-- Adds one table, one pointer table, one nullable column, and the append-only triggers.
-- It changes NO existing row's meaning: the seeded configuration is exactly the coded defaults
-- (W_T=0.30, W_A=0.35, W_E=0.35, TAU_S=30 s, LINK_THRESHOLD=0.50), so a migrated database scores
-- identically to v0.5.0 and every existing situation is truthfully backfilled to the parameters
-- that in fact formed it. That is what makes the parity gate hold across the upgrade.
--
-- The engine's trap path never reads these tables: parameters are loaded at engine configuration
-- load (never per packet, never in receiver.datagram_received), and the provenance column is
-- written where situations are already written, under the batch lock (prime directive 2).

-- Immutable, append-only history of scoring configurations. One row per parameter set ever
-- activated; rollback re-points `scorer_active`, it never edits or deletes a row (DECISIONS #47).
-- Unlike `audit_log` there is NO sanctioned deleter: the retention prune does not touch this
-- table, because a situation's provenance must outlive the alarms that formed it.
CREATE TABLE scorer_config (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    scorer_id        TEXT NOT NULL,              -- 'additive' is the built-in default
    contract_version TEXT NOT NULL,              -- semver of the feature/output schema, e.g. '1.0'
    w_t              REAL NOT NULL,              -- temporal weight
    w_a              REAL NOT NULL,              -- class-affinity weight
    w_e              REAL NOT NULL,              -- entity-affinity weight
    tau_s            REAL NOT NULL,              -- temporal decay constant, seconds
    threshold        REAL NOT NULL,              -- link acceptance threshold
    params_hash      TEXT NOT NULL,              -- stable fingerprint of (scorer_id, params)
    created_by       TEXT,                       -- admin actor; NULL for the migration seed
    created_at       REAL NOT NULL,
    note             TEXT NOT NULL DEFAULT ''    -- free-form operator note ("why did we change?")
);
CREATE INDEX idx_scorer_config_created ON scorer_config (created_at);

-- Append-only at the storage layer, exactly like audit_log: an edit to the parameter history
-- behind a historical grouping must be impossible through any normal path, so the provenance is
-- tamper-evident alongside the hash-chained audit log.
CREATE TRIGGER scorer_config_no_update BEFORE UPDATE ON scorer_config
BEGIN
    SELECT RAISE(ABORT, 'scorer_config is append-only');
END;

CREATE TRIGGER scorer_config_no_delete BEFORE DELETE ON scorer_config
BEGIN
    SELECT RAISE(ABORT, 'scorer_config is append-only');
END;

-- The active pointer: exactly one row, id pinned to 1 by the CHECK. Activating a configuration
-- (apply) and reverting to an earlier one (rollback) are the same operation — an UPDATE of this
-- single row — so rollback is one action and history stays immutable.
CREATE TABLE scorer_active (
    id           INTEGER PRIMARY KEY CHECK (id = 1),
    config_id    INTEGER NOT NULL REFERENCES scorer_config (id),
    activated_by TEXT,
    activated_at REAL NOT NULL
);

-- Seed: the coded defaults, marked active. This row is what makes a migrated database
-- byte-identical to v0.5.0 and gives every backfilled situation an honest provenance target.
-- params_hash is the sha256 of the same canonical JSON that
-- scoring.AdditiveScorer.params_fingerprint() hashes:
--   {"contract_version":"1.0","scorer_id":"additive","tau_s":30.0,"threshold":0.5,
--    "w_a":0.35,"w_e":0.35,"w_t":0.3}
-- A test asserts this literal equals the coded default's fingerprint, so a future change to
-- either the defaults or the canonicalisation cannot silently desynchronise the seed
-- (tests/test_scoring.py::test_seed_params_hash_matches_the_coded_default).
INSERT INTO scorer_config (
    id, scorer_id, contract_version, w_t, w_a, w_e, tau_s, threshold, params_hash,
    created_by, created_at, note
) VALUES (
    1, 'additive', '1.0', 0.3, 0.35, 0.35, 30.0, 0.5,
    'c490028245242445e9f58e46e11ac534e5a3f8be03bd9643135557a2e3183418',
    NULL, 0.0, 'coded defaults, seeded by migration 0005'
);

INSERT INTO scorer_active (id, config_id, activated_by, activated_at)
VALUES (1, 1, NULL, 0.0);

-- Decision provenance by reference (DECISIONS #47): which configuration was in effect when this
-- situation was created. Nullable so the column is additive; backfilled to the seed below, which
-- is truthful — every pre-v0.6.0 situation was in fact formed by the coded defaults.
ALTER TABLE situation ADD COLUMN scorer_config_id INTEGER REFERENCES scorer_config (id);

UPDATE situation SET scorer_config_id = 1 WHERE scorer_config_id IS NULL;
