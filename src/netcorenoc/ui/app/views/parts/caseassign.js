/* Who holds a case among the agents, and moving it (ADRs #467, #468).
 *
 * The panel says which agent the case is routed to, at which tier, since when — or that it is with
 * the NOC team, past the agents. Two acts, each the server's to allow (`actions`): **assign** routes
 * it to an agent of your choosing, or to the NOC team; **escalate** sends it up the chain now, to
 * the agent's manager, with the reason in its feed.
 */

import { html, Component } from "../../dom.js";
import { post } from "../../api.js";
import { can } from "../../session.js";
import { relative, timeTitle } from "../../format.js";
import { roster } from "../../roster.js";
import { AgentGlyph } from "./nocui.js";

class AssignForm extends Component {
  constructor(props) {
    super(props);
    this.state = { to: props.c.agent && props.c.agent.id ? String(props.c.agent.id) : "", note: "",
      busy: false, error: null };
  }

  async submit(event) {
    event.preventDefault();
    this.setState({ busy: true, error: null });
    try {
      const to = Number(this.state.to) || null;
      this.props.onDone(await post(`/api/noc/cases/${this.props.c.id}/assign`,
        { agent_id: to, note: this.state.note }));
    } catch (error) {
      this.setState({ busy: false, error: error.message });
    }
  }

  render({ c, onCancel }, st) {
    const p = `assign-${c.id}`;
    const agents = roster() || [];
    return html`<form class="case-act panel-block" onSubmit=${(e) => this.submit(e)}>
      <h3>Assign</h3>
      <div class="pe-fields">
        <div class="pe-field"><label for=${`${p}-to`}>Route it to</label>
          <select id=${`${p}-to`} value=${st.to} onChange=${(e) => this.setState({ to: e.currentTarget.value })}>
            <option value="">The NOC team (people)</option>
            ${agents.filter((a) => a.status !== "paused").map((a) => html`<option key=${a.id} value=${a.id}>
              ${a.name} — ${a.role_label}, Tier ${a.tier}</option>`)}
          </select></div>
        <div class="pe-field case-wide"><label for=${`${p}-note`}>Why (optional, for the feed)</label>
          <input id=${`${p}-note`} maxlength="500" value=${st.note}
            onInput=${(e) => this.setState({ note: e.currentTarget.value })} /></div>
      </div>
      ${st.error ? html`<p class="err" role="alert">${st.error}</p>` : null}
      <div class="stepup-acts">
        <button type="submit" class="primary" disabled=${st.busy}>${st.busy ? "Assigning…" : "Assign"}</button>
        <button type="button" onClick=${onCancel}>Cancel</button>
      </div>
    </form>`;
  }
}

class EscalateForm extends Component {
  constructor(props) {
    super(props);
    this.state = { reason: "", busy: false, error: null };
  }

  async submit(event) {
    event.preventDefault();
    this.setState({ busy: true, error: null });
    try {
      this.props.onDone(await post(`/api/noc/cases/${this.props.c.id}/escalate`, { reason: this.state.reason }));
    } catch (error) {
      this.setState({ busy: false, error: error.message });
    }
  }

  render({ c, onCancel }, st) {
    const p = `escalate-${c.id}`;
    return html`<form class="case-act panel-block" onSubmit=${(e) => this.submit(e)}>
      <h3>Escalate</h3>
      <p class="hint">${c.agent ? `To ${c.agent.name}'s manager, at the next tier` : "To the first agent able to take it"};
        past the top of the chain, to the NOC team. The reason goes in the feed.</p>
      <div class="pe-field"><label for=${`${p}-why`}>Why</label>
        <input id=${`${p}-why`} required maxlength="500" value=${st.reason}
          placeholder="Optical levels normal; the fault is past the demarcation."
          onInput=${(e) => this.setState({ reason: e.currentTarget.value })} /></div>
      ${st.error ? html`<p class="err" role="alert">${st.error}</p>` : null}
      <div class="stepup-acts">
        <button type="submit" class="primary" disabled=${st.busy || !st.reason.trim()}>
          ${st.busy ? "Escalating…" : "Escalate"}</button>
        <button type="button" onClick=${onCancel}>Cancel</button>
      </div>
    </form>`;
  }
}

/** The assign / escalate form open below the case's header, or null. */
export function MoveForm({ mode, c, onDone, onCancel }) {
  if (mode === "assign") return html`<${AssignForm} c=${c} onDone=${onDone} onCancel=${onCancel} />`;
  if (mode === "escalate") return html`<${EscalateForm} c=${c} onDone=${onDone} onCancel=${onCancel} />`;
  return null;
}

/** The case's place among the agents, for the record's side column. */
export function Assignment({ c, onMode }) {
  const known = c.agent && c.agent.id ? (roster() || []).find((a) => a.id === c.agent.id) : null;
  const may = (act) => can("cases.work") && c.actions.includes(act);
  return html`<section class="panel-block assignment" aria-label="Assignment">
    <h3>Assignment</h3>
    ${c.agent ? html`<div class="assignee">
      <${AgentGlyph} agent=${known || { role: "engineer", icon: "bot" }} size="sm" />
      <div>${c.agent.id && can("agents.read") ? html`<a href=${`#/agents/${c.agent.id}`}><b>${c.agent.name}</b></a>`
          : html`<b>${c.agent.name}</b>`}
        <p class="muted">${c.level_label}${c.assigned_at ? html` · <span title=${timeTitle(c.assigned_at)}>since ${relative(c.assigned_at)}</span>` : null}</p></div>
    </div>` : html`<p class="muted">${c.with_people ? "With the NOC team: past the agents." : "No agent holds it."}</p>`}
    ${c.escalated_at ? html`<p class="muted">Last escalated <span title=${timeTitle(c.escalated_at)}>${relative(c.escalated_at)}</span>.</p>` : null}
    ${may("assign") || may("escalate") ? html`<div class="case-actions">
      ${may("assign") && can("agents.read") ? html`<button type="button" onClick=${() => onMode("assign")}>Assign…</button>` : null}
      ${may("escalate") ? html`<button type="button" onClick=${() => onMode("escalate")}>Escalate…</button>` : null}
    </div>` : null}
  </section>`;
}
