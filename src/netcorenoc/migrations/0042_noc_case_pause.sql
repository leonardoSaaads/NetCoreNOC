-- 0042: a pending case's restore clock waits, and access to a device wakes its case (ADR #478).
-- Forward-only and additive.
--
-- 1. `noc_case.paused_s` and `noc_case.paused_at` — the time the case has spent pending. While a
--    case waits on someone outside the NOC (the customer, a fibre provider, access to the device),
--    nothing the NOC does can restore the service, so the handbook's Time to Resolve/Restore
--    (0036) pauses, as it already stops at `validate`. `paused_at` is when the current wait began
--    (NULL when the case is not pending), `paused_s` the waits already over. Kept by a trigger on
--    the status, as `restored_at` is, so no path can forget it; the restore clock counts from the
--    opening, less `paused_s` and the wait in progress (`engine/noc/kpi.py`).
--
-- 2. The cycle reason `access`: back in the NOC queue because a custodian let the agents use a
--    credential for one of the case's devices while the case waited for access
--    (`pending_connectivity` or `pending_device_access`). Until 0042 such a case slept until its
--    next update, however soon the access came.

ALTER TABLE noc_case ADD COLUMN paused_s double precision NOT NULL DEFAULT 0
    CHECK (paused_s >= 0);
ALTER TABLE noc_case ADD COLUMN paused_at timestamptz;

CREATE FUNCTION noc_case_pause_clock() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.paused_at IS NOT NULL THEN
        NEW.paused_s := OLD.paused_s
            + GREATEST(0, EXTRACT(EPOCH FROM (NEW.status_at - OLD.paused_at)))::double precision;
        NEW.paused_at := NULL;
    END IF;
    IF NEW.status LIKE 'pending\_%' THEN
        NEW.paused_at := NEW.status_at;
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER noc_case_pause_clock BEFORE UPDATE OF status ON noc_case
    FOR EACH ROW WHEN (OLD.status IS DISTINCT FROM NEW.status)
    EXECUTE FUNCTION noc_case_pause_clock();

-- A case pending when this migration runs starts its pause now: its past wait is not known.
UPDATE noc_case SET paused_at = now() WHERE status LIKE 'pending\_%';

ALTER TABLE noc_case_cycle DROP CONSTRAINT noc_case_cycle_reason_check;
ALTER TABLE noc_case_cycle ADD CONSTRAINT noc_case_cycle_reason_check
    CHECK (reason IN ('opened', 'automation', 'customer_post', 'next_update', 'released',
                      'escalated', 'alarm', 'access'));
