/* NOC → Cases: the queue the NOC works from, and one case opened at a time (ADRs #459-#461, #468,
 * #470).
 *
 * A case management system's list view: what nobody has taken yet first, most urgent first, each
 * with its take clock counting and who holds it — an agent at its tier, or a person with their
 * photo — and the same table for what is being worked, what waits on someone else, and what is
 * over. Two filters are the server's and live in the address, so the Overview's cells link here:
 * **severity** (the case's priority, which follows the Broker's) and **level** (an agents' tier,
 * or the NOC team). A third narrows the rows on screen by number or words. A case opens as its
 * record (`parts/casedetail.js`).
 *
 * Read again every fifteen seconds, without the loading state, so the clocks and the queue move
 * while the screen is open. Every act is the server's to allow (`actions` on each case).
 */

import { html, cx } from "../dom.js";
import { get } from "../api.js";
import { Loader, Empty } from "../widgets.js";
import { relative, timeTitle } from "../format.js";
import { roster } from "../roster.js";
import { Avatar } from "../avatar.js";
import { Clock, NextUpdate, Priority } from "./parts/caseclock.js";
import { CaseDetail } from "./parts/casedetail.js";
import { AgentGlyph } from "./parts/nocui.js";

const REFRESH_MS = 15000;
const PRIORITIES = ["critical", "major", "minor"];
const LEVELS = [["", "All levels"], ["1", "Tier 1"], ["2", "Tier 2"], ["3", "Tier 3"], ["4", "Tier 4"],
  ["0", "NOC team"]];
const TABS = [
  ["queue", "Queue", ["new", "auto", "queue"]],
  ["working", "Working", ["working", "validate"]],
  ["pending", "Pending", null],
  ["resolved", "Resolved", ["resolved", "closed"]],
  ["all", "All", undefined],
];

/** Who holds the case: a person who took it, with their photo (and the agent it was routed to);
 *  else the agent at its tier; else the NOC team or nobody. */
function Holder({ c }) {
  const agent = c.agent ? html`<span class="muted holder-tier">${c.owner && c.owner.kind === "person"
    ? ` · routed to ${c.agent.name}` : ` · T${c.tier}`}</span>` : null;
  if (c.owner && c.owner.kind === "person") {
    return html`<span class="holder">
      <${Avatar} id=${c.owner.user_id} digest=${c.owner.avatar} name=${c.owner.name}
        username=${c.owner.name} size=${24} />
      <span>${c.owner.name}${c.owner.you ? html`<span class="muted"> (you)</span>` : null}${agent}</span>
    </span>`;
  }
  if (c.agent) {
    const known = c.agent.id ? (roster() || []).find((a) => a.id === c.agent.id) : null;
    return html`<span class="holder">
      <${AgentGlyph} agent=${known || { role: "engineer", icon: "bot" }} size="sm" />
      <span>${c.agent.name}${agent}</span>
    </span>`;
  }
  if (c.owner) return html`<span class="holder"><span>${c.owner.name}</span></span>`;
  return html`<span class="muted">${c.with_people ? "NOC team" : "Unassigned"}</span>`;
}

function TakeCell({ clock, now }) {
  if (!clock) return html`<${Clock} clock=${clock} now=${now} label="Take" />`;
  const running = clock.state === "running" || clock.state === "late";
  const elapsed = running ? now - (clock.due_at - clock.target_s) : clock.elapsed_s;
  const share = Math.max(0, Math.min(1, elapsed / (clock.target_s || 1)));
  return html`<span class="take-cell">
    <${Clock} clock=${clock} now=${now} label="Take" />
    ${running ? html`<span class=${cx("take-bar", share >= 1 && "over", share >= 0.75 && share < 1 && "near")}
      aria-hidden="true"><span style=${`width:${(share * 100).toFixed(1)}%`}></span></span>` : null}
  </span>`;
}

export class Cases extends Loader {
  constructor(props) {
    super(props);
    this.what = "the cases";
    this.loadingLabel = "Reading the queue";
    this.state = { ...this.state, now: Date.now() / 1000, find: "" };
  }

  tab() {
    const asked = this.props.query && this.props.query.get("view");
    return TABS.some(([id]) => id === asked) ? asked : "queue";
  }

  mine() { return Boolean(this.props.query && this.props.query.get("mine") === "1"); }

  /** The server's filters, from the address: `priority` (one of the three) and `tier` (0-4). */
  filters(props = this.props) {
    const q = props.query;
    const priority = q && q.get("priority");
    const tier = q && q.get("tier");
    return {
      priority: PRIORITIES.includes(priority) ? priority : "",
      tier: LEVELS.some(([id]) => id && id === tier) ? tier : "",
    };
  }

  selected() { return Number((this.props.params || [])[0]) || null; }

  routeKey(props) {
    const q = props.query;
    const f = this.filters(props);
    return `${q && q.get("view")}|${q && q.get("mine")}|${f.priority}|${f.tier}`;
  }

  async load() {
    const { priority, tier } = this.filters();
    const extra = `${this.mine() ? "&mine=true" : ""}${priority ? `&priority=${priority}` : ""}${tier
      ? `&tier=${tier}` : ""}`;
    return get(`/api/noc/cases?view=${this.tab()}${extra}`);
  }

  async refresh() {
    try {
      this.setState({ data: await this.load(), now: Date.now() / 1000 });
    } catch { /* the rows stay as they were; the next tick reads again */ }
  }

  componentDidMount() {
    super.componentDidMount();
    this.timer = setInterval(() => this.refresh(), REFRESH_MS);
    this.tick = setInterval(() => this.setState({ now: Date.now() / 1000 }), 1000);
  }

  componentWillUnmount() { clearInterval(this.timer); clearInterval(this.tick); }

  /** The address for a view, keeping what else is chosen unless `change` says otherwise. */
  address(change = {}) {
    const st = { view: this.tab(), mine: this.mine(), ...this.filters(), ...change };
    return `view=${st.view}${st.mine ? "&mine=1" : ""}${st.priority ? `&priority=${st.priority}` : ""}${st.tier
      ? `&tier=${st.tier}` : ""}`;
  }

  go(view, mine = this.mine()) { this.props.navigate(`#/cases?${this.address({ view, mine })}`); }

  filter(change) { this.props.navigate(`#/cases?${this.address(change)}`); }

  open(id) {
    const back = this.address();
    this.props.navigate(id == null ? `#/cases?${back}` : `#/cases/${id}?${back}`);
  }

  count(data, statuses) {
    const counts = data.counts || {};
    const keys = statuses === null ? data.pending : statuses || Object.keys(counts);
    return keys.reduce((n, k) => n + (counts[k] || 0), 0);
  }

  view(data) {
    const sid = this.selected();
    if (sid) {
      return html`<${CaseDetail} key=${sid} id=${sid} catalogue=${data}
        onBack=${() => this.open(null)} onChanged=${() => this.refresh()} />`;
    }
    const tab = this.tab();
    return html`<div class="cases">
      <div class="noc-toolbar">
        <div class="seg" role="tablist" aria-label="Cases">
          ${TABS.map(([id, label, statuses]) => html`<button type="button" role="tab" key=${id}
            aria-selected=${tab === id} class=${cx("seg-btn", tab === id && "on")}
            onClick=${() => this.go(id)}>${label}${statuses !== undefined
              ? html` <b class="seg-count">${this.count(data, statuses)}</b>` : null}</button>`)}
        </div>
        <div class="noc-toolbar-end">
          <label class="visually-hidden" for="cases-find">Filter the cases on screen</label>
          <input id="cases-find" type="search" class="find" placeholder="Filter by number or words"
            value=${this.state.find} onInput=${(e) => this.setState({ find: e.currentTarget.value })} />
          <label class="cases-mine"><input type="checkbox" checked=${this.mine()}
            onChange=${(e) => this.go(tab, e.target.checked)} /> Only mine</label>
        </div>
      </div>
      ${this.filterBar(data)}
      ${this.table(data)}
    </div>`;
  }

  /** Severity and level: chips a lead reads at a glance, one click each, kept in the address. */
  filterBar(data) {
    const { priority, tier } = this.filters();
    const any = priority || tier;
    return html`<div class="case-filters" role="group" aria-label="Filter the cases">
      <span class="case-filter-label" id="cases-sev">Severity</span>
      <div class="cf-chips" role="group" aria-labelledby="cases-sev">
        <button type="button" class=${cx("cf-chip", !priority && "on")} aria-pressed=${!priority}
          onClick=${() => this.filter({ priority: "" })}>All</button>
        ${PRIORITIES.map((p) => html`<button type="button" key=${p} aria-pressed=${priority === p}
          class=${cx("cf-chip", `cf-${p}`, priority === p && "on")}
          onClick=${() => this.filter({ priority: priority === p ? "" : p })}>
          <span class="cf-dot" aria-hidden="true"></span>${data.priorities[p]}</button>`)}
      </div>
      <label class="case-filter-label" for="cases-level">Level</label>
      <select id="cases-level" class="case-level" value=${tier}
        onChange=${(e) => this.filter({ tier: e.currentTarget.value })}>
        ${LEVELS.map(([id, label]) => html`<option key=${id} value=${id}>${label}</option>`)}
      </select>
      ${any ? html`<button type="button" class="linkish" onClick=${() => this.filter({ priority: "", tier: "" })}>
        Clear filters</button>` : null}
      <span class="muted case-filter-count" role="status">${data.cases.length} shown</span>
    </div>`;
  }

  rows(data) {
    const words = this.state.find.trim().toLowerCase();
    if (!words) return data.cases;
    return data.cases.filter((c) => `#${c.id} ${c.title || ""} ${c.situation.name || ""} ${c.status_label}`
      .toLowerCase().includes(words));
  }

  table(data) {
    const { now } = this.state;
    if (!data.cases.length) {
      const { priority, tier } = this.filters();
      return priority || tier
        ? html`<${Empty} title="No case matches these filters"
            will="Clear a filter to see the rest of this view." />`
        : html`<${Empty} title="Nothing here"
            will="A situation the Broker opens arrives in the queue with its take clock running." />`;
    }
    const rows = this.rows(data);
    if (!rows.length) return html`<p class="muted">No case on screen matches “${this.state.find}”.</p>`;
    return html`<div class="table-scroll list-view"><table class="data cases-table">
      <thead><tr>
        <th scope="col">Priority</th><th scope="col">Case</th><th scope="col">Status</th>
        <th scope="col">Assigned to</th><th scope="col">Take</th>
        <th scope="col" class="wide-only">Customer update</th>
        <th scope="col" class="wide-only">Next update</th><th scope="col" class="wide-only">Opened</th>
      </tr></thead>
      <tbody>${rows.map((c) => html`<tr key=${c.id} class=${cx(`row-prio-${c.priority}`)}>
        <td><${Priority} priority=${c.priority} label=${c.priority_label} /></td>
        <td><button type="button" class="linkish case-open" onClick=${() => this.open(c.id)}>
          <span class="mono">#${c.id}</span> ${c.title || c.situation.name || "Untitled"}</button></td>
        <td><span class=${cx("case-status", `case-${c.super_state}`)}>${c.status_label}</span></td>
        <td><${Holder} c=${c} /></td>
        <td><${TakeCell} clock=${c.clocks && c.clocks.take} now=${now} /></td>
        <td class="wide-only"><${Clock} clock=${c.clocks && c.clocks.document} now=${now}
          label="Customer update" /></td>
        <td class="wide-only"><${NextUpdate} cadence=${c.cadence} now=${now} /></td>
        <td class="wide-only" title=${timeTitle(c.opened_at)}>${relative(c.opened_at)}</td>
      </tr>`)}</tbody>
    </table></div>`;
  }
}
