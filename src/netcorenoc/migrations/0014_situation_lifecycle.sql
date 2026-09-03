-- 0014: the situation lifecycle (v0.16.0). The release's whole schema deliverable, and it is
-- **exactly one migration, additive and forward-only**, applying cleanly onto a populated v0.15.5
-- database (schema v13).
--
-- IT SEEDS NO DATASET ROWS. Both tables below are empty after this script runs on any database,
-- and a migrated appliance behaves at first boot exactly as it did before the upgrade: capture of
-- an operator gesture begins when an operator makes one, never when this script runs. The two
-- `UPDATE`s at the bottom are the **decision-1 migration** of existing `situation` rows, not a
-- seed — they rewrite rows that already exist and create none.
--
-- ---------------------------------------------------------------------------------------------
-- `0008_feedback_dataset.sql`'s TWO RULES govern every column below, and a column satisfying
-- neither does not exist. They are quoted there in full; restated here in the form they take on
-- this schema:
--
--   1. STORE WHAT CANNOT BE RECOMPUTED; DERIVE WHAT CAN. `situation_alarm` is the CURRENT
--      membership and it is mutated by every gesture this release adds, so the bag an operator was
--      looking at when they acted is not recoverable one second later. Every event therefore
--      carries its own snapshot, in the ordered/positional/server-authoritative shape
--      `feedback_member` has used since `0008`. *"A moment not captured is not captured late — it
--      is captured never."*
--
--   2. KEYS ARE NOT FEATURES. `situation_id`, `peer_situation_id` and `alarm_id` below are present
--      FOR A JOIN. Feeding one to a model teaches it the training customer's estate.
-- ---------------------------------------------------------------------------------------------
--
-- SECURITY POSTURE. `situation_event` is written on the HTTP write path and carries the actor, so
-- unlike the `0008` tables it is *not* a scope bypass by construction — but its membership
-- snapshot names alarms without any scope filter, exactly as `feedback_member` does. It is read by
-- the dataset reports and by the census, both of which are `admin`-only and emit aggregates. **No
-- route below `admin` reads a row of either table below.**

-- -- the state machine ---------------------------------------------------------------------------
--
-- `situation.status` is WIDENED from `open | closed | merged` to `new | open | resolved`. The
-- column carries no CHECK constraint today (`0001_init.sql:58`) and it does not gain one: adding a
-- CHECK to an existing column requires a table rebuild, and rebuilding `situation` would move a
-- table three others reference by foreign key in order to state a rule the application already
-- enforces at its one write site. The values are documented here and in `store/situations.py`.
--
-- WHY `status` STAYS SMALL. It is what the three console tabs render, and nothing else. The
-- *reason* a situation left is a different fact with a different audience — an ISP manager
-- auditing two months later — and it goes in its own column, below.
ALTER TABLE situation ADD COLUMN resolution TEXT CHECK (
    resolution IS NULL OR resolution IN (
        'operator',      -- an operator closed it
        'self_cleared',  -- every member alarm cleared: the network fixed itself
        'idle',          -- the idle sweep timed it out at IDLE_CLOSE_S
        'merged',        -- the correlator merged it into another situation
        'manual_clear',  -- an operator hand-cleared the last active member (a zombie alarm)
        -- **The one value that will never be written again after this script.** DECISIONS #253.
        -- Before v0.16.0, `closed` meant operator-closed OR idle-swept and NOTHING DISTINGUISHED
        -- THEM. Writing `idle` here would be a GUESS ABOUT CONTENT where `0008`'s one permitted
        -- data write is explicitly a MARKER ABOUT PROVENANCE — the same distinction that made
        -- `legacy_capture` a statement about how a row was acquired rather than a verdict on it.
        -- An operator auditing later must be able to tell "nobody looked at it" from "we do not
        -- know", and a value that says "unknown" is the only honest way to say so.
        'unattributed'
    )
);

-- -- the two names, and they are TWO COLUMNS -----------------------------------------------------
--
-- A DERIVED name (`Storm -> 192.168.0.2`) is a PROJECTION of facts already in the database:
-- recomputable, it changes when membership changes, and it is evidence of nothing. An OPERATOR's
-- name is a LABEL and carries provenance. Collapsing them into one column would make an operator's
-- judgement indistinguishable from a string the server computed, which is the whole reason the
-- feedback dataset keeps `member_digest` and `client_member_digest` apart.
--
-- `derived_name` is written by the same statement group that changes `situation_alarm` and by
-- nothing else, so it cannot go stale (DECISIONS #257). Its value is a function of the member count
-- and the DISTINCT DEVICE ADDRESSES of the members, and of nothing else — not the root alarm, which
-- moves on every activation, and not an operator's device label, which is free text the server must
-- not fold into a name it computes.
ALTER TABLE situation ADD COLUMN derived_name TEXT;

-- **Never written by the server, and never by a model.** A model proposing "fibre cut" above a
-- grouping an operator is about to judge contaminates that judgement — the `incumbent_linked`
-- mistake in a new register — so v0.16.0 has no path that writes here except an operator's own
-- rename. `tests/test_store.py::test_no_server_derivation_ever_reaches_operator_name` is the guard.
ALTER TABLE situation ADD COLUMN operator_name TEXT;

-- -- the append-only history --------------------------------------------------------------------
--
-- `situation_alarm` remains the CURRENT membership and is MUTATED. That is safe because every
-- gesture below captures the membership at the instant it happened, so mutation loses nothing —
-- the rule `0008` established and `feedback_member` already implements.
--
-- **NO FOREIGN KEY ON `situation_id`, and that is deliberate**, exactly as `dataset_pair` carries
-- none (`0008`, and F44's reasoning in `store/retention.py`). History must outlive its subject: the
-- operational prune collects a situation once nothing needs its shell, and an event that could not
-- survive that would be an append-only log with a delete in it. `prune()` retains the shell while
-- an event still references it, and `prune_dataset_audit()` — the outer bound of the data's life —
-- is the one path that removes an event.
CREATE TABLE situation_event (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    -- KEY. A join, never a feature. No FK: see above.
    situation_id      INTEGER NOT NULL,
    kind              TEXT NOT NULL CHECK (kind IN (
        'verdict',          -- a `confirm` or `split` recorded through the feedback surface
        'move',             -- one alarm moved from this situation to another
        'merge',            -- another situation merged into this one, by an operator
        'operator_split',   -- this situation split into two, by an operator
        'manual_clear',     -- an operator hand-cleared a zombie alarm
        'self_clear',       -- every member cleared and the appliance resolved the situation
        'idle_close',       -- the idle sweep resolved it
        'operator_close',   -- an operator closed it
        'rename'            -- an operator named it
    )),
    -- WHO. NULL for the two the appliance does itself (`self_clear`, `idle_close`), and NULL is an
    -- honest unknown rather than a default — "the appliance" is not an actor with a confidence.
    actor             TEXT,
    role              TEXT,
    at                REAL NOT NULL,
    -- THE OPERATOR'S CONFIDENCE, ON A 0-1 SCALE, IN ITS OWN COLUMN.
    --
    -- `PREREGISTRATION-0.16.0.md` §4. It is recorded PER ACTOR precisely so a later release can
    -- measure whether a given operator's stated 0.8 corresponds to an 0.8 rate of being right —
    -- which is the only way this stops being a convention. Until that measurement exists, the
    -- multiplier `m(c) = 0.6 + 0.4c` is not revised.
    --
    -- It is **NEVER** multiplied into a stored `TrainingRow.weight`. That field already carries the
    -- design-effect correction (`1/len(bucket)`) and the class balance, and folding a third meaning
    -- into one number makes all three unrecoverable. The multiplier is applied AT DERIVATION and
    -- the composition is recorded in the run's diagnostics.
    --
    -- NULL means **not reported** — the gestures that predate a confidence control, and the two the
    -- appliance performs itself. It is not 0.0, which would fall below the floor and mean something
    -- entirely different.
    confidence        REAL CHECK (confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)),
    -- THE CHANNEL, extended and never repeated (DECISIONS #126). `move`, `merge` and
    -- `operator_split` join the existing `organic`, `close`, `confirm` and `split`. Channels are
    -- **reported separately and never averaged**: a merge selects for a different population from
    -- the one an operator browses, and blending them destroys the bias characterisation
    -- retroactively, including for rows already written.
    acquisition_channel TEXT,
    -- KEY. The `feedback` row this gesture produced, when it produced one. A gesture whose
    -- bag-level assertion is exactly what a `split` with marked members means writes one; a gesture
    -- whose assertion has no such shape writes none rather than over-asserting.
    feedback_id       INTEGER,
    -- KEY. The other situation, for `move` (the destination) and `merge` (the source).
    peer_situation_id INTEGER,
    -- KEY. The alarm this gesture is about, for `move` and `manual_clear`.
    alarm_id          INTEGER,
    -- THE SNAPSHOT, SUMMARISED. The rows are in `situation_event_member`; these two are here for
    -- the same reason `feedback.member_digest` and `member_count` are on the label row — a cheap
    -- comparison that does not need the child table. `member_count = 0` is legitimate and
    -- informative: a gesture on a situation whose members have all moved away is a real event.
    member_count      INTEGER NOT NULL,
    member_digest     TEXT NOT NULL,
    peer_member_count INTEGER,
    peer_member_digest TEXT,
    -- WHETHER THIS GESTURE ASSERTS ANYTHING ABOUT A GROUPING.
    --
    -- 0 for `manual_clear` and `self_clear`, always, and that is the invariant this release exists
    -- to keep: they are facts about an ALARM's lifecycle, not about correlation, and a fact about a
    -- different question may not do the work of a measurement about this one
    -- (`PREREGISTRATION-0.16.0.md` §1). It is stored rather than derived from `kind` so the
    -- prohibition is a value a query can count and a guard can assert, rather than a rule written
    -- in a `CASE` expression somewhere.
    --
    -- Also 0 when the operator's confidence was below the registered floor of 0.50: the action
    -- still happened and the event is recorded in full — the operator is running the network, not
    -- labelling it — and it produces no training row.
    produces_training_rows INTEGER NOT NULL DEFAULT 0
        CHECK (produces_training_rows IN (0, 1)),
    -- -- BAG PROVENANCE: RECORDED, AND NOT CONSUMED ----------------------------------------------
    --
    -- `PREREGISTRATION-0.16.0.md` §5, and the plan is explicit that a build which supplies either
    -- of these to a scorer, a promotion input or a verdict trigger has violated it. They are here
    -- because they CANNOT BE RECOMPUTED LATER — the scores decay, membership mutates, and `0008`'s
    -- first rule applies — and they are REPORTED in the census, stratified.
    --
    -- The weakest link's margin over the threshold, within the bag at the instant of the gesture. A
    -- grouping whose weakest pair cleared by 0.01 is one scorer nudge from falling apart; one whose
    -- weakest cleared by 0.3 is not. `ui/app/views/parts/why.js` already computes this number for
    -- the operator (#245); this records the server's own reading of it beside the assertion.
    -- NULL when the situation had no link at all — a one-member bag has no weakest pair, and 0.0
    -- would say something false about one.
    bag_weakest_margin REAL,
    -- Whether the bag's link graph has a BRIDGE whose removal splits it into two parts each above a
    -- registered minimum size. A bag held together by one frail edge is epistemically different
    -- from a densely-linked one, and today both enter training indistinguishable.
    bag_has_bridge    INTEGER CHECK (bag_has_bridge IS NULL OR bag_has_bridge IN (0, 1)),
    -- **The measurement the line above is a threshold on**, stored beside it because the plan
    -- registers *"two parts each above a registered minimum size"* and does not fix the size. A
    -- boolean alone would bake this build's choice of that number into the corpus permanently, and
    -- a later release that registered a different minimum could not recompute the answer — the link
    -- graph is gone by then. So the raw quantity is recorded: the number of members on the SMALLER
    -- side of the best bridge, NULL when the graph has no bridge at all. `bag_has_bridge` is this
    -- value tested against `provenance.MIN_BRIDGE_SIDE`, and the choice of that constant is an open
    -- question for v0.16.1 rather than a decision this release made with a result in view.
    bag_bridge_min_side INTEGER,
    bag_link_count    INTEGER
);
-- The history of one situation, in order. The census and the reports read this way, and so does
-- the console's resolved card.
CREATE INDEX idx_situation_event_situation ON situation_event (situation_id, at);
-- At most one event per label row, so the training join can LEFT JOIN for the confidence and the
-- provenance without changing its row count. A partial index because most events write no label.
CREATE UNIQUE INDEX idx_situation_event_feedback
    ON situation_event (feedback_id) WHERE feedback_id IS NOT NULL;
-- The channel breakdown the plan requires to be reported separately.
CREATE INDEX idx_situation_event_channel ON situation_event (acquisition_channel, at);

-- -- the membership at the instant of the gesture -------------------------------------------------
--
-- **The same shape `feedback_member` uses**, and deliberately not a variation on it: ordered,
-- positional, server-authoritative. The bag is ORDERED, so the position is part of the record and
-- cannot be recomputed.
--
-- `source` is `server` for the situation the gesture names and `peer` for the other one — the
-- destination of a `move`, the source of a `merge`, the departing half of an `operator_split`.
-- There is no `client` here: these gestures name their subject explicitly and there is no
-- client-reported bag to record. `feedback_member` keeps that half for the verdict surface.
--
-- IT MAY LEGITIMATELY HOLD ZERO ROWS FOR AN EVENT, and no "at least one member" constraint may be
-- imposed: a gesture on a situation whose members have all been moved away is a real gesture, and
-- recording an empty bag AS EMPTY is what makes that population countable.
CREATE TABLE situation_event_member (
    event_id INTEGER NOT NULL REFERENCES situation_event(id) ON DELETE CASCADE,
    source   TEXT NOT NULL CHECK (source IN ('server', 'peer')),
    position INTEGER NOT NULL,
    alarm_id INTEGER NOT NULL,
    PRIMARY KEY (event_id, source, position)
) WITHOUT ROWID;

-- -- the decision-1 migration of existing rows ----------------------------------------------------
--
-- DECISIONS #253. Three source values, three destinations, and only two of them are knowable:
--
--   `open`   -> `open`.        Unchanged, and no `resolution`: it has not left.
--   `merged` -> `resolved` + `merged`.  EXACT, not a guess: `merge_situations` itself wrote that
--                              status, so the reason is recorded in the value being replaced.
--   `closed` -> `resolved` + `unattributed`.  The honest answer. See the column comment above.
--
-- Order matters only in that neither statement may see the other's output: `merged` is rewritten
-- first and lands on `resolved`, which the second statement does not match.
UPDATE situation SET status = 'resolved', resolution = 'merged'       WHERE status = 'merged';
UPDATE situation SET status = 'resolved', resolution = 'unattributed' WHERE status = 'closed';
