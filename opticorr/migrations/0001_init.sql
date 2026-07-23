-- OptiCorr schema v1. Applied once at startup; see store.py for the migration runner.

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
    vendor     TEXT,
    name       TEXT,
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
    varbinds    TEXT NOT NULL DEFAULT '[]',
    UNIQUE (device_id, class_id, instance)
);
CREATE INDEX idx_alarm_last_seen ON alarm (last_seen);
CREATE INDEX idx_alarm_status ON alarm (status);

-- Learned pairwise state. kind: class | device (NPMI affinity, symmetric, a_id < b_id),
-- lead_class | lead_device (directional precedence wins), clear_pair (a raises, b clears).
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
);
CREATE INDEX idx_situation_status ON situation (status);

CREATE TABLE situation_alarm (
    situation_id INTEGER NOT NULL REFERENCES situation (id),
    alarm_id     INTEGER NOT NULL REFERENCES alarm (id),
    PRIMARY KEY (situation_id, alarm_id)
);
CREATE INDEX idx_situation_alarm_alarm ON situation_alarm (alarm_id);

-- One row per accepted link, with the three score terms (explainability requirement).
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
);
CREATE INDEX idx_link_situation ON link (situation_id);

CREATE TABLE feedback (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    situation_id INTEGER NOT NULL REFERENCES situation (id),
    verdict      TEXT NOT NULL, -- confirm | split
    created_at   REAL NOT NULL
);

CREATE TABLE label (
    kind       TEXT NOT NULL, -- device | class
    target_id  INTEGER NOT NULL,
    label      TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (kind, target_id)
);

CREATE TABLE quarantine (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,
    raw         BLOB NOT NULL,
    reason      TEXT NOT NULL,
    received_at REAL NOT NULL
);
