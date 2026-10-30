-- 0041: the NOC stops serving a customer (ADR #477). Forward-only and additive.
--
-- A customer is an `organization`, and the day the NOC stops serving one — Akamai moves its NOC
-- elsewhere — an admin ends it: its book goes (instructions, the NOC's memory, contacts, documents,
-- circuits: the customer's material, not the NOC's to keep), its elements return to the default
-- organization, and the row stays, ended, because the history names it: the maintenance windows
-- planned for it and the cases' summaries written for it keep their foreign keys, and the audit
-- log keeps the act. An ended customer is on no list, no picker and no agent's tool; adding it
-- again (its handle) reinstates it, its book empty.

ALTER TABLE organization ADD COLUMN ended_at timestamptz;
ALTER TABLE organization ADD COLUMN ended_by text COLLATE "C"
    CHECK (ended_by IS NULL OR length(ended_by) BETWEEN 1 AND 160);
ALTER TABLE organization ADD CONSTRAINT organization_default_never_ends
    CHECK (NOT (is_default AND ended_at IS NOT NULL));
