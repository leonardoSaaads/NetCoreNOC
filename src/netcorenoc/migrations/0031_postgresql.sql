-- 0031 — the PostgreSQL baseline (ADR #452).
--
-- One script for the schema SQLite reached after migrations 0001-0030, redesigned for PostgreSQL
-- rather than transcribed. The numbering continues, so "schema 31" is one more step in the same
-- history; a v0.30.0 SQLite database moves here with `netcorenoc db import-sqlite`.
--
-- The runner applies it inside one transaction (PostgreSQL DDL is transactional) in the schema the
-- appliance is configured for, so every name below is unqualified on purpose.
--
-- TYPES, by what the data is:
--   * an instant is `timestamptz` — microseconds, UTC, indexable, readable in psql and in any BI tool;
--     the driver maps it to and from the epoch seconds the code computes with (`store/db.py`);
--   * a duration, a score, a weight is `double precision` — the 8-byte float SQLite's REAL was, so a
--     model's coefficients and the correlator's scores round-trip bit for bit;
--   * a yes/no is `boolean` — SQLite's `INTEGER CHECK (x IN (0, 1))`, said natively;
--   * an element's address is `inet` — validated, 7 bytes for IPv4, sorted numerically;
--   * a packet or a photo is `bytea`;
--   * an alarm's varbinds are `jsonb`, the one JSON a person searches into (GIN index below); every
--     JSON document whose exact bytes are hashed or signed (the audit chain, governance policies,
--     model parameters) stays `text`, because jsonb re-serialises and the hash would stop matching;
--   * a closed vocabulary is `text` with a CHECK — extendable by `ALTER TABLE`, unlike an enum type;
--   * every `text` column is `COLLATE "C"`: byte order, as SQLite compared, on every server whatever
--     its locale (a Supabase project is en_US.UTF-8, a test cluster C), so a list sorted by name or
--     OID is the same list everywhere — and comparisons are memcmp, and `LIKE 'prefix%'` can use a
--     plain b-tree index.
--
-- REDUNDANCY REMOVED:
--   * `device` was a copy of `ne` (same address, same clock, kept "in sync for one version" since
--     v0.3.0). It is gone, with `alarm.device_id`, `maintenance_ledger.device_id` and
--     `dataset_observation.device_id`: an alarm's device is its element, `ne_id`;
--   * `idx_alarm_entity` and `idx_alarm_ne` repeated the leading column of a unique index;
--   * twenty-four copies of the same append-only trigger body are one function, `forbid_change()`.
--
-- WRITE PATH: `alarm`, `edge`, `session` and `meta` are rewritten in place constantly (a repeat
-- trap, a learned weight, a session touch, the spool cursor). They get a fillfactor so the update
-- fits on its own page and stays a HOT update — no index is touched — and autovacuum settings that
-- keep the dead versions from accumulating.

-- ---------------------------------------------------------------------------------------------
-- Privileges
-- ---------------------------------------------------------------------------------------------

-- Nobody but the role that owns the schema reaches it. On a shared server — Supabase, whose API
-- roles `anon` and `authenticated` exist in every project — no other role may even resolve a name
-- in it; a grant to a reporting role is a deliberate, separate statement.
DO $$ BEGIN
    EXECUTE format('REVOKE ALL ON SCHEMA %I FROM PUBLIC', current_schema());
END $$;

-- ---------------------------------------------------------------------------------------------
-- Functions
-- ---------------------------------------------------------------------------------------------

-- Seconds since the epoch, for arithmetic the code states in seconds (bucket indexes, latencies).
-- IMMUTABLE is true for the epoch field, which does not depend on the session's time zone.
CREATE FUNCTION epoch(at timestamptz) RETURNS double precision
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
    RETURN extract(epoch FROM at)::double precision;

-- A duration in seconds as an interval: `starts_at - secs(patch_s)`.
CREATE FUNCTION secs(seconds double precision) RETURNS interval
    LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
    RETURN make_interval(secs => seconds);

-- The append-only and immutable tables. One body for all of them; the message names the table.
-- `prune_audit` is the one sanctioned deleter (DECISIONS v0.2 #3): it sets `netcorenoc.prune` for
-- its own transaction only, so no other statement, before or after, can delete a row.
CREATE FUNCTION forbid_change() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' AND TG_TABLE_NAME = 'audit_log'
       AND current_setting('netcorenoc.prune', true) = 'audit_log' THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation', DETAIL = TG_ARGV[0];
END
$$;

-- ---------------------------------------------------------------------------------------------
-- Elements, entities, classes
-- ---------------------------------------------------------------------------------------------

CREATE TABLE organization (
    id         integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    name       text COLLATE "C"        NOT NULL UNIQUE,
    slug       text COLLATE "C"        NOT NULL UNIQUE,
    created_at timestamptz NOT NULL,
    is_default boolean     NOT NULL DEFAULT false
);
-- At most one default: every element and window must belong somewhere, and it cannot be deleted.
CREATE UNIQUE INDEX ux_organization_default ON organization (is_default) WHERE is_default;

CREATE TABLE ne (
    id              integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    ip              inet        NOT NULL UNIQUE,
    vendor          text COLLATE "C",
    first_seen      timestamptz NOT NULL,
    last_seen       timestamptz NOT NULL,
    organization_id integer REFERENCES organization (id)
) WITH (fillfactor = 90);
CREATE INDEX idx_ne_organization ON ne (organization_id);

CREATE TABLE alarm_class (
    id         integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    oid        text COLLATE "C"        NOT NULL UNIQUE,
    first_seen timestamptz NOT NULL,
    last_seen  timestamptz NOT NULL
) WITH (fillfactor = 90);

CREATE TABLE entity (
    id         integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    ne_id      integer          NOT NULL REFERENCES ne (id),
    parent_id  integer REFERENCES entity (id),   -- NULL at level 0
    level      smallint         NOT NULL CHECK (level >= 0),  -- 0 = the NE itself
    key        text COLLATE "C"             NOT NULL,        -- discriminator value, or the address at level 0
    key_source text COLLATE "C"             NOT NULL,        -- varbind OID, or 'self' at level 0
    confidence double precision NOT NULL,        -- S_entity at promotion time (1.0 at level 0)
    first_seen timestamptz      NOT NULL,
    last_seen  timestamptz      NOT NULL,
    -- NULLS NOT DISTINCT (PostgreSQL 15): the level-0 entity has a NULL parent, and SQLite's UNIQUE
    -- let two of them exist for one NE. This one does not.
    UNIQUE NULLS NOT DISTINCT (ne_id, parent_id, key)
);
CREATE INDEX idx_entity_parent ON entity (parent_id) WHERE parent_id IS NOT NULL;

CREATE TABLE label (
    kind       text COLLATE "C"        NOT NULL CHECK (kind IN ('ne', 'class', 'severity')),
    target_id  integer     NOT NULL,
    qualifier  text COLLATE "C"        NOT NULL DEFAULT '',  -- '' = the whole target
    label      text COLLATE "C"        NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (kind, target_id, qualifier)
);

CREATE TABLE class_rule (
    id         integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    oid        text COLLATE "C"        NOT NULL,             -- dotted numeric arcs, validated by the API
    subtree    boolean     NOT NULL DEFAULT false,  -- this node and every OID beneath it
    name       text COLLATE "C",
    severity   text COLLATE "C",
    vendor     text COLLATE "C",
    source     text COLLATE "C"        NOT NULL CHECK (source IN ('declared', 'imported')),
    origin     text COLLATE "C",
    created_by text COLLATE "C",
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    UNIQUE (oid, subtree, source)
);
CREATE INDEX idx_class_rule_source ON class_rule (source);

CREATE TABLE state_clear (
    class_id    integer     NOT NULL,
    varbind_oid text COLLATE "C"        NOT NULL,
    clear_value text COLLATE "C"        NOT NULL,
    raise_value text COLLATE "C"        NOT NULL,
    learned_at  timestamptz NOT NULL,
    PRIMARY KEY (class_id, varbind_oid)
);

CREATE TABLE varbind_profile (
    ne_id       integer          NOT NULL,
    class_id    integer          NOT NULL,
    varbind_oid text COLLATE "C"             NOT NULL,
    n_obs       integer          NOT NULL,
    n_repeat    integer          NOT NULL,
    n_monotonic integer          NOT NULL,
    n_numeric   integer          NOT NULL,
    n_distinct  integer          NOT NULL,
    score       double precision NOT NULL,
    role        text COLLATE "C" CHECK (role IS NULL OR role IN ('entity', 'severity', 'state')),
    updated_at  timestamptz      NOT NULL,
    PRIMARY KEY (ne_id, class_id, varbind_oid)
) WITH (fillfactor = 85);

-- ---------------------------------------------------------------------------------------------
-- Maintenance windows (before `alarm`, which records the window that surfaced it)
-- ---------------------------------------------------------------------------------------------

CREATE TABLE maintenance_window (
    id                 integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    name               text COLLATE "C"             NOT NULL,
    description        text COLLATE "C"             NOT NULL DEFAULT '',
    organization_id    integer          NOT NULL REFERENCES organization (id),
    status             text COLLATE "C"             NOT NULL,
    tz                 text COLLATE "C"             NOT NULL,   -- the IANA zone the site's clock reads in
    starts_at          timestamptz      NOT NULL,
    ends_at            timestamptz      NOT NULL,
    all_day            boolean          NOT NULL DEFAULT false,
    patch_s            double precision NOT NULL DEFAULT 600 CHECK (patch_s >= 0),
    ledger_enabled     boolean          NOT NULL DEFAULT true,
    visibility         text COLLATE "C"             NOT NULL DEFAULT 'editors'
                       CHECK (visibility IN ('everyone', 'editors')),
    owner_ref          text COLLATE "C"             NOT NULL,
    owner_role         text COLLATE "C"             NOT NULL,
    created_by_agent   boolean          NOT NULL DEFAULT false,
    needs_confirmation boolean          NOT NULL DEFAULT false,
    confirmed_at       timestamptz,
    confirmed_by       text COLLATE "C",
    created_at         timestamptz      NOT NULL,
    updated_at         timestamptz      NOT NULL,
    cancelled_at       timestamptz,
    ended_at           timestamptz,
    idempotency_key    text COLLATE "C",
    CHECK (ends_at > starts_at)
);
CREATE INDEX idx_mw_ends ON maintenance_window (ends_at);
CREATE UNIQUE INDEX idx_mw_idempotency ON maintenance_window (idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX idx_mw_organization ON maintenance_window (organization_id, starts_at);
CREATE INDEX idx_mw_status_starts ON maintenance_window (status, starts_at);

CREATE TABLE maintenance_window_target (
    window_id integer NOT NULL REFERENCES maintenance_window (id) ON DELETE CASCADE,
    ne_id     integer NOT NULL REFERENCES ne (id),
    PRIMARY KEY (window_id, ne_id)
);
CREATE INDEX idx_mw_target_ne ON maintenance_window_target (ne_id);

CREATE TABLE maintenance_window_rule (
    id             integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    window_id      integer     NOT NULL REFERENCES maintenance_window (id) ON DELETE CASCADE,
    ne_id          integer     NOT NULL REFERENCES ne (id),
    kind           text COLLATE "C"        NOT NULL CHECK (kind IN ('severity', 'oid', 'slot')),
    severity_rank  integer,
    oid_root       text COLLATE "C",
    match_on       text COLLATE "C" CHECK (match_on IS NULL OR match_on IN ('trap', 'varbind')),
    slot_starts_at timestamptz,
    slot_ends_at   timestamptz,
    created_at     timestamptz NOT NULL
);
CREATE INDEX idx_mw_rule_window ON maintenance_window_rule (window_id, ne_id);

-- ---------------------------------------------------------------------------------------------
-- Alarms
-- ---------------------------------------------------------------------------------------------

CREATE TABLE alarm (
    id                      bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    ne_id                   integer     NOT NULL REFERENCES ne (id),
    class_id                integer     NOT NULL REFERENCES alarm_class (id),
    entity_id               integer REFERENCES entity (id),
    instance                text COLLATE "C"        NOT NULL DEFAULT '',
    status                  text COLLATE "C"        NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active', 'cleared')),
    is_flapping             boolean     NOT NULL DEFAULT false,
    count                   integer     NOT NULL DEFAULT 1 CHECK (count >= 1),
    first_seen              timestamptz NOT NULL,
    last_seen               timestamptz NOT NULL,
    cleared_at              timestamptz,
    varbinds                jsonb       NOT NULL DEFAULT '[]',
    community_tag           text COLLATE "C",
    severity                text COLLATE "C",
    severity_rank           integer,
    severity_source         text COLLATE "C",
    surfaced_from_window_id integer REFERENCES maintenance_window (id),
    surfaced_at             timestamptz,
    surfaced_ack_at         timestamptz,
    surfaced_ack_by         text COLLATE "C",
    -- The dedup key: an alarm is one (element, class, instance).
    UNIQUE (ne_id, class_id, instance)
) WITH (fillfactor = 80, autovacuum_vacuum_scale_factor = 0.05);
CREATE UNIQUE INDEX ux_alarm_entity ON alarm (entity_id, class_id, instance);
CREATE INDEX idx_alarm_class ON alarm (class_id);
CREATE INDEX idx_alarm_first_seen ON alarm (first_seen);
CREATE INDEX idx_alarm_last_seen ON alarm (last_seen);
CREATE INDEX idx_alarm_cleared_at ON alarm (cleared_at) WHERE cleared_at IS NOT NULL;
CREATE INDEX idx_alarm_active ON alarm (ne_id) WHERE status = 'active';
CREATE INDEX idx_alarm_severity_source ON alarm (severity_source)
    WHERE severity_source IS NOT NULL;
CREATE INDEX idx_alarm_surfaced ON alarm (surfaced_from_window_id)
    WHERE surfaced_from_window_id IS NOT NULL;
-- An operator looking for every alarm that carried a given varbind (`varbinds @> ...`).
CREATE INDEX idx_alarm_varbinds ON alarm USING gin (varbinds jsonb_path_ops);

CREATE TABLE maintenance_ledger (
    window_id   integer NOT NULL REFERENCES maintenance_window (id) ON DELETE CASCADE,
    ne_id       integer NOT NULL REFERENCES ne (id),
    class_id    integer NOT NULL REFERENCES alarm_class (id),
    instance    text COLLATE "C"    NOT NULL DEFAULT '',
    raised_at   timestamptz,
    cleared_at  timestamptz,
    surfaced_at timestamptz,   -- NULL: the end-of-window sweep has not turned it into an alarm
    PRIMARY KEY (window_id, ne_id, class_id, instance)
);

CREATE TABLE quarantine (
    id          bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    source      text COLLATE "C"        NOT NULL,
    raw         bytea       NOT NULL,
    reason      text COLLATE "C"        NOT NULL,
    received_at timestamptz NOT NULL,
    sha256      text COLLATE "C",
    length      integer CHECK (length IS NULL OR length >= 0),
    first8      text COLLATE "C",
    sanitized   boolean     NOT NULL DEFAULT false
);
CREATE INDEX idx_quarantine_received ON quarantine USING brin (received_at);

CREATE TABLE ingest_gap (
    id         bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    started_at timestamptz NOT NULL,
    ended_at   timestamptz NOT NULL,
    dropped    integer     NOT NULL CHECK (dropped >= 0),
    reason     text COLLATE "C"        NOT NULL
);
CREATE INDEX idx_ingest_gap_started ON ingest_gap (started_at);

-- ---------------------------------------------------------------------------------------------
-- Learned state
-- ---------------------------------------------------------------------------------------------

CREATE TABLE edge (
    kind       text COLLATE "C"             NOT NULL,
    a_id       integer          NOT NULL,
    b_id       integer          NOT NULL,
    weight     double precision NOT NULL DEFAULT 0,
    n          double precision NOT NULL DEFAULT 0,
    g          integer          NOT NULL DEFAULT 0,
    version    integer          NOT NULL DEFAULT 0,
    updated_at timestamptz      NOT NULL,
    PRIMARY KEY (kind, a_id, b_id)
) WITH (fillfactor = 80, autovacuum_vacuum_scale_factor = 0.05);

CREATE TABLE episode_pair (
    kind    text COLLATE "C"        NOT NULL CHECK (kind IN ('ne', 'cls', 'item')),
    k       text COLLATE "C"        NOT NULL,
    count   integer     NOT NULL CHECK (count >= 1),
    last_ts timestamptz NOT NULL,
    PRIMARY KEY (kind, k)
) WITH (fillfactor = 85);

-- Key/value settings and cursors. `spool.cursor` is rewritten on every committed batch: a low
-- fillfactor and an aggressive autovacuum keep that one hot row from bloating the table.
CREATE TABLE meta (
    key   text COLLATE "C" PRIMARY KEY,
    value text COLLATE "C" NOT NULL
) WITH (fillfactor = 50, autovacuum_vacuum_threshold = 200, autovacuum_vacuum_scale_factor = 0);

-- ---------------------------------------------------------------------------------------------
-- Scoring and models
-- ---------------------------------------------------------------------------------------------

CREATE TABLE scorer_config (
    id               integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    scorer_id        text COLLATE "C"             NOT NULL,
    contract_version text COLLATE "C"             NOT NULL,
    w_t              double precision NOT NULL,
    w_a              double precision NOT NULL,
    w_e              double precision NOT NULL,
    tau_s            double precision NOT NULL,
    threshold        double precision NOT NULL,
    params_hash      text COLLATE "C"             NOT NULL,
    created_by       text COLLATE "C",
    created_at       timestamptz      NOT NULL,
    note             text COLLATE "C"             NOT NULL DEFAULT ''
);
CREATE INDEX idx_scorer_config_created ON scorer_config (created_at);

CREATE TABLE capture_run (
    id                      bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    started_at              timestamptz      NOT NULL,
    netcorenoc_version      text COLLATE "C"             NOT NULL,
    scorer_config_id        integer,         -- NULL: no configuration in effect (coded defaults)
    scorer_params_hash      text COLLATE "C",
    window_s                double precision NOT NULL,
    max_candidates          integer          NOT NULL,
    max_links_per_alarm     integer          NOT NULL,
    link_threshold          double precision NOT NULL,
    retention_sink_days     double precision NOT NULL,
    retention_sink_rows     integer          NOT NULL,
    retention_training_days double precision NOT NULL,
    retention_audit_days    double precision NOT NULL,
    a_epoch                 integer          NOT NULL,
    e_epoch                 integer          NOT NULL
);

CREATE TABLE challenger_run (
    id                 bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    started_at         timestamptz      NOT NULL,
    netcorenoc_version text COLLATE "C"             NOT NULL,
    scorer_id          text COLLATE "C"             NOT NULL,
    contract_version   text COLLATE "C"             NOT NULL,
    params_fingerprint text COLLATE "C",
    derivation_policy  text COLLATE "C"             NOT NULL CHECK (derivation_policy IN ('A', 'B', 'none')),
    train_oldest_at    timestamptz,
    train_newest_at    timestamptz,
    train_rows         integer          NOT NULL DEFAULT 0,
    train_bags         integer          NOT NULL DEFAULT 0,
    train_split_bags   integer          NOT NULL DEFAULT 0,
    train_mixed_bags   integer          NOT NULL DEFAULT 0,
    train_incidents    integer          NOT NULL DEFAULT 0,
    train_operators    integer          NOT NULL DEFAULT 0,
    thresholds         text COLLATE "C"             NOT NULL,   -- canonical JSON
    sufficient         boolean          NOT NULL,
    insufficiency      text COLLATE "C",
    coefficients       text COLLATE "C",
    iterations         integer,
    learning_rate      double precision,
    fit_seconds        double precision,
    sample_rate        double precision NOT NULL DEFAULT 0.0,
    capture_run_id     bigint REFERENCES capture_run (id),
    loss_trace         text COLLATE "C",
    loss_trace_stride  integer
);

CREATE TABLE model_version (
    id                integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    kind              text COLLATE "C"        NOT NULL,
    contract_version  text COLLATE "C"        NOT NULL,
    params_document   text COLLATE "C"        NOT NULL,   -- canonical JSON; `params_hash` is over these bytes
    params_hash       text COLLATE "C"        NOT NULL,
    challenger_run_id bigint REFERENCES challenger_run (id),
    created_by        text COLLATE "C",
    created_at        timestamptz NOT NULL,
    note              text COLLATE "C"        NOT NULL DEFAULT ''
);
CREATE INDEX idx_model_version_created ON model_version (created_at);

CREATE TABLE scorer_active (
    id               integer PRIMARY KEY CHECK (id = 1),
    config_id        integer REFERENCES scorer_config (id),
    model_version_id integer REFERENCES model_version (id),
    activated_by     text COLLATE "C",
    activated_at     timestamptz NOT NULL,
    -- EXACTLY ONE pointer is set.
    CHECK (num_nonnulls(config_id, model_version_id) = 1)
);

CREATE TABLE evaluation_fold (
    run_id      text COLLATE "C"        NOT NULL,
    incident_id bigint      NOT NULL,
    repeat      integer     NOT NULL CHECK (repeat >= 0),
    fold        integer     NOT NULL CHECK (fold >= 0),
    created_at  timestamptz NOT NULL,
    PRIMARY KEY (run_id, incident_id, repeat)
);

CREATE TABLE promotion (
    id                bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    model_version_id  integer     NOT NULL REFERENCES model_version (id),
    verdict           text COLLATE "C"        NOT NULL
                      CHECK (verdict IN ('BETTER', 'NOT_BETTER', 'INSUFFICIENT_EVIDENCE')),
    triggers          text COLLATE "C"        NOT NULL DEFAULT '[]',
    metrics           text COLLATE "C"        NOT NULL DEFAULT '{}',
    evaluation_run_id text COLLATE "C",
    plan_sha256       text COLLATE "C"        NOT NULL,
    query_count       integer     NOT NULL CHECK (query_count >= 0),
    approved_by       text COLLATE "C"        NOT NULL,
    decided_at        timestamptz NOT NULL,
    outcome           text COLLATE "C"        NOT NULL CHECK (outcome IN ('applied', 'refused')),
    refusal_reason    text COLLATE "C",
    unavailable       text COLLATE "C"        NOT NULL DEFAULT '[]',
    CHECK (
        (outcome = 'refused' AND refusal_reason IS NOT NULL AND length(refusal_reason) > 0)
     OR (outcome = 'applied' AND refusal_reason IS NULL)
    ),
    CHECK (outcome = 'refused' OR verdict = 'BETTER')
);
CREATE INDEX idx_promotion_decided ON promotion (decided_at);

CREATE TABLE search_run (
    id               bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    started_at       timestamptz NOT NULL,
    started_by       text COLLATE "C"        NOT NULL,
    seed             bigint      NOT NULL,
    budget           text COLLATE "C"        NOT NULL,
    status           text COLLATE "C"        NOT NULL
                     CHECK (status IN ('running', 'done', 'stopped', 'failed', 'refused')),
    finished_at      timestamptz,
    note             text COLLATE "C"        NOT NULL DEFAULT '',
    model_version_id integer REFERENCES model_version (id),
    rows             integer,
    judgement        text COLLATE "C"
);

CREATE TABLE search_trial (
    id         bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    run_id     bigint           NOT NULL REFERENCES search_run (id),
    trial      integer          NOT NULL,
    rung       integer          NOT NULL,
    params     text COLLATE "C"             NOT NULL,
    rounds     integer          NOT NULL,
    best_round integer          NOT NULL,
    train_loss double precision,
    valid_loss double precision,
    seconds    double precision NOT NULL,
    status     text COLLATE "C"             NOT NULL CHECK (status IN ('done', 'stopped', 'failed')),
    trace      text COLLATE "C"             NOT NULL DEFAULT '[]',
    UNIQUE (run_id, trial, rung)
);

CREATE TABLE decider_setting (
    id     integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    mode   text COLLATE "C"        NOT NULL CHECK (mode IN ('shipped', 'site', 'additive')),
    set_by text COLLATE "C"        NOT NULL,
    set_at timestamptz NOT NULL,
    reason text COLLATE "C"        NOT NULL DEFAULT '',
    pinned text COLLATE "C"
);

CREATE TABLE autonomy_setting (
    id               integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    grouping         boolean          NOT NULL DEFAULT false,
    naming           boolean          NOT NULL DEFAULT false,
    closing          boolean          NOT NULL DEFAULT false,
    severity         boolean          NOT NULL DEFAULT false,
    agreement_floor  double precision NOT NULL DEFAULT 0.8
                     CHECK (agreement_floor > 0 AND agreement_floor <= 1),
    "window"         integer          NOT NULL DEFAULT 30 CHECK ("window" BETWEEN 5 AND 1000),
    min_judged       integer          NOT NULL DEFAULT 8 CHECK (min_judged >= 3),
    confidence_floor double precision NOT NULL DEFAULT 0.9
                     CHECK (confidence_floor >= 0.5 AND confidence_floor < 1),
    set_by           text COLLATE "C"             NOT NULL,
    set_at           timestamptz      NOT NULL,
    reason           text COLLATE "C"             NOT NULL DEFAULT ''
);

CREATE TABLE league_decision (
    id       integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    at       timestamptz NOT NULL,
    champion text COLLATE "C"        NOT NULL,   -- '<kind>:<sha12>'
    previous text COLLATE "C",
    actor    text COLLATE "C"        NOT NULL,   -- 'judge', or the admin who pinned
    reason   text COLLATE "C"        NOT NULL CHECK (length(reason) > 0),
    pinned   boolean     NOT NULL DEFAULT false,
    evidence text COLLATE "C"        NOT NULL DEFAULT '{}'
);

-- ---------------------------------------------------------------------------------------------
-- Situations
-- ---------------------------------------------------------------------------------------------

CREATE TABLE situation (
    id                  bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    status              text COLLATE "C"        NOT NULL DEFAULT 'open'
                        CHECK (status IN ('new', 'pending', 'open', 'resolved')),
    created_at          timestamptz NOT NULL,
    updated_at          timestamptz NOT NULL,
    closed_at           timestamptz,
    root_alarm_id       bigint,
    scorer_config_id    integer REFERENCES scorer_config (id),
    merged_into         bigint,
    resolution          text COLLATE "C" CHECK (resolution IS NULL OR resolution IN (
        'operator', 'self_cleared', 'idle', 'merged', 'manual_clear', 'unattributed')),
    derived_name        text COLLATE "C",
    operator_name       text COLLATE "C",
    decider             text COLLATE "C",
    severity            text COLLATE "C" CHECK (severity IS NULL OR severity IN (
        'critical', 'major', 'minor', 'warning', 'indeterminate')),
    severity_by         text COLLATE "C",
    model_name          text COLLATE "C",
    proposed_into       bigint,
    proposal_confidence double precision
                        CHECK (proposal_confidence IS NULL OR proposal_confidence BETWEEN 0 AND 1)
) WITH (fillfactor = 85);
CREATE INDEX idx_situation_status ON situation (status);
CREATE INDEX idx_situation_proposal ON situation (proposed_into) WHERE proposed_into IS NOT NULL;

CREATE TABLE situation_alarm (
    situation_id bigint NOT NULL REFERENCES situation (id),
    alarm_id     bigint NOT NULL REFERENCES alarm (id),
    PRIMARY KEY (situation_id, alarm_id)
);
CREATE INDEX idx_situation_alarm_alarm ON situation_alarm (alarm_id);

CREATE TABLE link (
    id           bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    situation_id bigint           NOT NULL,
    alarm_a      bigint           NOT NULL,
    alarm_b      bigint           NOT NULL,
    score        double precision NOT NULL,
    term_t       double precision NOT NULL,
    term_a       double precision NOT NULL,
    term_e       double precision NOT NULL,
    created_at   timestamptz      NOT NULL,
    terms        text COLLATE "C"
);
CREATE INDEX idx_link_situation ON link (situation_id);

CREATE TABLE situation_event (
    id                     bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    situation_id           bigint      NOT NULL,
    kind                   text COLLATE "C"        NOT NULL CHECK (kind IN (
        'verdict', 'move', 'merge', 'operator_split', 'manual_clear', 'self_clear',
        'idle_close', 'operator_close', 'rename')),
    actor                  text COLLATE "C",
    role                   text COLLATE "C",
    at                     timestamptz NOT NULL,
    confidence             double precision
                           CHECK (confidence IS NULL OR confidence BETWEEN 0.0 AND 1.0),
    acquisition_channel    text COLLATE "C",
    feedback_id            bigint,
    peer_situation_id      bigint,
    alarm_id               bigint,
    member_count           integer     NOT NULL,
    member_digest          text COLLATE "C"        NOT NULL,
    peer_member_count      integer,
    peer_member_digest     text COLLATE "C",
    produces_training_rows boolean     NOT NULL DEFAULT false,
    bag_weakest_margin     double precision,
    bag_has_bridge         boolean,
    bag_bridge_min_side    integer,
    bag_link_count         integer
);
CREATE INDEX idx_situation_event_channel ON situation_event (acquisition_channel, at);
CREATE UNIQUE INDEX idx_situation_event_feedback ON situation_event (feedback_id)
    WHERE feedback_id IS NOT NULL;
CREATE INDEX idx_situation_event_situation ON situation_event (situation_id, at);

CREATE TABLE situation_event_member (
    event_id bigint  NOT NULL REFERENCES situation_event (id) ON DELETE CASCADE,
    source   text COLLATE "C"    NOT NULL CHECK (source IN ('server', 'peer')),
    position integer NOT NULL,
    alarm_id bigint  NOT NULL,
    PRIMARY KEY (event_id, source, position)
);

CREATE TABLE situation_event_moved (
    event_id bigint NOT NULL REFERENCES situation_event (id) ON DELETE CASCADE,
    alarm_id bigint NOT NULL,
    PRIMARY KEY (event_id, alarm_id)
);

CREATE TABLE autonomy_decision (
    id           bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    situation_id bigint           NOT NULL REFERENCES situation (id),
    grade        text COLLATE "C"             NOT NULL
                 CHECK (grade IN ('grouping', 'naming', 'closing', 'severity')),
    action       text COLLATE "C"             NOT NULL,
    decider      text COLLATE "C"             NOT NULL,
    confidence   double precision NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    explanation  text COLLATE "C"             NOT NULL CHECK (length(explanation) > 2),
    at           timestamptz      NOT NULL,
    verdict      text COLLATE "C" CHECK (verdict IS NULL OR verdict IN ('agreed', 'disagreed')),
    verdict_at   timestamptz,
    verdict_by   text COLLATE "C",
    verdict_why  text COLLATE "C"
);
CREATE INDEX idx_autonomy_decision_pending ON autonomy_decision (verdict, at);
CREATE INDEX idx_autonomy_decision_situation ON autonomy_decision (situation_id);

CREATE TABLE proposal_decision (
    id                     bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    pending_situation_id   bigint      NOT NULL,
    target_situation_id    bigint      NOT NULL,
    decision               text COLLATE "C"        NOT NULL CHECK (decision IN ('accept', 'reject')),
    actor                  text COLLATE "C"        NOT NULL,
    role                   text COLLATE "C",
    at                     timestamptz NOT NULL,
    confidence             double precision
                           CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
    proposal_confidence    double precision,
    decider                text COLLATE "C",
    produces_training_rows boolean     NOT NULL DEFAULT true
);
CREATE INDEX idx_proposal_decision_at ON proposal_decision (at);

CREATE TABLE proposal_decision_member (
    decision_id bigint  NOT NULL REFERENCES proposal_decision (id) ON DELETE CASCADE,
    side        text COLLATE "C"    NOT NULL CHECK (side IN ('pending', 'target')),
    position    integer NOT NULL,
    alarm_id    bigint  NOT NULL,
    PRIMARY KEY (decision_id, side, position)
);
CREATE INDEX idx_proposal_member_alarm ON proposal_decision_member (alarm_id);

-- ---------------------------------------------------------------------------------------------
-- Feedback and the dataset
-- ---------------------------------------------------------------------------------------------

CREATE TABLE feedback (
    id                               bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    situation_id                     bigint      NOT NULL REFERENCES situation (id),
    verdict                          text COLLATE "C"        NOT NULL CHECK (verdict IN ('confirm', 'split')),
    created_at                       timestamptz NOT NULL,
    principal_ref                    text COLLATE "C",
    role                             text COLLATE "C",
    member_digest                    text COLLATE "C",
    member_count                     integer,
    scope_policy_id                  integer,
    scope_restricted                 boolean,
    scope_redacted_members           integer,
    acquisition_channel              text COLLATE "C",
    capture_provenance               text COLLATE "C",
    situation_opened_at              timestamptz,
    situation_id_at_label            bigint,
    coverage                         text COLLATE "C",
    coverage_found                   integer,
    coverage_expected                integer,
    client_member_digest             text COLLATE "C",
    client_member_count              integer,
    client_updated_at                timestamptz,
    client_truncated                 boolean,
    excluded_count                   integer,
    excluded_truncated               boolean,
    remainder_together               boolean,
    excluded_reconciled              integer
        CHECK (excluded_reconciled IS NULL OR (excluded_reconciled >= 0
               AND member_count IS NOT NULL AND excluded_reconciled <= member_count)),
    excluded_reconciled_source       text COLLATE "C",
    excluded_reconciled_out_of_scope integer
        CHECK (excluded_reconciled_out_of_scope IS NULL OR (excluded_reconciled_out_of_scope >= 0
               AND excluded_reconciled IS NOT NULL
               AND excluded_reconciled_out_of_scope <= excluded_reconciled)),
    bag_key                          text COLLATE "C"        NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX idx_feedback_situation_verdict_bag ON feedback (situation_id, verdict, bag_key);

CREATE TABLE feedback_member (
    feedback_id bigint  NOT NULL REFERENCES feedback (id) ON DELETE CASCADE,
    source      text COLLATE "C"    NOT NULL CHECK (source IN ('server', 'client')),
    position    integer NOT NULL,
    alarm_id    bigint  NOT NULL,
    PRIMARY KEY (feedback_id, source, position)
);

CREATE TABLE feedback_exclusion (
    feedback_id bigint  NOT NULL REFERENCES feedback (id) ON DELETE CASCADE,
    position    integer NOT NULL,
    alarm_id    bigint  NOT NULL,
    PRIMARY KEY (feedback_id, position)
);

CREATE TABLE dataset_observation (
    id             bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    capture_run_id bigint      NOT NULL REFERENCES capture_run (id),
    alarm_id       bigint      NOT NULL,
    ne_id          integer     NOT NULL,
    entity_id      integer,
    class_id       integer     NOT NULL,
    observed_at    timestamptz NOT NULL,
    alarm_count    integer     NOT NULL,
    severity       text COLLATE "C",
    severity_rank  integer,
    instance       text COLLATE "C",
    trap_oid       text COLLATE "C"        NOT NULL,
    source_address text COLLATE "C",
    varbinds       jsonb,
    lifecycle      text COLLATE "C"        NOT NULL DEFAULT 'sink' CHECK (lifecycle IN ('sink', 'dataset')),
    promoted_at    timestamptz
);
CREATE INDEX idx_observation_alarm ON dataset_observation (alarm_id);
CREATE INDEX idx_observation_sink ON dataset_observation (lifecycle, observed_at);

CREATE TABLE dataset_pair (
    id               bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    capture_run_id   bigint           NOT NULL REFERENCES capture_run (id),
    alarm_a          bigint           NOT NULL,
    alarm_b          bigint           NOT NULL,
    observation_a    bigint REFERENCES dataset_observation (id),
    observation_b    bigint REFERENCES dataset_observation (id),
    situation_id     bigint,
    delta_t_s        double precision NOT NULL,
    class_affinity   double precision NOT NULL,
    entity_affinity  double precision NOT NULL,
    a_epoch          integer          NOT NULL,
    e_epoch          integer          NOT NULL,
    score            double precision NOT NULL,
    incumbent_linked boolean          NOT NULL,
    storm            boolean          NOT NULL,
    truncated        boolean          NOT NULL DEFAULT false,
    evaluated_at     timestamptz      NOT NULL,
    lifecycle        text COLLATE "C"             NOT NULL DEFAULT 'sink'
                     CHECK (lifecycle IN ('sink', 'dataset')),
    promoted_at      timestamptz,
    features         text COLLATE "C"
);
CREATE INDEX idx_pair_alarm_ab ON dataset_pair (alarm_a, alarm_b);
CREATE INDEX idx_pair_alarm_ba ON dataset_pair (alarm_b, alarm_a);
CREATE INDEX idx_pair_sink ON dataset_pair (lifecycle, evaluated_at);
CREATE INDEX idx_pair_situation ON dataset_pair (situation_id);
CREATE INDEX idx_pair_observation_a ON dataset_pair (observation_a) WHERE observation_a IS NOT NULL;
CREATE INDEX idx_pair_observation_b ON dataset_pair (observation_b) WHERE observation_b IS NOT NULL;

CREATE TABLE shadow_opinion (
    id                bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    challenger_run_id bigint           NOT NULL REFERENCES challenger_run (id),
    alarm_a           bigint           NOT NULL,
    alarm_b           bigint           NOT NULL,
    situation_id      bigint,
    delta_t_s         double precision NOT NULL,
    class_affinity    double precision NOT NULL,
    entity_affinity   double precision NOT NULL,
    same_oid_root     boolean          NOT NULL,
    score             double precision NOT NULL,
    linked            boolean          NOT NULL,
    incumbent_linked  boolean          NOT NULL,
    evaluated_at      timestamptz      NOT NULL
);
CREATE INDEX idx_shadow_age ON shadow_opinion USING brin (evaluated_at);
CREATE INDEX idx_shadow_pair ON shadow_opinion (alarm_a, alarm_b, evaluated_at);
CREATE INDEX idx_shadow_run ON shadow_opinion (challenger_run_id);

CREATE TABLE holdout_seal (
    id               integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    singleton        boolean     NOT NULL DEFAULT true UNIQUE CHECK (singleton),
    release          text COLLATE "C"        NOT NULL,
    plan_sha256      text COLLATE "C"        NOT NULL,
    created_at       timestamptz NOT NULL,
    digest           text COLLATE "C"        NOT NULL,
    incident_count   integer     NOT NULL CHECK (incident_count >= 0),
    corpus_incidents integer     NOT NULL CHECK (corpus_incidents >= 0)
);

CREATE TABLE holdout_seal_member (
    seal_id     integer NOT NULL REFERENCES holdout_seal (id),
    position    integer NOT NULL,
    incident_id bigint  NOT NULL,
    PRIMARY KEY (seal_id, position)
);

CREATE TABLE holdout_access (
    id           bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    at           timestamptz NOT NULL,
    release      text COLLATE "C"        NOT NULL,
    plan_sha256  text COLLATE "C",
    purpose      text COLLATE "C"        NOT NULL,
    granted      boolean     NOT NULL,
    outcome      text COLLATE "C"        NOT NULL,
    prev_hash    text COLLATE "C",
    entry_hash   text COLLATE "C",
    chain_source text COLLATE "C"
);

-- ---------------------------------------------------------------------------------------------
-- People, sessions, tokens, governance, audit
-- ---------------------------------------------------------------------------------------------

-- `user` is a reserved word in PostgreSQL; the table is the appliance's accounts.
CREATE TABLE app_user (
    id                   integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    username             text COLLATE "C"        NOT NULL UNIQUE,
    password_hash        text COLLATE "C"        NOT NULL,
    role                 text COLLATE "C"        NOT NULL CHECK (role IN ('admin', 'editor', 'viewer')),
    must_change_password boolean     NOT NULL DEFAULT false,
    disabled             boolean     NOT NULL DEFAULT false,
    created_at           timestamptz NOT NULL,
    updated_at           timestamptz NOT NULL,
    display_name         text COLLATE "C",
    email                text COLLATE "C"
);

CREATE TABLE user_avatar (
    user_id    integer     PRIMARY KEY REFERENCES app_user (id) ON DELETE CASCADE,
    mime       text COLLATE "C"        NOT NULL CHECK (mime IN ('image/png', 'image/webp', 'image/jpeg')),
    image      bytea       NOT NULL,
    sha256     text COLLATE "C"        NOT NULL,
    width      integer     NOT NULL CHECK (width > 0),
    height     integer     NOT NULL CHECK (height > 0),
    updated_at timestamptz NOT NULL
);

CREATE TABLE session (
    id              bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    session_hash    text COLLATE "C"        NOT NULL UNIQUE,   -- sha256 hex of the token
    user_id         integer     NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
    created_at      timestamptz NOT NULL,
    last_seen_at    timestamptz NOT NULL,
    idle_expiry     timestamptz NOT NULL,
    absolute_expiry timestamptz NOT NULL,
    source_ip       text COLLATE "C"
) WITH (fillfactor = 70, autovacuum_vacuum_scale_factor = 0.05);
CREATE INDEX idx_session_absolute ON session (absolute_expiry);
CREATE INDEX idx_session_user ON session (user_id);

CREATE TABLE password_reset (
    id         bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    user_id    integer     NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
    token_hash text COLLATE "C"        NOT NULL UNIQUE,
    created_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL,
    used_at    timestamptz,
    source_ip  text COLLATE "C"
);
CREATE INDEX idx_password_reset_user ON password_reset (user_id);

CREATE TABLE notice_snooze (
    user_id    integer     NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
    digest     text COLLATE "C"        NOT NULL,
    text       text COLLATE "C"        NOT NULL,
    security   boolean     NOT NULL DEFAULT false,
    mode       text COLLATE "C"        NOT NULL CHECK (mode IN ('24h', '7d', 'change')),
    until      timestamptz,   -- NULL only for 'change', which returns when the text does
    created_at timestamptz NOT NULL,
    PRIMARY KEY (user_id, digest)
);

CREATE TABLE api_token (
    id           integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    token_hash   text COLLATE "C"        NOT NULL UNIQUE,   -- sha256 hex of the token value
    name         text COLLATE "C"        NOT NULL UNIQUE,
    role         text COLLATE "C"        NOT NULL CHECK (role IN ('admin', 'editor', 'viewer')),
    created_at   timestamptz NOT NULL,
    created_by   text COLLATE "C",
    last_used_at timestamptz,
    revoked      boolean     NOT NULL DEFAULT false,
    revoked_at   timestamptz,
    purpose      text COLLATE "C"
) WITH (fillfactor = 80);

CREATE TABLE governance_policy (
    id         integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    kind       text COLLATE "C"        NOT NULL CHECK (kind IN ('rbac', 'scope')),
    document   text COLLATE "C"        NOT NULL,   -- canonical JSON; `doc_hash` is over these bytes
    doc_hash   text COLLATE "C"        NOT NULL,
    created_by text COLLATE "C",
    created_at timestamptz NOT NULL,
    note       text COLLATE "C"        NOT NULL DEFAULT ''
);
CREATE INDEX idx_governance_policy_kind ON governance_policy (kind, id);

CREATE TABLE governance_active (
    kind         text COLLATE "C" PRIMARY KEY CHECK (kind IN ('rbac', 'scope')),
    policy_id    integer     NOT NULL REFERENCES governance_policy (id),
    activated_by text COLLATE "C",
    activated_at timestamptz NOT NULL
);

-- The hash-chained audit log. `id` is assigned by the application because it is part of what each
-- row's hash covers; `details` is the canonical JSON the hash was computed over, byte for byte;
-- and **`ts` is the one instant kept as `double precision`**: the hash covers the float's exact
-- digits, and a `timestamptz` keeps microseconds, so `time.time()`'s last digits would be gone and
-- every row written since v0.2.0 would fail `audit verify`. `to_timestamp(ts)` reads it as a time.
CREATE TABLE audit_log (
    id          bigint PRIMARY KEY,
    ts          double precision NOT NULL,   -- epoch seconds, exactly as hashed
    actor       text COLLATE "C"        NOT NULL,
    role        text COLLATE "C",
    source_ip   text COLLATE "C",
    action      text COLLATE "C"        NOT NULL,
    object_type text COLLATE "C",
    object_id   text COLLATE "C",
    outcome     text COLLATE "C"        NOT NULL CHECK (outcome IN ('ok', 'denied', 'error')),
    details     text COLLATE "C"        NOT NULL DEFAULT '{}',
    prev_hash   text COLLATE "C"        NOT NULL,
    entry_hash  text COLLATE "C"        NOT NULL
);
CREATE INDEX idx_audit_action ON audit_log (action);
-- Append-only and pruned oldest-first: a block-range index is a few pages for millions of rows.
CREATE INDEX idx_audit_ts ON audit_log USING brin (ts);

-- ---------------------------------------------------------------------------------------------
-- Host and pipeline samples, one row per 30 s interval
-- ---------------------------------------------------------------------------------------------

CREATE TABLE host_sample (
    at               timestamptz PRIMARY KEY,
    cpu_pct          double precision,
    mem_pct          double precision,
    disk_pct         double precision,
    db_mb            double precision,
    queue_depth      integer,
    traps_per_s      double precision,
    latency_ms       double precision,
    classified       integer,
    classified_per_s double precision,
    latency_max_ms   double precision,
    over_target      integer,
    over_limit       integer,
    spool_mb         double precision
);

-- ---------------------------------------------------------------------------------------------
-- Append-only and immutable tables
-- ---------------------------------------------------------------------------------------------

CREATE TRIGGER audit_log_append_only BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION forbid_change('the audit log is a hash chain');
CREATE TRIGGER autonomy_setting_append_only BEFORE UPDATE OR DELETE ON autonomy_setting
    FOR EACH ROW EXECUTE FUNCTION forbid_change('a setting is changed by appending a new one');
CREATE TRIGGER decider_setting_append_only BEFORE UPDATE OR DELETE ON decider_setting
    FOR EACH ROW EXECUTE FUNCTION forbid_change('a setting is changed by appending a new one');
CREATE TRIGGER evaluation_fold_append_only BEFORE UPDATE OR DELETE ON evaluation_fold
    FOR EACH ROW EXECUTE FUNCTION forbid_change('a cited fold assignment that can move is not a citation');
CREATE TRIGGER governance_policy_append_only BEFORE UPDATE OR DELETE ON governance_policy
    FOR EACH ROW EXECUTE FUNCTION forbid_change('a policy is changed by appending a new one');
CREATE TRIGGER holdout_access_append_only BEFORE UPDATE OR DELETE ON holdout_access
    FOR EACH ROW EXECUTE FUNCTION forbid_change('every holdout access is recorded');
CREATE TRIGGER holdout_seal_append_only BEFORE UPDATE OR DELETE ON holdout_seal
    FOR EACH ROW EXECUTE FUNCTION forbid_change('a seal that can be edited is not a seal');
CREATE TRIGGER holdout_seal_member_append_only BEFORE UPDATE OR DELETE ON holdout_seal_member
    FOR EACH ROW EXECUTE FUNCTION forbid_change('a seal that can be edited is not a seal');
CREATE TRIGGER league_decision_append_only BEFORE UPDATE OR DELETE ON league_decision
    FOR EACH ROW EXECUTE FUNCTION forbid_change('the league history is a record');
CREATE TRIGGER model_version_append_only BEFORE UPDATE OR DELETE ON model_version
    FOR EACH ROW EXECUTE FUNCTION forbid_change('a registered model is immutable');
CREATE TRIGGER promotion_append_only BEFORE UPDATE OR DELETE ON promotion
    FOR EACH ROW EXECUTE FUNCTION forbid_change('a decision that can be edited is not a record');
CREATE TRIGGER scorer_config_append_only BEFORE UPDATE OR DELETE ON scorer_config
    FOR EACH ROW EXECUTE FUNCTION forbid_change('a configuration is changed by appending a new one');

-- ---------------------------------------------------------------------------------------------
-- The rows a new database starts with (what 0005, 0020 and 0026 seeded)
-- ---------------------------------------------------------------------------------------------

-- The coded defaults. `params_hash` is the sha256 of the canonical JSON
-- `scoring.AdditiveScorer.params_fingerprint()` hashes; a test pins the two together
-- (tests/correlation/test_scoring.py::test_seed_params_hash_matches_the_coded_default).
INSERT INTO scorer_config (scorer_id, contract_version, w_t, w_a, w_e, tau_s, threshold,
                           params_hash, created_by, created_at, note)
VALUES ('additive', '1.0', 0.3, 0.35, 0.35, 30.0, 0.5,
        'c490028245242445e9f58e46e11ac534e5a3f8be03bd9643135557a2e3183418',
        NULL, 'epoch', 'coded defaults, seeded by migration 0005');

INSERT INTO scorer_active (id, config_id, activated_by, activated_at)
VALUES (1, 1, NULL, 'epoch');

INSERT INTO organization (name, slug, created_at, is_default)
VALUES ('Default organization', 'default', 'epoch', true);

INSERT INTO decider_setting (mode, set_by, set_at, reason)
VALUES ('shipped', 'migration', 'epoch',
        'v0.26.0: the shipped model is the default decider; an appliance whose formula an admin '
        || 'had retuned keeps it, and one running a promoted model keeps that');
