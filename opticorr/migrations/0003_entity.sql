-- OptiCorr schema v3 — the learned entity model (user_version 2 -> 3).
--
-- Forward-only, additive; applies cleanly onto a populated v0.2.0 database (schema v2).
-- Adds the reporting-element / entity model, the persisted varbind profiler, and a durable
-- record of dropped traps, then augments `alarm` with the entity reference and learned
-- severity. The trap path gains no lock and no I/O from any of this (prime directive 2):
-- profiling runs in the engine, under the batch lock it already holds, and is flushed here
-- by maintenance().
--
-- Additive by design (DECISIONS v0.3 #22): the v0.2.0 UNIQUE (device_id, class_id, instance)
-- constraint is kept for one version alongside the new UNIQUE (entity_id, class_id, instance)
-- index, so an unmodified v0.2.0 store keeps working the moment this migration lands and the
-- entity wiring (Phase 3 S3) is switched on separately. The two keys stay consistent because
-- an alarm's `instance` always carries its entity's discriminator value (see docs/DESIGN.md).

-- The reporting element: the trap source IP. One row per existing v0.2.0 device, ids preserved
-- so alarm.ne_id can be backfilled from alarm.device_id directly.
CREATE TABLE ne (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ip         TEXT NOT NULL UNIQUE,
    vendor     TEXT,
    first_seen REAL NOT NULL,
    last_seen  REAL NOT NULL
);

-- The alarmed thing. level 0 is the NE itself (key = its IP, key_source = 'self',
-- confidence = 1.0). Deeper levels are promoted entities and their containment parents.
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

-- Durable record of dropped traps (§5.6): "I know I lost events between t1 and t2" is
-- first-class NOC information, not an in-memory counter that vanishes on restart.
CREATE TABLE ingest_gap (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   REAL NOT NULL,
    ended_at     REAL NOT NULL,
    dropped      INTEGER NOT NULL,
    reason       TEXT NOT NULL                    -- queue_full | window_overflow
);
CREATE INDEX idx_ingest_gap_started ON ingest_gap (started_at);

-- Persisted profiler state, flushed and pruned by maintenance(). Bounded in memory by the
-- profiler's caps (MAX_TRACKED_VARBINDS_PER_CLASS, MAX_TRACKED_VALUES); this table holds the
-- durable summary counters, one row per (NE, class, varbind OID).
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

-- Backfill: each device becomes one NE (ids preserved) plus one level-0 entity.
INSERT INTO ne (id, ip, vendor, first_seen, last_seen)
    SELECT id, ip, vendor, first_seen, last_seen FROM device;

INSERT INTO entity (ne_id, parent_id, level, key, key_source, confidence, first_seen, last_seen)
    SELECT id, NULL, 0, ip, 'self', 1.0, first_seen, last_seen FROM device;

-- Augment `alarm`. device_id is retained and kept in sync for one version (its removal is a
-- v0.4.0 ROADMAP line). ne_id and entity_id are backfilled to the level-0 entity, which makes
-- the new UNIQUE index exactly equivalent to the v0.2.0 constraint on all existing rows — the
-- mechanical basis of the cold-start parity gate. severity is NULL (unknown) by default and is
-- never rendered as a fabricated value (§5.3).
ALTER TABLE alarm ADD COLUMN ne_id INTEGER REFERENCES ne (id);
ALTER TABLE alarm ADD COLUMN entity_id INTEGER REFERENCES entity (id);
ALTER TABLE alarm ADD COLUMN severity TEXT;
ALTER TABLE alarm ADD COLUMN severity_rank INTEGER;

UPDATE alarm SET ne_id = device_id;
UPDATE alarm SET entity_id = (
    SELECT e.id FROM entity e WHERE e.ne_id = alarm.device_id AND e.level = 0
);

CREATE UNIQUE INDEX ux_alarm_entity ON alarm (entity_id, class_id, instance);
CREATE INDEX idx_alarm_entity ON alarm (entity_id);
CREATE INDEX idx_alarm_ne ON alarm (ne_id);
