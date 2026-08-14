-- 0013: champion/challenger (v0.11.0). The artefact, the event, the fold assignment a promotion
-- cites, and the hash chain over the seal's access log.
--
-- Its design is `docs/analysis/PREREGISTRATION-0.11.0.md` §2, §3 and §5, ratified at sha256
-- e011ee6ad2367d44f2ede14cad7b072df598298f91ecc1a405744358b589d449 before any of this existed.
-- The decisions are DECISIONS #160-#163.
--
-- Forward-only and additive, applying cleanly onto a populated v0.10.1 database (schema v12).
-- **NO ROWS ARE SEEDED.** A fresh install and an upgraded one both start with no model version, no
-- promotion and no fold assignment, and `tests/test_upgrade.py` asserts it.
--
-- ---------------------------------------------------------------------------------------------
-- WHY THIS IS THREE TABLES AND NOT A ROW IN `scorer_config`.
--
-- `CHAMPION-CHALLENGER-0.11-DRAFT.md` §1 specifies that promotion is `scorer_config` gaining a row.
-- That was written by v0.10.0 from what it could see, and it is refuted THREE INDEPENDENT TIMES.
-- Each refutation was reproduced by execution in `docs/gates/v0.11.0-phase-0.md` §2 rather than
-- argued:
--
--   1. THE COLUMNS ARE THE ADDITIVE SCORER'S. w_t, w_a, w_e, tau_s, threshold, all NOT NULL. A
--      logistic model has an intercept and one weight per feature, and the feature set is a
--      modelling decision this project has explicitly not frozen.
--
--   2. A FITTED CHALLENGER IS REJECTED BY THE VALIDATOR, and relaxing the validator would be a
--      SAFETY REGRESSION rather than a schema convenience. Measured:
--
--          validate_params(1.7, 3.1, -0.8, 30.0, 0.0)
--            -> ScorerParamsError: w_t must be between 0 and 1, got 1.7
--
--      and independently on w_a, on w_e, and on threshold. The threshold is the one that cannot be
--      negotiated: MIN_THRESHOLD is 0.01 because FOR THE ADDITIVE SCORER a threshold at zero links
--      every candidate pair into one situation, while FOR THE LOGISTIC SCORER 0.0 is exactly
--      p = 0.5, the neutral point. THE SAME NUMBER MEANS OPPOSITE THINGS TO THE TWO KINDS, and
--      `validate_params` is the sole barrier between a database row and the correlation engine.
--
--   3. NULL WOULD BECOME AMBIGUOUS. `POST /api/scorer` inserts a row with no judgement, no
--      challenger run and no approver — 11 arguments, none naming any of the three. A promotion
--      column added there would be NULL on EVERY manual retune, so NULL would mean both "a human
--      retuned this by hand" and "this was a promotion and its provenance is missing". That is the
--      ambiguity v0.9.2 spent a release eliminating.
--
-- Hence: `model_version` holds the ARTEFACT, `promotion` holds the EVENT, and `scorer_config` is
-- LEFT ENTIRELY ALONE and keeps receiving manual retunes exactly as it does today.
-- ---------------------------------------------------------------------------------------------

-- -- THE ARTEFACT ----------------------------------------------------------------------------------
--
-- `params_document` is CANONICAL JSON WITH A PER-KIND VALIDATOR, not typed columns (DECISIONS #161).
--
-- Typed columns per scorer kind means ONE MIGRATION PER SCORER KIND, FOREVER, and v0.12.0
-- (per-archetype weights) and v0.13.0 (the external cartridge) are both coming. Two precedents in
-- this schema already chose the document and wrote down why: `governance_policy` is kind + document
-- + doc_hash with a separate active pointer, and `challenger_run.coefficients` is already JSON
-- because "the feature set is a modelling decision this release is explicitly not freezing".
--
-- THE DOCUMENT DOES NOT MAKE THIS A PLUGIN SURFACE. There is no `adapter` column, no registry and
-- no entry point. v0.13.0 owns the external cartridge, and a model inventory that grew an adapter
-- column would be that release starting early — which the draft's §4 names in advance.
--
-- THE PAYLOAD CARRIES THE BARRIER THE TYPED COLUMNS PROVIDED, and this is the part a build gets
-- wrong: `SafeScorer` catches an EXCEPTION AT SCORE TIME. It does not catch A PARAMETER SET THAT
-- SCORES WITHOUT RAISING AND DESTROYS GROUPING. An all-zero logistic weight vector raises nothing,
-- returns a finite score and a full term breakdown, and gives every pair the identical logit — the
-- scorer stops discriminating and every guard stays green. The degeneracy rules that refuse it are
-- registered in the plan's §5, BEFORE any fit existed that they could have been chosen to suit.
CREATE TABLE IF NOT EXISTS model_version (
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

CREATE INDEX IF NOT EXISTS idx_model_version_created ON model_version (created_at);

-- Append-only at the storage layer, exactly like `scorer_config` and `audit_log`: an edit to the
-- parameters behind a historical grouping must be impossible through any normal path.
CREATE TRIGGER IF NOT EXISTS model_version_no_update BEFORE UPDATE ON model_version
BEGIN
    SELECT RAISE(ABORT, 'model_version is append-only');
END;

CREATE TRIGGER IF NOT EXISTS model_version_no_delete BEFORE DELETE ON model_version
BEGIN
    SELECT RAISE(ABORT, 'model_version is append-only');
END;

-- -- THE EVENT -------------------------------------------------------------------------------------
--
-- ONE ROW PER DECISION, AND **REFUSALS LEAVE ROWS**. The plan's §2 makes that a validity condition
-- rather than a suggestion, for the reason `holdout_access` records refusals: a table of successes
-- answers "what is deployed" and NOT "what has this appliance been asked to deploy", and the second
-- is the audit question.
--
-- Separate from `model_version` because the cardinalities differ: ONE model version may be proposed
-- many times and refused many times. Merging them would either duplicate the parameters per attempt
-- or lose the attempts, and losing the attempts is losing the audit question.
--
-- Every column below is one of the plan's §2 items, in its order, so a reader can check the list
-- against the schema without a mapping table:
--
--   (1) model_version_id      -> and through it params_hash and challenger_run_id
--   (2) verdict               -> AS RE-DERIVED BY THE SERVER at decision time
--   (3) triggers              -> enumerated, not summarised
--   (4) metrics               -> the four named quantities with clustered intervals, BOTH arms
--   (5) evaluation_run_id     -> the fold assignment reference, so numbers point at stored rows
--   (6) plan_sha256           -> the ratified plan in force
--   (7) query_count           -> the seal's query count at decision time
--   (8) approved_by/decided_at
--   (9) outcome / refusal_reason
--
-- plus `unavailable`, which is §2's "states which were unavailable and why" for a refusal that
-- could not carry all nine.
CREATE TABLE IF NOT EXISTS promotion (
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

CREATE INDEX IF NOT EXISTS idx_promotion_decided ON promotion (decided_at);

CREATE TRIGGER IF NOT EXISTS promotion_no_update BEFORE UPDATE ON promotion
BEGIN
    SELECT RAISE(ABORT, 'promotion is append-only: a decision that can be edited is not a record');
END;

CREATE TRIGGER IF NOT EXISTS promotion_no_delete BEFORE DELETE ON promotion
BEGIN
    SELECT RAISE(ABORT, 'promotion is append-only: deleting a refusal is how a history becomes a table of successes');
END;

-- -- THE FOLD ASSIGNMENT --------------------------------------------------------------------------
--
-- `DATA-LINEAGE.md` §4 measured why this table exists, and the measurement is the argument:
--
--   fold assignment is a pure function of the incident ids;
--   incident identity is `incidents.resolve_all` over the merge edges;
--   the merge edges CHANGE when situations merge;
--   NO SNAPSHOT OF THE MERGE GRAPH IS RETAINED ANYWHERE.
--
-- So a bag labelled today belongs to incident I, tomorrow its situation is merged and it belongs to
-- incident J, `assign_folds` over a different id set produces a different rotation, and THE BAG
-- MOVES TO A DIFFERENT FOLD. An evaluation is reproducible today and not necessarily later.
--
-- Until now nothing decided, so nothing broke. FROM THIS RELEASE AN ADMIN APPROVES A SWAP CITING AN
-- EVALUATION, and a citation to a number nobody can reproduce is not evidence.
--
-- WHAT THIS BUYS AND WHAT IT DOES NOT, stated here so the limitation is not discovered later:
-- storing the assignment makes the evaluation's FOLDS reproducible. It does not reproduce the
-- merge graph, so it does not explain WHY an incident moved, and it does not make the SEAL's own
-- membership reproducible. `DATA-LINEAGE.md` §4 names snapshotting the merge edges as the candidate
-- that would, and §5 names its cost — a second copy of a live table, with the drift risk that
-- implies. THIS RELEASE DELIBERATELY DOES NOT CHOOSE IT, and the deferral is recorded rather than
-- left as an omission.
CREATE TABLE IF NOT EXISTS evaluation_fold (
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

CREATE TRIGGER IF NOT EXISTS evaluation_fold_no_update BEFORE UPDATE ON evaluation_fold
BEGIN
    SELECT RAISE(ABORT, 'evaluation_fold is immutable: a cited fold assignment that can move is not a citation');
END;

CREATE TRIGGER IF NOT EXISTS evaluation_fold_no_delete BEFORE DELETE ON evaluation_fold
BEGIN
    SELECT RAISE(ABORT, 'evaluation_fold is immutable');
END;

-- -- THE ACTIVE POINTER --------------------------------------------------------------------------
--
-- ONE ACTIVE POINTER, NEVER TWO, ENFORCED BY THE DATABASE.
--
-- Two sources of truth about WHAT IS RUNNING is what principle 6 forbids, and this is the one place
-- where getting it wrong would be SILENT: the engine would load one of them, the API would report
-- the other, and every grouping decision in between would be attributed to the wrong parameters.
--
-- The table is REBUILT rather than altered because `config_id` has to become nullable and SQLite
-- cannot drop a NOT NULL or add a CHECK in place. The rebuild is the documented create-copy-drop-
-- rename procedure. The usual `PRAGMA foreign_keys=OFF` step is NOT needed and is deliberately not
-- taken: nothing in this schema references `scorer_active`, so there is no child row to orphan —
-- `PRAGMA foreign_key_check` after the migration is what proves that rather than this comment.
--
-- Every existing row keeps its `config_id` and gets `model_version_id = NULL`, which is truthful:
-- every pre-v0.11.0 appliance was in fact running an additive configuration.
CREATE TABLE scorer_active_0013 (
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

INSERT INTO scorer_active_0013 (id, config_id, model_version_id, activated_by, activated_at)
SELECT id, config_id, NULL, activated_by, activated_at FROM scorer_active;

DROP TABLE scorer_active;

ALTER TABLE scorer_active_0013 RENAME TO scorer_active;

-- -- THE SEAL'S ACCESS LOG BECOMES HASH-CHAINED ---------------------------------------------------
--
-- `PREREGISTRATION-0.11.0.md` §3 registers it, per `SECURITY-REVIEW-0.10.0.md` §3.4: **if a
-- promotion cites a query count as evidence, that record must be tamper-evident and not merely
-- append-only — triggers stop the application, not somebody with the file.**
--
-- Same construction as `audit_log` since 0002: entry_hash = sha256(prev_hash + canonical(row)),
-- genesis prev_hash is 64 zeros.
--
-- PRE-EXISTING ROWS CARRY NULL AND ARE NOT BACKFILLED, and that is a decision rather than an
-- omission. Two reasons, and the second is the one that settles it:
--
--   * SQLite has no sha256, and these migrations are plain SQL by contract. A backfill is not
--     expressible here.
--   * MORE IMPORTANTLY, IT WOULD BE FABRICATION. A chain hash asserts "this row was hashed AT WRITE
--     TIME and any later edit is detectable". Computing one during an upgrade would assert that
--     about rows for which it was never true. DECISIONS #133's line, in the direction that forbids:
--     recomputing a quantity from evidence already stored is derivation; inventing a quantity whose
--     input is gone is fabrication and must be NULL. Here the missing input is THE CHAIN ITSELF.
--
-- `chain_source` names which rows were chained live, exactly as `excluded_reconciled_source`
-- distinguishes 'live' from 'backfill' one release earlier. The verifier reports the unchained
-- prefix as a count rather than treating it as damage.
--
-- IN PRACTICE THE PREFIX IS EMPTY ON EVERY REAL UPGRADE: no code path in v0.10.x writes
-- `holdout_access` at all — `seal.ratify` and `seal.spend` have no call site in `src/`, and
-- `seal.construct` does not log. Measured in `docs/gates/v0.11.0-phase-2.md` rather than assumed.
ALTER TABLE holdout_access ADD COLUMN prev_hash TEXT;
ALTER TABLE holdout_access ADD COLUMN entry_hash TEXT;
ALTER TABLE holdout_access ADD COLUMN chain_source TEXT;

-- -- An index? Only the two above. ----------------------------------------------------------------
--
-- `model_version` and `promotion` get one each on their timestamps, because both are listed
-- newest-first by an operator-facing read, which is the same justification `idx_scorer_config_created`
-- carries. `evaluation_fold` gets none: it is read by exact `run_id`, which the PRIMARY KEY's
-- leftmost column already serves. `holdout_access` gets none, for 0012's reason — it is scanned in
-- full to produce a query count, which is the only thing anyone asks it.
