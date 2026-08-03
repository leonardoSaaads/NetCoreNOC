-- 0009: shadow mode (v0.9.0). The challenger's provenance, and the sampled opinions it wrote
-- where nobody acts on them. Its design is `docs/architecture/SHADOW-MODE-0.9-DRAFT.md`; the
-- evidentiary standard it serves is `docs/analysis/PREREGISTRATION-0.9.0.md`, which was written
-- before any model existed and is hash-guarded.
--
-- Forward-only and additive, applying cleanly onto a populated v0.8.1 database (schema v8).
--
-- IT SEEDS NO ROWS, and that is a release gate. A migrated appliance holds two empty tables and
-- behaves at first boot exactly as it did before the upgrade: **shadow mode changes no grouping**,
-- and it changes none on the first boot after the upgrade either. Compare 0005, which *had* to seed
-- a row because the engine needs scoring parameters; nothing here is needed for the engine to run,
-- because the challenger is never consulted about anything.
--
-- ---------------------------------------------------------------------------------------------
-- THE INVARIANT THIS MIGRATION EXPRESSES STRUCTURALLY:
--
--     THE CHAMPION DECIDES EVERYTHING. Nothing in these two tables is read by the correlation
--     path, by `engine.scorer`, by the API, or by the UI. There is NO promotion mechanism in this
--     release, deliberately: a release that could promote would be judged by the only metric it
--     had, which would be agreement with the champion.
--
-- The schema cannot enforce that on its own — no schema can — so it is asserted by
-- `tests/test_shadow.py::test_the_challenger_cannot_become_the_active_scorer` and by the layer
-- guard. What the schema DOES do is refuse to make it convenient: there is no pointer from
-- `scorer_config` to a `challenger_run`, and adding one would be a visible, arguable diff.
--
-- KEYS ARE NOT FEATURES (0008's rule 2, unchanged). Every identifier below is present FOR A JOIN.
-- ---------------------------------------------------------------------------------------------
--
-- SECURITY POSTURE. These rows are written engine-side, where visibility scoping does not exist
-- and must not, so they inherit 0008's posture exactly: **no read below `admin`, on any route, in
-- any format, ever.** v0.9.0 adds no route at all, which is the strongest form of that.

-- -- The challenger run: WHAT WAS FITTED, ON WHAT, AND UNDER WHICH STANDARD --------------------
--
-- The counterpart of `capture_run`, and for the same reason: version, policy, training window and
-- thresholds are CONSTANTS OVER A PERIOD, not per-opinion facts. Normalising them here is what
-- makes two shadow reports comparable — and, more importantly, what makes a report from one
-- deployment interpretable in another. Two deployments reporting "passed" must not be able to mean
-- different things without saying so.
--
-- A ROW IS WRITTEN EVEN WHEN NOTHING IS FITTED. `sufficient = 0` with `coefficients` NULL is the
-- release's most likely outcome and is a RESULT, not an absence: "the corpus does not support a
-- challenger, here is which floors it missed, and here is how long until it would meet them."
-- A schema that could only record a successful fit would make insufficiency invisible, which is
-- precisely the failure the pre-registration exists to prevent.
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
);

-- -- One row per SAMPLED pair the challenger scored ONLINE, inside the engine ------------------
--
-- Sampled, not complete: at the default rate this is a rounding error against the pair rows capture
-- already writes, and the release proves that with a measurement in rows, bytes and microseconds
-- rather than claiming it.
--
-- >>> THE FEATURE VALUES BELOW ARE STORED **AS SERVED**, AND THAT IS THE WHOLE POINT. <<<
--
-- Offline reconstruction recomputes the challenger's opinion from `dataset_pair`, which is
-- tautologically consistent with itself and therefore CANNOT MEASURE TRAINING/SERVING SKEW. These
-- columns are the other side of that comparison: the numbers the scorer actually saw at serving
-- time. Their disagreement with the reconstruction IS the skew test (DECISIONS #119), the expected
-- rate is 0.0000%, and a non-zero rate is a defect rather than a tolerance to widen.
--
-- There is no target column here either, for 0008's reason: the only label in this schema lives in
-- `feedback` and reaching it requires the join.
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
-- The bounded prune runs oldest-first over this table, so the index leads with time.
CREATE INDEX idx_shadow_age ON shadow_opinion (evaluated_at);
-- The skew test's join: "the offline reconstruction of this sampled pair".
CREATE INDEX idx_shadow_pair ON shadow_opinion (alarm_a, alarm_b, evaluated_at);
