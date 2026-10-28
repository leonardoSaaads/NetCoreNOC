/* NOC → Organization: who reports to whom (ADRs #465, #467, #468).
 *
 * Paperclip's org chart, with the NOC team — the people — at the top: every agent with no manager
 * reports to them, and a case that climbs past the last agent comes to them. Each card is an
 * agent's mark, name, role, tier, state and open cases. Choosing one opens its place in the NOC
 * beside the chart: its manager, its tier, whether cases start with it, and its instructions —
 * which an admin changes there, without leaving the chart.
 */

import { html, Component, cx } from "../dom.js";
import { get, post } from "../api.js";
import { Loader, Empty } from "../widgets.js";
import { can } from "../session.js";
import { Icon } from "../icons.js";
import { refreshRoster } from "../roster.js";
import { AgentGlyph, AgentState, Tier } from "./parts/nocui.js";

function Card({ node, selected, onPick }) {
  return html`<button type="button" class=${cx("org-card", `role-${node.role}`, selected && "on")}
      aria-pressed=${selected ? "true" : "false"} onClick=${() => onPick(node.id)}>
    <${AgentGlyph} agent=${node} size="md" />
    <span class="org-card-text">
      <b>${node.name}</b>
      <span class="muted">${node.title || node.role_label}</span>
      <span class="org-card-facts"><${Tier} tier=${node.tier} />
        <${AgentState} status=${node.status} label=${node.status} />
        ${node.open_cases ? html`<span class="muted">${node.open_cases} case${node.open_cases === 1 ? "" : "s"}</span>` : null}
      </span>
    </span>
  </button>`;
}

function Branch({ nodes, picked, onPick }) {
  if (!nodes.length) return null;
  return html`<ul class="org-level">${nodes.map((node) => html`<li key=${node.id} class="org-node">
    <${Card} node=${node} selected=${picked === node.id} onPick=${onPick} />
    <${Branch} nodes=${node.reports} picked=${picked} onPick=${onPick} />
  </li>`)}</ul>`;
}

class Place extends Component {
  constructor(props) {
    super(props);
    this.state = { a: null, error: null, draft: null, busy: false, note: null };
  }

  componentDidMount() { this.read(); }

  async read() {
    try {
      const a = (await get(`/api/noc/agents/${this.props.id}?days=1`)).agent;
      this.setState({ a, draft: { reports_to: a.reports_to || 0, tier: a.tier, role: a.role,
        entry: a.entry, instructions: a.instructions || "" }, error: null });
    } catch (error) {
      this.setState({ error: error.message });
    }
  }

  async save() {
    const { a, draft } = this.state;
    const body = { version: a.version };
    if (Number(draft.reports_to) !== (a.reports_to || 0)) body.reports_to = Number(draft.reports_to);
    if (Number(draft.tier) !== a.tier) body.tier = Number(draft.tier);
    if (draft.role !== a.role) body.role = draft.role;
    if (draft.entry !== a.entry) body.entry = draft.entry;
    if (draft.instructions !== (a.instructions || "")) body.instructions = draft.instructions;
    if (Object.keys(body).length === 1) return;
    this.setState({ busy: true, note: null });
    try {
      await post(`/api/noc/agents/${a.id}`, body);
      this.setState({ busy: false, note: "Saved." });
      await this.read();
      refreshRoster();
      this.props.onChanged();
    } catch (error) {
      this.setState({ busy: false, note: error.message });
    }
  }

  render({ catalogue, onClose }, { a, error, draft, busy, note }) {
    if (error) return html`<aside class="org-place panel-block"><p class="err" role="alert">${error}</p></aside>`;
    if (!a) return html`<aside class="org-place panel-block"><p class="muted">Reading…</p></aside>`;
    const admin = can("agents.manage");
    const set = (key) => (e) => this.setState({ draft: { ...draft,
      [key]: e.currentTarget.type === "checkbox" ? e.currentTarget.checked : e.currentTarget.value } });
    const others = catalogue.agents.filter((o) => o.id !== a.id && o.status !== "terminated");
    const p = `org-${a.id}`;
    return html`<aside class="org-place panel-block" aria-label=${`${a.name} in the NOC`}>
      <header class="org-place-head">
        <${AgentGlyph} agent=${a} size="md" />
        <div><h3>${a.name}</h3><p class="muted">${a.role_label}${a.title ? ` · ${a.title}` : ""}</p></div>
        <button type="button" class="icon" aria-label="Close" title="Close" onClick=${onClose}><${Icon} name="cross" /></button>
      </header>
      ${admin ? html`<div class="pe-fields">
        <div class="pe-field"><label for=${`${p}-boss`}>Reports to</label>
          <select id=${`${p}-boss`} value=${draft.reports_to} onChange=${set("reports_to")}>
            <option value="0">The NOC team (people)</option>
            ${others.map((o) => html`<option key=${o.id} value=${o.id}>${o.name} — ${o.role_label}</option>`)}
          </select></div>
        <div class="pe-field"><label for=${`${p}-role`}>Role</label>
          <select id=${`${p}-role`} value=${draft.role} onChange=${set("role")}>
            ${Object.entries(catalogue.roles).map(([k, v]) => html`<option key=${k} value=${k}>${v}</option>`)}
          </select></div>
        <div class="pe-field"><label for=${`${p}-tier`}>Tier</label>
          <select id=${`${p}-tier`} value=${draft.tier} onChange=${set("tier")}>
            ${[1, 2, 3, 4].map((t) => html`<option key=${t} value=${t}>Tier ${t}</option>`)}</select></div>
      </div>
      <label class="check-row"><input type="checkbox" checked=${draft.entry} onChange=${set("entry")} />
        Entry point: cases from the queue start with it</label>
      <div class="pe-field"><label for=${`${p}-instr`}>Instructions</label>
        <textarea id=${`${p}-instr`} rows="9" maxlength="65536" value=${draft.instructions}
          onInput=${set("instructions")}></textarea></div>
      ${note ? html`<p class="hint" role="status">${note}</p>` : null}
      <div class="stepup-acts">
        <button type="button" class="primary" disabled=${busy} onClick=${() => this.save()}>
          ${busy ? "Saving…" : "Save its place"}</button>
        <a class="btn-ghost" href=${`#/agents/${a.id}`}>Open its page</a>
      </div>` : html`<dl class="fact-grid">
        <div><dt>Reports to</dt><dd>${a.reports_to_name || "The NOC team"}</dd></div>
        <div><dt>Tier</dt><dd>${a.tier}</dd></div>
        <div><dt>Entry point</dt><dd>${a.entry ? "yes" : "no"}</dd></div>
      </dl>
      <h4>Instructions</h4>
      ${a.instructions ? html`<pre class="instructions-text">${a.instructions}</pre>` : html`<p class="muted">None yet.</p>`}
      <a class="btn-ghost" href=${`#/agents/${a.id}`}>Open its page</a>`}
    </aside>`;
  }
}

export class Org extends Loader {
  constructor(props) {
    super(props);
    this.what = "the organization";
    this.loadingLabel = "Drawing the organization";
  }

  async load() { return get("/api/noc/agents"); }

  routeKey() { return "org"; }

  async refresh() {
    try { this.setState({ data: await this.load() }); } catch { /* the chart stays */ }
  }

  picked() { return Number((this.props.params || [])[0]) || null; }

  pick(id) { this.props.navigate(id === this.picked() || id == null ? "#/org" : `#/org/${id}`); }

  view(data) {
    const live = data.agents.filter((a) => a.status !== "terminated");
    if (!live.length) {
      return html`<${Empty} title="No agents yet"
        will="The NOC team is the top of the organization; each agent hired reports to it or to another agent." >
        ${can("agents.manage") ? html`<a class="btn-ghost" href="#/agents?hire=1">Hire the first agent</a>` : null}
      <//>`;
    }
    const picked = this.picked();
    const entries = live.filter((a) => a.entry).length;
    return html`<div class="org">
      <div class="noc-toolbar">
        <p class="muted">${live.length} agent${live.length === 1 ? "" : "s"} · ${entries} entry
          point${entries === 1 ? "" : "s"} · a case climbs one level each time it is not concluded in time.</p>
        <div class="noc-toolbar-end">
          <a class="btn-ghost" href="#/agents">All agents</a>
          ${can("agents.manage") ? html`<a class="primary btn-link" href="#/agents?hire=1">Hire an agent</a>` : null}
        </div>
      </div>
      <div class=${cx("org-layout", picked && "with-place")}>
        <div class="org-chart" role="group" aria-label="Organization chart">
          <ul class="org-level org-root"><li class="org-node">
            <div class="org-card org-people">
              <span class="agent-glyph glyph-md role-people"><${Icon} name="users" /></span>
              <span class="org-card-text"><b>The NOC team</b>
                <span class="muted">People: editors and admins. Every case past the agents comes here.</span></span>
            </div>
            <${Branch} nodes=${data.org} picked=${picked} onPick=${(id) => this.pick(id)} />
          </li></ul>
        </div>
        ${picked ? html`<${Place} key=${picked} id=${picked} catalogue=${data}
          onClose=${() => this.pick(null)} onChanged=${() => this.refresh()} />` : null}
      </div>
    </div>`;
  }
}
