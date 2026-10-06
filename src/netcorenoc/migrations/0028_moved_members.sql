-- 0028: one move of several alarms is one gesture, and two more host series (v0.29.0). Forward-only
-- and additive, applying cleanly onto a populated v0.28.x database (schema 27). No table is rebuilt.
--
-- Until this release the console moved N ticked alarms as N single-alarm moves, and each one
-- asserted *"this alarm does not belong with the rest"* against a "rest" that still held the other
-- N-1 alarms the operator was moving WITH it. So moving twenty alarms together wrote negative
-- evidence about pairs the operator had just kept together, twenty history rows, and — when the
-- twenty were every member — an empty situation that stayed on the board.
--
-- A move of several alarms is now ONE `situation_event` of kind `move` with `alarm_id` NULL, and
-- the moved alarms are listed here. Its two halves are the single move's, applied to the set:
--   NEGATIVE: the moved set against the members it left (one `split` label carrying the set);
--   POSITIVE: each moved alarm against the members it joined (`gesture_positive_pairs`, which
--             reads this table beside `situation_event.alarm_id`).
-- Pairs inside the moved set are asserted neither way, which is DECISIONS #124's reading of a
-- marked split. A single-alarm move is unchanged: `alarm_id` set, nothing written here.
--
-- KEYS ARE NOT FEATURES (`0008`): `alarm_id` is present for a join and is never read as a value.
CREATE TABLE IF NOT EXISTS situation_event_moved (
    event_id INTEGER NOT NULL REFERENCES situation_event (id) ON DELETE CASCADE,
    alarm_id INTEGER NOT NULL,
    PRIMARY KEY (event_id, alarm_id)
) WITHOUT ROWID;

-- -- Two more host series: is the appliance keeping up (ADR #437) -----------------------------------
--
-- The Overview's "Is the appliance keeping up" drew CPU, memory, storage, database size and queue
-- depth — what the HOST is doing — and nothing about the WORK: how many traps arrive and how long
-- a batch takes to correlate. Those two answer the question in its own terms: a rising rate with a
-- flat latency is a busy network handled; a rising latency is the appliance falling behind before
-- the queue shows it. Sampled with the others, every 30 s; NULL before the first interval and on
-- every row written before this migration, never a made-up zero.
ALTER TABLE host_sample ADD COLUMN traps_per_s REAL;
ALTER TABLE host_sample ADD COLUMN latency_ms REAL;
