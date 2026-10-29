-- 0038: NOC leads, and agents on any OpenAI-compatible provider (ADRs #470, #472). Forward-only.
--
-- 1. `noc_grant.role` gains `lead`: an editor an admin named to read the NOC's measures (KPIs) and
--    to read and write its playbooks. Every admin is one by role, as every admin is a custodian;
--    the grant names the editors. Like a custodian, a lead is a person (`user:<id>`), never a
--    token: an agent's playbook access is its `agent` grant, unchanged.
--
-- 2. `noc_agent.adapter` gains `openai`: the appliance runs the agent itself against an
--    OpenAI-compatible Chat Completions endpoint — OpenAI, Google AI Studio (Gemini), xAI (Grok),
--    Meta (Llama API) or a local Ollama. Its API key stays in the vault, as the `claude`
--    adapter's does, and nobody is shown it; a local Ollama needs none.

ALTER TABLE noc_grant DROP CONSTRAINT noc_grant_role_check;
ALTER TABLE noc_grant ADD CONSTRAINT noc_grant_role_check
    CHECK (role IN ('custodian', 'agent', 'lead'));
ALTER TABLE noc_grant DROP CONSTRAINT noc_grant_check;
ALTER TABLE noc_grant ADD CONSTRAINT noc_grant_check
    CHECK ((role IN ('custodian', 'lead')) = (subject_ref LIKE 'user:%'));

ALTER TABLE noc_agent DROP CONSTRAINT noc_agent_adapter_check;
ALTER TABLE noc_agent ADD CONSTRAINT noc_agent_adapter_check
    CHECK (adapter IN ('webhook', 'claude', 'openai', 'external'));
