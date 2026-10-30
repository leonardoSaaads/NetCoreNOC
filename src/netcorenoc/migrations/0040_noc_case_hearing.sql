-- 0040: a case hears its alarms, and may wait while the NOC watches (ADR #474). Forward-only and
-- additive.
--
-- 1. `noc_case_alarm` — each live case's view of its situation's alarms as the case loop last read
--    them: which were active, and how many times each had raised (`raised`, the alarm's count) —
--    so a port that went down and up again between two looks is still heard. The loop compares
--    the Broker's alarms with it every half minute, and what changed is the case's news: an alarm
--    that **joined** the situation, one that **raised again** after it had cleared, one that
--    **cleared**. The news is an event on the case's feed,
--    and a pending case whose fault came back or grew returns to the NOC queue (`alarm`, below).
--    Until 0040 a pending case heard nothing: a cut fibre that failed again an hour after its
--    repair waited for the next update, and the NOC learnt of it from the customer.
--    The first read of a case is its baseline and says nothing.
--
-- 2. The cycle reason `alarm`: back in the queue because an alarm joined or raised again while the
--    case was pending.
--
-- 3. The pending status `pending_monitoring`: the fault is quiet, the NOC watches it for stability
--    before it resolves — after a flap, a restore or a repair. The round's agents had no status to
--    say it and chose a technician's.

CREATE TABLE noc_case_alarm (
    situation_id bigint  NOT NULL REFERENCES noc_case (situation_id),
    alarm_id     bigint  NOT NULL REFERENCES alarm (id) ON DELETE CASCADE,
    active       boolean NOT NULL,
    raised       integer NOT NULL CHECK (raised >= 0),
    PRIMARY KEY (situation_id, alarm_id)
);
CREATE INDEX idx_noc_case_alarm_alarm ON noc_case_alarm (alarm_id);

ALTER TABLE noc_case_cycle DROP CONSTRAINT noc_case_cycle_reason_check;
ALTER TABLE noc_case_cycle ADD CONSTRAINT noc_case_cycle_reason_check
    CHECK (reason IN ('opened', 'automation', 'customer_post', 'next_update', 'released',
                      'escalated', 'alarm'));

ALTER TABLE noc_case DROP CONSTRAINT noc_case_status_check;
ALTER TABLE noc_case ADD CONSTRAINT noc_case_status_check CHECK (status IN (
    'new', 'auto', 'queue', 'working', 'pending_customer', 'pending_fiber_provider',
    'pending_maintenance', 'pending_dispatch', 'pending_device_access', 'pending_connectivity',
    'pending_vendor', 'pending_site_access', 'pending_field_tech', 'pending_rca',
    'pending_monitoring', 'validate', 'resolved', 'closed'));
