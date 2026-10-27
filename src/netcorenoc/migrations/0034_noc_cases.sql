-- 0034: NOC cases (ADRs #459-#461). Forward-only and additive.
--
-- 1. `noc_case` — the NOC's half of a situation: owner, status, priority, and the customer update
--    (title, description, executive summary, current analysis, NOC next step, customer next step,
--    next update), with the resolution when it ends. Keyed by the situation it works: a trap
--    belongs to one situation, and a situation has at most one case. The status vocabulary is
--    `engine/noc/cases.py` STATUSES, asserted equal to this CHECK by `tests/noc/test_cases.py`.
--
-- 2. **A situation that becomes `open` gets its case in the same transaction**, by the trigger at
--    the end of this file. Every path to `open` — an operator's promotion, a feedback, a move, a
--    merge, a split, a hand clear, autonomy, an operator's split creating one `open` — goes
--    through `situation.status`, so one trigger covers all of them, including any added later,
--    and the broker's code does not change. The case starts `new`; the case loop dispatches it to
--    automation or to the NOC queue, and the take clock counts from the instant it opened.
--
-- 3. `noc_case_cycle` — one row per entry into the NOC queue, with the instants the two clocks are
--    read from (taken, documented, left working, or ended untaken). The KPI history.
--
-- 4. `noc_case_summary` — the executive summary per organization: each company whose equipment
--    the situation touches may have its own title for the case. The case's own
--    `executive_summary` is the default every other organization reads.
--
-- 5. `noc_case_post` (and `noc_case_post_reader`) — the case feed. Append-only: a post is
--    evidence, and an edit would rewrite what a customer was told. `visibility` is who may read it:
--    `internal` (the NOC: whoever may work cases, people and agents), `specified` (its author, the
--    people it names, and admins), `all` (everyone who may read the case).
--
-- Retention keeps a situation that has a case (`store/retention.py`), as it keeps one with a label.

CREATE TABLE noc_case (
    situation_id       bigint PRIMARY KEY REFERENCES situation (id),
    status             text COLLATE "C" NOT NULL CHECK (status IN (
        'new', 'auto', 'queue', 'working', 'pending_customer', 'pending_fiber_provider',
        'pending_maintenance', 'pending_dispatch', 'pending_device_access', 'pending_connectivity',
        'pending_vendor', 'pending_site_access', 'pending_field_tech', 'pending_rca', 'validate',
        'resolved', 'closed')),
    priority           text COLLATE "C" NOT NULL CHECK (priority IN ('critical', 'major', 'minor')),
    title              text COLLATE "C" NOT NULL DEFAULT '' CHECK (length(title) <= 120),
    description        text COLLATE "C" NOT NULL DEFAULT '' CHECK (length(description) <= 4000),
    executive_summary  text COLLATE "C" NOT NULL DEFAULT ''
                       CHECK (length(executive_summary) <= 200),
    current_analysis   text COLLATE "C" NOT NULL DEFAULT ''
                       CHECK (length(current_analysis) <= 8000),
    noc_next_step      text COLLATE "C" NOT NULL DEFAULT '' CHECK (length(noc_next_step) <= 4000),
    customer_next_step text COLLATE "C" NOT NULL DEFAULT ''
                       CHECK (length(customer_next_step) <= 4000),
    next_update_at     timestamptz,
    owner_ref          text COLLATE "C",
    owner_name         text COLLATE "C",
    owner_kind         text COLLATE "C" CHECK (owner_kind IS NULL OR owner_kind IN ('person', 'agent')),
    opened_at          timestamptz NOT NULL,
    status_at          timestamptz NOT NULL,
    cause              text COLLATE "C",
    solution           text COLLATE "C" NOT NULL DEFAULT '' CHECK (length(solution) <= 8000),
    resolution         text COLLATE "C"
                       CHECK (resolution IS NULL OR resolution IN ('noc', 'automation', 'cleared',
                                                                   'merged')),
    resolved_at        timestamptz,
    resolved_by        text COLLATE "C",
    alarms_cleared     boolean NOT NULL DEFAULT false,
    version            integer NOT NULL DEFAULT 1,
    created_at         timestamptz NOT NULL,
    updated_at         timestamptz NOT NULL,
    updated_by         text COLLATE "C",
    CHECK ((owner_ref IS NULL) = (owner_kind IS NULL))
);
CREATE INDEX idx_noc_case_status ON noc_case (status, status_at);
CREATE INDEX idx_noc_case_owner ON noc_case (owner_ref) WHERE owner_ref IS NOT NULL;
CREATE INDEX idx_noc_case_next_update ON noc_case (next_update_at)
    WHERE status LIKE 'pending\_%';

CREATE TABLE noc_case_cycle (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    situation_id  bigint NOT NULL REFERENCES noc_case (situation_id),
    reason        text COLLATE "C" NOT NULL CHECK (reason IN ('opened', 'automation',
                                                              'customer_post', 'next_update',
                                                              'released')),
    priority      text COLLATE "C" NOT NULL CHECK (priority IN ('critical', 'major', 'minor')),
    entered_at    timestamptz NOT NULL,
    taken_at      timestamptz,
    taken_by      text COLLATE "C",
    documented_at timestamptz,
    left_at       timestamptz,
    ended_at      timestamptz,
    CHECK (taken_at IS NULL OR ended_at IS NULL)
);
CREATE INDEX idx_noc_case_cycle_case ON noc_case_cycle (situation_id, id DESC);
CREATE INDEX idx_noc_case_cycle_entered ON noc_case_cycle (entered_at);

CREATE TABLE noc_case_summary (
    situation_id    bigint NOT NULL REFERENCES noc_case (situation_id),
    organization_id integer NOT NULL REFERENCES organization (id),
    summary         text COLLATE "C" NOT NULL CHECK (length(summary) BETWEEN 1 AND 200),
    updated_at      timestamptz NOT NULL,
    updated_by      text COLLATE "C" NOT NULL,
    PRIMARY KEY (situation_id, organization_id)
);

CREATE TABLE noc_case_post (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    situation_id bigint NOT NULL REFERENCES noc_case (situation_id),
    author_ref   text COLLATE "C" NOT NULL,
    author_name  text COLLATE "C" NOT NULL,
    author_kind  text COLLATE "C" NOT NULL CHECK (author_kind IN ('person', 'agent', 'system')),
    author_role  text COLLATE "C" CHECK (author_role IS NULL OR author_role IN ('admin', 'editor',
                                                                               'viewer')),
    visibility   text COLLATE "C" NOT NULL CHECK (visibility IN ('internal', 'specified', 'all')),
    kind         text COLLATE "C" NOT NULL CHECK (kind IN ('post', 'event')),
    body         text COLLATE "C" NOT NULL CHECK (length(body) BETWEEN 1 AND 8000),
    created_at   timestamptz NOT NULL
);
CREATE INDEX idx_noc_case_post_case ON noc_case_post (situation_id, id DESC);

CREATE TABLE noc_case_post_reader (
    post_id bigint NOT NULL REFERENCES noc_case_post (id),
    user_id integer NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
    PRIMARY KEY (post_id, user_id)
);
CREATE INDEX idx_noc_case_post_reader_user ON noc_case_post_reader (user_id);

CREATE TRIGGER noc_case_post_append_only BEFORE UPDATE OR DELETE ON noc_case_post
    FOR EACH ROW EXECUTE FUNCTION forbid_change('a post is evidence of what someone was told');

-- The case for a situation that becomes `open`, in the transaction that opened it (header, 2).
CREATE FUNCTION noc_case_open() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO noc_case (situation_id, status, priority, opened_at, status_at, created_at,
                          updated_at)
    VALUES (NEW.id, 'new',
            CASE NEW.severity WHEN 'critical' THEN 'critical' WHEN 'major' THEN 'major'
                              ELSE 'minor' END,
            NEW.updated_at, NEW.updated_at, NEW.updated_at, NEW.updated_at)
    ON CONFLICT (situation_id) DO NOTHING;
    RETURN NULL;
END
$$;

CREATE TRIGGER situation_opens_a_case AFTER INSERT OR UPDATE OF status ON situation
    FOR EACH ROW WHEN (NEW.status = 'open') EXECUTE FUNCTION noc_case_open();

-- Situations already open when this runs: a case each, counted from now — the instant each was
-- opened was never recorded, and a guess would charge the NOC for time before the NOC existed.
INSERT INTO noc_case (situation_id, status, priority, opened_at, status_at, created_at, updated_at)
SELECT id, 'new',
       CASE severity WHEN 'critical' THEN 'critical' WHEN 'major' THEN 'major' ELSE 'minor' END,
       now(), now(), now(), now()
FROM situation WHERE status = 'open';
