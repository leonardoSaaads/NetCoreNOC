/* NOC → Cases: the queue the NOC works from, and one case opened at a time (ADRs #459-#461, #468).
 *
 * A case management system's list view: what nobody has taken yet first, most urgent first, each
 * with its take clock counting and who holds it — an agent at its tier, a person, or nobody — and
 * the same table for what is being worked, what waits on someone else, and what is over. A filter
 * narrows the rows on screen by number or words, without asking the server again. A case opens as
 * its record (`parts/casedetail.js`).
 *
 * Read again every fifteen seconds, without the loading state, so the clocks and the queue move
 * while the screen is open. Every act is the server's to allow (`actions` on each case).
 */

import { html, cx } from "../dom.js";
import { get } from "../api.js";
import { Loader, Empty } from "../widgets.js";
import { relative, timeTitle } from "../format.js";
import { roster } from "../roster.js";
import { Clock, NextUpdate, Priority } from "./parts/caseclock.js";
import { CaseDetail } from "./parts/casedetail.js";
import { AgentGlyph } from "./parts/nocui.js";

const REFRESH_MS = 15000;
const TABS = [
  ["queue", "Queue", ["new", "auto", "queue"]],
  ["working", "Working", ["working", "validate"]],
  ["pending", "Pending", null],
  ["resolved", "Resolved", ["resolved", "closed"]],
  ["all", "All", undefined],
];

function Holder({ c }) {
  if (c.agent) {
    const known = c.agent.id ? (roster() || []).find((a) => a.id === c.agent.id) : null;
    return html`<span class="holder">
      <${AgentGlyph} agent=${known || { role: "engineer", icon: "bot" }} size="sm" />
      <span>${c.agent.name}<span class="muted holder-tier"> · T${c.tier}</span></span>
    </span>`;
  }
  if (c.owner) return html`<span class="holder"><span class="holder-person" aria-hidden="true">${
    (c.owner.name || "?").slice(0, 1).toUpperCase()}</span><span>${c.owner.name}</span></span>`;
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

  selected() { return Number((this.props.params || [])[0]) || null; }

  routeKey(props) {
    const q = props.query;
    return `${q && q.get("view")}|${q && q.get("mine")}`;
  }

  async load() {
    const mine = this.mine() ? "&mine=true" : "";
    return get(`/api/noc/cases?view=${this.tab()}${mine}`);
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

  go(view, mine = this.mine()) {
    this.props.navigate(`#/cases?view=${view}${mine ? "&mine=1" : ""}`);
  }

  open(id) {
    const back = `view=${this.tab()}${this.mine() ? "&mine=1" : ""}`;
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
      ${this.table(data)}
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
      return html`<${Empty} title="Nothing here"
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
