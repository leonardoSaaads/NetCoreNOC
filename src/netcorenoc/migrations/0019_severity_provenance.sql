-- 0019: where a placed severity came from (v0.21.0, D5). **The prerequisite, and it ships first.**
--
-- D4's severity rule — *"on host A, collect only critical alarms"* — is evaluated on the INGEST
-- path, against the severity the appliance has placed on the trap in hand. At v0.20.0 that
-- severity is NULL for **every** alarm, so the rule would have admitted nothing on any estate and
-- looked like it worked. D5 is therefore not a nicety beside the window feature; it is the thing
-- without which the window feature is a no-op.
--
-- WHAT WAS ACTUALLY WRONG, measured on 14 cut/repair cycles of the lab (HANDOFF §2):
--
--   alarms whose trap carried an X.733 severity word   29 / 30
--   alarm rows with a severity placed on them           0 / 30
--   census provenance                                   {standard: 10, learned: 0, declared: 0}
--
-- v0.17.1 taught the appliance to read the X.733 perceived-severity word a trap carries — but it
-- taught it to `store/read_models.py`, on the CENSUS read. The `alarm.severity` column itself is
-- still written only from a LEARNED severity field, which needs 200 observations of the varbind
-- and 50 closed alarms to confirm. So the Overview's numbers were right and every alarm ROW was
-- unplaced, and nothing on the ingest path could tell how serious anything was.
--
-- This column makes the placement a property of the row rather than of one read model:
--
--   'standard'  the trap carried a word in the X.733 vocabulary and the appliance believed it.
--               Not an inference and not a claim about a vendor — the device's own statement about
--               its own alarm, in the vocabulary the ITU standardised (ITU-T Rec. X.733, carried
--               into SNMP by RFC 3877's ALARM-MIB; cited in `known_oids.BUNDLED_SOURCES`).
--   'learned'   the NE has a confirmed severity varbind and this trap carried a value on it.
--   NULL        UNPLACED, and it stays a first-class state. A trap that says nothing about how
--               serious it is gets no severity invented for it (prime directive 2). What was a
--               defect was 100 %, not the category.
--
-- `declared` is deliberately NOT a value here. An operator's declaration is a row of `label`
-- against an alarm CLASS, it outranks both of these (#338), and one row of it moves every active
-- alarm of that class at once — so writing it onto individual alarm rows would denormalise a
-- declaration that is revocable in one gesture.
ALTER TABLE alarm ADD COLUMN severity_source TEXT;

-- Rows that already carry a severity are attributed to `learned`, and that is a statement about
-- the code that wrote them rather than a guess: **before this migration the learned path was the
-- only writer of `alarm.severity` that has ever existed.** Rows with no severity are left NULL,
-- which is what they are — unplaced. Nothing is invented and nothing is re-derived from stored
-- varbinds, because re-reading them would be this release deciding what an earlier one saw.
UPDATE alarm SET severity_source = 'learned' WHERE severity IS NOT NULL;

-- The census groups by this column. The ingest path never reads it — it writes it.
CREATE INDEX IF NOT EXISTS idx_alarm_severity_source ON alarm (severity_source)
    WHERE severity_source IS NOT NULL;

-- -- withdrawing the decision that made the standard column unreadable (II.1c) -----------------
--
-- MEASURED on the same 14 cycles, and it is the brief's own finding reproduced three releases on:
--
--   varbind_profile: ne_id=1  oid=1.3.6.1.2.1.118.1.2.2.1.4  role='entity'  n_obs=230  n_distinct=2
--
-- The RFC 3877 perceived-severity column was typed as the **entity discriminator**. It wins
-- because `ENTITY_PROMOTE_OBS = 200` and the severity column is the most-observed varbind on any
-- NE — every trap carries it — so it crosses the observation floor first and, on that lab, was the
-- ONLY candidate to cross it. It is the lowest-scoring candidate of the five and it won
-- uncontested.
--
-- The consequence is worse than a mis-typed column, and it is why this migration touches the
-- learned state at all: `_resolve_entity` makes the finest chain value the **dedup instance**, so
-- from the promotion onward every alarm on that NE was keyed on its severity word. Six ONUs' loss
-- of signal collapsed into one row called `major`, and the appliance created four network entities
-- named `major`, `minor`, `critical` and `cleared`.
--
-- `varbind_profile.py` now refuses to promote a varbind whose values are the X.733 vocabulary, so
-- this cannot happen again. That fix governs NEW evidence; an appliance upgrading carries the old
-- decision in this table and would reload it at the next start. So the decision is **withdrawn**,
-- exactly as `reset_entity` withdraws one as admin recourse: the role is cleared, future alarms
-- attribute to level 0, and the next sweep re-decides from current evidence — which now cannot
-- choose this column.
--
-- HISTORY IS UNTOUCHED, which is the forward-only discipline every learned decision in this
-- appliance follows. The `entity` rows named after severity words are NOT deleted and the alarms
-- attributed to them are NOT re-keyed: those alarms really were recorded that way, and rewriting
-- them would make the database say something that did not happen. They age out through the
-- ordinary retention prune.
UPDATE varbind_profile SET role = NULL
WHERE role = 'entity'
  AND varbind_oid IN (
      SELECT DISTINCT key_source FROM entity
      WHERE level > 0
        AND key_source IS NOT NULL
      GROUP BY key_source
      -- Every promoted key under this varbind is an X.733 token. ALL of them, not merely one:
      -- a discriminator that happens to take the value `minor` for one entity among two hundred
      -- is a real discriminator with an unfortunate value, and withdrawing it would be this
      -- migration guessing. `HAVING COUNT(*) = COUNT(... IN vocabulary)` is that "all" written in
      -- SQL, and it is why the six-token list is repeated here rather than referenced: a migration
      -- runs against a schema, not against a Python module.
      HAVING COUNT(*) = SUM(
          CASE WHEN lower(trim(key)) IN
              ('critical', 'major', 'minor', 'warning', 'indeterminate', 'cleared')
          THEN 1 ELSE 0 END
      )
  );
