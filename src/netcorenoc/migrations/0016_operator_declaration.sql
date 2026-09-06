-- 0016: the operator's declaration (v0.16.3). One table rebuilt, one kind renamed by address,
-- two stored derivations dropped.
--
-- Forward-only, applying cleanly onto a populated v0.16.2 database (schema v15). It fabricates
-- nothing, and no column changes meaning: `label.kind` gains a member and loses one whose rows are
-- carried over, and `qualifier` arrives with the value that means what every existing row already
-- meant.
--
-- ---------------------------------------------------------------------------------------------
-- WHY IT EXISTS, IN THREE MEASUREMENTS.
--
-- 1. THE NAME NOBODY COULD SEE. The console wrote `label(kind='device', target_id=device.id)` and
--    the Entities screen read the `ne` table, which `store/entities.py::list_ne` selected five
--    columns from and joined no label to. An operator renamed a host and nothing changed, forever.
--
-- 2. `device.id == ne.id` FOR 25/25 ADDRESSES, AND THAT IS A COINCIDENCE. Two independent
--    AUTOINCREMENT sequences, no foreign key, no shared key, and one migration (`0003`) that
--    happened to preserve ids on a backfill. So the repair is not a join onto the same integer —
--    that is F53's shape, a property that holds by accident — it is a **species**. Every
--    `kind='device'` row moves to `kind='ne'` KEYED ON THE ADDRESS, below, which is correct whether
--    or not the two ids agree. DECISIONS #281.
--
-- 3. `alarm_class.name` AND `.vendor` ARE STORED DERIVATIONS. Measured on a ten-scenario replay:
--    the stored `name` equals `known_oids.trap_name(oid)` for 48/48 rows and the stored `vendor`
--    equals `known_oids.vendor_of(oid)` for 48/48. `oid` is in the same row, so `0008`'s first
--    rule — STORE WHAT CANNOT BE RECOMPUTED, DERIVE WHAT CAN — says both columns should never have
--    existed. They are dropped and both values are computed at read time. DECISIONS #280.
--
-- WHAT THE OPERATOR GAINS, WHICH IS THE WHOLE RELEASE. `label` becomes the one place three things
-- an operator already knows can be written down: which equipment this is (`ne`), what this trap
-- means (`class`), and how serious it is (`severity`). The declared takes precedence over the
-- derived at READ time and the derived is never overwritten, so a disagreement between an operator
-- and the appliance survives as evidence instead of being spent.
-- ---------------------------------------------------------------------------------------------
--
-- -- The label table, rebuilt for a key wide enough for the refinement it will need ------------
--
-- WHY A REBUILD RATHER THAN AN `ALTER TABLE ADD COLUMN`. `qualifier` has to be part of the PRIMARY
-- KEY, and SQLite cannot widen one in place. `label` is the one table in this schema small enough
-- for a create/copy/drop/rename to be free — it holds one row per named thing, not one per trap.
--
-- WHAT `qualifier` IS FOR, REGISTERED NOW RATHER THAN DISCOVERED LATER (DECISIONS #283). Severity
-- is declared PER ALARM CLASS in this release: `qualifier = ''` means "the whole class". A later
-- release refining it to class + varbind writes the varbind OID into the column and the key that
-- already admit it, and the read becomes "longest matching qualifier wins" — CODE, not a second
-- migration. `''` means what it means today and still means it then; nothing is repurposed.
CREATE TABLE label_new (
    kind       TEXT NOT NULL, -- ne | class | severity
    target_id  INTEGER NOT NULL,
    qualifier  TEXT NOT NULL DEFAULT '', -- '' = the whole target; a varbind OID refines it later
    label      TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (kind, target_id, qualifier)
);

-- The rename, BY ADDRESS. `device.ip` and `ne.ip` are both UNIQUE, so the join is 1:1 and no two
-- device labels can collide on one NE.
--
-- A `device` row with no `ne` sharing its address would lose its label here, and that is stated
-- rather than worked around: `0003` created one NE per device and both ingest paths insert the
-- pair under the same address, so such a row is unreachable — and if one existed, its label named
-- an element that has no NE, which nothing renders after this migration. Carrying it by id
-- instead would be exactly the accident this migration exists to stop relying on.
INSERT INTO label_new (kind, target_id, qualifier, label, updated_at)
SELECT 'ne', n.id, '', l.label, l.updated_at
FROM label l
JOIN device d ON d.id = l.target_id
JOIN ne n ON n.ip = d.ip
WHERE l.kind = 'device';

-- Everything that was not a device label, carried verbatim at the qualifier that means what it
-- has always meant. `kind='class'` is the only other kind `0001` declared.
INSERT INTO label_new (kind, target_id, qualifier, label, updated_at)
SELECT kind, target_id, '', label, updated_at FROM label WHERE kind <> 'device';

DROP TABLE label;
ALTER TABLE label_new RENAME TO label;

-- -- The two stored derivations -------------------------------------------------------------
--
-- Neither column is indexed and neither appears in a constraint, so both drop in place. Every
-- reader now calls `known_oids.trap_name(oid)` / `vendor_of(oid)` on the `oid` beside them, which
-- is where the value came from in the first place — and which, unlike a stored copy, is still
-- right the day `IANA_ENTERPRISES` gains an entry.
ALTER TABLE alarm_class DROP COLUMN name;
ALTER TABLE alarm_class DROP COLUMN vendor;
