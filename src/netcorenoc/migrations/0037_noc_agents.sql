-- 0037: the NOC's agents, their organization, their runs, and the cases routed to them
-- (ADRs #465-#467). Forward-only and additive.
--
-- 1. `noc_agent` — an AI agent the NOC "hired": a name, a role and a title, an icon, its tier, who it
--    reports to (NULL: the NOC team, the people at the top), what it is good at, its instructions,
--    how it is woken (its adapter) and its limits. Its identity is a service token (`token_id`),
--    created with it, so everything it does goes through the same perimeter, RBAC, scope and
--    audit as any other caller. For the `claude` adapter the appliance acts as the agent and keeps
--    that token sealed (`token_sealed`); for `webhook` it keeps the signing key sealed
--    (`webhook_key_sealed`). Neither is ever returned by the API.
--
-- 2. `noc_agent_run` — one heartbeat: why it woke (a timer, a case assigned, a person, an
--    escalation, or the agent acting on its own over MCP), on which case, how it ended, and what it
--    cost. `noc_agent_run_step` is what happened in it — each tool call, append-only.
--
-- 3. On `noc_case`: the agent a case is routed to (`agent_id`, `assigned_at`), the tier it has
--    reached (the handbook's With NOC Tier 1-4), when it last escalated, and `with_people` once it
--    climbed past the top of the agents' chain to the NOC team. A taken case that escalates to the
--    people re-enters the queue for a new cycle: reason `escalated`.

CREATE TABLE noc_agent (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name                text COLLATE "C" NOT NULL UNIQUE CHECK (length(name) BETWEEN 1 AND 60),
    role                text COLLATE "C" NOT NULL CHECK (role IN ('head', 'supervisor', 'manager',
                                                                  'engineer', 'specialist',
                                                                  'analyst')),
    title               text COLLATE "C" NOT NULL DEFAULT '' CHECK (length(title) <= 80),
    icon                text COLLATE "C" NOT NULL DEFAULT 'bot',
    tier                smallint NOT NULL DEFAULT 1 CHECK (tier BETWEEN 1 AND 4),
    reports_to          bigint REFERENCES noc_agent (id),
    capabilities        text COLLATE "C" NOT NULL DEFAULT '' CHECK (length(capabilities) <= 2000),
    instructions        text COLLATE "C" NOT NULL DEFAULT '' CHECK (length(instructions) <= 65536),
    adapter             text COLLATE "C" NOT NULL CHECK (adapter IN ('webhook', 'claude',
                                                                     'external')),
    adapter_config      text COLLATE "C" NOT NULL DEFAULT '{}' CHECK (length(adapter_config) <= 8192),
    token_id            integer REFERENCES api_token (id),
    token_sealed        text COLLATE "C",
    webhook_key_sealed  text COLLATE "C",
    entry               boolean NOT NULL DEFAULT false,
    wake_on_assignment  boolean NOT NULL DEFAULT true,
    heartbeat_s         integer NOT NULL DEFAULT 0
                        CHECK (heartbeat_s = 0 OR heartbeat_s BETWEEN 60 AND 86400),
    escalate_after_s    integer CHECK (escalate_after_s BETWEEN 60 AND 86400),
    run_timeout_s       integer NOT NULL DEFAULT 900 CHECK (run_timeout_s BETWEEN 60 AND 7200),
    budget_month_cents  integer NOT NULL DEFAULT 0 CHECK (budget_month_cents >= 0),
    spent_cents         numeric(14, 4) NOT NULL DEFAULT 0,
    spend_month         text COLLATE "C",
    status              text COLLATE "C" NOT NULL DEFAULT 'idle'
                        CHECK (status IN ('idle', 'running', 'paused', 'error', 'terminated')),
    pause_reason        text COLLATE "C" CHECK (length(pause_reason) <= 200),
    paused_at           timestamptz,
    error_reason        text COLLATE "C" CHECK (length(error_reason) <= 500),
    last_run_at         timestamptz,
    version             integer NOT NULL DEFAULT 1,
    created_at          timestamptz NOT NULL,
    created_by          text COLLATE "C" NOT NULL,
    updated_at          timestamptz NOT NULL,
    updated_by          text COLLATE "C" NOT NULL,
    CHECK (reports_to IS NULL OR reports_to <> id)
);
CREATE INDEX idx_noc_agent_reports_to ON noc_agent (reports_to);
CREATE UNIQUE INDEX idx_noc_agent_token ON noc_agent (token_id) WHERE token_id IS NOT NULL;

CREATE TABLE noc_agent_run (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    agent_id      bigint NOT NULL REFERENCES noc_agent (id),
    trigger       text COLLATE "C" NOT NULL CHECK (trigger IN ('timer', 'assignment', 'on_demand',
                                                              'escalation', 'external')),
    situation_id  bigint REFERENCES noc_case (situation_id),
    status        text COLLATE "C" NOT NULL CHECK (status IN ('queued', 'running', 'succeeded',
                                                             'failed', 'timed_out', 'cancelled')),
    queued_at     timestamptz NOT NULL,
    started_at    timestamptz,
    ended_at      timestamptz,
    requested_by  text COLLATE "C" NOT NULL,
    summary       text COLLATE "C" NOT NULL DEFAULT '' CHECK (length(summary) <= 8000),
    error         text COLLATE "C" NOT NULL DEFAULT '' CHECK (length(error) <= 2000),
    input_tokens  bigint NOT NULL DEFAULT 0,
    output_tokens bigint NOT NULL DEFAULT 0,
    cost_cents    numeric(14, 4) NOT NULL DEFAULT 0,
    tool_calls    integer NOT NULL DEFAULT 0
);
CREATE INDEX idx_noc_agent_run_agent ON noc_agent_run (agent_id, id DESC);
CREATE INDEX idx_noc_agent_run_live ON noc_agent_run (status) WHERE status IN ('queued', 'running');
CREATE INDEX idx_noc_agent_run_case ON noc_agent_run (situation_id) WHERE situation_id IS NOT NULL;

CREATE TABLE noc_agent_run_step (
    id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id  bigint NOT NULL REFERENCES noc_agent_run (id),
    at      timestamptz NOT NULL,
    kind    text COLLATE "C" NOT NULL CHECK (kind IN ('wake', 'tool', 'text', 'error')),
    name    text COLLATE "C" NOT NULL DEFAULT '' CHECK (length(name) <= 120),
    ok      boolean NOT NULL DEFAULT true,
    detail  text COLLATE "C" NOT NULL DEFAULT '' CHECK (length(detail) <= 4000)
);
CREATE INDEX idx_noc_agent_run_step_run ON noc_agent_run_step (run_id, id);
CREATE TRIGGER noc_agent_run_step_append_only BEFORE UPDATE OR DELETE ON noc_agent_run_step
    FOR EACH ROW EXECUTE FUNCTION forbid_change('a run step is evidence of what an agent did');

ALTER TABLE noc_case ADD COLUMN agent_id bigint REFERENCES noc_agent (id);
ALTER TABLE noc_case ADD COLUMN assigned_at timestamptz;
ALTER TABLE noc_case ADD COLUMN tier smallint NOT NULL DEFAULT 1 CHECK (tier BETWEEN 1 AND 4);
ALTER TABLE noc_case ADD COLUMN escalated_at timestamptz;
ALTER TABLE noc_case ADD COLUMN with_people boolean NOT NULL DEFAULT false;
CREATE INDEX idx_noc_case_agent ON noc_case (agent_id) WHERE agent_id IS NOT NULL;

ALTER TABLE noc_case_cycle DROP CONSTRAINT noc_case_cycle_reason_check;
ALTER TABLE noc_case_cycle ADD CONSTRAINT noc_case_cycle_reason_check
    CHECK (reason IN ('opened', 'automation', 'customer_post', 'next_update', 'released',
                      'escalated'));
