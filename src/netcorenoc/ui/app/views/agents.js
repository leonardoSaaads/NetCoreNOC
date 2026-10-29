/* NOC → Agents: the NOC's AI agents, one line each, and one agent's page (ADRs #465, #468).
 *
 * Paperclip's agents page, in this console's words: each agent's mark, name and title, its role and
 * tier, what it is doing now, the cases it holds, its last run and what it has spent this month —
 * filtered by state, in the order of the organization. `#/agents/{id}` opens one agent.
 *
 * Hiring is an admin's (`agents.manage`); the form opens here. An agent that runs elsewhere is
 * given its key once, in the answer, with what its MCP client needs to reach this appliance.
 */

import { html, cx } from "../dom.js";
import { get } from "../api.js";
import { Loader, Empty } from "../widgets.js";
import { can } from "../session.js";
import { relative, timeTitle } from "../format.js";
import { orgOrder, refreshRoster } from "../roster.js";
import { AgentGlyph, AgentState, Tier, money } from "./parts/nocui.js";
import { BrandMark } from "../brands.js";
import { AgentDetail } from "./parts/agentdetail.js";
import { AgentForm } from "./parts/agentform.js";
import { KeyOnce, McpConnect } from "./parts/agentkey.js";

const FILTERS = [["all", "All"], ["running", "Running"], ["idle", "Idle"], ["paused", "Paused"],
  ["error", "Error"]];

export class Agents extends Loader {
  constructor(props) {
    super(props);
    this.what = "the NOC's agents";
    this.loadingLabel = "Reading the organization";
    this.state = { ...this.state, hired: null };
  }

  async load() { return get("/api/noc/agents"); }

  /** Opening an agent or a filter is a re-render: the list is already here. */
  routeKey() { return "agents"; }

  async refresh() {
    try {
      this.setState({ data: await this.load() });
    } catch { /* the list stays; the next change reads again */ }
    refreshRoster();
  }

  selected() { return Number((this.props.params || [])[0]) || null; }

  filter() {
    const asked = this.props.query && this.props.query.get("state");
    return FILTERS.some(([id]) => id === asked) ? asked : "all";
  }

  hiring() { return Boolean(this.props.query && this.props.query.get("hire") === "1") && can("agents.manage"); }

  view(data) {
    const id = this.selected();
    if (id) {
      return html`<${AgentDetail} key=${id} id=${id} catalogue=${data}
        tab=${this.props.query && this.props.query.get("tab")}
        onBack=${() => this.props.navigate("#/agents")} onChanged=${() => this.refresh()}
        navigate=${this.props.navigate} />`;
    }
    const { hired } = this.state;
    if (hired) {
      return html`<${KeyOnce} hired=${hired} mcpPath=${data.mcp_path}
        onDone=${() => { this.setState({ hired: null }); this.props.navigate(`#/agents/${hired.agent.id}`); }} />`;
    }
    if (this.hiring()) {
      return html`<${AgentForm} catalogue=${data} agents=${data.agents}
        onCancel=${() => this.props.navigate("#/agents")}
        onSaved=${(answer) => { this.setState({ hired: answer }); this.refresh(); }} />`;
    }
    return this.list(data);
  }

  list(data) {
    const filter = this.filter();
    const order = orgOrder(data.org);
    const live = data.agents.filter((a) => a.status !== "terminated")
      .sort((a, b) => order.indexOf(a.id) - order.indexOf(b.id));
    const shown = live.filter((a) => filter === "all" || a.status === filter);
    const count = (state) => live.filter((a) => state === "all" || a.status === state).length;
    return html`<div class="agents">
      <div class="noc-toolbar">
        <div class="seg" role="tablist" aria-label="Agents by state">
          ${FILTERS.map(([key, label]) => html`<button type="button" role="tab" key=${key}
            aria-selected=${filter === key} class=${cx("seg-btn", filter === key && "on")}
            onClick=${() => this.props.navigate(key === "all" ? "#/agents" : `#/agents?state=${key}`)}>
            ${label} <b class="seg-count">${count(key)}</b></button>`)}
        </div>
        <div class="noc-toolbar-end">
          <a class="btn-ghost" href="#/org">Organization chart</a>
          ${can("agents.manage") ? html`<button type="button" class="primary"
            onClick=${() => this.props.navigate("#/agents?hire=1")}>Hire an agent</button>` : null}
        </div>
      </div>
      ${live.length ? this.month(live) : null}
      ${!live.length ? html`<${Empty} title="No agents yet"
        will=${can("agents.manage")
          ? "Hire the first one: a NOC engineer at Tier 1 that takes cases from the queue, with a supervisor above it to escalate to."
          : "An admin hires the NOC's agents; each then appears here, and in the sidebar."} />`
        : !shown.length ? html`<p class="muted">No agent is ${filter} now.</p>`
          : html`<ul class="agent-rows">${shown.map((a) => html`<li key=${a.id}>${this.row(a)}</li>`)}</ul>`}
      ${can("agents.manage") && live.length ? html`<${McpConnect} mcpPath=${data.mcp_path} />` : null}
    </div>`;
  }

  /** Paperclip's costs at a glance (ADR #472): this month's spend against the budgets set, who is
   *  near its budget, and how many run unpriced — whose $0 is not a saving. */
  month(live) {
    const spent = live.reduce((n, a) => n + Number(a.spent_month_cents || 0), 0);
    const budgeted = live.filter((a) => a.budget_month_cents);
    const budget = budgeted.reduce((n, a) => n + a.budget_month_cents, 0);
    const near = budgeted.filter((a) => a.spent_month_cents >= 0.8 * a.budget_month_cents).length;
    const unpriced = live.filter((a) => a.priced === "unpriced").length;
    return html`<div class="agents-month" role="group" aria-label="This month">
      <div class="agents-month-fact"><b>${money(spent)}</b><span>spent this month</span></div>
      <div class="agents-month-fact"><b>${budget ? money(budget) : "—"}</b><span>${budgeted.length
        ? `budgets of ${budgeted.length} agent${budgeted.length === 1 ? "" : "s"}` : "no budget set"}</span></div>
      <div class=${cx("agents-month-fact", near && "warn")}><b>${near}</b><span>at 80% of their budget or more</span></div>
      <div class=${cx("agents-month-fact", unpriced && "warn")}><b>${unpriced}</b><span>unpriced: tokens counted, spend not</span></div>
    </div>`;
  }

  row(a) {
    const budget = a.budget_month_cents;
    const used = budget ? Math.min(100, (100 * a.spent_month_cents) / budget) : null;
    return html`<a class="agent-row" href=${`#/agents/${a.id}`}>
      <${AgentGlyph} agent=${a} size="md" />
      <span class="agent-row-name">
        <b>${a.name}</b>
        <span class="muted">${a.provider ? html`<${BrandMark} brand=${a.provider} size=${14} />${" "}` : null}${a.title || a.role_label}</span>
      </span>
      <span class="agent-row-role">
        <span class=${cx("role-chip", `role-${a.role}`)}>${a.role_label}</span>
        <${Tier} tier=${a.tier} />
        ${a.entry ? html`<span class="entry-chip" title="Cases from the queue start with an entry agent">Entry</span>` : null}
      </span>
      <span class="agent-row-state"><${AgentState} status=${a.status} label=${a.status_label} /></span>
      <span class="agent-row-fact"><b>${a.open_cases}</b> <span class="muted">open case${a.open_cases === 1 ? "" : "s"}</span></span>
      <span class="agent-row-fact wide-only" title=${a.last_run_at ? timeTitle(a.last_run_at) : ""}>
        <span class="muted">Last run</span> ${a.last_run_at ? relative(a.last_run_at) : "never"}</span>
      <span class="agent-row-fact wide-only">
        <span class="muted">This month</span> ${money(a.spent_month_cents)}${budget
          ? html`<span class=${cx("mini-meter", used >= 80 && used < 100 && "near", used >= 100 && "over")}
              aria-hidden="true"><span style=${`width:${used.toFixed(1)}%`}></span></span>
            <span class="muted"> of ${money(budget)}</span>` : null}
      </span>
    </a>`;
  }
}
