/* An agent's Instructions and Configuration tabs (ADRs #465, #468).
 *
 * **Instructions** are what its manager wrote for it: served to it as the MCP prompt `noc_agent`,
 * before how the NOC works here. **Configuration** is how it runs and its limits, read-only to an
 * editor; an admin (`agents.manage`) changes it, issues it a new key, or retires it.
 *
 * A new key and a retirement both end something at once, so both say so before they can be used:
 * the key the agent holds stops working; a retired agent's reports move up to its manager and its
 * cases climb. Its runs and its audit history stay.
 */

import { html, Component } from "../../dom.js";
import { post } from "../../api.js";
import { can } from "../../session.js";
import { absolute } from "../../format.js";
import { Destructive } from "../../destructive.js";
import { AgentForm } from "./agentform.js";
import { KeyOnce } from "./agentkey.js";
import { money } from "./nocui.js";
import { span } from "./caseclock.js";

export class Instructions extends Component {
  constructor(props) {
    super(props);
    this.state = { editing: false, text: props.agent.instructions || "", busy: false, error: null };
  }

  edit(editing) {
    this.setState({ editing, text: this.props.agent.instructions || "", error: null });
    this.props.onEditing(editing);
  }

  async save() {
    const { agent } = this.props;
    this.setState({ busy: true, error: null });
    try {
      await post(`/api/noc/agents/${agent.id}`, { version: agent.version, instructions: this.state.text });
      this.setState({ busy: false, editing: false });
      this.props.onEditing(false);
      this.props.onSaved();
    } catch (error) {
      this.setState({ busy: false, error: error.message });
    }
  }

  render({ agent }, { editing, text, busy, error }) {
    const id = `agent-${agent.id}-instructions`;
    if (editing) {
      return html`<section class="panel-block instructions">
        <label for=${id}><b>Instructions for ${agent.name}</b></label>
        <textarea id=${id} rows="18" maxlength="65536" value=${text}
          onInput=${(e) => this.setState({ text: e.currentTarget.value })}></textarea>
        <p class="hint">${String(text.length).replace(/\B(?=(\d{3})+(?!\d))/g, ",")} of 65,536 characters. It reads them at its
          next run.</p>
        ${error ? html`<p class="err" role="alert">${error}</p>` : null}
        <div class="stepup-acts">
          <button type="button" class="primary" disabled=${busy} onClick=${() => this.save()}>
            ${busy ? "Saving…" : "Save the instructions"}</button>
          <button type="button" onClick=${() => this.edit(false)}>Cancel</button>
        </div>
      </section>`;
    }
    return html`<section class="panel-block instructions">
      <div class="instructions-head">
        <p class="muted">Served to ${agent.name} as the MCP prompt <code>noc_agent</code>, before how
          the NOC works here (the queue, the clocks, the feed, escalating).</p>
        ${can("agents.manage") ? html`<button type="button" onClick=${() => this.edit(true)}>Edit</button>` : null}
      </div>
      ${agent.instructions ? html`<pre class="instructions-text">${agent.instructions}</pre>`
        : html`<p class="muted">None yet: it works from how the NOC works here alone.</p>`}
    </section>`;
  }
}

function facts(a) {
  const c = a.adapter_config || {};
  const rows = [["How it runs", a.adapter_label]];
  if (a.adapter === "claude") {
    rows.push(["Model", c.model], ["Effort", c.effort], ["Most steps per run", c.max_turns],
      ["API key", `vault credential #${c.secret_id}`]);
  }
  if (a.adapter === "webhook") rows.push(["Wake-up URL", c.url], ["Waits for", `${c.timeout_s} s`]);
  rows.push(
    ["Wakes on a routed case", a.wake_on_assignment ? "yes" : "no"],
    ["Heartbeat", a.heartbeat_s ? `every ${span(a.heartbeat_s)}` : "off"],
    ["Escalates after", a.escalate_after_s ? span(a.escalate_after_s) : "the handbook's (30 min critical)"],
    ["A run may last", span(a.run_timeout_s)],
    ["Monthly budget", a.budget_month_cents ? money(a.budget_month_cents) : "none"],
    ["Its key", !a.has_token ? "none" : a.acts_in_process ? "sealed in the vault" : "issued, shown once"],
    ["Hired", `${absolute(a.created_at)} by ${a.created_by}`],
  );
  return rows;
}

export class AgentConfig extends Component {
  constructor(props) {
    super(props);
    this.state = { mode: null, keyed: null, busy: false, error: null };
  }

  mode(mode) {
    this.setState({ mode, error: null });
    this.props.onEditing(mode === "edit");
  }

  saved(answer) {
    this.mode(null);
    if (answer.token || answer.webhook_key) this.setState({ keyed: answer });
    else this.props.onChanged("Saved.");
  }

  async rotate() {
    this.setState({ busy: true, error: null });
    try {
      const answer = await post(`/api/noc/agents/${this.props.agent.id}/key`, {});
      this.setState({ busy: false, mode: null, keyed: { agent: this.props.agent, ...answer } });
    } catch (error) {
      this.setState({ busy: false, error: error.message });
    }
  }

  render({ agent, catalogue, onTerminated }, { mode, keyed, busy, error }) {
    if (keyed) {
      return html`<${KeyOnce} hired=${keyed} mcpPath=${catalogue.mcp_path} title=${`A new key for ${agent.name}`}
        onDone=${() => { this.setState({ keyed: null }); this.props.onChanged("Its new key is in use."); }} />`;
    }
    if (mode === "edit") {
      return html`<${AgentForm} agent=${agent} catalogue=${catalogue} agents=${catalogue.agents}
        onCancel=${() => this.mode(null)} onSaved=${(answer) => this.saved(answer)} />`;
    }
    const admin = can("agents.manage");
    return html`<div class="agent-config">
      <section class="panel-block">
        <div class="instructions-head"><h3>How it runs</h3>
          ${admin ? html`<button type="button" onClick=${() => this.mode("edit")}>Edit the configuration</button>` : null}</div>
        <dl class="fact-grid">${facts(agent).map(([k, v]) => html`<div key=${k}><dt>${k}</dt><dd>${v}</dd></div>`)}</dl>
      </section>
      ${admin && agent.adapter !== "claude" ? html`<section class="panel-block">
        <h3>Its key</h3>
        <p class="consequence" role="note">A new key replaces the one ${agent.name} holds: the old one
          stops working at once, and its credential leases end.</p>
        ${mode === "rotate" ? html`<div class="stepup-acts">
          <button type="button" class="primary" disabled=${busy} onClick=${() => this.rotate()}>
            ${busy ? "Issuing…" : "Issue the new key"}</button>
          <button type="button" onClick=${() => this.mode(null)}>Cancel</button></div>`
          : html`<button type="button" onClick=${() => this.mode("rotate")}>Issue a new key…</button>`}
        ${error ? html`<p class="err" role="alert">${error}</p>` : null}
      </section>` : null}
      ${admin ? html`<${Destructive} title=${`Retire ${agent.name}`}
        hint="For good: it cannot be hired back under this record."
        consequence=${`Its key is revoked; the ${agent.reports.length} agent(s) reporting to it move up to
          ${agent.reports_to_name || "the NOC team"}; the cases it holds climb. Its runs and its audit
          history stay.`}
        previewLabel="Retire…" confirmLabel=${`Retire ${agent.name}`}
        preview=${async () => ({ reports: agent.reports.length, cases: agent.open_cases })}
        renderPreview=${(p) => html`<p>${p.reports} report(s) move up; ${p.cases} open case(s) climb.</p>`}
        apply=${() => post(`/api/noc/agents/${agent.id}/terminate`, {})}
        onDone=${onTerminated} />` : null}
    </div>`;
  }
}
