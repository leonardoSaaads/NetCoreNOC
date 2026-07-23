-- 0004: state-based clear (S9, §5.5). Many platforms send one trap OID whose *state* varbind
-- carries both the raise and the clear (down/up, 2/1, active/cleared) rather than distinct
-- raise/clear OIDs. A varbind that strictly alternates between exactly two values for enough
-- full cycles on a (device, instance) is learned as that class's state field, and the value it
-- returns to (the second value seen) as the clear value. The class-level clear-pair learner
-- (edges of kind 'clear_pair') is untouched — this is purely additive, so cold-start grouping
-- is unchanged until a state field is actually learned. One row per (class, varbind OID).
CREATE TABLE state_clear (
    class_id    INTEGER NOT NULL,
    varbind_oid TEXT NOT NULL,
    clear_value TEXT NOT NULL,     -- the terminating (resolved) state value
    raise_value TEXT NOT NULL,     -- the value that opens the alarm
    learned_at  REAL NOT NULL,
    PRIMARY KEY (class_id, varbind_oid)
);
