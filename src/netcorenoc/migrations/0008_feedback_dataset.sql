-- 0008: the operator-feedback dataset (v0.8.0). The release's whole deliverable is the schema
-- below, and its design — with every column justified — is `docs/architecture/DESIGN.md`
-- § "v0.8.0 — the feedback dataset". The binding specification is
-- `docs/architecture/FEEDBACK-DATASET-0.8-DRAFT.md` as corrected in this release's Phase 1.
--
-- Forward-only and additive, applying cleanly onto a populated v0.7.5 database (schema v7).
--
-- IT SEEDS NO DATASET ROWS, and that is a release gate. A migrated appliance holds four empty
-- tables and behaves at first boot exactly as it did before the upgrade; capture begins when the
-- engine next processes a trap, not when this script runs. Compare 0005, which *had* to seed a row
-- because the engine needs scoring parameters — this needs nothing, because an empty dataset is a
-- correct dataset.
--
-- THE ONE PERMITTED DATA WRITE is the `capture_provenance` backfill at the bottom. It writes a
-- MARKER ABOUT PROVENANCE, never a guess about content, and the difference is the point.
--
-- No trigger is added to a hot table that was not already triggered. `alarm`, `link` and
-- `situation_alarm` are untouched; `situation` gains one nullable column and no trigger.
--
-- ---------------------------------------------------------------------------------------------
-- TWO RULES GOVERN EVERY COLUMN BELOW, and a column satisfying neither does not exist:
--
--   1. STORE WHAT CANNOT BE RECOMPUTED; DERIVE WHAT CAN. `A` and `E` decay continuously, `alarm`
--      is deduplicated and mutated on re-fire, and situations are merged and lose their
--      membership. A moment not captured is not captured late — it is captured never. So the
--      asymmetry here runs the opposite way from every previous release of this project: an
--      unneeded column costs bytes, a missing one costs the field forever.
--
--   2. KEYS ARE NOT FEATURES. Every identifier below is present FOR A JOIN. Feeding one to a model
--      as a numeric input teaches it the training customer's estate — `ne_id = 47` means "the
--      forty-seventh NE this appliance ever saw" and nothing else — so it generalises to nobody
--      while scoring extremely well on that customer's held-out data, which is what makes the
--      mistake survive review. Columns marked KEY below are joins. They are not inputs.
-- ---------------------------------------------------------------------------------------------
--
-- SECURITY POSTURE (`FEEDBACK-DATASET-0.8-DRAFT.md` §5a). These tables are captured ENGINE-SIDE,
-- where visibility scoping does not exist and must not — correlation learns across the whole
-- estate. They therefore contain every NE, entity and raw varbind in the network, UNGOVERNED by
-- any scope policy. That makes the dataset a scope bypass BY CONSTRUCTION, and the response is to
-- treat it as one: NO READ OF THESE ROWS BELOW `admin`, ON ANY ROUTE, IN ANY FORMAT, EVER. The
-- bias report emits aggregates only.

-- -- The capture context ------------------------------------------------------------------------
--
-- Version, scorer configuration, window bounds and retention policy are CONSTANTS OVER A PERIOD,
-- not per-pair facts. Normalising them here is "generous in columns, not in row multiplicity"
-- applied one level up, and it is what makes the dataset reproducible and two bias reports
-- comparable. A new row is opened at engine start and whenever the scorer configuration or the
-- retention policy changes, so pair rows on either side of an admin's retune are distinguishable —
-- which nothing else would record.
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

-- -- One row per alarm OBSERVATION ---------------------------------------------------------------
--
-- PER ACTIVATION, NOT PER TRAP (DECISIONS #105). Measured: 3 159 traps produced 2 256 activations
-- over the eval corpus, a ratio of 0.7142 agreeing with `make eval`'s independently-reported
-- dedup_ratio. The 903 extra rows a per-trap policy would write are re-fires that BY CONSTRUCTION
-- never reached `score_link`, so no pair row could ever reference them: a 40% increase in write
-- volume on the ingest path, inside the batch lock, buying rows nothing can join to.
--
-- Not redundant with `alarm`, and this is the subtle part: `alarm` is deduplicated on
-- (entity_id, class_id, instance) — `migrations/0003_entity.sql:94` — and MUTATED ON RE-FIRE, so it
-- holds the LATEST state and cannot answer "what did this look like when the decision was made?".
-- This row is the immutable record of what arrived, and the pair rows reference it.
--
-- This is also where "record everything raw and generous" belongs: ONE ROW PER OBSERVATION, not one
-- per pair. Storing varbinds per pair would duplicate the same blob up to MAX_CANDIDATES = 100
-- times and amplify I/O inside the batch lock.
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

-- -- One row per EVALUATED pair ------------------------------------------------------------------
--
-- Linked AND rejected alike, written BEFORE MAX_LINKS_PER_ALARM truncation. Phase 0 measured what
-- that truncation discards on the eval corpus: 194 341 pairs evaluated, 194 002 accepted, 10 973
-- persisted to `link` — so 183 029 rows, 94%, are ACCEPTED LINKS THE CAP DROPPED, and only 339
-- (0.17%) are pairs the scorer rejected.
--
-- >>> THERE IS NO TARGET COLUMN IN THIS TABLE, AND THAT IS DELIBERATE. <<<
--
-- The only label in this schema lives in `feedback`, and reaching it requires the join. That
-- friction is the mechanism: it is what stops a v0.9.0 author from reaching for `incumbent_linked`
-- by accident or by autocomplete. The invariant it expresses (DECISIONS #103):
--
--     NO METRIC THAT DECIDES PROMOTION MAY BE COMPUTED AGAINST `incumbent_linked`.
--
-- Training against the incumbent is a technique with a purpose; being JUDGED by it is circular, and
-- the challenger's ceiling becomes the champion's performance.
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
);
-- The sink's bounded deletion runs once a minute over the largest table in the schema; this index
-- is what keeps it cheap. It leads with `lifecycle` because the prune must never see a promoted row
-- (the checked guarantee DECISIONS #107 accepts as the cost of the one-table layout).
CREATE INDEX idx_pair_sink ON dataset_pair (lifecycle, evaluated_at);
-- Promotion's join: "every pair belonging to this situation".
CREATE INDEX idx_pair_situation ON dataset_pair (situation_id);

-- -- The membership record: two halves, ONE trusted ----------------------------------------------
--
-- Phase 0 proved by execution that a merged situation's label loses its referent ENTIRELY:
-- `merge_situations` moves `situation_alarm`, re-points `link`, marks the source 'merged' — and
-- does not touch `feedback`. Afterwards `feedback JOIN situation_alarm` returns NOTHING, and the
-- surviving situation holds the union of both bags with nothing distinguishing them.
--
-- A CHILD TABLE rather than a column, because it is a LIST — and because a digest proves that
-- something changed without saying what it was, and after a merge there is nothing left to compare
-- a digest against (DECISIONS #104). The digest is stored as well, on `feedback`, for cheap
-- comparison.
--
-- IT MAY LEGITIMATELY HOLD ZERO ROWS FOR A FEEDBACK ID. A verdict posted to an already-merged
-- situation answers 200, writes a `feedback` row, and hands `learn.penalize()` an empty bag —
-- reproduced in Phase 0 §2. Recording an empty bag AS EMPTY is what makes that population countable
-- for the first time, so NO "at least one member" constraint may be imposed here.
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

-- -- What the label row gains ---------------------------------------------------------------------
--
-- `feedback` already carries `principal_ref` and `role` from 0007. Everything below is one-to-one
-- with a verdict and irrecoverable afterwards. All nullable, and DELIBERATELY NOT BACKFILLED except
-- where noted: the information does not exist for rows written before this migration, and inventing
-- it would be worse than admitting none — 0007's own comment states that principle and it applies
-- unchanged here.

-- The server-side bag, summarised. `member_count = 0` is legitimate and informative (see above).
ALTER TABLE feedback ADD COLUMN member_digest TEXT;
ALTER TABLE feedback ADD COLUMN member_count INTEGER;

-- THE SCOPE FINGERPRINT. A scoped editor labels a PARTIAL VIEW and cannot say which part, because
-- the redaction deliberately carries no NE id, address or entity key. Without this the label is
-- uninterpretable — and the resulting noise is SYSTEMATIC, not random: it correlates with the scope
-- policy, so it does NOT average out with more data. It teaches a model the shape of the policy.
ALTER TABLE feedback ADD COLUMN scope_policy_id INTEGER;
ALTER TABLE feedback ADD COLUMN scope_restricted INTEGER;
ALTER TABLE feedback ADD COLUMN scope_redacted_members INTEGER;

-- THE ACQUISITION CHANNEL. v0.8.0 writes 'organic' on every row and that is the whole behaviour.
-- It exists for what happens if a later release ever SOLICITS labels (active learning): solicited
-- labels have a deliberately different distribution, and mixed into one undifferentiated column
-- they would destroy the bias characterisation RETROACTIVELY — including for rows written before
-- solicitation began. Adding it later costs a migration PLUS an ambiguity no migration can resolve.
ALTER TABLE feedback ADD COLUMN acquisition_channel TEXT;

-- THE CAPTURE PROVENANCE. See the backfill at the bottom of this file.
ALTER TABLE feedback ADD COLUMN capture_provenance TEXT;

-- LABEL LATENCY. A verdict given four seconds after a situation opened and one given after ten
-- minutes of investigation are NOT THE SAME EVIDENCE. This is also how the sink's 21-day default
-- gets tested against reality instead of defended as a guess.
ALTER TABLE feedback ADD COLUMN situation_opened_at REAL;

-- LINEAGE. The situation id AS IT STOOD when the verdict was given, which is not necessarily the id
-- anything holds afterwards. With `sid = min(sids)` and merges, situation identity is NOT STABLE,
-- and two labels that look like different incidents may be the same one — a split that leaks across
-- that boundary leaks SILENTLY.
ALTER TABLE feedback ADD COLUMN situation_id_at_label INTEGER;

-- PROMOTION COVERAGE, recorded per label and never silent. Three cases, all three facts on the row:
-- every pair of the bag was found; some were not; none were. The middle case is NOT an edge case —
-- the sliding window and MAX_CANDIDATES mean pairs within a labelled situation MAY NEVER HAVE BEEN
-- EVALUATED (a nine-member situation has thirty-six pairs and not all passed through `score_link`),
-- so a naive join drops part of the bag silently and v0.9.0 trains believing it saw a whole
-- situation. That is the same censoring this release exists to prevent, one level up.
ALTER TABLE feedback ADD COLUMN coverage TEXT;
ALTER TABLE feedback ADD COLUMN coverage_found INTEGER;
ALTER TABLE feedback ADD COLUMN coverage_expected INTEGER;

-- THE CLIENT'S REPORT — untrusted, and never rejected. `client_truncated` records that a bound was
-- applied, because truncation must be a FACT rather than a silence.
ALTER TABLE feedback ADD COLUMN client_member_digest TEXT;
ALTER TABLE feedback ADD COLUMN client_member_count INTEGER;
ALTER TABLE feedback ADD COLUMN client_updated_at REAL;
ALTER TABLE feedback ADD COLUMN client_truncated INTEGER;

-- -- The merge edge -------------------------------------------------------------------------------
--
-- A Phase 0 finding, and a PREREQUISITE rather than a nicety. `merge_situations` records
-- `status='merged'` but NOT THE DESTINATION, so today the merge chain is unrecoverable: a reader
-- holding a labelled situation id cannot follow it forward to the situation that absorbed it.
-- Without this column, lineage is unimplementable and v0.10.0's split-by-incident cannot be built.
--
-- Nullable, no trigger, no index: it is written by the single UPDATE that `merge_situations`
-- already runs, so this adds one column to an existing statement and no new write.
--
-- HONEST LIMIT: this records merges from v0.8.0 FORWARD. Merges that happened before the upgrade
-- are gone — the destination was never written and no migration can reconstruct one.
ALTER TABLE situation ADD COLUMN merged_into INTEGER;

-- -- The one permitted data write: the capture-provenance backfill -------------------------------
--
-- Labels written BEFORE v0.7.5 were acquired over the defective path that release repaired: an
-- expanded situation card could be destroyed and rebuilt underneath the operator, so a fraction of
-- those clicks — UNKNOWABLE, because nothing recorded it — landed on a card the operator was not
-- reading.
--
-- The temptation is to delete them. The precise claim does not support that:
--
--     THEY ARE NOT KNOWN TO BE BAD. They are of UNKNOWN QUALITY, which is a different and weaker
--     claim, and the difference decides what to do with them.
--
-- So they are stored, MARKED, excluded from training by default, and includable by an explicit and
-- conscious choice. That preserves a comparison a future release may well want — models trained on
-- new data versus new plus old — which deleting them would have destroyed permanently.
-- "These are contaminated" is a hypothesis wearing the clothes of a fact, and a schema is the wrong
-- place to promote one.
--
-- THIS WRITES A MARKER ABOUT PROVENANCE, NEVER A GUESS ABOUT CONTENT. It says "this row was
-- acquired over the pre-v0.7.5 path", which is knowable from the fact that the row exists at
-- migration time. It says nothing whatever about whether any particular verdict was meant.
--
-- Every row present when 0008 runs predates v0.8.0's capture path by definition, so the marker is
-- exact rather than estimated. Rows written afterwards are marked 'current' by the application.
UPDATE feedback SET capture_provenance = 'legacy_capture' WHERE capture_provenance IS NULL;
