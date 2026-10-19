-- The SQLite schema NetCoreNOC v0.30.0 reached (migrations 0001-0030), as `sqlite3` holds it,
-- with the rows those migrations seeded. A test fixture only: `tests/store/test_import.py` builds
-- a v0.30.0 database from it and imports that into PostgreSQL (ADR #452). Generated, not edited:
-- applying the v0.30.0 migrations to an empty file and dumping `sqlite_master` in creation order.
PRAGMA foreign_keys = OFF;
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE device (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ip         TEXT NOT NULL UNIQUE,
    vendor     TEXT,
    first_seen REAL NOT NULL,
    last_seen  REAL NOT NULL
);
CREATE TABLE alarm_class (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    oid        TEXT NOT NULL UNIQUE,
    first_seen REAL NOT NULL,
    last_seen  REAL NOT NULL
);
CREATE TABLE alarm (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id   INTEGER NOT NULL REFERENCES device (id),
    class_id    INTEGER NOT NULL REFERENCES alarm_class (id),
    instance    TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'active', -- active | cleared
    is_flapping INTEGER NOT NULL DEFAULT 0,
    count       INTEGER NOT NULL DEFAULT 1,
    first_seen  REAL NOT NULL,
    last_seen   REAL NOT NULL,
    cleared_at  REAL,
    varbinds    TEXT NOT NULL DEFAULT '[]', community_tag TEXT, ne_id INTEGER REFERENCES ne (id), entity_id INTEGER REFERENCES entity (id), severity TEXT, severity_rank INTEGER, severity_source TEXT, surfaced_from_window_id INTEGER REFERENCES maintenance_window (id), surfaced_at REAL, surfaced_ack_at REAL, surfaced_ack_by TEXT,
    UNIQUE (device_id, class_id, instance)
);
CREATE INDEX idx_alarm_last_seen ON alarm (last_seen);
CREATE INDEX idx_alarm_status ON alarm (status);
CREATE TABLE edge (
    kind       TEXT NOT NULL,
    a_id       INTEGER NOT NULL,
    b_id       INTEGER NOT NULL,
    weight     REAL NOT NULL DEFAULT 0,
    n          REAL NOT NULL DEFAULT 0,
    g          INTEGER NOT NULL DEFAULT 0,
    version    INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    PRIMARY KEY (kind, a_id, b_id)
);
CREATE TABLE situation (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    status        TEXT NOT NULL DEFAULT 'open', -- open | closed | merged
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    closed_at     REAL,
    root_alarm_id INTEGER
, scorer_config_id INTEGER REFERENCES scorer_config (id), merged_into INTEGER, resolution TEXT CHECK (
    resolution IS NULL OR resolution IN (
        'operator',      -- an operator closed it
        'self_cleared',  -- every member alarm cleared: the network fixed itself
        'idle',          -- the idle sweep timed it out at IDLE_CLOSE_S
        'merged',        -- the correlator merged it into another situation
        'manual_clear',  -- an operator hand-cleared the last active member (a zombie alarm)
        -- **The one value that will never be written again after this script.** DECISIONS #253.
        -- Before v0.16.0, `closed` meant operator-closed OR idle-swept and NOTHING DISTINGUISHED
        -- THEM. Writing `idle` here would be a GUESS ABOUT CONTENT where `0008`'s one permitted
        -- data write is explicitly a MARKER ABOUT PROVENANCE — the same distinction that made
        -- `legacy_capture` a statement about how a row was acquired rather than a verdict on it.
        -- An operator auditing later must be able to tell "nobody looked at it" from "we do not
        -- know", and a value that says "unknown" is the only honest way to say so.
        'unattributed'
    )
), derived_name TEXT, operator_name TEXT, decider TEXT, severity TEXT
    CHECK (severity IS NULL OR severity IN ('critical', 'major', 'minor', 'warning', 'indeterminate')), severity_by TEXT, model_name TEXT, proposed_into INTEGER, proposal_confidence REAL
    CHECK (proposal_confidence IS NULL OR (proposal_confidence >= 0 AND proposal_confidence <= 1)));
CREATE INDEX idx_situation_status ON situation (status);
CREATE TABLE situation_alarm (
    situation_id INTEGER NOT NULL REFERENCES situation (id),
    alarm_id     INTEGER NOT NULL REFERENCES alarm (id),
    PRIMARY KEY (situation_id, alarm_id)
);
CREATE INDEX idx_situation_alarm_alarm ON situation_alarm (alarm_id);
CREATE TABLE link (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    situation_id INTEGER NOT NULL,
    alarm_a      INTEGER NOT NULL,
    alarm_b      INTEGER NOT NULL,
    score        REAL NOT NULL,
    term_t       REAL NOT NULL,
    term_a       REAL NOT NULL,
    term_e       REAL NOT NULL,
    created_at   REAL NOT NULL
, terms TEXT);
CREATE INDEX idx_link_situation ON link (situation_id);
CREATE TABLE feedback (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    situation_id INTEGER NOT NULL REFERENCES situation (id),
    verdict      TEXT NOT NULL, -- confirm | split
    created_at   REAL NOT NULL
, principal_ref TEXT, role TEXT, member_digest TEXT, member_count INTEGER, scope_policy_id INTEGER, scope_restricted INTEGER, scope_redacted_members INTEGER, acquisition_channel TEXT, capture_provenance TEXT, situation_opened_at REAL, situation_id_at_label INTEGER, coverage TEXT, coverage_found INTEGER, coverage_expected INTEGER, client_member_digest TEXT, client_member_count INTEGER, client_updated_at REAL, client_truncated INTEGER, excluded_count INTEGER, excluded_truncated INTEGER, remainder_together INTEGER, excluded_reconciled INTEGER
    CHECK (excluded_reconciled IS NULL OR (excluded_reconciled >= 0
           AND member_count IS NOT NULL AND excluded_reconciled <= member_count)), excluded_reconciled_source TEXT, excluded_reconciled_out_of_scope INTEGER
    CHECK (excluded_reconciled_out_of_scope IS NULL OR (excluded_reconciled_out_of_scope >= 0
           AND excluded_reconciled IS NOT NULL
           AND excluded_reconciled_out_of_scope <= excluded_reconciled)), bag_key TEXT NOT NULL DEFAULT '');
CREATE TABLE quarantine (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,
    raw         BLOB NOT NULL,
    reason      TEXT NOT NULL,
    received_at REAL NOT NULL
, sha256 TEXT, length INTEGER, first8 TEXT, sanitized INTEGER NOT NULL DEFAULT 0);
CREATE TABLE user (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    username             TEXT NOT NULL UNIQUE,
    password_hash        TEXT NOT NULL,
    role                 TEXT NOT NULL,               -- admin | editor | viewer
    must_change_password INTEGER NOT NULL DEFAULT 0,
    disabled             INTEGER NOT NULL DEFAULT 0,
    created_at           REAL NOT NULL,
    updated_at           REAL NOT NULL
, display_name TEXT, email TEXT);
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
, purpose TEXT);
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
CREATE TRIGGER audit_log_no_update BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only');
END;
CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only');
END;
CREATE TABLE ne (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ip         TEXT NOT NULL UNIQUE,
    vendor     TEXT,
    first_seen REAL NOT NULL,
    last_seen  REAL NOT NULL
, organization_id INTEGER REFERENCES organization (id));
CREATE TABLE entity (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ne_id       INTEGER NOT NULL REFERENCES ne (id),
    parent_id   INTEGER REFERENCES entity (id),   -- NULL at level 0
    level       INTEGER NOT NULL,                 -- 0 = the NE itself
    key         TEXT NOT NULL,                    -- discriminator value, or the IP at level 0
    key_source  TEXT NOT NULL,                    -- varbind OID, or 'self' at level 0
    confidence  REAL NOT NULL,                    -- S_entity at promotion time (1.0 at level 0)
    first_seen  REAL NOT NULL,
    last_seen   REAL NOT NULL,
    UNIQUE (ne_id, parent_id, key)
);
CREATE INDEX idx_entity_ne ON entity (ne_id);
CREATE TABLE ingest_gap (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   REAL NOT NULL,
    ended_at     REAL NOT NULL,
    dropped      INTEGER NOT NULL,
    reason       TEXT NOT NULL                    -- queue_full | window_overflow
);
CREATE INDEX idx_ingest_gap_started ON ingest_gap (started_at);
CREATE TABLE varbind_profile (
    ne_id       INTEGER NOT NULL,
    class_id    INTEGER NOT NULL,
    varbind_oid TEXT NOT NULL,
    n_obs       INTEGER NOT NULL,
    n_repeat    INTEGER NOT NULL,
    n_monotonic INTEGER NOT NULL,
    n_numeric   INTEGER NOT NULL,
    n_distinct  INTEGER NOT NULL,
    score       REAL NOT NULL,
    role        TEXT,                             -- NULL | entity | severity | state
    updated_at  REAL NOT NULL,
    PRIMARY KEY (ne_id, class_id, varbind_oid)
);
CREATE INDEX idx_varbind_profile_ne ON varbind_profile (ne_id);
CREATE UNIQUE INDEX ux_alarm_entity ON alarm (entity_id, class_id, instance);
CREATE INDEX idx_alarm_entity ON alarm (entity_id);
CREATE INDEX idx_alarm_ne ON alarm (ne_id);
CREATE TABLE state_clear (
    class_id    INTEGER NOT NULL,
    varbind_oid TEXT NOT NULL,
    clear_value TEXT NOT NULL,     -- the terminating (resolved) state value
    raise_value TEXT NOT NULL,     -- the value that opens the alarm
    learned_at  REAL NOT NULL,
    PRIMARY KEY (class_id, varbind_oid)
);
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
CREATE TRIGGER scorer_config_no_update BEFORE UPDATE ON scorer_config
BEGIN
    SELECT RAISE(ABORT, 'scorer_config is append-only');
END;
CREATE TRIGGER scorer_config_no_delete BEFORE DELETE ON scorer_config
BEGIN
    SELECT RAISE(ABORT, 'scorer_config is append-only');
END;
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
CREATE TRIGGER governance_policy_no_update BEFORE UPDATE ON governance_policy
BEGIN
    SELECT RAISE(ABORT, 'governance_policy is append-only');
END;
CREATE TRIGGER governance_policy_no_delete BEFORE DELETE ON governance_policy
BEGIN
    SELECT RAISE(ABORT, 'governance_policy is append-only');
END;
CREATE TABLE governance_active (
    kind         TEXT PRIMARY KEY CHECK (kind IN ('rbac', 'scope')),
    policy_id    INTEGER NOT NULL REFERENCES governance_policy (id),
    activated_by TEXT,
    activated_at REAL NOT NULL
);
CREATE TABLE capture_run (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at              REAL NOT NULL,
    netcorenoc_version      TEXT NOT NULL,     -- the code that produced these rows
    -- NULL means "no configuration in effect" — the fail-safe state where the engine runs on the
    -- coded defaults. Not a missing value: a meaningful one, and the same convention
    -- `situation.scorer_config_id` has used since 0005.
    scorer_config_id        INTEGER,
    scorer_params_hash      TEXT,              -- the params fingerprint; a retune changes it
    -- The censoring bounds in effect. These determine what the sink COULD POSSIBLY have seen, so a
    -- dataset read without them is a dataset whose coverage cannot be interpreted.
    window_s                REAL NOT NULL,
    max_candidates          INTEGER NOT NULL,
    max_links_per_alarm     INTEGER NOT NULL,
    link_threshold          REAL NOT NULL,
    -- Retention in effect. Pruning a training corpus is CENSORING BY POLICY — legitimate, and it
    -- must be visible: a model trained under twelve months' retention and one trained under three
    -- are not comparable, and nothing else in the schema would record which was which.
    retention_sink_days     REAL NOT NULL,
    retention_sink_rows     INTEGER NOT NULL,
    retention_training_days REAL NOT NULL,
    retention_audit_days    REAL NOT NULL,
    -- The decay clock at run start. See `dataset_pair.a_epoch` for why an epoch is stored at all.
    a_epoch                 INTEGER NOT NULL,
    e_epoch                 INTEGER NOT NULL
);
CREATE TABLE dataset_observation (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    capture_run_id INTEGER NOT NULL REFERENCES capture_run(id),
    -- KEY. Joins only — see rule 2 above.
    alarm_id       INTEGER NOT NULL,
    ne_id          INTEGER,
    device_id      INTEGER NOT NULL,
    entity_id      INTEGER,
    class_id       INTEGER NOT NULL,
    -- The state at decision time. None of the following is recoverable afterwards.
    observed_at    REAL NOT NULL,
    alarm_count    INTEGER NOT NULL,  -- the alarm's count AT THIS DECISION; `alarm.count` advances
    -- Severity as received. NULL is an HONEST UNKNOWN, never a default (S8's rule, kept).
    severity       TEXT,
    severity_rank  INTEGER,
    instance       TEXT,              -- the dedup instance resolved for this trap
    trap_oid       TEXT NOT NULL,
    source_address TEXT,
    -- The raw material, as JSON. Deliberately NOT parsed into columns: "same OID root?", "same
    -- vendor?", "same /24?" are MODELLING DECISIONS, and freezing one now is the same mistake as
    -- freezing the label-derivation policy. Store the raw material; let the release that models
    -- decide what a feature is.
    varbinds       TEXT,
    -- The two populations (see `dataset_pair.lifecycle`).
    lifecycle      TEXT NOT NULL DEFAULT 'sink' CHECK (lifecycle IN ('sink', 'dataset')),
    promoted_at    REAL
);
CREATE INDEX idx_observation_sink ON dataset_observation (lifecycle, observed_at);
CREATE INDEX idx_observation_alarm ON dataset_observation (alarm_id);
CREATE TABLE dataset_pair (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    capture_run_id   INTEGER NOT NULL REFERENCES capture_run(id),
    -- KEY. Joins only. `alarm_a` is the window alarm, `alarm_b` the newly activated one.
    alarm_a          INTEGER NOT NULL,
    alarm_b          INTEGER NOT NULL,
    observation_a    INTEGER REFERENCES dataset_observation(id),
    observation_b    INTEGER REFERENCES dataset_observation(id),
    -- KEY. The situation `alarm_b` landed in AT CAPTURE TIME — the join promotion uses. Situation
    -- identity is not stable under `sid = min(sids)`, which is what `feedback.situation_id_at_label`
    -- and `situation.merged_into` exist to make traceable.
    situation_id     INTEGER,
    -- THE FEATURE VALUES AT DECISION TIME. Not one of these is recomputable afterwards.
    delta_t_s        REAL NOT NULL,
    -- The VALUE of A and of E — NOT `term_a`/`term_e`, which are `weight × value`. Dividing the
    -- weight back out FAILS WHEN A WEIGHT IS ZERO, and zero is a legal, supported setting: an admin
    -- may disable a term entirely, which is the most interesting configuration to have data about.
    -- `capture_run.scorer_config_id` keeps the contribution derivable in the direction that always
    -- works. (`FEEDBACK-DATASET-0.8-DRAFT.md` §3.2.)
    class_affinity   REAL NOT NULL,
    entity_affinity  REAL NOT NULL,
    -- The decay clock, one integer per matrix. A stored mass is read back through
    -- `_decayed(mass, at_epoch) = mass * (1-lambda)^(epoch - at_epoch)` (`learn.py:65`), so without
    -- the epoch a reading of 0.4 at epoch 12 and one at epoch 900 are NOT COMPARABLE — and no later
    -- join recovers it, because the matrix is mutated in place.
    --
    -- NOTE, because it is easy to state wrongly: a human label does NOT advance the epoch. Since
    -- v0.7.1's F36 fix neither feedback path ticks it (`confirm` calls learn_epoch(False), `split`
    -- calls penalize) — only a CLOSED SITUATION does. A label does mutate the MASSES (measured:
    -- 1.0 -> 0.5), so the contamination is real and travels through the values, not this counter.
    -- Measuring it is a join of these epochs against `feedback.created_at`.
    a_epoch          INTEGER NOT NULL,
    e_epoch          INTEGER NOT NULL,
    score            REAL NOT NULL,
    -- THE CHAMPION'S DECISION AT THAT INSTANT. Provenance, context, and a legitimate FEATURE — and
    -- the basis of champion/challenger comparison in v0.11.0. IT IS NOT A TARGET. Read the block
    -- above this CREATE TABLE before using it for anything.
    incumbent_linked INTEGER NOT NULL CHECK (incumbent_linked IN (0, 1)),
    storm            INTEGER NOT NULL CHECK (storm IN (0, 1)),
    -- Whether this ACCEPTED link was then dropped by MAX_LINKS_PER_ALARM. Without it, "the scorer
    -- rejected this pair" and "the cap discarded it" are indistinguishable — and Phase 0 measured
    -- that the second is 94% of the discard, so v0.9.0 would read a cap artefact as a scoring
    -- decision. Always 0 for a pair the scorer rejected.
    truncated        INTEGER NOT NULL DEFAULT 0 CHECK (truncated IN (0, 1)),
    evaluated_at     REAL NOT NULL,
    -- THE TWO POPULATIONS, and they differ in SEMANTICS, not merely in lifetime:
    --   'sink'    — destiny UNKNOWN, no label has arrived. Grows with TRAFFIC. Bounded by time AND
    --               a row cap, whichever binds first.
    --   'dataset' — destiny KNOWN, promoted by a label. Grows with LABELS. Bounded by the training
    --               retention tier.
    -- That is what makes growth bounded BY CONSTRUCTION rather than by a configured number: a
    -- 200 000-trap storm fills the sink and does not move the dataset.
    --
    -- One table with this column rather than two tables, chosen AGAINST the measurement
    -- (DECISIONS #107): two tables were faster on every operation, but a promoted pair would live
    -- in one table while its observations lived in another, so promotion would have to rewrite
    -- every reference as it moved. Here nothing moves — promotion is one UPDATE and ids are stable.
    lifecycle        TEXT NOT NULL DEFAULT 'sink' CHECK (lifecycle IN ('sink', 'dataset')),
    promoted_at      REAL
, features TEXT);
CREATE INDEX idx_pair_sink ON dataset_pair (lifecycle, evaluated_at);
CREATE INDEX idx_pair_situation ON dataset_pair (situation_id);
CREATE TABLE feedback_member (
    feedback_id INTEGER NOT NULL REFERENCES feedback(id) ON DELETE CASCADE,
    -- 'server' — AUTHORITATIVE, always written, from the server's own state at verdict time. This
    --            does not depend on the client and it survives the merge.
    -- 'client'  — EVIDENCE. Optional, bounded, UNTRUSTED, and NEVER used to validate the existence
    --             of anything: out-of-scope and non-existent ids are recorded exactly as reported
    --             and change nothing about the response, its status or its timing. Closing that
    --             existence oracle is a security requirement, not a nicety (F34/F37 precedent).
    source      TEXT NOT NULL CHECK (source IN ('server', 'client')),
    -- The bag is ORDERED, so the position is part of the record and cannot be recomputed.
    position    INTEGER NOT NULL,
    alarm_id    INTEGER NOT NULL,
    PRIMARY KEY (feedback_id, source, position)
) WITHOUT ROWID;
CREATE TABLE challenger_run (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at          REAL NOT NULL,
    netcorenoc_version  TEXT NOT NULL,     -- the code that produced this run
    -- The challenger's `LinkScorer` identity. It satisfies the v0.6.0 Protocol structurally, so
    -- these are the same two fields `scorer_config` carries for the champion — and they are here
    -- rather than there BECAUSE the challenger is not a configuration anything can activate.
    scorer_id           TEXT NOT NULL,
    contract_version    TEXT NOT NULL,
    -- Over the LEARNED COEFFICIENTS. NULL when nothing was fitted.
    params_fingerprint  TEXT,
    -- 'A' fabricates the negatives (`split` -> every pair negative); 'B' discards the minority
    -- (`confirm` bags only). BOTH ARE MANDATORY and both are reported — a release that picked one
    -- and reported its number would have assumed exactly what v0.8.0 refused to assume.
    -- 'none' is the honest value for a run that fitted nothing.
    derivation_policy   TEXT NOT NULL CHECK (derivation_policy IN ('A', 'B', 'none')),
    -- THE TRAINING WINDOW, as bounds rather than as a day count: a day count is a policy and these
    -- are the rows the policy actually selected. Without them a coefficient vector is a number with
    -- no population attached.
    train_oldest_at     REAL,
    train_newest_at     REAL,
    train_rows          INTEGER NOT NULL DEFAULT 0,
    -- THE EFFECTIVE SAMPLE SIZE, in the unit that is correct rather than the unit that is large.
    -- *n* is the number of independent BAGS, not the number of pairs; `train_rows` above is pairs
    -- and is recorded so the two can never be confused by a reader who only has this row.
    train_bags          INTEGER NOT NULL DEFAULT 0,
    train_split_bags    INTEGER NOT NULL DEFAULT 0,
    train_mixed_bags    INTEGER NOT NULL DEFAULT 0,
    train_incidents     INTEGER NOT NULL DEFAULT 0,
    train_operators     INTEGER NOT NULL DEFAULT 0,
    -- THE THRESHOLDS IN EFFECT, resolved, as JSON. `resolved = the more demanding of (project
    -- floor, deployment policy)` — a deployment may harden a requirement and can never soften one
    -- (DECISIONS #114). Recorded here because a "passed" that does not carry its bar is not a
    -- result; it is a claim.
    thresholds          TEXT NOT NULL,
    -- THE VERDICT, and the reason. `insufficiency` is JSON: which floors were unmet, by how much,
    -- and the projection of how long until they would be met at the measured labelling rate.
    -- "Not enough yet" is not actionable; "about seven months at the current rate" is.
    sufficient          INTEGER NOT NULL CHECK (sufficient IN (0, 1)),
    insufficiency       TEXT,
    -- THE FIT. JSON, one key per free parameter, NULL when nothing was fitted. Text rather than
    -- columns because the feature set is a modelling decision this release is explicitly not
    -- freezing for v0.10.0 — the same reasoning that left `dataset_observation.varbinds` opaque.
    coefficients        TEXT,
    iterations          INTEGER,
    learning_rate       REAL,
    fit_seconds         REAL,
    -- The sampling rate this run wrote its online opinions at. Deployment-settable; the DEPLOYMENT
    -- CHOOSES THE RATE AND THE DURATION, NOT WHETHER ONLINE EVER RUNS — a model that was never
    -- measured inside the engine must not be promotable two releases later.
    sample_rate         REAL NOT NULL DEFAULT 0.0,
    -- The capture run whose rows this was fitted from, so a retune of the CHAMPION that re-opened
    -- a capture run is traceable to the challenger runs on either side of it.
    capture_run_id      INTEGER REFERENCES capture_run(id)
, loss_trace TEXT, loss_trace_stride INTEGER);
CREATE TABLE shadow_opinion (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    challenger_run_id INTEGER NOT NULL REFERENCES challenger_run(id),
    -- KEY. Joins only. Same orientation as `dataset_pair`: `alarm_a` is the window alarm,
    -- `alarm_b` the newly activated one, so the two tables join without a case analysis.
    alarm_a           INTEGER NOT NULL,
    alarm_b           INTEGER NOT NULL,
    situation_id      INTEGER,
    -- THE FEATURES AS SERVED. Four, matching the pre-registration §2.3 exactly.
    delta_t_s         REAL NOT NULL,
    class_affinity    REAL NOT NULL,
    entity_affinity   REAL NOT NULL,
    same_oid_root     INTEGER NOT NULL CHECK (same_oid_root IN (0, 1)),
    -- THE OPINION, AND NOBODY ACTS ON IT. `score` is the challenger's probability; `linked` is that
    -- probability against its own threshold. Neither reaches a situation, a link, the UI, an
    -- operator, `learn.penalize()`, or `engine.scorer`.
    score             REAL NOT NULL,
    linked            INTEGER NOT NULL CHECK (linked IN (0, 1)),
    -- THE CHAMPION'S DECISION AT THE SAME INSTANT. Provenance and comparison basis, exactly as on
    -- `dataset_pair` — and NOT A TARGET. No metric that decides promotion may be computed against
    -- it, and this release has no promotion to decide.
    incumbent_linked  INTEGER NOT NULL CHECK (incumbent_linked IN (0, 1)),
    evaluated_at      REAL NOT NULL
);
CREATE INDEX idx_shadow_age ON shadow_opinion (evaluated_at);
CREATE INDEX idx_shadow_pair ON shadow_opinion (alarm_a, alarm_b, evaluated_at);
CREATE TABLE feedback_exclusion (
    feedback_id INTEGER NOT NULL REFERENCES feedback(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    alarm_id    INTEGER NOT NULL,
    PRIMARY KEY (feedback_id, position)
) WITHOUT ROWID;
CREATE TABLE holdout_seal (
    id             INTEGER PRIMARY KEY,
    singleton      INTEGER NOT NULL DEFAULT 1 UNIQUE CHECK (singleton = 1),
    release        TEXT    NOT NULL,
    plan_sha256    TEXT    NOT NULL,
    created_at     REAL    NOT NULL,
    digest         TEXT    NOT NULL,
    incident_count INTEGER NOT NULL CHECK (incident_count >= 0),
    -- The corpus the seal was cut from, recorded so a later reader can tell whether the corpus has
    -- grown since. A seal over a third of 37 incidents and a seal over a third of 3 700 are
    -- different objects, and nothing else on the row would say so.
    corpus_incidents INTEGER NOT NULL CHECK (corpus_incidents >= 0)
);
CREATE TRIGGER holdout_seal_no_update BEFORE UPDATE ON holdout_seal
BEGIN
    SELECT RAISE(ABORT, 'holdout_seal is immutable: a seal that can be edited is not a seal');
END;
CREATE TRIGGER holdout_seal_no_delete BEFORE DELETE ON holdout_seal
BEGIN
    SELECT RAISE(ABORT, 'holdout_seal is immutable: deleting a seal is how it gets re-cut');
END;
CREATE TABLE holdout_seal_member (
    seal_id     INTEGER NOT NULL REFERENCES holdout_seal(id),
    position    INTEGER NOT NULL,
    incident_id INTEGER NOT NULL,
    PRIMARY KEY (seal_id, position)
);
CREATE TRIGGER holdout_seal_member_no_update BEFORE UPDATE ON holdout_seal_member
BEGIN
    SELECT RAISE(ABORT, 'holdout_seal_member is immutable');
END;
CREATE TRIGGER holdout_seal_member_no_delete BEFORE DELETE ON holdout_seal_member
BEGIN
    SELECT RAISE(ABORT, 'holdout_seal_member is immutable');
END;
CREATE TABLE holdout_access (
    id          INTEGER PRIMARY KEY,
    at          REAL    NOT NULL,
    release     TEXT    NOT NULL,
    plan_sha256 TEXT,
    purpose     TEXT    NOT NULL,
    granted     INTEGER NOT NULL CHECK (granted IN (0, 1)),
    -- What came back, or why nothing did. Free text on purpose: the refusal reasons are a design
    -- surface that will grow, and a CHECK on them would have to be rewritten by every release that
    -- adds one -- 0010's `acquisition_channel` reasoning, applied again.
    outcome     TEXT    NOT NULL
, prev_hash TEXT, entry_hash TEXT, chain_source TEXT);
CREATE TRIGGER holdout_access_no_update BEFORE UPDATE ON holdout_access
BEGIN
    SELECT RAISE(ABORT, 'holdout_access is append-only');
END;
CREATE TRIGGER holdout_access_no_delete BEFORE DELETE ON holdout_access
BEGIN
    SELECT RAISE(ABORT, 'holdout_access is append-only');
END;
CREATE TABLE model_version (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    kind              TEXT    NOT NULL,            -- 'additive' | 'logistic'; dispatched, not trusted
    contract_version  TEXT    NOT NULL,            -- semver of the feature/output schema, e.g. '1.0'
    params_document   TEXT    NOT NULL,            -- canonical JSON: sorted keys, tight separators
    params_hash       TEXT    NOT NULL,            -- sha256 over (kind, contract_version, document)
    -- WHICH SHADOW RUN PRODUCED THESE COEFFICIENTS. Nullable, because an `additive` model version
    -- may be registered from hand-chosen parameters and there is no run to name; a PROMOTION of one
    -- is refused without it, which is a rule the gate enforces rather than the schema, because the
    -- schema cannot tell a registration from a proposal.
    challenger_run_id INTEGER REFERENCES challenger_run (id),
    created_by        TEXT,
    created_at        REAL    NOT NULL,
    note              TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX idx_model_version_created ON model_version (created_at);
CREATE TRIGGER model_version_no_update BEFORE UPDATE ON model_version
BEGIN
    SELECT RAISE(ABORT, 'model_version is append-only');
END;
CREATE TRIGGER model_version_no_delete BEFORE DELETE ON model_version
BEGIN
    SELECT RAISE(ABORT, 'model_version is append-only');
END;
CREATE TABLE promotion (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    model_version_id  INTEGER NOT NULL REFERENCES model_version (id),
    -- THE SERVER'S VERDICT. A request body may name a candidate; it may NEVER assert one. The CHECK
    -- pins the three-valued type into the schema so a fourth value cannot be introduced by a caller
    -- — `INSUFFICIENT_EVIDENCE` in particular can never be lost by being written as a NULL.
    verdict           TEXT    NOT NULL
                      CHECK (verdict IN ('BETTER', 'NOT_BETTER', 'INSUFFICIENT_EVIDENCE')),
    triggers          TEXT    NOT NULL DEFAULT '[]',   -- canonical JSON array of Trigger names
    metrics           TEXT    NOT NULL DEFAULT '{}',   -- canonical JSON, four quantities, both arms
    evaluation_run_id TEXT,                            -- -> evaluation_fold.run_id
    plan_sha256       TEXT    NOT NULL,
    query_count       INTEGER NOT NULL CHECK (query_count >= 0),
    approved_by       TEXT    NOT NULL,
    decided_at        REAL    NOT NULL,
    outcome           TEXT    NOT NULL CHECK (outcome IN ('applied', 'refused')),
    -- Free text on purpose, for `holdout_access.outcome`'s reason: the refusal messages are a
    -- design surface that will grow, and a CHECK on them would have to be rewritten by every
    -- release that adds one.
    refusal_reason    TEXT,
    unavailable       TEXT    NOT NULL DEFAULT '[]',
    -- A REFUSAL HAS A REASON AND AN APPLICATION DOES NOT. Enforced here rather than by the writer,
    -- because "the refusal path forgot to say why" is precisely the defect that would make the two
    -- refusals indistinguishable after the fact — which is the whole thing this release is for.
    CHECK (
        (outcome = 'refused' AND refusal_reason IS NOT NULL AND length(refusal_reason) > 0)
     OR (outcome = 'applied' AND refusal_reason IS NULL)
    ),
    -- AND AN APPLIED PROMOTION IS ONLY EVER `BETTER`. The gate refuses on the other two by two
    -- separate code paths; this is the database saying the same thing, so a future caller that
    -- bypassed the gate could still not record a promotion the evidence did not support.
    CHECK (outcome = 'refused' OR verdict = 'BETTER')
);
CREATE INDEX idx_promotion_decided ON promotion (decided_at);
CREATE TRIGGER promotion_no_update BEFORE UPDATE ON promotion
BEGIN
    SELECT RAISE(ABORT, 'promotion is append-only: a decision that can be edited is not a record');
END;
CREATE TRIGGER promotion_no_delete BEFORE DELETE ON promotion
BEGIN
    SELECT RAISE(ABORT, 'promotion is append-only: deleting a refusal is how a history becomes a table of successes');
END;
CREATE TABLE evaluation_fold (
    run_id      TEXT    NOT NULL,
    incident_id INTEGER NOT NULL,
    repeat      INTEGER NOT NULL CHECK (repeat >= 0),
    fold        INTEGER NOT NULL CHECK (fold >= 0),
    created_at  REAL    NOT NULL,
    -- One assignment per (run, incident, repetition). The PRIMARY KEY is the guard: a second write
    -- for the same triple fails at SQLite rather than silently replacing an assignment a promotion
    -- may already have cited.
    PRIMARY KEY (run_id, incident_id, repeat)
);
CREATE TRIGGER evaluation_fold_no_update BEFORE UPDATE ON evaluation_fold
BEGIN
    SELECT RAISE(ABORT, 'evaluation_fold is immutable: a cited fold assignment that can move is not a citation');
END;
CREATE TRIGGER evaluation_fold_no_delete BEFORE DELETE ON evaluation_fold
BEGIN
    SELECT RAISE(ABORT, 'evaluation_fold is immutable');
END;
CREATE TABLE "scorer_active" (
    id               INTEGER PRIMARY KEY CHECK (id = 1),
    config_id        INTEGER REFERENCES scorer_config (id),
    model_version_id INTEGER REFERENCES model_version (id),
    activated_by     TEXT,
    activated_at     REAL NOT NULL,
    -- EXACTLY ONE. `IS NOT NULL` yields 0 or 1 in SQLite, so the sum is the count of set pointers.
    -- Written as a sum rather than as a pair of OR'd clauses because the sum says "exactly one" in
    -- one expression that cannot be half-edited into "at least one".
    CHECK ((config_id IS NOT NULL) + (model_version_id IS NOT NULL) = 1)
);
CREATE TABLE situation_event (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    -- KEY. A join, never a feature. No FK: see above.
    situation_id      INTEGER NOT NULL,
    kind              TEXT NOT NULL CHECK (kind IN (
        'verdict',          -- a `confirm` or `split` recorded through the feedback surface
        'move',             -- one alarm moved from this situation to another
        'merge',            -- another situation merged into this one, by an operator
        'operator_split',   -- this situation split into two, by an operator
        'manual_clear',     -- an operator hand-cleared a zombie alarm
        'self_clear',       -- every member cleared and the appliance resolved the situation
        'idle_close',       -- the idle sweep resolved it
        'operator_close',   -- an operator closed it
        'rename'            -- an operator named it
    )),
    -- WHO. NULL for the two the appliance does itself (`self_clear`, `idle_close`), and NULL is an
    -- honest unknown rather than a default — "the appliance" is not an actor with a confidence.
    actor             TEXT,
    role              TEXT,
    at                REAL NOT NULL,
    -- THE OPERATOR'S CONFIDENCE, ON A 0-1 SCALE, IN ITS OWN COLUMN.
    --
    -- `PREREGISTRATION-0.16.0.md` §4. It is recorded PER ACTOR precisely so a later release can
    -- measure whether a given operator's stated 0.8 corresponds to an 0.8 rate of being right —
    -- which is the only way this stops being a convention. Until that measurement exists, the
    -- multiplier `m(c) = 0.6 + 0.4c` is not revised.
    --
    -- It is **NEVER** multiplied into a stored `TrainingRow.weight`. That field already carries the
    -- design-effect correction (`1/len(bucket)`) and the class balance, and folding a third meaning
    -- into one number makes all three unrecoverable. The multiplier is applied AT DERIVATION and
    -- the composition is recorded in the run's diagnostics.
    --
    -- NULL means **not reported** — the gestures that predate a confidence control, and the two the
    -- appliance performs itself. It is not 0.0, which would fall below the floor and mean something
    -- entirely different.
    confidence        REAL CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)),
    -- THE CHANNEL, extended and never repeated (DECISIONS #126). `move`, `merge` and
    -- `operator_split` join the existing `organic`, `close`, `confirm` and `split`. Channels are
    -- **reported separately and never averaged**: a merge selects for a different population from
    -- the one an operator browses, and blending them destroys the bias characterisation
    -- retroactively, including for rows already written.
    acquisition_channel TEXT,
    -- KEY. The `feedback` row this gesture produced, when it produced one. A gesture whose
    -- bag-level assertion is exactly what a `split` with marked members means writes one; a gesture
    -- whose assertion has no such shape writes none rather than over-asserting.
    feedback_id       INTEGER,
    -- KEY. The other situation, for `move` (the destination) and `merge` (the source).
    peer_situation_id INTEGER,
    -- KEY. The alarm this gesture is about, for `move` and `manual_clear`.
    alarm_id          INTEGER,
    -- THE SNAPSHOT, SUMMARISED. The rows are in `situation_event_member`; these two are here for
    -- the same reason `feedback.member_digest` and `member_count` are on the label row — a cheap
    -- comparison that does not need the child table. `member_count = 0` is legitimate and
    -- informative: a gesture on a situation whose members have all moved away is a real event.
    member_count      INTEGER NOT NULL,
    member_digest     TEXT NOT NULL,
    peer_member_count INTEGER,
    peer_member_digest TEXT,
    -- WHETHER THIS GESTURE ASSERTS ANYTHING ABOUT A GROUPING.
    --
    -- 0 for `manual_clear` and `self_clear`, always, and that is the invariant this release exists
    -- to keep: they are facts about an ALARM's lifecycle, not about correlation, and a fact about a
    -- different question may not do the work of a measurement about this one
    -- (`PREREGISTRATION-0.16.0.md` §1). It is stored rather than derived from `kind` so the
    -- prohibition is a value a query can count and a guard can assert, rather than a rule written
    -- in a `CASE` expression somewhere.
    --
    -- Also 0 when the operator's confidence was below the registered floor of 0.50: the action
    -- still happened and the event is recorded in full — the operator is running the network, not
    -- labelling it — and it produces no training row.
    produces_training_rows INTEGER NOT NULL DEFAULT 0
        CHECK (produces_training_rows IN (0, 1)),
    -- -- BAG PROVENANCE: RECORDED, AND NOT CONSUMED ----------------------------------------------
    --
    -- `PREREGISTRATION-0.16.0.md` §5, and the plan is explicit that a build which supplies either
    -- of these to a scorer, a promotion input or a verdict trigger has violated it. They are here
    -- because they CANNOT BE RECOMPUTED LATER — the scores decay, membership mutates, and `0008`'s
    -- first rule applies — and they are REPORTED in the census, stratified.
    --
    -- The weakest link's margin over the threshold, within the bag at the instant of the gesture. A
    -- grouping whose weakest pair cleared by 0.01 is one scorer nudge from falling apart; one whose
    -- weakest cleared by 0.3 is not. `ui/app/views/parts/why.js` already computes this number for
    -- the operator (#245); this records the server's own reading of it beside the assertion.
    -- NULL when the situation had no link at all — a one-member bag has no weakest pair, and 0.0
    -- would say something false about one.
    bag_weakest_margin REAL,
    -- Whether the bag's link graph has a BRIDGE whose removal splits it into two parts each above a
    -- registered minimum size. A bag held together by one frail edge is epistemically different
    -- from a densely-linked one, and today both enter training indistinguishable.
    bag_has_bridge    INTEGER CHECK (bag_has_bridge IS NULL OR bag_has_bridge IN (0, 1)),
    -- **The measurement the line above is a threshold on**, stored beside it because the plan
    -- registers *"two parts each above a registered minimum size"* and does not fix the size. A
    -- boolean alone would bake this build's choice of that number into the corpus permanently, and
    -- a later release that registered a different minimum could not recompute the answer — the link
    -- graph is gone by then. So the raw quantity is recorded: the number of members on the SMALLER
    -- side of the best bridge, NULL when the graph has no bridge at all. `bag_has_bridge` is this
    -- value tested against `provenance.MIN_BRIDGE_SIDE`, and the choice of that constant is an open
    -- question for v0.16.1 rather than a decision this release made with a result in view.
    bag_bridge_min_side INTEGER,
    bag_link_count    INTEGER
);
CREATE INDEX idx_situation_event_situation ON situation_event (situation_id, at);
CREATE UNIQUE INDEX idx_situation_event_feedback
    ON situation_event (feedback_id) WHERE feedback_id IS NOT NULL;
CREATE INDEX idx_situation_event_channel ON situation_event (acquisition_channel, at);
CREATE TABLE situation_event_member (
    event_id INTEGER NOT NULL REFERENCES situation_event(id) ON DELETE CASCADE,
    source   TEXT NOT NULL CHECK (source IN ('server', 'peer')),
    position INTEGER NOT NULL,
    alarm_id INTEGER NOT NULL,
    PRIMARY KEY (event_id, source, position)
) WITHOUT ROWID;
CREATE UNIQUE INDEX idx_feedback_situation_verdict_bag
    ON feedback (situation_id, verdict, bag_key);
CREATE TABLE "label" (
    kind       TEXT NOT NULL, -- ne | class | severity
    target_id  INTEGER NOT NULL,
    qualifier  TEXT NOT NULL DEFAULT '', -- '' = the whole target; a varbind OID refines it later
    label      TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (kind, target_id, qualifier)
);
CREATE INDEX idx_pair_alarm_ab ON dataset_pair (alarm_a, alarm_b);
CREATE INDEX idx_pair_alarm_ba ON dataset_pair (alarm_b, alarm_a);
CREATE INDEX idx_alarm_severity_source ON alarm (severity_source)
    WHERE severity_source IS NOT NULL;
CREATE TABLE organization (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    -- The operator's own name for the provider. Not a key, not a selector, not read by any
    -- authorization decision — see the banner above.
    name       TEXT    NOT NULL UNIQUE,
    -- A stable short handle for the API and the console's filter chips, so a rename does not
    -- change every stored reference. Lower-case, digits and hyphens; the API validator enforces
    -- the shape and this column carries no CHECK, for the reason `0014` gives about `status`:
    -- adding one to an existing column needs a table rebuild, and the application has one write
    -- site.
    slug       TEXT    NOT NULL UNIQUE,
    created_at REAL    NOT NULL,
    -- `1` for the row this migration creates. It cannot be deleted, because every element and
    -- every window must belong somewhere and a delete would orphan them.
    is_default INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_ne_organization ON ne (organization_id);
CREATE TABLE maintenance_window (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    description     TEXT    NOT NULL DEFAULT '',
    organization_id INTEGER NOT NULL REFERENCES organization (id),
    status          TEXT    NOT NULL,

    -- WHEN. `starts_at`/`ends_at` are absolute instants — seconds since the epoch, UTC — because
    -- an instant is what the ingest check compares a trap's timestamp against. `tz` is the
    -- canonical IANA zone the operator chose the site in, kept so the console can render the
    -- site's wall clock and so an EXTEND is computed in the zone the work is happening in rather
    -- than in the viewer's.
    --
    -- **The zone is stored beside the instants, not instead of them** (ADR #362). A window across
    -- a DST transition has two offsets, so a stored offset would make half of it an hour wrong —
    -- which is one of this release's injections.
    tz              TEXT    NOT NULL,
    starts_at       REAL    NOT NULL,
    ends_at         REAL    NOT NULL,
    all_day         INTEGER NOT NULL DEFAULT 0,

    -- THE PATCH BAND. Equipment does not come back the instant a window closes: a chassis
    -- rebooting at 11:58 emits its linkUp storm at 12:03, and an operator who declared 10:00-12:00
    -- did not mean to be paged for it. `patch_s` extends the window by the same margin at BOTH
    -- ends, so the interval actually in force is
    --
    --     [starts_at - patch_s, ends_at + patch_s)
    --
    -- and the console's timeline bar draws exactly that: a leading band, the declared window, a
    -- trailing band. The declared bounds are what the card says and what an audit row records;
    -- the effective bounds are what the ingest check compares against, and `engine/mw/index.py`
    -- computes them once, when the window is compiled.
    patch_s         REAL    NOT NULL DEFAULT 600,

    -- THE STATE LEDGER (II.2). On by default. Off is true discard, and the console shows the risk
    -- in the operator's own words rather than hiding the switch — see `engine/mw/ledger.py`.
    ledger_enabled  INTEGER NOT NULL DEFAULT 1,

    -- WHO SEES THE DETAILS. `everyone` or `editors`. The window's EXISTENCE ignores this column
    -- entirely: every role sees a marker on a device or a situation under a window, because a
    -- silent quiet host is the failure mode this feature would otherwise create.
    visibility      TEXT    NOT NULL DEFAULT 'editors',

    -- WHO DECLARED IT. `owner_ref` is the principal reference the audit log already uses
    -- (`user:<id>` or `token:<id>`), so a window and its audit rows join without a second notion
    -- of identity. `created_by_agent` is 1 when the creator authenticated with a service token —
    -- **an agent-created window is marked in the API and on screen** (Part III), because an
    -- operator reading a list of planned work is entitled to know which entries a human wrote.
    owner_ref       TEXT    NOT NULL,
    owner_role      TEXT    NOT NULL,
    created_by_agent INTEGER NOT NULL DEFAULT 0,

    -- D6. `needs_confirmation` is decided ONCE, at create, from the declared duration against
    -- `CONFIRMATION_THRESHOLD_S`, and stored — rather than recomputed on every read from the
    -- current bounds. An extend that pushes a four-hour window past six hours re-decides it
    -- explicitly at that one write site, which is a visible line of code; recomputing on read
    -- would mean a window silently losing its confirmation because somebody moved its end.
    needs_confirmation INTEGER NOT NULL DEFAULT 0,
    confirmed_at    REAL,
    confirmed_by    TEXT,

    created_at      REAL    NOT NULL,
    updated_at      REAL    NOT NULL,
    cancelled_at    REAL,
    -- Set when the window stops being in force, by the clock or by "end now". Distinct from
    -- `ends_at`, which is what was DECLARED: an engineer who finishes at 11:20 leaves
    -- `ends_at = 12:00` and `ended_at = 11:20`, and both facts matter.
    ended_at        REAL,

    -- IDEMPOTENCY (Part III). An agent that retries after a timeout must not create two windows.
    -- The key is the caller's, opaque here, and the partial unique index below is what enforces
    -- it — a plain UNIQUE would collide every window created without one.
    idempotency_key TEXT
);
CREATE UNIQUE INDEX idx_mw_idempotency ON maintenance_window (idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX idx_mw_status_starts ON maintenance_window (status, starts_at);
CREATE INDEX idx_mw_ends ON maintenance_window (ends_at);
CREATE INDEX idx_mw_organization ON maintenance_window (organization_id, starts_at);
CREATE TABLE maintenance_window_target (
    window_id INTEGER NOT NULL REFERENCES maintenance_window (id) ON DELETE CASCADE,
    ne_id     INTEGER NOT NULL REFERENCES ne (id),
    PRIMARY KEY (window_id, ne_id)
);
CREATE INDEX idx_mw_target_ne ON maintenance_window_target (ne_id);
CREATE TABLE maintenance_window_rule (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    window_id      INTEGER NOT NULL REFERENCES maintenance_window (id) ON DELETE CASCADE,
    ne_id          INTEGER NOT NULL REFERENCES ne (id),
    kind           TEXT    NOT NULL,           -- severity | oid | slot

    -- kind='severity': an X.733 rank, so SMALLER IS MORE SEVERE. A trap admits when its placed
    -- rank is <= this. A trap with NO placed severity never admits — which is why D5 shipped
    -- first, and why a severity rule on a v0.20.0 appliance would have admitted nothing at all.
    severity_rank  INTEGER,

    -- kind='oid': the subtree, and WHERE TO LOOK FOR IT. `match_on` is `trap` (the notification's
    -- own `snmpTrapOID.0`) or `varbind` (any varbind the trap carries). Both are supported and
    -- the rule says which, because the maintainer's example arc is shaped like a table column
    -- rather than a notification — see `MatchOn` in `engine/mw/rules.py`.
    --
    -- Matched on ARC BOUNDARIES, never on string prefixes: a rule for `…1.1.1.1` must not catch
    -- `…1.1.1.10`. That is this feature's most likely bug and it has its own injection.
    oid_root       TEXT,
    match_on       TEXT,

    -- kind='slot': absolute instants, resolved once at write time against the window's zone.
    -- Half-open, `[starts, ends)`.
    slot_starts_at REAL,
    slot_ends_at   REAL,

    created_at     REAL    NOT NULL
);
CREATE INDEX idx_mw_rule_window ON maintenance_window_rule (window_id, ne_id);
CREATE TABLE maintenance_ledger (
    window_id  INTEGER NOT NULL REFERENCES maintenance_window (id) ON DELETE CASCADE,
    ne_id      INTEGER NOT NULL REFERENCES ne (id),
    -- The same `(device_id, class_id, instance)` triple `alarm` dedups on, so a surfaced entry
    -- lands on exactly the row a collected trap would have opened — and a later real trap for the
    -- same fault bumps that row rather than opening a second one.
    device_id  INTEGER NOT NULL REFERENCES device (id),
    class_id   INTEGER NOT NULL REFERENCES alarm_class (id),
    instance   TEXT    NOT NULL DEFAULT '',
    raised_at  REAL,
    cleared_at REAL,
    -- When the end-of-window sweep turned this into an alarm. NULL means it has not, and the
    -- sweep is idempotent because it reads this column.
    surfaced_at REAL,
    PRIMARY KEY (window_id, device_id, class_id, instance)
);
CREATE INDEX idx_alarm_surfaced ON alarm (surfaced_from_window_id)
    WHERE surfaced_from_window_id IS NOT NULL;
CREATE TABLE host_sample (
    at          REAL PRIMARY KEY,
    cpu_pct     REAL,
    mem_pct     REAL,
    disk_pct    REAL,
    db_mb       REAL,
    queue_depth INTEGER
, traps_per_s REAL, latency_ms REAL, classified INTEGER, classified_per_s REAL, latency_max_ms REAL, over_target INTEGER, over_limit INTEGER, spool_mb REAL) WITHOUT ROWID;
CREATE INDEX idx_alarm_first_seen ON alarm (first_seen);
CREATE INDEX idx_alarm_cleared_at ON alarm (cleared_at) WHERE cleared_at IS NOT NULL;
CREATE TABLE class_rule (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Dotted numeric arcs. Validated by the API; no CHECK here, for `0014`'s reason — the
    -- application has two write sites and both validate the same shape.
    oid         TEXT    NOT NULL,
    -- 1: this node and every OID beneath it. 0: exactly this OID.
    subtree     INTEGER NOT NULL DEFAULT 0,
    -- NULL means the rule does not name, or does not grade. A rule that does neither is refused.
    name        TEXT,
    severity    TEXT,
    -- What the operator's file said the vendor was. Informational: the vendor shown for a class is
    -- derived from its enterprise arc (`known_oids.vendor_of`) unless an imported row says otherwise.
    vendor      TEXT,
    -- 'declared' (written in the console, one rule at a time) or 'imported' (from a file).
    source      TEXT    NOT NULL,
    -- The file an imported row came from, by name, so the screen can say which import wrote it.
    origin      TEXT,
    created_by  TEXT,
    created_at  REAL    NOT NULL,
    updated_at  REAL    NOT NULL,
    UNIQUE (oid, subtree, source)
);
CREATE INDEX idx_class_rule_source ON class_rule (source);
CREATE TABLE notice_snooze (
    user_id    INTEGER NOT NULL REFERENCES user (id) ON DELETE CASCADE,
    digest     TEXT    NOT NULL,
    -- The warning as it read when snoozed, so the list of what is snoozed can say what it was.
    text       TEXT    NOT NULL,
    security   INTEGER NOT NULL DEFAULT 0,
    -- '24h' | '7d' | 'change'. 'change' is refused for a security warning by the API.
    mode       TEXT    NOT NULL,
    -- When it returns. NULL only for 'change', which returns when the text does.
    until      REAL,
    created_at REAL    NOT NULL,
    PRIMARY KEY (user_id, digest)
);
CREATE TABLE user_avatar (
    user_id    INTEGER PRIMARY KEY REFERENCES user (id) ON DELETE CASCADE,
    mime       TEXT    NOT NULL,               -- image/png | image/webp | image/jpeg
    image      BLOB    NOT NULL,
    sha256     TEXT    NOT NULL,               -- hex digest of `image`: the ETag and the URL version
    width      INTEGER NOT NULL,
    height     INTEGER NOT NULL,
    updated_at REAL    NOT NULL
);
CREATE TABLE episode_pair (
    kind    TEXT    NOT NULL CHECK (kind IN ('ne', 'cls', 'item')),
    k       TEXT    NOT NULL,
    count   INTEGER NOT NULL CHECK (count >= 1),
    last_ts REAL    NOT NULL,
    PRIMARY KEY (kind, k)
);
CREATE TABLE decider_setting (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    mode    TEXT    NOT NULL CHECK (mode IN ('shipped', 'site', 'additive')),
    set_by  TEXT    NOT NULL,
    set_at  REAL    NOT NULL,
    reason  TEXT    NOT NULL DEFAULT ''
, pinned TEXT);
CREATE TRIGGER decider_setting_no_update BEFORE UPDATE ON decider_setting
BEGIN
    SELECT RAISE(ABORT, 'decider_setting is append-only');
END;
CREATE TRIGGER decider_setting_no_delete BEFORE DELETE ON decider_setting
BEGIN
    SELECT RAISE(ABORT, 'decider_setting is append-only');
END;
CREATE TABLE autonomy_setting (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    grouping          INTEGER NOT NULL DEFAULT 0 CHECK (grouping IN (0, 1)),
    naming            INTEGER NOT NULL DEFAULT 0 CHECK (naming IN (0, 1)),
    closing           INTEGER NOT NULL DEFAULT 0 CHECK (closing IN (0, 1)),
    severity          INTEGER NOT NULL DEFAULT 0 CHECK (severity IN (0, 1)),
    agreement_floor   REAL    NOT NULL DEFAULT 0.8 CHECK (agreement_floor > 0 AND agreement_floor <= 1),
    window            INTEGER NOT NULL DEFAULT 30 CHECK (window >= 5 AND window <= 1000),
    min_judged        INTEGER NOT NULL DEFAULT 8 CHECK (min_judged >= 3),
    confidence_floor  REAL    NOT NULL DEFAULT 0.9 CHECK (confidence_floor >= 0.5 AND confidence_floor < 1),
    set_by            TEXT    NOT NULL,
    set_at            REAL    NOT NULL,
    reason            TEXT    NOT NULL DEFAULT ''
);
CREATE TRIGGER autonomy_setting_no_update BEFORE UPDATE ON autonomy_setting
BEGIN
    SELECT RAISE(ABORT, 'autonomy_setting is append-only');
END;
CREATE TRIGGER autonomy_setting_no_delete BEFORE DELETE ON autonomy_setting
BEGIN
    SELECT RAISE(ABORT, 'autonomy_setting is append-only');
END;
CREATE TABLE autonomy_decision (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    situation_id  INTEGER NOT NULL REFERENCES situation (id),
    grade         TEXT    NOT NULL CHECK (grade IN ('grouping', 'naming', 'closing', 'severity')),
    action        TEXT    NOT NULL,
    decider       TEXT    NOT NULL,
    confidence    REAL    NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    explanation   TEXT    NOT NULL CHECK (length(explanation) > 2),
    at            REAL    NOT NULL,
    verdict       TEXT    CHECK (verdict IS NULL OR verdict IN ('agreed', 'disagreed')),
    verdict_at    REAL,
    verdict_by    TEXT,
    verdict_why   TEXT
);
CREATE INDEX idx_autonomy_decision_pending ON autonomy_decision (verdict, at);
CREATE INDEX idx_autonomy_decision_situation ON autonomy_decision (situation_id);
CREATE TABLE search_run (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   REAL    NOT NULL,
    started_by   TEXT    NOT NULL,
    seed         INTEGER NOT NULL,
    budget       TEXT    NOT NULL,          -- canonical JSON of search.Budget
    status       TEXT    NOT NULL CHECK (status IN ('running', 'done', 'stopped', 'failed', 'refused')),
    finished_at  REAL,
    note         TEXT    NOT NULL DEFAULT '',
    model_version_id INTEGER REFERENCES model_version (id),
    rows         INTEGER,
    judgement    TEXT                       -- canonical JSON of the paired comparison, when run
);
CREATE TABLE search_trial (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES search_run (id),
    trial       INTEGER NOT NULL,
    rung        INTEGER NOT NULL,
    params      TEXT    NOT NULL,           -- canonical JSON
    rounds      INTEGER NOT NULL,
    best_round  INTEGER NOT NULL,
    train_loss  REAL,
    valid_loss  REAL,
    seconds     REAL    NOT NULL,
    status      TEXT    NOT NULL CHECK (status IN ('done', 'stopped', 'failed')),
    trace       TEXT    NOT NULL DEFAULT '[]',
    UNIQUE (run_id, trial, rung)
);
CREATE INDEX idx_situation_proposal
    ON situation (proposed_into) WHERE proposed_into IS NOT NULL;
CREATE TABLE proposal_decision (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    pending_situation_id INTEGER NOT NULL,  -- KEY. No FK: see above.
    target_situation_id  INTEGER NOT NULL,  -- KEY.
    decision             TEXT    NOT NULL CHECK (decision IN ('accept', 'reject')),
    actor                TEXT    NOT NULL,
    role                 TEXT,
    at                   REAL    NOT NULL,
    -- The operator's confidence, on `0014`'s scale and floor; NULL = not reported.
    confidence           REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    -- The MODEL's probability at decision time and which model proposed. RECORDED, NEVER A FEATURE:
    -- a label must not learn from the opinion it is judging (the `incumbent_linked` rule).
    proposal_confidence  REAL,
    decider              TEXT,
    produces_training_rows INTEGER NOT NULL DEFAULT 1 CHECK (produces_training_rows IN (0, 1))
);
CREATE INDEX idx_proposal_decision_at ON proposal_decision (at);
CREATE TABLE proposal_decision_member (
    decision_id INTEGER NOT NULL REFERENCES proposal_decision (id) ON DELETE CASCADE,
    side        TEXT    NOT NULL CHECK (side IN ('pending', 'target')),
    position    INTEGER NOT NULL,
    alarm_id    INTEGER NOT NULL,
    PRIMARY KEY (decision_id, side, position)
) WITHOUT ROWID;
CREATE INDEX idx_proposal_member_alarm ON proposal_decision_member (alarm_id);
CREATE TABLE league_decision (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    at        REAL    NOT NULL,
    champion  TEXT    NOT NULL,  -- '<kind>:<sha12>'
    previous  TEXT,
    actor     TEXT    NOT NULL,  -- 'judge', or the admin who pinned
    reason    TEXT    NOT NULL CHECK (length(reason) > 0),
    pinned    INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1)),
    evidence  TEXT    NOT NULL DEFAULT '{}'
);
CREATE TRIGGER league_decision_no_update BEFORE UPDATE ON league_decision
BEGIN
    SELECT RAISE(ABORT, 'league_decision is append-only');
END;
CREATE TRIGGER league_decision_no_delete BEFORE DELETE ON league_decision
BEGIN
    SELECT RAISE(ABORT, 'league_decision is append-only');
END;
CREATE TABLE situation_event_moved (
    event_id INTEGER NOT NULL REFERENCES situation_event (id) ON DELETE CASCADE,
    alarm_id INTEGER NOT NULL,
    PRIMARY KEY (event_id, alarm_id)
) WITHOUT ROWID;
CREATE TABLE password_reset (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES user (id) ON DELETE CASCADE,
    token_hash TEXT    NOT NULL UNIQUE,
    created_at REAL    NOT NULL,
    expires_at REAL    NOT NULL,
    used_at    REAL,
    source_ip  TEXT
);
CREATE INDEX idx_password_reset_user ON password_reset (user_id);
INSERT INTO "scorer_config" (id, scorer_id, contract_version, w_t, w_a, w_e, tau_s, threshold, params_hash, created_by, created_at, note) VALUES (1, 'additive', '1.0', 0.3, 0.35, 0.35, 30.0, 0.5, 'c490028245242445e9f58e46e11ac534e5a3f8be03bd9643135557a2e3183418', NULL, 0.0, 'coded defaults, seeded by migration 0005');
INSERT INTO "scorer_active" (id, config_id, model_version_id, activated_by, activated_at) VALUES (1, 1, NULL, NULL, 0.0);
INSERT INTO "organization" (id, name, slug, created_at, is_default) VALUES (1, 'Default organization', 'default', 0.0, 1);
INSERT INTO "decider_setting" (id, mode, set_by, set_at, reason, pinned) VALUES (1, 'shipped', 'migration', 0.0, 'v0.26.0: the shipped model is the default decider; an appliance whose formula an admin had retuned keeps it, and one running a promoted model keeps that', NULL);
PRAGMA user_version = 30;
