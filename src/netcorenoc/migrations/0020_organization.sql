-- 0020: the organization (v0.21.0, D1). **Attribution, not isolation** — and the schema says so.
--
-- The appliance may monitor two, three or four providers. Each network element belongs to one of
-- them, each maintenance window belongs to one of them, and every list filters by it. That is the
-- whole of what this migration builds, and the boundary is drawn here rather than left to prose:
--
-- ---------------------------------------------------------------------------------------------
-- ⚠ THIS IS NOT TENANT ISOLATION, AND NOTHING BELOW MAKES IT ONE.
--
-- `crosscutting/shaping/scope.py` has carried the same warning about visibility scoping since
-- v0.7.0, and it applies here unchanged: **correlation still learns across every network element,
-- and a situation may still form across an organization boundary.** An organization is a label on
-- a row that answers *"whose equipment is this?"*. It is not a security boundary, it is not an RBAC
-- subject, and no read path below denies on it.
--
-- The path to real isolation is written down as future work — correlation that does not cross
-- organizations, learned affinities per organization, RBAC bound to organization — in
-- `docs/ROADMAP.md`. Claiming isolation this schema does not provide would be worse than not
-- having the column (ADR #366).
-- ---------------------------------------------------------------------------------------------
--
-- RELATIONSHIP TO THE EXISTING SCOPE POLICY (II.4, asked and answered). They stay **separate**,
-- and the reason is F35: a scope selector may only read data a scopable role cannot write, and an
-- organization name is written through `POST /api/organizations` by an admin. Admin-written is not
-- the same as engine-written, and folding a second admin-writable axis into the resolver whose
-- inputs are audited by `test_f35_no_resolver_input_is_writable_by_a_scopable_role` would widen
-- that test's surface for a feature that is explicitly not a security control. Scoping answers
-- *"which elements may this principal see?"*; organization answers *"whose are they?"*. Two
-- questions, two mechanisms (ADR #367).

CREATE TABLE organization (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    -- The operator's own name for the provider. Not a key, not a selector, not read by any
    -- authorization decision — see the banner above.
    name       TEXT    NOT NULL UNIQUE,
    -- A stable short handle for the API and the console's filter chips, so a rename does not
    -- change every stored reference. Lower-case, digits and hyphens; the API validator enforces
    -- the shape and this column carries no CHECK, for the reason `0014` gives about `status`:
    -- adding one to an existing column needs a table rebuild, and the application has one write
    -- site.
    slug       TEXT    NOT NULL UNIQUE,
    created_at REAL    NOT NULL,
    -- `1` for the row this migration creates. It cannot be deleted, because every element and
    -- every window must belong somewhere and a delete would orphan them.
    is_default INTEGER NOT NULL DEFAULT 0
);

-- THE DEFAULT ORGANIZATION. Created here so that nothing is orphaned by the upgrade (II.4): every
-- element that exists on the day this migration runs lands in it, and an appliance monitoring one
-- provider never has to know the feature exists.
--
-- Named for what it is rather than for a customer nobody named. An admin renames it in one gesture
-- and every element follows, because the elements reference the row and not the string.
INSERT INTO organization (name, slug, created_at, is_default)
VALUES ('Default organization', 'default', strftime('%s', 'now') + 0.0, 1);

-- The attribution itself. NULL is possible only in the instant between an element being discovered
-- and the sweep attributing it, and the read models coalesce it to the default — so a new element
-- discovered from the trap stream is never invisible on a filtered list.
ALTER TABLE ne ADD COLUMN organization_id INTEGER REFERENCES organization (id);

UPDATE ne SET organization_id = (SELECT id FROM organization WHERE is_default = 1);

CREATE INDEX IF NOT EXISTS idx_ne_organization ON ne (organization_id);

-- `device` deliberately does NOT gain this column, and that is a finding rather than an omission.
-- `HANDOFF.md` §6 carries the measurement: `device` and `ne` are a duality with one row per
-- address in each and `device.vendor`/`ne.vendor` NULL in every row since F105. Adding a second
-- copy of the attribution to a table this release argues should be merged away would be building
-- the migration that a later release has to unpick. Every read joins `device` to `ne` by address
-- already — `crosscutting/shaping/scope.py` says why it must — so the organization is reachable
-- from a device row without a column on it.
