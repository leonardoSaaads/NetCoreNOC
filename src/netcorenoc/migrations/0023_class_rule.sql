-- 0023: the alarm-class catalogue — what an operator declared or imported about a trap OID or a
-- whole branch of them (v0.22.0, ADR #384, #385).
--
-- A per-class name and severity already existed (`label` kind 'class' / 'severity', keyed on the
-- class id), which means an operator could describe a trap only after it had arrived. This table
-- is keyed on the **OID** instead, for two reasons the maintainer named:
--
--   * a customer with a MIB list should not have to wait for traps before the console is useful,
--     so an imported row names an OID nobody has sent yet;
--   * naming a vendor branch once — `1.3.6.1.4.1.2011.1.2` -> "ENERGIA CAIU", critical — should
--     name every trap beneath it, which a per-class row cannot express.
--
-- ## A rule is COSMETIC PLUS SEVERITY, and nothing else
--
-- It is resolved at READ time, like the per-class declaration it generalises: it renames a class on
-- every screen and grades its alarms, and it changes nothing about correlation, nothing about what a
-- maintenance window collects (that reads the severity placed at ingest), and nothing in the
-- dataset. **An imported row is not evidence** and no promotion path reads this table.
--
-- ## Precedence (ADR #385)
--
-- For one class: the per-class declaration (`label`) wins outright; then the rules that match it,
-- most specific first — an exact rule, then the deepest subtree — and at the same node `declared`
-- before `imported`; then the name the bundled table gives a standard trap, or the severity the trap
-- carried or the appliance learned; then nothing. Matching is on ARC BOUNDARIES
-- (`known_oids.ancestors`), never string prefixes: a rule on `…2011.1.2` covers `…2011.1.2.1` and
-- never `…2011.1.12`.
CREATE TABLE class_rule (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Dotted numeric arcs. Validated by the API; no CHECK here, for `0014`'s reason — the
    -- application has two write sites and both validate the same shape.
    oid         TEXT    NOT NULL,
    -- 1: this node and every OID beneath it. 0: exactly this OID.
    subtree     INTEGER NOT NULL DEFAULT 0,
    -- NULL means the rule does not name, or does not grade. A rule that does neither is refused.
    name        TEXT,
    severity    TEXT,
    -- What the operator's file said the vendor was. Informational: the vendor shown for a class is
    -- derived from its enterprise arc (`known_oids.vendor_of`) unless an imported row says otherwise.
    vendor      TEXT,
    -- 'declared' (written in the console, one rule at a time) or 'imported' (from a file).
    source      TEXT    NOT NULL,
    -- The file an imported row came from, by name, so the screen can say which import wrote it.
    origin      TEXT,
    created_by  TEXT,
    created_at  REAL    NOT NULL,
    updated_at  REAL    NOT NULL,
    UNIQUE (oid, subtree, source)
);
CREATE INDEX idx_class_rule_source ON class_rule (source);
