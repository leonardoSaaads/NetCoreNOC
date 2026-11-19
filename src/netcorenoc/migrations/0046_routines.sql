-- 0046: routines — a playbook belongs to a customer and has a tier, and an agent may run one (ADR #492).
-- Forward-only.
--
-- Playbooks (ADR #463) matched every customer's cases alike, but a provider's fault is worked its
-- own way: the commands, the hosts and the order differ from one customer to the next. And a check
-- that reads a core router is not a first-line agent's. So:
--
-- * `noc_playbook.organization_id` — the customer a routine is for; NULL is every customer. A
--   routine for a customer runs only on that customer's cases, and is chosen before one for every
--   customer when both match.
-- * `noc_playbook.tier` — the least tier of agent that may see and run it (1 to 4). A tier-1 agent
--   never sees a tier-2 routine; an agent writes routines at or below its own tier.
-- * `noc_playbook_run.started_by` — who asked: NULL for the automation that runs a matching routine
--   before the NOC is called, an agent or a person when one ran it on a case on demand. A run asked
--   for ends `advised`: the routine's conclusion is reported to whoever asked, never applied.

ALTER TABLE noc_playbook ADD COLUMN organization_id integer REFERENCES organization (id);
ALTER TABLE noc_playbook ADD COLUMN tier smallint NOT NULL DEFAULT 1 CHECK (tier BETWEEN 1 AND 4);
CREATE INDEX idx_noc_playbook_organization ON noc_playbook (organization_id);

ALTER TABLE noc_playbook_run ADD COLUMN started_by text COLLATE "C"
    CHECK (started_by IS NULL OR length(started_by) <= 120);
ALTER TABLE noc_playbook_run DROP CONSTRAINT noc_playbook_run_outcome_check;
ALTER TABLE noc_playbook_run ADD CONSTRAINT noc_playbook_run_outcome_check CHECK (
    outcome IS NULL OR outcome IN (
        'resolved', 'pending', 'queue', 'timeout', 'error', 'taken', 'advised'));
