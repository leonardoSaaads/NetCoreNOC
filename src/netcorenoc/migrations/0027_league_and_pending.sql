-- 0027: the model league and the Pending state (v0.27.0). Forward-only and additive, applying
-- cleanly onto a populated v0.26.0 database (schema 26). No table is rebuilt.
--
-- `0008`'s two rules govern every column below: STORE WHAT CANNOT BE RECOMPUTED; DERIVE WHAT CAN.
-- KEYS ARE NOT FEATURES — every `*_situation_id` and `alarm_id` here is present for a join.

-- -- Pending: a model's proposal to grow a situation an operator has confirmed (ADR #428) ---------
--
-- `situation.status` gains a fourth value, `pending`, beside `new | open | resolved`. The column
-- has carried no CHECK since `0001` and gains none (a CHECK is a table rebuild; `0014` records why
-- not). The values are documented in `store/situations.py`, the one place they are written.
--
-- THE RULE: **an `open` situation's membership changes only by an operator's act.** When the model
-- places an alarm (or a `new` situation) with an `open` one, the alarm goes into a `pending`
-- situation that PROPOSES to join it, and waits for an operator to accept or reject the proposal.
-- `proposed_into` names the `open` situation; `proposal_confidence` is the model's probability that
-- the two belong together, at the last alarm it placed there. Both NULL on every other situation.
-- No foreign key: history outlives its subject, exactly as `situation_event` (0014).
ALTER TABLE situation ADD COLUMN proposed_into INTEGER;
ALTER TABLE situation ADD COLUMN proposal_confidence REAL
    CHECK (proposal_confidence IS NULL OR (proposal_confidence >= 0 AND proposal_confidence <= 1));
CREATE INDEX IF NOT EXISTS idx_situation_proposal
    ON situation (proposed_into) WHERE proposed_into IS NOT NULL;

-- An operator's answer to a proposal. ACCEPT merges the pending situation into its target (and is
-- also a `merge` event, whose cross pairs are positive labels — `gesture_positive_pairs`); REJECT
-- returns it to `new` as a situation of its own, and its cross pairs are NEGATIVE labels
-- (`proposal_negative_pairs`). Both are recorded here, so how often each model's proposals are
-- accepted is a query, and that rate is the fast loop's own report card.
CREATE TABLE IF NOT EXISTS proposal_decision (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    pending_situation_id INTEGER NOT NULL,  -- KEY. No FK: see above.
    target_situation_id  INTEGER NOT NULL,  -- KEY.
    decision             TEXT    NOT NULL CHECK (decision IN ('accept', 'reject')),
    actor                TEXT    NOT NULL,
    role                 TEXT,
    at                   REAL    NOT NULL,
    -- The operator's confidence, on `0014`'s scale and floor; NULL = not reported.
    confidence           REAL CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    -- The MODEL's probability at decision time and which model proposed. RECORDED, NEVER A FEATURE:
    -- a label must not learn from the opinion it is judging (the `incumbent_linked` rule).
    proposal_confidence  REAL,
    decider              TEXT,
    produces_training_rows INTEGER NOT NULL DEFAULT 1 CHECK (produces_training_rows IN (0, 1))
);
CREATE INDEX IF NOT EXISTS idx_proposal_decision_at ON proposal_decision (at);

-- The two bags at the instant of the answer: ordered, positional, server-authoritative — the
-- `situation_event_member` shape. The membership is mutated one statement later and is not
-- recoverable afterwards.
CREATE TABLE IF NOT EXISTS proposal_decision_member (
    decision_id INTEGER NOT NULL REFERENCES proposal_decision (id) ON DELETE CASCADE,
    side        TEXT    NOT NULL CHECK (side IN ('pending', 'target')),
    position    INTEGER NOT NULL,
    alarm_id    INTEGER NOT NULL,
    PRIMARY KEY (decision_id, side, position)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_proposal_member_alarm ON proposal_decision_member (alarm_id);

-- -- The league: which model decides, and who said so (ADRs #423, #425) ----------------------------
--
-- The slow loop's output, APPEND-ONLY: every champion the judge (or an admin's pin) chose, with the
-- reason and the evidence it was chosen on. The fast loop reads the newest row at its reload point.
-- `evidence` is the judge's table as JSON — the ranking, the latencies, the site comparisons — so
-- a switch two months ago is readable without re-running anything.
CREATE TABLE IF NOT EXISTS league_decision (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    at        REAL    NOT NULL,
    champion  TEXT    NOT NULL,  -- '<kind>:<sha12>'
    previous  TEXT,
    actor     TEXT    NOT NULL,  -- 'judge', or the admin who pinned
    reason    TEXT    NOT NULL CHECK (length(reason) > 0),
    pinned    INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1)),
    evidence  TEXT    NOT NULL DEFAULT '{}'
);

CREATE TRIGGER IF NOT EXISTS league_decision_no_update BEFORE UPDATE ON league_decision
BEGIN
    SELECT RAISE(ABORT, 'league_decision is append-only');
END;

CREATE TRIGGER IF NOT EXISTS league_decision_no_delete BEFORE DELETE ON league_decision
BEGIN
    SELECT RAISE(ABORT, 'league_decision is append-only');
END;

-- An admin's pin, on the existing append-only switch table: NULL = the judge chooses.
ALTER TABLE decider_setting ADD COLUMN pinned TEXT;

-- The formula is retired as a decider (ADR #425): an appliance that had opted into it, or was
-- running a site model, hands the choice to the league's judge. One appended row, only when the
-- newest row names another mode — the history above it is untouched.
INSERT INTO decider_setting (mode, set_by, set_at, reason)
SELECT
    'shipped',
    'migration',
    0.0,
    'v0.27.0: the pre-trained models compete and the judge chooses; the additive formula is '
    || 'retired as a decider and remains only as the fail-safe'
WHERE (SELECT mode FROM decider_setting ORDER BY id DESC LIMIT 1) IS NOT 'shipped';
