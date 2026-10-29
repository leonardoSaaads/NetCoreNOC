/* One agent's page (ADRs #465, #468), Paperclip's: who it is and what it is doing, then four tabs —
 * Overview (its latest run, its activity, its cases), Instructions, Runs (each with its steps) and
 * Configuration.
 *
 * Read again every ten seconds while nothing is being edited, so a run that starts or ends shows
 * without a reload. Every button is the server's to allow: running, pausing and assigning are
 * `agents.run` / `cases.work`; changing the agent is `agents.manage`.
 */

import { html, Component, cx } from "../../dom.js";
import { get, post } from "../../api.js";
import { can } from "../../session.js";
import { relative, timeTitle } from "../../format.js";
import { Icon } from "../../icons.js";
import { refreshRoster } from "../../roster.js";
import { AgentGlyph, AgentState, Tier } from "./nocui.js";
import { BrandMark } from "../../brands.js";
import { AgentOverview } from "./agentdash.js";
import { AgentRuns } from "./agentruns.js";
import { AgentConfig, Instructions } from "./agentadmin.js";

const TABS = [["overview", "Overview"], ["instructions", "Instructions"], ["runs", "Runs"],
  ["configuration", "Configuration"]];
const REFRESH_MS = 10000;

/** The AI provider an agent the appliance runs thinks with (ADR #471), or null. */
function brandOf(a) {
  if (a.adapter === "claude") return "anthropic";
  return a.adapter === "openai" && a.adapter_config ? a.adapter_config.provider : null;
}

class AssignPanel extends Component {
  constructor(props) {
    super(props);
    this.state = { cases: null, error: null, busy: false };
  }

  async componentDidMount() {
    try {
      const queue = await get("/api/noc/cases?view=queue");
      const working = await get("/api/noc/cases?view=working");
      this.setState({ cases: [...queue.cases, ...working.cases] });
    } catch (error) {
      this.setState({ error: error.message });
    }
  }

  async assign(sid) {
    this.setState({ busy: true, error: null });
    try {
      await post(`/api/noc/cases/${sid}/assign`, { agent_id: this.props.agent.id, note: "" });
      this.props.onDone(`Case #${sid} is ${this.props.agent.name}'s now.`);
    } catch (error) {
      this.setState({ busy: false, error: error.message });
    }
  }

  render({ agent, onCancel }, { cases, error, busy }) {
    return html`<section class="panel-block assign-panel" aria-label=${`Assign a case to ${agent.name}`}>
      <h3>Assign a case to ${agent.name}</h3>
      ${error ? html`<p class="err" role="alert">${error}</p>` : null}
      ${cases === null && !error ? html`<p class="muted">Reading the queue…</p>` : null}
      ${cases && !cases.length ? html`<p class="muted">Nothing is waiting or being worked.</p>` : null}
      ${cases && cases.length ? html`<ul class="pick-list">${cases.map((c) => html`<li key=${c.id}>
        <span class=${cx("case-priority", `prio-${c.priority}`)}>${c.priority_label}</span>
        <span class="pick-name"><span class="mono">#${c.id}</span> ${c.title || c.situation.name || "Untitled"}</span>
        <span class="muted">${c.agent ? c.agent.name : c.status_label}</span>
        <button type="button" disabled=${busy || (c.agent && c.agent.id === agent.id)}
          onClick=${() => this.assign(c.id)}>${c.agent && c.agent.id === agent.id ? "Already its" : "Assign"}</button>
      </li>`)}</ul>` : null}
      <button type="button" onClick=${onCancel}>Close</button>
    </section>`;
  }
}

export class AgentDetail extends Component {
  constructor(props) {
    super(props);
    this.state = { d: null, error: null, busy: false, note: null, assigning: false, editing: false };
  }

  componentDidMount() {
    this.read();
    this.poll = setInterval(() => { if (!this.state.editing) this.read(); }, REFRESH_MS);
  }

  componentWillUnmount() { clearInterval(this.poll); }

  async read() {
    try {
      this.setState({ d: await get(`/api/noc/agents/${this.props.id}?days=14`), error: null });
    } catch (error) {
      this.setState({ error: error.message });
    }
  }

  changed(note) {
    this.setState({ busy: false, note, assigning: false });
    this.read();
    refreshRoster();
    this.props.onChanged();
  }

  async act(url, said) {
    this.setState({ busy: true, note: null });
    try {
      const answer = await post(url, {});
      this.changed(typeof said === "function" ? said(answer) : said);
    } catch (error) {
      this.setState({ busy: false, note: error.message });
    }
  }

  tab() {
    const asked = this.props.tab;
    return TABS.some(([id]) => id === asked) ? asked : "overview";
  }

  go(tab) { this.props.navigate(`#/agents/${this.props.id}${tab === "overview" ? "" : `?tab=${tab}`}`); }

  actions(a) {
    const { busy } = this.state;
    if (a.status === "terminated") return null;
    const run = can("agents.run");
    return html`<div class="agent-actions">
      ${can("cases.work") ? html`<button type="button" disabled=${busy}
        onClick=${() => this.setState({ assigning: true })}>Assign a case</button>` : null}
      ${run && a.status !== "paused" && a.adapter !== "external" ? html`<button type="button" class="primary" disabled=${busy}
        onClick=${() => this.act(`/api/noc/agents/${a.id}/invoke`, (x) => `Woken: run #${x.run.id} is ${x.run.status_label.toLowerCase()}.`)}>
        <${Icon} name="play" /> Run now</button>` : null}
      ${run && a.status === "paused" ? html`<button type="button" class="primary" disabled=${busy}
        onClick=${() => this.act(`/api/noc/agents/${a.id}/resume`, `${a.name} is back at work.`)}><${Icon} name="play" /> Resume</button>` : null}
      ${run && a.status !== "paused" ? html`<button type="button" disabled=${busy}
        onClick=${() => this.act(`/api/noc/agents/${a.id}/pause`, `${a.name} is paused: its key opens nothing until it is resumed.`)}>
        <${Icon} name="pause" /> Pause</button>` : null}
    </div>`;
  }

  header(a) {
    return html`<header class="agent-head">
      <${AgentGlyph} agent=${a} size="lg" />
      <div class="agent-head-text">
        <h2 id=${`agent-${a.id}-heading`}>${a.name}</h2>
        <p class="agent-head-line">
          <span class=${cx("role-chip", `role-${a.role}`)}>${a.role_label}</span>
          ${a.title ? html`<span>${a.title}</span>` : null}
          <${Tier} tier=${a.tier} />
          ${a.entry ? html`<span class="entry-chip">Entry</span>` : null}
          <${AgentState} status=${a.status} label=${a.status_label} />
        </p>
        <p class="muted agent-head-line">
          ${a.reports_to ? html`Reports to <a href=${`#/agents/${a.reports_to}`}>${a.reports_to_name}</a>`
            : "Reports to the NOC team"}
          <span aria-hidden="true">·</span> ${brandOf(a) ? html`<${BrandMark} brand=${brandOf(a)} size=${18} />${" "}` : null}${a.adapter_label.split(" — ")[0]}${
            a.adapter_config && a.adapter_config.model ? ` · ${a.adapter_config.model}` : ""}
          <span aria-hidden="true">·</span> <span title=${a.last_run_at ? timeTitle(a.last_run_at) : ""}>
            last run ${a.last_run_at ? relative(a.last_run_at) : "never"}</span>
        </p>
      </div>
      ${this.actions(a)}
    </header>`;
  }

  render({ onBack, catalogue, navigate }, { d, error, note, assigning }) {
    if (error && !d) {
      return html`<div><button type="button" class="linkish" onClick=${onBack}>← Agents</button>
        <p class="err" role="alert">${error}</p></div>`;
    }
    if (!d) return html`<p class="muted">Opening the agent…</p>`;
    const a = d.agent;
    const tab = this.tab();
    return html`<article class="agent-detail" aria-labelledby=${`agent-${a.id}-heading`}>
      <button type="button" class="linkish" onClick=${onBack}>← Agents</button>
      ${this.header(a)}
      ${a.status === "paused" ? html`<p class="warnbox" role="status"><b>Paused${a.paused_at
        ? ` ${relative(a.paused_at)}` : ""}.</b> ${a.pause_reason || "Its key opens nothing and it is not woken until it is resumed."}</p>` : null}
      ${a.status === "error" ? html`<p class="err" role="status"><b>Its last wake-up failed.</b> ${a.error_reason}</p>` : null}
      ${note ? html`<p class="hint" role="status">${note}</p>` : null}
      ${assigning ? html`<${AssignPanel} agent=${a} onDone=${(said) => this.changed(said)}
        onCancel=${() => this.setState({ assigning: false })} />` : null}
      <div class="tabs-line" role="tablist" aria-label=${`${a.name}'s page`}>
        ${TABS.map(([id, label]) => html`<button type="button" role="tab" key=${id}
          aria-selected=${tab === id} class=${cx("tab-line", tab === id && "on")}
          onClick=${() => this.go(id)}>${label}${id === "runs" && d.runs.length
            ? html` <b class="seg-count">${d.runs.length}</b>` : null}</button>`)}
      </div>
      ${tab === "overview" ? html`<${AgentOverview} d=${d} onRuns=${() => this.go("runs")} />` : null}
      ${tab === "instructions" ? html`<${Instructions} agent=${a}
        onEditing=${(editing) => this.setState({ editing })} onSaved=${() => this.changed("Saved.")} />` : null}
      ${tab === "runs" ? html`<${AgentRuns} runs=${d.runs} />` : null}
      ${tab === "configuration" ? html`<${AgentConfig} agent=${a} catalogue=${catalogue}
        onEditing=${(editing) => this.setState({ editing })} onChanged=${(said) => this.changed(said)}
        onTerminated=${() => { refreshRoster(); this.props.onChanged(); navigate("#/agents"); }} />` : null}
    </article>`;
  }
}
