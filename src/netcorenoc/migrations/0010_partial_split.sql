-- 0010: the partial split (v0.9.1). The operator marks WHICH members do not belong, and the
-- assertion is recorded exactly as made. Its design is `docs/scope/SCOPE-0.9.1.md` and
-- DECISIONS #123-#124; the defect it repairs is demonstrated by query and by arithmetic in
-- `docs/gates/v0.9.1-phase-0.md` §1.
--
-- Forward-only and additive, applying cleanly onto a populated v0.9.0 database (schema v9).
--
-- IT SEEDS NOTHING, and that is a release gate. A migrated appliance holds one empty table and
-- three NULL columns, and behaves at first boot exactly as it did before the upgrade: every label
-- already in the corpus stays readable as what it is — a PLAIN split, which asserts that the bag is
-- at least two situations and says nothing about any pair. There is no backfill here at all, not
-- even the marker one that 0008 permitted itself: 0008 could write `capture_provenance` because the
-- fact was knowable from the row's mere existence at migration time. Nothing about WHICH members an
-- operator would have marked is knowable from anything, and inventing it would be exactly the
-- fabrication this release exists to refuse.
--
-- ---------------------------------------------------------------------------------------------
-- THE INVARIANT THIS MIGRATION EXPRESSES STRUCTURALLY:
--
--     A PARTIAL SPLIT ASSERTS `marked x rest` AND NOTHING ELSE.
--
-- The pairs WITHIN the remainder and the pairs WITHIN the marked set are UNKNOWN — not negative,
-- not positive, unknown. There is deliberately no column in which either could be recorded as an
-- inference, because an inference stored beside an observation is indistinguishable from one at
-- every layer downstream, and that is what v0.8.0 §3.3 refused and what policy A in v0.9.0
-- measured the cost of.
--
-- The arithmetic closes exactly, which is what makes "and nothing else" CHECKABLE rather than
-- merely promised. Bag of n, m marked, r = n - m remaining:
--
--     asserted negative   m * r
--     unasserted          r(r-1)/2  within the remainder
--                       + m(m-1)/2  within the marked set
--     -------------------------------------------------
--     total               n(n-1)/2  = every pair of the bag, and nothing is unaccounted for
--
-- At n = 9, m = 2: 14 asserted, 22 unasserted, 36 total. `tests/test_learn.py` asserts the
-- identity rather than trusting it.
-- ---------------------------------------------------------------------------------------------
--
-- A PARTIAL SPLIT IS STILL A `split`. No verdict value is added and none is changed. Every reader
-- that understands `split` today stays correct, `split_bag_intact_rate` keeps its meaning, and the
-- exclusion is EXTRA EVIDENCE ATTACHED TO A SPLIT rather than a fourth thing every downstream
-- consumer must learn (DECISIONS #124).
--
-- SECURITY POSTURE. The marked ids are CLIENT-SUPPLIED, on a write path that has already produced
-- F34, F35 and F39. They get the treatment `FEEDBACK-DATASET-0.8-DRAFT.md` §5.4b gave the client
-- fingerprint, unchanged and for the same reasons: BOUNDED, NEVER REJECTED, and NEVER USED TO
-- VALIDATE THE EXISTENCE OF ANYTHING. An id the principal cannot see, or that does not exist, is
-- recorded exactly as reported and changes nothing about the response, its status or its timing.
-- That path does not get to produce a fourth finding.

-- -- The marked members --------------------------------------------------------------------------
--
-- A CHILD TABLE rather than a column, for the reason DECISIONS #104 gave for `feedback_member`: it
-- is a LIST, and a digest proves that something changed without saying what it was. It is a
-- SEPARATE table from `feedback_member` rather than a third `source` value there, and that is a
-- semantic decision before it is a mechanical one: `feedback_member` records WHAT THE BAG WAS, and
-- this records WHAT THE OPERATOR ASSERTED ABOUT IT. They answer different questions, they have
-- different trust (`feedback_member.source='server'` is authoritative; every row here is the
-- client's report), and only one of them survives a merge as a statement of fact.
-- (SQLite could not have added a third value to that CHECK constraint in place in any case.)
--
-- ORDERED, like the bag is: the position is part of what the client reported and cannot be
-- recomputed afterwards.
--
-- IT MAY LEGITIMATELY HOLD ZERO ROWS FOR A FEEDBACK ID, and the absence means "the operator marked
-- nothing", which is a PLAIN split — never a guess, and never confused with "the operator marked
-- everything". `feedback.excluded_count` is NULL in that case rather than 0, so the two states are
-- distinguishable in the label row alone, without a join.
CREATE TABLE feedback_exclusion (
    feedback_id INTEGER NOT NULL REFERENCES feedback(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    alarm_id    INTEGER NOT NULL,
    PRIMARY KEY (feedback_id, position)
) WITHOUT ROWID;

-- -- What the label row gains --------------------------------------------------------------------
--
-- All nullable, none backfilled. The quantities that make the assertion interpretable later
-- WITHOUT re-reading the child table — a report that had to join to count would not stay cheap, and
-- a corpus whose exclusions were pruned would lose the count as well as the members.
--
-- `member_count` — the bag size at label time — is ALREADY PRESENT from 0008 and is the other half
-- of every ratio here: marking eight of nine is a different assertion from marking one of nine, and
-- neither number means anything without the other.

-- How many members the operator marked. NULL means NO EXCLUSION WAS ASSERTED (a plain split, or a
-- confirm); 0 cannot occur, because an empty marked set is recorded as no assertion rather than as
-- an assertion about nothing.
ALTER TABLE feedback ADD COLUMN excluded_count INTEGER;

-- Whether the report was truncated at the bound. Truncation must be a FACT rather than a silence —
-- the same rule `client_truncated` follows, because a silence would make the bound itself invisible
-- in the data, and a partial split whose marked set was clipped asserts FEWER negatives than the
-- operator made. A reader must be able to tell that from the row.
ALTER TABLE feedback ADD COLUMN excluded_truncated INTEGER;

-- THE REMAINDER ASSERTION, AND ITS THIRD STATE. 1 = the operator asserted the remainder holds
-- together; 0 = the operator asserted it does not; NULL = THE OPERATOR DID NOT SAY.
--
-- NULL is the interesting value and it is the whole reason this is a column rather than an
-- inference. Excluding two members from nine says nothing whatever about the other seven, and a
-- release that treated the exclusion as implying "the rest belong together" would be manufacturing
-- the stronger claim to save a click. The shipped UI does not offer it at all (DECISIONS #127),
-- because a control for it is a SECOND GESTURE and §4.1 forbids buying the stronger claim that way
-- — so every row this release writes has NULL here, and the report says that rather than printing a
-- rate over an affordance nobody was given.
ALTER TABLE feedback ADD COLUMN remainder_together INTEGER;

-- -- The acquisition channel, which already exists and is now actually used --------------------
--
-- No schema change: `feedback.acquisition_channel` was added by 0008 for precisely this, and Gate 0
-- §4 confirmed all 41 rows of the fullest corpus available read 'organic' before relying on it.
-- From v0.9.1 a verdict recorded through `POST /api/situations/{sid}/close` writes 'close' instead
-- (DECISIONS #126), because closing selects for RESOLVED incidents and that is a different
-- population from the one an operator browses and labels spontaneously. Two channels, reported
-- separately, never averaged.
--
-- Stated here and not enforced here on purpose: a CHECK constraint on the value would have to be
-- rewritten by every release that adds a channel, and SQLite cannot alter one in place.
