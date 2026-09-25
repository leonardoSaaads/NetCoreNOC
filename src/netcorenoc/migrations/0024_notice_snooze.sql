-- 0024: per-user warning snoozes, and the lifetime of the "outlived a window" marker (v0.22.0).
--
-- ## notice_snooze (item 1, ADR #387)
--
-- The bell held the same two security warnings forever and the maintainer asked for an ×. A
-- permanently dismissible security warning is how appliances ship insecure, so this is a SNOOZE:
--
--   * **per user** — one operator silencing a warning for everybody is a different decision;
--   * **for an interval the operator chooses** — 24 h or 7 days, and for a warning that is not
--     security posture also "until it changes"; a security snooze always expires;
--   * **keyed on the warning's text** (a digest of it), so a warning whose text changes — a count
--     that moved, a condition that got worse — comes back without anyone asking;
--   * **audited** with the actor, like every other operator action;
--   * and it hides nothing it cannot: a warning whose condition is fixed stops being emitted on the
--     next poll whether or not it was snoozed, and a snoozed one stays counted on the bell.
CREATE TABLE notice_snooze (
    user_id    INTEGER NOT NULL REFERENCES user (id) ON DELETE CASCADE,
    digest     TEXT    NOT NULL,
    -- The warning as it read when snoozed, so the list of what is snoozed can say what it was.
    text       TEXT    NOT NULL,
    security   INTEGER NOT NULL DEFAULT 0,
    -- '24h' | '7d' | 'change'. 'change' is refused for a security warning by the API.
    mode       TEXT    NOT NULL,
    -- When it returns. NULL only for 'change', which returns when the text does.
    until      REAL,
    created_at REAL    NOT NULL,
    PRIMARY KEY (user_id, digest)
);

-- ## The "outlived a maintenance window" marker (item 8, ADR #388)
--
-- `alarm.surfaced_from_window_id` (0021) was set when a window's ledger surfaced a fault and never
-- cleared, so the console said "raised during maintenance, still active" on that alarm after it had
-- cleared, and after the element had re-reported it itself. The marker's lifetime is now read from
-- three facts: the alarm is active, it has not been seen since it was surfaced, and nobody has
-- acknowledged it. The first is `status`; these are the other two.
ALTER TABLE alarm ADD COLUMN surfaced_at REAL;
ALTER TABLE alarm ADD COLUMN surfaced_ack_at REAL;
ALTER TABLE alarm ADD COLUMN surfaced_ack_by TEXT;
-- Every alarm surfaced before this migration was surfaced at its `last_seen` (the sweep wrote both
-- in one statement), so that is its surfacing instant.
UPDATE alarm SET surfaced_at = last_seen WHERE surfaced_from_window_id IS NOT NULL;
