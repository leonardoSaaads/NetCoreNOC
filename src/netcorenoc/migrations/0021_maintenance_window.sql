-- 0021: the maintenance window (v0.21.0). The release's schema deliverable, additive and
-- forward-only, applying cleanly onto a populated v0.20.0 database (schema v20).
--
-- IT SEEDS NO WINDOW. Every table below is empty after this script runs on any database, and a
-- migrated appliance behaves at first boot exactly as it did before the upgrade: nothing is
-- suppressed until an operator or an agent declares a window.
--
-- ---------------------------------------------------------------------------------------------
-- `0008_feedback_dataset.sql`'s TWO RULES govern every column below, restated on this schema:
--
--   1. STORE WHAT CANNOT BE RECOMPUTED; DERIVE WHAT CAN. `status` is stored because it is the
--      result of a human gesture (`confirm`, `cancel`, `end now`) as often as of the clock, and a
--      status derived from timestamps alone could not distinguish "ended because the clock passed
--      it" from "ended because an engineer finished early" — which are different facts an operator
--      acts on differently. Everything the clock alone decides — *"starts in 2 h 15 min"*, the
--      timeline bands — is derived at read time and stored nowhere.
--
--   2. KEYS ARE NOT FEATURES. `ne_id`, `class_id` and `device_id` below are present FOR A JOIN.
--      Nothing in this release feeds one to a model, and the ledger is walled off from the dataset
--      entirely — see the banner on `maintenance_ledger`.
-- ---------------------------------------------------------------------------------------------
--
-- SECURITY POSTURE. A window is written on the HTTP write path and carries its actor, so it is
-- attributable in the way the `0008` tables are not. Its **existence** is readable by every role
-- (prime directive 4: a host that goes quiet with no marker reads as healthy, which is how
-- maintenance windows hide outages); its **details** follow `visibility`. That split is enforced
-- in the QUERY, never in the render — see `store/mw_reads.py`, and the injection in
-- `tests/test_maintenance_api.py` that runs it red.

-- -- the window -----------------------------------------------------------------------------
--
-- STATUSES, and there are six because an operator does something different about each:
--
--   pending_confirmation  over six hours and nobody has confirmed it yet (D6). It is NOT in
--                         force: alarms keep flowing, which is the safe failure.
--   scheduled             confirmed (or never needing it) and not yet started.
--   active                in force right now. The only status that suppresses anything.
--   ended                 over — by the clock, or because an engineer pressed "end now".
--   cancelled             called off before it ran. A STATE, not a delete: the audit row and the
--                         record of what was planned both survive (Part III).
--   expired               its start arrived while it was still `pending_confirmation`, so it
--                         never took effect. Visible, so an operator learns that the work they
--                         planned was not covered (II.7).
--
-- No CHECK constraint, for the reason `0014` gives about `situation.status`: adding one to a
-- column needs a table rebuild, and the application has one write site. The values are documented
-- here and in `store/maintenance_windows.py`, and `tests/test_maintenance_window.py` asserts the
-- set from the store's own constant so the two cannot drift.
CREATE TABLE maintenance_window (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT    NOT NULL,
    description     TEXT    NOT NULL DEFAULT '',
    organization_id INTEGER NOT NULL REFERENCES organization (id),
    status          TEXT    NOT NULL,

    -- WHEN. `starts_at`/`ends_at` are absolute instants — seconds since the epoch, UTC — because
    -- an instant is what the ingest check compares a trap's timestamp against. `tz` is the
    -- canonical IANA zone the operator chose the site in, kept so the console can render the
    -- site's wall clock and so an EXTEND is computed in the zone the work is happening in rather
    -- than in the viewer's.
    --
    -- **The zone is stored beside the instants, not instead of them** (ADR #362). A window across
    -- a DST transition has two offsets, so a stored offset would make half of it an hour wrong —
    -- which is one of this release's injections.
    tz              TEXT    NOT NULL,
    starts_at       REAL    NOT NULL,
    ends_at         REAL    NOT NULL,
    all_day         INTEGER NOT NULL DEFAULT 0,

    -- THE PATCH BAND. Equipment does not come back the instant a window closes: a chassis
    -- rebooting at 11:58 emits its linkUp storm at 12:03, and an operator who declared 10:00-12:00
    -- did not mean to be paged for it. `patch_s` extends the window by the same margin at BOTH
    -- ends, so the interval actually in force is
    --
    --     [starts_at - patch_s, ends_at + patch_s)
    --
    -- and the console's timeline bar draws exactly that: a leading band, the declared window, a
    -- trailing band. The declared bounds are what the card says and what an audit row records;
    -- the effective bounds are what the ingest check compares against, and `engine/mw/index.py`
    -- computes them once, when the window is compiled.
    patch_s         REAL    NOT NULL DEFAULT 600,

    -- THE STATE LEDGER (II.2). On by default. Off is true discard, and the console shows the risk
    -- in the operator's own words rather than hiding the switch — see `engine/mw/ledger.py`.
    ledger_enabled  INTEGER NOT NULL DEFAULT 1,

    -- WHO SEES THE DETAILS. `everyone` or `editors`. The window's EXISTENCE ignores this column
    -- entirely: every role sees a marker on a device or a situation under a window, because a
    -- silent quiet host is the failure mode this feature would otherwise create.
    visibility      TEXT    NOT NULL DEFAULT 'editors',

    -- WHO DECLARED IT. `owner_ref` is the principal reference the audit log already uses
    -- (`user:<id>` or `token:<id>`), so a window and its audit rows join without a second notion
    -- of identity. `created_by_agent` is 1 when the creator authenticated with a service token —
    -- **an agent-created window is marked in the API and on screen** (Part III), because an
    -- operator reading a list of planned work is entitled to know which entries a human wrote.
    owner_ref       TEXT    NOT NULL,
    owner_role      TEXT    NOT NULL,
    created_by_agent INTEGER NOT NULL DEFAULT 0,

    -- D6. `needs_confirmation` is decided ONCE, at create, from the declared duration against
    -- `CONFIRMATION_THRESHOLD_S`, and stored — rather than recomputed on every read from the
    -- current bounds. An extend that pushes a four-hour window past six hours re-decides it
    -- explicitly at that one write site, which is a visible line of code; recomputing on read
    -- would mean a window silently losing its confirmation because somebody moved its end.
    needs_confirmation INTEGER NOT NULL DEFAULT 0,
    confirmed_at    REAL,
    confirmed_by    TEXT,

    created_at      REAL    NOT NULL,
    updated_at      REAL    NOT NULL,
    cancelled_at    REAL,
    -- Set when the window stops being in force, by the clock or by "end now". Distinct from
    -- `ends_at`, which is what was DECLARED: an engineer who finishes at 11:20 leaves
    -- `ends_at = 12:00` and `ended_at = 11:20`, and both facts matter.
    ended_at        REAL,

    -- IDEMPOTENCY (Part III). An agent that retries after a timeout must not create two windows.
    -- The key is the caller's, opaque here, and the partial unique index below is what enforces
    -- it — a plain UNIQUE would collide every window created without one.
    idempotency_key TEXT
);

CREATE UNIQUE INDEX idx_mw_idempotency ON maintenance_window (idempotency_key)
    WHERE idempotency_key IS NOT NULL;

-- The two reads that run most often: the upcoming list (D7) orders by start within a status, and
-- the index refresh asks for everything in force or about to be.
CREATE INDEX idx_mw_status_starts ON maintenance_window (status, starts_at);
CREATE INDEX idx_mw_ends ON maintenance_window (ends_at);
CREATE INDEX idx_mw_organization ON maintenance_window (organization_id, starts_at);

-- -- the targets ----------------------------------------------------------------------------
--
-- Which elements the window covers. `ON DELETE CASCADE` is safe here in a way it is not on the
-- window itself: a window is CANCELLED rather than deleted, so this cascade fires only where a
-- release deliberately removes a row (the retention prune below).
CREATE TABLE maintenance_window_target (
    window_id INTEGER NOT NULL REFERENCES maintenance_window (id) ON DELETE CASCADE,
    ne_id     INTEGER NOT NULL REFERENCES ne (id),
    PRIMARY KEY (window_id, ne_id)
);
CREATE INDEX idx_mw_target_ne ON maintenance_window_target (ne_id);

-- -- the collection rules (D4) ---------------------------------------------------------------
--
-- One row per rule, per target. A target with no row here collects **nothing**, which is D4's
-- default and the reason absence is meaningful rather than an empty configuration.
--
-- THE COMPOSITION RULE, stored nowhere because it is not configurable: rules of DIFFERENT kinds
-- are ANDed, rules of the SAME kind are ORed. `engine/mw/rules.py` implements it and states why
-- the maintainer's own example settles it.
--
-- A sparse table rather than three tables or a JSON blob: three tables would make "every rule on
-- this target" a three-way union on the refresh path, and a blob would put a parser between the
-- operator's gesture and the check. The unused columns are NULL, which is what a sparse row is
-- for, and the API model validates that exactly the columns of the declared `kind` are present.
CREATE TABLE maintenance_window_rule (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    window_id      INTEGER NOT NULL REFERENCES maintenance_window (id) ON DELETE CASCADE,
    ne_id          INTEGER NOT NULL REFERENCES ne (id),
    kind           TEXT    NOT NULL,           -- severity | oid | slot

    -- kind='severity': an X.733 rank, so SMALLER IS MORE SEVERE. A trap admits when its placed
    -- rank is <= this. A trap with NO placed severity never admits — which is why D5 shipped
    -- first, and why a severity rule on a v0.20.0 appliance would have admitted nothing at all.
    severity_rank  INTEGER,

    -- kind='oid': the subtree, and WHERE TO LOOK FOR IT. `match_on` is `trap` (the notification's
    -- own `snmpTrapOID.0`) or `varbind` (any varbind the trap carries). Both are supported and
    -- the rule says which, because the maintainer's example arc is shaped like a table column
    -- rather than a notification — see `MatchOn` in `engine/mw/rules.py`.
    --
    -- Matched on ARC BOUNDARIES, never on string prefixes: a rule for `…1.1.1.1` must not catch
    -- `…1.1.1.10`. That is this feature's most likely bug and it has its own injection.
    oid_root       TEXT,
    match_on       TEXT,

    -- kind='slot': absolute instants, resolved once at write time against the window's zone.
    -- Half-open, `[starts, ends)`.
    slot_starts_at REAL,
    slot_ends_at   REAL,

    created_at     REAL    NOT NULL
);
CREATE INDEX idx_mw_rule_window ON maintenance_window_rule (window_id, ne_id);

-- -- the state ledger (II.2) -----------------------------------------------------------------
--
-- ---------------------------------------------------------------------------------------------
-- ⚠ THIS TABLE IS NOT COLLECTION, AND ITS SHAPE IS THE PROOF.
--
-- A trap reports a transition ONCE. A fault raised at 11:40 inside a window and never cleared
-- would, without this table, leave the appliance with no record at all at 12:00 — window closed,
-- fibre still dark. So a suppressed trap leaves **one bit and two instants** behind: raise seen,
-- clear seen, when.
--
-- There is NO varbind column, NO severity column and NO payload column, and that absence is the
-- whole design. Storing them would be collecting the trap, which is what the operator said not to
-- do. The consequence is stated rather than hidden: an alarm surfaced from this table says *"this
-- was raised during the window and never cleared"* and says NOTHING about how serious it is,
-- because the appliance does not know. Inventing a severity here would be the fabrication prime
-- directive 2 forbids.
--
-- It is not an alarm, not shown below `admin`, not correlated, not trained on and not in the
-- dataset. `tests/test_maintenance_window.py` runs each of those as an injection.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE maintenance_ledger (
    window_id  INTEGER NOT NULL REFERENCES maintenance_window (id) ON DELETE CASCADE,
    ne_id      INTEGER NOT NULL REFERENCES ne (id),
    -- The same `(device_id, class_id, instance)` triple `alarm` dedups on, so a surfaced entry
    -- lands on exactly the row a collected trap would have opened — and a later real trap for the
    -- same fault bumps that row rather than opening a second one.
    device_id  INTEGER NOT NULL REFERENCES device (id),
    class_id   INTEGER NOT NULL REFERENCES alarm_class (id),
    instance   TEXT    NOT NULL DEFAULT '',
    raised_at  REAL,
    cleared_at REAL,
    -- When the end-of-window sweep turned this into an alarm. NULL means it has not, and the
    -- sweep is idempotent because it reads this column.
    surfaced_at REAL,
    PRIMARY KEY (window_id, device_id, class_id, instance)
);

-- -- the marker on a surfaced alarm ------------------------------------------------------------
--
-- Which window an alarm was surfaced from, or NULL for every alarm that arrived the ordinary way.
-- The console renders it as *"raised during maintenance, still active"*, which is a different
-- sentence from *"this device is under maintenance"* and has to be: one is a fault that outlived
-- planned work, the other is planned work in progress.
ALTER TABLE alarm ADD COLUMN surfaced_from_window_id INTEGER REFERENCES maintenance_window (id);
CREATE INDEX idx_alarm_surfaced ON alarm (surfaced_from_window_id)
    WHERE surfaced_from_window_id IS NOT NULL;
