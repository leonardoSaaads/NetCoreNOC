-- 0007: the write perimeter (v0.7.1). A security patch: it adds the schema support that F36 and
-- F37 need in order to be *correct today*, and it removes the rows that F37's missing existence
-- check already allowed in. It adds no feature.
--
-- Forward-only and additive, applying cleanly onto a populated v0.7.0 database (schema v6).
--
-- IT SEEDS NOTHING and it CHANGES NO BEHAVIOUR BY ITSELF. Two nullable columns, one unique index,
-- and two clean-up deletes. An appliance that applies this migration and runs the v0.7.0 code
-- would behave identically; the behaviour changes live in the application, are exactly three, and
-- are enumerated in `docs/scope/SCOPE-0.7.1.md` §2.
--
-- Explicitly NOT here (DECISIONS #71): a FOREIGN KEY on `label`. SQLite cannot add a constraint in
-- place, so it would require a create-new / copy / drop / rename table rebuild — the one class of
-- migration that can lose rows — against every operator's populated database, inside a security
-- patch. The application-level existence check plus the one-time orphan cleanup below close the
-- write primitive today; the durable structural fix is a ROADMAP line for a release that can carry
-- a rebuild properly.
--
-- The engine's trap path reads none of this: `feedback` is written only by the API's feedback
-- handler, and `label` only by the label handler (prime directive 4).

-- -- F36: attribution -----------------------------------------------------------------------
--
-- Who gave this feedback, and as what role. Without an author, "who degraded the matrices?" is
-- unanswerable: the v0.7.0 audit chain records the API *call* but not its *effect* on learned
-- state, and the two are separated by `Engine.apply_feedback`.
--
-- Nullable, and deliberately NOT backfilled. The information does not exist for rows written
-- before this migration, and inventing a plausible author would be worse than admitting none —
-- an audit column that is sometimes a guess is not an audit column. NULL means "written by
-- v0.7.0 or earlier, author unrecorded".
--
-- These two columns are here because F36/F39 need them to be correct today. They are NOT the
-- v0.8.0 feedback dataset, which is explicitly out of scope for this release (SCOPE-0.7.1 §3.1).
ALTER TABLE feedback ADD COLUMN principal_ref TEXT;
ALTER TABLE feedback ADD COLUMN role TEXT;

-- -- F36: idempotence per (situation, verdict) ----------------------------------------------
--
-- De-duplicate first, because the index below cannot be created over a table that already
-- violates it — and a populated v0.7.0 database very likely does, since v0.7.0 recorded one row
-- per post with no uniqueness at all.
--
-- The EARLIEST row by `created_at` is kept (ties broken by the lowest `id`, which is monotonic).
-- Keeping the earliest preserves *when the operator first said this*, which is the fact the
-- learned state was actually derived from; keeping the latest would rewrite that history.
-- Reads as: delete every row that has a strictly *earlier* sibling in its own
-- (situation_id, verdict) group, which leaves exactly one survivor per group — the earliest.
-- `id` breaks a `created_at` tie, and is monotonic, so the survivor is deterministic.
DELETE FROM feedback
WHERE EXISTS (
    SELECT 1 FROM feedback AS earlier
    WHERE earlier.situation_id = feedback.situation_id
      AND earlier.verdict = feedback.verdict
      AND (earlier.created_at < feedback.created_at
           OR (earlier.created_at = feedback.created_at AND earlier.id < feedback.id))
);

-- A situation has two possible verdicts, so its total influence on the learned state is now
-- bounded at two applications whatever anyone posts (DECISIONS #68). A CREATE UNIQUE INDEX rather
-- than a UNIQUE table constraint, because SQLite cannot add the latter in place and the former is
-- exactly as binding for INSERT … ON CONFLICT.
CREATE UNIQUE INDEX IF NOT EXISTS idx_feedback_situation_verdict
    ON feedback (situation_id, verdict);

-- -- F37: remove the orphans the missing existence check let in ------------------------------
--
-- `label` has no foreign key and `store.prune()` never touched it, so every `editor` held an
-- unbounded, never-reclaimed write primitive against the database file: a POST naming any integer
-- created a row. These deletes reclaim what that allowed; the application-level existence check
-- stops new ones.
--
-- Labels are cosmetic by construction (they are display strings joined LEFT into read models), so
-- deleting a row whose target does not exist can lose no information that anything could render.
DELETE FROM label
WHERE (kind = 'device' AND target_id NOT IN (SELECT id FROM device))
   OR (kind = 'class'  AND target_id NOT IN (SELECT id FROM alarm_class));
