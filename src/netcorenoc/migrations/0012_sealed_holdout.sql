-- 0012: the sealed holdout (v0.10.0). A set of incidents reserved for a decision no release in this
-- version is permitted to make, plus an append-only record of every time anyone looked at it.
--
-- Its design is `docs/analysis/PREREGISTRATION-0.10.0.md` §3.3 and §4.3, ratified at sha256
-- c03aef0181554c0c71482e57d03677f25964c3a5ac20a7bf1b1d74bff1ba1e01 before any of this existed.
-- The decisions are DECISIONS #145 and #147-#149.
--
-- Forward-only and additive, applying cleanly onto a populated v0.9.2 database (schema v11).
-- **NO ROWS ARE SEEDED.** A seal is constructed by the maintenance pass, once, deliberately.
--
-- ---------------------------------------------------------------------------------------------
-- WHY THIS MIGRATION EXISTS, IN ONE MEASUREMENT.
--
-- Adaptive selection over 12 queries on 37 incidents inflates a reported rate by a MEDIAN
-- +11.1 percentage points WHEN EVERY CANDIDATE IS EQUALLY GOOD -- i.e. when the entire gain is
-- selection noise. Reproduced by execution in `docs/gates/v0.10.0-phase-1.md` §3, exactly, on all
-- six figures of the plan's table:
--
--     incidents  queries  median inflation   p90
--            12        4           +13.3    +21.7
--            37       12           +11.1    +16.5
--           120       12            +6.7    +10.0
--
-- Four releases (v0.10.0 -> v0.13.0) tuning against one holdout is the middle row. AN ELEVEN-POINT
-- IMPROVEMENT PRODUCED ENTIRELY BY LOOKING IS LARGER THAN ANY IMPROVEMENT THIS PROJECT IS LIKELY TO
-- PRODUCE BY LEARNING.
--
-- Hence the asymmetry that governs everything below: RESERVING LATER IS IMPOSSIBLE; SPENDING LATER
-- IS ALWAYS POSSIBLE. v0.10.0 constructs the seal and does not spend it. Its query count is 0.
-- ---------------------------------------------------------------------------------------------

-- -- THE SEAL -------------------------------------------------------------------------------------
--
-- EXACTLY ONE ROW, EVER, ENFORCED BY THE SCHEMA.
--
-- `singleton` is a constant column with a UNIQUE constraint and a CHECK pinning it to 1, so a second
-- INSERT fails at the database rather than at whichever layer above remembered to look. That is the
-- whole point: A SEAL THAT CAN BE REBUILT IS A SEAL THAT CAN BE REBUILT *AFTER* SEEING A RESULT.
-- Refusal has to be structural, because the person rebuilding it would be doing so for a reason
-- that felt good at the time.
--
-- The alternative -- allowing many seals and taking the newest -- was rejected for exactly that
-- reason. It reads as more flexible and is precisely the affordance the plan forbids.
--
-- `digest` is the SHA-256 over the ordered incident ids, so the membership is verifiable
-- independently of the child table. `plan_sha256` records WHICH ratified pre-registration
-- authorised this construction; a seal built with no plan on record is not a seal, and §4.3(3)
-- requires the same hash before it may ever be read.
CREATE TABLE IF NOT EXISTS holdout_seal (
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

-- Immutable once written. The seal is the one object in this schema whose VALUE is the thing being
-- protected: an UPDATE that changed which incidents were sealed, after a result had been seen,
-- would be undetectable in the reports and would invalidate every claim the release makes.
CREATE TRIGGER IF NOT EXISTS holdout_seal_no_update BEFORE UPDATE ON holdout_seal
BEGIN
    SELECT RAISE(ABORT, 'holdout_seal is immutable: a seal that can be edited is not a seal');
END;

CREATE TRIGGER IF NOT EXISTS holdout_seal_no_delete BEFORE DELETE ON holdout_seal
BEGIN
    SELECT RAISE(ABORT, 'holdout_seal is immutable: deleting a seal is how it gets re-cut');
END;

-- -- THE MEMBERSHIP -------------------------------------------------------------------------------
--
-- AN EXPLICIT IMMUTABLE LIST, not a predicate. §3.3(4).
--
-- A predicate -- "the last third by earliest label" -- would be re-evaluated against whatever the
-- corpus looked like at read time, so the sealed set would DRIFT as labels arrived, and a release
-- could change what was sealed by changing what was labelled. Storing the ids makes the seal a
-- fact about a moment rather than a query about a table.
--
-- `position` preserves the construction order, which is what `digest` is computed over.
CREATE TABLE IF NOT EXISTS holdout_seal_member (
    seal_id     INTEGER NOT NULL REFERENCES holdout_seal(id),
    position    INTEGER NOT NULL,
    incident_id INTEGER NOT NULL,
    PRIMARY KEY (seal_id, position)
);

CREATE TRIGGER IF NOT EXISTS holdout_seal_member_no_update BEFORE UPDATE ON holdout_seal_member
BEGIN
    SELECT RAISE(ABORT, 'holdout_seal_member is immutable');
END;

CREATE TRIGGER IF NOT EXISTS holdout_seal_member_no_delete BEFORE DELETE ON holdout_seal_member
BEGIN
    SELECT RAISE(ABORT, 'holdout_seal_member is immutable');
END;

-- -- THE ACCESS LOG -------------------------------------------------------------------------------
--
-- EVERY ACCESS IS A ROW, NOT PROSE. §4.3(2).
--
-- `HONEST-JUDGE-0.10-DRAFT.md` §4 proposed "a counter maintained by hand, in prose" as the only
-- available defence against iterative overfitting. This is that counter, made durable and made
-- unable to disagree with itself: which release asked, under which ratified plan, what was asked,
-- and what came back.
--
-- REFUSALS ARE LOGGED TOO, and `granted` is what distinguishes them. A log that recorded only
-- successful reads would answer "how many times has the holdout been spent" and NOT "how many
-- times has somebody tried" -- and the second is the question a reviewer of a four-release tuning
-- loop actually needs. It is also the only way a missing-plan refusal leaves a trace.
--
-- APPEND-ONLY, by the same triggers `audit_log` has carried since 0002, and for a stronger reason:
-- the audit log is evidence about operators, and this is evidence about the ANALYSIS ITSELF. A
-- release that could delete its own query count could report 0 having spent the seal.
--
-- NOT the audit log, deliberately (DECISIONS #148). `audit_log` is a hash-chained record of
-- OPERATOR actions reachable from HTTP; nothing here is reachable from a route, no principal
-- performs it, and adding a system-generated action to a frozen catalog is the decision v0.9.2 §4.6
-- declined to make alone.
CREATE TABLE IF NOT EXISTS holdout_access (
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
);

CREATE TRIGGER IF NOT EXISTS holdout_access_no_update BEFORE UPDATE ON holdout_access
BEGIN
    SELECT RAISE(ABORT, 'holdout_access is append-only');
END;

CREATE TRIGGER IF NOT EXISTS holdout_access_no_delete BEFORE DELETE ON holdout_access
BEGIN
    SELECT RAISE(ABORT, 'holdout_access is append-only');
END;

-- -- An index? No. ---------------------------------------------------------------------------------
--
-- `holdout_seal` holds at most one row and `holdout_seal_member` at most a few hundred, read whole
-- on the rare occasions they are read at all. `holdout_access` is scanned in full to produce a
-- query count, which is the only thing anyone asks it. Every index here would cost a write to serve
-- no read -- 0008, 0010 and 0011 all declined the same index for the same reason, and it is stated
-- rather than left as an omission a later reader has to re-derive.
--
-- NO ROWS ARE SEEDED, and the distinction matters. 0011 backfilled because RECOMPUTING A QUANTITY
-- FROM EVIDENCE ALREADY STORED IS DERIVATION. A seal is not a derivation: it is a CHOICE about
-- which evidence will be allowed to decide something later, and manufacturing one during an upgrade
-- would make that choice on behalf of an operator who was never asked. A fresh install and an
-- upgraded one both start with no seal, and `tests/test_upgrade.py` asserts it.
