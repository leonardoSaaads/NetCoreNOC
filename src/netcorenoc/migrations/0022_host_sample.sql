-- 0022: host samples on disk, and the two alarm indexes a time window needs (v0.22.0, ADR #380).
--
-- ## host_sample
--
-- The Overview's range control offered 15 min to 7 days and the host charts under it drew the same
-- two hours whatever was chosen: `engine/operate/resources.py` kept a 240-reading ring in process
-- memory and nothing persisted it, so 6 h and 12 h were windows the data could not serve and a
-- restart emptied even the two hours (F154). One row per 30-second reading, pruned at seven days —
-- the longest range the control offers — is 20 160 rows at most, six scalars each.
--
-- `queue_depth` is here too. The queue chart was derived in the browser from the update stream
-- ("this browser only"), so it covered however long the tab had been open and ignored the range
-- as well.
--
-- WITHOUT ROWID on the instant: the table is read only by range on `at` and written only at the
-- end, so the clustered key is the one index it needs.
CREATE TABLE host_sample (
    at          REAL PRIMARY KEY,
    cpu_pct     REAL,
    mem_pct     REAL,
    disk_pct    REAL,
    db_mb       REAL,
    queue_depth INTEGER
) WITHOUT ROWID;

-- ## Two indexes on alarm, for every read bounded by a time window
--
-- `timeline_buckets` counts raises by `first_seen` and clears by `cleared_at` inside a window, and
-- both were full scans of `alarm` (measured: `SCAN a` + a temp B-tree). The cost at ingest is one
-- index insert per NEW alarm row — a repeat of an existing alarm updates `last_seen`, which is
-- already indexed, and touches neither column — and one per clear. The clear index is partial:
-- most rows on a working appliance are active and have no clear instant to index.
CREATE INDEX idx_alarm_first_seen ON alarm (first_seen);
CREATE INDEX idx_alarm_cleared_at ON alarm (cleared_at) WHERE cleared_at IS NOT NULL;
