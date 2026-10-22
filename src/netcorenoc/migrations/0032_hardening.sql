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
--
-- 4. The audit chain is keyed. `entry_hash` is a plain SHA-256 chain: whoever may write the table
--    as its owner (or a superuser, or a restored and edited dump) can rewrite history and
--    recompute every hash after it. `mac` is an HMAC of each new row's `entry_hash` under the
--    appliance's secret key, which the database never holds, so a rewritten row cannot carry a
--    valid one. Rows written before this release have none and stay verifiable by hash alone.
--
-- 5. Counters that grow with traffic are 64-bit. An alarm repeating a hundred times a second
--    reached 2^31 in eight months, and the write that crossed it failed the whole batch.
--
-- 6. A quarantined packet's hash and prefix are of the stored, community-blanked bytes. The hash
--    of the original beside the blanked payload tested any guessed community offline, and the
--    original's first eight bytes carry a community's first characters (F4). Rows already kept
--    are rewritten; a packet whose community could not be located keeps four bytes, not eight.
--
-- 7. The trigger functions resolve nothing through the caller's `search_path`.
--
-- 8. Indexes, from measuring the read paths against a year of history (511k alarms, 2M pairs):
--    alarms still uncleared by when they began (the activity views' "active at" OR could use no
--    index: 50 ms -> 3 ms); the peer side of a situation's history (a sequential scan on every
--    detail view: 14 ms -> 0.1 ms); and two that cost more than they serve — the GIN index on
--    `alarm.varbinds`, which no query reads and every repeat trap rewrites, and the full
--    `(alarm_b, alarm_a)` pair index, whose every reader is in the dataset tier (90 MB -> 0.3 MB).

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

ALTER TABLE audit_log ADD COLUMN mac text COLLATE "C";

ALTER TABLE alarm ALTER COLUMN count TYPE bigint;
ALTER TABLE episode_pair ALTER COLUMN count TYPE bigint;
ALTER TABLE varbind_profile
    ALTER COLUMN n_obs TYPE bigint,
    ALTER COLUMN n_repeat TYPE bigint,
    ALTER COLUMN n_monotonic TYPE bigint,
    ALTER COLUMN n_numeric TYPE bigint,
    ALTER COLUMN n_distinct TYPE bigint;

UPDATE quarantine SET sha256 = encode(sha256(raw), 'hex'), first8 = encode(substr(raw, 1, 8), 'hex')
    WHERE sanitized;
UPDATE quarantine SET first8 = left(first8, 8) WHERE NOT sanitized;

ALTER FUNCTION forbid_change() SET search_path = pg_catalog, pg_temp;
ALTER FUNCTION forbid_truncate() SET search_path = pg_catalog, pg_temp;

CREATE INDEX idx_alarm_uncleared_first ON alarm (first_seen) WHERE cleared_at IS NULL;
CREATE INDEX idx_situation_event_peer ON situation_event (peer_situation_id)
    WHERE peer_situation_id IS NOT NULL;
DROP INDEX idx_alarm_varbinds;
DROP INDEX idx_pair_alarm_ba;
CREATE INDEX idx_pair_alarm_ba ON dataset_pair (alarm_b, alarm_a) WHERE lifecycle = 'dataset';
