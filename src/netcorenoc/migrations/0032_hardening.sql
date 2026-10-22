-- 0032: what a breach or an injection may not do (ADR #453). Forward-only and additive.
--
-- 1. TRUNCATE. `forbid_change()` fires per row, and TRUNCATE fires no row trigger: the twelve
--    append-only tables could be emptied by one statement. A statement-level trigger refuses it.
--
-- 2. Pruning the audit log is a function, not a privilege. The appliance's runtime role may be a
--    role that does not own the schema (`NETCORENOC_DATABASE_OWNER_URL`), and then it holds no
--    DELETE on `audit_log` at all: `prune_audit_log` runs as the owner (SECURITY DEFINER), deletes
--    only rows older than its cutoff, and refuses a cutoff less than a day old, so an injected
--    call cannot erase the recent past. One role owning everything gets the same function.
--
-- 3. Recovery addresses are sealed (crosscutting/vault.py), so the lookup a reset request needs —
--    an address, case-insensitively — reads `email_index`, a keyed digest, instead of the text.
--    The appliance seals what is still plain on its next start; this file cannot, the key is not
--    the database's to hold.

CREATE FUNCTION forbid_truncate() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation', DETAIL = 'TRUNCATE would remove every row';
END
$$;

CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_truncate();
CREATE TRIGGER autonomy_setting_no_truncate BEFORE TRUNCATE ON autonomy_setting
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_truncate();
CREATE TRIGGER decider_setting_no_truncate BEFORE TRUNCATE ON decider_setting
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_truncate();
CREATE TRIGGER evaluation_fold_no_truncate BEFORE TRUNCATE ON evaluation_fold
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_truncate();
CREATE TRIGGER governance_policy_no_truncate BEFORE TRUNCATE ON governance_policy
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_truncate();
CREATE TRIGGER holdout_access_no_truncate BEFORE TRUNCATE ON holdout_access
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_truncate();
CREATE TRIGGER holdout_seal_no_truncate BEFORE TRUNCATE ON holdout_seal
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_truncate();
CREATE TRIGGER holdout_seal_member_no_truncate BEFORE TRUNCATE ON holdout_seal_member
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_truncate();
CREATE TRIGGER league_decision_no_truncate BEFORE TRUNCATE ON league_decision
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_truncate();
CREATE TRIGGER model_version_no_truncate BEFORE TRUNCATE ON model_version
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_truncate();
CREATE TRIGGER promotion_no_truncate BEFORE TRUNCATE ON promotion
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_truncate();
CREATE TRIGGER scorer_config_no_truncate BEFORE TRUNCATE ON scorer_config
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_truncate();

-- `search_path` is fixed at creation (the schema this runs in, then pg_temp last), so a role
-- calling it cannot redirect `audit_log` to a table of its own.
CREATE FUNCTION prune_audit_log(cutoff double precision) RETURNS bigint
    LANGUAGE plpgsql SECURITY DEFINER AS $$
DECLARE
    removed bigint;
BEGIN
    IF cutoff > date_part('epoch', now()) - 86400 THEN
        RAISE EXCEPTION 'audit rows less than a day old are never pruned'
            USING ERRCODE = 'restrict_violation';
    END IF;
    PERFORM set_config('netcorenoc.prune', 'audit_log', true);
    DELETE FROM audit_log WHERE ts < cutoff;
    GET DIAGNOSTICS removed = ROW_COUNT;
    PERFORM set_config('netcorenoc.prune', '', true);
    RETURN removed;
END
$$;
DO $$ BEGIN
    EXECUTE format('ALTER FUNCTION prune_audit_log(double precision) SET search_path = %I, pg_temp',
                   current_schema());
END $$;
REVOKE ALL ON FUNCTION prune_audit_log(double precision) FROM PUBLIC;

ALTER TABLE app_user ADD COLUMN email_index text COLLATE "C";
CREATE INDEX idx_app_user_email_index ON app_user (email_index) WHERE email_index IS NOT NULL;
