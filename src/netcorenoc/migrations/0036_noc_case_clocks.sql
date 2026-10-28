-- 0036: a case's priority follows its alarms, its take clock follows its priority, and the
-- handbook's restore clock (ADR #464). Forward-only and additive.
--
-- 1. `noc_case.priority_by` — who set the priority, or NULL while it is the appliance's: the worst
--    effective severity among the situation's members (declared, then the catalogue's rule, then
--    the trap's own word or the learned field — the precedence the Broker shows). Until 0036 the
--    priority came from `situation.severity`, which only autonomy writes, so a link-down the
--    Broker showed as critical opened a minor case. While NULL, the case loop raises the priority
--    when a worse alarm joins; it never lowers it — that is a person's call, and once a person
--    sets it the appliance stops deciding.
--
-- 2. The open cycle's priority is updated with the case's (the store does it): the take target is
--    the priority in force while the clock runs, so a case raised to critical is late at 25
--    minutes, not at the two hours of the minor it opened as. A cycle already taken keeps the
--    priority it was taken under.
--
-- 3. `noc_case.restored_at` — when the case first reached `validate` or `resolved`: the stop of the
--    handbook's Time to Resolve/Restore (4 h critical, 24 h major, 72 h minor, from the event). Kept
--    by a trigger on the status, so no path can forget it; a case taken back from `validate` is not
--    restored after all, and the clock runs again.

ALTER TABLE noc_case ADD COLUMN priority_by text COLLATE "C";
ALTER TABLE noc_case ADD COLUMN restored_at timestamptz;

CREATE FUNCTION noc_case_restore_clock() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status IN ('validate', 'resolved', 'closed') THEN
        NEW.restored_at := COALESCE(NEW.restored_at, NEW.status_at);
    ELSE
        NEW.restored_at := NULL;
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER noc_case_restore_clock BEFORE UPDATE OF status ON noc_case
    FOR EACH ROW WHEN (OLD.status IS DISTINCT FROM NEW.status)
    EXECUTE FUNCTION noc_case_restore_clock();

UPDATE noc_case SET restored_at = COALESCE(resolved_at, status_at)
WHERE status IN ('validate', 'resolved', 'closed');
