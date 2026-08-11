-- 0011: the evidence boundary (v0.9.2). A quantity that describes the EVIDENCE is now derived by
-- the server; a quantity that describes the CLIENT may still be derived from the client; and the
-- place where the two meet is a named, stored, auditable act rather than an arithmetic accident.
--
-- Its design is `docs/architecture/EVIDENCE-BOUNDARY-0.9.2.md` and DECISIONS #131-#136; the two
-- defects it repairs are reproduced over HTTP, with a control on every probe, in
-- `docs/gates/v0.9.2-phase-0.md` §1 and §3.
--
-- Forward-only and additive, applying cleanly onto a populated v0.9.1 database (schema v10).
--
-- ---------------------------------------------------------------------------------------------
-- WHY THIS MIGRATION EXISTS, IN ONE MEASUREMENT.
--
-- `feedback.excluded_count` is `len(the client's list)`. Three consumers multiply it by
-- `member_count - excluded_count`. Nothing checked `m <= n`. Measured over HTTP as an ordinary
-- `editor`, against an honest corpus:
--
--     8 honest labels (9-member bags, 2 marked each)      total =     112
--     + ONE label: bag of 4, 600 ids sent (truncated 512) total = -259,984   delta = -260,096
--     + ONE label: bag of 60, 30 GHOST ids marked         total = -259,084   delta =    +900
--
-- The third line is the finding. -260,096 is loud and any `>= N` floor simply fails on it. +900 is
-- positive, plausible, of the right order of magnitude, and composed of ZERO true assertions: the
-- same label read through `Exclusion.marked_positions` -- the path `learn.penalize` actually uses --
-- resolves 0 marks and moves not one matrix cell.
--
-- THE ROOT CAUSE IS STRUCTURAL AND IT IS THIS MIGRATION'S PREDECESSOR'S. `feedback_member` carries
-- a `source` column precisely so trusted and untrusted rows stay distinguishable at every layer
-- downstream. `feedback_exclusion` carries none -- it did not need one, since every row in it is
-- the client's -- and the denormalized counter on the label row then inherited the client's trust
-- WITHOUT EVER SAYING SO. 0010 argued the separate-table decision well and argued the
-- denormalization well; what it did not do is carry the TRUST MARKER across the boundary it created.
-- ---------------------------------------------------------------------------------------------
--
-- WHAT DOES NOT CHANGE, AND IT IS MOST OF THE FILE'S POINT.
--
-- The client's report is still recorded VERBATIM, still BOUNDED, still NEVER REJECTED, and still
-- NEVER USED TO VALIDATE THE EXISTENCE OF ANYTHING. F34, F35, F37 and F39 are not being reversed.
-- `feedback_exclusion`, `feedback_member(source='client')`, `excluded_count` and
-- `excluded_truncated` keep their exact present semantics and their exact present bytes. An id the
-- principal cannot see, or that does not exist, is recorded exactly as reported and changes nothing
-- about the response, its status or its timing.
--
-- RECONCILIATION IS AN ADDITIONAL QUANTITY. It is never a replacement and never a filter on what is
-- stored. A release that started rejecting ids would have traded an evidence-quality problem for an
-- existence oracle, which is a strictly worse exchange and is refused in DECISIONS #131.
--
-- NO COLUMN CHANGES MEANING. `excluded_count` is RE-DOCUMENTED below, not altered: it is TIER 1 --
-- what the client reported, a measurement of the client, never of the evidence.

-- -- TIER 2: the reconciled count ---------------------------------------------------------------
--
--     excluded_reconciled = | the reported set INTERSECT the server's own bag |
--
-- Computed server-side at the instant of the verdict, from a bag the client cannot influence.
-- **This is the number every quantity about the evidence reads**, and it is EXACTLY
-- `len(Exclusion.marked_positions(bag))` -- the same value `learn.penalize` has always used. The
-- report and the learner therefore agree by construction rather than by coincidence, which is the
-- property that made `penalize` the one consumer that was correct all along.
--
-- DISTINCT BY ALARM ID, not by position. `feedback_exclusion`'s primary key is
-- (feedback_id, position), so a client may legitimately send [5, 5, 5]; three rows are recorded as
-- evidence, and they reconcile to ONE mark, because they are one member of the bag.
--
-- THE INVARIANT, ENFORCED HERE AND AT THREE LAYERS ABOVE:
--
--     0 <= excluded_reconciled <= member_count
--
-- `member_count IS NOT NULL` is stated EXPLICITLY inside the CHECK and is not an oversight of
-- brevity: SQLite treats a CHECK that evaluates to NULL as SATISFIED, so `x <= member_count` with a
-- NULL `member_count` would pass silently and a reconciled count could be written against a bag
-- whose size was never recorded. Determined by experiment in `docs/gates/v0.9.2-phase-0.md` §7,
-- which also established that ALTER TABLE ... ADD COLUMN carries an ENFORCED CHECK in SQLite 3.45,
-- on INSERT and on UPDATE alike.
--
-- THE CHECK IS NOT RETROACTIVE -- the same experiment, §7(e). A row written before this column
-- existed is never re-checked. So the constraint bounds the FUTURE and the maintenance drift check
-- bounds the PAST, and both are required; neither is sufficient alone.
ALTER TABLE feedback ADD COLUMN excluded_reconciled INTEGER
    CHECK (excluded_reconciled IS NULL OR (excluded_reconciled >= 0
           AND member_count IS NOT NULL AND excluded_reconciled <= member_count));

-- -- The provenance of that count, which is a DIFFERENT FACT from the count -----------------------
--
-- 'live'     -- written by v0.9.2+ at the moment of the verdict, against the bag as it then stood.
-- 'backfill' -- derived by THIS MIGRATION from `feedback_exclusion` and `feedback_member`.
-- NULL       -- no assertion was made (a plain split, or a confirm), so there is nothing to reconcile.
--
-- Two different acts, by two different actors, at two different times. A column that cannot tell
-- them apart is a column that will be misread -- and the test is DECISIONS #131's provenance
-- sentence, borrowed from W3C PROV as vocabulary only: *the entity X was attributed to the agent A
-- and generated by the activity Y*. A reconciled count with no recorded activity fails that
-- sentence, so the column exists.
--
-- THE VALUE SET IS DELIBERATELY NOT CONSTRAINED IN THE SCHEMA, and that is 0010's own reasoning
-- about `acquisition_channel` applied unchanged: a CHECK on the value would have to be rewritten by
-- every release that adds a source, and SQLite cannot alter one in place. It is enforced where it
-- can be revised -- in `store/feedback.py` -- and stated here.
ALTER TABLE feedback ADD COLUMN excluded_reconciled_source TEXT;

-- -- TIER 3: whether the assertion could have been made -------------------------------------------
--
--     excluded_reconciled_out_of_scope
--       = how many of the RECONCILED marks were about members the labeller could NOT observe
--
-- F47. A scoped editor may label any situation with at least one visible member, and may mark ids
-- they were never shown -- the redaction deliberately carries no NE id, address or entity key, so
-- the ids are guessable and the response is (correctly) indistinguishable either way. Measured:
--
--     GET  /api/situations/{sid}  as a scoped editor -> 2 of 4 alarms; redacted_members.count = 2
--     POST .../feedback {"verdict":"split","excluded_ids":[<a redacted member>]} -> 200
--       -> asserted_negative_pairs = 3, and all three involve an alarm the operator never saw.
--
-- ZERO IS A REAL AND COMMON ANSWER and must be distinguishable from "not recorded". An unrestricted
-- scope records 0 -- nothing was hidden, so no mark could have been blind -- and a restricted scope
-- whose every reconciled mark was about a visible member also records 0, which is a STRONG
-- statement. NULL means UNKNOWN, and it means it FOREVER.
--
-- THE CORPUS THEREFORE DIVIDES INTO THREE POPULATIONS A READER CAN NAME:
--
--     clean    scope_redacted_members = 0            -- no mark could have been about a hidden member
--     checked  restricted, and this column says how much of the assertion was blind
--     unknown  written before 0011, and permanently so
--
-- Reported SEPARATELY and NEVER AVERAGED, exactly as `acquisition_channel` is (DECISIONS #126). An
-- aggregate over the three would destroy the characterisation retroactively, for rows already
-- written, which is the failure 0010's channel column was created to prevent.
--
-- WHAT THIS DOES NOT FIX, stated here because the next reader will assume otherwise: it does not
-- stop a scoped editor from making a blind assertion. IT MAKES THE ASSERTION LEGIBLE. Preventing it
-- would mean rejecting client ids, which is refused above and for the same reason.
--
-- WHAT IT COVERS, AND WHAT IT DOES NOT: the MARKED side. A partial split asserts `marked x rest`,
-- and the REST may also contain redacted members -- a pair is unobservable if EITHER end is. The
-- surviving columns bound that without a second stored one: with n = member_count,
-- m = excluded_reconciled, h = scope_redacted_members and b = this column, the observable asserted
-- pairs lie in [(m-b) * (n-m-(h-b)), (m-b) * (n-m)], which on the measured case is [2, 2] -- exact.
-- EVIDENCE-BOUNDARY-0.9.2.md §10 carries the decision; an exact stored count is a ROADMAP line.
ALTER TABLE feedback ADD COLUMN excluded_reconciled_out_of_scope INTEGER
    CHECK (excluded_reconciled_out_of_scope IS NULL OR (excluded_reconciled_out_of_scope >= 0
           AND excluded_reconciled IS NOT NULL
           AND excluded_reconciled_out_of_scope <= excluded_reconciled));

-- -- THE BACKFILL, AND ITS EXACT BOUNDARY ----------------------------------------------------------
--
-- 0010 seeded nothing and was right to. This one seeds ONE column, and the difference is DECISIONS
-- #133's line:
--
--     RECOMPUTING A QUANTITY FROM EVIDENCE ALREADY STORED IS DERIVATION, AND MAY BE BACKFILLED.
--     INVENTING A QUANTITY WHOSE INPUT IS GONE IS FABRICATION, AND MUST BE NULL.
--
-- `feedback_exclusion` and `feedback_member(source='server')` are BOTH stored evidence that NO
-- retention path removes. Measured on one clock in `docs/gates/v0.9.2-phase-0.md` §4: a prune pass
-- that collects every `alarm` row of a closed situation -- and with them every `alarm.ne_id` --
-- leaves `feedback` (1 -> 1), `feedback_exclusion` (2 -> 2) and the server bag (4 -> 4) untouched,
-- and the reconciled count recomputes correctly AFTERWARDS. So this is derivation, and it is the
-- same justification 0008 used for `capture_provenance`.
--
-- NOTHING FROM TIER 3 IS SEEDED, AND THERE IS NO EXCEPTION. `alarm.ne_id` expires -- the same
-- measurement, the other half of it: `marked ids still resolvable to an NE` went 2 -> 0 in that
-- pass. And even on a database young enough that every `alarm.ne_id` survives, the SCOPE POLICY IN
-- EFFECT AT LABEL TIME would be a reconstruction of what the policy SAID rather than of what the
-- operator SAW, and the gap between those two is exactly what the column exists to record.
-- `excluded_reconciled_out_of_scope` is left NULL on every row this migration touches, and
-- `tests/test_upgrade.py` asserts it on a database written by real v0.9.1 code.
--
-- WHAT AN OPERATOR WILL SEE CHANGE. On any corpus written by the shipped UI: nothing numeric.
-- Gate 0 §6 counted the disagreement on the fullest corpus this repository can construct and found
-- ZERO rows where the reported count differs from the reconciled one, and ZERO rows with
-- `excluded_count > member_count`. The backfill will correct nothing; it records that nothing
-- needed correcting, which is what lets a later reader tell "reconciled and equal" from "never
-- reconciled". MIGRATION.md says this in the operator's own terms.
--
-- COUNT(DISTINCT x.alarm_id), not COUNT(*): see the tier-2 comment above. A client that sent one id
-- three times has three evidence rows and ONE reconciled mark.
--
-- The WHERE clause is the same predicate the reports use to decide a row carries an assertion, and
-- it also guarantees `member_count IS NOT NULL`, which the CHECK above requires. A pre-v0.8.0 row
-- (no `member_count`) and a plain split (no `excluded_count`) are both left entirely alone.
UPDATE feedback
   SET excluded_reconciled = (
           SELECT COUNT(DISTINCT x.alarm_id)
             FROM feedback_exclusion x
             JOIN feedback_member m
               ON m.feedback_id = x.feedback_id
              AND m.alarm_id = x.alarm_id
              AND m.source = 'server'
            WHERE x.feedback_id = feedback.id
       ),
       excluded_reconciled_source = 'backfill'
 WHERE excluded_count IS NOT NULL
   AND member_count IS NOT NULL;

-- -- An index? No. ---------------------------------------------------------------------------------
--
-- Every consumer of these columns is an aggregate over the whole `feedback` table -- the bias
-- report, the shadow report, and the maintenance drift check -- so each is a full scan by nature
-- and an index would cost writes to serve no read. 0008 and 0010 both declined the same index for
-- the same reason; stated rather than left as an omission a later reader has to re-derive.
