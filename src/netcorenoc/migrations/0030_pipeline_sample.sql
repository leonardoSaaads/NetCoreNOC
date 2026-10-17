-- 0030: the trap pipeline, sampled with the host (ADRs #450, #451). Forward-only and additive,
-- applying cleanly onto a populated v0.30.x database (schema 29). No table is rebuilt.
--
-- The Overview's queue chart sampled `engine.queue.qsize()` once every 30 s. A storm that filled
-- the queue and drained it between two samples drew a flat zero, and one that straddled a sample
-- drew a single spike of whatever the instant held: the chart answered "how many traps were
-- waiting at the stroke of each half minute", which nobody asked. And with the spool (#450) the
-- in-memory queue is no longer the backlog at all — most of a storm waits on disk.
--
-- So, from this migration, two existing columns change what they hold, and five are added. Every
-- value is about the INTERVAL since the previous sample, never an instant:
--
--   queue_depth       the BACKLOG'S PEAK in the interval: traps written to the spool and not yet
--                     classified, whether on disk, on the queue or in the batch (was: qsize at the
--                     sampling instant)
--   latency_ms        the p95 DECISION LATENCY of the traps classified in the interval, arrival to
--                     commit (was: the p95 of the last 4 096 traps, whenever they were classified)
--   classified        traps classified in the interval
--   classified_per_s  the same, per second: the throughput OUT, beside `traps_per_s` IN
--   latency_max_ms    the slowest decision in the interval
--   over_target       traps classified later than the KPI target in force at the time
--   over_limit        traps classified later than the KPI limit in force at the time
--   spool_mb          the spool's size on disk at the sample
--
-- A NULL is "not measured" — an interval with no trap has no latency, and every row written before
-- this migration has none of the new columns — never a made-up zero (DECISIONS #289).
ALTER TABLE host_sample ADD COLUMN classified INTEGER;
ALTER TABLE host_sample ADD COLUMN classified_per_s REAL;
ALTER TABLE host_sample ADD COLUMN latency_max_ms REAL;
ALTER TABLE host_sample ADD COLUMN over_target INTEGER;
ALTER TABLE host_sample ADD COLUMN over_limit INTEGER;
ALTER TABLE host_sample ADD COLUMN spool_mb REAL;
