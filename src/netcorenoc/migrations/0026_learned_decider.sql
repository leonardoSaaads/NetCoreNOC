-- 0026: the model takes over (v0.26.0). Forward-only and additive, applying cleanly onto a
-- populated v0.25.0 database (schema 25).
--
-- ADRs #405-#414 carry the decisions; MIGRATION.md carries what an operator sees change.

-- -- A stored link's full explanation --------------------------------------------------------------
--
-- `link` has carried `term_t`, `term_a`, `term_e` since v0.2.0 and, for the additive formula, those
-- three ARE the explanation. A trained model has one term per feature, so its explanation is kept
-- whole: canonical JSON of the basis, the base value, the threshold and every term. NULL for a link
-- the formula made, and for every link written before this migration — nothing can recover a
-- decomposition nobody recorded.
ALTER TABLE link ADD COLUMN terms TEXT;

-- -- Episode memory ---------------------------------------------------------------------------------
--
-- How many SEPARATE occasions two elements, two classes, or two (element, class) signatures have
-- gone wrong together (`engine/correlate/episodes.py`). `k` is the pair's ids joined by commas —
-- a key for a join and for reload, never a feature (0008 rule 2): the model reads the COUNT.
-- Bounded by the engine (200 000 keys per kind, a year of age) and pruned by the sweep.
CREATE TABLE IF NOT EXISTS episode_pair (
    kind    TEXT    NOT NULL CHECK (kind IN ('ne', 'cls', 'item')),
    k       TEXT    NOT NULL,
    count   INTEGER NOT NULL CHECK (count >= 1),
    last_ts REAL    NOT NULL,
    PRIMARY KEY (kind, k)
);

-- -- The v2 feature vector, captured at decision time -------------------------------------------------
--
-- `dataset_pair` has captured the three v1 features since v0.8.0. A site-adapted model is trained
-- on the FOURTEEN the appliance now scores with, and a feature re-derived later from moved learned
-- state would be a feature the scorer never saw (capture.py's `_value_of` argument, one release
-- on). JSON array in `features.FEATURE_NAMES` order; NULL for every row captured before this.
ALTER TABLE dataset_pair ADD COLUMN features TEXT;

-- -- What decides links: the shipped model, a site-adapted one, or the formula ---------------------
--
-- ADR #405: the additive formula stops being the default and becomes an opt-in. APPEND-ONLY, like
-- `scorer_config` and `model_version`: the latest row is in force, and "who switched it, when, and
-- why" is a query, never a reconstruction. The formula's own parameters stay in `scorer_config` and
-- a site model stays in `model_version`; this names which of the three families is running.
CREATE TABLE IF NOT EXISTS decider_setting (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    mode    TEXT    NOT NULL CHECK (mode IN ('shipped', 'site', 'additive')),
    set_by  TEXT    NOT NULL,
    set_at  REAL    NOT NULL,
    reason  TEXT    NOT NULL DEFAULT ''
);

CREATE TRIGGER IF NOT EXISTS decider_setting_no_update BEFORE UPDATE ON decider_setting
BEGIN
    SELECT RAISE(ABORT, 'decider_setting is append-only');
END;

CREATE TRIGGER IF NOT EXISTS decider_setting_no_delete BEFORE DELETE ON decider_setting
BEGIN
    SELECT RAISE(ABORT, 'decider_setting is append-only');
END;

-- THE ONE SEED ROW, and it respects what each appliance had chosen:
--
--   * the active pointer names a MODEL VERSION -> an admin promoted a model here: 'site';
--   * it names a scorer configuration OTHER than the first one ever written (the 0005 seed) -> an
--     admin retuned the formula deliberately, which is opting in to it: 'additive';
--   * otherwise the appliance was running the formula because it was the default: 'shipped'.
INSERT INTO decider_setting (mode, set_by, set_at, reason)
SELECT
    CASE
        WHEN EXISTS (SELECT 1 FROM scorer_active WHERE id = 1 AND model_version_id IS NOT NULL)
            THEN 'site'
        WHEN EXISTS (
            SELECT 1 FROM scorer_active
            WHERE id = 1 AND config_id IS NOT NULL
              AND config_id <> (SELECT MIN(id) FROM scorer_config)
        ) THEN 'additive'
        ELSE 'shipped'
    END,
    'migration',
    0.0,  -- a seeded row is as old as the database (see test_no_migration_reads_a_clock_of_its_own)
    'v0.26.0: the shipped model is the default decider; an appliance whose formula an admin had '
    || 'retuned keeps it, and one running a promoted model keeps that'
WHERE NOT EXISTS (SELECT 1 FROM decider_setting);

-- Which decider formed each situation, beside `scorer_config_id`: 'shipped:<sha12>', 'site:<id>',
-- 'additive:<config id>'. NULL for every situation formed before this migration.
ALTER TABLE situation ADD COLUMN decider TEXT;

-- -- Autonomy (ADR #412) -------------------------------------------------------------------------------
--
-- GRADED: four acts, each switchable on its own. APPEND-ONLY: the newest row is in force, and a row
-- with every grade off is how autonomy is STOPPED — by a person from any screen (the kill switch),
-- or by autonomy itself when its agreement with the operators falls below the floor, with the reason
-- written into the row. Nothing turns it back on except an admin writing a new row.
CREATE TABLE IF NOT EXISTS autonomy_setting (
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

CREATE TRIGGER IF NOT EXISTS autonomy_setting_no_update BEFORE UPDATE ON autonomy_setting
BEGIN
    SELECT RAISE(ABORT, 'autonomy_setting is append-only');
END;

CREATE TRIGGER IF NOT EXISTS autonomy_setting_no_delete BEFORE DELETE ON autonomy_setting
BEGIN
    SELECT RAISE(ABORT, 'autonomy_setting is append-only');
END;

-- Every act autonomy performs, ATTRIBUTED to the decider that made it and EXPLAINED: the evidence
-- it acted on, as canonical JSON. `verdict` is filled in later from what happened next — an
-- operator's gesture that contradicts the act, or one that works the situation without doing so —
-- and the agreement rate over recent verdicts is what suspends autonomy.
CREATE TABLE IF NOT EXISTS autonomy_decision (
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

CREATE INDEX IF NOT EXISTS idx_autonomy_decision_pending ON autonomy_decision (verdict, at);
CREATE INDEX IF NOT EXISTS idx_autonomy_decision_situation ON autonomy_decision (situation_id);

-- A situation's severity, as autonomy (or an operator correcting it) assigned it. NULL until one of
-- them does; the console otherwise shows the worst member alarm's severity, as it always has.
ALTER TABLE situation ADD COLUMN severity TEXT
    CHECK (severity IS NULL OR severity IN ('critical', 'major', 'minor', 'warning', 'indeterminate'));
ALTER TABLE situation ADD COLUMN severity_by TEXT;

-- The name autonomy's `naming` grade gave a situation — a column of its own, because
-- `operator_name` has exactly one writer, the rename route, and a model does not write there
-- (`tests/test_gesture_boundary.py`). The console shows operator_name, then model_name, then
-- derived_name; an operator's rename supersedes the model's without touching it.
ALTER TABLE situation ADD COLUMN model_name TEXT;

-- -- The hyperparameter search, one row per trial (ADR #413) -------------------------------------------
--
-- The record the Judge screen's optimisation-history, parameter and importance charts are drawn
-- from, and what makes a search resumable: a search is (run, seed, space) and trial k's parameters
-- are a pure function of (seed, k), so a restarted search skips the trials it finds here.
CREATE TABLE IF NOT EXISTS search_run (
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

CREATE TABLE IF NOT EXISTS search_trial (
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
